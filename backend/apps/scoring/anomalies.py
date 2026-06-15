"""Détection d'anomalies opérationnelles SNDE.

Cycle normal SNDE :
  1. Un client en retard reçoit `code_relance="1"` UNE SEULE FOIS (ordre de coupure).
  2. Le FAB du lendemain, ce client revient à `code_relance="0"` quoi qu'il arrive
     (le code 1 ne sert qu'à déclencher la coupure, il ne persiste pas).
  3. Si la coupure est exécutée → le client devient `code_activite="2"` (suspendu)
     et sort de notre DB (on n'ingère que `code_activite="1"`).
  4. Si la coupure n'est PAS exécutée → le client reste `code_activite="1"` et donc
     reste dans notre DB. Sa consommation peut continuer à monter.

**Anomalie** = client qui a eu `code_relance="1"` il y a ≥ N jours, mais qui :
  - Est toujours dans le FAB actuel (= code_activite="1", coupure NON exécutée)
  - Solde a augmenté depuis (= consommation continue)
  - date_dernier_paiement inchangée (= aucun paiement)

C'est la signature d'une coupure ordonnée mais jamais réalisée. Le releveur et la
tournée concernés sont responsables.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta

from apps.clients.models import Client
from apps.imports.models import FabImport


DEFAULT_PERSISTENT_CODE_1_THRESHOLD = 15


@dataclass
class UncutClient:
    """Client en anomalie : coupure ordonnée non exécutée."""

    client_id: int             # ID du Client (snapshot du FAB courant) pour lien détail
    reference_abonnement: str
    nom_client: str
    tournee_releve: str
    releveur_1: str
    centre_nom: str
    zone: str
    # État actuel
    solde_current: float
    arrieres_current: float
    date_paiement_current: str | None
    # État au moment du code 1
    code_1_date: str           # date du FAB où code_relance="1"
    days_since_code_1: int      # jours écoulés depuis ce code 1
    solde_at_code_1: float
    date_paiement_at_code_1: str | None
    # Métrique d'anomalie
    delta_solde: float          # solde_current - solde_at_code_1 (>0 = conso continue)


def detect_uncut_clients(
    current_import: FabImport,
    threshold_days: int = DEFAULT_PERSISTENT_CODE_1_THRESHOLD,
) -> list[UncutClient]:
    """Détecte les clients dont la coupure (code_relance="1") n'a pas été exécutée.

    Critères :
      1. Le client a eu code_relance="1" au moins une fois il y a ≥ threshold_days.
      2. Il est toujours présent dans current_import (= code_activite=1 confirmé).
      3. Son solde actuel > solde au moment du code 1 (consommation a continué).
      4. Sa date_dernier_paiement n'a pas changé depuis le code 1 (aucun paiement).
    """
    if threshold_days < 1:
        return []

    cutoff_date = current_import.file_date - timedelta(days=threshold_days)

    # Étape 1 : trouver toutes les refs présentes dans le FAB courant qui ont
    # eu code_relance="1" au moins une fois avant cutoff_date.
    current_refs = set(
        Client.objects.filter(import_ref=current_import)
        .values_list("reference_abonnement", flat=True)
    )
    if not current_refs:
        return []

    # Refs avec un code 1 passé (au moins threshold_days ago)
    refs_with_code_1 = set(
        Client.objects.filter(
            reference_abonnement__in=current_refs,
            code_relance="1",
            import_ref__file_date__lte=cutoff_date,
            import_ref__status=FabImport.Status.DONE,
        )
        .values_list("reference_abonnement", flat=True)
        .distinct()
    )

    if not refs_with_code_1:
        return []

    # Étape 2 : pour chaque ref, trouver le snapshot le plus récent où
    # code_relance="1" (= la dernière coupure ordonnée). On charge ces snapshots
    # avec leur file_date pour les comparer plus tard.
    # Stratégie efficace : on charge TOUS les snapshots code=1 de ces refs et on
    # garde le plus récent en Python (1 query, ~quelques milliers de records).
    all_code_1_snaps = (
        Client.objects.filter(
            reference_abonnement__in=refs_with_code_1,
            code_relance="1",
            import_ref__status=FabImport.Status.DONE,
            import_ref__file_date__lte=cutoff_date,
        )
        .select_related("import_ref")
        .values(
            "reference_abonnement",
            "solde",
            "date_dernier_paiement",
            "import_ref__file_date",
        )
        .order_by("reference_abonnement", "-import_ref__file_date")
    )

    # Garde le plus récent par ref (= premier de la liste pour chaque ref)
    latest_code_1: dict[str, dict] = {}
    for snap in all_code_1_snaps:
        ref = snap["reference_abonnement"]
        if ref not in latest_code_1:
            latest_code_1[ref] = snap

    if not latest_code_1:
        return []

    # Étape 3 : charger les snapshots du FAB courant pour les mêmes refs
    current_snaps = (
        Client.objects.filter(
            import_ref=current_import,
            reference_abonnement__in=list(latest_code_1.keys()),
        )
        .values(
            "id",
            "reference_abonnement",
            "nom_client",
            "tournee_releve",
            "releveur_1",
            "centre_nom",
            "zone",
            "solde",
            "arrieres",
            "date_dernier_paiement",
        )
    )

    # Étape 4 : comparer et garder les anomalies
    anomalies: list[UncutClient] = []
    for cur in current_snaps:
        ref = cur["reference_abonnement"]
        past = latest_code_1.get(ref)
        if past is None:
            continue

        # Critère "consommation continue" : solde a augmenté
        solde_increased = float(cur["solde"]) > float(past["solde"])
        # Critère "aucun paiement" : date_dernier_paiement inchangée
        paiement_unchanged = cur["date_dernier_paiement"] == past["date_dernier_paiement"]

        if not (solde_increased and paiement_unchanged):
            continue

        code_1_date = past["import_ref__file_date"]
        anomalies.append(
            UncutClient(
                client_id=cur["id"],
                reference_abonnement=ref,
                nom_client=cur["nom_client"],
                tournee_releve=cur["tournee_releve"],
                releveur_1=cur["releveur_1"] or "",
                centre_nom=cur["centre_nom"],
                zone=cur["zone"],
                solde_current=float(cur["solde"]),
                arrieres_current=float(cur["arrieres"]),
                date_paiement_current=(
                    cur["date_dernier_paiement"].isoformat()
                    if cur["date_dernier_paiement"]
                    else None
                ),
                code_1_date=code_1_date.isoformat(),
                days_since_code_1=(current_import.file_date - code_1_date).days,
                solde_at_code_1=float(past["solde"]),
                date_paiement_at_code_1=(
                    past["date_dernier_paiement"].isoformat()
                    if past["date_dernier_paiement"]
                    else None
                ),
                delta_solde=float(cur["solde"]) - float(past["solde"]),
            )
        )

    # Trier par delta_solde décroissant (anomalies les plus "graves" en premier)
    anomalies.sort(key=lambda a: a.delta_solde, reverse=True)
    return anomalies


def aggregate_uncut_by_zone(anomalies: list[UncutClient]) -> list[dict]:
    """Agrège par zone (centre+secteur+tournée) avec le releveur responsable.

    1 zone = 1 releveur dans 99.96 % des cas SNDE (vérifié sur le terrain).
    Si plusieurs releveurs sur la même zone (rare edge case), on liste le
    plus fréquent ou agrège les deux.
    """
    from collections import Counter

    # Groupe par zone, et compte les releveurs vus
    groups: dict[str, dict] = {}
    releveurs_per_zone: dict[str, Counter] = {}

    for a in anomalies:
        key = a.zone
        if key not in groups:
            groups[key] = {
                "zone": a.zone,
                "centre_nom": a.centre_nom,
                "tournee_releve": a.tournee_releve,
                "releveur_1": "",  # rempli après
                "nb_anomalies": 0,
                "total_delta_solde": 0.0,
                "total_arrieres": 0.0,
            }
            releveurs_per_zone[key] = Counter()
        groups[key]["nb_anomalies"] += 1
        groups[key]["total_delta_solde"] += a.delta_solde
        groups[key]["total_arrieres"] += a.arrieres_current
        if a.releveur_1:
            releveurs_per_zone[key][a.releveur_1] += 1

    # Pour chaque zone, choisir le releveur dominant (le + fréquent)
    for key, group in groups.items():
        if releveurs_per_zone[key]:
            group["releveur_1"] = releveurs_per_zone[key].most_common(1)[0][0]

    return sorted(groups.values(), key=lambda g: g["nb_anomalies"], reverse=True)


def aggregate_uncut_by_releveur(anomalies: list[UncutClient]) -> list[dict]:
    """Agrège par releveur (agent terrain)."""
    groups: dict[tuple[str, str], dict] = {}
    tournees_per_releveur: dict[tuple[str, str], set[str]] = {}

    for a in anomalies:
        key = (a.releveur_1 or "(inconnu)", a.centre_nom)
        if key not in groups:
            groups[key] = {
                "releveur_1": a.releveur_1 or "(inconnu)",
                "centre_nom": a.centre_nom,
                "nb_anomalies": 0,
                "nb_tournees_impactees": 0,
                "total_delta_solde": 0.0,
                "total_arrieres": 0.0,
            }
            tournees_per_releveur[key] = set()
        groups[key]["nb_anomalies"] += 1
        groups[key]["total_delta_solde"] += a.delta_solde
        groups[key]["total_arrieres"] += a.arrieres_current
        tournees_per_releveur[key].add(a.tournee_releve)

    for key, group in groups.items():
        group["nb_tournees_impactees"] = len(tournees_per_releveur[key])

    return sorted(groups.values(), key=lambda g: g["nb_anomalies"], reverse=True)
