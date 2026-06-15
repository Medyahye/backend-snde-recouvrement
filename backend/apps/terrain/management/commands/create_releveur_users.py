from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand
from django.db.models import Count

from apps.clients.models import Client
from apps.imports.models import FabImport


User = get_user_model()


class Command(BaseCommand):
    help = (
        "Cree les comptes terrain depuis les identifiants releveur_1 du FAB. "
        "Le username du compte = releveur_1."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--import-id",
            type=int,
            help="Import FAB a utiliser. Par defaut: dernier import DONE.",
        )
        parser.add_argument(
            "--password",
            default="terrain123",
            help="Mot de passe initial des nouveaux comptes terrain.",
        )
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Affiche ce qui serait cree sans modifier la base.",
        )

    def handle(self, *args, **options):
        import_id = options.get("import_id")
        password = options["password"]
        dry_run = options["dry_run"]

        if import_id:
            fab_import = FabImport.objects.get(pk=import_id)
        else:
            fab_import = (
                FabImport.objects.filter(status=FabImport.Status.DONE)
                .order_by("-file_date")
                .first()
            )

        if fab_import is None:
            self.stderr.write("Aucun import DONE trouve.")
            return

        rows = (
            Client.objects.filter(import_ref=fab_import)
            .exclude(releveur_1="")
            .values("releveur_1")
            .annotate(nb_clients=Count("id"))
            .order_by("releveur_1")
        )

        created = 0
        existing = 0

        self.stdout.write(
            f"Import utilise: #{fab_import.id} ({fab_import.file_date})"
        )

        for row in rows:
            username = str(row["releveur_1"]).strip()
            if not username:
                continue

            user_exists = User.objects.filter(username=username).exists()
            if user_exists:
                existing += 1
                self.stdout.write(
                    f"= existe deja: {username} ({row['nb_clients']} clients)"
                )
                continue

            if dry_run:
                created += 1
                self.stdout.write(
                    f"+ serait cree: {username} ({row['nb_clients']} clients)"
                )
                continue

            user = User(username=username, role=User.Role.TERRAIN, is_active=True)
            user.set_password(password)
            user.save()
            created += 1
            self.stdout.write(
                f"+ cree: {username} ({row['nb_clients']} clients)"
            )

        if dry_run:
            self.stdout.write(
                self.style.WARNING(
                    f"Dry-run termine: {created} comptes a creer, {existing} existants."
                )
            )
        else:
            self.stdout.write(
                self.style.SUCCESS(
                    f"Termine: {created} comptes crees, {existing} existants."
                )
            )
