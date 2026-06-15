"""Management command : exporte le dataset d'entraînement FT-Transformer.

Usage :
    python manage.py export_training_data
    python manage.py export_training_data --output /app/dataset.npz

Produit un fichier .npz (numpy) contenant :
  - X       : matrice [N, 10] des features
  - y       : vecteur [N] des labels (1=a payé dans 7j, 0=non)
  - feature_names : noms des 10 colonnes

Télécharger ensuite avec :
    docker cp snde-backend:/app/dataset.npz ./dataset.npz
"""
from django.core.management.base import BaseCommand

import numpy as np

from apps.scoring.ft_transformer import FEATURE_NAMES, extract_training_data


class Command(BaseCommand):
    help = "Exporte le dataset d'entraînement FT-Transformer en fichier .npz"

    def add_arguments(self, parser):
        parser.add_argument(
            "--output",
            default="/app/dataset.npz",
            help="Chemin de sortie (défaut: /app/dataset.npz)",
        )
        parser.add_argument(
            "--label-window",
            type=int,
            default=7,
            help="Nombre d'imports suivants pour labelliser un paiement (defaut: 7).",
        )

    def handle(self, *args, **options):
        output = options["output"]
        label_window = options["label_window"]

        self.stdout.write(
            f"Extraction du dataset en cours avec label_window={label_window}..."
        )
        X, y = extract_training_data(label_window=label_window)

        np.savez_compressed(
            output,
            X=X,
            y=y,
            feature_names=np.array(FEATURE_NAMES),
            label_window=np.array(label_window),
        )

        pos_rate = y.mean() * 100
        self.stdout.write(self.style.SUCCESS(f"\n✓ Dataset exporté : {output}"))
        self.stdout.write(f"  Exemples     : {len(y):,}")
        self.stdout.write(f"  Features     : {X.shape[1]}")
        self.stdout.write(f"  Fenetre label: {label_window} imports")
        self.stdout.write(f"  Taux payeurs : {pos_rate:.1f}%")
        self.stdout.write(f"\nPour télécharger :")
        self.stdout.write(f"  docker cp snde-backend:/app/dataset.npz ./dataset.npz")
