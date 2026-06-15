"""Vues REST — étapes 4.1 (auth + imports) et 4.2 (zones + clients)."""
import logging
import uuid

from django.db import transaction
from django.shortcuts import get_object_or_404
from django_filters.rest_framework import DjangoFilterBackend
from rest_framework import filters, generics, status
from rest_framework.decorators import api_view, permission_classes
from rest_framework.exceptions import ValidationError
from rest_framework.parsers import FormParser, MultiPartParser
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.clients.models import Client
from apps.imports.models import FabImport
from apps.scoring.storage import upload_file_object
from apps.scoring.tasks import process_fab_import
from apps.zones.models import Centre, Zone
from apps.terrain import views as terrain_views

from django.http import HttpResponse

from datetime import date as date_cls, datetime, timedelta

from apps.scoring.models import ScoringConfig
from apps.scoring.config_service import activate_config

from . import exports as exports_module
from . import recouvrement_stats
from . import stats as stats_module
from .filters import ClientFilter, FabImportFilter, UserFilter, ZoneFilter
from .serializers import (
    CentreSerializer,
    ClientDetailSerializer,
    ClientListSerializer,
    FabImportDetailSerializer,
    FabImportListSerializer,
    ScoringConfigSerializer,
    UserCreateSerializer,
    UserListSerializer,
    UserSlimSerializer,
    UserUpdateSerializer,
    ZoneDetailSerializer,
    ZoneListSerializer,
)
from django.contrib.auth import get_user_model

DjangoUser = get_user_model()


class IsAdmin(IsAuthenticated):
    """Permission custom : authentifié + role=admin (ou is_superuser)."""

    def has_permission(self, request, view):
        if not super().has_permission(request, view):
            return False
        u = request.user
        return getattr(u, "role", None) == "admin" or u.is_superuser


terrain_mobile_summary = terrain_views.mobile_summary
TerrainMobileAssignmentListView = terrain_views.MobileAssignmentListView
TerrainMobileAssignmentDetailView = terrain_views.MobileAssignmentDetailView
TerrainMobileReadingCreateView = terrain_views.MobileReadingCreateView
from .validators import (
    validate_fab_content,
    validate_fab_date_is_today,
    validate_fab_filename,
    validate_fab_size,
)

logger = logging.getLogger(__name__)


# --------------------------------------------------------------------------- #
# Racine d'API + auth/me
# --------------------------------------------------------------------------- #


@api_view(["GET"])
@permission_classes([AllowAny])
def api_root(_request):
    """Liste les endpoints disponibles."""
    return Response(
        {
            "service": "snde-api",
            "version": "0.4.1",
            "endpoints": {
                "auth_login": "/api/auth/login/",
                "auth_refresh": "/api/auth/refresh/",
                "auth_me": "/api/auth/me/",
                "imports_upload": "/api/imports/upload/",
                "imports_list": "/api/imports/",
                "imports_detail": "/api/imports/{id}/",
                "zones_list": "/api/zones/?import_id=...",
                "zones_detail": "/api/zones/{id}/",
                "zones_clients": "/api/zones/{id}/clients/",
                "clients_list": "/api/clients/?import_id=...",
                "clients_detail": "/api/clients/{id}/",
                "stats_kpis": "/api/stats/kpis/?import_id=...",
                "stats_distribution": "/api/stats/distribution/?import_id=...",
                "stats_comparison": "/api/stats/comparison/?import_a=...&import_b=...",
                "agg_centres": "/api/aggregations/centres/?import_id=...",
                "agg_secteurs": "/api/aggregations/secteurs/?import_id=...",
                "agg_tournees": "/api/aggregations/tournees/?import_id=...",
                "agg_releveurs": "/api/aggregations/releveurs/?import_id=...",
                "centres_lookup": "/api/centres/",
                "exports_csv": "/api/exports/csv/?import_id=...&type=zones|clients",
                "exports_word": "/api/exports/word/?import_id=...",
                "recouvrement_daily": "/api/recouvrement/daily/?date=AAAA-MM-JJ",
                "recouvrement_period": "/api/recouvrement/period/?start=&end=",
                "recouvrement_by_centre": "/api/recouvrement/by-centre/?date=",
                "recouvrement_by_zone": "/api/recouvrement/by-zone/?date=",
                "recouvrement_movements": "/api/recouvrement/movements/?date=&type=",
            },
        }
    )


@api_view(["GET"])
@permission_classes([IsAuthenticated])
def me(request):
    """Retourne le user authentifié (rôle, email, nom)."""
    return Response(UserSlimSerializer(request.user).data)


# --------------------------------------------------------------------------- #
# Imports : upload, liste, détail
# --------------------------------------------------------------------------- #


def _build_minio_key(file_date, original_name: str) -> str:
    """Construit une clé MinIO unique préservant la traçabilité.

    Format : `fab/AAAA/MM/{uuid12}_{filename}` — un nouvel upload pour la
    même date ne se marche jamais sur l'archive précédente.
    """
    short_uuid = uuid.uuid4().hex[:12]
    return (
        f"fab/{file_date.year:04d}/{file_date.month:02d}/"
        f"{short_uuid}_{original_name}"
    )


