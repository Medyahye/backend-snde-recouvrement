"""Stockage des photos de compteur dans MinIO.

Les photos sont uploadées par l'app mobile via multipart, stockées dans le
bucket `meter-photos`, et exposées au front via une URL signée 7 jours.

Convention de clé : `photos/<assignment_id>/<uuid>.<ext>`
"""
from __future__ import annotations

import logging
import uuid
from datetime import timedelta
from urllib.parse import urlparse, urlunparse

from django.conf import settings
from minio import Minio

from apps.scoring.storage import ensure_bucket, get_minio_client

logger = logging.getLogger(__name__)

# Sept jours — la mobile peut afficher la photo le temps qu'elle reste pertinente.
# Pour archivage long terme, régénérer une URL signée à la demande côté serveur.
PRESIGNED_TTL = timedelta(days=7)

ALLOWED_EXTENSIONS = {"jpg", "jpeg", "png", "webp", "heic"}


def _extension_from_filename(name: str) -> str:
    if "." not in name:
        return "jpg"
    ext = name.rsplit(".", 1)[-1].lower()
    return ext if ext in ALLOWED_EXTENSIONS else "jpg"


def upload_meter_photo(
    file_obj,
    assignment_id: int,
    filename: str | None = None,
    content_type: str = "image/jpeg",
) -> str:
    """Upload une photo de compteur dans MinIO et retourne la clé MinIO.

    Le caller doit ensuite générer une URL signée via `get_photo_presigned_url`.
    """
    bucket = settings.MINIO_BUCKET_PHOTOS
    ensure_bucket(bucket)

    ext = _extension_from_filename(filename or "")
    photo_id = uuid.uuid4().hex
    key = f"photos/{assignment_id}/{photo_id}.{ext}"

    # On lit l'objet entier (les photos compteur ne sont pas énormes : <5 Mo).
    # Pour de très gros fichiers, on passerait par upload multipart.
    file_obj.seek(0, 2)
    length = file_obj.tell()
    file_obj.seek(0)

    client = get_minio_client()
    client.put_object(
        bucket,
        key,
        file_obj,
        length=length,
        content_type=content_type,
    )
    logger.info("Photo compteur uploadée : %s/%s (%d octets)", bucket, key, length)
    return key


def get_photo_presigned_url(minio_key: str) -> str:
    """Génère une URL HTTPs signée pour télécharger une photo.

    L'URL est valide PRESIGNED_TTL (7 jours par défaut). Le host est réécrit
    avec MINIO_PUBLIC_ENDPOINT pour que le mobile puisse l'atteindre depuis
    le LAN (sinon la clé renverrait `minio:9000` qui n'existe que dans Docker).
    """
    bucket = settings.MINIO_BUCKET_PHOTOS
    client = get_minio_client()
    raw_url = client.presigned_get_object(bucket, minio_key, expires=PRESIGNED_TTL)

    # Réécriture du host : remplace "minio:9000" par l'endpoint public.
    public = settings.MINIO_PUBLIC_ENDPOINT
    if public and public != settings.MINIO_ENDPOINT:
        parsed = urlparse(raw_url)
        scheme = "https" if settings.MINIO_USE_SSL else "http"
        return urlunparse(
            (scheme, public, parsed.path, parsed.params, parsed.query, parsed.fragment)
        )
    return raw_url


def is_minio_key(value: str) -> bool:
    """Détecte si un photo_url stocké en DB est une clé MinIO (vs une URL externe)."""
    return value.startswith("photos/")
