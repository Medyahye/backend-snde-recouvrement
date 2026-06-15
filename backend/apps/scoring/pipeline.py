"""Pipeline de scoring + agrégation — port fidèle des cellules 14, 15 du notebook.

Toutes les fonctions sont *pures* (entrée DataFrame → sortie DataFrame) et
configurables via les constantes Django (`settings.SCORING_*`).
Cf. Note Explicative §3 et §5 pour la justification métier.
"""
from __future__ import annotations

from datetime import date

import numpy as np
import pandas as pd
from django.conf import settings

from .config_service import ScoringParams


def filter_eligible_clients(df: pd.DataFrame) -> pd.DataFrame:
    """V2 : filtre les clients éligibles à l'ingestion (tous codes de relance,
    tous soldes incluant zéro et négatif).

    On garde tous les abonnés actifs SANS aucune contrainte sur le solde
    (positif, nul, ou négatif). Ceci permet :
      - de tracker les **cycles de paiement complets** : un client qui paye
        toute sa dette (solde → 0) reste visible. Son retour avec une nouvelle
        facture est détecté comme NEW_BILLING au lieu de NEW_CLIENT.
      - de garder les **bons payeurs en crédit** (solde < 0 = trop-perçu) avec
        leur magnitude réelle : information précieuse pour l'audit, l'IA, et
        la compréhension du comportement client.

    Le filtre `solde > 0` est appliqué plus tard dans `filter_for_scoring`.
    Les endpoints qui souhaitent ne montrer que les "clients à recouvrer"
    doivent ajouter explicitement `solde > 0` dans leur requête.

    Critères d'éligibilité minimale (ingestion) :
    - code_activite == "1" (abonné actif)
    - code_echeance == 0 (pas d'échéancier de paiement actif)
    """
    if df.empty:
        return df.copy()

    s = df.copy()
    s = s[s["code_activite"] == "1"]
    s["_code_echeance_num"] = pd.to_numeric(s["code_echeance"], errors="coerce").fillna(0)
    s = s[s["_code_echeance_num"] == 0].drop(columns=["_code_echeance_num"])
    # Note : on NE clippe PAS le solde (Option A). Garder la valeur brute
    # préserve l'information de magnitude du crédit, utile pour ML et audit.
    # Les calculs sensibles (arrieres, scoring) ont déjà leur propre clip
    # interne via .clip(lower=0) là où nécessaire.
    # Dédupliquer par reference_abonnement : certains FABs SNDE contiennent des
    # doublons exacts (même client retourné 2 fois). On garde la première
    # occurrence pour respecter la contrainte UNIQUE(import_ref, reference_abonnement).
    s = s.drop_duplicates(subset=["reference_abonnement"], keep="first")
    return s.reset_index(drop=True)


def filter_for_scoring(df: pd.DataFrame) -> pd.DataFrame:
    """Sous-ensemble des clients éligibles AU SCORING uniquement (code_relance=='1').

    Sur l'ensemble déjà filtré par `filter_eligible_clients`, on garde uniquement
    ceux en coupure immédiate qui méritent un score :
    - code_relance == "1"
    - solde > 0 (filtre déplacé ici depuis filter_eligible_clients en V2)
    - date_facture renseignée
    - date_dernier_paiement renseignée
    - activite_client renseigné
    """
    if df.empty:
        return df.copy()

    s = df.copy()
    s = s[s["code_relance"] == "1"]
    s = s[s["solde"] > 0]
    s = s[s["date_facture"].notna()]
    s = s[s["date_dernier_paiement"].notna()]
    s = s[
        s["activite_client"].notna()
        & (s["activite_client"].astype(str).str.strip() != "")
    ]
    return s.reset_index(drop=True)


def compute_score_components(
    df: pd.DataFrame,
    statement_date: date,
    params: ScoringParams | None = None,
) -> pd.DataFrame:
    """Calcule les 4 composantes normalisées + coef + Score (cf. Note §5).

    `params` permet d'injecter une configuration arbitraire (utile pour le
    recalcul d'un import avec une autre config). Si None, on prend la config
    active depuis la base.
    """
    if df.empty:
        for col in (
            "Montant_norm",
            "Anciennete_norm",
            "Historique_norm",
            "Arrieres_norm",
            "Coefficient_type",
            "Score",
            "proba_paiement",
            "jours_impaye",
            "jours_sans_paiement",
            "type_client",
        ):
            df[col] = pd.Series(dtype="float64")
        return df

    if params is None:
        # Import différé pour éviter une dépendance circulaire à l'app loading.
        from .config_service import get_active_params

        params = get_active_params()

    weights = params.weights
    threshold = params.threshold_days
    # La liste des activités domestiques reste dans settings (peu modifiable).
    domestique = settings.DOMESTIQUE_ACTIVITIES

    df = df.copy()
    df["date_facture"] = pd.to_datetime(df["date_facture"])
    df["date_dernier_paiement"] = pd.to_datetime(df["date_dernier_paiement"])
    ref = pd.Timestamp(statement_date)

    # 1. Montant_norm — min-max sur l'ensemble du df (cf. Note §5.2.1)
    mn, mx = df["solde"].min(), df["solde"].max()
    df["Montant_norm"] = ((df["solde"] - mn) / (mx - mn)) if mx > mn else 0.0

    # 2. Anciennete_norm — jours_impaye / threshold plafonné à 1 (Note §5.2.2)
    df["jours_impaye"] = (ref - df["date_facture"]).dt.days.clip(lower=0)
    df["Anciennete_norm"] = np.minimum(df["jours_impaye"] / threshold, 1.0)

    # 3. Historique_norm — jours_sans_paiement / threshold (Note §5.2.3)
    df["jours_sans_paiement"] = (
        (ref - df["date_dernier_paiement"]).dt.days.clip(lower=0)
    )
    df["Historique_norm"] = np.minimum(df["jours_sans_paiement"] / threshold, 1.0)

    # 4. Arrieres_norm — arrieres / solde, clipé [0,1] (Note §5.2.4)
    df["arrieres"] = df["arrieres"].clip(lower=0)
    df["Arrieres_norm"] = (df["arrieres"] / df["solde"]).clip(0, 1)

    # 5. Type client + coef (Note §5.3)
    df["type_client"] = np.where(
        df["activite_client"].astype(str).str.strip().isin(domestique),
        "Domestique", "Entreprise",
    )
    df["Coefficient_type"] = np.where(
        df["type_client"] == "Domestique",
        params.coef_domestique, params.coef_entreprise,
    )

    # 6. Score final (formule)
    df["Score"] = (
        weights["MONTANT"] * df["Montant_norm"]
        + weights["ANCIENNETE"] * df["Anciennete_norm"]
        + weights["HISTORIQUE"] * df["Historique_norm"]
        + weights["ARRIERES"] * df["Arrieres_norm"]
    ) * df["Coefficient_type"]

    # 7. FT-Transformer (si activé via SCORING_ENGINE=ft_transformer dans .env)
    # Score = P(paiement) × solde → maximise le montant recouvré par visite terrain
    if getattr(settings, "SCORING_ENGINE", "formula") == "ft_transformer":
        from .ft_transformer import predict_scores
        df["proba_paiement"] = predict_scores(df)
        df["Score"] = df["proba_paiement"] * df["solde"]

    return df


