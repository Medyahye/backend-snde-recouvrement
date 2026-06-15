"""Service d'accès à la configuration de scoring active.

Sert d'abstraction unique entre le pipeline et les paramètres de scoring :
- Si une ScoringConfig active existe en DB → on l'utilise.
- Sinon → on crée une config par défaut à partir des constantes settings.py
  (bootstrap au premier import).
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from django.conf import settings
from django.db import transaction

if TYPE_CHECKING:
    from .models import ScoringConfig


@dataclass(frozen=True)
class ScoringParams:
    """Snapshot immuable des paramètres de scoring (pour passer au pipeline)."""

    weight_montant: float
    weight_anciennete: float
    weight_historique: float
    weight_arrieres: float
    coef_domestique: float
    coef_entreprise: float
    threshold_days: int
    priority_quantile_high: float
    priority_quantile_med: float

    @property
    def weights(self) -> dict[str, float]:
        return {
            "MONTANT": self.weight_montant,
            "ANCIENNETE": self.weight_anciennete,
            "HISTORIQUE": self.weight_historique,
            "ARRIERES": self.weight_arrieres,
        }

    @classmethod
    def from_settings(cls) -> "ScoringParams":
        """Construit les paramètres depuis settings.py (fallback)."""
        w = settings.SCORING_WEIGHTS
        return cls(
            weight_montant=w["MONTANT"],
            weight_anciennete=w["ANCIENNETE"],
            weight_historique=w["HISTORIQUE"],
            weight_arrieres=w["ARRIERES"],
            coef_domestique=settings.COEF_DOMESTIQUE,
            coef_entreprise=settings.COEF_ENTREPRISE,
            threshold_days=settings.SCORING_THRESHOLD_DAYS,
            priority_quantile_high=settings.PRIORITY_QUANTILE_HIGH,
            priority_quantile_med=settings.PRIORITY_QUANTILE_MED,
        )

    @classmethod
    def from_model(cls, config: "ScoringConfig") -> "ScoringParams":
        return cls(
            weight_montant=config.weight_montant,
            weight_anciennete=config.weight_anciennete,
            weight_historique=config.weight_historique,
            weight_arrieres=config.weight_arrieres,
            coef_domestique=config.coef_domestique,
            coef_entreprise=config.coef_entreprise,
            threshold_days=config.threshold_days,
            priority_quantile_high=config.priority_quantile_high,
            priority_quantile_med=config.priority_quantile_med,
        )


def get_active_config() -> "ScoringConfig":
    """Retourne la `ScoringConfig` active, ou crée une config par défaut au besoin."""
    from .models import ScoringConfig

    config = ScoringConfig.objects.filter(is_active=True).first()
    if config is not None:
        return config

    # Bootstrap : créer une config par défaut depuis settings.py
    defaults = ScoringParams.from_settings()
    config = ScoringConfig(
        weight_montant=defaults.weight_montant,
        weight_anciennete=defaults.weight_anciennete,
        weight_historique=defaults.weight_historique,
        weight_arrieres=defaults.weight_arrieres,
        coef_domestique=defaults.coef_domestique,
        coef_entreprise=defaults.coef_entreprise,
        threshold_days=defaults.threshold_days,
        priority_quantile_high=defaults.priority_quantile_high,
        priority_quantile_med=defaults.priority_quantile_med,
        is_active=True,
        description="Configuration par défaut (bootstrap depuis settings.py)",
    )
    config.save()
    return config


def get_active_params() -> ScoringParams:
    """Helper pour le pipeline : retourne directement un snapshot."""
    return ScoringParams.from_model(get_active_config())


def activate_config(config: "ScoringConfig") -> "ScoringConfig":
    """Bascule la config active de manière atomique.

    Désactive toutes les autres configs et active celle passée en argument.
    """
    from .models import ScoringConfig

    with transaction.atomic():
        ScoringConfig.objects.filter(is_active=True).exclude(pk=config.pk).update(
            is_active=False
        )
        config.is_active = True
        config.save(update_fields=["is_active"])
    return config
