"""Management command : calcule (ou recalcule) tous les ClientBehavior.

Usage :
    python manage.py compute_behavior

Durée estimée : 1-5 min selon volume DB (utilise des agrégations SQL groupées,
pas du client-par-client).
"""
from django.core.management.base import BaseCommand

from apps.scoring.behavior import compute_all_behaviors


class Command(BaseCommand):
    help = "Calcule les profils comportementaux pour tous les clients."

    def handle(self, *args, **options):
        self.stdout.write("Calcul des ClientBehavior en cours...")
        result = compute_all_behaviors()
        self.stdout.write(self.style.SUCCESS(f"\n✓ Terminé."))
        self.stdout.write(f"  Total : {result['nb_total']:,} profils créés")
        self.stdout.write("  Par catégorie :")
        for cat, n in result["by_category"].items():
            pct = 100 * n / result["nb_total"] if result["nb_total"] else 0
            self.stdout.write(f"    {cat:8} : {n:>8,}  ({pct:.1f}%)")
        self.stdout.write("  Par type :")
        for t, n in result["by_type"].items():
            self.stdout.write(f"    {t:11} : {n:>8,}")
