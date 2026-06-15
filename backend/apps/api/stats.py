"""Logique métier pour les KPIs, distributions et agrégations.

Toutes les fonctions opèrent sur des QuerySets Django (pas de pandas)
pour rester rapides et économes en RAM.
"""
from __future__ import annotations

from decimal import Decimal
from typing import Iterable

from django.db.models import Avg, Count, Max, Sum

from apps.clients.models import Client
from apps.imports.models import FabImport
from apps.zones.models import Zone


# --------------------------------------------------------------------------- #
# KPIs
# --------------------------------------------------------------------------- #


def compute_kpis(import_id: int) -> dict:
    """Renvoie les indicateurs clés d'un import (vue dashboard accueil).

    V1.5 : `nb_clients` correspond aux clients scorés (code_relance='1') pour
    rester comparable avec la V1. Les autres codes sont visibles ailleurs.
    """
    imp = FabImport.objects.get(id=import_id)
    # Seulement les clients scorés pour rester cohérent avec les KPIs initiaux
    clients = Client.objects.filter(
        import_ref_id=import_id, score_final__isnull=False
    )
    clients_total = Client.objects.filter(import_ref_id=import_id)
    zones = Zone.objects.filter(import_ref_id=import_id)

    nb_zones = zones.count()
    nb_clients = clients.count()

    # Répartition des zones par priorité
    by_priorite = {row["priorite"]: row["c"] for row in zones.values("priorite").annotate(c=Count("id"))}
    nb_haute = by_priorite.get("Haute", 0)
    nb_moyenne = by_priorite.get("Moyenne", 0)
    nb_faible = by_priorite.get("Faible", 0)

    # Agrégats financiers
    fin = clients.aggregate(
        solde_total=Sum("solde"),
        arrieres_total=Sum("arrieres"),
        score_moyen_global=Avg("score_final"),
        score_max_global=Max("score_final"),
    )

    # Top zone (rang=1)
    top_zone_obj = zones.filter(rang=1).first()
    top_zone = (
        {
            "id": top_zone_obj.id,
            "zone_id": top_zone_obj.zone_id,
            "centre_nom": top_zone_obj.centre_nom,
            "nb_clients": top_zone_obj.nb_clients,
            "score_moyen": top_zone_obj.score_moyen,
            "priorite_zone": top_zone_obj.priorite_zone,
            "solde_total": top_zone_obj.solde_total,
        }
        if top_zone_obj
        else None
    )

    duration = None
    if imp.started_at and imp.finished_at:
        duration = int((imp.finished_at - imp.started_at).total_seconds())

    return {
        "import": {
            "id": imp.id,
            "file_date": imp.file_date.isoformat(),
            "status": imp.status,
            "uploaded_at": imp.uploaded_at.isoformat(),
            "nb_lines_total": imp.nb_lines_total,
            "duration_seconds": duration,
        },
        "totaux": {
            "nb_clients": nb_clients,  # = scorés (code 1 + données complètes)
            "nb_clients_total": clients_total.count(),  # tous codes ingérés
            "nb_code_1": clients_total.filter(code_relance="1").count(),  # tous les code 1
            "nb_zones": nb_zones,
            "solde_total": fin["solde_total"] or Decimal("0"),
            "arrieres_total": fin["arrieres_total"] or Decimal("0"),
            "score_moyen": round(fin["score_moyen_global"] or 0, 4),
            "score_max": round(fin["score_max_global"] or 0, 4),
        },
        "zones_par_priorite": {
            "Haute": nb_haute,
            "Moyenne": nb_moyenne,
            "Faible": nb_faible,
            "pct_haute": round(100 * nb_haute / nb_zones, 1) if nb_zones else 0,
        },
        "top_zone": top_zone,
    }


# --------------------------------------------------------------------------- #
# Distribution des scores
# --------------------------------------------------------------------------- #


