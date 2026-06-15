"""Modèles de l'app zones :
- Centre : table de référence (lookup) des centres SNDE.
- Zone : agrégation par zone (centre + secteur + tournée) pour un import donné.
"""
from django.db import models


class Centre(models.Model):
    """Table de correspondance code → nom de centre SNDE.
    Indépendante des imports : on peut ajouter/renommer un centre via l'admin.
    """

    code = models.CharField(
        primary_key=True,
        max_length=10,
        help_text="Code numérique du centre (ex: '42').",
    )
    nom = models.CharField(
        max_length=100,
        help_text="Nom usuel du centre (ex: 'CARREFOUR2').",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "centres"
        verbose_name = "Centre SNDE"
        verbose_name_plural = "Centres SNDE"
        ordering = ["code"]

    def __str__(self) -> str:
        return f"{self.code} — {self.nom}"


class Zone(models.Model):
    """Agrégation par zone fine (centre + secteur + tournée) pour un import.
    `priorite_zone = score_moyen × nb_clients` (cf. Note Explicative §6).
    """

    class Priorite(models.TextChoices):
        HAUTE = "Haute", "Haute"
        MOYENNE = "Moyenne", "Moyenne"
        FAIBLE = "Faible", "Faible"

    import_ref = models.ForeignKey(
        "imports.FabImport",
        on_delete=models.CASCADE,
        related_name="zones",
    )
    zone_id = models.CharField(
        max_length=150,
        help_text="Identifiant complet : NomCentre_SecteurZfill2_TourneeZfill2.",
    )
    centre_nom = models.CharField(max_length=100)
    secteur = models.CharField(max_length=10)
    tournee = models.CharField(max_length=10)

    nb_clients = models.IntegerField()
    nb_entreprises = models.IntegerField()
    nb_domestiques = models.IntegerField()

    score_moyen = models.FloatField()
    score_max = models.FloatField()
    score_total = models.FloatField()
    anciennete_moyenne = models.FloatField()

    solde_total = models.DecimalField(max_digits=16, decimal_places=2)
    solde_moyen = models.DecimalField(max_digits=14, decimal_places=2)
    arrieres_total = models.DecimalField(max_digits=16, decimal_places=2)

    priorite_zone = models.FloatField(
        help_text="score_moyen × nb_clients (intensité × volume).",
    )
    priorite = models.CharField(
        max_length=10,
        choices=Priorite.choices,
        help_text="Catégorisation auto-adaptative par quantiles 75/50.",
    )
    rang = models.IntegerField(
        help_text="Position dans le classement de l'import (1 = plus prioritaire).",
    )

    class Meta:
        db_table = "zones"
        verbose_name = "Zone agrégée"
        verbose_name_plural = "Zones agrégées"
        ordering = ["import_ref", "rang"]
        constraints = [
            models.UniqueConstraint(
                fields=["import_ref", "zone_id"],
                name="zone_unique_per_import",
            ),
        ]
        indexes = [
            models.Index(fields=["import_ref", "priorite_zone"]),
            models.Index(fields=["import_ref", "priorite"]),
            models.Index(fields=["import_ref", "centre_nom"]),
        ]

    def __str__(self) -> str:
        return f"{self.zone_id} (rang {self.rang})"
