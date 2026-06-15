"""Modèle de l'app clients : snapshot d'un client pour un FabImport donné.

V1.5 : on ingère désormais TOUS les clients éligibles, quel que soit leur
code de relance (0, 1, 2, 3, 4). Seuls les clients en code_relance == "1"
sont scorés et classés.
"""
from django.db import models


class Client(models.Model):
    """Snapshot d'un client pour un import FAB donné.

    `is_scored = True` ⇔ client en code_relance "1" → tous les champs de score
    sont remplis (`score_final`, `priorite`, `rang`, composantes normalisées).
    Sinon (codes 0, 2, 3, 4) → ces champs sont à null. Seules les infos
    d'identité, finance et localisation sont garanties.
    """

    class TypeClient(models.TextChoices):
        DOMESTIQUE = "Domestique", "Domestique"
        ENTREPRISE = "Entreprise", "Entreprise"

    class Priorite(models.TextChoices):
        HAUTE = "Haute", "Haute"
        MOYENNE = "Moyenne", "Moyenne"
        FAIBLE = "Faible", "Faible"

    class RelanceState(models.TextChoices):
        """État dérivé du cycle de relance — V2 Axe B.

        Calculé à partir de `code_relance` + `date_facture` + `date_dernier_paiement`.
        """

        NORMAL = "normal", "Normal"
        SMS_J8 = "sms_j8", "SMS rappel J+8"
        GRACE_J8 = "grace_j8", "Grâce J+8"
        SMS_J48H = "sms_j48h", "SMS rappel J+48h"
        GRACE_J48H = "grace_j48h", "Grâce J+48h"
        CUT_OFF = "cut_off", "Coupure immédiate"
        CUT_OFF_FAST_TRACK = "cut_off_fast", "Coupure (cycle accéléré)"
        ANOMALY_OVERDUE = "anomaly_overdue", "Anomalie : coupure manquée"
        UNKNOWN = "unknown", "Inconnu"

    import_ref = models.ForeignKey(
        "imports.FabImport",
        on_delete=models.CASCADE,
        related_name="clients",
    )

    # --- Identité & contact ---
    reference_abonnement = models.CharField(max_length=20)
    nom_client = models.CharField(max_length=200)
    adresse = models.CharField(max_length=300, blank=True)
    telephone = models.CharField(max_length=30, blank=True)

    # --- Catégorisation ---
    activite_client = models.CharField(max_length=100, blank=True)
    type_client = models.CharField(
        max_length=20,
        choices=TypeClient.choices,
    )

    # --- Localisation (snapshot dénormalisé pour requêtes rapides) ---
    code_centre = models.CharField(max_length=10)
    centre_nom = models.CharField(max_length=100)
    secteur_facturation = models.CharField(max_length=10)
    tournee_releve = models.CharField(max_length=10)
    releveur_1 = models.CharField(max_length=20, blank=True)
    zone = models.CharField(
        max_length=150,
        help_text="NomCentre_SecteurZfill2_TourneeZfill2 — joint à zones.Zone.zone_id.",
    )

    # --- Données financières (MRU) ---
    solde = models.DecimalField(max_digits=14, decimal_places=2)
    montant_facture = models.DecimalField(max_digits=14, decimal_places=2)
    arrieres = models.DecimalField(
        max_digits=14,
        decimal_places=2,
        help_text="solde - montant_facture (clipé à 0 si négatif).",
    )

    # --- Dates & délais ---
    date_facture = models.DateField(null=True, blank=True)
    date_dernier_paiement = models.DateField(null=True, blank=True)
    jours_impaye = models.IntegerField()
    jours_sans_paiement = models.IntegerField()
    code_relance = models.CharField(
        max_length=2,
        help_text=(
            "0=repos · 4=SMS J+8 · 2=SMS J+48h · 1=coupure immédiate · "
            "3=autre. Voir Notes V1.5 sur le cycle de relance."
        ),
    )
    relance_state = models.CharField(
        max_length=20,
        choices=RelanceState.choices,
        default=RelanceState.UNKNOWN,
        help_text=(
            "État dérivé du cycle de relance, calculé depuis code_relance + dates. "
            "Permet de distinguer 'code 0 = normal' vs 'code 0 = en grâce J+8' etc."
        ),
    )

    # --- Composantes du score (Note Explicative §5) ---
    # Tous nullable depuis V1.5 : remplis uniquement si is_scored=True.
    montant_norm = models.FloatField(null=True, blank=True)
    anciennete_norm = models.FloatField(null=True, blank=True)
    historique_norm = models.FloatField(null=True, blank=True)
    arrieres_norm = models.FloatField(null=True, blank=True)
    coefficient_type = models.FloatField(
        null=True,
        blank=True,
        help_text="1.00 si Domestique, 1.20 si Entreprise.",
    )
    score_final = models.FloatField(null=True, blank=True)
    proba_paiement = models.FloatField(
        null=True,
        blank=True,
        help_text="Probabilité de paiement prédite par le FT-Transformer [0, 1]. Null si scoring formule.",
    )
    priorite = models.CharField(
        max_length=10,
        choices=Priorite.choices,
        blank=True,
        default="",
    )
    rang = models.IntegerField(
        null=True,
        blank=True,
        help_text="Position dans le classement de l'import (1 = plus prioritaire).",
    )

    class Meta:
        db_table = "clients"
        verbose_name = "Client (snapshot import)"
        verbose_name_plural = "Clients (snapshot import)"
        ordering = ["import_ref", "rang"]
        constraints = [
            models.UniqueConstraint(
                fields=["import_ref", "reference_abonnement"],
                name="client_unique_per_import",
            ),
        ]
        indexes = [
            models.Index(fields=["import_ref", "score_final"]),
            models.Index(fields=["import_ref", "zone"]),
            models.Index(fields=["import_ref", "priorite"]),
            models.Index(fields=["import_ref", "code_centre"]),
            models.Index(fields=["import_ref", "code_relance"]),
            models.Index(fields=["import_ref", "relance_state"]),
            models.Index(fields=["reference_abonnement"]),
            # Accélère le calcul ClientBehavior (DISTINCT ON par ref + ordre par import)
            models.Index(
                fields=["reference_abonnement", "import_ref"],
                name="clients_ref_import_idx",
            ),
        ]

    @property
    def is_scored(self) -> bool:
        """True ⇔ ce client a été scoré (code_relance='1' + dates valides à l'import)."""
        return self.score_final is not None

    def __str__(self) -> str:
        if self.is_scored:
            return f"{self.reference_abonnement} — {self.nom_client} (rang {self.rang})"
        return f"{self.reference_abonnement} — {self.nom_client} (code {self.code_relance})"