def compute_score_distribution(import_id: int, n_buckets: int = 10) -> dict:
    """Histogramme des scores clients en `n_buckets` paliers égaux entre [0, 1.2].

    Le max théorique = 1.20 (Entreprise avec toutes composantes = 1.0).

    V1.5 : on filtre `score_final__isnull=False` car depuis qu'on ingère tous
    les codes de relance, seuls les code_relance='1' ont un score.
    """
    if n_buckets < 2 or n_buckets > 50:
        raise ValueError("n_buckets doit être entre 2 et 50.")

    clients = Client.objects.filter(
        import_ref_id=import_id, score_final__isnull=False
    )
    nb_clients = clients.count()

    # Bornes : [0.0, 0.12, 0.24, ..., 1.20]
    min_score, max_score = 0.0, 1.20
    width = (max_score - min_score) / n_buckets

    # Comptage par bucket (uniquement clients scorés)
    scores = list(clients.values_list("score_final", flat=True))

    buckets = []
    for i in range(n_buckets):
        lo = min_score + i * width
        hi = lo + width
        # Le dernier bucket inclut la borne haute (cas score=1.20 exact)
        if i == n_buckets - 1:
            count = sum(1 for s in scores if lo <= s <= hi)
        else:
            count = sum(1 for s in scores if lo <= s < hi)
        buckets.append(
            {
                "min": round(lo, 4),
                "max": round(hi, 4),
                "count": count,
                "pct": round(100 * count / nb_clients, 2) if nb_clients else 0,
            }
        )

    return {
        "import_id": import_id,
        "nb_clients": nb_clients,
        "n_buckets": n_buckets,
        "buckets": buckets,
    }


# --------------------------------------------------------------------------- #
# Comparaison entre 2 imports
# --------------------------------------------------------------------------- #


def compute_comparison(import_a_id: int, import_b_id: int) -> dict:
    """Diff entre 2 imports — vue directeur pour suivre l'évolution.

    V2 : comparaison fiable sur l'ensemble des zones (pas juste les scorés).
    Métriques utiles : zones qui se dégradent/améliorent, top deltas de solde.
    """
    from apps.clients.models import Client
    from django.db.models import Sum, Count

    a = FabImport.objects.get(id=import_a_id)
    b = FabImport.objects.get(id=import_b_id)

    kpi_a = compute_kpis(import_a_id)
    kpi_b = compute_kpis(import_b_id)

    # ====================================================================== #
    # Comparaison ZONES sur l'ensemble des clients (pas juste scorés)
    # → reflète la stabilité géographique réelle entre 2 imports
    # ====================================================================== #
    zones_set_a = set(
        Client.objects.filter(import_ref_id=import_a_id)
        .values_list("zone", flat=True)
        .distinct()
    )
    zones_set_b = set(
        Client.objects.filter(import_ref_id=import_b_id)
        .values_list("zone", flat=True)
        .distinct()
    )
    zones_communes = zones_set_a & zones_set_b
    zones_nouvelles_b = zones_set_b - zones_set_a
    zones_disparues = zones_set_a - zones_set_b

    # ====================================================================== #
    # Comparaison CLIENTS — nouvelles / disparues refs entre les 2 imports
    # ====================================================================== #
    refs_a = set(
        Client.objects.filter(import_ref_id=import_a_id)
        .values_list("reference_abonnement", flat=True)
    )
    refs_b = set(
        Client.objects.filter(import_ref_id=import_b_id)
        .values_list("reference_abonnement", flat=True)
    )
    refs_communes = refs_a & refs_b
    refs_nouvelles_b = refs_b - refs_a
    refs_disparues = refs_a - refs_b

    # Charger les détails des nouveaux clients (top 50 par solde)
    nouveaux_clients_data = list(
        Client.objects.filter(
            import_ref_id=import_b_id,
            reference_abonnement__in=list(refs_nouvelles_b)[:5000],
        )
        .order_by("-solde")[:50]
        .values(
            "id",
            "reference_abonnement",
            "nom_client",
            "zone",
            "centre_nom",
            "solde",
        )
    )

    # Charger les détails des clients disparus (top 50 par solde dans A)
    disparus_clients_data = list(
        Client.objects.filter(
            import_ref_id=import_a_id,
            reference_abonnement__in=list(refs_disparues)[:5000],
        )
        .order_by("-solde")[:50]
        .values(
            "id",
            "reference_abonnement",
            "nom_client",
            "zone",
            "centre_nom",
            "solde",
        )
    )

    return {
        "import_a": {
            "id": a.id,
            "file_date": a.file_date.isoformat(),
            "kpis": kpi_a["totaux"],
            "zones_par_priorite": kpi_a["zones_par_priorite"],
        },
        "import_b": {
            "id": b.id,
            "file_date": b.file_date.isoformat(),
            "kpis": kpi_b["totaux"],
            "zones_par_priorite": kpi_b["zones_par_priorite"],
        },
        "deltas": {
            "nb_clients": kpi_b["totaux"]["nb_clients"] - kpi_a["totaux"]["nb_clients"],
            "nb_zones": kpi_b["totaux"]["nb_zones"] - kpi_a["totaux"]["nb_zones"],
            "solde_total": kpi_b["totaux"]["solde_total"] - kpi_a["totaux"]["solde_total"],
            "arrieres_total": kpi_b["totaux"]["arrieres_total"] - kpi_a["totaux"]["arrieres_total"],
            "nb_zones_haute": kpi_b["zones_par_priorite"]["Haute"]
            - kpi_a["zones_par_priorite"]["Haute"],
        },
        "zones": {
            "total_a": len(zones_set_a),
            "total_b": len(zones_set_b),
            "communes": len(zones_communes),
            "total_nouvelles": len(zones_nouvelles_b),
            "total_disparues": len(zones_disparues),
            "nouvelles": sorted(zones_nouvelles_b)[:50],
            "disparues": sorted(zones_disparues)[:50],
        },
        "clients": {
            "total_a": len(refs_a),
            "total_b": len(refs_b),
            "communs": len(refs_communes),
            "total_nouveaux": len(refs_nouvelles_b),
            "total_disparus": len(refs_disparues),
            "nouveaux": [
                {
                    "client_id": c["id"],
                    "reference_abonnement": c["reference_abonnement"],
                    "nom_client": c["nom_client"],
                    "centre_nom": c["centre_nom"],
                    "zone": c["zone"],
                    "solde": float(c["solde"]),
                }
                for c in nouveaux_clients_data
            ],
            "disparus": [
                {
                    "client_id": c["id"],
                    "reference_abonnement": c["reference_abonnement"],
                    "nom_client": c["nom_client"],
                    "centre_nom": c["centre_nom"],
                    "zone": c["zone"],
                    "solde": float(c["solde"]),
                }
                for c in disparus_clients_data
            ],
        },
    }


