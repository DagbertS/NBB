"""Geografie (postcode -> provincie) en de NACE-conversiebrug 2008<->2025."""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from screen.peers.geo import province_for_zipcode  # noqa: E402
import screen.peers.nace_bridge as nb  # noqa: E402


@pytest.mark.parametrize("zipcode,province", [
    ("3500", "Limburg"), ("2000", "Antwerpen"), ("9000", "Oost-Vlaanderen"),
    ("1000", "Brussel"), ("3000", "Vlaams-Brabant"), ("6600", "Luxemburg"),
    ("7000", "Henegouwen"), ("", ""), (None, ""), ("abc", ""), ("0999", ""),
])
def test_province_for_zipcode(zipcode, province):
    assert province_for_zipcode(zipcode) == province


def make_bridge_csv(tmp_path) -> Path:
    p = tmp_path / "conversion_2008_2025.csv"
    p.write_text(
        "nace_2008,nace_2025\n"
        "37000,37000\n"
        "43221,43221\n"
        "43221,43222\n"      # 1-op-veel
        "10710,10710\n"
    )
    return p


def test_bridge_one_to_many(tmp_path):
    bridge = nb.load_bridge(make_bridge_csv(tmp_path))
    assert bridge.is_one_to_many("37000") is False
    assert bridge.is_one_to_many("43221") is True
    assert bridge.is_one_to_many("43.221") is True     # met punten
    assert bridge.is_one_to_many("99999") is None      # niet in tabel: onbekend
    assert bridge.targets_2025("43221") == ["43221", "43222"]


def test_bridge_missing_file_is_none(tmp_path):
    assert nb.load_bridge(tmp_path / "nope.csv") is None


def test_bridge_unrecognizable_columns(tmp_path):
    p = tmp_path / "conversion_2008_2025.csv"
    p.write_text("a,b\n1,2\n")
    with pytest.raises(nb.BridgeError, match="2008"):
        nb.load_bridge(p)
