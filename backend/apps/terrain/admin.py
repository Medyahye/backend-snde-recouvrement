from django.contrib import admin

from .models import MeterReading, TerrainAssignment


@admin.register(TerrainAssignment)
class TerrainAssignmentAdmin(admin.ModelAdmin):
    list_display = (
        "id",
        "agent",
        "client",
        "status",
        "planned_order",
        "due_date",
        "updated_at",
    )
    list_filter = ("status", "due_date", "agent")
    search_fields = (
        "client__reference_abonnement",
        "client__nom_client",
        "agent__username",
    )


@admin.register(MeterReading)
class MeterReadingAdmin(admin.ModelAdmin):
    list_display = ("id", "assignment", "agent", "result", "meter_index", "created_at")
    list_filter = ("result", "agent")
    search_fields = (
        "assignment__client__reference_abonnement",
        "assignment__client__nom_client",
        "agent__username",
    )
