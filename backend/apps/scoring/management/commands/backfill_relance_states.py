"""Backfill du champ `Client.relance_state` pour tous les imports existants.

À lancer une seule fois après l'ajout du champ pour calculer rétroactivement
l'état du cycle pour les clients déjà ingérés.
"""
from django.core.management.base import BaseCommand
from django.db import transaction

from apps.clients.models import Client
from apps.imports.models import FabImport
from apps.scoring.state import ClientSnapshot, derive_relance_state


class Command(BaseCommand):
    help = "Recalcule Client.relance_state pour tous les clients de tous les imports."

    def add_arguments(self, parser):
        parser.add_argument(
            "--import-id",
            type=int,
            help="Limiter à un import spécifique (sinon tous).",
        )

    def handle(self, *args, **options):
        if options["import_id"]:
            imports = FabImport.objects.filter(id=options["import_id"])
        else:
            imports = FabImport.objects.filter(
                status=FabImport.Status.DONE
            ).order_by("file_date")

        self.stdout.write(
            f"Backfill relance_state pour {imports.count()} imports...\n"
        )

        total_updated = 0
        for imp in imports:
            self.stdout.write(
                f"  → Import #{imp.id} (FAB {imp.file_date})... ", ending=""
            )
            clients = Client.objects.filter(import_ref=imp).only(
                "id",
                "code_relance",
                "date_facture",
                "date_dernier_paiement",
                "solde",
            )
            updates = []
            for c in clients:
                snap = ClientSnapshot(
                    code_relance=c.code_relance,
                    date_facture=c.date_facture,
                    date_dernier_paiement=c.date_dernier_paiement,
                    solde=c.solde,
                )
                c.relance_state = derive_relance_state(snap, imp.file_date)
                updates.append(c)

            with transaction.atomic():
                Client.objects.bulk_update(
                    updates, ["relance_state"], batch_size=1000
                )

            self.stdout.write(
                self.style.SUCCESS(f"OK · {len(updates)} clients mis à jour")
            )
            total_updated += len(updates)

        self.stdout.write(
            self.style.SUCCESS(f"\n✓ Terminé. Total : {total_updated} clients.")
        )
