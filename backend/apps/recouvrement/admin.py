from django.contrib import admin

from .models import ClientMovement


@admin.register(ClientMovement)
class ClientMovementAdmin(admin.ModelAdmin):
    list_display = (
        "reference_abonnement",
        "date_to",
        "type",
        "delta_solde",
        "confidence",
        "code_before",
        "code_after",
        "centre_nom",
    )
    list_filter = ("type", "date_to", "centre_nom")
    search_fields = ("reference_abonnement", "nom_client", "notes")
    readonly_fields = tuple(
        f.name for f in ClientMovement._meta.get_fields() if not f.many_to_many
    )

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False
