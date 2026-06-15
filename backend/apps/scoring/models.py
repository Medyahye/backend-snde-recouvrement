"""Modèle de configuration du scoring SNDE — versionné en DB.

V2.B.3 : un administrateur peut créer plusieurs configurations de scoring
(poids des 4 composantes, coefficients par type, seuils, quantiles) et
activer celle qu'il veut. Une seule config est active à un instant donné.

Les anciens configs restent en base pour traçabilité (qui a créé, quand,
quels poids).
"""
from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models


class ScoringConfig(models.Model):
    """Configuration de scoring. Une seule `is_active=True` à la fois."""

    # --- Poids des 4 composantes (somme doit valoir 1.0) ---
    weight_montant = models.FloatField(
        default=0.40,
        help_text="Poids de la composante Montant (solde normalisé min-max).",
    )
    weight_anciennete = models.FloatField(
        default=0.25,
        help_text="Poids de la composante Ancienneté (jours_impaye / seuil).",
    )
    weight_historique = models.FloatField(
        default=0.20,
        help_text="Poids de la composante Historique (jours_sans_paiement / seuil).",
    )
    weight_arrieres = models.FloatField(
        default=0.15,
        help_text="Poids de la composante Arriérés (arrieres / solde).",
    )

    # --- Coefficients multiplicateurs par type de client ---
    coef_domestique = models.FloatField(
        default=1.00,
        help_text="Multiplicateur du score pour les clients Domestique.",
    )
    coef_entreprise = models.FloatField(
        default=1.20,
        help_text="Multiplicateur du score pour les clients Entreprise.",
    )

    # --- Seuil temporel pour normalisation ancienneté/historique ---
    threshold_days = models.IntegerField(
        default=180,
        help_text="Jours au-delà desquels Ancienneté/Historique sont plafonnés à 1.0.",
    )

    # --- Quantiles de catégorisation Haute/Moyenne/Faible ---
    priority_quantile_high = models.FloatField(
        default=0.75,
        help_text="Quantile à partir duquel un score est en priorité Haute.",
    )
    priority_quantile_med = models.FloatField(
        default=0.50,
        help_text="Quantile à partir duquel un score est en priorité Moyenne.",
    )

    # --- Métadonnées de versioning ---
    is_active = models.BooleanField(
        default=False,
        help_text="Configuration utilisée pour les nouveaux imports + recalculs.",
    )
    description = models.CharField(
        max_length=200,
        blank=True,
        help_text="Note libre : 'Test plus de poids sur ancienneté', etc.",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="scoring_configs_created",
        null=True,
        blank=True,
    )

    class Meta:
        db_table = "scoring_configs"
        verbose_name = "Configuration de scoring"
        verbose_name_plural = "Configurations de scoring"
        ordering = ["-created_at"]
        constraints = [
            # Garde-fou DB : pas plus d'une config active en même temps.
            models.UniqueConstraint(
                fields=["is_active"],
                condition=models.Q(is_active=True),
                name="scoring_config_only_one_active",
            ),
        ]

    def clean(self):
        """Validation métier : somme des 4 poids = 1.0 (tolérance 0.001)."""
        total = (
            self.weight_montant
            + self.weight_anciennete
            + self.weight_historique
            + self.weight_arrieres
        )
        if abs(total - 1.0) > 0.001:
            raise ValidationError(
                {
                    "weight_montant": (
                        f"La somme des 4 poids doit valoir 1.0 (actuel : {total:.4f}). "
                        "Ajuste les valeurs ou utilise la normalisation automatique."
                    )
                }
            )
        if self.threshold_days <= 0:
            raise ValidationError(
                {"threshold_days": "Doit être strictement positif."}
            )
        if not (0 < self.priority_quantile_med < self.priority_quantile_high < 1):
            raise ValidationError(
                {
                    "priority_quantile_high": (
                        "Les quantiles doivent satisfaire 0 < med < high < 1."
                    )
                }
            )

    def save(self, *args, **kwargs):
        # Validation systématique avant sauvegarde
        self.full_clean()
        super().save(*args, **kwargs)

    @property
    def weights_pct(self) -> dict:
        """Retourne les poids en pourcentage (pour l'affichage)."""
        return {
            "montant": round(self.weight_montant * 100, 1),
            "anciennete": round(self.weight_anciennete * 100, 1),
            "historique": round(self.weight_historique * 100, 1),
            "arrieres": round(self.weight_arrieres * 100, 1),
        }

    def __str__(self) -> str:
        flag = " (active)" if self.is_active else ""
        pct = self.weights_pct
        return (
            f"Config #{self.id}{flag} — "
            f"{pct['montant']}/{pct['anciennete']}/{pct['historique']}/{pct['arrieres']}"
        )
