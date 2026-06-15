"""FilterSets django-filter pour Zone, Client, FabImport et User.

V2.A.1 — filtrage maximal :
- Range filters (min/max) sur les champs numériques (solde, score, jours_impaye…)
- Date range filters (after/before) sur les champs date
- Recherche texte avec `icontains` sur l'activité, le téléphone, etc.
- FilterSets pour FabImport (statut, date, uploader) et User (rôle, statut actif)

Le paramètre `import_id` n'est pas dans les filtersets : il est géré dans le
`get_queryset` des vues comme paramètre **obligatoire**.
"""
from django.contrib.auth import get_user_model
from django_filters import rest_framework as filters

from apps.clients.models import Client
from apps.imports.models import FabImport
from apps.zones.models import Zone

User = get_user_model()


# --------------------------------------------------------------------------- #
# Zone
# --------------------------------------------------------------------------- #


class ZoneFilter(filters.FilterSet):
    """Filtres pour `GET /api/zones/?priorite=Haute&centre=KIFFA2&nb_clients_min=50&...`"""

    centre = filters.CharFilter(field_name="centre_nom", lookup_expr="iexact")
    secteur = filters.CharFilter(lookup_expr="iexact")
    tournee = filters.CharFilter(lookup_expr="iexact")

    # Ranges numériques
    nb_clients_min = filters.NumberFilter(field_name="nb_clients", lookup_expr="gte")
    nb_clients_max = filters.NumberFilter(field_name="nb_clients", lookup_expr="lte")
    score_min = filters.NumberFilter(field_name="score_moyen", lookup_expr="gte")
    score_max = filters.NumberFilter(field_name="score_moyen", lookup_expr="lte")
    solde_min = filters.NumberFilter(field_name="solde_total", lookup_expr="gte")
    solde_max = filters.NumberFilter(field_name="solde_total", lookup_expr="lte")
    priorite_zone_min = filters.NumberFilter(
        field_name="priorite_zone", lookup_expr="gte"
    )

    class Meta:
        model = Zone
        fields = ["priorite", "centre"]


# --------------------------------------------------------------------------- #
# Client
# --------------------------------------------------------------------------- #


class ClientFilter(filters.FilterSet):
    """Filtres pour `GET /api/clients/?priorite=Haute&zone=KIFFA2_18_11&solde_min=50000&...`"""

    zone = filters.CharFilter(lookup_expr="iexact")
    centre = filters.CharFilter(field_name="code_centre", lookup_expr="iexact")
    secteur = filters.CharFilter(field_name="secteur_facturation", lookup_expr="iexact")
    tournee = filters.CharFilter(field_name="tournee_releve", lookup_expr="iexact")
    releveur = filters.CharFilter(field_name="releveur_1", lookup_expr="iexact")

    # Code de relance (0=repos / 4=SMS J+8 / 2=SMS J+48h / 1=coupure / 3=autre)
    code_relance = filters.CharFilter(lookup_expr="exact")
    # État dérivé du cycle (normal / grace_j8 / sms_j48h / cut_off / anomaly_overdue…)
    relance_state = filters.CharFilter(lookup_expr="exact")
    # is_scored est dérivé côté modèle (property). Côté filtre, on traduit en
    # critère sur score_final (null = non scoré, non-null = scoré).
    is_scored = filters.BooleanFilter(method="filter_is_scored")

    # Recherche texte sur l'activité (USINE, BOULANGERIE, TOUS CLIENTS DOMESTIQUES…)
    activite = filters.CharFilter(field_name="activite_client", lookup_expr="icontains")

    # Ranges numériques
    solde_min = filters.NumberFilter(field_name="solde", lookup_expr="gte")
    solde_max = filters.NumberFilter(field_name="solde", lookup_expr="lte")
    arrieres_min = filters.NumberFilter(field_name="arrieres", lookup_expr="gte")
    arrieres_max = filters.NumberFilter(field_name="arrieres", lookup_expr="lte")
    score_min = filters.NumberFilter(field_name="score_final", lookup_expr="gte")
    score_max = filters.NumberFilter(field_name="score_final", lookup_expr="lte")
    jours_impaye_min = filters.NumberFilter(
        field_name="jours_impaye", lookup_expr="gte"
    )
    jours_impaye_max = filters.NumberFilter(
        field_name="jours_impaye", lookup_expr="lte"
    )
    jours_sans_paiement_min = filters.NumberFilter(
        field_name="jours_sans_paiement", lookup_expr="gte"
    )
    jours_sans_paiement_max = filters.NumberFilter(
        field_name="jours_sans_paiement", lookup_expr="lte"
    )

    # Date ranges
    date_facture_after = filters.DateFilter(
        field_name="date_facture", lookup_expr="gte"
    )
    date_facture_before = filters.DateFilter(
        field_name="date_facture", lookup_expr="lte"
    )
    date_paiement_after = filters.DateFilter(
        field_name="date_dernier_paiement", lookup_expr="gte"
    )
    date_paiement_before = filters.DateFilter(
        field_name="date_dernier_paiement", lookup_expr="lte"
    )

    # Présence / absence de téléphone (utile pour le suivi terrain)
    has_telephone = filters.BooleanFilter(method="filter_has_telephone")

    class Meta:
        model = Client
        fields = [
            "priorite",
            "type_client",
            "zone",
            "centre",
            "code_relance",
            "relance_state",
        ]

    def filter_has_telephone(self, queryset, _name, value):
        if value is True:
            return queryset.exclude(telephone="")
        if value is False:
            return queryset.filter(telephone="")
        return queryset

    def filter_is_scored(self, queryset, _name, value):
        """is_scored=True → score_final non null. is_scored=False → null."""
        if value is True:
            return queryset.filter(score_final__isnull=False)
        if value is False:
            return queryset.filter(score_final__isnull=True)
        return queryset


# --------------------------------------------------------------------------- #
# FabImport
# --------------------------------------------------------------------------- #


class FabImportFilter(filters.FilterSet):
    """Filtres pour `GET /api/imports/?status=done&file_date_after=2026-04-01&...`"""

    uploader = filters.CharFilter(
        field_name="uploaded_by__username", lookup_expr="icontains"
    )

    file_date_after = filters.DateFilter(field_name="file_date", lookup_expr="gte")
    file_date_before = filters.DateFilter(field_name="file_date", lookup_expr="lte")
    uploaded_at_after = filters.DateFilter(
        field_name="uploaded_at", lookup_expr="gte"
    )
    uploaded_at_before = filters.DateFilter(
        field_name="uploaded_at", lookup_expr="lte"
    )

    nb_clients_min = filters.NumberFilter(
        field_name="nb_clients_kept", lookup_expr="gte"
    )

    class Meta:
        model = FabImport
        fields = ["status"]


# --------------------------------------------------------------------------- #
# User (admin)
# --------------------------------------------------------------------------- #


class UserFilter(filters.FilterSet):
    """Filtres pour `GET /api/users/?role=admin&is_active=true`"""

    class Meta:
        model = User
        fields = ["role", "is_active"]
