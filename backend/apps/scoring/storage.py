"""Helpers MinIO pour télécharger / déposer les fichiers FAB."""
from pathlib import Path

from django.conf import settings
from minio import Minio


def get_minio_client() -> Minio:
    """Retourne un client MinIO configuré depuis les settings Django."""
    return Minio(
        settings.MINIO_ENDPOINT,
        access_key=settings.MINIO_ACCESS_KEY,
        secret_key=settings.MINIO_SECRET_KEY,
        secure=settings.MINIO_USE_SSL,
    )


def ensure_bucket(bucket: str | None = None) -> str:
    """Crée le bucket s'il n'existe pas. Retourne son nom."""
    bucket = bucket or settings.MINIO_BUCKET_FAB
    client = get_minio_client()
    if not client.bucket_exists(bucket):
        client.make_bucket(bucket)
    return bucket


def download_fab(minio_key: str, bucket: str | None = None) -> bytes:
    """Télécharge un objet FAB depuis MinIO et retourne ses bytes bruts."""
    bucket = bucket or settings.MINIO_BUCKET_FAB
    client = get_minio_client()
    response = client.get_object(bucket, minio_key)
    try:
        return response.read()
    finally:
        response.close()
        response.release_conn()


def upload_fab(local_path: str | Path, minio_key: str, bucket: str | None = None) -> str:
    """Dépose un fichier local sur MinIO. Retourne la clé."""
    bucket = bucket or settings.MINIO_BUCKET_FAB
    ensure_bucket(bucket)
    client = get_minio_client()
    client.fput_object(bucket, minio_key, str(local_path))
    return minio_key


def delete_fab(minio_key: str, bucket: str | None = None) -> bool:
    """Supprime un objet MinIO. Retourne True si OK, False si erreur (loggée)."""
    import logging

    logger = logging.getLogger(__name__)
    bucket = bucket or settings.MINIO_BUCKET_FAB
    try:
        client = get_minio_client()
        client.remove_object(bucket, minio_key)
        logger.info("Objet MinIO supprimé : %s/%s", bucket, minio_key)
        return True
    except Exception as exc:  # noqa: BLE001
        logger.warning("Échec suppression MinIO %s/%s : %s", bucket, minio_key, exc)
        return False


def upload_file_object(
    file_obj,
    minio_key: str,
    length: int,
    bucket: str | None = None,
    content_type: str = "text/plain",
) -> str:
    """Dépose un objet `file-like` (ex: `request.FILES['file']`) sur MinIO.

    `length` est la taille connue du fichier en octets (obligatoire pour MinIO).
    Retourne la clé.
    """
    bucket = bucket or settings.MINIO_BUCKET_FAB
    ensure_bucket(bucket)
    client = get_minio_client()
    file_obj.seek(0)
    client.put_object(
        bucket,
        minio_key,
        file_obj,
        length=length,
        content_type=content_type,
    )
    return minio_key
