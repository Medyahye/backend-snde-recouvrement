"""Tests des validateurs upload (apps/api/validators.py)."""
import io
from datetime import date, timedelta

import pytest
from django.test import override_settings
from django.utils import timezone
from rest_framework.exceptions import ValidationError

from apps.api.validators import (
    validate_fab_content,
    validate_fab_date_is_today,
    validate_fab_filename,
    validate_fab_size,
)


# --- validate_fab_filename ---

def test_filename_valid():
    d = validate_fab_filename("fab20260412.txt")
    assert d == date(2026, 4, 12)


def test_filename_wrong_extension():
    with pytest.raises(ValidationError):
        validate_fab_filename("fab20260412.csv")


def test_filename_wrong_prefix():
    with pytest.raises(ValidationError):
        validate_fab_filename("fichier20260412.txt")


def test_filename_invalid_date():
    with pytest.raises(ValidationError):
        validate_fab_filename("fab20261332.txt")  # mois 13, jour 32


def test_filename_empty():
    with pytest.raises(ValidationError):
        validate_fab_filename("")


# --- validate_fab_date_is_today ---

@override_settings(FAB_REQUIRE_TODAY_DATE=True)
def test_date_today_passes():
    today = timezone.localdate()
    validate_fab_date_is_today(today)


@override_settings(FAB_REQUIRE_TODAY_DATE=True)
def test_date_yesterday_fails_when_strict():
    yesterday = timezone.localdate() - timedelta(days=1)
    with pytest.raises(ValidationError):
        validate_fab_date_is_today(yesterday)


@override_settings(FAB_REQUIRE_TODAY_DATE=False)
def test_date_yesterday_passes_when_disabled():
    yesterday = timezone.localdate() - timedelta(days=1)
    validate_fab_date_is_today(yesterday)  # ne lève rien


# --- validate_fab_content ---

def _make_valid_fab_line() -> str:
    return "$$".join(["1"] + [""] * 33 + ["1"])  # 35 colonnes minimum


def test_content_with_valid_lines_passes():
    content = ("\n".join([_make_valid_fab_line()] * 5)).encode("utf-8")
    validate_fab_content(io.BytesIO(content))


def test_content_empty_fails():
    with pytest.raises(ValidationError, match="vide"):
        validate_fab_content(io.BytesIO(b""))


def test_content_only_whitespace_fails():
    with pytest.raises(ValidationError, match="vide"):
        validate_fab_content(io.BytesIO(b"   \n\n   \n"))


def test_content_no_dollar_separator_fails():
    fake = b"col1,col2,col3\nrow1,a,b\nrow2,c,d\n"
    with pytest.raises(ValidationError, match="FAB valide"):
        validate_fab_content(io.BytesIO(fake))


def test_content_too_few_columns_fails():
    line = "$$".join(["x"] * 10)  # < 35
    fake = ("\n".join([line] * 3)).encode("utf-8")
    with pytest.raises(ValidationError, match="FAB valide"):
        validate_fab_content(io.BytesIO(fake))


def test_content_resets_cursor_after_validation():
    content = (_make_valid_fab_line() + "\n").encode("utf-8")
    f = io.BytesIO(content)
    f.seek(50)  # curseur déplacé
    validate_fab_content(f)
    assert f.tell() == 0  # remis à 0


# --- validate_fab_size ---

def test_size_under_limit():
    validate_fab_size(1024)


def test_size_over_limit():
    """Indépendant de la valeur configurée : on prend (limite + 1)."""
    from django.conf import settings

    huge = settings.FAB_MAX_UPLOAD_SIZE + 1
    with pytest.raises(ValidationError, match="Mo"):
        validate_fab_size(huge)
