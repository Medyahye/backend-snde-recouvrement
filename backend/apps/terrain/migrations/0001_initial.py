from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):
    initial = True

    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ("clients", "0006_client_proba_paiement"),
        ("imports", "0003_fabimport_nb_clients_total_and_more"),
    ]

    operations = [
        migrations.CreateModel(
            name="TerrainAssignment",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("status", models.CharField(choices=[("todo", "A faire"), ("in_progress", "En cours"), ("done", "Releve fait"), ("absent", "Client absent"), ("blocked", "Compteur bloque"), ("inaccessible", "Inaccessible"), ("anomaly", "Anomalie"), ("cancelled", "Annule")], default="todo", max_length=20)),
                ("planned_order", models.PositiveIntegerField(default=0)),
                ("due_date", models.DateField(blank=True, null=True)),
                ("assigned_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("completed_at", models.DateTimeField(blank=True, null=True)),
                ("last_note", models.TextField(blank=True)),
                ("agent", models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name="terrain_assignments", to=settings.AUTH_USER_MODEL)),
                ("client", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="terrain_assignments", to="clients.client")),
                ("import_ref", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="terrain_assignments", to="imports.fabimport")),
            ],
            options={
                "db_table": "terrain_assignments",
                "ordering": ["planned_order", "id"],
            },
        ),
        migrations.CreateModel(
            name="MeterReading",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("result", models.CharField(choices=[("reading_done", "Releve fait"), ("absent", "Client absent"), ("blocked", "Compteur bloque"), ("inaccessible", "Inaccessible"), ("anomaly", "Anomalie")], max_length=20)),
                ("meter_index", models.DecimalField(blank=True, decimal_places=2, max_digits=14, null=True)),
                ("latitude", models.DecimalField(blank=True, decimal_places=7, max_digits=10, null=True)),
                ("longitude", models.DecimalField(blank=True, decimal_places=7, max_digits=10, null=True)),
                ("photo_url", models.TextField(blank=True)),
                ("comment", models.TextField(blank=True)),
                ("client_timestamp", models.DateTimeField(blank=True, null=True)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("agent", models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name="meter_readings", to=settings.AUTH_USER_MODEL)),
                ("assignment", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="readings", to="terrain.terrainassignment")),
            ],
            options={
                "db_table": "meter_readings",
                "ordering": ["-created_at"],
            },
        ),
        migrations.AddConstraint(
            model_name="terrainassignment",
            constraint=models.UniqueConstraint(fields=("import_ref", "client"), name="terrain_assignment_unique_client_import"),
        ),
        migrations.AddIndex(
            model_name="terrainassignment",
            index=models.Index(fields=["agent", "status"], name="terrain_ass_agent__601d77_idx"),
        ),
        migrations.AddIndex(
            model_name="terrainassignment",
            index=models.Index(fields=["import_ref", "agent"], name="terrain_ass_import__e7b9e9_idx"),
        ),
        migrations.AddIndex(
            model_name="terrainassignment",
            index=models.Index(fields=["client"], name="terrain_ass_client__207f04_idx"),
        ),
        migrations.AddIndex(
            model_name="meterreading",
            index=models.Index(fields=["assignment", "created_at"], name="meter_readi_assignm_8859ee_idx"),
        ),
        migrations.AddIndex(
            model_name="meterreading",
            index=models.Index(fields=["agent", "created_at"], name="meter_readi_agent_i_8d0b5f_idx"),
        ),
        migrations.AddIndex(
            model_name="meterreading",
            index=models.Index(fields=["result"], name="meter_readi_result_045f34_idx"),
        ),
    ]
