"""Tâche Celery de synchronisation automatique S3 → MinIO.

Idempotente : ne traite que les FABs absents de la DB. Vérifie les FABs vides
côté S3 et les marque EMPTY sans déclencher le pipeline. Peut être appelée
manuellement ou via Celery Beat (planifiée).
"""
from __future__ import annotations

import io
import logging
from datetime import date

from celery import shared_task
from django.contrib.auth import get_user_model
from django.db import transaction

logger = logging.getLogger(__name__)
User = get_user_model()


def sync_s3_to_minio(
    *,
    from_date: date | None = None,
    to_date: date | None = None,
    limit: int = 0,
    owner_username: str | None = None,
    trigger_pipeline: bool = True,
) -> dict:
    """Synchronise les FABs S3 → MinIO + déclenche le pipeline.

    Retourne un résumé : {nb_listed, nb_already_in_db, nb_imported, nb_empty, nb_errors}.
    """
    from apps.imports.models import FabImport
    from apps.scoring.s3_storage import (
        download_s3_object,
        is_empty_fab,
        list_s3_fabs,
    )
    from apps.scoring.storage import upload_file_object
    from apps.scoring.tasks import process_fab_import

    logger.info("=== Sync S3 → MinIO démarré ===")

    # User propriétaire des nouveaux imports.
    # Stratégie : username explicite, sinon "admin@snde.local", sinon "admin",
    # sinon n'importe quel superuser actif. Robuste face aux changements de config.
    owner = None
    candidates = []
    if owner_username:
        candidates.append(owner_username)
    candidates += ["admin@snde.local", "admin"]
    for candidate in candidates:
        try:
            owner = User.objects.get(username=candidate)
            logger.info("Utilisateur trouvé pour la sync : %s", candidate)
            break
        except User.DoesNotExist:
            continue

    if owner is None:
        # Fallback ultime : prendre n'importe quel superuser actif
        owner = User.objects.filter(is_superuser=True, is_active=True).first()
        if owner:
            logger.info(
                "Fallback : utilisation du superuser '%s' comme propriétaire.",
                owner.username,
            )

    if owner is None:
        logger.error("Aucun utilisateur trouvé. Créer au moins un superuser.")
        return {
            "status": "error",
            "error": "Aucun utilisateur trouvé. Créer au moins un superuser.",
        }

    # 1. Lister S3
    try:
        s3_fabs = list_s3_fabs(after=from_date, before=to_date)
    except Exception as exc:
        logger.exception("Échec listage S3.")
        return {"status": "error", "error": f"Listage S3 : {exc}"}

    if not s3_fabs:
        logger.info("Aucun FAB sur S3 dans la période demandée.")
        return {
            "status": "ok",
            "nb_listed": 0,
            "nb_already_in_db": 0,
            "nb_imported": 0,
            "nb_empty": 0,
            "nb_errors": 0,
        }

    # 2. Filtrer ceux déjà en DB (idempotence)
    existing_dates = set(FabImport.objects.values_list("file_date", flat=True))
    to_import = [f for f in s3_fabs if f.file_date not in existing_dates]
    nb_already = len(s3_fabs) - len(to_import)

    if limit > 0:
        to_import = to_import[:limit]

    if not to_import:
        logger.info("Tout est déjà à jour. %s FABs déjà en DB.", nb_already)
        return {
            "status": "ok",
            "nb_listed": len(s3_fabs),
            "nb_already_in_db": nb_already,
            "nb_imported": 0,
            "nb_empty": 0,
            "nb_errors": 0,
        }

    logger.info(
        "Synchronisation : %s nouveaux FABs à traiter (sur %s sur S3, %s déjà en DB)",
        len(to_import), len(s3_fabs), nb_already,
    )

    nb_ok, nb_empty, nb_err = 0, 0, 0
    for s3_fab in to_import:
        try:
            # Télécharger depuis S3
            content = download_s3_object(s3_fab.key)

            # Détecter FAB vide (sans contenu utile)
            is_empty, nb_valid = is_empty_fab(content)
            if is_empty:
                # Créer FabImport en EMPTY sans pipeline
                with transaction.atomic():
                    FabImport.objects.create(
                        minio_key=f"s3/{s3_fab.filename}",
                        file_date=s3_fab.file_date,
                        uploaded_by=owner,
                        status=FabImport.Status.EMPTY,
                        source=FabImport.Source.S3_AUTO,
                        nb_lines_total=nb_valid,
                    )
                nb_empty += 1
                logger.info("  → %s : EMPTY (%s lignes valides)", s3_fab.filename, nb_valid)
                continue

            # Upload vers MinIO (la longueur est obligatoire pour MinIO)
            minio_key = f"s3/{s3_fab.filename}"
            upload_file_object(
                io.BytesIO(content),
                minio_key,
                length=len(content),
            )

            # Créer FabImport
            with transaction.atomic():
                imp = FabImport.objects.create(
                    minio_key=minio_key,
                    file_date=s3_fab.file_date,
                    uploaded_by=owner,
                    status=FabImport.Status.PENDING,
                    source=FabImport.Source.S3_AUTO,
                )

            # Déclencher pipeline (async via Celery)
            if trigger_pipeline:
                process_fab_import.delay(imp.id)

            nb_ok += 1
            logger.info("  → %s : OK (FabImport #%s)", s3_fab.filename, imp.id)

        except Exception as exc:
            logger.exception("  → Erreur sur %s : %s", s3_fab.filename, exc)
            nb_err += 1

    logger.info(
        "=== Sync terminé : %s OK, %s empty, %s errors ===",
        nb_ok, nb_empty, nb_err,
    )
    return {
        "status": "ok",
        "nb_listed": len(s3_fabs),
        "nb_already_in_db": nb_already,
        "nb_imported": nb_ok,
        "nb_empty": nb_empty,
        "nb_errors": nb_err,
    }


@shared_task(bind=True, name="scoring.sync_s3_daily")
def sync_s3_daily(self) -> dict:
    """Tâche Celery — appelée par Celery Beat (planifiée quotidiennement).

    Synchronise tout FAB non encore en DB. Pas de bornes de date (S3 a tout).
    """
    return sync_s3_to_minio(trigger_pipeline=True)
