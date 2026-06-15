"""Dérivation de l'état du cycle de relance — V2 Axe B.

Logique déterministe basée sur :
- `code_relance` (le code SNDE actuel)
- `date_facture` (quand la dernière facture a été émise)
- `date_dernier_paiement` (quand le client a payé pour la dernière fois)
- `today` (date de référence = `file_date` de l'import)

Permet de distinguer un "code 0 normal" d'un "code 0 en grâce J+8/J+48h", et
de détecter les anomalies (clients qui devraient être en code 1 mais restent
en code 4 ou 0 au-delà de 10 jours d'impayé).
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from typing import Optional


@dataclass
class ClientSnapshot:
    """Snapshot minimal d'un client pour la classification d'état."""

    code_relance: str
    date_facture: Optional[date]
    date_dernier_paiement: Optional[date]
    solde: Decimal


# Constantes du cycle SNDE (cf. Note du tuteur)
GRACE_J8_DURATION = 8   # 8 jours après émission de la facture (code 4)
GRACE_J48H_END = 10     # 8 jours + 48h = 10 jours total
ANOMALY_THRESHOLD = 12  # Au-delà : SNDE n'a pas coupé alors qu'il devrait


def derive_relance_state(snapshot: ClientSnapshot, today: date) -> str:
    """Dérive l'état du cycle de relance d'un client.

    Retourne une valeur de `Client.RelanceState`.
    """
    # Import différé pour éviter la dépendance circulaire
    from apps.clients.models import Client

    S = Client.RelanceState

    # Pas de facture renseignée → on ne sait pas
    if snapshot.date_facture is None:
        return S.UNKNOWN

    # Le client a payé sa facture courante OU son solde est nul → normal
    if snapshot.solde is not None and snapshot.solde <= 0:
        return S.NORMAL
    if (
        snapshot.date_dernier_paiement is not None
        and snapshot.date_dernier_paiement >= snapshot.date_facture
    ):
        return S.NORMAL

    jours_impaye = (today - snapshot.date_facture).days
    code = (snapshot.code_relance or "").strip()

    # États directs déterminés par le code actuel
    if code == "1":
        # Le détail "fast track" vs "normal cycle" est calculé ailleurs
        # (par compute_movements_for_import qui regarde l'historique).
        return S.CUT_OFF
    if code == "4":
        # Si on est largement après les 8j sans avoir avancé → anomalie
        if jours_impaye > ANOMALY_THRESHOLD:
            return S.ANOMALY_OVERDUE
        return S.SMS_J8
    if code == "2":
        if jours_impaye > ANOMALY_THRESHOLD:
            return S.ANOMALY_OVERDUE
        return S.SMS_J48H

    # code == "0" : utiliser la temporalité pour deviner où il est dans le cycle
    if code == "0":
        if jours_impaye < 0:
            # Facture future (rare, cas d'erreur de date)
            return S.NORMAL
        if jours_impaye == 0:
            # Le jour même de la facture
            return S.NORMAL
        if jours_impaye <= GRACE_J8_DURATION - 1:
            # Jours 1 à 7 : SMS code 4 a été envoyé, en grâce
            return S.GRACE_J8
        if jours_impaye <= GRACE_J48H_END:
            # Jours 8 à 10 : SMS code 2 a été envoyé, en grâce 48h
            return S.GRACE_J48H
        # > 10 jours d'impayé sans coupure → SNDE n'a pas appliqué
        return S.ANOMALY_OVERDUE

    # Autres codes (3 ou inconnus)
    return S.UNKNOWN
