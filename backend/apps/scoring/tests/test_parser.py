"""Tests du parser FAB (apps/scoring/parser.py)."""
from datetime import date

from apps.scoring.parser import parse_fab_text


# Construit une ligne FAB synthétique avec 35 colonnes ($$-séparées).
def _make_line(
    *,
    code_activite="1",
    code_centre="42",
    secteur="03",
    tournee="01",
    ref="REF001",
    nom="JEAN DOE",
    telephone="+22246123456",
    activite="TOUS CLIENTS DOMESTIQUES",
    solde="50000",
    date_fact="01/03/26",
    montant_fact="5000",
    date_paye="01/02/26",
    code_echeance="0",
    code_relance="1",
):
    parts = [""] * 35
    parts[0] = code_activite
    parts[1] = "2026"
    parts[2] = "4"
    parts[3] = code_centre
    parts[4] = secteur
    parts[5] = tournee
    parts[6] = ref
    parts[7] = nom
    parts[8] = "ADR1"
    parts[9] = telephone
    parts[10] = ""
    parts[11] = "NNI001"
    parts[12] = "12345"
    parts[13] = "15"
    parts[14] = "6"
    parts[15] = "DIAMETRE 025 MM"
    parts[16] = "1234"
    parts[17] = "0"
    parts[18] = "10.5"
    parts[19] = activite
    parts[20] = "TARIF1"
    parts[21] = "F1"
    parts[22] = solde
    parts[23] = date_fact
    parts[24] = montant_fact
    parts[25] = date_paye
    parts[26] = ""
    parts[27] = "3153"
    parts[28] = "A1542"
    parts[29] = ""
    parts[30] = ""
    parts[31] = ""
    parts[32] = code_echeance
    parts[33] = "0.0"
    parts[34] = code_relance
    return "$$".join(parts)


def test_parse_valid_line():
    line = _make_line()
    df, stats = parse_fab_text(line, date(2026, 4, 12))
    assert stats == {
        "total": 1,
        "valid": 1,
        "invalid": 0,
        "reminder_counts": {"0": 0, "1": 1, "2": 0, "3": 0, "4": 0, "err": 0},
    }
    row = df.iloc[0]
    assert row["reference_abonnement"] == "REF001"
    assert row["nom_client"] == "JEAN DOE"
    assert row["solde"] == 50000.0
    assert row["montant_facture"] == 5000.0
    assert row["arrieres"] == 45000.0  # solde - montant_facture
    assert row["date_facture"] == date(2026, 3, 1)
    assert row["date_dernier_paiement"] == date(2026, 2, 1)
    assert row["code_relance"] == "1"
    assert row["telephone"] == "+22246123456"


def test_invalid_lines_are_counted_not_parsed():
    short_line = "1$$2026$$4"  # < 35 colonnes
    empty_line = ""
    valid = _make_line()
    content = "\n".join([short_line, empty_line, valid])
    df, stats = parse_fab_text(content, date(2026, 4, 12))
    assert stats["total"] == 3
    assert stats["valid"] == 1
    assert stats["invalid"] == 2
    assert len(df) == 1


def test_phone_normalization():
    line = _make_line(telephone="  +222 46-12 34 56 ")
    df, _ = parse_fab_text(line, date(2026, 4, 12))
    assert df.iloc[0]["telephone"] == "+22246123456"


def test_decimal_with_comma_or_dot():
    line = _make_line(solde="1234,56", montant_fact="100.50")
    df, _ = parse_fab_text(line, date(2026, 4, 12))
    assert df.iloc[0]["solde"] == 1234.56
    assert df.iloc[0]["montant_facture"] == 100.50


def test_invalid_date_returns_none():
    line = _make_line(date_fact="invalid-date")
    df, _ = parse_fab_text(line, date(2026, 4, 12))
    assert df.iloc[0]["date_facture"] is None


def test_reminder_count_mix():
    content = "\n".join(
        [
            _make_line(code_relance="0"),
            _make_line(code_relance="1"),
            _make_line(code_relance="1"),
            _make_line(code_relance="2"),
            _make_line(code_relance="4"),
        ]
    )
    _, stats = parse_fab_text(content, date(2026, 4, 12))
    assert stats["reminder_counts"]["0"] == 1
    assert stats["reminder_counts"]["1"] == 2
    assert stats["reminder_counts"]["2"] == 1
    assert stats["reminder_counts"]["4"] == 1


def test_empty_content():
    df, stats = parse_fab_text("", date(2026, 4, 12))
    assert df.empty
    assert stats["total"] == 0
