"""Tests des endpoints zones (étape 4.2)."""
from django.urls import reverse


# --- Liste paginée ---

def test_zones_list_requires_import_id(authed_client, fab_import):
    res = authed_client.get(reverse("zones_list"))
    assert res.status_code == 400
    assert "import_id" in str(res.data)


def test_zones_list_with_import_id_returns_all_zones(authed_client, fab_import):
    res = authed_client.get(reverse("zones_list"), {"import_id": fab_import.id})
    assert res.status_code == 200
    assert res.data["count"] == 6
    # Tri par défaut : rang ascendant
    first = res.data["results"][0]
    assert first["zone_id"] == "KIFFA2_18_11"
    assert first["rang"] == 1


def test_zones_list_filter_by_priorite(authed_client, fab_import):
    res = authed_client.get(
        reverse("zones_list"),
        {"import_id": fab_import.id, "priorite": "Haute"},
    )
    assert res.status_code == 200
    assert res.data["count"] == 2
    assert all(z["priorite"] == "Haute" for z in res.data["results"])


def test_zones_list_filter_by_centre(authed_client, fab_import):
    res = authed_client.get(
        reverse("zones_list"),
        {"import_id": fab_import.id, "centre": "KIFFA2"},
    )
    assert res.status_code == 200
    assert res.data["count"] == 1
    assert res.data["results"][0]["centre_nom"] == "KIFFA2"


def test_zones_list_search_in_zone_id(authed_client, fab_import):
    res = authed_client.get(
        reverse("zones_list"),
        {"import_id": fab_import.id, "search": "KIFFA"},
    )
    assert res.status_code == 200
    assert res.data["count"] == 1


def test_zones_list_ordering_by_priorite_zone_desc(authed_client, fab_import):
    res = authed_client.get(
        reverse("zones_list"),
        {"import_id": fab_import.id, "ordering": "-priorite_zone"},
    )
    assert res.status_code == 200
    scores = [z["priorite_zone"] for z in res.data["results"]]
    assert scores == sorted(scores, reverse=True)


def test_zones_list_unauthenticated(db, fab_import):
    from rest_framework.test import APIClient
    client = APIClient()
    res = client.get(reverse("zones_list"), {"import_id": fab_import.id})
    assert res.status_code == 401


# --- Détail zone ---

def test_zone_detail_returns_all_aggregations(authed_client, fab_import):
    from apps.zones.models import Zone
    zone = Zone.objects.get(import_ref=fab_import, zone_id="KIFFA2_18_11")
    res = authed_client.get(reverse("zones_detail", args=[zone.id]))
    assert res.status_code == 200
    assert res.data["zone_id"] == "KIFFA2_18_11"
    assert res.data["nb_clients"] == 3
    assert "score_max" in res.data
    assert "anciennete_moyenne" in res.data


def test_zone_detail_404(authed_client, fab_import):
    res = authed_client.get(reverse("zones_detail", args=[999_999]))
    assert res.status_code == 404


# --- Drill-down clients d'une zone ---

def test_zone_clients_drill_down(authed_client, fab_import):
    from apps.zones.models import Zone
    zone = Zone.objects.get(import_ref=fab_import, zone_id="KIFFA2_18_11")
    res = authed_client.get(reverse("zones_clients", args=[zone.id]))
    assert res.status_code == 200
    assert res.data["count"] == 3
    refs = [c["reference_abonnement"] for c in res.data["results"]]
    assert set(refs) == {"R001", "R002", "R003"}


def test_zone_clients_search_within_zone(authed_client, fab_import):
    from apps.zones.models import Zone
    zone = Zone.objects.get(import_ref=fab_import, zone_id="KIFFA2_18_11")
    res = authed_client.get(
        reverse("zones_clients", args=[zone.id]), {"search": "MAHFOUDH"}
    )
    assert res.status_code == 200
    assert res.data["count"] == 1
    assert res.data["results"][0]["reference_abonnement"] == "R001"


def test_zone_clients_empty_zone(authed_client, fab_import):
    """Une zone sans client ne doit pas crasher (cas limite)."""
    from apps.zones.models import Zone
    zone = Zone.objects.get(import_ref=fab_import, zone_id="ALEG_05_05")
    res = authed_client.get(reverse("zones_clients", args=[zone.id]))
    assert res.status_code == 200
    assert res.data["count"] == 0
