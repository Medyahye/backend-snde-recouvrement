from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("clients", "0005_add_ref_date_index"),
        ("clients", "0003_client_proba_paiement"),
    ]

    operations = [
        migrations.AddField(
            model_name="client",
            name="proba_paiement",
            field=models.FloatField(
                blank=True,
                null=True,
                help_text="Probabilité de paiement prédite par le FT-Transformer [0, 1]. Null si scoring formule.",
            ),
        ),
    ]
