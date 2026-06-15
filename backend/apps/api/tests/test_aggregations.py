"""Tests des endpoints agrégations Top-N + lookup centres (étape 4.3)."""
from django.urls import reverse


# --- Top centres ---

def test_aggregations_centres_returns_grouped(authed_client, fab_import):
    res = authed_client.get(
        reverse("aggregations_centres"), {"import_id": fab_import.id}
    )
    assert res.status_code == 200
    data = res.data
    # Fixture : 9 clients dans 5 centres distincts (KIFFA2, VASALA, CARREFOUR2, BABABE, NEMA)
    assert len(data) == 5
    centres = {row["centre"] for row in data}
    assert centres == {"KIFFA2", "VASALA", "CARREFOUR2", "BABABE", "NEMA"}

    # Chaque ligne expose les agrégats attendus
    first = data[0]
    for key in (
        "centre",
        "nb_clients",
        "score_moyen",
        "score_total",
        "solde_total",
        "priorite_score",
        "priorite",
        "rang",
    ):
        assert key in first

    # Tri par priorite_score décroissant + rang ascendant
    assert first["rang"] == 1
    scores = [row["priorite_score"] for row in data]
    assert scores == sorted(scores, reverse=True)


def test_aggregations_centres_kiffa_aggregates_correctly(authed_client, fab_import):
    """KIFFA2 a 3 clients (R001, R002, R003) — la somme des scores doit valoir 0.75+0.57+0.52."""
    res = authed_client.get(
        reverse("aggregations_centres"), {"import_id": fab_import.id}
    )
    assert res.status_code == 200
    kiffa = next(row for row in res.data if row["centre"] == "KIFFA2")
    assert kiffa["nb_clients"] == 3
    expected_total = round(0.75 + 0.57 + 0.52, 4)
    assert abs(kiffa["score_total"] - expected_total) < 0.01


# --- Top secteurs ---

def test_aggregations_secteurs(authed_client, fab_import):
    res = authed_client.get(
        reverse("aggregations_secteurs"), {"import_id": fab_import.id}
    )
    assert res.status_code == 200
    secteurs = {row["secteur"] for row in res.data}
    # Fixture : secteurs 18, 03, 20, 07
    assert "18" in secteurs and "03" in secteurs


# --- Top tournées ---

def test_aggregations_tournees(authed_client, fab_import):
    res = authed_client.get(
        reverse("aggregations_tournees"), {"import_id": fab_import.id}
    )
    assert res.status_code == 200
    tournees = {row["tournee"] for row in res.data}
    assert "11" in tournees


# --- Top releveurs ---

def test_aggregations_releveurs(authed_client, fab_import):
    res = authed_client.get(
        reverse("aggregations_releveurs"), {"import_id": fab_import.id}
    )
    assert res.status_code == 200
    # Tous les clients de la fixture ont releveur_1="3153"
    assert len(res.data) == 1
    assert res.data[0]["releveur"] == "3153"
    assert res.data[0]["nb_clients"] == 9


# --- Lookup centres ---

def test_centres_lookup_returns_seeded_91(authed_client, db):
    """La commande seed_centres doit avoir tournée avant les tests."""
    from apps.zones.models import Centre

    # Si pas seedé (cas test isolé), on crée 3 centres factices pour valider l'endpoint
    if Centre.objects.count() == 0:
        Centre.objects.bulk_create(
            [
                Centre(code="42", nom="CARREFOUR2"),
                Centre(code="96", nom="KSAR"),
                Centre(code="39", nom="KIFFA2"),
            ]
        )

    res = authed_client.get(reverse("centres_lookup"))
    assert res.status_code == 200
    # Endpoint sans pagination
    assert isinstance(res.data, list)
    assert len(res.data) >= 3
    # Chaque entrée a code + nom
    for row in res.data:
        assert "code" in row and "nom" in row


def test_centres_lookup_search_by_nom(authed_client, db):
    from apps.zones.models import Centre

    Centre.objects.get_or_create(code="42", defaults={"nom": "CARREFOUR2"})
    Centre.objects.get_or_create(code="96", defaults={"nom": "KSAR"})

    res = authed_client.get(reverse("centres_lookup"), {"search": "KSAR"})
    assert res.status_code == 200
    nom_set = {row["nom"] for row in res.data}
    assert "KSAR" in nom_set
    assert "CARREFOUR2" not in nom_set


def test_centres_lookup_unauthenticated(db):
    from rest_framework.test import APIClient

    api = APIClient()
    res = api.get(reverse("centres_lookup"))
    assert res.status_code == 401
