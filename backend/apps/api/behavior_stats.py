"""Métriques comportementales (V2 Axe C).

Calculs analytiques avancés sur le cycle de relance et le comportement de paiement :
- Pipeline de recouvrement : argent en attente de paiement (clients en grâce / SMS)
- Taux de chute en code 1 : % des clients SMS J+8 qui finissent en coupure
- Vitesse de recouvrement : jours moyens entre facture et paiement
- Taux de réaction au SMS : % de clients qui paient après un SMS J+8 ou J+48h
- Distribution temporelle : histogramme des jours d'impayé au moment du paiement
"""
from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal

from django.db.models import Count, Q, Sum

from apps.clients.models import Client
from apps.imports.models import FabImport
from apps.recouvrement.models import ClientMovement

# Types ClientMovement comptés comme paiement effectif
PAYMENT_TYPES = [
    ClientMovement.Type.PAYMENT_CERTAIN,
    ClientMovement.Type.PAYMENT_LIKELY,
]

# États relance_state considérés comme "dans le pipeline" (argent en attente)
PIPELINE_STATES = [
    Client.RelanceState.SMS_J8,
    Client.RelanceState.GRACE_J8,
    Client.RelanceState.SMS_J48H,
    Client.RelanceState.GRACE_J48H,
]


# --------------------------------------------------------------------------- #
# C.1 — Pipeline de recouvrement
# --------------------------------------------------------------------------- #


def pipeline_de_recouvrement(import_id: int) -> dict:
    """Argent en attente dans les phases pré-coupure (J+8 et J+48h).

    Représente le revenu attendu "sans effort" si la SNDE laisse le cycle SMS
    se dérouler normalement. Décomposé par état pour identifier où en est
    chaque tranche.
    """
    qs = Client.objects.filter(
        import_ref_id=import_id, relance_state__in=PIPELINE_STATES
    )

    breakdown = {}
    for state in PIPELINE_STATES:
        agg = qs.filter(relance_state=state).aggregate(
            total=Sum("solde"),
            nb=Count("id"),
        )
        breakdown[state] = {
            "total": agg["total"] or Decimal("0"),
            "nb_clients": agg["nb"] or 0,
        }

    global_agg = qs.aggregate(total=Sum("solde"), nb=Count("id"))

    return {
        "import_id": import_id,
        "total_pipeline": global_agg["total"] or Decimal("0"),
        "nb_clients": global_agg["nb"] or 0,
        "breakdown": breakdown,
    }


# --------------------------------------------------------------------------- #
# C.2 — Taux de chute en code 1
# --------------------------------------------------------------------------- #


def taux_de_chute(start: date, end: date) -> dict:
    """% des clients qui sont entrés en cycle (code 4) et finissent en code 1.

    Approximation : on regarde sur la fenêtre `[start, end + 10 jours]` pour
    capturer les chutes survenues juste après le cycle SMS.
    """
    # Clients entrés en cycle (code 4) dans la période
    refs_entres = set(
        Client.objects.filter(
            code_relance="4",
            import_ref__file_date__gte=start,
            import_ref__file_date__lte=end,
        )
        .values_list("reference_abonnement", flat=True)
        .distinct()
    )

    if not refs_entres:
        return {
            "start": start.isoformat(),
            "end": end.isoformat(),
            "taux": None,
            "nb_entres_cycle": 0,
            "nb_chutes_code_1": 0,
        }

    # Parmi eux, ceux qui ont atteint code 1 (avec marge de 10j après end)
    refs_chutes = set(
        Client.objects.filter(
            code_relance="1",
            reference_abonnement__in=refs_entres,
            import_ref__file_date__gte=start,
            import_ref__file_date__lte=end + timedelta(days=10),
        )
        .values_list("reference_abonnement", flat=True)
        .distinct()
    )

    return {
        "start": start.isoformat(),
        "end": end.isoformat(),
        "taux": round(len(refs_chutes) / len(refs_entres) * 100, 1),
        "nb_entres_cycle": len(refs_entres),
        "nb_chutes_code_1": len(refs_chutes),
    }


# --------------------------------------------------------------------------- #
# C.3 — Vitesse de recouvrement + C.5 distribution temporelle
# --------------------------------------------------------------------------- #


