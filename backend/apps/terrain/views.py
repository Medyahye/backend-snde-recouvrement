from __future__ import annotations

from django.db import transaction
from django.db.models import Count, F, Q
from rest_framework import generics, status
from rest_framework.decorators import api_view, permission_classes
from rest_framework.exceptions import NotFound, PermissionDenied
from rest_framework.parsers import FormParser, JSONParser, MultiPartParser
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.clients.models import Client
from apps.imports.models import FabImport

from .models import MeterReading, TerrainAssignment
from .serializers import (
    MeterReadingCreateSerializer,
    MeterReadingSerializer,
    TerrainAssignmentSerializer,
)


def _is_terrain_user(user) -> bool:
    return user.is_superuser or getattr(user, "role", None) in {"admin", "terrain"}


def _latest_done_import() -> FabImport | None:
    return FabImport.objects.filter(status=FabImport.Status.DONE).order_by("-file_date").first()


def _client_matches_agent(client: Client, user) -> bool:
    markers = {
        str(user.username or "").strip().lower(),
        str(user.email or "").strip().lower(),
        str(user.first_name or "").strip().lower(),
        str(user.last_name or "").strip().lower(),
    }
    releveur = str(client.releveur_1 or "").strip().lower()
    return bool(releveur and releveur in markers)


def ensure_assignments_for_user(user) -> FabImport | None:
    """
    Cree automatiquement les affectations du dernier import pour le releveur.

    En production, un superviseur pourra affecter manuellement. Cette fonction
    donne deja un comportement utile : si le code releveur FAB correspond au
    username/email/nom du compte terrain, l'app mobile recoit sa liste.
    """
    latest_import = _latest_done_import()
    if latest_import is None:
        return None

    markers = [
        marker
        for marker in {
            str(user.username or "").strip(),
            str(user.email or "").strip(),
            str(user.first_name or "").strip(),
            str(user.last_name or "").strip(),
        }
        if marker
    ]

    clients = list(
        Client.objects.filter(import_ref=latest_import, code_relance="1")
        .filter(
            Q(releveur_1__in=markers)
            | Q(releveur_1__iexact=user.username)
            | Q(releveur_1__iexact=user.email)
            | Q(releveur_1__iexact=user.first_name)
            | Q(releveur_1__iexact=user.last_name)
        )
        .order_by(F("proba_paiement").desc(nulls_last=True), "reference_abonnement")
    )

    if not clients and user.is_superuser:
        clients = list(
            Client.objects.filter(import_ref=latest_import, code_relance="1")
            .order_by(F("proba_paiement").desc(nulls_last=True), "reference_abonnement")[:100]
        )

    existing_client_ids = set(
        TerrainAssignment.objects.filter(import_ref=latest_import, agent=user)
        .values_list("client_id", flat=True)
    )
    existing_count = len(existing_client_ids)

    assignments = []
    for client in clients:
        if client.id in existing_client_ids:
            continue
        if not (_client_matches_agent(client, user) or user.is_superuser):
            continue
        assignments.append(
            TerrainAssignment(
                import_ref=latest_import,
                client=client,
                agent=user,
                planned_order=existing_count + len(assignments) + 1,
            )
        )
    if assignments:
        TerrainAssignment.objects.bulk_create(assignments, ignore_conflicts=True)

    return latest_import


DONE_STATUSES = (
    TerrainAssignment.Status.DONE,
    TerrainAssignment.Status.ABSENT,
    TerrainAssignment.Status.BLOCKED,
    TerrainAssignment.Status.INACCESSIBLE,
    TerrainAssignment.Status.ANOMALY,
)


def _build_fab_summary(agent, fab_import: FabImport) -> dict:
    """Stats par FAB pour un agent : total / faits / restants + by_status."""
    qs = TerrainAssignment.objects.filter(
        agent=agent,
        import_ref=fab_import,
        client__code_relance="1",
    )
    by_status = {
        row["status"]: row["count"]
        for row in qs.values("status").annotate(count=Count("id"))
    }
    total = sum(by_status.values())
    done = sum(by_status.get(s, 0) for s in DONE_STATUSES)
    return {
        "import": {
            "id": fab_import.id,
            "file_date": fab_import.file_date.isoformat(),
        },
        "total": total,
        "done": done,
        "remaining": max(0, total - done),
        "by_status": by_status,
    }


@api_view(["GET"])
@permission_classes([IsAuthenticated])
def mobile_summary(request):
    if not _is_terrain_user(request.user):
        raise PermissionDenied("Acces terrain reserve aux releveurs.")

    latest_import = ensure_assignments_for_user(request.user)

    # Tous les FABs où cet agent a au moins une affectation, par date desc.
    fab_ids = (
        TerrainAssignment.objects.filter(
            agent=request.user, client__code_relance="1",
        )
        .values_list("import_ref_id", flat=True)
        .distinct()
    )
    fab_imports = list(
        FabImport.objects.filter(id__in=fab_ids).order_by("-file_date")
    )
    fabs = [_build_fab_summary(request.user, fab) for fab in fab_imports]

    # Le FAB "courant" = le dernier import done (ou le plus récent dans la liste).
    current = None
    if latest_import:
        current = next(
            (f for f in fabs if f["import"]["id"] == latest_import.id), None
        )
    if current is None and fabs:
        current = fabs[0]

    # Total accumulé sur les FABs anciens (hors courant) qui ont des restants.
    accumulated_remaining = sum(
        f["remaining"]
        for f in fabs
        if current is None or f["import"]["id"] != current["import"]["id"]
    )

    return Response(
        {
            # Champs "courant" rétrocompatibles (anciens clients mobile).
            "import": current["import"] if current else None,
            "total": current["total"] if current else 0,
            "done": current["done"] if current else 0,
            "remaining": current["remaining"] if current else 0,
            "by_status": current["by_status"] if current else {},
            # Nouveaux champs multi-FAB.
            "fabs": fabs,
            "accumulated_remaining": accumulated_remaining,
        }
    )


