from __future__ import annotations

from django.utils import timezone
from rest_framework import serializers

from .models import MeterReading, TerrainAssignment
from .photo_storage import (
    get_photo_presigned_url,
    is_minio_key,
    upload_meter_photo,
)


class TerrainAssignmentSerializer(serializers.ModelSerializer):
    reference_abonnement = serializers.CharField(
        source="client.reference_abonnement", read_only=True
    )
    nom_client = serializers.CharField(source="client.nom_client", read_only=True)
    adresse = serializers.CharField(source="client.adresse", read_only=True)
    telephone = serializers.CharField(source="client.telephone", read_only=True)
    type_client = serializers.CharField(source="client.type_client", read_only=True)
    activite_client = serializers.CharField(source="client.activite_client", read_only=True)
    centre_nom = serializers.CharField(source="client.centre_nom", read_only=True)
    zone = serializers.CharField(source="client.zone", read_only=True)
    secteur_facturation = serializers.CharField(
        source="client.secteur_facturation", read_only=True
    )
    tournee_releve = serializers.CharField(source="client.tournee_releve", read_only=True)
    releveur_1 = serializers.CharField(source="client.releveur_1", read_only=True)
    solde = serializers.DecimalField(
        source="client.solde", max_digits=14, decimal_places=2, read_only=True
    )
    score_final = serializers.FloatField(source="client.score_final", read_only=True)
    proba_paiement = serializers.FloatField(source="client.proba_paiement", read_only=True)
    priorite = serializers.CharField(source="client.priorite", read_only=True)
    code_relance = serializers.CharField(source="client.code_relance", read_only=True)
    jours_sans_paiement = serializers.IntegerField(
        source="client.jours_sans_paiement", read_only=True
    )
    import_date = serializers.DateField(source="import_ref.file_date", read_only=True)
    latest_reading = serializers.SerializerMethodField()

    class Meta:
        model = TerrainAssignment
        fields = (
            "id",
            "status",
            "planned_order",
            "due_date",
            "assigned_at",
            "updated_at",
            "completed_at",
            "last_note",
            "import_date",
            "reference_abonnement",
            "nom_client",
            "adresse",
            "telephone",
            "type_client",
            "activite_client",
            "centre_nom",
            "zone",
            "secteur_facturation",
            "tournee_releve",
            "releveur_1",
            "solde",
            "score_final",
            "proba_paiement",
            "priorite",
            "code_relance",
            "jours_sans_paiement",
            "latest_reading",
        )
        read_only_fields = fields

    def get_latest_reading(self, obj: TerrainAssignment):
        reading = obj.readings.first()
        if reading is None:
            return None
        # Si la photo est une clé MinIO, on génère une URL signée à la volée.
        # Sinon (URL externe historique), on retourne tel quel.
        photo_url = reading.photo_url
        if photo_url and is_minio_key(photo_url):
            try:
                photo_url = get_photo_presigned_url(photo_url)
            except Exception:  # noqa: BLE001
                photo_url = ""
        return {
            "id": reading.id,
            "result": reading.result,
            "meter_index": str(reading.meter_index) if reading.meter_index is not None else None,
            "latitude": str(reading.latitude) if reading.latitude is not None else None,
            "longitude": str(reading.longitude) if reading.longitude is not None else None,
            "comment": reading.comment,
            "photo_url": photo_url,
            "created_at": reading.created_at.isoformat(),
        }


class MeterReadingCreateSerializer(serializers.ModelSerializer):
    # Champ d'upload multipart pour la photo compteur. Si fourni, la photo
    # est uploadée dans MinIO et son URL signée remplace le champ photo_url.
    photo = serializers.ImageField(write_only=True, required=False, allow_null=True)

    class Meta:
        model = MeterReading
        fields = (
            "result",
            "meter_index",
            "latitude",
            "longitude",
            "photo",
            "photo_url",
            "comment",
            "client_timestamp",
        )
        extra_kwargs = {
            "photo_url": {"required": False, "allow_blank": True},
        }

    def validate(self, attrs):
        result = attrs.get("result")
        meter_index = attrs.get("meter_index")
        if result == MeterReading.Result.READING_DONE and meter_index is None:
            raise serializers.ValidationError(
                {"meter_index": "L'index est obligatoire quand le releve est fait."}
            )
        return attrs

    def create(self, validated_data):
        assignment = self.context["assignment"]
        user = self.context["request"].user

        # Si une photo est fournie, on l'upload dans MinIO et on garde la clé
        # dans photo_url. La sérialisation retournera une URL signée.
        photo_file = validated_data.pop("photo", None)
        if photo_file is not None:
            try:
                minio_key = upload_meter_photo(
                    photo_file.file if hasattr(photo_file, "file") else photo_file,
                    assignment_id=assignment.id,
                    filename=getattr(photo_file, "name", None),
                    content_type=getattr(photo_file, "content_type", "image/jpeg"),
                )
                validated_data["photo_url"] = minio_key
            except Exception as exc:  # noqa: BLE001
                raise serializers.ValidationError(
                    {"photo": f"Echec upload photo : {exc}"}
                ) from exc

        reading = MeterReading.objects.create(
            assignment=assignment,
            agent=user,
            **validated_data,
        )

        status_by_result = {
            MeterReading.Result.READING_DONE: TerrainAssignment.Status.DONE,
            MeterReading.Result.ABSENT: TerrainAssignment.Status.ABSENT,
            MeterReading.Result.BLOCKED: TerrainAssignment.Status.BLOCKED,
            MeterReading.Result.INACCESSIBLE: TerrainAssignment.Status.INACCESSIBLE,
            MeterReading.Result.ANOMALY: TerrainAssignment.Status.ANOMALY,
        }
        assignment.status = status_by_result[reading.result]
        assignment.last_note = reading.comment
        assignment.completed_at = timezone.now()
        assignment.save(update_fields=["status", "last_note", "completed_at", "updated_at"])
        return reading


class MeterReadingSerializer(serializers.ModelSerializer):
    class Meta:
        model = MeterReading
        fields = (
            "id",
            "assignment",
            "result",
            "meter_index",
            "latitude",
            "longitude",
            "photo_url",
            "comment",
            "client_timestamp",
            "created_at",
        )
        read_only_fields = fields
