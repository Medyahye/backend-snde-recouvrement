"""Tâches Celery du pipeline FAB → ingestion + scoring + agrégation → DB.

V1.5 : on stocke TOUS les clients éligibles (codes 0/2/3/4 inclus, pas seulement 1)
pour permettre le suivi du cycle de relance et la détection des paiements
(transitions 1 → 0 entre 2 imports).
Seuls les clients code_relance="1" sont scorés et classés.
"""
import logging
from decimal import Decimal

import pandas as pd
from celery import shared_task
from django.db import transaction
from django.utils import timezone

from .movements import compute_movements_for_import  # noqa: F401 — enregistre la tâche
from .recompute import recompute_scores_for_import  # noqa: F401 — enregistre la tâche
from .parser import parse_fab_text
from .pipeline import (
    categorise_clients_priority,
    compute_score_components,
    compute_zones_aggregation,
    filter_eligible_clients,
    filter_for_scoring,
    map_centres_and_zone,
    rank_clients,
)
from .storage import download_fab

logger = logging.getLogger(__name__)


@shared_task(name="scoring.ping")
def ping() -> str:
    """Tâche de validation pour vérifier que Celery fonctionne."""
    return "pong"


def _to_pydate(val):
    """Convertit un Timestamp/NaT pandas en datetime.date Python (ou None)."""
    if val is None or pd.isna(val):
        return None
    return val.date() if hasattr(val, "date") else val


def _to_decimal(val) -> Decimal:
    """Convertit un float/Decimal en Decimal arrondi à 2 décimales (pour les MRU)."""
    if val is None or pd.isna(val):
        return Decimal("0.00")
    return Decimal(str(round(float(val), 2)))


def _safe_get(row: dict, key: str, default=None):
    """Lit une clé d'un dict pandas en gérant les NaN."""
    val = row.get(key, default)
    if val is None or (isinstance(val, float) and pd.isna(val)):
        return default
    return val


