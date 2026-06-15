"""Calculs pour les endpoints de recouvrement.

Centralisé ici pour rester pur (testable sans HTTP).
"""
from __future__ import annotations

from datetime import date
from decimal import Decimal

from django.db.models import Case, Count, DecimalField, F, Q, Sum, Value, When

from apps.recouvrement.models import ClientMovement

# Types comptés comme recouvrement (paiement effectif)
PAYMENT_TYPES = [
    ClientMovement.Type.PAYMENT_CERTAIN,
    ClientMovement.Type.PAYMENT_LIKELY,
]


def _paid_amount_expr():
    """SQL expression: max(0, delta_solde) — conservative payment amount.

    Pour un paiement détecté via date_paiement, le delta_solde peut être négatif
    (cas paiement + nouvelle facturation > paiement). On compte 0 dans ce cas
    pour éviter de polluer le KPI avec des montants "négatifs".
    """
    return Case(
        When(delta_solde__gt=0, then=F("delta_solde")),
        default=Value(Decimal("0")),
        output_field=DecimalField(max_digits=16, decimal_places=2),
    )


def daily_recovery(date_to: date) -> dict:
    """Recouvré sur une date_to spécifique (= file_date de l'import courant)."""
    qs = ClientMovement.objects.filter(date_to=date_to)
    paid_amount = _paid_amount_expr()

    agg = qs.aggregate(
        total_paye=Sum(paid_amount, filter=Q(type__in=PAYMENT_TYPES)),
        nb_payeurs=Count("id", filter=Q(type__in=PAYMENT_TYPES)),
        total_certain=Sum(
            paid_amount,
            filter=Q(type=ClientMovement.Type.PAYMENT_CERTAIN),
        ),
        total_likely=Sum(
            paid_amount,
            filter=Q(type=ClientMovement.Type.PAYMENT_LIKELY),
        ),
        nb_ajustements=Count(
            "id", filter=Q(type=ClientMovement.Type.ADJUSTMENT)
        ),
        nb_sorties=Count(
            "id", filter=Q(type=ClientMovement.Type.DEPARTURE)
        ),
        nouvelle_facturation=Sum(
            "delta_solde", filter=Q(type=ClientMovement.Type.NEW_BILLING)
        ),
    )

    return {
        "date": date_to.isoformat(),
        "total_paye": agg["total_paye"] or Decimal("0"),
        "nb_payeurs": agg["nb_payeurs"] or 0,
        "decomposition": {
            "certain": agg["total_certain"] or Decimal("0"),
            "probable": agg["total_likely"] or Decimal("0"),
        },
        "anomalies": {
            "nb_ajustements": agg["nb_ajustements"] or 0,
            "nb_sorties_suspectes": agg["nb_sorties"] or 0,
        },
        "nouvelle_facturation": -(agg["nouvelle_facturation"] or Decimal("0")),
    }


def period_recovery(start: date, end: date) -> dict:
    """Recouvré entre `start` et `end` (inclus)."""
    paid_amount = _paid_amount_expr()
    qs = ClientMovement.objects.filter(
        date_to__gte=start, date_to__lte=end, type__in=PAYMENT_TYPES
    )

    agg = qs.aggregate(
        total_paye=Sum(paid_amount),
        nb_payeurs=Count("id"),
    )

    # Évolution par jour
    by_day = (
        ClientMovement.objects.filter(
            date_to__gte=start, date_to__lte=end, type__in=PAYMENT_TYPES
        )
        .values("date_to")
        .annotate(total=Sum(paid_amount), nb=Count("id"))
        .order_by("date_to")
    )

    return {
        "start": start.isoformat(),
        "end": end.isoformat(),
        "total_paye": agg["total_paye"] or Decimal("0"),
        "nb_payeurs": agg["nb_payeurs"] or 0,
        "par_jour": [
            {
                "date": row["date_to"].isoformat(),
                "total": row["total"],
                "nb": row["nb"],
            }
            for row in by_day
        ],
    }


def recovery_by_centre(date_to: date) -> list[dict]:
    """Répartition du recouvrement par centre pour une date."""
    paid_amount = _paid_amount_expr()
    qs = (
        ClientMovement.objects.filter(
            date_to=date_to, type__in=PAYMENT_TYPES
        )
        .values("centre_nom")
        .annotate(total=Sum(paid_amount), nb=Count("id"))
        .order_by("-total")
    )
    return [
        {
            "centre": row["centre_nom"] or "INCONNU",
            "total_paye": row["total"],
            "nb_payeurs": row["nb"],
        }
        for row in qs
    ]


def recovery_by_zone(date_to: date, limit: int = 50) -> list[dict]:
    """Top zones par montant recouvré pour une date."""
    paid_amount = _paid_amount_expr()
    qs = (
        ClientMovement.objects.filter(
            date_to=date_to, type__in=PAYMENT_TYPES
        )
        .values("zone", "centre_nom")
        .annotate(total=Sum(paid_amount), nb=Count("id"))
        .order_by("-total")[:limit]
    )
    return [
        {
            "zone": row["zone"] or "INCONNUE",
            "centre": row["centre_nom"] or "INCONNU",
            "total_paye": row["total"],
            "nb_payeurs": row["nb"],
        }
        for row in qs
    ]
