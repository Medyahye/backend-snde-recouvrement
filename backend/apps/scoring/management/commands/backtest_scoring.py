"""Backtest : compare le Top-N formule vs Top-N IA sur les imports historiques.

Usage :
    python manage.py backtest_scoring
    python manage.py backtest_scoring --top 50 --imports 30

Résultat : combien de MRU récupérés dans les 7 jours suivants avec chaque méthode.
"""
import numpy as np
import pandas as pd
from django.core.management.base import BaseCommand


class Command(BaseCommand):
    help = "Backtest formule vs FT-Transformer sur les imports historiques."

    def add_arguments(self, parser):
        parser.add_argument("--top",     type=int, default=50, help="Nb clients visités (défaut: 50)")
        parser.add_argument("--imports", type=int, default=30, help="Nb imports à tester (défaut: 30)")

    def handle(self, *args, **options):
        top_n   = options["top"]
        n_imps  = options["imports"]

        from apps.clients.models import Client
        from apps.imports.models import FabImport
        from apps.recouvrement.models import ClientBehavior, ClientMovement
        from apps.scoring.ft_transformer import (
            LABEL_WINDOW, _build_feature_matrix, _get_model,
        )

        model = _get_model()
        if model is None:
            self.stderr.write("Modèle FT-Transformer introuvable. Lancez train_ft_transformer d'abord.")
            return

        import torch

        # Charger les comportements
        behavior_map = {
            r["reference_abonnement"]: r
            for r in ClientBehavior.objects.values(
                "reference_abonnement", "behavior_score", "payment_freq_score",
                "nb_code_1", "nb_payments", "nb_imports_seen",
            )
        }

        imports = list(
            FabImport.objects.filter(status=FabImport.Status.DONE)
            .order_by("file_date")
        )
        if len(imports) < LABEL_WINDOW + 1:
            self.stderr.write("Pas assez d'imports.")
            return

        # Prendre les imports du milieu (pas les tout derniers — on a besoin de 7 suivants)
        testable = imports[:-(LABEL_WINDOW)]
        sample   = testable[-n_imps:]  # les N plus récents parmi testables

        results = []

        for idx, imp in enumerate(sample):
            # Clients scorés dans cet import avec score formule stocké
            rows = list(
                Client.objects.filter(
                    import_ref=imp,
                    code_relance="1",
                    montant_norm__isnull=False,
                    score_final__isnull=False,
                ).values(
                    "reference_abonnement", "nom_client",
                    "montant_norm", "anciennete_norm", "historique_norm",
                    "arrieres_norm", "coefficient_type", "type_client",
                    "jours_impaye", "solde", "score_final",
                )
            )
            if not rows:
                continue

            df = pd.DataFrame(rows)

            # Score formule = score_final stocké (seulement si < 2, sinon c'est du IA)
            df = df[df["score_final"] < 2.0]
            if df.empty:
                continue

            # Score IA : inférence du modèle
            feat = _build_feature_matrix(df, behavior_map)
            with torch.no_grad():
                probs = torch.sigmoid(
                    model(torch.from_numpy(feat))
                ).numpy()

            df["proba_ia"]    = probs
            df["score_ia"]    = df["proba_ia"] * df["solde"].astype(float)
            df["score_forme"] = df["score_final"].astype(float)

            # Paiements réels dans les 7 imports suivants
            imp_idx       = imports.index(imp)
            future        = imports[imp_idx + 1: imp_idx + 1 + LABEL_WINDOW]
            paid_data     = dict(
                ClientMovement.objects.filter(
                    reference_abonnement__in=df["reference_abonnement"].tolist(),
                    import_to__in=future,
                    type__in=["payment_certain", "payment_likely"],
                ).values_list("reference_abonnement", "delta_solde")
            )

            # Top-N par chaque méthode
            top_forme = set(df.nlargest(top_n, "score_forme")["reference_abonnement"])
            top_ia    = set(df.nlargest(top_n, "score_ia")["reference_abonnement"])

            def mru_recovered(refs):
                return sum(float(paid_data[r]) for r in refs if r in paid_data)

            def nb_paid(refs):
                return sum(1 for r in refs if r in paid_data)

            results.append({
                "date":         str(imp.file_date),
                "nb_clients":   len(df),
                "mru_forme":    mru_recovered(top_forme),
                "mru_ia":       mru_recovered(top_ia),
                "nb_paid_forme": nb_paid(top_forme),
                "nb_paid_ia":    nb_paid(top_ia),
            })

            if (idx + 1) % 10 == 0:
                self.stdout.write(f"  {idx+1}/{len(sample)} imports traités...")

        if not results:
            self.stderr.write("Aucun résultat. Vérifiez que les imports ont des scores formule (score_final < 2).")
            return

        df_r = pd.DataFrame(results)
        total_forme   = df_r["mru_forme"].sum()
        total_ia      = df_r["mru_ia"].sum()
        gain_mru      = total_ia - total_forme
        gain_pct      = (gain_mru / total_forme * 100) if total_forme > 0 else 0
        paid_forme    = df_r["nb_paid_forme"].sum()
        paid_ia       = df_r["nb_paid_ia"].sum()

        self.stdout.write("\n" + "═" * 60)
        self.stdout.write(self.style.SUCCESS("  BACKTEST : Formule vs FT-Transformer"))
        self.stdout.write("═" * 60)
        self.stdout.write(f"  Imports testés        : {len(results)}")
        self.stdout.write(f"  Top-N visités         : {top_n} clients par import")
        self.stdout.write("")
        self.stdout.write(f"  {'Méthode':<25} {'Clients payés':>15} {'MRU récupérés':>15}")
        self.stdout.write(f"  {'-'*55}")
        self.stdout.write(f"  {'Formule (avant)':<25} {paid_forme:>15,} {total_forme:>15,.0f} MRU")
        self.stdout.write(f"  {'IA (FT-Transformer)':<25} {paid_ia:>15,} {total_ia:>15,.0f} MRU")
        self.stdout.write(f"  {'-'*55}")
        self.stdout.write(self.style.SUCCESS(
            f"  Gain IA               : +{gain_mru:>14,.0f} MRU  (+{gain_pct:.1f}%)"
        ))
        self.stdout.write("═" * 60)
        self.stdout.write("")
        self.stdout.write("  Interprétation : en visitant les mêmes 50 clients,")
        self.stdout.write(f"  l'IA aurait récupéré {gain_pct:.1f}% de MRU en plus que la formule.")
