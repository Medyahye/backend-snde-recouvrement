from __future__ import annotations

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

import pandas as pd


class Command(BaseCommand):
    help = (
        "Calcule proba_paiement avec le FT-Transformer pour les clients scorables "
        "d'un import FAB existant."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "import_id",
            nargs="?",
            type=int,
            help="ID du FabImport a scorer. Si absent, utilise le dernier import DONE.",
        )

    def handle(self, *args, **options):
        from apps.clients.models import Client
        from apps.imports.models import FabImport
        from apps.scoring.ft_transformer import predict_scores

        import_id = options.get("import_id")
        if import_id:
            try:
                imp = FabImport.objects.get(id=import_id)
            except FabImport.DoesNotExist as exc:
                raise CommandError(f"FabImport #{import_id} introuvable.") from exc
        else:
            imp = FabImport.objects.filter(status=FabImport.Status.DONE).order_by("-file_date", "-id").first()
            if imp is None:
                raise CommandError("Aucun FabImport DONE trouve.")

        clients = list(
            Client.objects.filter(
                import_ref=imp,
                code_relance="1",
                solde__gt=0,
                montant_norm__isnull=False,
            ).order_by("id")
        )
        if not clients:
            self.stdout.write(self.style.WARNING(f"Aucun client scorable pour import #{imp.id}."))
            return

        df = pd.DataFrame(
            [
                {
                    "id": c.id,
                    "reference_abonnement": c.reference_abonnement,
                    "montant_norm": c.montant_norm,
                    "anciennete_norm": c.anciennete_norm,
                    "historique_norm": c.historique_norm,
                    "arrieres_norm": c.arrieres_norm,
                    "coefficient_type": c.coefficient_type,
                    "type_client": c.type_client,
                    "jours_impaye": c.jours_impaye,
                    "solde": float(c.solde),
                    "Score": c.score_final or 0.0,
                }
                for c in clients
            ]
        )

        probabilities = predict_scores(df).astype(float)
        df["proba_paiement"] = probabilities.clip(0, 1)
        df["Score"] = df["proba_paiement"] * df["solde"]
        df = df.sort_values("Score", ascending=False).reset_index(drop=True)
        df["rang"] = range(1, len(df) + 1)

        q75 = df["Score"].quantile(0.75)
        q50 = df["Score"].quantile(0.50)
        df["priorite"] = df["Score"].apply(
            lambda value: "Haute" if value >= q75 else ("Moyenne" if value >= q50 else "Faible")
        )

        by_id = df.set_index("id").to_dict("index")
        for client in clients:
            scored = by_id[client.id]
            client.proba_paiement = float(scored["proba_paiement"])
            client.score_final = float(scored["Score"])
            client.priorite = scored["priorite"]
            client.rang = int(scored["rang"])

        with transaction.atomic():
            Client.objects.bulk_update(
                clients,
                ["proba_paiement", "score_final", "priorite", "rang"],
                batch_size=1000,
            )

        self.stdout.write(
            self.style.SUCCESS(
                f"Import #{imp.id} score IA: {len(clients)} clients mis a jour."
            )
        )
