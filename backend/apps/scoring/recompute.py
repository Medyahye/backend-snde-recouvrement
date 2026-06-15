"""Tâche de recalcul des scores d'un import existant sans re-uploader le FAB.

Utilisé par l'admin scoring : quand on change les poids, on peut appliquer la
nouvelle config sur un (ou plusieurs) imports déjà ingérés.

Algorithme :
1. Charger tous les clients code_relance=1 de l'import depuis la DB
2. Reconstruire un DataFrame minimal (les champs nécessaires au pipeline)
3. Recalculer scores + composantes + priorités avec la config cible
4. Recalculer l'agrégation par zone
5. Mettre à jour les rangées Client + Zone (UPDATE uniquement, pas DELETE/INSERT)
"""
from __future__ import annotations

import logging
from decimal import Decimal

import pandas as pd
from celery import shared_task
from django.db import transaction

from .config_service import ScoringParams
from .pipeline import (
    categorise_clients_priority,
    compute_score_components,
    compute_zones_aggregation,
    rank_clients,
)

logger = logging.getLogger(__name__)


def _to_decimal(val) -> Decimal:
    if val is None or pd.isna(val):
        return Decimal("0.00")
    return Decimal(str(round(float(val), 2)))


@shared_task(bind=True, name="scoring.recompute_scores_for_import")
def recompute_scores_for_import(
    self, import_id: int, config_id: int | None = None
) -> dict:
    """Recalcule scores + zones d'un import existant avec une config donnée.

    Si `config_id` est None, utilise la config active.
    """
    from apps.clients.models import Client
    from apps.imports.models import FabImport
    from apps.zones.models import Zone

    from .config_service import get_active_config
    from .models import ScoringConfig

    imp = FabImport.objects.get(id=import_id)

    # Récupérer la config cible
    if config_id is not None:
        config = ScoringConfig.objects.get(id=config_id)
    else:
        config = get_active_config()
    params = ScoringParams.from_model(config)
    logger.info(
        "Recalcul scoring : FabImport #%s avec config #%s (poids %s)",
        imp.id, config.id, params.weights,
    )

    # 1. Charger les clients code_relance=1 (les seuls scorables)
    scorable_qs = Client.objects.filter(
        import_ref=imp, code_relance="1"
    ).values(
        "id",
        "reference_abonnement",
        "activite_client",
        "centre_nom",
        "secteur_facturation",
        "tournee_releve",
        "zone",
        "solde",
        "montant_facture",
        "arrieres",
        "date_facture",
        "date_dernier_paiement",
    )
    df = pd.DataFrame.from_records(scorable_qs)
    if df.empty:
        logger.info("Aucun client à recalculer pour FabImport #%s", imp.id)
        return {"import_id": imp.id, "config_id": config.id, "nb_clients": 0}

    # Mapper en types numériques exploitables par le pipeline
    df["solde"] = df["solde"].astype(float)
    df["montant_facture"] = df["montant_facture"].astype(float)
    df["arrieres"] = df["arrieres"].astype(float)

    # 2. Pipeline scoring (avec la config cible)
    df = compute_score_components(df, imp.file_date, params=params)
    df = categorise_clients_priority(df, params=params)
    df = rank_clients(df)

    # 3. Agrégation zones
    zones_df = compute_zones_aggregation(df, params=params)

    # 4. Update des Client (uniquement les champs de scoring)
    with transaction.atomic():
        # Mapping id → row de df pour faire des UPDATE rapides
        for row in df.to_dict("records"):
            Client.objects.filter(pk=row["id"]).update(
                montant_norm=float(row["Montant_norm"]),
                anciennete_norm=float(row["Anciennete_norm"]),
                historique_norm=float(row["Historique_norm"]),
                arrieres_norm=float(row["Arrieres_norm"]),
                coefficient_type=float(row["Coefficient_type"]),
                score_final=float(row["Score"]),
                priorite=row["Priorite"],
                rang=int(row["rang"]),
            )

        # 5. Remplacer les Zone (DELETE/INSERT, c'est plus simple)
        Zone.objects.filter(import_ref=imp).delete()
        zone_objs = [
            Zone(
                import_ref=imp,
                zone_id=str(row["zone"])[:150],
                centre_nom=str(row["centre_nom"])[:100],
                secteur=str(row["secteur"])[:10],
                tournee=str(row["tournee"])[:10],
                nb_clients=int(row["nb_clients"]),
                nb_entreprises=int(row["nb_entreprises"]),
                nb_domestiques=int(row["nb_domestiques"]),
                score_moyen=float(row["score_moyen"]),
                score_max=float(row["score_max"]),
                score_total=float(row["score_total"]),
                anciennete_moyenne=float(row["anciennete_moyenne"]),
                solde_total=_to_decimal(row["solde_total"]),
                solde_moyen=_to_decimal(row["solde_moyen"]),
                arrieres_total=_to_decimal(row["arrieres_total"]),
                priorite_zone=float(row["priorite_zone"]),
                priorite=row["priorite"],
                rang=int(row["rang"]),
            )
            for row in zones_df.to_dict("records")
        ]
        Zone.objects.bulk_create(zone_objs, batch_size=500)

    logger.info(
        "Recalcul terminé : %s clients, %s zones (FabImport #%s, Config #%s)",
        len(df), len(zone_objs), imp.id, config.id,
    )

    return {
        "import_id": imp.id,
        "config_id": config.id,
        "nb_clients": len(df),
        "nb_zones": len(zone_objs),
    }
