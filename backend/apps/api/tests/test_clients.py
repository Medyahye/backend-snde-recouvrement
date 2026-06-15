"""Tests des endpoints clients (étape 4.2)."""
from django.urls import reverse


# --- Liste paginée ---

def test_clients_list_requires_import_id(authed_client, fab_import):
    res = authed_client.get(reverse("clients_list"))
    assert res.status_code == 400


def test_clients_list_returns_all_clients(authed_client, fab_import):
    res = authed_client.get(reverse("clients_list"), {"import_id": fab_import.id})
    assert res.status_code == 200
    assert res.data["count"] == 9
    # Tri par défaut : rang ascendant
    assert res.data["results"][0]["reference_abonnement"] == "R001"


def test_clients_list_filter_by_priorite(authed_client, fab_import):
    res = authed_client.get(
        reverse("clients_list"),
        {"import_id": fab_import.id, "priorite": "Haute"},
    )
    assert res.status_code == 200
    assert res.data["count"] == 5
    assert all(c["priorite"] == "Haute" for c in res.data["results"])


def test_clients_list_filter_by_zone(authed_client, fab_import):
    res = authed_client.get(
        reverse("clients_list"),
        {"import_id": fab_import.id, "zone": "KIFFA2_18_11"},
    )
    assert res.status_code == 200
    assert res.data["count"] == 3


def test_clients_list_filter_by_type(authed_client, fab_import):
    res = authed_client.get(
        reverse("clients_list"),
        {"import_id": fab_import.id, "type_client": "Entreprise"},
    )
    assert res.status_code == 200
    assert res.data["count"] == 2
    assert all(c["type_client"] == "Entreprise" for c in res.data["results"])


def test_clients_list_search_by_name(authed_client, fab_import):
    res = authed_client.get(
        reverse("clients_list"),
        {"import_id": fab_import.id, "search": "MAHFOUDH"},
    )
    assert res.status_code == 200
    assert res.data["count"] == 1


def test_clients_list_search_by_telephone(authed_client, fab_import):
    res = authed_client.get(
        reverse("clients_list"),
        {"import_id": fab_import.id, "search": "+22246000001"},
    )
    assert res.status_code == 200
    assert res.data["count"] == 1


def test_clients_list_combined_filters(authed_client, fab_import):
    """priorite=Haute & type_client=Entreprise → R004 uniquement."""
    res = authed_client.get(
        reverse("clients_list"),
        {
            "import_id": fab_import.id,
            "priorite": "Haute",
            "type_client": "Entreprise",
        },
    )
    assert res.status_code == 200
    assert res.data["count"] == 1
    assert res.data["results"][0]["reference_abonnement"] == "R004"


def test_clients_list_ordering_by_score_desc(authed_client, fab_import):
    res = authed_client.get(
        reverse("clients_list"),
        {"import_id": fab_import.id, "ordering": "-score_final"},
    )
    assert res.status_code == 200
    scores = [c["score_final"] for c in res.data["results"]]
    assert scores == sorted(scores, reverse=True)


# --- Détail client ---

def test_client_detail_includes_score_components(authed_client, fab_import):
    """Le détail expose les 4 composantes normalisées + coef pour expliquer le score."""
    from apps.clients.models import Client
    client = Client.objects.get(
        import_ref=fab_import, reference_abonnement="R001"
    )
    res = authed_client.get(reverse("clients_detail", args=[client.id]))
    assert res.status_code == 200
    assert res.data["reference_abonnement"] == "R001"
    # Composantes du score visibles dans le détail
    assert "montant_norm" in res.data
    assert "anciennete_norm" in res.data
    assert "historique_norm" in res.data
    assert "arrieres_norm" in res.data
    assert "coefficient_type" in res.data
    assert "score_final" in res.data


def test_client_detail_unauthenticated(db, fab_import):
    from rest_framework.test import APIClient
    from apps.clients.models import Client
    client_obj = Client.objects.filter(import_ref=fab_import).first()
    api = APIClient()
    res = api.get(reverse("clients_detail", args=[client_obj.id]))
    assert res.status_code == 401
