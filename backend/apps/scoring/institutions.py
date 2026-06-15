"""Détection et agrégation des institutions publiques.

Beaucoup de clients SNDE sont en réalité des installations d'institutions
publiques (ONSER, SONADER, SNIM, etc.) qui possèdent des dizaines de comptes
séparés (forages, écoles, casernes, etc.).

Pour le chef SNDE, voir 73 refs ONSER éparpillées n'a aucun sens — il a besoin
d'une vue agrégée "ONSER doit X millions, négociation au siège".

Ce module détecte les institutions par pattern sur le nom client et agrège
les profils comportementaux. Pure lecture (pas de migration DB).
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Iterable

from django.db.models import Avg, Count, Q, Sum

from apps.recouvrement.models import ClientBehavior


@dataclass
class InstitutionDef:
    slug: str
    name: str
    full_name: str
    patterns: list[str]  # regex patterns (case-insensitive)


# Liste des institutions publiques détectables.
# Les patterns sont volontairement larges pour capturer les variantes
# orthographiques courantes dans le FAB SNDE.
INSTITUTIONS: list[InstitutionDef] = [
    InstitutionDef(
        slug="onser",
        name="ONSER",
        full_name="Office National des Services d'Eau Rurale",
        patterns=[r"\yONSER\y"],
    ),
    InstitutionDef(
        slug="sonader",
        name="SONADER",
        full_name="Société Nationale de Développement Rural",
        patterns=[r"\ySONADER\y"],
    ),
    InstitutionDef(
        slug="snim",
        name="SNIM",
        full_name="Société Nationale Industrielle et Minière",
        patterns=[r"\ySNIM\y"],
    ),
    InstitutionDef(
        slug="attm",
        name="ATTM",
        full_name="Agence des Travaux et Maintenance",
        patterns=[r"\yATTM\y", r"A\.?T\.?T\.?M\y"],
    ),
    InstitutionDef(
        slug="ecoles",
        name="Écoles",
        full_name="Établissements scolaires (BEP, écoles, lycées)",
        patterns=[r"\yBEP\y", r"\yECOLE\y", r"\yLYCEE\y", r"\yCOLLEGE\y"],
    ),
    InstitutionDef(
        slug="gendarmerie",
        name="Gendarmerie",
        full_name="Brigades de Gendarmerie",
        patterns=[r"\yGENDARME\y", r"\yGENDARMERIE\y", r"\yBRIGADE\y"],
    ),
    InstitutionDef(
        slug="universites",
        name="Universités",
        full_name="Universités et Facultés",
        patterns=[r"\yFAC\s", r"\yFACULTE\y", r"\yUNIVERSITE\y"],
    ),
    InstitutionDef(
        slug="mauriconserv",
        name="MAURICONSERV",
        full_name="MAURICONSERV (incluant variantes MOURICONSERV)",
        patterns=[r"MAU?RICONSERV", r"MOURICONSERV"],
    ),
    InstitutionDef(
        slug="stam",
        name="STAM",
        full_name="STAM Mauritanie",
        patterns=[r"\ySTAM\y"],
    ),
    InstitutionDef(
        slug="ministere",
        name="Ministères",
        full_name="Ministères et administrations centrales",
        patterns=[r"\yMINISTERE\y", r"\yMINISTRE\y"],
    ),
    InstitutionDef(
        slug="mairie",
        name="Mairies",
        full_name="Mairies et collectivités locales",
        patterns=[r"\yMAIRIE\y", r"\yCOMMUNE\y"],
    ),
    InstitutionDef(
        slug="hopital",
        name="Hôpitaux",
        full_name="Hôpitaux et centres de santé",
        patterns=[r"\yHOPITAL\y", r"\yCENTRE\s+DE\s+SANTE\y", r"\yCLINIQUE\y"],
    ),
    InstitutionDef(
        slug="mosquee",
        name="Mosquées",
        full_name="Mosquées et lieux de culte",
        patterns=[r"\yMOSQUEE\y", r"\yMASJID\y"],
    ),
    InstitutionDef(
        slug="armee",
        name="Armée",
        full_name="Armée nationale",
        patterns=[r"\yARMEE\y", r"\yMILITAIRE\y", r"\yCASERNE\y"],
    ),
    InstitutionDef(
        slug="police",
        name="Police",
        full_name="Commissariats de police",
        patterns=[r"\yPOLICE\y", r"\yCOMMISSARIAT\y"],
    ),
]


def _build_q_for_institution(inst: InstitutionDef) -> Q:
    """Construit un filtre Q Django qui matche tous les patterns d'une institution.

    Utilise __iregex pour la recherche insensible à la casse via PostgreSQL.
    Combine les patterns avec OR.
    """
    q = Q()
    for pattern in inst.patterns:
        q |= Q(nom_client__iregex=pattern)
    return q


def list_institutions_summary() -> list[dict]:
    """Liste agrégée de toutes les institutions détectées.

    Pour chaque institution, retourne :
      - slug, name, full_name
      - nb_installations (= nb refs)
      - total_dette (somme des avg_solde)
      - total_paye (somme des total_paid)
      - taux_recouvrement (= total_paye / (total_dette + total_paye) × 100)
      - nb_centres distincts
      - répartition par catégorie (bon / moyen / mauvais)
    """
    result = []
    for inst in INSTITUTIONS:
        q = _build_q_for_institution(inst)
        qs = ClientBehavior.objects.filter(q)
        nb = qs.count()
        if nb == 0:
            continue  # On n'inclut pas les institutions absentes

        agg = qs.aggregate(
            total_dette=Sum("avg_solde"),
            total_paye=Sum("total_paid"),
            nb_centres=Count("last_centre_nom", distinct=True),
        )
        total_dette = float(agg["total_dette"] or 0)
        total_paye = float(agg["total_paye"] or 0)
        # Recouvrement = % de la dette honorée (estimation grossière)
        denom = total_dette + total_paye
        taux_recouvrement = (total_paye / denom * 100) if denom > 0 else 0.0

        # Distribution par catégorie
        cat_counts = {"bon": 0, "moyen": 0, "mauvais": 0}
        for row in qs.values("category").annotate(n=Count("reference_abonnement")):
            cat_counts[row["category"]] = row["n"]

        result.append(
            {
                "slug": inst.slug,
                "name": inst.name,
                "full_name": inst.full_name,
                "nb_installations": nb,
                "total_dette": total_dette,
                "total_paye": total_paye,
                "taux_recouvrement": round(taux_recouvrement, 1),
                "nb_centres": agg["nb_centres"],
                "nb_bon": cat_counts["bon"],
                "nb_moyen": cat_counts["moyen"],
                "nb_mauvais": cat_counts["mauvais"],
            }
        )

    # Tri par dette décroissante (qui dépend le plus à SNDE)
    result.sort(key=lambda i: i["total_dette"], reverse=True)
    return result


def institution_installations(
    slug: str,
    *,
    page: int = 1,
    page_size: int = 50,
    search: str = "",
    category: str = "",
    ordering: str = "-avg_solde",
) -> dict | None:
    """Liste paginée de TOUTES les installations d'une institution.

    Permet de filtrer par catégorie (bon/moyen/mauvais), recherche par
    référence/nom, et tri.
    """
    inst = next((i for i in INSTITUTIONS if i.slug == slug), None)
    if inst is None:
        return None

    qs = ClientBehavior.objects.filter(_build_q_for_institution(inst))

    # Filtres additionnels
    if search:
        qs = qs.filter(
            Q(reference_abonnement__icontains=search)
            | Q(nom_client__icontains=search)
            | Q(last_zone__icontains=search)
            | Q(last_centre_nom__icontains=search)
        )
    if category in ("bon", "moyen", "mauvais"):
        qs = qs.filter(category=category)

    # Tri (limité aux champs autorisés)
    allowed_orderings = (
        "-avg_solde",
        "avg_solde",
        "-total_paid",
        "total_paid",
        "-behavior_score",
        "behavior_score",
        "nom_client",
        "-nom_client",
        "reference_abonnement",
    )
    if ordering in allowed_orderings:
        qs = qs.order_by(ordering)
    else:
        qs = qs.order_by("-avg_solde")

    total = qs.count()
    start = (page - 1) * page_size
    rows = list(
        qs[start : start + page_size].values(
            "reference_abonnement",
            "nom_client",
            "type_client",
            "last_centre_nom",
            "last_zone",
            "last_releveur",
            "avg_solde",
            "max_solde",
            "total_paid",
            "nb_payments",
            "nb_code_1",
            "category",
            "behavior_score",
        )
    )

    return {
        "institution": {"slug": inst.slug, "name": inst.name},
        "count": total,
        "page": page,
        "page_size": page_size,
        "results": [
            {
                "reference_abonnement": r["reference_abonnement"],
                "nom_client": r["nom_client"],
                "type_client": r["type_client"],
                "centre_nom": r["last_centre_nom"],
                "zone": r["last_zone"],
                "releveur": r["last_releveur"],
                "avg_solde": float(r["avg_solde"]),
                "max_solde": float(r["max_solde"]),
                "total_paid": float(r["total_paid"]),
                "nb_payments": r["nb_payments"],
                "nb_code_1": r["nb_code_1"],
                "category": r["category"],
                "behavior_score": r["behavior_score"],
            }
            for r in rows
        ],
    }


def institution_detail(slug: str) -> dict | None:
    """Détail complet d'une institution : KPIs + breakdown + top débiteurs."""
    inst = next((i for i in INSTITUTIONS if i.slug == slug), None)
    if inst is None:
        return None

    q = _build_q_for_institution(inst)
    qs = ClientBehavior.objects.filter(q)
    total = qs.count()
    if total == 0:
        return {
            "slug": inst.slug,
            "name": inst.name,
            "full_name": inst.full_name,
            "nb_installations": 0,
        }

    # KPIs globaux
    agg = qs.aggregate(
        total_dette=Sum("avg_solde"),
        total_paye=Sum("total_paid"),
        nb_centres=Count("last_centre_nom", distinct=True),
    )
    total_dette = float(agg["total_dette"] or 0)
    total_paye = float(agg["total_paye"] or 0)
    denom = total_dette + total_paye
    taux = (total_paye / denom * 100) if denom > 0 else 0.0

    # Catégories
    cat_counts = {"bon": 0, "moyen": 0, "mauvais": 0}
    for row in qs.values("category").annotate(n=Count("reference_abonnement")):
        cat_counts[row["category"]] = row["n"]

    # Top 5 par centre (agrégation)
    by_centre = list(
        qs.values("last_centre_nom")
        .annotate(
            n=Count("reference_abonnement"),
            dette=Sum("avg_solde"),
            paye=Sum("total_paid"),
        )
        .order_by("-dette")[:10]
    )

    # Top 10 plus gros débiteurs (avg_solde, peu importe paiement)
    top_debtors = list(
        qs.order_by("-avg_solde")[:10].values(
            "reference_abonnement",
            "nom_client",
            "last_centre_nom",
            "last_zone",
            "last_releveur",
            "avg_solde",
            "total_paid",
            "category",
            "behavior_score",
        )
    )

    # Top 5 meilleurs payeurs au sein de l'institution
    top_payers = list(
        qs.filter(total_paid__gt=0)
        .order_by("-total_paid")[:5]
        .values(
            "reference_abonnement",
            "nom_client",
            "last_centre_nom",
            "last_zone",
            "avg_solde",
            "total_paid",
            "category",
            "behavior_score",
        )
    )

    return {
        "slug": inst.slug,
        "name": inst.name,
        "full_name": inst.full_name,
        "nb_installations": total,
        "total_dette": total_dette,
        "total_paye": total_paye,
        "taux_recouvrement": round(taux, 1),
        "nb_centres": agg["nb_centres"],
        "nb_bon": cat_counts["bon"],
        "nb_moyen": cat_counts["moyen"],
        "nb_mauvais": cat_counts["mauvais"],
        "by_centre": [
            {
                "centre_nom": c["last_centre_nom"] or "(inconnu)",
                "nb_installations": c["n"],
                "dette": float(c["dette"] or 0),
                "paye": float(c["paye"] or 0),
            }
            for c in by_centre
        ],
        "top_debtors": [
            {
                "reference_abonnement": d["reference_abonnement"],
                "nom_client": d["nom_client"],
                "centre_nom": d["last_centre_nom"],
                "zone": d["last_zone"],
                "releveur": d["last_releveur"],
                "avg_solde": float(d["avg_solde"]),
                "total_paid": float(d["total_paid"]),
                "category": d["category"],
                "behavior_score": d["behavior_score"],
            }
            for d in top_debtors
        ],
        "top_payers": [
            {
                "reference_abonnement": p["reference_abonnement"],
                "nom_client": p["nom_client"],
                "centre_nom": p["last_centre_nom"],
                "zone": p["last_zone"],
                "avg_solde": float(p["avg_solde"]),
                "total_paid": float(p["total_paid"]),
                "category": p["category"],
                "behavior_score": p["behavior_score"],
            }
            for p in top_payers
        ],
    }
