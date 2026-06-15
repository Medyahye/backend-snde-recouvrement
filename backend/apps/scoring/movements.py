"""Détection des mouvements de solde entre 2 imports successifs.

Pour chaque client présent dans l'import courant, on cherche sa version
dans l'import précédent et on classifie la transition selon :
- la variation de solde (`delta_solde`)
- le changement de code de relance
- le montant de la dernière facture
"""
from __future__ import annotations

import logging
from decimal import Decimal
from typing import Iterable

from celery import shared_task
from django.db import transaction

logger = logging.getLogger(__name__)

# Seuil "montant rond" : multiples de cette valeur sont des candidats paiement
ROUNDING_GRAIN_MRU = Decimal("100")


def _is_round_amount(delta: Decimal) -> bool:
    """True si delta est multiple de ROUNDING_GRAIN_MRU (typique d'un paiement caisse)."""
    if delta <= 0:
        return False
    return (delta % ROUNDING_GRAIN_MRU) == 0


def classify_movement(
    *,
    solde_before: Decimal | None,
    solde_after: Decimal | None,
    code_before: str,
    code_after: str,
    montant_facture_before: Decimal | None,
    date_paiement_before=None,
    date_paiement_after=None,
) -> tuple[str, float, str]:
    """Classifie une transition. Retourne (type, confidence 0-1, notes).

    Hiérarchie de décision (V2 Axe A) :
    0. Nouveau / disparu (cas extrêmes)
    1. **`date_dernier_paiement` a avancé** → PAYMENT_CERTAIN (signal #1)
       car la SNDE ne touche jamais ce champ pour un ajustement comptable.
    2. delta == 0 → NO_MOVEMENT
    3. delta < 0 → NEW_BILLING (plus de dette)
    4. Heuristiques delta > 0 (montant facture exact, solde soldé, etc.)
    """
    from apps.recouvrement.models import ClientMovement

    T = ClientMovement.Type

    # Cas 1 : nouveau client (pas vu avant)
    if solde_before is None:
        return T.NEW_CLIENT, 1.0, "Client présent uniquement dans l'import courant."

    # Cas 2 : client disparu (présent avant, absent maintenant)
    if solde_after is None:
        if solde_before > 0:
            return (
                T.DEPARTURE,
                0.0,
                f"Client présent avec solde {solde_before} MRU mais absent du FAB courant. "
                "À investiguer : résiliation, créance perdue ou simple omission ?",
            )
        return T.DEPARTURE, 0.0, "Client sorti (solde nul)."

    delta = (solde_before or Decimal("0")) - (solde_after or Decimal("0"))

    # ====================================================================== #
    # 🎯 SIGNAL #1 (V2 Axe A) — la date de paiement a avancé
    # La SNDE met à jour `date_dernier_paiement` uniquement quand elle
    # enregistre un paiement réel en caisse. Aucun ajustement comptable
    # ne touche ce champ. C'est le signal le plus fiable.
    # ====================================================================== #
    if (
        date_paiement_before is not None
        and date_paiement_after is not None
        and date_paiement_after > date_paiement_before
    ):
        delta_str = f"{delta:+}"
        return (
            T.PAYMENT_CERTAIN,
            0.99,
            f"date_dernier_paiement passée de {date_paiement_before} à "
            f"{date_paiement_after} → paiement enregistré par SNDE "
            f"(delta_solde {delta_str} MRU).",
        )

    # Cas 3 : pas de mouvement
    if delta == 0:
        return T.NO_MOVEMENT, 1.0, ""

    # Cas 4 : nouvelle facturation (solde augmenté)
    if delta < 0:
        return (
            T.NEW_BILLING,
            1.0,
            f"Nouvelle facturation : +{-delta} MRU.",
        )

    # delta > 0 mais date_paiement inchangée → ajustement OU paiement non
    # encore traduit dans date_dernier_paiement (rare)
    notes_parts = []

    # Heuristique 1 : delta = montant exact de la dernière facture → paiement certain
    if (
        montant_facture_before is not None
        and montant_facture_before > 0
        and delta == montant_facture_before
    ):
        return (
            T.PAYMENT_CERTAIN,
            0.99,
            f"Delta {delta} = montant facture précédente exact.",
        )

    # Heuristique 2 : delta = solde_before total (a soldé toute sa dette)
    if delta == solde_before:
        notes_parts.append(f"Solde totalement remis à zéro ({solde_before} MRU).")
        # On regarde aussi le code de relance pour augmenter la confiance
        if code_before == "1" and code_after in ("0", "4"):
            return (
                T.PAYMENT_CERTAIN,
                0.95,
                " ".join(notes_parts)
                + f" Code passé de 1 (coupure) à {code_after} → cycle redémarré, paiement très probable.",
            )
        if _is_round_amount(delta):
            return (
                T.PAYMENT_LIKELY,
                0.85,
                " ".join(notes_parts) + " Montant rond.",
            )
        return (
            T.PAYMENT_LIKELY,
            0.75,
            " ".join(notes_parts) + " Montant non rond (possible ajustement).",
        )

    # Heuristique 3 : transition de code de relance favorable (1 → 0 / 4)
    # Comme on exclut les échéanciers, un paiement partiel ne peut pas exister :
    # soit le client paie tout (et passe 1→0), soit il ne paie pas.
    # Un delta partiel + transition 1→0 est suspect → ADJUSTMENT.
    if code_before == "1" and code_after in ("0", "4"):
        notes_parts.append(
            f"Code passé de 1 (coupure) à {code_after} → cycle redémarré."
        )
        if _is_round_amount(delta):
            return (
                T.PAYMENT_LIKELY,
                0.90,
                " ".join(notes_parts) + f" Montant rond de {delta} MRU.",
            )
        return (
            T.ADJUSTMENT,
            0.50,
            " ".join(notes_parts)
            + f" Delta non rond ({delta} MRU) sans solde complet → "
            "ajustement comptable probable (on n'a pas d'échéanciers).",
        )

    # Heuristique 4 : montant rond mais code inchangé → paiement probable
    if _is_round_amount(delta):
        return (
            T.PAYMENT_LIKELY,
            0.70,
            f"Delta rond de {delta} MRU (typique caisse).",
        )

    # Heuristique 5 (V2.1) : grosse baisse significative sans autre signal
    # Cas typique : clients institutionnels (ONSER, SONADER, universités, etc.)
    # qui paient par virement → SNDE ne met pas à jour date_dernier_paiement,
    # et les montants ne sont pas ronds (factures précises au centime).
    # Sans cette règle, ces paiements légitimes étaient classés ADJUSTMENT
    # à tort → biais systémique du score comportemental.
    if (
        solde_before is not None
        and solde_before > 0
        and delta >= Decimal("100")
        and (delta / solde_before) >= Decimal("0.05")
    ):
        pct = (delta / solde_before * 100).quantize(Decimal("0.1"))
        return (
            T.PAYMENT_LIKELY,
            0.65,
            f"Baisse significative de {delta} MRU ({pct}% du solde précédent) "
            "sans changement de date_paiement → paiement probable par virement "
            "(typique clients institutionnels).",
        )

    # Sinon : à investiguer (delta non rond petit, pas de signal fort)
    return (
        T.ADJUSTMENT,
        0.40,
        f"Baisse de {delta} MRU non ronde, codes inchangés ({code_before}→{code_after}). "
        "Vérifier s'il s'agit d'un paiement réel ou d'un ajustement comptable.",
    )


