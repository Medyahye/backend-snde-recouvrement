"""Modèles de l'app imports : un FabImport = un fichier FAB uploadé."""
from django.conf import settings
from django.db import models


class FabImport(models.Model):
    """Métadonnées d'un fichier FAB uploadé.
    Un FabImport agit comme racine de partition pour les Client/Zone associés
    (cf. on_delete=CASCADE côté FK).
    """

    class Status(models.TextChoices):
        PENDING = "pending", "En attente"
        PROCESSING = "processing", "En cours"
        DONE = "done", "Terminé"
        FAILED = "failed", "Échec"
        EMPTY = "empty", "Vide (FAB SNDE sans contenu utile)"

    class Source(models.TextChoices):
        MANUAL = "manual", "Upload manuel"
        S3_AUTO = "s3_auto", "Sync S3 quotidienne"
        S3_BACKFILL = "s3_backfill", "Backfill historique S3"

    minio_key = models.CharField(
        max_length=255,
        help_text="Clé de l'objet dans le bucket MinIO (ex: fab/2026/04/fab20260412.txt).",
    )
    file_date = models.DateField(
        help_text="Date de l'extrait FAB (parsée depuis le nom de fichier fabAAAAMMJJ.txt).",
    )
    uploaded_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="imports",
    )
    uploaded_at = models.DateTimeField(auto_now_add=True)
    started_at = models.DateTimeField(null=True, blank=True)
    finished_at = models.DateTimeField(null=True, blank=True)
    status = models.CharField(
        max_length=20,
        choices=Status.choices,
        default=Status.PENDING,
    )
    source = models.CharField(
        max_length=20,
        choices=Source.choices,
        default=Source.MANUAL,
        help_text="Origine de cet import : upload manuel ou sync auto depuis S3.",
    )
    nb_lines_total = models.IntegerField(
        null=True,
        blank=True,
        help_text="Nombre de lignes totales du FAB brut.",
    )
    nb_clients_kept = models.IntegerField(
        null=True,
        blank=True,
        help_text="Nombre de clients scorés (code_relance='1' + toutes les conditions).",
    )
    nb_clients_total = models.IntegerField(
        null=True,
        blank=True,
        help_text="Nombre total de clients ingérés en DB (tous codes de relance avec solde>0).",
    )
    error_message = models.TextField(
        blank=True,
        help_text="Message d'erreur si status=failed.",
    )

    class Meta:
        db_table = "fab_imports"
        verbose_name = "Import FAB"
        verbose_name_plural = "Imports FAB"
        ordering = ["-uploaded_at"]
        indexes = [
            models.Index(fields=["status"]),
            models.Index(fields=["file_date"]),
            models.Index(fields=["source"]),
        ]

    def __str__(self) -> str:
        return f"FAB {self.file_date.isoformat()} ({self.get_status_display()})"
