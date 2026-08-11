"""JSON-XBRL-parser: correcte tidy rijen, strikte fouten, geen stille gok."""

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import screen.parse.jsonxbrl as jx  # noqa: E402

FIXTURES = Path(__file__).parent / "fixtures" / "nbb"


def test_parse_full_deposit():
    rows = jx.parse_deposit(FIXTURES / "dep_full_2023.json")
    assert len(rows) == 6
    r = {(row["rubric_code"], row["period"]): row for row in rows}

    eigen_vermogen = r[("10/15", "N")]
    assert eigen_vermogen["value"] == 500000.0
    assert eigen_vermogen["enterprise_number"] == "0123456789"  # punten weg
    assert eigen_vermogen["fiscal_year"] == 2023                # uit ExerciseDates.EndDate
    assert eigen_vermogen["schema_type"] == "volledig"          # m01-f
    assert eigen_vermogen["deposit_date"] == "2024-07-10"
    assert eigen_vermogen["source"] == "nbb"
    assert eigen_vermogen["as_of"] == "2024-07-10"

    # vorig boekjaar als aparte periode-rij, niet weggegooid
    assert r[("10/15", "NM1")]["value"] == 450000.0


def test_parse_abbreviated_schema():
    rows = jx.parse_deposit(FIXTURES / "dep_abbr_2023.json")
    assert rows[0]["schema_type"] == "verkort"  # m02-a
    codes = {row["rubric_code"] for row in rows}
    assert "9900" in codes and "70" not in codes  # geen omzet in verkort


def test_corrupt_json_raises_parse_error():
    with pytest.raises(jx.ParseError, match="JSON"):
        jx.parse_deposit(FIXTURES / "dep_kapot.json")


def test_missing_required_field(tmp_path):
    p = tmp_path / "d.json"
    p.write_text(json.dumps({"ReferenceNumber": "x", "EnterpriseNumber": "0123456789"}))
    with pytest.raises(jx.ParseError, match="EndDate|ExerciseDates"):
        jx.parse_deposit(p)


def test_invalid_enterprise_number(tmp_path):
    p = tmp_path / "d.json"
    p.write_text(json.dumps({
        "ReferenceNumber": "x", "EnterpriseNumber": "12AB",
        "ExerciseDates": {"EndDate": "2023-12-31"}, "Rubrics": [],
    }))
    with pytest.raises(jx.ParseError, match="[Oo]ndernemingsnummer"):
        jx.parse_deposit(p)


def test_non_numeric_value_raises(tmp_path):
    p = tmp_path / "d.json"
    p.write_text(json.dumps({
        "ReferenceNumber": "x", "EnterpriseNumber": "0123456789",
        "DepositDate": "2024-01-01", "ModelType": "m01-f",
        "ExerciseDates": {"EndDate": "2023-12-31"},
        "Rubrics": [{"Code": "70", "Value": "n.v.t."}],
    }))
    with pytest.raises(jx.ParseError, match="niet numeriek"):
        jx.parse_deposit(p)


@pytest.mark.parametrize("model,expected", [
    ("m01-f", "volledig"), ("m02-a", "verkort"), ("m03-m", "micro"),
    ("Full", "volledig"), ("iets-anders", "unknown"),
])
def test_classify_schema(model, expected):
    assert jx.classify_schema(model) == expected


def test_parse_unwraps_nested_document(tmp_path):
    """Echte CBSO-antwoorden kunnen het document inpakken (lijst of wrapper-
    object); de parser vindt het EnterpriseNumber-object zelf."""
    inner = json.loads((FIXTURES / "dep_full_2023.json").read_text())
    for wrapped in ([inner], {"Content": inner}, {"Deposits": [inner]}):
        p = tmp_path / "wrapped.json"
        p.write_text(json.dumps(wrapped))
        rows = jx.parse_deposit(p)
        assert rows and rows[0]["enterprise_number"] == "0123456789"


def test_parse_unknown_format_reports_structure(tmp_path):
    """Bij een onbekend formaat vertelt de fout de wérkelijke structuur,
    zodat de eerste echte neerlegging ons het officiële schema leert."""
    p = tmp_path / "oim.json"
    p.write_text(json.dumps({
        "documentInfo": {"documentType": "https://xbrl.org/2021/xbrl-json"},
        "facts": {"f1": {"value": "500000", "dimensions": {
            "concept": "pfs:Equity", "entity": "scheme:0435808429",
            "period": "2024-01-01T00:00:00/2025-01-01T00:00:00"}}},
    }))
    with pytest.raises(jx.ParseError) as exc:
        jx.parse_deposit(p)
    msg = str(exc.value)
    assert "Werkelijke structuur" in msg
    assert "documentInfo" in msg and "concept" in msg


def _real_cbso_body():
    """Nagebootst naar de live vastgestelde structuur (2026-08-11):
    geen EnterpriseNumber/ExerciseDates/ModelType in het bestand zelf."""
    return {
        "ReferenceNumber": "2026-00298256",
        "EnterpriseName": "AQUA JET VLERICK",
        "Address": {"City": "Nazareth"},
        "LegalForm": "610",
        "JointCommittees": [],
        "Rubrics": [
            {"Code": "9900", "Period": "N", "Value": "1961298.0"},
            {"Code": "9901", "Period": "N", "Value": "364440.0"},
            {"Code": "9900", "Period": "NM1", "Value": "2012785.0"},
        ],
        "Administrators": [],
        "Shareholders": [],
    }


def test_parse_real_cbso_format_enriched(tmp_path):
    """De webapp verrijkt de kopie met metadata; de parser leest hem dan."""
    body = {"EnterpriseNumber": "0435808429", "DepositDate": "2026-07-15",
            "ModelType": "m81-f", "ExerciseDates": {"EndDate": "2025-12-31"},
            **_real_cbso_body()}
    p = tmp_path / "0435808429_2026-00298256.json"
    p.write_text(json.dumps(body))
    rows = jx.parse_deposit(p)
    assert len(rows) == 3
    r = {(row["rubric_code"], row["period"]): row for row in rows}
    assert r[("9900", "N")]["value"] == 1961298.0
    assert r[("9900", "N")]["enterprise_number"] == "0435808429"
    assert r[("9900", "N")]["fiscal_year"] == 2025
    assert r[("9900", "NM1")]["value"] == 2012785.0


def test_parse_real_format_number_from_filename(tmp_path):
    """Zonder verrijking: nummer uit de bestandsnaam, maar boekjaar blijft
    verplicht — met een duidelijke uitleg."""
    p = tmp_path / "0435808429_2026-00298256.json"
    p.write_text(json.dumps(_real_cbso_body()))
    with pytest.raises(jx.ParseError, match="ExerciseDates"):
        jx.parse_deposit(p)


def test_parse_rubric_without_code_reports_fields(tmp_path):
    body = {"EnterpriseNumber": "0435808429",
            "ExerciseDates": {"EndDate": "2025-12-31"},
            "Rubrics": [{"Concept": "x", "Amount": "1"}]}
    p = tmp_path / "d.json"
    p.write_text(json.dumps(body))
    with pytest.raises(jx.ParseError, match="werkelijke rubriekvelden"):
        jx.parse_deposit(p)
