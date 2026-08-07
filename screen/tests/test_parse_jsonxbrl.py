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
