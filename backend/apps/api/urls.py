"""Routes REST de l'API SNDE."""
from django.urls import path
from rest_framework_simplejwt.views import TokenObtainPairView, TokenRefreshView

from . import views

urlpatterns = [
    path("", views.api_root, name="api-root"),
    # Auth
    path("auth/login/", TokenObtainPairView.as_view(), name="token_obtain_pair"),
    path("auth/refresh/", TokenRefreshView.as_view(), name="token_refresh"),
    path("auth/me/", views.me, name="auth_me"),
    # Terrain mobile
    path(
        "terrain/mobile/summary/",
        views.terrain_mobile_summary,
        name="terrain_mobile_summary",
    ),
    path(
        "terrain/mobile/assignments/",
        views.TerrainMobileAssignmentListView.as_view(),
        name="terrain_mobile_assignments",
    ),
    path(
        "terrain/mobile/assignments/<int:pk>/",
        views.TerrainMobileAssignmentDetailView.as_view(),
        name="terrain_mobile_assignment_detail",
    ),
    path(
        "terrain/mobile/assignments/<int:pk>/reading/",
        views.TerrainMobileReadingCreateView.as_view(),
        name="terrain_mobile_reading_create",
    ),
    # Imports
    path(
        "imports/upload/",
        views.FabImportUploadView.as_view(),
        name="imports_upload",
    ),
    path("imports/", views.FabImportListView.as_view(), name="imports_list"),
    path(
        "imports/duplicates/",
        views.imports_duplicates,
        name="imports_duplicates",
    ),
    path(
        "imports/gaps/",
        views.imports_gaps,
        name="imports_gaps",
    ),
    path(
        "imports/<int:pk>/",
        views.FabImportDetailView.as_view(),
        name="imports_detail",
    ),
    # Zones
    path("zones/", views.ZoneListView.as_view(), name="zones_list"),
    path("zones/<int:pk>/", views.ZoneDetailView.as_view(), name="zones_detail"),
    path(
        "zones/<int:pk>/clients/",
        views.ZoneClientsView.as_view(),
        name="zones_clients",
    ),
    # Clients
    path("clients/", views.ClientListView.as_view(), name="clients_list"),
    path(
        "clients/<int:pk>/",
        views.ClientDetailView.as_view(),
        name="clients_detail",
    ),
    path(
        "clients/<int:pk>/timeline/",
        views.client_timeline,
        name="clients_timeline",
    ),
    path(
        "clients/by-ref/<str:reference>/",
        views.client_lookup_by_ref,
        name="clients_lookup_by_ref",
    ),
    # Stats
    path("stats/kpis/", views.stats_kpis, name="stats_kpis"),
    path("stats/distribution/", views.stats_distribution, name="stats_distribution"),
    path("stats/comparison/", views.stats_comparison, name="stats_comparison"),
    # Agrégations Top-N
    path(
        "aggregations/centres/",
        views.aggregations_centres,
        name="aggregations_centres",
    ),
    path(
        "aggregations/secteurs/",
        views.aggregations_secteurs,
        name="aggregations_secteurs",
    ),
    path(
        "aggregations/tournees/",
        views.aggregations_tournees,
        name="aggregations_tournees",
    ),
    path(
        "aggregations/releveurs/",
        views.aggregations_releveurs,
        name="aggregations_releveurs",
    ),
    # Lookup centres
    path("centres/", views.CentresLookupView.as_view(), name="centres_lookup"),
    # Exports
    path("exports/csv/", views.export_csv, name="export_csv"),
    path("exports/word/", views.export_word, name="export_word"),
    # Recouvrement (V2.C.1)
    path("recouvrement/daily/", views.recouvrement_daily, name="recouvrement_daily"),
    path("recouvrement/period/", views.recouvrement_period, name="recouvrement_period"),
    path(
        "recouvrement/by-centre/",
        views.recouvrement_by_centre,
        name="recouvrement_by_centre",
    ),
    path(
        "recouvrement/by-zone/",
        views.recouvrement_by_zone,
        name="recouvrement_by_zone",
    ),
    path(
        "recouvrement/movements/",
        views.recouvrement_movements,
        name="recouvrement_movements",
    ),
    path(
        "recouvrement/behavior/",
        views.recouvrement_behavior,
        name="recouvrement_behavior",
    ),
    # Anomalies opérationnelles
    path(
        "anomalies/persistent-code-1/",
        views.anomalies_persistent_code_1,
        name="anomalies_persistent_code_1",
    ),
    # Profils comportementaux (bon/moyen/mauvais payeur)
    path(
        "behaviors/summary/",
        views.client_behaviors_summary,
        name="behaviors_summary",
    ),
    path(
        "behaviors/",
        views.client_behaviors_list,
        name="behaviors_list",
    ),
    # Synchronisation S3 (statut + déclenchement manuel admin)
    path(
        "sync/s3/",
        views.sync_s3_status,
        name="sync_s3_status",
    ),
    # Institutions publiques (agrégation par groupe)
    path(
        "institutions/",
        views.institutions_list,
        name="institutions_list",
    ),
    path(
        "institutions/<slug:slug>/",
        views.institution_detail,
        name="institution_detail",
    ),
    path(
        "institutions/<slug:slug>/installations/",
        views.institution_installations,
        name="institution_installations",
    ),
    # Users (admin only)
    path("users/", views.UserListCreateView.as_view(), name="users_list"),
    path("users/<int:pk>/", views.UserDetailView.as_view(), name="users_detail"),
    # Scoring config (V2.B.3 — admin only)
    path(
        "scoring/configs/",
        views.ScoringConfigListCreateView.as_view(),
        name="scoring_configs_list",
    ),
    path(
        "scoring/configs/active/",
        views.scoring_config_active,
        name="scoring_configs_active",
    ),
    path(
        "scoring/configs/<int:pk>/",
        views.ScoringConfigDetailView.as_view(),
        name="scoring_configs_detail",
    ),
    path(
        "scoring/configs/<int:pk>/activate/",
        views.scoring_config_activate,
        name="scoring_configs_activate",
    ),
    path(
        "scoring/configs/<int:pk>/recompute/",
        views.scoring_config_recompute,
        name="scoring_configs_recompute",
    ),
]
