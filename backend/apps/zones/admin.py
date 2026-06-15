from django.contrib import admin

from .models import Centre, Zone


@admin.register(Centre)
class CentreAdmin(admin.ModelAdmin):
    list_display = ("code", "nom", "updated_at")
    search_fields = ("code", "nom")
    ordering = ("code",)


@admin.register(Zone)
class ZoneAdmin(admin.ModelAdmin):
    list_display = (
        "rang",
        "zone_id",
        "centre_nom",
        "nb_clients",
        "score_moyen",
        "priorite_zone",
        "priorite",
        "import_ref",
    )
    list_filter = ("priorite", "centre_nom", "import_ref")
    search_fields = ("zone_id", "centre_nom", "secteur", "tournee")
    ordering = ("import_ref", "rang")
    readonly_fields = tuple(
        f.name for f in Zone._meta.get_fields() if not f.many_to_many
    )

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False
