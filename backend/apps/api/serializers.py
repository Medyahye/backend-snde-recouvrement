"""Serializers DRF — étapes 4.1 à 4.3 + V2.B.3 (scoring config)."""
from django.conf import settings
from django.contrib.auth import get_user_model
from rest_framework import serializers

from apps.clients.models import Client
from apps.imports.models import FabImport
from apps.scoring.models import ScoringConfig
from apps.zones.models import Centre, Zone

User = get_user_model()


class ScoringConfigSerializer(serializers.ModelSerializer):
    """V2.B.3 — config de scoring versionnée (admin)."""

    created_by_username = serializers.CharField(
        source="created_by.username", read_only=True
    )
    weights_sum = serializers.SerializerMethodField()

    class Meta:
        model = ScoringConfig
        fields = (
            "id",
            "weight_montant",
            "weight_anciennete",
            "weight_historique",
            "weight_arrieres",
            "coef_domestique",
            "coef_entreprise",
            "threshold_days",
            "priority_quantile_high",
            "priority_quantile_med",
            "is_active",
            "description",
            "created_at",
            "created_by",
            "created_by_username",
            "weights_sum",
        )
        read_only_fields = (
            "id",
            "is_active",
            "created_at",
            "created_by",
            "created_by_username",
            "weights_sum",
        )

    def get_weights_sum(self, obj: ScoringConfig) -> float:
        return round(
            obj.weight_montant
            + obj.weight_anciennete
            + obj.weight_historique
            + obj.weight_arrieres,
            4,
        )


class CentreSerializer(serializers.ModelSerializer):
    """Vue table de référence (lookup) — étape 4.3."""

    class Meta:
        model = Centre
        fields = ("code", "nom")
        read_only_fields = fields


class UserSlimSerializer(serializers.ModelSerializer):
    """Représentation compacte d'un utilisateur (pour /auth/me et embed dans imports)."""

    class Meta:
        model = User
        fields = ("id", "username", "email", "role", "first_name", "last_name")
        read_only_fields = fields


class UserListSerializer(serializers.ModelSerializer):
    """Vue admin liste — étape 5.3."""

    class Meta:
        model = User
        fields = (
            "id",
            "username",
            "email",
            "role",
            "first_name",
            "last_name",
            "is_active",
            "date_joined",
            "last_login",
        )
        read_only_fields = ("id", "date_joined", "last_login")


class UserCreateSerializer(serializers.ModelSerializer):
    """Création d'un user — étape 5.3 (admin)."""

    password = serializers.CharField(write_only=True, min_length=4)

    class Meta:
        model = User
        fields = (
            "id",
            "username",
            "email",
            "password",
            "role",
            "first_name",
            "last_name",
            "is_active",
        )
        read_only_fields = ("id",)

    def create(self, validated_data):
        password = validated_data.pop("password")
        user = User(**validated_data)
        user.set_password(password)
        user.save()
        return user


class UserUpdateSerializer(serializers.ModelSerializer):
    """Mise à jour d'un user — étape 5.3 (admin)."""

    password = serializers.CharField(write_only=True, required=False, min_length=4)

    class Meta:
        model = User
        fields = (
            "id",
            "username",
            "email",
            "password",
            "role",
            "first_name",
            "last_name",
            "is_active",
        )
        read_only_fields = ("id", "username")

    def update(self, instance, validated_data):
        password = validated_data.pop("password", None)
        for k, v in validated_data.items():
            setattr(instance, k, v)
        if password:
            instance.set_password(password)
        instance.save()
        return instance


class FabImportListSerializer(serializers.ModelSerializer):
    """Représentation paginée d'un import (vue liste)."""

    uploaded_by = UserSlimSerializer(read_only=True)
    duration_seconds = serializers.SerializerMethodField()

    class Meta:
        model = FabImport
        fields = (
            "id",
            "minio_key",
            "file_date",
            "uploaded_by",
            "uploaded_at",
            "status",
            "nb_lines_total",
            "nb_clients_kept",
            "nb_clients_total",
            "duration_seconds",
        )
        read_only_fields = fields

    def get_duration_seconds(self, obj: FabImport) -> int | None:
        if obj.started_at and obj.finished_at:
            return int((obj.finished_at - obj.started_at).total_seconds())
        return None