# --------------------------------------------------------------------------- #
# Agrégations Top-N (centres / secteurs / tournées / releveurs)
# --------------------------------------------------------------------------- #


def aggregate_by_field(
    import_id: int, group_field: str, label: str = "value"
) -> list[dict]:
    """Agrège les clients d'un import par `group_field` et calcule la priorité.

    Reproduit la logique `agg_by()` du notebook (cellule 15) :
    - nb_clients, score_moyen, score_total, solde_total
    - priorite (= score_moyen × nb_clients)
    - Priorite Haute/Moyenne/Faible via quantiles 75/50 indépendants par niveau
    - Tri par priorite décroissant + rang
    """
    from django.conf import settings

    # V1.5 : on agrège uniquement les clients scorés (sinon score_moyen=null)
    qs = (
        Client.objects.filter(
            import_ref_id=import_id, score_final__isnull=False
        )
        .values(group_field)
        .annotate(
            nb_clients=Count("id"),
            score_moyen=Avg("score_final"),
            score_total=Sum("score_final"),
            solde_total=Sum("solde"),
        )
    )
    rows = list(qs)
    if not rows:
        return []

    # Calcul de la métrique composite priorite_score
    for r in rows:
        sm = r["score_moyen"] or 0
        nc = r["nb_clients"] or 0
        r["priorite_score"] = round(sm * nc, 2)
        r["score_moyen"] = round(sm, 4)
        r["score_total"] = round(r["score_total"] or 0, 4)
        r[label] = r.pop(group_field)

    # Quantiles indépendants pour ce niveau
    priority_scores = sorted(r["priorite_score"] for r in rows)
    q_high_idx = int(len(priority_scores) * settings.PRIORITY_QUANTILE_HIGH)
    q_med_idx = int(len(priority_scores) * settings.PRIORITY_QUANTILE_MED)
    q_high = priority_scores[min(q_high_idx, len(priority_scores) - 1)]
    q_med = priority_scores[min(q_med_idx, len(priority_scores) - 1)]

    for r in rows:
        ps = r["priorite_score"]
        r["priorite"] = (
            "Haute" if ps >= q_high else ("Moyenne" if ps >= q_med else "Faible")
        )

    # Tri par priorite_score décroissant + rang
    rows.sort(key=lambda r: r["priorite_score"], reverse=True)
    for i, r in enumerate(rows, start=1):
        r["rang"] = i

    return rows


# Wrappers pour clarté côté views
def aggregate_by_centre(import_id: int) -> list[dict]:
    return aggregate_by_field(import_id, "centre_nom", "centre")


def aggregate_by_secteur(import_id: int) -> list[dict]:
    return aggregate_by_field(import_id, "secteur_facturation", "secteur")


def aggregate_by_tournee(import_id: int) -> list[dict]:
    return aggregate_by_field(import_id, "tournee_releve", "tournee")


def aggregate_by_releveur(import_id: int) -> list[dict]:
    return aggregate_by_field(import_id, "releveur_1", "releveur")