def vitesse_et_distribution(start: date, end: date) -> dict:
    """Pour les paiements détectés dans la période :
    - jours moyens entre date_facture et date_paiement (= vitesse)
    - histogramme jours_impaye au moment du paiement (= distribution).
    """
    movements = (
        ClientMovement.objects.filter(
            date_to__gte=start,
            date_to__lte=end,
            type__in=PAYMENT_TYPES,
            date_paiement_after__isnull=False,
        )
        .select_related("import_to")
    )

    # Pour chaque paiement, on a besoin du date_facture du client à ce moment-là.
    # On fait une lookup en bulk pour éviter N+1.
    keys = [(m.import_to_id, m.reference_abonnement) for m in movements]
    if not keys:
        return _empty_vitesse_distribution(start, end)

    # Map (import_id, ref) → date_facture
    refs_by_import: dict[int, set[str]] = {}
    for imp_id, ref in keys:
        refs_by_import.setdefault(imp_id, set()).add(ref)

    date_facture_map: dict[tuple[int, str], date] = {}
    for imp_id, refs in refs_by_import.items():
        clients = Client.objects.filter(
            import_ref_id=imp_id, reference_abonnement__in=refs
        ).only("reference_abonnement", "date_facture")
        for c in clients:
            if c.date_facture:
                date_facture_map[(imp_id, c.reference_abonnement)] = c.date_facture

    # Pour chaque mouvement : calcul du delai
    days_to_pay = []
    for m in movements:
        df = date_facture_map.get((m.import_to_id, m.reference_abonnement))
        if df is None:
            continue
        delta = (m.date_paiement_after - df).days
        if delta >= 0:
            days_to_pay.append(delta)

    if not days_to_pay:
        return _empty_vitesse_distribution(start, end)

    mean = sum(days_to_pay) / len(days_to_pay)
    sorted_days = sorted(days_to_pay)
    median = sorted_days[len(sorted_days) // 2]

    # Histogramme : buckets de 1 jour, range 0-30, le reste va dans "30+"
    HIST_BUCKETS = 30
    histogram = [0] * (HIST_BUCKETS + 1)  # 0 → 30
    over = 0
    for d in days_to_pay:
        if d <= HIST_BUCKETS:
            histogram[d] += 1
        else:
            over += 1

    return {
        "start": start.isoformat(),
        "end": end.isoformat(),
        "vitesse_moyenne_jours": round(mean, 1),
        "vitesse_mediane_jours": median,
        "nb_paiements": len(days_to_pay),
        "min_jours": sorted_days[0],
        "max_jours": sorted_days[-1],
        "histogram": [
            {"jours": i, "nb": histogram[i]} for i in range(HIST_BUCKETS + 1)
        ],
        "histogram_over": {"jours": f"> {HIST_BUCKETS}", "nb": over},
    }


def _empty_vitesse_distribution(start: date, end: date) -> dict:
    return {
        "start": start.isoformat(),
        "end": end.isoformat(),
        "vitesse_moyenne_jours": None,
        "vitesse_mediane_jours": None,
        "nb_paiements": 0,
        "min_jours": None,
        "max_jours": None,
        "histogram": [],
        "histogram_over": {"jours": "> 30", "nb": 0},
    }


# --------------------------------------------------------------------------- #
# C.4 — Taux de réaction au SMS J+8 et J+48h
# --------------------------------------------------------------------------- #


def taux_de_reaction(start: date, end: date, niveau: str = "j8") -> dict:
    """% des clients qui ont reçu un SMS (J+8 ou J+48h) et ont payé avant
    d'atteindre le code suivant.

    niveau = 'j8'  → SMS code 4, on regarde s'ils ont payé avant code 2
    niveau = 'j48h' → SMS code 2, on regarde s'ils ont payé avant code 1
    """
    if niveau == "j8":
        sms_code = "4"
        next_code = "2"
    elif niveau == "j48h":
        sms_code = "2"
        next_code = "1"
    else:
        raise ValueError("niveau doit valoir 'j8' ou 'j48h'")

    # Clients ayant reçu ce SMS dans la période
    refs_sms = set(
        Client.objects.filter(
            code_relance=sms_code,
            import_ref__file_date__gte=start,
            import_ref__file_date__lte=end,
        )
        .values_list("reference_abonnement", flat=True)
        .distinct()
    )

    if not refs_sms:
        return {
            "niveau": niveau,
            "taux": None,
            "nb_sms_envoyes": 0,
            "nb_reactions": 0,
        }

    # Parmi eux, lesquels ont payé (paiement détecté dans la fenêtre + 10j)
    refs_payes = set(
        ClientMovement.objects.filter(
            reference_abonnement__in=refs_sms,
            date_to__gte=start,
            date_to__lte=end + timedelta(days=10),
            type__in=PAYMENT_TYPES,
        )
        .values_list("reference_abonnement", flat=True)
        .distinct()
    )

    # Parmi eux, lesquels ont atteint le code suivant (= ont ignoré le SMS)
    refs_next = set(
        Client.objects.filter(
            code_relance=next_code,
            reference_abonnement__in=refs_sms,
            import_ref__file_date__gte=start,
            import_ref__file_date__lte=end + timedelta(days=10),
        )
        .values_list("reference_abonnement", flat=True)
        .distinct()
    )

    # Réaction = a payé ET n'a pas atteint le code suivant
    refs_reactions = refs_payes - refs_next

    return {
        "niveau": niveau,
        "taux": round(len(refs_reactions) / len(refs_sms) * 100, 1),
        "nb_sms_envoyes": len(refs_sms),
        "nb_reactions": len(refs_reactions),
        "nb_chutes_apres_sms": len(refs_next),
    }


# --------------------------------------------------------------------------- #
# Helper agrégeant tous les KPIs (pour 1 seul endpoint)
# --------------------------------------------------------------------------- #


def all_behavior_metrics(
    import_id: int, start: date, end: date
) -> dict:
    """Agrège toutes les métriques comportementales en une seule réponse."""
    return {
        "pipeline": pipeline_de_recouvrement(import_id),
        "taux_chute": taux_de_chute(start, end),
        "vitesse_distribution": vitesse_et_distribution(start, end),
        "reaction_sms_j8": taux_de_reaction(start, end, "j8"),
        "reaction_sms_j48h": taux_de_reaction(start, end, "j48h"),
    }
