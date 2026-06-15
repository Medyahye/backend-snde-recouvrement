"""Fixtures pytest partagées pour les tests d'API (zones, clients, etc.)."""
from datetime import date
from decimal import Decimal

import pytest
from django.contrib.auth import get_user_model
from rest_framework.test import APIClient

User = get_user_model()


@pytest.fixture
def authed_user(db):
    return User.objects.create_user(
        username="tester@snde.local",
        email="tester@snde.local",
        password="testpass",
        role="gestionnaire",
    )


@pytest.fixture
def authed_client(authed_user):
    client = APIClient()
    client.force_authenticate(user=authed_user)
    return client


@pytest.fixture
def fab_import(authed_user):
    """Crée un FabImport avec status=done et 6 zones / 9 clients factices.

    Composition :
    - Zone KIFFA2_18_11 (3 clients, priorité Haute)
    - Zone CARREFOUR2_03_01 (2 clients, priorité Moyenne)
    - Zone NEMA_07_10 (1 client, priorité Faible)
    - Plus 3 zones/clients additionnels pour les tests de pagination/filtres.
    """
    from apps.clients.models import Client
    from apps.imports.models import FabImport
    from apps.zones.models import Zone

    imp = FabImport.objects.create(
        minio_key="fab/2026/05/test.txt",
        file_date=date(2026, 5, 5),
        uploaded_by=authed_user,
        status=FabImport.Status.DONE,
        nb_lines_total=10,
        nb_clients_kept=9,
    )

    # 6 zones avec priorités variées
    zones_data = [
        ("KIFFA2_18_11", "KIFFA2", "18", "11", 3, 0.32, 1.0, "Haute", 1),
        ("VASALA_03_00", "VASALA", "03", "00", 2, 0.25, 0.5, "Haute", 2),
        ("CARREFOUR2_03_01", "CARREFOUR2", "03", "01", 2, 0.20, 0.40, "Moyenne", 3),
        ("BABABE_20_00", "BABABE", "20", "00", 1, 0.18, 0.18, "Moyenne", 4),
        ("NEMA_07_10", "NEMA", "07", "10", 1, 0.10, 0.10, "Faible", 5),
        ("ALEG_05_05", "ALEG", "05", "05", 0, 0.05, 0.0, "Faible", 6),
    ]
    for zid, centre, sec, tour, nbc, smean, pz, prio, rang in zones_data:
        Zone.objects.create(
            import_ref=imp,
            zone_id=zid,
            centre_nom=centre,
            secteur=sec,
            tournee=tour,
            nb_clients=nbc,
            nb_entreprises=0,
            nb_domestiques=nbc,
            score_moyen=smean,
            score_max=smean + 0.05,
            score_total=smean * max(nbc, 1),
            anciennete_moyenne=120.0,
            solde_total=Decimal("100000.00"),
            solde_moyen=Decimal("50000.00"),
            arrieres_total=Decimal("80000.00"),
            priorite_zone=pz,
            priorite=prio,
            rang=rang,
        )

    # 9 clients : 3 dans KIFFA2_18_11, 2 dans VASALA_03_00, 2 dans CARREFOUR2,
    # 1 dans BABABE, 1 dans NEMA
    clients_data = [
        # (ref, nom, zone, centre_nom, type, score, priorite, rang)
        ("R001", "MAHFOUDH AMI", "KIFFA2_18_11", "KIFFA2", "Domestique", 0.75, "Haute", 1),
        ("R002", "MOHAMED YAHYA", "KIFFA2_18_11", "KIFFA2", "Domestique", 0.57, "Haute", 2),
        ("R003", "KHADI BOWBA", "KIFFA2_18_11", "KIFFA2", "Domestique", 0.52, "Haute", 3),
        ("R004", "GARRIDO EMILIO", "VASALA_03_00", "VASALA", "Entreprise", 0.51, "Haute", 4),
        ("R005", "SALEM ABDELLAHI", "VASALA_03_00", "VASALA", "Domestique", 0.50, "Haute", 5),
        ("R006", "DAHONA CHEIKH", "CARREFOUR2_03_01", "CARREFOUR2", "Domestique", 0.48, "Moyenne", 6),
        ("R007", "GHALIA CHEIBANI", "CARREFOUR2_03_01", "CARREFOUR2", "Entreprise", 0.45, "Moyenne", 7),
        ("R008", "EL MOUNA VADEL", "BABABE_20_00", "BABABE", "Domestique", 0.20, "Moyenne", 8),
        ("R009", "GASTAN AGRER", "NEMA_07_10", "NEMA", "Domestique", 0.10, "Faible", 9),
    ]
    for ref, nom, zone, centre, typec, score, prio, rang in clients_data:
        Client.objects.create(
            import_ref=imp,
            reference_abonnement=ref,
            nom_client=nom,
            adresse=f"Adresse de {ref}",
            telephone=f"+22246000{rang:03d}",
            activite_client=(
                "TOUS CLIENTS DOMESTIQUES" if typec == "Domestique" else "USINE"
            ),
            type_client=typec,
            code_centre=zone.split("_")[0][:5],
            centre_nom=centre,
            secteur_facturation=zone.split("_")[1],
            tournee_releve=zone.split("_")[2],
            releveur_1="3153",
            zone=zone,
            solde=Decimal(str(50000 + rang * 1000)),
            montant_facture=Decimal("5000"),
            arrieres=Decimal(str(45000 + rang * 1000)),
            date_facture=date(2026, 1, 15),
            date_dernier_paiement=date(2025, 11, 1),
            jours_impaye=110,
            jours_sans_paiement=185,
            code_relance="1",
            montant_norm=0.5,
            anciennete_norm=0.6,
            historique_norm=1.0,
            arrieres_norm=0.9,
            coefficient_type=1.0 if typec == "Domestique" else 1.2,
            score_final=score,
            priorite=prio,
            rang=rang,
        )

    return imp
