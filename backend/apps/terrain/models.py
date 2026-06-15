from __future__ import annotations

from django.conf import settings
from django.db import models


class TerrainAssignment(models.Model):
    """Client affecte a un releveur pour une tournee terrain."""

    class Status(models.TextChoices):
        TODO = "todo", "A faire"
        IN_PROGRESS = "in_progress", "En cours"
        DONE = "done", "Releve fait"
        ABSENT = "absent", "Client absent"
        BLOCKED = "blocked", "Compteur bloque"
        INACCESSIBLE = "inaccessible", "Inaccessible"
        ANOMALY = "anomaly", "Anomalie"
        CANCELLED = "cancelled", "Annule"

    import_ref = models.ForeignKey(
        "imports.FabImport",
        on_delete=models.CASCADE,
        related_name="terrain_assignments",
    )
    client = models.ForeignKey(
        "clients.Client",
        on_delete=models.CASCADE,
        related_name="terrain_assignments",
    )
    agent = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="terrain_assignments",
    )
    status = models.CharField(
        max_length=20,
        choices=Status.choices,
        default=Status.TODO,
    )
    planned_order = models.PositiveIntegerField(default=0)
    due_date = models.DateField(null=True, blank=True)
    assigned_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    completed_at = models.DateTimeField(null=True, blank=True)
    last_note = models.TextField(blank=True)

    class Meta:
        db_table = "terrain_assignments"
        ordering = ["planned_order", "id"]
        constraints = [
            models.UniqueConstraint(
                fields=["import_ref", "client"],
                name="terrain_assignment_unique_client_import",
            )
        ]
        indexes = [
            models.Index(fields=["agent", "status"]),
            models.Index(fields=["import_ref", "agent"]),
            models.Index(fields=["client"]),
        ]

    def __str__(self) -> str:
        return f"{self.agent} -> {self.client.reference_abonnement}"


class MeterReading(models.Model):
    """Retour terrain synchronise par l'application mobile."""

    class Result(models.TextChoices):
        READING_DONE = "reading_done", "Releve fait"
        ABSENT = "absent", "Client absent"
        BLOCKED = "blocked", "Compteur bloque"
        INACCESSIBLE = "inaccessible", "Inaccessible"
        ANOMALY = "anomaly", "Anomalie"

    assignment = models.ForeignKey(
        TerrainAssignment,
        on_delete=models.CASCADE,
        related_name="readings",
    )
    agent = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="meter_readings",
    )
    result = models.CharField(max_length=20, choices=Result.choices)
    meter_index = models.DecimalField(
        max_digits=14,
        decimal_places=2,
        null=True,
        blank=True,
    )
    latitude = models.DecimalField(
        max_digits=10,
        decimal_places=7,
        null=True,
        blank=True,
    )
    longitude = models.DecimalField(
        max_digits=10,
        decimal_places=7,
        null=True,
        blank=True,
    )
    photo_url = models.TextField(blank=True)
    comment = models.TextField(blank=True)
    client_timestamp = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "meter_readings"
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["assignment", "created_at"]),
            models.Index(fields=["agent", "created_at"]),
            models.Index(fields=["result"]),
        ]

    def __str__(self) -> str:
        return f"{self.assignment_id} - {self.result}"
