from django.contrib import admin

from .models import FabImport


@admin.register(FabImport)
class FabImportAdmin(admin.ModelAdmin):
    list_display = (
        "file_date",
        "status",
        "uploaded_by",
        "uploaded_at",
        "nb_lines_total",
        "nb_clients_kept",
    )
    list_filter = ("status", "file_date")
    search_fields = ("minio_key", "uploaded_by__username", "uploaded_by__email")
    readonly_fields = (
        "uploaded_at",
        "started_at",
        "finished_at",
        "nb_lines_total",
        "nb_clients_kept",
    )
    ordering = ("-uploaded_at",)
    date_hierarchy = "file_date"