@shared_task(bind=True, name="scoring.process_fab_import")
def process_fab_import(self, import_id: int) -> dict:
    """Pipeline complet pour un FabImport :

    1. Télécharge le FAB depuis MinIO
    2. Parse → DataFrame
    3. Filtre clients éligibles (tous codes de relance)
    4. Mappe code_centre → centre_nom + construit la zone
    5. Sous-ensemble scorable (code_relance='1') → calcule scores, priorités, rang
    6. Agrège par zone (sur scorés uniquement)
    7. Bulk insert Client (tous éligibles, avec is_scored marqué) + Zone
    8. Met à jour le statut du FabImport
    """
    from apps.clients.models import Client
    from apps.imports.models import FabImport
    from apps.zones.models import Centre, Zone

    imp = FabImport.objects.get(id=import_id)
    imp.status = FabImport.Status.PROCESSING
    imp.started_at = timezone.now()
    imp.error_message = ""
    imp.save(update_fields=["status", "started_at", "error_message"])
    logger.info("Pipeline démarré pour FabImport #%s (%s)", imp.id, imp.minio_key)

    try:
        # 1. Télécharger
        raw = download_fab(imp.minio_key)
        # Auto-détection encodage (UTF-8 manuel / UTF-16 exports S3 Windows…)
        from .parser import decode_fab_bytes

        content = decode_fab_bytes(raw)

        # 2. Parser
        df, stats = parse_fab_text(content, imp.file_date)
        imp.nb_lines_total = stats["total"]
        imp.save(update_fields=["nb_lines_total"])
        logger.info(
            "FAB parsé : %s lignes total, %s valides, %s invalides",
            stats["total"], stats["valid"], stats["invalid"],
        )

        # 3. Filtre éligibilité (tous codes : 0, 1, 2, 3, 4)
        df_all = filter_eligible_clients(df)
        nb_eligible = len(df_all)
        logger.info("Clients éligibles (tous codes) : %s", nb_eligible)

        # 4. Mapper centres + zone (sur tous les clients)
        centres_map = dict(Centre.objects.values_list("code", "nom"))
        df_all = map_centres_and_zone(df_all, centres_map)

        # 5. Sous-ensemble scorable (code_relance='1') → scoring + classement
        df_scored = filter_for_scoring(df_all)
        df_scored = compute_score_components(df_scored, imp.file_date)
        df_scored = categorise_clients_priority(df_scored)
        df_scored = rank_clients(df_scored)
        nb_scored = len(df_scored)
        logger.info("Clients scorés (code_relance='1') : %s", nb_scored)

        # 6. Agrégation par zone (sur scorés uniquement)
        zones_df = compute_zones_aggregation(df_scored)
        nb_zones = len(zones_df)

        # Construire un index ref → données scoring pour les retrouver lors de l'insert
        scored_by_ref = {
            r["reference_abonnement"]: r for r in df_scored.to_dict("records")
        }

        # 7. Insertions atomiques
        with transaction.atomic():
            # Idempotence : nettoyer existant pour ce FabImport
            Client.objects.filter(import_ref=imp).delete()
            Zone.objects.filter(import_ref=imp).delete()

            # Pour le calcul de relance_state
            from .state import ClientSnapshot, derive_relance_state

            # Insert : tous les éligibles (df_all), avec scoring si présent dans scored_by_ref
            client_objs = []
            for row in df_all.to_dict("records"):
                ref = str(row["reference_abonnement"]).strip()
                scored = scored_by_ref.get(ref)

                # On a besoin de type_client et jours_impaye / jours_sans_paiement même
                # pour les non-scorés. Si pas calculés (car pas passés par compute_score_components),
                # on les calcule à la volée ici.
                if scored:
                    type_client = scored["type_client"]
                    jours_impaye = int(scored["jours_impaye"])
                    jours_sans_paiement = int(scored["jours_sans_paiement"])
                else:
                    # Type client basé sur l'activité (même règle que pipeline)
                    from django.conf import settings
                    act = str(row.get("activite_client") or "").strip()
                    type_client = (
                        "Domestique"
                        if act in settings.DOMESTIQUE_ACTIVITIES
                        else "Entreprise"
                    )
                    # Jours depuis les dates (peuvent être None pour les non-scorés)
                    ref_date = pd.Timestamp(imp.file_date)
                    df_facture = pd.to_datetime(row.get("date_facture"), errors="coerce")
                    df_paiement = pd.to_datetime(
                        row.get("date_dernier_paiement"), errors="coerce"
                    )
                    jours_impaye = (
                        max(0, (ref_date - df_facture).days)
                        if pd.notna(df_facture)
                        else 0
                    )
                    jours_sans_paiement = (
                        max(0, (ref_date - df_paiement).days)
                        if pd.notna(df_paiement)
                        else 0
                    )

                # Dériver le relance_state (état dans le cycle SNDE)
                solde_dec = _to_decimal(row["solde"])
                snap = ClientSnapshot(
                    code_relance=str(row.get("code_relance") or "").strip(),
                    date_facture=_to_pydate(row.get("date_facture")),
                    date_dernier_paiement=_to_pydate(
                        row.get("date_dernier_paiement")
                    ),
                    solde=solde_dec,
                )
                relance_state = derive_relance_state(snap, imp.file_date)

                client_objs.append(
                    Client(
                        import_ref=imp,
                        reference_abonnement=ref[:20],
                        nom_client=str(row["nom_client"])[:200],
                        adresse=str(_safe_get(row, "adresse", ""))[:300],
                        telephone=str(_safe_get(row, "telephone", ""))[:30],
                        activite_client=str(_safe_get(row, "activite_client", ""))[:100],
                        type_client=type_client,
                        code_centre=str(row["code_centre"])[:10],
                        centre_nom=str(row["centre_nom"])[:100],
                        secteur_facturation=str(row["secteur_facturation"]).strip()[:10],
                        tournee_releve=str(row["tournee_releve"]).strip()[:10],
                        releveur_1=str(_safe_get(row, "releveur_1", ""))[:20],
                        zone=str(row["zone"])[:150],
                        solde=solde_dec,
                        montant_facture=_to_decimal(row["montant_facture"]),
                        arrieres=_to_decimal(row["arrieres"]),
                        date_facture=_to_pydate(row.get("date_facture")),
                        date_dernier_paiement=_to_pydate(
                            row.get("date_dernier_paiement")
                        ),
                        jours_impaye=jours_impaye,
                        jours_sans_paiement=jours_sans_paiement,
                        code_relance=str(row.get("code_relance") or "")[:2],
                        relance_state=relance_state,
                        # Champs scoring : remplis seulement pour les scored.
                        # `is_scored` est dérivé automatiquement (= score_final non null).
                        montant_norm=(
                            float(scored["Montant_norm"]) if scored else None
                        ),
                        anciennete_norm=(
                            float(scored["Anciennete_norm"]) if scored else None
                        ),
                        historique_norm=(
                            float(scored["Historique_norm"]) if scored else None
                        ),
                        arrieres_norm=(
                            float(scored["Arrieres_norm"]) if scored else None
                        ),
                        coefficient_type=(
                            float(scored["Coefficient_type"]) if scored else None
                        ),
                        score_final=(float(scored["Score"]) if scored else None),
                        proba_paiement=(
                            float(scored["proba_paiement"])
                            if scored and "proba_paiement" in scored
                            else None
                        ),
                        priorite=(scored["Priorite"] if scored else ""),
                        rang=(int(scored["rang"]) if scored else None),
                    )
                )
            Client.objects.bulk_create(client_objs, batch_size=1000)

            # Zones (basées sur scorés)
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

        # 8. Statut final
        # nb_clients_kept = scorés (code_relance='1') — rétro-compat
        # nb_clients_total = tous éligibles ingérés en DB (tous codes)
        imp.nb_clients_kept = nb_scored
        imp.nb_clients_total = nb_eligible
        imp.status = FabImport.Status.DONE
        imp.finished_at = timezone.now()
        imp.save(update_fields=["nb_clients_kept", "nb_clients_total", "status", "finished_at"])
        logger.info(
            "Pipeline terminé pour FabImport #%s — %s éligibles, %s scorés, %s zones",
            imp.id, nb_eligible, nb_scored, nb_zones,
        )

        # 9. Calcul des mouvements (recouvrement) — déclenché en async après commit DB
        transaction.on_commit(
            lambda: compute_movements_for_import.delay(imp.id)
        )

        return {
            "import_id": imp.id,
            "nb_lines_total": stats["total"],
            "nb_eligible": nb_eligible,
            "nb_clients_kept": nb_scored,
            "nb_zones": nb_zones,
        }

    except Exception as exc:  # noqa: BLE001
        logger.exception("Pipeline en échec pour FabImport #%s", imp.id)
        imp.status = FabImport.Status.FAILED
        imp.error_message = f"{type(exc).__name__}: {exc}"[:5000]
        imp.finished_at = timezone.now()
        imp.save(update_fields=["status", "error_message", "finished_at"])
        raise


# --------------------------------------------------------------------------- #
# Re-export des taches definies ailleurs (pour l'auto-discovery Celery).
# Celery scanne uniquement les fichiers nommes `tasks.py` dans chaque app.
# Sans ce import, les taches dans sync_s3.py ne sont pas enregistrees.
# --------------------------------------------------------------------------- #
from apps.scoring.sync_s3 import sync_s3_daily  # noqa: F401, E402
