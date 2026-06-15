from django.contrib import admin

from .models import Client


@admin.register(Client)
class ClientAdmin(admin.ModelAdmin):
    list_display = (
        "rang",
        "reference_abonnement",
        "nom_client",
        "type_client",
        "zone",
        "solde",
        "score_final",
        "priorite",
        "import_ref",
    )
    list_filter = ("priorite", "type_client", "centre_nom", "import_ref")
    search_fields = (
        "reference_abonnement",
        "nom_client",
        "telephone",
        "adresse",
        "zone",
    )
    ordering = ("import_ref", "rang")
    readonly_fields = tuple(
        f.name for f in Client._meta.get_fields() if not f.many_to_many
    )

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False