class MobileAssignmentListView(generics.ListAPIView):
    permission_classes = [IsAuthenticated]
    serializer_class = TerrainAssignmentSerializer
    # Utilise la pagination DRF par défaut (StandardResultsSetPagination, 50/page).
    # Le mobile fait du infinite scroll via la query string ?page=N.

    def get_queryset(self):
        if not _is_terrain_user(self.request.user):
            raise PermissionDenied("Acces terrain reserve aux releveurs.")

        latest_import = ensure_assignments_for_user(self.request.user)
        qs = (
            TerrainAssignment.objects.select_related("client", "import_ref")
            .prefetch_related("readings")
            .filter(agent=self.request.user, client__code_relance="1")
        )

        # Filtrage par FAB :
        #   ?fab=<id>  → FAB explicite
        #   ?fab=all   → toutes les affectations de l'agent (tous FABs confondus)
        #   absent     → FAB courant par défaut (dernier import done)
        fab_param = self.request.query_params.get("fab")
        if fab_param == "all":
            pass  # pas de filtre
        elif fab_param and fab_param.isdigit():
            qs = qs.filter(import_ref_id=int(fab_param))
        elif latest_import:
            qs = qs.filter(import_ref=latest_import)

        status_filter = self.request.query_params.get("status")
        if status_filter == "done":
            # "Faits" = toutes les visites terminées (tout sauf todo / in_progress)
            qs = qs.filter(status__in=DONE_STATUSES)
        elif status_filter == "todo":
            qs = qs.filter(
                status__in=(
                    TerrainAssignment.Status.TODO,
                    TerrainAssignment.Status.IN_PROGRESS,
                )
            )
        elif status_filter:
            qs = qs.filter(status=status_filter)

        search = self.request.query_params.get("search")
        if search:
            qs = qs.filter(
                Q(client__reference_abonnement__icontains=search)
                | Q(client__nom_client__icontains=search)
                | Q(client__adresse__icontains=search)
                | Q(client__zone__icontains=search)
                | Q(client__centre_nom__icontains=search)
            )

        # Filtre par priorité (Haute / Moyenne / Faible)
        priorite = self.request.query_params.get("priorite")
        if priorite:
            qs = qs.filter(client__priorite=priorite)

        # Filtre par intervalle IA — proba [proba_min, proba_max]
        proba_min = self.request.query_params.get("proba_min")
        proba_max = self.request.query_params.get("proba_max")
        if proba_min is not None:
            try:
                qs = qs.filter(client__proba_paiement__gte=float(proba_min))
            except (TypeError, ValueError):
                pass
        if proba_max is not None:
            try:
                qs = qs.filter(client__proba_paiement__lte=float(proba_max))
            except (TypeError, ValueError):
                pass

        # Filtre zone explicite (en plus de la recherche fuzzy)
        zone = self.request.query_params.get("zone")
        if zone:
            qs = qs.filter(client__zone__iexact=zone)

        sort = self.request.query_params.get("sort", "prob")
        if sort == "score":
            return qs.order_by(
                F("client__score_final").desc(nulls_last=True),
                "client__reference_abonnement",
            )

        return qs.order_by(
            F("client__proba_paiement").desc(nulls_last=True),
            "client__reference_abonnement",
        )


class MobileAssignmentDetailView(generics.RetrieveAPIView):
    permission_classes = [IsAuthenticated]
    serializer_class = TerrainAssignmentSerializer

    def get_queryset(self):
        return (
            TerrainAssignment.objects.select_related("client", "import_ref")
            .prefetch_related("readings")
            .filter(agent=self.request.user, client__code_relance="1")
        )


class MobileReadingCreateView(APIView):
    permission_classes = [IsAuthenticated]
    # JSON pour les requêtes sans photo (et la queue offline) + multipart pour
    # les uploads photo. FormParser au cas où le client envoie urlencoded.
    parser_classes = [JSONParser, MultiPartParser, FormParser]

    @transaction.atomic
    def post(self, request, pk: int):
        try:
            assignment = TerrainAssignment.objects.select_for_update().get(
                pk=pk,
                agent=request.user,
            )
        except TerrainAssignment.DoesNotExist:
            raise NotFound("Affectation introuvable.")

        serializer = MeterReadingCreateSerializer(
            data=request.data,
            context={"request": request, "assignment": assignment},
        )
        serializer.is_valid(raise_exception=True)
        reading = serializer.save()
        return Response(
            MeterReadingSerializer(reading).data,
            status=status.HTTP_201_CREATED,
        )
