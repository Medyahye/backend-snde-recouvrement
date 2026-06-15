"""Calcul du score comportemental par client.

Agrège l'historique complet d'un client (snapshots Client + ClientMovement)
pour produire un score 0-100 et une catégorie Bon/Moyen/Mauvais.

Méthodologie :
  3 sous-scores équipondérés (33.3% chacun) :
  - Fréquence de paiement : (nb_payments / nb_factures_recues) × 100
  - Promptitude de paiement : 100 - (avg_jours_entre_facture_et_paiement / 0.6)
  - Fréquence des coupures : 100 - (nb_code_1 / nb_apparitions × 500)

Catégorisation à seuils absolus (cohérent à travers les FABs) :
  - 0 - 33 : Mauvais payeur
  - 33 - 66 : Moyen
  - 66 - 100 : Bon payeur

Scoring séparé par type_client (Domestique vs Entreprise) :
  Les profils sont calculés séparément et stockés indépendamment.
  L'affichage permet de filtrer ou comparer les deux populations.
"""
from __future__ import annotations

import logging
from decimal import Decimal

from django.db import transaction
from django.db.models import Avg, Count, Max, Q, Sum

from apps.clients.models import Client
from apps.recouvrement.models import ClientBehavior, ClientMovement


logger = logging.getLogger(__name__)

CATEGORY_THRESHOLDS = (33.0, 66.0)  # < 33: mauvais · < 66: moyen · ≥ 66: bon


def _category_for_score(score: float) -> str:
    if score < CATEGORY_THRESHOLDS[0]:
        return ClientBehavior.Category.MAUVAIS
    if score < CATEGORY_THRESHOLDS[1]:
        return ClientBehavior.Category.MOYEN
    return ClientBehavior.Category.BON


def _payment_freq_score(nb_payments: int, nb_factures: int) -> float:
    """Score 0-100 basé sur le taux de paiement."""
    if nb_factures == 0:
        return 50.0  # Pas de données → score neutre
    rate = nb_payments / nb_factures
    return min(100.0, rate * 100.0)


def _promptness_score(avg_jours_impaye: float | None) -> float:
    """Score 0-100 : 100 si paiement quasi-immédiat, 0 si délai >= 60 jours.

    `avg_jours_impaye` = moyenne du compteur SNDE "jours depuis facture" sur
    tous les snapshots. Plus c'est bas, plus le client paye vite ses factures.
    """
    if avg_jours_impaye is None:
        return 50.0  # Neutre
    # Linéaire : 0 jours = 100, 60 jours = 0
    score = 100.0 - (avg_jours_impaye / 0.6)
    return max(0.0, min(100.0, score))


def _code_1_score(nb_code_1: int, nb_imports_seen: int) -> float:
    """Score 0-100 : 100 si jamais en coupure, baisse fortement avec récidive.

    On pénalise sévèrement les récidivistes : 2% de code_1 → 0 points.
    """
    if nb_imports_seen == 0:
        return 50.0
    rate = nb_code_1 / nb_imports_seen
    # 0% → 100, 2% → 0 (sévère)
    score = 100.0 - (rate * 5000.0)
    return max(0.0, min(100.0, score))


