"""Tests des endpoints users CRUD (étape 5.3 — admin only)."""
from django.contrib.auth import get_user_model
from django.urls import reverse
from rest_framework.test import APIClient

User = get_user_model()


def _admin_client(db):
    admin = User.objects.create_user(
        username="admin@snde.local",
        password="adminpass",
        role="admin",
        is_staff=True,
        is_superuser=True,
    )
    c = APIClient()
    c.force_authenticate(user=admin)
    return c, admin


def _gestionnaire_client(db):
    user = User.objects.create_user(
        username="gest@snde.local", password="x", role="gestionnaire"
    )
    c = APIClient()
    c.force_authenticate(user=user)
    return c


# --- Permissions ---

def test_list_users_requires_admin(db):
    c = _gestionnaire_client(db)
    res = c.get(reverse("users_list"))
    assert res.status_code == 403


def test_list_users_works_for_admin(db):
    c, _ = _admin_client(db)
    res = c.get(reverse("users_list"))
    assert res.status_code == 200
    assert isinstance(res.data, list)
    assert len(res.data) >= 1


def test_unauthenticated_returns_401(db):
    c = APIClient()
    res = c.get(reverse("users_list"))
    assert res.status_code == 401


# --- Création ---

def test_create_user_with_password(db):
    c, _ = _admin_client(db)
    res = c.post(
        reverse("users_list"),
        {
            "username": "newuser@snde.local",
            "email": "newuser@snde.local",
            "password": "secret123",
            "role": "terrain",
        },
        format="json",
    )
    assert res.status_code == 201, res.data
    assert res.data["role"] == "terrain"
    assert res.data["username"] == "newuser@snde.local"
    assert "password" not in res.data  # write_only

    new_user = User.objects.get(username="newuser@snde.local")
    assert new_user.check_password("secret123")


def test_create_user_invalid_role(db):
    c, _ = _admin_client(db)
    res = c.post(
        reverse("users_list"),
        {
            "username": "x@snde.local",
            "email": "x@snde.local",
            "password": "secret",
            "role": "INEXISTANT",
        },
        format="json",
    )
    assert res.status_code == 400


# --- Update ---

def test_update_user_role(db):
    c, _ = _admin_client(db)
    user = User.objects.create_user(
        username="update@snde.local", password="x", role="terrain"
    )
    res = c.patch(
        reverse("users_detail", args=[user.id]),
        {"role": "gestionnaire"},
        format="json",
    )
    assert res.status_code == 200
    user.refresh_from_db()
    assert user.role == "gestionnaire"


def test_update_user_password(db):
    c, _ = _admin_client(db)
    user = User.objects.create_user(
        username="pw@snde.local", password="oldpass", role="terrain"
    )
    res = c.patch(
        reverse("users_detail", args=[user.id]),
        {"password": "newsecret"},
        format="json",
    )
    assert res.status_code == 200
    user.refresh_from_db()
    assert user.check_password("newsecret")
    assert not user.check_password("oldpass")


# --- Delete (soft : is_active=False) ---

def test_delete_disables_user(db):
    c, _ = _admin_client(db)
    user = User.objects.create_user(
        username="del@snde.local", password="x", role="terrain"
    )
    assert user.is_active is True

    res = c.delete(reverse("users_detail", args=[user.id]))
    assert res.status_code == 204

    user.refresh_from_db()
    assert user.is_active is False
    # L'user existe toujours physiquement (préserve les FK uploaded_by)
    assert User.objects.filter(id=user.id).exists()
