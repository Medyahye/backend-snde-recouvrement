"""Modèle ClientMovement : trace les transitions de solde + code_relance
entre 2 imports successifs pour chaque client.

Logique métier (cf. discussion 2026-05-08) :
- On compare client par client (`reference_abonnement`) entre `import_from` et `import_to`.
- On classifie chaque transition pour distinguer un paiement réel d'un ajustement
  comptable, en croisant le delta de solde ET le changement de code de relance.
"""
from django.db import models


class ClientMovement(models.Model):
    """Une transition de solde + code_relance entre 2 imports successifs."""

    class Type(models.TextChoices):
        # Transitions avec gain réel
        PAYMENT_CERTAIN = "payment_certain", "Paiement certain"
        PAYMENT_LIKELY = "payment_likely", "Paiement probable"
        # Mouvements suspects
        ADJUSTMENT = "adjustment", "Ajustement / correction"
        DEPARTURE = "departure", "Sortie suspecte"
        # Augmentations
        NEW_BILLING = "new_billing", "Nouvelle facturation"
        NEW_CLIENT = "new_client", "Nouveau client"
        # Pas de changement
        NO_MOVEMENT = "no_movement", "Pas de mouvement"

    # --- Identification ---
    reference_abonnement = models.CharField(max_length=20)
    nom_client = models.CharField(max_length=200, blank=True)

    import_from = models.ForeignKey(
        "imports.FabImport",
        on_delete=models.CASCADE,
        related_name="movements_out",
        null=True,
        blank=True,
        help_text="Import précédent (null si nouveau client).",
    )
    import_to = models.ForeignKey(
        "imports.FabImport",
        on_delete=models.CASCADE,
        related_name="movements_in",
        help_text="Import courant (sur lequel la transition est calculée).",
    )

    # --- Soldes ---
    solde_before = models.DecimalField(
        max_digits=14, decimal_places=2, null=True, blank=True
    )
    solde_after = models.DecimalField(
        max_digits=14, decimal_places=2, null=True, blank=True
    )
    delta_solde = models.DecimalField(
        max_digits=14,
        decimal_places=2,
        help_text=(
            "solde_before - solde_after. Positif = solde baissé (paiement probable). "
            "Négatif = solde augmenté (nouvelle facturation)."
        ),
    )

    # --- Codes de relance ---
    code_before = models.CharField(max_length=2, blank=True)
    code_after = models.CharField(max_length=2, blank=True)
    montant_facture_before = models.DecimalField(
        max_digits=14, decimal_places=2, null=True, blank=True,
        help_text="Pour comparer delta_solde vs montant facture précédente.",
    )

    # --- Dates de paiement (signal #1 de détection — V2 Axe A) ---
    date_paiement_before = models.DateField(
        null=True, blank=True,
        help_text="date_dernier_paiement du client dans le FAB précédent.",
    )
    date_paiement_after = models.DateField(
        null=True, blank=True,
        help_text="date_dernier_paiement du client dans le FAB courant. "
                  "Si > date_paiement_before, un paiement a été enregistré par SNDE.",
    )

    # --- Fast-track-cutoff (V2 Axe B2 — NB du tuteur) ---
    skipped_grace = models.BooleanField(
        default=False,
        help_text=(
            "True si la transition mène à code 1 sans être passée par code 2 "
            "récemment. Indique que SNDE a accéléré le cycle (typique des "
            "clients à fort solde)."
        ),
    )

    # --- Classification ---
    type = models.CharField(max_length=20, choices=Type.choices)
    confidence = models.FloatField(
        help_text="Niveau de confiance dans la classification (0.0 à 1.0).",
    )
    notes = models.TextField(
        blank=True,
        help_text="Détails sur la classification (ex: 'delta=montant_facture exact').",
    )

    # --- Localisation (dénormalisé pour requêtes rapides) ---
    centre_nom = models.CharField(max_length=100, blank=True)
    zone = models.CharField(max_length=150, blank=True)

    # --- Dates ---
    date_from = models.DateField(
        help_text="file_date de l'import_from (date du FAB précédent).",
        null=True,
        blank=True,
    )
    date_to = models.DateField(
        help_text="file_date de l'import_to (date du FAB courant).",
    )
    computed_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "client_movements"
        verbose_name = "Mouvement client"
        verbose_name_plural = "Mouvements clients"
        ordering = ["-date_to", "-delta_solde"]
        indexes = [
            models.Index(fields=["import_to", "type"]),
            models.Index(fields=["import_to", "date_to"]),
            models.Index(fields=["reference_abonnement"]),
            models.Index(fields=["type", "date_to"]),
        ]

    @property
    def is_payment(self) -> bool:
        """Mouvement comptabilisable comme recouvrement effectif."""
        return self.type in (
            self.Type.PAYMENT_CERTAIN,
            self.Type.PAYMENT_LIKELY,
        )

    def __str__(self) -> str:
        return (
            f"{self.reference_abonnement} {self.date_to} · "
            f"{self.get_type_display()} ({self.delta_solde} MRU)"
        )