@shared_task(bind=True, name="recouvrement.compute_movements_for_import")
def compute_movements_for_import(self, import_id: int) -> dict:
    """Calcule tous les ClientMovement pour un FabImport donné en comparant
    avec le précédent import 'done'.
    """
    from apps.clients.models import Client
    from apps.imports.models import FabImport
    from apps.recouvrement.models import ClientMovement

    imp = FabImport.objects.get(id=import_id)

    # Trouver l'import précédent terminé (file_date strictement antérieure)
    prev = (
        FabImport.objects.filter(
            status=FabImport.Status.DONE,
            file_date__lt=imp.file_date,
        )
        .order_by("-file_date", "-uploaded_at")
        .first()
    )
    if prev is None:
        logger.info(
            "Pas d'import précédent pour FabImport #%s — pas de mouvements à calculer.",
            imp.id,
        )
        return {"import_id": imp.id, "previous": None, "nb_movements": 0}

    logger.info(
        "Calcul des mouvements : import %s (%s) vs précédent %s (%s)",
        imp.id, imp.file_date, prev.id, prev.file_date,
    )

    # MEMORY OPTIMIZATION : charger en tuples légers (pas d'objets Django)
    # ~10× moins de RAM vs ORM. Avec iterator() on évite aussi le cache QuerySet.
    PREV_FIELDS = (
        "reference_abonnement",
        "nom_client",
        "solde",
        "montant_facture",
        "code_relance",
        "centre_nom",
        "zone",
        "date_dernier_paiement",
    )
    CURR_FIELDS = (
        "reference_abonnement",
        "nom_client",
        "solde",
        "code_relance",
        "centre_nom",
        "zone",
        "date_dernier_paiement",
    )

    # Capturer les dates ici (utilisées en aval pour les requêtes historiques)
    imp_file_date = imp.file_date
    prev_file_date = prev.file_date

    prev_data: dict[str, tuple] = {}
    for vals in (
        Client.objects.filter(import_ref=prev)
        .values_list(*PREV_FIELDS)
        .iterator(chunk_size=5000)
    ):
        prev_data[vals[0]] = vals[1:]

    curr_data: dict[str, tuple] = {}
    for vals in (
        Client.objects.filter(import_ref=imp)
        .values_list(*CURR_FIELDS)
        .iterator(chunk_size=5000)
    ):
        curr_data[vals[0]] = vals[1:]

    # ====================================================================== #
    # 🎯 V2 — Détection vrai NEW_CLIENT (vs returning après gap)
    # Pour les refs présentes dans curr mais ABSENTES de prev (N-1), on
    # cherche leur DERNIÈRE apparition dans n'importe quel FAB plus ancien.
    # Si trouvée → on l'utilise comme "before" pour la classification (=
    # le delta sera calculé contre cette ancienne valeur, pas contre None).
    # Si pas trouvée → vrai NEW_CLIENT (jamais vu nulle part).
    # ====================================================================== #
    missing_in_prev = set(curr_data.keys()) - set(prev_data.keys())
    historical_imports: dict[str, FabImport] = {}  # ref -> FabImport historique

    if missing_in_prev:
        from django.db.models import OuterRef, Subquery

        # Sous-requête : récupère l'id du FabImport le plus récent pour chaque ref,
        # parmi les imports DONE strictement antérieurs à l'import courant.
        latest_import_subq = (
            Client.objects.filter(
                reference_abonnement=OuterRef("reference_abonnement"),
                import_ref__file_date__lt=imp_file_date,
                import_ref__status=FabImport.Status.DONE,
            )
            .order_by("-import_ref__file_date")
            .values("import_ref_id")[:1]
        )

        # On traite par chunks pour éviter une requête SQL gigantesque
        # si missing_in_prev contient des milliers de refs
        refs_list = list(missing_in_prev)
        CHUNK = 5000
        seen_historical: dict[str, tuple] = {}
        for i in range(0, len(refs_list), CHUNK):
            chunk = refs_list[i : i + CHUNK]
            for vals in (
                Client.objects.filter(
                    reference_abonnement__in=chunk,
                    import_ref_id=Subquery(latest_import_subq),
                )
                .values_list(
                    "reference_abonnement",   # 0
                    "nom_client",             # 1
                    "solde",                  # 2
                    "montant_facture",        # 3
                    "code_relance",           # 4
                    "centre_nom",             # 5
                    "zone",                   # 6
                    "date_dernier_paiement",  # 7
                    "import_ref_id",          # 8
                )
            ):
                seen_historical[vals[0]] = vals

        if seen_historical:
            # Charger les FabImport correspondants (un objet par import historique unique)
            historical_import_ids = {v[8] for v in seen_historical.values()}
            historical_imports_by_id = {
                obj.id: obj
                for obj in FabImport.objects.filter(id__in=historical_import_ids)
            }
            # Injecter dans prev_data au format PREV_FIELDS (nom, solde, mont_fact, code, centre, zone, date_paiement)
            for ref, vals in seen_historical.items():
                prev_data[ref] = vals[1:8]  # drop ref (idx 0) et import_id (idx 8)
                historical_imports[ref] = historical_imports_by_id[vals[8]]

        logger.info(
            "Refs absentes de N-1 : %s — dont %s retrouvées dans l'historique (returning), %s vraiment nouvelles",
            len(missing_in_prev),
            len(historical_imports),
            len(missing_in_prev) - len(historical_imports),
        )

    all_refs = set(prev_data.keys()) | set(curr_data.keys())
    logger.info(
        "Mouvements à calculer : %s refs uniques (prev=%s, curr=%s)",
        len(all_refs), len(prev_data), len(curr_data),
    )

    # Nettoyer les anciens mouvements de cet import avant insertion
    ClientMovement.objects.filter(import_to=imp).delete()

    # MEMORY OPTIMIZATION : streaming inserts par batch (au lieu d'accumuler tout)
    BATCH_SIZE = 2000
    batch: list[ClientMovement] = []
    nb_movements = 0
    # imp_file_date / prev_file_date sont déjà définis plus haut

    for ref in all_refs:
        p = prev_data.get(ref)
        c = curr_data.get(ref)

        # Unpack tuples (None si client absent du FAB correspondant)
        if p is not None:
            p_nom, p_solde, p_mont_fact, p_code, p_centre, p_zone, p_dpaiement = p
        else:
            p_nom = p_centre = p_zone = ""
            p_solde = p_mont_fact = p_code = p_dpaiement = None

        if c is not None:
            c_nom, c_solde, c_code, c_centre, c_zone, c_dpaiement = c
        else:
            c_nom = c_centre = c_zone = ""
            c_solde = c_code = c_dpaiement = None

        nom = (c_nom or p_nom or "")[:200]
        centre = (c_centre or p_centre or "")[:100]
        zone = (c_zone or p_zone or "")[:150]

        type_, confidence, notes = classify_movement(
            solde_before=p_solde,
            solde_after=c_solde,
            code_before=p_code or "",
            code_after=c_code or "",
            montant_facture_before=p_mont_fact,
            date_paiement_before=p_dpaiement,
            date_paiement_after=c_dpaiement,
        )

        delta = (p_solde or Decimal("0")) - (c_solde or Decimal("0"))

        # Détermine quel FabImport référencer comme "import_from" :
        #   - Si le ref vient directement de N-1 → prev (immédiat)
        #   - Si le ref vient d'un FAB plus ancien (returning) → historical_imports[ref]
        #   - Si le ref n'a aucun antécédent (vrai NEW_CLIENT) → None
        if p is None:
            from_import = None
            from_date = None
        elif ref in historical_imports:
            from_import = historical_imports[ref]
            from_date = from_import.file_date
        else:
            from_import = prev
            from_date = prev_file_date

        batch.append(
            ClientMovement(
                reference_abonnement=ref[:20],
                nom_client=nom,
                import_from=from_import,
                import_to=imp,
                solde_before=p_solde,
                solde_after=c_solde,
                delta_solde=delta,
                code_before=p_code or "",
                code_after=c_code or "",
                montant_facture_before=p_mont_fact,
                date_paiement_before=p_dpaiement,
                date_paiement_after=c_dpaiement,
                type=type_,
                confidence=confidence,
                notes=notes,
                centre_nom=centre,
                zone=zone,
                date_from=from_date,
                date_to=imp_file_date,
            )
        )

        if len(batch) >= BATCH_SIZE:
            ClientMovement.objects.bulk_create(batch, batch_size=BATCH_SIZE)
            nb_movements += len(batch)
            batch.clear()

    # Flush du dernier batch
    if batch:
        ClientMovement.objects.bulk_create(batch, batch_size=BATCH_SIZE)
        nb_movements += len(batch)
        batch.clear()

    # Libère explicitement les dicts (Python GC les nettoiera, on accélère)
    prev_data.clear()
    curr_data.clear()
    all_refs.clear()
    historical_imports.clear()

    # ====================================================================== #
    # 🎯 Détection FAST-TRACK-CUTOFF (V2 Axe B2 — NB du tuteur)
    # Un client est "fast-track" s'il est en code 1 dans l'import courant
    # MAIS n'est jamais passé par code 2 dans les 30 derniers jours.
    # ====================================================================== #
    nb_fast_track = _detect_and_flag_fast_track_cutoffs(imp)

    # Comptage paiements via SQL (pas en mémoire)
    nb_payments = ClientMovement.objects.filter(
        import_to=imp,
        type__in=[
            ClientMovement.Type.PAYMENT_CERTAIN,
            ClientMovement.Type.PAYMENT_LIKELY,
        ],
    ).count()

    logger.info(
        "Mouvements calculés pour FabImport #%s : %s mouvements, %s paiements, %s fast-track",
        imp.id, nb_movements, nb_payments, nb_fast_track,
    )

    return {
        "import_id": imp.id,
        "previous": prev.id,
        "nb_movements": nb_movements,
        "nb_payments": nb_payments,
        "nb_fast_track_cutoffs": nb_fast_track,
    }


