from django.contrib.auth.models import AbstractUser
from django.db import models


class User(AbstractUser):
    """Utilisateur SNDE avec un rôle métier."""

    class Role(models.TextChoices):
        ADMIN = "admin", "Administrateur"
        DIRECTEUR = "directeur", "Directeur recouvrement"
        GESTIONNAIRE = "gestionnaire", "Gestionnaire"
        TERRAIN = "terrain", "Équipe terrain"

    role = models.CharField(
        max_length=20,
        choices=Role.choices,
        default=Role.GESTIONNAIRE,
    )

    class Meta:
        db_table = "users"
        verbose_name = "Utilisateur"
        verbose_name_plural = "Utilisateurs"