def map_centres_and_zone(df: pd.DataFrame, centres_map: dict[str, str]) -> pd.DataFrame:
    """Ajoute centre_nom (depuis le mapping) et zone (NomCentre_SS_TT)."""
    if df.empty:
        df["centre_nom"] = pd.Series(dtype="object")
        df["zone"] = pd.Series(dtype="object")
        return df

    df = df.copy()
    df["code_centre"] = df["code_centre"].astype(str).str.strip()
    df["centre_nom"] = (
        df["code_centre"].map(centres_map).fillna("INCONNU_" + df["code_centre"])
    )
    df["zone"] = df.apply(
        lambda r: (
            f"{r['centre_nom']}_"
            f"{str(r['secteur_facturation']).strip().zfill(2)}_"
            f"{str(r['tournee_releve']).strip().zfill(2)}"
        ),
        axis=1,
    )
    return df


def categorise_clients_priority(
    df: pd.DataFrame, params: ScoringParams | None = None
) -> pd.DataFrame:
    """Ajoute une colonne 'Priorite' (Haute/Moyenne/Faible) basée sur quantiles 75/50 du Score."""
    if df.empty:
        df["Priorite"] = pd.Series(dtype="object")
        return df

    if params is None:
        from .config_service import get_active_params

        params = get_active_params()

    q75 = df["Score"].quantile(params.priority_quantile_high)
    q50 = df["Score"].quantile(params.priority_quantile_med)

    df = df.copy()
    df["Priorite"] = df["Score"].apply(
        lambda x: "Haute" if x >= q75 else ("Moyenne" if x >= q50 else "Faible")
    )
    return df


def rank_clients(df: pd.DataFrame) -> pd.DataFrame:
    """Trie par Score décroissant et ajoute la colonne 'rang' (1 = plus prioritaire)."""
    if df.empty:
        df["rang"] = pd.Series(dtype="int64")
        return df

    df = df.sort_values("Score", ascending=False).reset_index(drop=True)
    df.insert(0, "rang", range(1, len(df) + 1))
    return df


def compute_zones_aggregation(
    df: pd.DataFrame, params: ScoringParams | None = None
) -> pd.DataFrame:
    """Agrège par zone et calcule priorite_zone + catégorisation Haute/Moyenne/Faible.

    `priorite_zone = score_moyen × nb_clients` (cf. Note §6).
    Quantiles 75/50 indépendants des quantiles clients (Note §7.4).
    """
    if df.empty:
        return pd.DataFrame()

    if params is None:
        from .config_service import get_active_params

        params = get_active_params()

    g = (
        df.groupby("zone")
        .agg(
            nb_clients=("Score", "size"),
            nb_entreprises=("type_client", lambda x: int((x == "Entreprise").sum())),
            nb_domestiques=("type_client", lambda x: int((x == "Domestique").sum())),
            score_moyen=("Score", "mean"),
            score_max=("Score", "max"),
            score_total=("Score", "sum"),
            solde_total=("solde", "sum"),
            solde_moyen=("solde", "mean"),
            arrieres_total=("arrieres", "sum"),
            anciennete_moyenne=("jours_impaye", "mean"),
        )
        .reset_index()
    )

    g["priorite_zone"] = g["score_moyen"] * g["nb_clients"]
    g = g.sort_values("priorite_zone", ascending=False).reset_index(drop=True)
    g.insert(0, "rang", range(1, len(g) + 1))

    # Décomposer la zone en (centre_nom, secteur, tournee)
    # Format : "NomCentre_SS_TT" — split de gauche, max 2 splits.
    # Aucun centre SNDE ne contient '_' dans son nom (vérifié via la table seed).
    g[["centre_nom", "secteur", "tournee"]] = g["zone"].str.split(
        "_", n=2, expand=True
    )

    # Catégorisation par quantiles (Note §7)
    q75 = g["priorite_zone"].quantile(params.priority_quantile_high)
    q50 = g["priorite_zone"].quantile(params.priority_quantile_med)
    g["priorite"] = g["priorite_zone"].apply(
        lambda p: "Haute" if p >= q75 else ("Moyenne" if p >= q50 else "Faible")
    )
    return g
