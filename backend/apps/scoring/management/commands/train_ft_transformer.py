"""Management command : entraine le FT-Transformer sur les donnees historiques.

Usage :
    python manage.py train_ft_transformer
    python manage.py train_ft_transformer --label-window 60

Duree estimee : 2-10 min selon volume DB et disponibilite GPU.
Le modele entraine est sauvegarde dans gnn_models/ft_transformer_snde.pt.
Activer ensuite dans .env : SCORING_ENGINE=ft_transformer
"""
from django.core.management.base import BaseCommand

from apps.scoring.ft_transformer import train_model


class Command(BaseCommand):
    help = "Entraine le FT-Transformer sur les imports historiques."

    def add_arguments(self, parser):
        parser.add_argument(
            "--label-window",
            type=int,
            default=7,
            help="Nombre d'imports suivants pour labelliser un paiement (defaut: 7).",
        )

    def handle(self, *args, **options):
        label_window = options["label_window"]
        self.stdout.write("Demarrage de l'entrainement FT-Transformer...")
        self.stdout.write(f"Fenetre de label : {label_window} imports")
        self.stdout.write("(Consultez les logs Django pour le detail epoch par epoch)\n")

        metrics = train_model(label_window=label_window)

        self.stdout.write(self.style.SUCCESS("\nEntrainement termine."))
        self.stdout.write(f"  Meilleure epoch   : {metrics['epoch']}")
        self.stdout.write(f"  Val loss          : {metrics['val_loss']:.4f}")
        self.stdout.write(
            f"  AUC-ROC           : {metrics['val_auc']:.3f}  (> 0.70 = bon, > 0.80 = tres bon)"
        )
        self.stdout.write("\nPour activer le modele, ajoutez dans .env :")
        self.stdout.write("  SCORING_ENGINE=ft_transformer")