class FabImportUploadView(APIView):
    """`POST /api/imports/upload/` — upload multipart d'un FAB.

    Workflow :
    1. Validation : nom de fichier, taille, date du jour, structure du contenu.
    2. Upload vers MinIO (clé unique).
    3. Création du `FabImport` (status=pending).
    4. Dispatch de la tâche Celery `process_fab_import`.
    5. Réponse 202 Accepted avec l'objet `FabImport`.
    """

    permission_classes = [IsAuthenticated]
    parser_classes = [MultiPartParser, FormParser]

    def post(self, request, *args, **kwargs):
        upload = request.FILES.get("file")
        if upload is None:
            return Response(
                {"file": "Aucun fichier fourni (champ multipart attendu : 'file')."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        # 1.a — Taille
        validate_fab_size(upload.size)
        # 1.b — Filename + extraction date
        file_date = validate_fab_filename(upload.name)
        # 1.c — Date du jour (configurable)
        validate_fab_date_is_today(file_date)
        # 1.d — Structure du contenu
        validate_fab_content(upload)

        # 2 — Upload MinIO
        minio_key = _build_minio_key(file_date, upload.name)
        try:
            upload.seek(0)
            upload_file_object(
                upload,
                minio_key=minio_key,
                length=upload.size,
                content_type="text/plain",
            )
        except Exception as exc:  # noqa: BLE001
            logger.exception("Échec upload MinIO pour %s", upload.name)
            return Response(
                {"detail": f"Échec du dépôt sur MinIO : {exc}"},
                status=status.HTTP_502_BAD_GATEWAY,
            )

        # 3 — FabImport + 4 — dispatch Celery
        with transaction.atomic():
            imp = FabImport.objects.create(
                minio_key=minio_key,
                file_date=file_date,
                uploaded_by=request.user,
                status=FabImport.Status.PENDING,
            )
            transaction.on_commit(lambda: process_fab_import.delay(imp.id))

        logger.info(
            "Upload OK pour %s → FabImport #%s, clé MinIO %s, by %s",
            upload.name, imp.id, minio_key, request.user,
        )

        return Response(
            FabImportDetailSerializer(imp).data,
            status=status.HTTP_202_ACCEPTED,
        )


class FabImportListView(generics.ListAPIView):
    """`GET /api/imports/?status=done&file_date_after=2026-04-01&uploader=admin` — paginé."""

    permission_classes = [IsAuthenticated]
    serializer_class = FabImportListSerializer
    queryset = FabImport.objects.select_related("uploaded_by").all()
    filter_backends = [
        DjangoFilterBackend,
        filters.OrderingFilter,
        filters.SearchFilter,
    ]
    filterset_class = FabImportFilter
    ordering_fields = [
        "uploaded_at",
        "file_date",
        "status",
        "nb_clients_kept",
    ]
    ordering = ["-uploaded_at"]
    search_fields = ["minio_key", "uploaded_by__username", "uploaded_by__email"]


class FabImportDetailView(generics.RetrieveDestroyAPIView):
    """`GET /api/imports/{id}/` — détail + statut + error_message éventuel.

    `DELETE /api/imports/{id}/` — supprime l'import (admin only). Cascade :
    - Tous les Client liés (via FK CASCADE)
    - Toutes les Zone liées
    - Tous les ClientMovement où cet import est `import_from` ou `import_to`
    - Le fichier MinIO associé (best-effort, log si échec)
    """

    permission_classes = [IsAuthenticated]
    serializer_class = FabImportDetailSerializer
    queryset = FabImport.objects.select_related("uploaded_by").all()

    def get_permissions(self):
        # Seuls les admins peuvent supprimer
        if self.request.method == "DELETE":
            return [IsAdmin()]
        return super().get_permissions()

    def perform_destroy(self, instance):
        from apps.scoring.storage import delete_fab

        minio_key = instance.minio_key
        logger.info(
            "Suppression FabImport #%s (%s) par %s",
            instance.id, instance.file_date, self.request.user,
        )
        # 1. Supprimer l'objet MinIO (best-effort, n'arrête pas si échec)
        if minio_key:
            delete_fab(minio_key)
        # 2. Supprimer la ligne DB → cascade sur Client/Zone/ClientMovement
        instance.delete()


# --------------------------------------------------------------------------- #
# Zones — étape 4.2
# --------------------------------------------------------------------------- #


def _require_import_id(request) -> int:
    """Extrait `import_id` de la query string et lève 400 s'il est absent."""
    raw = request.query_params.get("import_id")
    if not raw:
        raise ValidationError(
            {"import_id": "Le paramètre 'import_id' est obligatoire."}
        )
    try:
        return int(raw)
    except (TypeError, ValueError):
        raise ValidationError({"import_id": "Doit être un entier."})


class ZoneListView(generics.ListAPIView):
    """`GET /api/zones/?import_id=N&priorite=Haute&centre=KIFFA2&search=...`

    Tri par défaut : `rang` ascendant (la plus prioritaire en premier).
    """

    permission_classes = [IsAuthenticated]
    serializer_class = ZoneListSerializer
    filter_backends = [
        DjangoFilterBackend,
        filters.OrderingFilter,
        filters.SearchFilter,
    ]
    filterset_class = ZoneFilter
    ordering_fields = [
        "rang",
        "priorite_zone",
        "score_moyen",
        "nb_clients",
        "solde_total",
    ]
    ordering = ["rang"]
    search_fields = ["zone_id", "centre_nom"]

    def get_queryset(self):
        import_id = _require_import_id(self.request)
        return Zone.objects.filter(import_ref_id=import_id)


class ZoneDetailView(generics.RetrieveAPIView):
    """`GET /api/zones/{id}/` — détail d'une zone."""

    permission_classes = [IsAuthenticated]
    serializer_class = ZoneDetailSerializer
    queryset = Zone.objects.all()


class ZoneClientsView(generics.ListAPIView):
    """`GET /api/zones/{id}/clients/` — drill-down : tous les clients d'une zone.

    Ne nécessite pas d'`import_id` en query : il est déduit du PK de la zone.
    """

    permission_classes = [IsAuthenticated]
    serializer_class = ClientListSerializer
    filter_backends = [filters.OrderingFilter, filters.SearchFilter]
    ordering_fields = ["rang", "score_final", "solde"]
    ordering = ["rang"]
    search_fields = ["reference_abonnement", "nom_client", "telephone"]

    def get_queryset(self):
        zone = get_object_or_404(Zone, pk=self.kwargs["pk"])
        return Client.objects.filter(
            import_ref_id=zone.import_ref_id, zone=zone.zone_id
        )


# --------------------------------------------------------------------------- #
# Clients — étape 4.2
# --------------------------------------------------------------------------- #


class ClientListView(generics.ListAPIView):
    """`GET /api/clients/?import_id=N&priorite=Haute&zone=...&type_client=Entreprise&search=...`"""

    permission_classes = [IsAuthenticated]
    serializer_class = ClientListSerializer
    filter_backends = [
        DjangoFilterBackend,
        filters.OrderingFilter,
        filters.SearchFilter,
    ]
    filterset_class = ClientFilter
    ordering_fields = ["rang", "score_final", "solde"]
    ordering = ["rang"]
    search_fields = ["reference_abonnement", "nom_client", "telephone"]

    def get_queryset(self):
        import_id = _require_import_id(self.request)
        return Client.objects.filter(import_ref_id=import_id)


class ClientDetailView(generics.RetrieveAPIView):
    """`GET /api/clients/{id}/` — détail d'un client (toutes composantes du score)."""

    permission_classes = [IsAuthenticated]
    serializer_class = ClientDetailSerializer
    queryset = Client.objects.all()


# --------------------------------------------------------------------------- #
# Stats — étape 4.3
# --------------------------------------------------------------------------- #


def _ensure_import_exists(import_id: int) -> FabImport:
    try:
        return FabImport.objects.get(id=import_id)
    except FabImport.DoesNotExist:
        raise ValidationError(
            {"import_id": f"Aucun import trouvé avec id={import_id}."}
        )


@api_view(["GET"])
@permission_classes([IsAuthenticated])
def stats_kpis(request):
    """`GET /api/stats/kpis/?import_id=N` — KPIs dashboard accueil."""
    import_id = _require_import_id(request)
    _ensure_import_exists(import_id)
    return Response(stats_module.compute_kpis(import_id))


@api_view(["GET"])
@permission_classes([IsAuthenticated])
def stats_distribution(request):
    """`GET /api/stats/distribution/?import_id=N&buckets=10` — histogramme des scores."""
    import_id = _require_import_id(request)
    _ensure_import_exists(import_id)

    raw_buckets = request.query_params.get("buckets", "10")
    try:
        n_buckets = int(raw_buckets)
    except (TypeError, ValueError):
        raise ValidationError({"buckets": "Doit être un entier entre 2 et 50."})

    try:
        return Response(stats_module.compute_score_distribution(import_id, n_buckets))
    except ValueError as exc:
        raise ValidationError({"buckets": str(exc)})


@api_view(["GET"])
@permission_classes([IsAuthenticated])
def stats_comparison(request):
    """`GET /api/stats/comparison/?import_a=A&import_b=B` — diff entre 2 imports."""
    a_raw = request.query_params.get("import_a")
    b_raw = request.query_params.get("import_b")
    if not a_raw or not b_raw:
        raise ValidationError(
            {
                "detail": (
                    "Les paramètres 'import_a' et 'import_b' sont obligatoires."
                )
            }
        )
    try:
        a_id, b_id = int(a_raw), int(b_raw)
    except (TypeError, ValueError):
        raise ValidationError({"detail": "import_a et import_b doivent être des entiers."})

    _ensure_import_exists(a_id)
    _ensure_import_exists(b_id)
    return Response(stats_module.compute_comparison(a_id, b_id))


# --------------------------------------------------------------------------- #
# Agrégations Top-N — étape 4.3
# --------------------------------------------------------------------------- #


@api_view(["GET"])
@permission_classes([IsAuthenticated])
def aggregations_centres(request):
    """`GET /api/aggregations/centres/?import_id=N` — Top centres SNDE."""
    import_id = _require_import_id(request)
    _ensure_import_exists(import_id)
    return Response(stats_module.aggregate_by_centre(import_id))


@api_view(["GET"])
@permission_classes([IsAuthenticated])
def aggregations_secteurs(request):
    """`GET /api/aggregations/secteurs/?import_id=N` — Top secteurs."""
    import_id = _require_import_id(request)
    _ensure_import_exists(import_id)
    return Response(stats_module.aggregate_by_secteur(import_id))


@api_view(["GET"])
@permission_classes([IsAuthenticated])
def aggregations_tournees(request):
    """`GET /api/aggregations/tournees/?import_id=N` — Top tournées."""
    import_id = _require_import_id(request)
    _ensure_import_exists(import_id)
    return Response(stats_module.aggregate_by_tournee(import_id))


@api_view(["GET"])
@permission_classes([IsAuthenticated])
def aggregations_releveurs(request):
    """`GET /api/aggregations/releveurs/?import_id=N` — Top releveurs."""
    import_id = _require_import_id(request)
    _ensure_import_exists(import_id)
    return Response(stats_module.aggregate_by_releveur(import_id))


class CentresLookupView(generics.ListAPIView):
    """`GET /api/centres/` — table de référence des 91 centres SNDE.

    Pas de pagination (la liste est petite et stable). Pas de filtre import_id
    (c'est une référence indépendante des imports).
    """

    permission_classes = [IsAuthenticated]
    serializer_class = CentreSerializer
    queryset = Centre.objects.all()
    pagination_class = None
    filter_backends = [filters.SearchFilter]
    search_fields = ["code", "nom"]


# --------------------------------------------------------------------------- #
# Exports CSV + Word — étape 4.4
# --------------------------------------------------------------------------- #


@api_view(["GET"])
@permission_classes([IsAuthenticated])
def export_csv(request):
    """`GET /api/exports/csv/?import_id=N&type=zones|clients`

    Streaming response : compatible avec des centaines de milliers de lignes.
    """
    import_id = _require_import_id(request)
    _ensure_import_exists(import_id)

    export_type = request.query_params.get("type", "zones")
    if export_type == "zones":
        return exports_module.stream_csv_zones(import_id)
    if export_type == "clients":
        return exports_module.stream_csv_clients(import_id)
    raise ValidationError(
        {"type": "Doit valoir 'zones' ou 'clients' (par défaut : zones)."}
    )


# --------------------------------------------------------------------------- #
# Users CRUD — étape 5.3 (admin only)
# --------------------------------------------------------------------------- #


class UserListCreateView(generics.ListCreateAPIView):
    """`GET /api/users/?role=admin&is_active=true&search=...` · `POST /api/users/`"""

    permission_classes = [IsAdmin]
    queryset = DjangoUser.objects.all().order_by("-date_joined")
    filter_backends = [DjangoFilterBackend, filters.SearchFilter]
    filterset_class = UserFilter
    search_fields = ["username", "email", "first_name", "last_name"]
    pagination_class = None

    def get_serializer_class(self):
        if self.request.method == "POST":
            return UserCreateSerializer
        return UserListSerializer


class UserDetailView(generics.RetrieveUpdateDestroyAPIView):
    """`GET/PATCH/DELETE /api/users/{id}/` (admin)."""

    permission_classes = [IsAdmin]
    queryset = DjangoUser.objects.all()

    def get_serializer_class(self):
        if self.request.method in ("PUT", "PATCH"):
            return UserUpdateSerializer
        return UserListSerializer

    def perform_destroy(self, instance):
        # Ne supprime pas physiquement : désactive (préserve la traçabilité des imports).
        instance.is_active = False
        instance.save(update_fields=["is_active"])


# --------------------------------------------------------------------------- #
# Recouvrement — V2.C.1 (mouvements de solde + KPIs journaliers)
# --------------------------------------------------------------------------- #


def _parse_date(raw: str | None, fallback_today: bool = False):
    """Parse une date AAAA-MM-JJ. Retourne aujourd'hui si None et fallback_today=True."""
    if not raw:
        if fallback_today:
            from django.utils import timezone

            return timezone.localdate()
        raise ValidationError({"date": "Paramètre 'date' obligatoire (AAAA-MM-JJ)."})
    try:
        return datetime.strptime(raw, "%Y-%m-%d").date()
    except ValueError:
        raise ValidationError({"date": f"Format invalide : {raw}. Attendu AAAA-MM-JJ."})


@api_view(["GET"])
@permission_classes([IsAuthenticated])
def recouvrement_daily(request):
    """`GET /api/recouvrement/daily/?date=AAAA-MM-JJ` — recouvré sur cette date.

    Si `date` est omis, utilise la date du dernier import 'done'.
    """
    raw_date = request.query_params.get("date")
    if not raw_date:
        # Date du dernier import done
        last = FabImport.objects.filter(status=FabImport.Status.DONE).order_by(
            "-file_date"
        ).first()
        if last is None:
            return Response(
                {
                    "date": None,
                    "total_paye": "0",
                    "nb_payeurs": 0,
                    "decomposition": {"certain": "0", "probable": "0", "partiel": "0"},
                    "anomalies": {"nb_ajustements": 0, "nb_sorties_suspectes": 0},
                    "nouvelle_facturation": "0",
                }
            )
        target_date = last.file_date
    else:
        target_date = _parse_date(raw_date)

    return Response(recouvrement_stats.daily_recovery(target_date))


@api_view(["GET"])
@permission_classes([IsAuthenticated])
def recouvrement_period(request):
    """`GET /api/recouvrement/period/?start=&end=` — total et évolution journalière."""
    start = _parse_date(request.query_params.get("start"))
    end = _parse_date(request.query_params.get("end"))
    if start > end:
        raise ValidationError({"detail": "'start' doit être ≤ 'end'."})
    return Response(recouvrement_stats.period_recovery(start, end))


@api_view(["GET"])
@permission_classes([IsAuthenticated])
def recouvrement_by_centre(request):
    """`GET /api/recouvrement/by-centre/?date=` — répartition par centre."""
    target_date = _parse_date(request.query_params.get("date"), fallback_today=True)
    return Response(recouvrement_stats.recovery_by_centre(target_date))


@api_view(["GET"])
@permission_classes([IsAuthenticated])
def recouvrement_by_zone(request):
    """`GET /api/recouvrement/by-zone/?date=&limit=` — top zones par recouvrement."""
    target_date = _parse_date(request.query_params.get("date"), fallback_today=True)
    raw_limit = request.query_params.get("limit", "50")
    try:
        limit = max(1, min(500, int(raw_limit)))
    except (TypeError, ValueError):
        raise ValidationError({"limit": "Doit être un entier."})
    return Response(recouvrement_stats.recovery_by_zone(target_date, limit=limit))


@api_view(["GET"])
@permission_classes([IsAuthenticated])
def recouvrement_movements(request):
    """`GET /api/recouvrement/movements/?date=&type=` — liste détaillée filtrable.

    Types possibles : payment_certain, payment_likely, adjustment, departure,
    new_billing, new_client, no_movement.
    """
    from apps.recouvrement.models import ClientMovement

    qs = ClientMovement.objects.all()
    raw_date = request.query_params.get("date")
    if raw_date:
        qs = qs.filter(date_to=_parse_date(raw_date))
    type_filter = request.query_params.get("type")
    if type_filter:
        qs = qs.filter(type=type_filter)
    centre = request.query_params.get("centre")
    if centre:
        qs = qs.filter(centre_nom__iexact=centre)

    # Pagination simple manuelle
    page = int(request.query_params.get("page", "1"))
    page_size = min(100, int(request.query_params.get("page_size", "50")))
    total = qs.count()
    qs = qs[(page - 1) * page_size : page * page_size]

    return Response(
        {
            "count": total,
            "page": page,
            "page_size": page_size,
            "results": [
                {
                    "id": m.id,
                    "reference_abonnement": m.reference_abonnement,
                    "nom_client": m.nom_client,
                    "type": m.type,
                    "type_display": m.get_type_display(),
                    "confidence": m.confidence,
                    "solde_before": str(m.solde_before) if m.solde_before else None,
                    "solde_after": str(m.solde_after) if m.solde_after else None,
                    "delta_solde": str(m.delta_solde),
                    "code_before": m.code_before,
                    "code_after": m.code_after,
                    "centre_nom": m.centre_nom,
                    "zone": m.zone,
                    "date_from": m.date_from.isoformat() if m.date_from else None,
                    "date_to": m.date_to.isoformat(),
                    "notes": m.notes,
                }
                for m in qs
            ],
        }
    )


@api_view(["GET"])
@permission_classes([IsAuthenticated])
def export_word(request):
    """`GET /api/exports/word/?import_id=N` — rapport Word formaté."""
    import_id = _require_import_id(request)
    imp = _ensure_import_exists(import_id)

    content = exports_module.build_word_report(import_id)
    response = HttpResponse(
        content,
        content_type=(
            "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
        ),
    )
    filename = f"Rapport_SNDE_{imp.file_date.strftime('%Y%m%d')}.docx"
    response["Content-Disposition"] = f'attachment; filename="{filename}"'
    return response


# --------------------------------------------------------------------------- #
# Scoring config — V2.B.3 (admin)
# --------------------------------------------------------------------------- #


class ScoringConfigListCreateView(generics.ListCreateAPIView):
    """`GET /api/scoring/configs/` · `POST /api/scoring/configs/` — admin only."""

    permission_classes = [IsAdmin]
    serializer_class = ScoringConfigSerializer
    queryset = ScoringConfig.objects.select_related("created_by").all()
    pagination_class = None

    def perform_create(self, serializer):
        serializer.save(created_by=self.request.user)


class ScoringConfigDetailView(generics.RetrieveAPIView):
    """`GET /api/scoring/configs/{id}/`"""

    permission_classes = [IsAdmin]
    serializer_class = ScoringConfigSerializer
    queryset = ScoringConfig.objects.select_related("created_by").all()


@api_view(["GET"])
@permission_classes([IsAuthenticated])
def scoring_config_active(request):
    """`GET /api/scoring/configs/active/` — lit la config actuellement active."""
    from apps.scoring.config_service import get_active_config

    config = get_active_config()
    return Response(ScoringConfigSerializer(config).data)


@api_view(["POST"])
@permission_classes([IsAdmin])
def scoring_config_activate(request, pk: int):
    """`POST /api/scoring/configs/{id}/activate/` — bascule la config active."""
    try:
        config = ScoringConfig.objects.get(pk=pk)
    except ScoringConfig.DoesNotExist:
        return Response(
            {"detail": "Configuration introuvable."},
            status=status.HTTP_404_NOT_FOUND,
        )
    activate_config(config)
    logger.info("Config scoring #%s activée par %s", config.id, request.user)
    return Response(ScoringConfigSerializer(config).data)


@api_view(["POST"])
@permission_classes([IsAdmin])
def scoring_config_recompute(request, pk: int):
    """`POST /api/scoring/configs/{id}/recompute/` — applique cette config sur un import.

    Body : `{"import_id": N}` (obligatoire).
    Mode `?sync=1` exécute synchroniquement (utile pour debug).
    """
    from apps.scoring.recompute import recompute_scores_for_import

    try:
        config = ScoringConfig.objects.get(pk=pk)
    except ScoringConfig.DoesNotExist:
        return Response(
            {"detail": "Configuration introuvable."},
            status=status.HTTP_404_NOT_FOUND,
        )

    import_id = request.data.get("import_id")
    if not import_id:
        raise ValidationError(
            {"import_id": "Body 'import_id' obligatoire."}
        )

    sync = request.query_params.get("sync") == "1"
    if sync:
        result = recompute_scores_for_import.apply(args=[import_id, config.id]).get()
        return Response(result)
    else:
        task = recompute_scores_for_import.delay(import_id, config.id)
        return Response(
            {"task_id": task.id, "config_id": config.id, "import_id": import_id},
            status=status.HTTP_202_ACCEPTED,
        )


# --------------------------------------------------------------------------- #
# Détection des imports doublons (même file_date)
# --------------------------------------------------------------------------- #


@api_view(["GET"])
@permission_classes([IsAuthenticated])
def imports_duplicates(request):
    """`GET /api/imports/duplicates/` — liste les groupes d'imports ayant la même file_date.

    Format : [
      {"file_date": "2026-05-08", "imports": [{id, status, uploaded_at, ...}, ...]},
      ...
    ]
    Ne renvoie que les groupes avec ≥2 imports.
    """
    from django.db.models import Count

    duplicate_dates = (
        FabImport.objects.values("file_date")
        .annotate(n=Count("id"))
        .filter(n__gte=2)
        .values_list("file_date", flat=True)
    )

    result = []
    for fd in duplicate_dates:
        imports = FabImport.objects.filter(file_date=fd).select_related(
            "uploaded_by"
        ).order_by("-uploaded_at")
        result.append(
            {
                "file_date": fd.isoformat(),
                "imports": FabImportListSerializer(imports, many=True).data,
            }
        )

    return Response(result)


# --------------------------------------------------------------------------- #
# Timeline client — Axe D (historique des mouvements d'un client)
# --------------------------------------------------------------------------- #


@api_view(["GET"])
@permission_classes([IsAuthenticated])
def client_timeline(request, pk: int):
    """`GET /api/clients/{id}/timeline/` — historique chronologique des mouvements.

    Retourne tous les ClientMovement avec la même `reference_abonnement`,
    triés du plus ancien au plus récent. Permet de voir la trajectoire
    complète : factures, SMS, paiements, etc.
    """
    from apps.recouvrement.models import ClientMovement

    try:
        client = Client.objects.get(pk=pk)
    except Client.DoesNotExist:
        return Response(
            {"detail": "Client introuvable."},
            status=status.HTTP_404_NOT_FOUND,
        )

    movements = (
        ClientMovement.objects.filter(reference_abonnement=client.reference_abonnement)
        .order_by("date_to")
    )

    return Response(
        {
            "reference_abonnement": client.reference_abonnement,
            "nom_client": client.nom_client,
            "centre_nom": client.centre_nom,
            "zone": client.zone,
            "movements": [
                {
                    "id": m.id,
                    "date_to": m.date_to.isoformat(),
                    "date_from": m.date_from.isoformat() if m.date_from else None,
                    "type": m.type,
                    "type_display": m.get_type_display(),
                    "confidence": m.confidence,
                    "solde_before": str(m.solde_before) if m.solde_before else None,
                    "solde_after": str(m.solde_after) if m.solde_after else None,
                    "delta_solde": str(m.delta_solde),
                    "code_before": m.code_before,
                    "code_after": m.code_after,
                    "date_paiement_before": (
                        m.date_paiement_before.isoformat()
                        if m.date_paiement_before
                        else None
                    ),
                    "date_paiement_after": (
                        m.date_paiement_after.isoformat()
                        if m.date_paiement_after
                        else None
                    ),
                    "notes": m.notes,
                }
                for m in movements
            ],
        }
    )


# --------------------------------------------------------------------------- #
# Détection des FABs manqués — V2 Axe B2
# --------------------------------------------------------------------------- #


@api_view(["GET"])
@permission_classes([IsAuthenticated])
def imports_gaps(request):
    """`GET /api/imports/gaps/` — liste les gaps temporels entre imports successifs.

    Retourne tous les couples d'imports successifs (par file_date) où il manque
    au moins 1 jour entre les deux.

    Format : [
      {
        "from_import": {id, file_date},
        "to_import": {id, file_date},
        "days_missing": 2,
      },
      ...
    ]
    """
    imports = list(
        FabImport.objects.filter(status=FabImport.Status.DONE)
        .order_by("file_date")
        .values("id", "file_date")
    )

    gaps = []
    for prev, cur in zip(imports, imports[1:]):
        delta = (cur["file_date"] - prev["file_date"]).days
        if delta > 1:
            gaps.append(
                {
                    "from_import": {
                        "id": prev["id"],
                        "file_date": prev["file_date"].isoformat(),
                    },
                    "to_import": {
                        "id": cur["id"],
                        "file_date": cur["file_date"].isoformat(),
                    },
                    "days_missing": delta - 1,
                }
            )

    return Response(gaps)


# --------------------------------------------------------------------------- #
# Métriques comportementales — V2 Axe C
# --------------------------------------------------------------------------- #


@api_view(["GET"])
@permission_classes([IsAuthenticated])
def recouvrement_behavior(request):
    """`GET /api/recouvrement/behavior/?import_id=N&start=AAAA-MM-JJ&end=AAAA-MM-JJ`

    Retourne 5 métriques comportementales pour la période donnée :
    - pipeline de recouvrement (argent en attente sur l'import courant)
    - taux de chute en code 1
    - vitesse de recouvrement + distribution temporelle
    - taux de réaction SMS J+8
    - taux de réaction SMS J+48h

    Si start/end omis : 30 jours avant la file_date de l'import sélectionné.
    """
    from . import behavior_stats

    import_id = _require_import_id(request)
    imp = _ensure_import_exists(import_id)

    # Période par défaut : 30 jours avant la file_date courante
    raw_start = request.query_params.get("start")
    raw_end = request.query_params.get("end")
    if raw_start:
        start = _parse_date(raw_start)
    else:
        start = imp.file_date - timedelta(days=30)
    if raw_end:
        end = _parse_date(raw_end)
    else:
        end = imp.file_date

    if start > end:
        raise ValidationError({"detail": "'start' doit être ≤ 'end'."})

    return Response(behavior_stats.all_behavior_metrics(import_id, start, end))


# --------------------------------------------------------------------------- #
# Synchronisation S3 (statut + déclenchement manuel)
# --------------------------------------------------------------------------- #


@api_view(["GET", "POST"])
@permission_classes([IsAuthenticated])
def sync_s3_status(request):
    """GET  : statut de la dernière sync S3 (derniers imports s3_auto).
    POST : déclenche manuellement une sync immédiate (admin only).
    """
    from apps.imports.models import FabImport

    if request.method == "POST":
        # Admin only
        if not request.user.is_staff and getattr(request.user, "role", None) != "admin":
            return Response(
                {"detail": "Réservé aux administrateurs."}, status=403
            )
        from apps.scoring.sync_s3 import sync_s3_daily

        # Lance en async via Celery (résultat visible dans les imports s3_auto)
        result = sync_s3_daily.delay()
        return Response(
            {
                "task_id": result.id,
                "message": "Synchronisation déclenchée. Surveiller la page Historique.",
            }
        )

    # GET : récap des derniers imports s3_auto
    recent_auto = FabImport.objects.filter(source=FabImport.Source.S3_AUTO).order_by(
        "-uploaded_at"
    )[:10]

    return Response(
        {
            "last_auto_imports": [
                {
                    "id": imp.id,
                    "file_date": imp.file_date.isoformat(),
                    "uploaded_at": imp.uploaded_at.isoformat(),
                    "status": imp.status,
                    "nb_lines_total": imp.nb_lines_total,
                    "nb_clients_kept": imp.nb_clients_kept,
                }
                for imp in recent_auto
            ],
            "total_auto_imports": FabImport.objects.filter(
                source=FabImport.Source.S3_AUTO
            ).count(),
            "schedule": {
                "frequency": "Quotidien",
                "time": "02:00 Africa/Nouakchott",
                "next_run_info": "Tâche déclenchée par Celery Beat",
            },
        }
    )


# --------------------------------------------------------------------------- #
# Institutions publiques (ONSER, SONADER, SNIM, écoles, etc.)
# --------------------------------------------------------------------------- #


@api_view(["GET"])
@permission_classes([IsAuthenticated])
def institutions_list(request):
    """`GET /api/institutions/` — liste agrégée des institutions détectées."""
    from apps.scoring.institutions import list_institutions_summary

    data = list_institutions_summary()
    return Response({"results": data, "count": len(data)})


@api_view(["GET"])
@permission_classes([IsAuthenticated])
def institution_detail(request, slug):
    """`GET /api/institutions/<slug>/` — détail d'une institution."""
    from apps.scoring.institutions import institution_detail as get_detail

    data = get_detail(slug)
    if data is None:
        return Response({"detail": "Institution non trouvée."}, status=404)
    return Response(data)


@api_view(["GET"])
@permission_classes([IsAuthenticated])
def institution_installations(request, slug):
    """`GET /api/institutions/<slug>/installations/` — liste paginée."""
    from apps.scoring.institutions import institution_installations as get_list

    page = int(request.query_params.get("page", "1"))
    page_size = min(100, int(request.query_params.get("page_size", "50")))
    search = request.query_params.get("search", "")
    category = request.query_params.get("category", "")
    ordering = request.query_params.get("ordering", "-avg_solde")

    data = get_list(
        slug,
        page=page,
        page_size=page_size,
        search=search,
        category=category,
        ordering=ordering,
    )
    if data is None:
        return Response({"detail": "Institution non trouvée."}, status=404)
    return Response(data)


# --------------------------------------------------------------------------- #
# Lookup client par référence (utilisé par la page comportement → détails)
# --------------------------------------------------------------------------- #


@api_view(["GET"])
@permission_classes([IsAuthenticated])
def client_lookup_by_ref(request, reference):
    """`GET /api/clients/by-ref/<reference>/` — retourne l'id du client le plus
    récent pour cette référence (utilisé pour rediriger depuis /comportement).
    """
    last_client = (
        Client.objects.filter(reference_abonnement=reference)
        .order_by("-import_ref__file_date")
        .values("id", "reference_abonnement", "nom_client")
        .first()
    )
    if not last_client:
        return Response({"detail": "Client non trouvé."}, status=404)
    return Response(last_client)


# --------------------------------------------------------------------------- #
# Profils comportementaux (Bon / Moyen / Mauvais payeur)
# --------------------------------------------------------------------------- #


@api_view(["GET"])
@permission_classes([IsAuthenticated])
def client_behaviors_summary(request):
    """`GET /api/behaviors/summary/?type_client=Domestique`

    Synthèse globale : distribution par catégorie, par type, top/bottom 10.
    """
    from apps.recouvrement.models import ClientBehavior
    from django.db.models import Count

    type_filter = request.query_params.get("type_client")  # Domestique | Entreprise | None
    qs = ClientBehavior.objects.all()
    if type_filter in ("Domestique", "Entreprise"):
        qs = qs.filter(type_client=type_filter)

    total = qs.count()
    by_cat = {
        row["category"]: row["n"]
        for row in qs.values("category").annotate(n=Count("reference_abonnement"))
    }
    by_type = {
        row["type_client"]: row["n"]
        for row in qs.values("type_client").annotate(n=Count("reference_abonnement"))
    }

    return Response(
        {
            "total": total,
            "type_filter": type_filter,
            "by_category": {
                "bon": by_cat.get("bon", 0),
                "moyen": by_cat.get("moyen", 0),
                "mauvais": by_cat.get("mauvais", 0),
            },
            "by_type": by_type,
        }
    )


@api_view(["GET"])
@permission_classes([IsAuthenticated])
def client_behaviors_list(request):
    """`GET /api/behaviors/?category=bon&type_client=Domestique&ordering=-behavior_score&page=1`

    Liste paginée filtrée et triée.
    """
    from django.db.models import Q
    from apps.recouvrement.models import ClientBehavior

    qs = ClientBehavior.objects.all()

    # Filtres
    category = request.query_params.get("category")
    if category in ("bon", "moyen", "mauvais"):
        qs = qs.filter(category=category)

    type_filter = request.query_params.get("type_client")
    if type_filter in ("Domestique", "Entreprise"):
        qs = qs.filter(type_client=type_filter)

    zone = request.query_params.get("zone")
    if zone:
        qs = qs.filter(last_zone__icontains=zone)

    releveur = request.query_params.get("releveur")
    if releveur:
        qs = qs.filter(last_releveur=releveur)

    search = request.query_params.get("search")
    if search:
        qs = qs.filter(
            Q(reference_abonnement__icontains=search)
            | Q(nom_client__icontains=search)
        )

    # Tri
    ordering = request.query_params.get("ordering", "-behavior_score")
    allowed = (
        "behavior_score",
        "-behavior_score",
        "nb_payments",
        "-nb_payments",
        "nb_code_1",
        "-nb_code_1",
        "avg_solde",
        "-avg_solde",
        "nom_client",
        "-nom_client",
    )
    if ordering in allowed:
        qs = qs.order_by(ordering)

    # Pagination
    page = int(request.query_params.get("page", "1"))
    page_size = min(100, int(request.query_params.get("page_size", "50")))
    total = qs.count()
    start = (page - 1) * page_size
    rows = list(
        qs[start : start + page_size].values(
            "reference_abonnement",
            "nom_client",
            "type_client",
            "last_zone",
            "last_centre_nom",
            "last_releveur",
            "last_seen_date",
            "nb_imports_seen",
            "nb_payments",
            "nb_code_1",
            "nb_new_billings",
            "avg_solde",
            "max_solde",
            "total_paid",
            "avg_jours_impaye",
            "payment_freq_score",
            "promptness_score",
            "code_1_score",
            "behavior_score",
            "category",
        )
    )

    return Response(
        {
            "count": total,
            "page": page,
            "page_size": page_size,
            "results": [
                {
                    **r,
                    "avg_solde": float(r["avg_solde"]) if r["avg_solde"] is not None else 0,
                    "max_solde": float(r["max_solde"]) if r["max_solde"] is not None else 0,
                    "total_paid": float(r["total_paid"]) if r["total_paid"] is not None else 0,
                    "last_seen_date": (
                        r["last_seen_date"].isoformat() if r["last_seen_date"] else None
                    ),
                }
                for r in rows
            ],
        }
    )


# --------------------------------------------------------------------------- #
# Anomalies opérationnelles
# --------------------------------------------------------------------------- #


@api_view(["GET"])
@permission_classes([IsAuthenticated])
def anomalies_persistent_code_1(request):
    """`GET /api/anomalies/persistent-code-1/?import_id=X&threshold_days=15`

    Détecte les coupures ordonnées (code_relance="1") il y a ≥ threshold_days
    mais jamais exécutées : le client est toujours actif, sa consommation
    continue, et aucun paiement n'a été enregistré.

    Retourne :
        {
            "import": {"id", "file_date"},
            "threshold_days": 15,
            "nb_anomalies": 247,
            "clients": [...],
            "by_tournee": [...],
            "by_releveur": [...],
        }
    """
    from apps.scoring.anomalies import (
        DEFAULT_PERSISTENT_CODE_1_THRESHOLD,
        aggregate_uncut_by_releveur,
        aggregate_uncut_by_zone,
        detect_uncut_clients,
    )

    import_id = _require_import_id(request)
    imp = _ensure_import_exists(import_id)

    threshold_raw = request.query_params.get(
        "threshold_days", str(DEFAULT_PERSISTENT_CODE_1_THRESHOLD)
    )
    try:
        threshold_days = int(threshold_raw)
    except ValueError:
        raise ValidationError({"threshold_days": "Doit être un entier."})
    if threshold_days < 1 or threshold_days > 365:
        raise ValidationError({"threshold_days": "Doit être entre 1 et 365."})

    anomalies = detect_uncut_clients(imp, threshold_days=threshold_days)

    return Response(
        {
            "import": {"id": imp.id, "file_date": imp.file_date.isoformat()},
            "threshold_days": threshold_days,
            "nb_anomalies": len(anomalies),
            "clients": [
                {
                    "client_id": a.client_id,
                    "reference_abonnement": a.reference_abonnement,
                    "nom_client": a.nom_client,
                    "tournee_releve": a.tournee_releve,
                    "releveur_1": a.releveur_1,
                    "centre_nom": a.centre_nom,
                    "zone": a.zone,
                    "solde_current": a.solde_current,
                    "arrieres_current": a.arrieres_current,
                    "date_paiement_current": a.date_paiement_current,
                    "code_1_date": a.code_1_date,
                    "days_since_code_1": a.days_since_code_1,
                    "solde_at_code_1": a.solde_at_code_1,
                    "date_paiement_at_code_1": a.date_paiement_at_code_1,
                    "delta_solde": a.delta_solde,
                }
                for a in anomalies
            ],
            "by_zone": aggregate_uncut_by_zone(anomalies),
            "by_releveur": aggregate_uncut_by_releveur(anomalies),
        }
    )
