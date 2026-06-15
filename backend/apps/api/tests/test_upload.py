"""Tests d'intégration de l'endpoint POST /api/imports/upload/.

On mocke les appels MinIO et Celery (pas besoin du worker pour les tests).
"""
from datetime import timedelta
from unittest.mock import patch

import pytest
from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase as DjangoTestCase
from django.test import override_settings
from django.urls import reverse
from django.utils import timezone
from rest_framework.test import APIClient

User = get_user_model()


def _valid_fab_bytes(nb_lines: int = 5) -> bytes:
    """Construit un mini-FAB structurellement valide (≥35 colonnes par ligne)."""
    line = "$$".join(["1", "2026", "5"] + [""] * 31 + ["1"])
    return ("\n".join([line] * nb_lines)).encode("utf-8")


def _today_filename() -> str:
    today = timezone.localdate()
    return f"fab{today.year:04d}{today.month:02d}{today.day:02d}.txt"


@pytest.fixture
def authed_client(db):
    user = User.objects.create_user(
        username="tester@snde.local",
        email="tester@snde.local",
        password="testpass",
        role="gestionnaire",
    )
    client = APIClient()
    client.force_authenticate(user=user)
    return client, user


@pytest.fixture(autouse=True)
def mute_minio_and_celery():
    """Évite tout appel réseau dans les tests."""
    with patch("apps.api.views.upload_file_object") as up_mock, patch(
        "apps.api.views.process_fab_import"
    ) as task_mock:
        up_mock.return_value = "fab/2026/05/abc_test.txt"
        yield up_mock, task_mock


# --- Auth ---

def test_upload_requires_auth(db):
    client = APIClient()
    res = client.post(reverse("imports_upload"), {})
    assert res.status_code == 401


def test_me_returns_user(authed_client):
    client, user = authed_client
    res = client.get(reverse("auth_me"))
    assert res.status_code == 200
    assert res.data["username"] == user.username
    assert res.data["role"] == "gestionnaire"


# --- Upload : cas nominal ---

def test_upload_valid_fab_creates_import_and_dispatches(
    authed_client, mute_minio_and_celery
):
    """L'upload doit créer un FabImport et dispatcher la tâche Celery
    après commit DB (cf. transaction.on_commit dans la vue)."""
    client, _ = authed_client
    up_mock, task_mock = mute_minio_and_celery

    f = SimpleUploadedFile(
        _today_filename(),
        _valid_fab_bytes(10),
        content_type="text/plain",
    )
    # captureOnCommitCallbacks(execute=True) exécute manuellement les callbacks
    # on_commit qui sinon ne se déclencheraient pas dans la transaction de test
    # (rollback automatique en fin de test).
    with DjangoTestCase.captureOnCommitCallbacks(execute=True) as callbacks:
        res = client.post(
            reverse("imports_upload"), {"file": f}, format="multipart"
        )

    assert res.status_code == 202, res.content
    assert res.data["status"] == "pending"
    assert "id" in res.data
    assert "minio_key" in res.data
    # MinIO appelé pendant l'upload, Celery dispatché lors du commit
    assert up_mock.call_count == 1
    assert task_mock.delay.call_count == 1
    assert len(callbacks) == 1  # 1 callback on_commit enregistré


# --- Upload : erreurs de validation ---

def test_upload_missing_file(authed_client):
    client, _ = authed_client
    res = client.post(reverse("imports_upload"), {}, format="multipart")
    assert res.status_code == 400
    assert "Aucun fichier" in str(res.data)


def test_upload_wrong_filename(authed_client):
    client, _ = authed_client
    f = SimpleUploadedFile(
        "monfichier.txt", _valid_fab_bytes(), content_type="text/plain"
    )
    res = client.post(reverse("imports_upload"), {"file": f}, format="multipart")
    assert res.status_code == 400
    assert "fabAAAAMMJJ" in str(res.data)


@override_settings(FAB_REQUIRE_TODAY_DATE=True)
def test_upload_yesterday_rejected_strict_mode(authed_client):
    client, _ = authed_client
    yesterday = timezone.localdate() - timedelta(days=1)
    name = f"fab{yesterday.year:04d}{yesterday.month:02d}{yesterday.day:02d}.txt"
    f = SimpleUploadedFile(name, _valid_fab_bytes(), content_type="text/plain")
    res = client.post(reverse("imports_upload"), {"file": f}, format="multipart")
    assert res.status_code == 400
    assert "jour" in str(res.data)


@override_settings(FAB_REQUIRE_TODAY_DATE=False)
def test_upload_yesterday_accepted_when_strict_disabled(
    authed_client, mute_minio_and_celery
):
    client, _ = authed_client
    yesterday = timezone.localdate() - timedelta(days=1)
    name = f"fab{yesterday.year:04d}{yesterday.month:02d}{yesterday.day:02d}.txt"
    f = SimpleUploadedFile(name, _valid_fab_bytes(), content_type="text/plain")
    res = client.post(reverse("imports_upload"), {"file": f}, format="multipart")
    assert res.status_code == 202, res.content


def test_upload_corrupted_content_rejected(authed_client):
    """Cas de l'attaque : nom valide mais contenu non-FAB → doit être rejeté
    AVANT d'arriver dans Celery."""
    client, _ = authed_client
    fake = b"<html>looks like a webpage, not a FAB</html>"
    f = SimpleUploadedFile(_today_filename(), fake, content_type="text/plain")
    res = client.post(reverse("imports_upload"), {"file": f}, format="multipart")
    assert res.status_code == 400
    assert "FAB valide" in str(res.data)


def test_upload_empty_file_rejected(authed_client):
    client, _ = authed_client
    f = SimpleUploadedFile(_today_filename(), b"", content_type="text/plain")
    res = client.post(reverse("imports_upload"), {"file": f}, format="multipart")
    assert res.status_code == 400


# --- Liste / détail ---

def test_imports_list_returns_paginated(authed_client):
    client, user = authed_client
    from apps.imports.models import FabImport
    from datetime import date

    FabImport.objects.create(
        minio_key="k1", file_date=date(2026, 5, 1), uploaded_by=user
    )
    FabImport.objects.create(
        minio_key="k2", file_date=date(2026, 5, 2), uploaded_by=user
    )

    res = client.get(reverse("imports_list"))
    assert res.status_code == 200
    assert res.data["count"] == 2
    assert len(res.data["results"]) == 2


def test_imports_detail_includes_error_message(authed_client):
    client, user = authed_client
    from apps.imports.models import FabImport
    from datetime import date

    imp = FabImport.objects.create(
        minio_key="k1",
        file_date=date(2026, 5, 1),
        uploaded_by=user,
        status=FabImport.Status.FAILED,
        error_message="Boom : KeyError 'date_facture'",
    )
    res = client.get(reverse("imports_detail", args=[imp.id]))
    assert res.status_code == 200
    assert res.data["status"] == "failed"
    assert "KeyError" in res.data["error_message"]