def compute_all_behaviors(batch_size: int = 5000) -> dict:
    """Calcule ClientBehavior pour TOUS les clients en DB.

    Stratégie :
      1. Charge les agrégations par ref via une seule requête SQL groupée
         sur Client (compteurs, moyennes, type, last_seen).
      2. Charge les agrégations par ref via une seule requête sur ClientMovement
         (nb_payments, nb_code_1, total_paid).
      3. Combine + calcule les sous-scores + score global + catégorie.
      4. bulk_create / bulk_update en lots.

    Retourne un résumé : {nb_created, nb_updated, by_category, by_type}.
    """
    logger.info("Début du calcul des ClientBehavior...")

    # ---- Étape 1 : agrégations sur Client ----
    logger.info("  → Agrégation Client (nb_imports_seen, soldes, type, last_seen)...")
    client_agg = (
        Client.objects.values("reference_abonnement")
        .annotate(
            nb_imports_seen=Count("id"),
            avg_solde=Avg("solde"),
            max_solde=Max("solde"),
            avg_jours_impaye=Avg("jours_impaye"),
            max_import_date=Max("import_ref__file_date"),
        )
    )
    client_data: dict[str, dict] = {row["reference_abonnement"]: row for row in client_agg}
    logger.info("    %s clients uniques trouvés.", len(client_data))

    if not client_data:
        return {"nb_created": 0, "nb_updated": 0}

    # ---- Étape 2 : agrégations sur ClientMovement ----
    logger.info("  → Agrégation ClientMovement (paiements, code_1, total_paid)...")
    mvt_agg = (
        ClientMovement.objects.values("reference_abonnement")
        .annotate(
            nb_payments=Count(
                "id",
                filter=Q(
                    type__in=[
                        ClientMovement.Type.PAYMENT_CERTAIN,
                        ClientMovement.Type.PAYMENT_LIKELY,
                    ]
                ),
            ),
            nb_code_1=Count("id", filter=Q(code_after="1")),
            nb_new_billings=Count(
                "id", filter=Q(type=ClientMovement.Type.NEW_BILLING)
            ),
            total_paid=Sum(
                "delta_solde",
                filter=Q(
                    type__in=[
                        ClientMovement.Type.PAYMENT_CERTAIN,
                        ClientMovement.Type.PAYMENT_LIKELY,
                    ]
                ),
            ),
        )
    )
    mvt_data: dict[str, dict] = {row["reference_abonnement"]: row for row in mvt_agg}
    logger.info("    %s clients avec des mouvements.", len(mvt_data))

    # ---- Étape 3 : récupérer le dernier snapshot (nom, type, zone, releveur) ----
    # Optimisation : 1 seule requête SQL avec DISTINCT ON (PostgreSQL natif)
    # plutôt que 120 chunks de 5000 refs (chacun ~7s = 14 min total).
    # Ici on traite les 600k refs en une passe.
    logger.info("  → Chargement du dernier snapshot par ref (DISTINCT ON)...")
    from django.db import connection

    last_snaps: dict[str, dict] = {}
    with connection.cursor() as cursor:
        cursor.execute(
            """
            SELECT DISTINCT ON (c.reference_abonnement)
              c.reference_abonnement, c.nom_client, c.type_client,
              c.centre_nom, c.zone, c.releveur_1
            FROM clients c
            INNER JOIN fab_imports fi ON c.import_ref_id = fi.id
            ORDER BY c.reference_abonnement, fi.file_date DESC
            """
        )
        for ref, nom, type_c, centre, zone, releveur in cursor:
            last_snaps[ref] = {
                "nom_client": nom,
                "type_client": type_c,
                "centre_nom": centre,
                "zone": zone,
                "releveur_1": releveur,
            }
    logger.info("    Chargé %s derniers snapshots.", len(last_snaps))

    # ---- Étape 4 : construire les ClientBehavior ----
    logger.info("  → Construction des ClientBehavior...")
    behaviors: list[ClientBehavior] = []
    nb_by_cat = {"bon": 0, "moyen": 0, "mauvais": 0}
    nb_by_type = {"Domestique": 0, "Entreprise": 0}

    for ref, c_agg in client_data.items():
        m_agg = mvt_data.get(ref, {})
        snap = last_snaps.get(ref, {})
        if not snap:
            continue

        nb_imports = c_agg["nb_imports_seen"]
        nb_payments = m_agg.get("nb_payments", 0) or 0
        nb_code_1 = m_agg.get("nb_code_1", 0) or 0
        nb_new_billings = m_agg.get("nb_new_billings", 0) or 0
        total_paid = m_agg.get("total_paid") or Decimal("0")
        # nb_factures = NEW_BILLING (= apparitions de nouvelles factures)
        # Si pas de mouvements, on prend nb_imports comme proxy.
        nb_factures = max(nb_new_billings, 1)

        avg_jours_impaye = c_agg["avg_jours_impaye"]

        # Sous-scores
        s_freq = _payment_freq_score(nb_payments, nb_factures)
        s_prompt = _promptness_score(avg_jours_impaye)
        s_code_1 = _code_1_score(nb_code_1, nb_imports)

        # Score global équipondéré
        behavior_score = (s_freq + s_prompt + s_code_1) / 3.0
        category = _category_for_score(behavior_score)

        type_client = snap["type_client"] or "Domestique"
        nb_by_cat[category] += 1
        nb_by_type[type_client] = nb_by_type.get(type_client, 0) + 1

        behaviors.append(
            ClientBehavior(
                reference_abonnement=ref,
                nom_client=snap["nom_client"][:200],
                type_client=type_client,
                last_seen_date=c_agg["max_import_date"],
                last_centre_nom=snap["centre_nom"][:100],
                last_zone=snap["zone"][:150],
                last_releveur=(snap["releveur_1"] or "")[:20],
                nb_imports_seen=nb_imports,
                nb_payments=nb_payments,
                nb_code_1=nb_code_1,
                nb_new_billings=nb_new_billings,
                avg_solde=c_agg["avg_solde"] or Decimal("0"),
                max_solde=c_agg["max_solde"] or Decimal("0"),
                total_paid=total_paid,
                avg_jours_impaye=avg_jours_impaye,
                payment_freq_score=round(s_freq, 2),
                promptness_score=round(s_prompt, 2),
                code_1_score=round(s_code_1, 2),
                behavior_score=round(behavior_score, 2),
                category=category,
            )
        )

    # ---- Étape 5 : insertion atomique (DELETE + INSERT pour idempotence) ----
    logger.info("  → Insertion en DB (%s lignes)...", len(behaviors))
    with transaction.atomic():
        ClientBehavior.objects.all().delete()
        ClientBehavior.objects.bulk_create(behaviors, batch_size=batch_size)

    logger.info("Calcul terminé. Catégories : %s", nb_by_cat)
    return {
        "nb_total": len(behaviors),
        "by_category": nb_by_cat,
        "by_type": nb_by_type,
    }