def _detect_and_flag_fast_track_cutoffs(imp) -> int:
    """Détecte les clients passés en code 1 sans avoir transité par code 2.

    Met à jour :
    - `ClientMovement.skipped_grace = True` pour les transitions menant à code 1
      sans historique de code 2 récent.
    - `Client.relance_state = CUT_OFF_FAST_TRACK` pour ces mêmes clients.
    """
    from datetime import timedelta

    from apps.clients.models import Client
    from apps.recouvrement.models import ClientMovement

    # Fenêtre d'observation : 30 jours avant l'import courant
    window_start = imp.file_date - timedelta(days=30)

    # 1. Récupérer toutes les références des clients à code 1 dans l'import courant
    cur_code1_refs = set(
        Client.objects.filter(
            import_ref=imp, code_relance="1"
        ).values_list("reference_abonnement", flat=True)
    )
    if not cur_code1_refs:
        return 0

    # 2. Trouver lesquels sont passés par code 2 dans la fenêtre
    refs_with_code2 = set(
        Client.objects.filter(
            reference_abonnement__in=cur_code1_refs,
            code_relance="2",
            import_ref__file_date__gte=window_start,
            import_ref__file_date__lt=imp.file_date,
        )
        .values_list("reference_abonnement", flat=True)
        .distinct()
    )

    # 3. Fast-track = code 1 maintenant SANS code 2 dans les 30 derniers jours
    fast_track_refs = cur_code1_refs - refs_with_code2
    if not fast_track_refs:
        return 0

    # 4. Flag des ClientMovement (transitions vers ce code 1)
    ClientMovement.objects.filter(
        import_to=imp,
        reference_abonnement__in=fast_track_refs,
        code_after="1",
    ).update(skipped_grace=True)

    # 5. Mettre à jour relance_state des clients concernés
    Client.objects.filter(
        import_ref=imp,
        reference_abonnement__in=fast_track_refs,
    ).update(relance_state=Client.RelanceState.CUT_OFF_FAST_TRACK)

    return len(fast_track_refs)
