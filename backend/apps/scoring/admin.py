from django.contrib import admin

from .models import ScoringConfig


@admin.register(ScoringConfig)
class ScoringConfigAdmin(admin.ModelAdmin):
    list_display = (
        "id",
        "is_active",
        "weight_montant",
        "weight_anciennete",
        "weight_historique",
        "weight_arrieres",
        "coef_entreprise",
        "threshold_days",
        "description",
        "created_by",
        "created_at",
    )
    list_filter = ("is_active",)
    search_fields = ("description",)
    readonly_fields = ("created_at", "created_by")
