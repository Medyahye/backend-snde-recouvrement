"""Recalcule TOUS les ClientMovement de tous les imports terminés.

Utile après une mise à jour de la logique de classification (Axe A V2 : ajout
du signal date_dernier_paiement).
"""
from django.core.management.base import BaseCommand

from apps.imports.models import FabImport
from apps.scoring.movements import compute_movements_for_import


class Command(BaseCommand):
    help = (
        "Recalcule les ClientMovement pour tous les FabImport en statut 'done'. "
        "Utile après modification de classify_movement (ajout colonnes, nouvelles règles)."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--sync",
            action="store_true",
            help="Exécute synchroniquement (sans passer par Celery). Recommandé.",
        )

    def handle(self, *args, **options):
        imports = FabImport.objects.filter(
            status=FabImport.Status.DONE
        ).order_by("file_date")

        self.stdout.write(
            f"Recalcul de {imports.count()} imports terminés...\n"
        )

        for imp in imports:
            self.stdout.write(
                f"  → Import #{imp.id} (FAB {imp.file_date})... ", ending=""
            )
            try:
                if options["sync"]:
                    result = compute_movements_for_import.apply(args=[imp.id]).get()
                else:
                    task = compute_movements_for_import.delay(imp.id)
                    result = {"task_id": task.id, "queued": True}
                self.stdout.write(self.style.SUCCESS(f"OK · {result}"))
            except Exception as exc:  # noqa: BLE001
                self.stdout.write(self.style.ERROR(f"ERR : {exc}"))

        self.stdout.write(self.style.SUCCESS("\n✓ Terminé."))
