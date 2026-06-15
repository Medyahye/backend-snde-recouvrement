"""Tests du pipeline de scoring (apps/scoring/pipeline.py)."""
from datetime import date

import pandas as pd
import pytest

from apps.scoring.pipeline import (
    categorise_clients_priority,
    compute_score_components,
    compute_zones_aggregation,
    filter_for_scoring,
    map_centres_and_zone,
    rank_clients,
)


def _client_row(**overrides):
    """Construit une ligne client par défaut conforme au schéma post-parsing."""
    base = {
        "code_activite": "1",
        "code_centre": "42",
        "secteur_facturation": "3",
        "tournee_releve": "1",
        "reference_abonnement": "REF001",
        "nom_client": "JEAN DOE",
        "adresse": "ADR1",
        "telephone": "+22246123456",
        "activite_client": "TOUS CLIENTS DOMESTIQUES",
        "solde": 50000.0,
        "montant_facture": 5000.0,
        "arrieres": 45000.0,
        "date_facture": date(2026, 1, 1),  # ~100j d'ancienneté avec ref 12/04/26
        "date_dernier_paiement": date(2025, 10, 1),
        "code_echeance": "0",
        "code_relance": "1",
        "releveur_1": "3153",
    }
    base.update(overrides)
    return base


# --- Filtrage ---


def test_filter_keeps_only_relance_1_active_with_balance():
    df = pd.DataFrame(
        [
            _client_row(code_relance="1"),  # gardé
            _client_row(code_relance="2"),  # rejeté
            _client_row(code_relance="1", code_activite="0"),  # rejeté (résilié)
            _client_row(code_relance="1", solde=0),  # rejeté
            _client_row(code_relance="1", date_facture=None),  # rejeté
            _client_row(code_relance="1", code_echeance="1"),  # rejeté (échéancier actif)
        ]
    )
    out = filter_for_scoring(df)
    assert len(out) == 1
    assert out.iloc[0]["code_relance"] == "1"


def test_filter_empty_returns_empty():
    out = filter_for_scoring(pd.DataFrame())
    assert out.empty


# --- Calcul du score ---


def test_score_domestique_known_value():
    """Cas reproductible : solde unique → Montant_norm = 0.
    Score = (0.40·0 + 0.25·100/180 + 0.20·193/180=1 + 0.15·45000/50000) × 1.00
    """
    df = pd.DataFrame([_client_row()])
    df = filter_for_scoring(df)
    df = compute_score_components(df, date(2026, 4, 11))
    row = df.iloc[0]
    assert row["type_client"] == "Domestique"
    assert row["Coefficient_type"] == 1.00
    # 100 jours d'impayé (01/01/26 → 11/04/26)
    assert row["jours_impaye"] == 100
    assert row["Anciennete_norm"] == pytest.approx(100 / 180, abs=1e-6)
    # 193 jours sans paiement, plafonné à 1.0
    assert row["Historique_norm"] == 1.0
    # arrieres / solde = 45000/50000 = 0.9
    assert row["Arrieres_norm"] == pytest.approx(0.9, abs=1e-6)
    # Score = 0 + 0.25·(100/180) + 0.20·1.0 + 0.15·0.9 = 0.1389 + 0.2 + 0.135 = 0.4739
    expected = 0.40 * 0 + 0.25 * (100 / 180) + 0.20 * 1.0 + 0.15 * 0.9
    assert row["Score"] == pytest.approx(expected, abs=1e-4)


def test_score_entreprise_gets_coef_120():
    df = pd.DataFrame([_client_row(activite_client="USINE")])
    df = filter_for_scoring(df)
    df = compute_score_components(df, date(2026, 4, 12))
    assert df.iloc[0]["type_client"] == "Entreprise"
    assert df.iloc[0]["Coefficient_type"] == 1.20


def test_score_montant_norm_minmax():
    """Avec 2 clients de soldes différents : le max a Montant_norm=1, le min=0."""
    df = pd.DataFrame(
        [
            _client_row(reference_abonnement="LO", solde=10000, arrieres=0),
            _client_row(reference_abonnement="HI", solde=200000, arrieres=0),
        ]
    )
    df = filter_for_scoring(df)
    df = compute_score_components(df, date(2026, 4, 12))
    df = df.sort_values("solde").reset_index(drop=True)
    assert df.iloc[0]["Montant_norm"] == 0.0
    assert df.iloc[1]["Montant_norm"] == 1.0


def test_score_arrieres_clipped_to_zero():
    """Si arrieres < 0 (overpayment), il est clipé à 0 pour le scoring."""
    df = pd.DataFrame([_client_row(arrieres=-5000)])
    df = filter_for_scoring(df)
    df = compute_score_components(df, date(2026, 4, 12))
    assert df.iloc[0]["Arrieres_norm"] == 0.0


