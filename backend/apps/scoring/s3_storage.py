"""Helpers AWS S3 — lecture des FABs déposés par la SNDE.

Le bucket S3 est notre **source de vérité externe** : c'est là que la SNDE
dépose les FABs quotidiens. MinIO est notre **miroir local** (cache + résilience).
Les FABs sont d'abord syncs S3 → MinIO, puis le pipeline existant lit depuis MinIO.

Ce module est READ-ONLY sur S3 : on ne crée jamais, on ne supprime jamais.
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from datetime import date, datetime
from typing import Iterable

import boto3
from botocore.exceptions import BotoCoreError, ClientError
from django.conf import settings

logger = logging.getLogger(__name__)

# Pattern de nom de fichier FAB attendu
FAB_FILENAME_RE = re.compile(r"^fab(\d{4})(\d{2})(\d{2})\.txt$", re.IGNORECASE)


@dataclass
class S3FabObject:
    """Représentation d'un fichier FAB tel qu'il est sur S3."""

    key: str                  # ex: "Solde/fab20260513.txt"
    filename: str             # ex: "fab20260513.txt"
    file_date: date           # ex: 2026-05-13
    size: int                 # octets
    last_modified: datetime   # quand SNDE l'a déposé


def get_s3_client():
    """Construit un client boto3 S3 avec les credentials des settings.

    Lève RuntimeError si les credentials sont vides (= mauvaise configuration).
    """
    if not settings.AWS_ACCESS_KEY_ID or not settings.AWS_SECRET_ACCESS_KEY:
        raise RuntimeError(
            "AWS_ACCESS_KEY_ID / AWS_SECRET_ACCESS_KEY manquants dans .env. "
            "Impossible de se connecter à S3."
        )
    if not settings.AWS_S3_BUCKET:
        raise RuntimeError("AWS_S3_BUCKET manquant dans .env.")

    return boto3.client(
        "s3",
        aws_access_key_id=settings.AWS_ACCESS_KEY_ID,
        aws_secret_access_key=settings.AWS_SECRET_ACCESS_KEY,
        region_name=settings.AWS_S3_REGION,
    )


def extract_date_from_filename(filename: str) -> date | None:
    """Extrait la date depuis 'fabAAAAMMJJ.txt'. None si format invalide."""
    m = FAB_FILENAME_RE.match(filename)
    if not m:
        return None
    try:
        return date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
    except ValueError:
        return None


def list_s3_fabs(
    prefix: str | None = None,
    after: date | None = None,
    before: date | None = None,
) -> list[S3FabObject]:
    """Liste tous les FABs présents sur S3, optionnellement filtrés par date.

    Paginé : récupère tous les objets même si > 1000 (limite par défaut S3).
    Filtre uniquement les fichiers respectant le pattern `fabAAAAMMJJ.txt`.
    """
    bucket = settings.AWS_S3_BUCKET
    prefix = prefix if prefix is not None else settings.AWS_S3_PREFIX or ""
    client = get_s3_client()

    fabs: list[S3FabObject] = []
    continuation: str | None = None

    while True:
        kwargs = {"Bucket": bucket, "Prefix": prefix}
        if continuation:
            kwargs["ContinuationToken"] = continuation

        try:
            response = client.list_objects_v2(**kwargs)
        except (BotoCoreError, ClientError) as exc:
            logger.exception("Erreur S3 list_objects : %s", exc)
            raise

        for obj in response.get("Contents", []):
            key = obj["Key"]
            filename = key.rsplit("/", 1)[-1]
            file_date = extract_date_from_filename(filename)
            if file_date is None:
                continue
            if after and file_date < after:
                continue
            if before and file_date > before:
                continue
            fabs.append(
                S3FabObject(
                    key=key,
                    filename=filename,
                    file_date=file_date,
                    size=obj["Size"],
                    last_modified=obj["LastModified"],
                )
            )

        if not response.get("IsTruncated"):
            break
        continuation = response.get("NextContinuationToken")

    # Tri par date ascendante (plus ancien d'abord)
    fabs.sort(key=lambda f: f.file_date)
    return fabs


def download_s3_object(key: str) -> bytes:
    """Télécharge un objet S3 et retourne ses bytes."""
    bucket = settings.AWS_S3_BUCKET
    client = get_s3_client()
    try:
        response = client.get_object(Bucket=bucket, Key=key)
        return response["Body"].read()
    except (BotoCoreError, ClientError) as exc:
        logger.exception("Erreur S3 get_object %s/%s : %s", bucket, key, exc)
        raise


def is_empty_fab(content: bytes, min_valid_lines: int | None = None) -> tuple[bool, int]:
    """Retourne (is_empty, nb_valid_lines).

    Un FAB est "vide" si moins de `min_valid_lines` lignes ont ≥ 35 colonnes
    après split sur '$$'. Permet de détecter les fichiers envoyés à blanc
    par la SNDE.

    Utilise `decode_fab_bytes()` pour gérer auto UTF-8 / UTF-16 / Windows-1252.
    """
    from .parser import decode_fab_bytes

    if min_valid_lines is None:
        min_valid_lines = settings.FAB_EMPTY_MIN_VALID_LINES

    text = decode_fab_bytes(content)

    valid = 0
    for line in text.splitlines():
        if not line.strip():
            continue
        # Accepte les 2 formats : 33 cols (S3 sans GPS) ou 35 cols (avec GPS)
        if len(line.split("$$")) >= 33:
            valid += 1

    return valid < min_valid_lines, valid


def test_s3_connection() -> dict:
    """Test simple : tente de lister les 5 premiers fichiers du bucket.

    Utile pour valider la configuration depuis une management command.
    """
    bucket = settings.AWS_S3_BUCKET
    prefix = settings.AWS_S3_PREFIX or ""
    client = get_s3_client()
    response = client.list_objects_v2(Bucket=bucket, Prefix=prefix, MaxKeys=5)
    contents = response.get("Contents", [])
    return {
        "bucket": bucket,
        "prefix": prefix,
        "nb_objects_sample": len(contents),
        "sample_keys": [obj["Key"] for obj in contents],
    }
