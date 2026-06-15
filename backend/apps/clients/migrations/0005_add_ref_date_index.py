"""Index composé pour accélérer la récupération du dernier snapshot par ref.

Sans cet index, la requête `DISTINCT ON (reference_abonnement) ... ORDER BY
reference_abonnement, file_date DESC` doit faire un tri global sur 85M lignes.
Avec l'index, elle utilise un parcours en ordre d'index → ~10× plus rapide.
"""
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("clients", "0004_client_relance_state_and_more"),
    ]

    operations = [
        migrations.AddIndex(
            model_name="client",
            index=models.Index(
                fields=["reference_abonnement", "import_ref"],
                name="clients_ref_import_idx",
            ),
        ),
    ]