# --- Classement et catégorisation ---


def test_rank_orders_by_score_desc():
    df = pd.DataFrame(
        [
            _client_row(reference_abonnement="LOW", solde=1000, arrieres=0),
            _client_row(reference_abonnement="HIGH", solde=900000, arrieres=800000),
        ]
    )
    df = filter_for_scoring(df)
    df = compute_score_components(df, date(2026, 4, 12))
    df = rank_clients(df)
    assert df.iloc[0]["reference_abonnement"] == "HIGH"
    assert df.iloc[0]["rang"] == 1
    assert df.iloc[1]["rang"] == 2


def test_categorise_quantiles_distribution():
    """Avec 100 clients de scores variés, ~25% doivent être Haute,
    ~25% Moyenne, ~50% Faible (cf. Note Explicative §7.2)."""
    rows = [
        _client_row(reference_abonnement=f"R{i:03}", solde=1000 + i * 100, arrieres=i * 50)
        for i in range(100)
    ]
    df = pd.DataFrame(rows)
    df = filter_for_scoring(df)
    df = compute_score_components(df, date(2026, 4, 12))
    df = categorise_clients_priority(df)
    counts = df["Priorite"].value_counts()
    # Tolérance ±3 sur 100 (les quantiles peuvent grouper à l'identique)
    assert 22 <= counts.get("Haute", 0) <= 28
    assert 47 <= counts.get("Faible", 0) <= 53


# --- Mapping centres + zone ---


def test_map_centres_and_zone_uses_lookup():
    df = pd.DataFrame(
        [
            _client_row(code_centre="42", secteur_facturation="3", tournee_releve="1"),
            _client_row(code_centre="999", secteur_facturation="5", tournee_releve="9"),
        ]
    )
    df = filter_for_scoring(df)
    df = compute_score_components(df, date(2026, 4, 12))
    df = map_centres_and_zone(df, {"42": "CARREFOUR2"})
    df = df.sort_values("code_centre").reset_index(drop=True)
    assert df.iloc[0]["centre_nom"] == "CARREFOUR2"
    assert df.iloc[0]["zone"] == "CARREFOUR2_03_01"
    # Code inconnu → préfixe INCONNU_
    assert df.iloc[1]["centre_nom"] == "INCONNU_999"


# --- Agrégation par zone ---


def test_zone_aggregation_priorite_zone_formula():
    """priorite_zone doit être exactement score_moyen × nb_clients."""
    df = pd.DataFrame(
        [
            _client_row(reference_abonnement=f"R{i}", solde=10000 + i * 100, arrieres=5000)
            for i in range(5)
        ]
    )
    df = filter_for_scoring(df)
    df = compute_score_components(df, date(2026, 4, 12))
    df = map_centres_and_zone(df, {"42": "CARREFOUR2"})
    df = rank_clients(df)
    zones = compute_zones_aggregation(df)
    assert len(zones) == 1
    z = zones.iloc[0]
    assert z["nb_clients"] == 5
    assert z["zone"] == "CARREFOUR2_03_01"
    assert z["centre_nom"] == "CARREFOUR2"
    assert z["secteur"] == "03"
    assert z["tournee"] == "01"
    # Vérification de la formule : priorite_zone == score_moyen × nb_clients
    expected = z["score_moyen"] * z["nb_clients"]
    assert z["priorite_zone"] == pytest.approx(expected, abs=1e-9)


def test_zone_aggregation_counts_entreprises_and_domestiques():
    df = pd.DataFrame(
        [
            _client_row(reference_abonnement="D1", activite_client="TOUS CLIENTS DOMESTIQUES"),
            _client_row(reference_abonnement="D2", activite_client="BRANCHEMENTS SOCIAUX"),
            _client_row(reference_abonnement="E1", activite_client="USINE"),
        ]
    )
    df = filter_for_scoring(df)
    df = compute_score_components(df, date(2026, 4, 12))
    df = map_centres_and_zone(df, {"42": "CARREFOUR2"})
    df = rank_clients(df)
    zones = compute_zones_aggregation(df)
    z = zones.iloc[0]
    assert z["nb_domestiques"] == 2
    assert z["nb_entreprises"] == 1
    assert z["nb_clients"] == 3


def test_empty_pipeline_does_not_crash():
    """Un FAB sans aucun client relance=1 ne doit pas crasher."""
    df = pd.DataFrame([_client_row(code_relance="2")])
    df = filter_for_scoring(df)
    assert df.empty
    df = compute_score_components(df, date(2026, 4, 12))
    df = map_centres_and_zone(df, {})
    df = categorise_clients_priority(df)
    df = rank_clients(df)
    zones = compute_zones_aggregation(df)
    assert zones.empty