class ClientBehavior(models.Model):
    """Profil comportemental cumulatif par client (1 ligne par reference_abonnement).

    Agrège l'historique complet du client (~150-200 apparitions sur 8 mois)
    pour produire un score 0-100 et une catégorie Bon/Moyen/Mauvais. Permet :
      - de prédire qui va payer (Bons) → l'argent qui rentre dans X jours
      - d'identifier les comportements à risque (Mauvais) → mesures préventives

    Score calculé par pondération équilibrée de 3 signaux indépendants :
      1. Fréquence de paiement (% factures honorées)
      2. Promptitude de paiement (délai moyen)
      3. Fréquence de coupures (récidive en code_relance=1)

    Scoring séparé par type_client (Domestique vs Entreprise) car les cycles
    de facturation et montants diffèrent significativement.
    """

    class Category(models.TextChoices):
        BON = "bon", "Bon payeur"
        MOYEN = "moyen", "Payeur moyen"
        MAUVAIS = "mauvais", "Mauvais payeur"

    class TypeClient(models.TextChoices):
        DOMESTIQUE = "Domestique", "Domestique"
        ENTREPRISE = "Entreprise", "Entreprise"

    reference_abonnement = models.CharField(max_length=20, primary_key=True)
    nom_client = models.CharField(max_length=200)
    type_client = models.CharField(max_length=20, choices=TypeClient.choices)

    # Dernière vue (snapshot du FAB le + récent où ce client apparaît)
    last_seen_date = models.DateField(
        null=True, blank=True,
        help_text="Date du FAB le + récent où le client était présent.",
    )
    last_centre_nom = models.CharField(max_length=100, blank=True)
    last_zone = models.CharField(max_length=150, blank=True)
    last_releveur = models.CharField(max_length=20, blank=True)

    # --- Compteurs historiques ---
    nb_imports_seen = models.IntegerField(
        help_text="Nombre total d'apparitions dans des FABs.",
    )
    nb_payments = models.IntegerField(
        help_text="Nombre de mouvements PAYMENT_CERTAIN + PAYMENT_LIKELY.",
    )
    nb_code_1 = models.IntegerField(
        help_text="Nombre de fois passé en code_relance='1' (coupures ordonnées).",
    )
    nb_new_billings = models.IntegerField(
        help_text="Nombre de NEW_BILLING (factures supplémentaires).",
    )

    # --- Agrégats financiers ---
    avg_solde = models.DecimalField(max_digits=14, decimal_places=2)
    max_solde = models.DecimalField(max_digits=14, decimal_places=2)
    total_paid = models.DecimalField(
        max_digits=16, decimal_places=2, default=0,
        help_text="Somme des delta_solde des PAYMENT_* (montant payé estimé).",
    )

    # --- Agrégats temporels ---
    avg_jours_impaye = models.FloatField(
        null=True, blank=True,
        help_text="Moyenne des jours_impaye sur tous les snapshots.",
    )

    # --- Sous-scores (0-100) ---
    payment_freq_score = models.FloatField(
        help_text="Score fréquence paiement (taux paiements / nb_factures).",
    )
    promptness_score = models.FloatField(
        help_text="Score promptitude (inverse du délai moyen).",
    )
    code_1_score = models.FloatField(
        help_text="Score code 1 (100 - fréquence des coupures ordonnées).",
    )

    # --- Score global et catégorie ---
    behavior_score = models.FloatField(
        help_text="Score global 0-100 (moyenne équipondérée des 3 sous-scores).",
    )
    category = models.CharField(
        max_length=10,
        choices=Category.choices,
        help_text="0-33: Mauvais · 33-66: Moyen · 66-100: Bon",
    )

    # --- Métadonnées ---
    computed_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "client_behaviors"
        verbose_name = "Profil comportemental client"
        verbose_name_plural = "Profils comportementaux clients"
        ordering = ["-behavior_score"]
        indexes = [
            models.Index(fields=["category"]),
            models.Index(fields=["type_client"]),
            models.Index(fields=["behavior_score"]),
            models.Index(fields=["type_client", "category"]),
            models.Index(fields=["last_zone"]),
            models.Index(fields=["last_releveur"]),
        ]

    def __str__(self) -> str:
        return (
            f"{self.reference_abonnement} {self.nom_client[:30]} · "
            f"{self.get_category_display()} ({self.behavior_score:.0f}/100)"
        )
