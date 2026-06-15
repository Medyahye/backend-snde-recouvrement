"""Backfill historique : importe tous les FABs disponibles sur S3.

Pour chaque FAB sur S3 qui n'est pas encore en base :
  1. Télécharge le fichier depuis S3
  2. Détecte s'il est vide → status EMPTY (pas de pipeline lancé)
  3. Sinon : pousse vers MinIO (clé `s3/<filename>`) + crée FabImport
  4. Déclenche `process_fab_import` (sync ou async selon options)

Usage :
    python manage.py s3_backfill                  # tout, async via Celery
    python manage.py s3_backfill --sync           # tout, sync (debug)
    python manage.py s3_backfill --limit 10       # 10 fichiers max
    python manage.py s3_backfill --from-date 2025-12-01 --to-date 2026-02-28
    python manage.py s3_backfill --dry-run        # liste sans rien faire
    python manage.py s3_backfill --test           # test connexion seulement
"""
import io
from datetime import datetime

from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand
from django.db import transaction

User = get_user_model()


class Command(BaseCommand):
    help = "Backfill : ingère tous les FABs disponibles sur AWS S3 vers MinIO + pipeline."

    def add_arguments(self, parser):
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Liste ce qui serait fait sans rien importer.",
        )
        parser.add_argument(
            "--sync",
            action="store_true",
            help="Exécute le pipeline en synchrone (sans Celery).",
        )
        parser.add_argument(
            "--limit",
            type=int,
            default=0,
            help="Nombre max de fichiers à importer (0 = pas de limite).",
        )
        parser.add_argument(
            "--from-date",
            type=str,
            help="Ne traite que les FABs >= cette date (AAAA-MM-JJ).",
        )
        parser.add_argument(
            "--to-date",
            type=str,
            help="Ne traite que les FABs <= cette date (AAAA-MM-JJ).",
        )
        parser.add_argument(
            "--test",
            action="store_true",
            help="Test la connexion S3 sans rien importer.",
        )
        parser.add_argument(
            "--user",
            type=str,
            default="admin@snde.local",
            help="User propriétaire des FabImport créés (défaut: admin@snde.local).",
        )

    def handle(self, *args, **options):
        from apps.imports.models import FabImport
        from apps.scoring.s3_storage import (
            download_s3_object,
            is_empty_fab,
            list_s3_fabs,
            test_s3_connection,
        )
        from apps.scoring.storage import upload_file_object
        from apps.scoring.tasks import process_fab_import

        # --- Mode test ---
        if options["test"]:
            self.stdout.write("Test connexion S3...")
            try:
                result = test_s3_connection()
                self.stdout.write(self.style.SUCCESS("✓ Connexion OK"))
                self.stdout.write(f"  Bucket  : {result['bucket']}")
                self.stdout.write(f"  Prefix  : {result['prefix'] or '(racine)'}")
                self.stdout.write(
                    f"  Objets  : {result['nb_objects_sample']} (échantillon)"
                )
                for k in result["sample_keys"]:
                    self.stdout.write(f"    • {k}")
            except Exception as exc:
                self.stdout.write(self.style.ERROR(f"✗ Erreur : {exc}"))
            return

        # --- Parsing des bornes de date ---
        from_date = (
            datetime.strptime(options["from_date"], "%Y-%m-%d").date()
            if options["from_date"]
            else None
        )
        to_date = (
            datetime.strptime(options["to_date"], "%Y-%m-%d").date()
            if options["to_date"]
            else None
        )

        # --- User propriétaire ---
        try:
            owner = User.objects.get(username=options["user"])
        except User.DoesNotExist:
            self.stdout.write(
                self.style.ERROR(
                    f"User '{options['user']}' introuvable. Lance `create_default_admin`."
                )
            )
            return

        # --- 1. Lister les FABs sur S3 ---
        self.stdout.write(f"Liste des FABs sur S3...")
        try:
            s3_fabs = list_s3_fabs(after=from_date, before=to_date)
        except Exception as exc:
            self.stdout.write(self.style.ERROR(f"Échec listage S3 : {exc}"))
            return

        if not s3_fabs:
            self.stdout.write(self.style.WARNING("Aucun FAB trouvé sur S3."))
            return

        self.stdout.write(self.style.SUCCESS(f"  → {len(s3_fabs)} FABs trouvés."))
        self.stdout.write(f"  De : {s3_fabs[0].file_date}")
        self.stdout.write(f"  À  : {s3_fabs[-1].file_date}")

        # --- 2. Filtrer ceux déjà en DB ---
        existing_dates = set(
            FabImport.objects.values_list("file_date", flat=True)
        )
        to_import = [f for f in s3_fabs if f.file_date not in existing_dates]
        self.stdout.write(
            f"  → {len(to_import)} nouveaux ({len(s3_fabs) - len(to_import)} déjà en DB)"
        )

        if options["limit"] and options["limit"] > 0:
            to_import = to_import[: options["limit"]]
            self.stdout.write(f"  → limité à {len(to_import)} (option --limit)")

        if not to_import:
            self.stdout.write(self.style.SUCCESS("Rien à importer. Tout est déjà à jour."))
            return

        # --- 3. Dry-run : on s'arrête ici ---
        if options["dry_run"]:
            self.stdout.write(self.style.WARNING("\n[DRY-RUN] Aucune action effectuée."))
            self.stdout.write("Les fichiers suivants seraient importés :")
            for f in to_import[:20]:
                size_kb = f.size // 1024
                self.stdout.write(f"  • {f.filename} ({f.file_date}, {size_kb} Ko)")
            if len(to_import) > 20:
                self.stdout.write(f"  ... et {len(to_import) - 20} autres")
            return

        # --- 4. Importer (download + upload MinIO + créer FabImport + trigger) ---
        nb_ok, nb_empty, nb_err = 0, 0, 0
        for i, s3_fab in enumerate(to_import, 1):
            prefix = f"[{i}/{len(to_import)}]"
            self.stdout.write(f"{prefix} {s3_fab.filename} ({s3_fab.file_date})... ", ending="")

            try:
                # Download depuis S3
                content = download_s3_object(s3_fab.key)
                size_kb = len(content) // 1024

                # Détection FAB vide
                empty, valid_lines = is_empty_fab(content)

                # Clé MinIO mirrored (préserve la structure pour traçabilité)
                minio_key = f"s3/{s3_fab.filename}"

                # Upload MinIO (même si vide → on garde la trace du fichier)
                upload_file_object(
                    io.BytesIO(content),
                    minio_key=minio_key,
                    length=len(content),
                    content_type="text/plain",
                )

                # Créer FabImport
                with transaction.atomic():
                    if empty:
                        imp = FabImport.objects.create(
                            minio_key=minio_key,
                            file_date=s3_fab.file_date,
                            uploaded_by=owner,
                            source=FabImport.Source.S3_BACKFILL,
                            status=FabImport.Status.EMPTY,
                            nb_lines_total=valid_lines,
                            error_message=(
                                f"FAB vide ({valid_lines} lignes valides, "
                                f"seuil {180})."
                            ),
                        )
                        nb_empty += 1
                        self.stdout.write(
                            self.style.WARNING(
                                f"VIDE ({valid_lines} lignes, {size_kb} Ko)"
                            )
                        )
                    else:
                        imp = FabImport.objects.create(
                            minio_key=minio_key,
                            file_date=s3_fab.file_date,
                            uploaded_by=owner,
                            source=FabImport.Source.S3_BACKFILL,
                            status=FabImport.Status.PENDING,
                        )

                        if options["sync"]:
                            transaction.on_commit(
                                lambda imp_id=imp.id: process_fab_import.apply(
                                    args=[imp_id]
                                ).get()
                            )
                        else:
                            transaction.on_commit(
                                lambda imp_id=imp.id: process_fab_import.delay(imp_id)
                            )

                        nb_ok += 1
                        self.stdout.write(
                            self.style.SUCCESS(f"OK ({size_kb} Ko)")
                        )
            except Exception as exc:
                nb_err += 1
                self.stdout.write(self.style.ERROR(f"ERR : {exc}"))

        # --- 5. Récap ---
        self.stdout.write("")
        self.stdout.write(self.style.SUCCESS(f"✓ Terminé. {nb_ok} OK, {nb_empty} vides, {nb_err} erreurs."))
        if not options["sync"]:
            self.stdout.write(
                "  Les pipelines tournent en arrière-plan via Celery. "
                "Suivre avec : docker compose logs celery_worker -f"
            )
