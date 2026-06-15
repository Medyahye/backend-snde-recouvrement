"""Crée un FabImport pointant vers un objet MinIO et déclenche le pipeline.

Utile pour tester le pipeline de bout en bout sans frontend.

Usage :
    python manage.py process_fab --minio-key fab20260412.txt --file-date 2026-04-12
    python manage.py process_fab --minio-key fab20260412.txt --file-date 2026-04-12 --sync
"""
import re
from datetime import datetime

from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand, CommandError

User = get_user_model()


class Command(BaseCommand):
    help = "Crée un FabImport et lance le pipeline (async par défaut, --sync pour exécution immédiate)."

    def add_arguments(self, parser):
        parser.add_argument(
            "--minio-key",
            required=True,
            help="Clé de l'objet FAB dans le bucket MinIO (ex: fab20260412.txt).",
        )
        parser.add_argument(
            "--file-date",
            help="Date du fichier au format AAAA-MM-JJ. Par défaut, déduite du nom de fichier.",
        )
        parser.add_argument(
            "--user",
            help="Username du user uploader (par défaut : admin@snde.local).",
            default="admin@snde.local",
        )
        parser.add_argument(
            "--sync",
            action="store_true",
            help="Exécute le pipeline en synchrone (debug/dev) au lieu de l'envoyer à Celery.",
        )

    def handle(self, *args, **options):
        from apps.imports.models import FabImport
        from apps.scoring.tasks import process_fab_import

        minio_key = options["minio_key"]

        # Déduction de la date depuis le nom de fichier fab AAAA MM JJ .txt
        file_date_str = options.get("file_date")
        if file_date_str:
            file_date = datetime.strptime(file_date_str, "%Y-%m-%d").date()
        else:
            m = re.search(r"fab(\d{4})(\d{2})(\d{2})\.", minio_key)
            if not m:
                raise CommandError(
                    "Impossible de déduire la date depuis la clé MinIO. "
                    "Précise --file-date AAAA-MM-JJ."
                )
            file_date = datetime(
                int(m.group(1)), int(m.group(2)), int(m.group(3))
            ).date()

        try:
            user = User.objects.get(username=options["user"])
        except User.DoesNotExist:
            raise CommandError(
                f"User '{options['user']}' introuvable. "
                "Lance `python manage.py create_default_admin` d'abord."
            )

        imp = FabImport.objects.create(
            minio_key=minio_key,
            file_date=file_date,
            uploaded_by=user,
        )
        self.stdout.write(
            self.style.SUCCESS(
                f"FabImport #{imp.id} créé : {minio_key} (date {file_date})"
            )
        )

        if options["sync"]:
            self.stdout.write("Exécution synchrone du pipeline...")
            result = process_fab_import.apply(args=[imp.id]).get()
            self.stdout.write(self.style.SUCCESS(f"Terminé : {result}"))
        else:
            process_fab_import.delay(imp.id)
            self.stdout.write(
                "Tâche envoyée à Celery. Suivre l'avancement avec :\n"
                f"  docker compose logs celery_worker --tail=20 -f\n"
                f"  docker compose exec backend python manage.py shell -c "
                f"\"from apps.imports.models import FabImport; "
                f"i=FabImport.objects.get(id={imp.id}); "
                f"print(i.status, i.nb_clients_kept, i.error_message)\""
            )
