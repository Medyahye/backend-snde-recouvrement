"""Validateurs pour l'upload d'un fichier FAB.

3 niveaux complémentaires :
1. **Filename** (filtre superficiel) — pattern `fab\\d{8}\\.txt`.
2. **Date du fichier** — doit être celle du jour (configurable, cf. FAB_REQUIRE_TODAY_DATE).
3. **Contenu** (sécurité réelle) — au moins une ligne $$-séparée avec ≥35 colonnes
   sur les N premières lignes non vides.
"""
from __future__ import annotations

import re
from datetime import date, datetime

from django.conf import settings
from django.utils import timezone
from rest_framework.exceptions import ValidationError


def validate_fab_filename(filename: str) -> date:
    """Valide le nom et retourne la date extraite.

    Format attendu : `fabAAAAMMJJ.txt` (en minuscules).
    Lève ValidationError sinon.
    """
    if not filename:
        raise ValidationError({"file": "Nom de fichier manquant."})

    match = re.match(settings.FAB_FILENAME_REGEX, filename)
    if not match:
        raise ValidationError(
            {
                "file": (
                    "Le nom doit suivre le format 'fabAAAAMMJJ.txt' "
                    f"(reçu : '{filename}')."
                )
            }
        )

    yyyy, mm, dd = match.groups()
    try:
        return date(int(yyyy), int(mm), int(dd))
    except ValueError as exc:
        raise ValidationError({"file": f"Date invalide dans le nom : {exc}."})


def validate_fab_date_is_today(file_date: date) -> None:
    """Vérifie que la date du fichier correspond à celle du jour.

    Si `settings.FAB_REQUIRE_TODAY_DATE` est False, ne fait rien (utile en dev/test).
    """
    if not settings.FAB_REQUIRE_TODAY_DATE:
        return

    today = timezone.localdate()
    if file_date != today:
        raise ValidationError(
            {
                "file": (
                    f"La date du fichier ({file_date.isoformat()}) ne correspond "
                    f"pas à celle du jour ({today.isoformat()}). "
                    "Vérifie que tu uploades bien le FAB du jour."
                )
            }
        )


def validate_fab_content(file_obj) -> None:
    """Vérifie que le fichier contient au moins une ligne FAB structurellement valide.

    Lit les `FAB_CONTENT_VALIDATION_SAMPLE_LINES` premières lignes non vides
    et exige qu'au moins une soit $$-séparée avec ≥35 colonnes.
    Remet le curseur à 0 à la fin (le caller pourra ré-uploader).
    """
    sample_size = settings.FAB_CONTENT_VALIDATION_SAMPLE_LINES
    file_obj.seek(0)

    valid_lines = 0
    checked = 0
    for raw_line in file_obj:
        if checked >= sample_size:
            break
        try:
            line = raw_line.decode("utf-8", errors="ignore").strip()
        except (AttributeError, UnicodeDecodeError):
            line = str(raw_line).strip()
        if not line:
            continue
        checked += 1
        parts = line.split("$$")
        # Accepte les 2 formats SNDE : 33 cols (sans GPS) ou 35 cols (avec GPS)
        if len(parts) >= 33:
            valid_lines += 1

    file_obj.seek(0)

    if checked == 0:
        raise ValidationError({"file": "Le fichier est vide ou illisible."})
    if valid_lines == 0:
        raise ValidationError(
            {
                "file": (
                    "Le fichier ne semble pas être un FAB valide : aucune ligne "
                    f"$$-séparée avec au moins 33 colonnes trouvée parmi les "
                    f"{checked} premières lignes inspectées."
                )
            }
        )


def validate_fab_size(size: int) -> None:
    """Vérifie que la taille du fichier est dans la limite autorisée."""
    if size > settings.FAB_MAX_UPLOAD_SIZE:
        max_mb = settings.FAB_MAX_UPLOAD_SIZE // (1024 * 1024)
        actual_mb = size // (1024 * 1024)
        raise ValidationError(
            {
                "file": (
                    f"Le fichier fait {actual_mb} Mo, la limite est {max_mb} Mo."
                )
            }
        )
