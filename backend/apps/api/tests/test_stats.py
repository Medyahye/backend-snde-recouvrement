"""Tests des endpoints stats (étape 4.3)."""
from django.urls import reverse


# --- KPIs ---

def test_kpis_requires_import_id(authed_client, fab_import):
    res = authed_client.get(reverse("stats_kpis"))
    assert res.status_code == 400


def test_kpis_returns_expected_structure(authed_client, fab_import):
    res = authed_client.get(reverse("stats_kpis"), {"import_id": fab_import.id})
    assert res.status_code == 200
    data = res.data

    # 4 sections principales
    assert "import" in data
    assert "totaux" in data
    assert "zones_par_priorite" in data
    assert "top_zone" in data

    # Cohérence des totaux (cf. fixture : 9 clients, 6 zones)
    assert data["totaux"]["nb_clients"] == 9
    assert data["totaux"]["nb_zones"] == 6

    # Répartition zones
    assert data["zones_par_priorite"]["Haute"] == 2
    assert data["zones_par_priorite"]["Moyenne"] == 2
    assert data["zones_par_priorite"]["Faible"] == 2
    # 2/6 = 33.3 %
    assert abs(data["zones_par_priorite"]["pct_haute"] - 33.3) < 0.1

    # Top zone (rang=1)
    assert data["top_zone"]["zone_id"] == "KIFFA2_18_11"


def test_kpis_unknown_import_returns_400(authed_client, fab_import):
    res = authed_client.get(reverse("stats_kpis"), {"import_id": 999_999})
    assert res.status_code == 400


# --- Distribution ---

def test_distribution_default_10_buckets(authed_client, fab_import):
    res = authed_client.get(
        reverse("stats_distribution"), {"import_id": fab_import.id}
    )
    assert res.status_code == 200
    assert res.data["n_buckets"] == 10
    assert len(res.data["buckets"]) == 10
    # La somme des comptages doit valoir nb_clients
    total_count = sum(b["count"] for b in res.data["buckets"])
    assert total_count == res.data["nb_clients"] == 9


def test_distribution_custom_buckets(authed_client, fab_import):
    res = authed_client.get(
        reverse("stats_distribution"),
        {"import_id": fab_import.id, "buckets": "5"},
    )
    assert res.status_code == 200
    assert res.data["n_buckets"] == 5
    assert len(res.data["buckets"]) == 5


def test_distribution_invalid_buckets(authed_client, fab_import):
    res = authed_client.get(
        reverse("stats_distribution"),
        {"import_id": fab_import.id, "buckets": "1"},
    )
    assert res.status_code == 400


# --- Comparison ---

def test_comparison_requires_both_imports(authed_client, fab_import):
    res = authed_client.get(
        reverse("stats_comparison"), {"import_a": fab_import.id}
    )
    assert res.status_code == 400


def test_comparison_two_imports(authed_client, authed_user, fab_import):
    """Crée un 2e import léger pour valider la diff."""
    from datetime import date
    from decimal import Decimal

    from apps.clients.models import Client
    from apps.imports.models import FabImport
    from apps.zones.models import Zone

    imp_b = FabImport.objects.create(
        minio_key="fab/2026/05/test_b.txt",
        file_date=date(2026, 5, 6),
        uploaded_by=authed_user,
        status=FabImport.Status.DONE,
        nb_lines_total=20,
        nb_clients_kept=4,
    )
    # 4 clients dans 2 zones (dont 1 commune avec fab_import : KIFFA2_18_11,
    # et 1 nouvelle : NEWZONE_99_99)
    Zone.objects.create(
        import_ref=imp_b,
        zone_id="KIFFA2_18_11",
        centre_nom="KIFFA2",
        secteur="18",
        tournee="11",
        nb_clients=2,
        nb_entreprises=0,
        nb_domestiques=2,
        score_moyen=0.40,
        score_max=0.45,
        score_total=0.80,
        anciennete_moyenne=130.0,
        solde_total=Decimal("60000"),
        solde_moyen=Decimal("30000"),
        arrieres_total=Decimal("50000"),
        priorite_zone=0.80,
        priorite="Haute",
        rang=1,
    )
    Zone.objects.create(
        import_ref=imp_b,
        zone_id="NEWZONE_99_99",
        centre_nom="NEWZONE",
        secteur="99",
        tournee="99",
        nb_clients=2,
        nb_entreprises=0,
        nb_domestiques=2,
        score_moyen=0.30,
        score_max=0.35,
        score_total=0.60,
        anciennete_moyenne=100.0,
        solde_total=Decimal("40000"),
        solde_moyen=Decimal("20000"),
        arrieres_total=Decimal("35000"),
        priorite_zone=0.60,
        priorite="Moyenne",
        rang=2,
    )
    for ref in ("X1", "X2", "X3", "X4"):
        Client.objects.create(
            import_ref=imp_b,
            reference_abonnement=ref,
            nom_client=f"Client {ref}",
            type_client="Domestique",
            code_centre="42",
            centre_nom="KIFFA2",
            secteur_facturation="18",
            tournee_releve="11",
            zone="KIFFA2_18_11",
            solde=Decimal("25000"),
            montant_facture=Decimal("5000"),
            arrieres=Decimal("20000"),
            jours_impaye=100,
            jours_sans_paiement=180,
            code_relance="1",
            montant_norm=0.4,
            anciennete_norm=0.55,
            historique_norm=1.0,
            arrieres_norm=0.8,
            coefficient_type=1.0,
            score_final=0.5,
            priorite="Haute",
            rang=1,
        )

    res = authed_client.get(
        reverse("stats_comparison"),
        {"import_a": fab_import.id, "import_b": imp_b.id},
    )
    assert res.status_code == 200
    data = res.data

    assert data["import_a"]["id"] == fab_import.id
    assert data["import_b"]["id"] == imp_b.id

    # Deltas (b - a) : nb_clients 4 - 9 = -5 ; nb_zones 2 - 6 = -4
    assert data["deltas"]["nb_clients"] == -5
    assert data["deltas"]["nb_zones"] == -4

    # Zones communes / nouvelles / disparues
    assert data["zones"]["communes"] == 1
    assert "NEWZONE_99_99" in data["zones"]["nouvelles_dans_b"]
    assert "VASALA_03_00" in data["zones"]["disparues_de_a"]