class FabImportDetailSerializer(FabImportListSerializer):
    """Vue détaillée : même chose + horodatage worker + message d'erreur."""

    class Meta(FabImportListSerializer.Meta):
        fields = FabImportListSerializer.Meta.fields + (
            "started_at",
            "finished_at",
            "error_message",
        )
        read_only_fields = fields


# --------------------------------------------------------------------------- #
# Zones — étape 4.2
# --------------------------------------------------------------------------- #


class ZoneListSerializer(serializers.ModelSerializer):
    """Vue liste (paginée) — payload léger pour le tableau /zones."""

    class Meta:
        model = Zone
        fields = (
            "id",
            "rang",
            "zone_id",
            "centre_nom",
            "secteur",
            "tournee",
            "nb_clients",
            "score_moyen",
            "priorite_zone",
            "priorite",
            "solde_total",
        )
        read_only_fields = fields


class ZoneDetailSerializer(serializers.ModelSerializer):
    """Vue détaillée d'une zone : toutes les agrégations."""

    class Meta:
        model = Zone
        fields = (
            "id",
            "import_ref",
            "rang",
            "zone_id",
            "centre_nom",
            "secteur",
            "tournee",
            "nb_clients",
            "nb_entreprises",
            "nb_domestiques",
            "score_moyen",
            "score_max",
            "score_total",
            "anciennete_moyenne",
            "solde_total",
            "solde_moyen",
            "arrieres_total",
            "priorite_zone",
            "priorite",
        )
        read_only_fields = fields


# --------------------------------------------------------------------------- #
# Clients — étape 4.2
# --------------------------------------------------------------------------- #


class ClientListSerializer(serializers.ModelSerializer):
    """Vue liste (paginée) — payload léger pour le tableau clients."""

    relance_state_display = serializers.CharField(
        source="get_relance_state_display", read_only=True
    )
    score_manuel = serializers.SerializerMethodField()

    def get_score_manuel(self, obj: Client) -> float | None:
        return _compute_manual_score(obj)

    class Meta:
        model = Client
        fields = (
            "id",
            "rang",
            "reference_abonnement",
            "nom_client",
            "telephone",
            "type_client",
            "centre_nom",
            "zone",
            "solde",
            "score_final",
            "proba_paiement",
            "score_manuel",
            "priorite",
            "jours_sans_paiement",
            "code_relance",
            "relance_state",
            "relance_state_display",
        )
        read_only_fields = fields


class ClientDetailSerializer(serializers.ModelSerializer):
    """Vue détaillée d'un client : tous les champs, y compris les composantes
    normalisées (utile pour expliquer le score à l'utilisateur)."""

    score_manuel = serializers.SerializerMethodField()

    def get_score_manuel(self, obj: Client) -> float | None:
        return _compute_manual_score(obj)

    class Meta:
        model = Client
        fields = (
            "id",
            "import_ref",
            "rang",
            "reference_abonnement",
            "nom_client",
            "adresse",
            "telephone",
            "activite_client",
            "type_client",
            "code_centre",
            "centre_nom",
            "secteur_facturation",
            "tournee_releve",
            "releveur_1",
            "zone",
            "solde",
            "montant_facture",
            "arrieres",
            "date_facture",
            "date_dernier_paiement",
            "jours_impaye",
            "jours_sans_paiement",
            "code_relance",
            "relance_state",
            "montant_norm",
            "anciennete_norm",
            "historique_norm",
            "arrieres_norm",
            "coefficient_type",
            "score_final",
            "proba_paiement",
            "score_manuel",
            "priorite",
        )
        read_only_fields = fields


def _compute_manual_score(obj: Client) -> float | None:
    values = (
        obj.montant_norm,
        obj.anciennete_norm,
        obj.historique_norm,
        obj.arrieres_norm,
        obj.coefficient_type,
    )
    if any(v is None for v in values):
        return None

    weights = settings.SCORING_WEIGHTS
    score = (
        weights["MONTANT"] * obj.montant_norm
        + weights["ANCIENNETE"] * obj.anciennete_norm
        + weights["HISTORIQUE"] * obj.historique_norm
        + weights["ARRIERES"] * obj.arrieres_norm
    ) * obj.coefficient_type
    return round(float(score), 6)
