"""Thesis = vrij invulbare zoekcriteria; de loader moet elk redelijk dossier
accepteren en onzin met een leesbare fout weigeren."""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

# Module-referentie i.p.v. from-import: andere fixtures herladen screen.config,
# en een direct geïmporteerde exception-klasse zou dan een oude versie zijn.
import screen.config as config  # noqa: E402

EXAMPLE = Path(__file__).resolve().parents[1] / "thesis.yaml"


def load_thesis(path):
    return config.load_thesis(path)


def write(tmp_path, text: str) -> Path:
    p = tmp_path / "thesis.yaml"
    p.write_text(text)
    return p


def test_example_thesis_loads():
    t = load_thesis(EXAMPLE)
    assert t.name == "ruiming-riolering-vlaanderen"
    assert "37.00" in t.nace_codes
    assert "3700" in t.nace_prefixes  # prefix zonder punt
    assert "Antwerpen" in t.provinces
    assert t.size.balance_total_min == 500000
    assert abs(sum(t.weights.values()) - 1.0) < 1e-9


def test_other_dossier_freely_fillable(tmp_path):
    """Een compleet ander dossier moet zonder codewijziging laden."""
    p = write(tmp_path, """
name: bakkerijen-west-vlaanderen
nace_version: 2008
nace_codes: ["10.71"]
geography:
  provinces: [West-Vlaanderen]
size:
  fte_min: 3
  fte_max: 40
""")
    t = load_thesis(p)
    assert t.name == "bakkerijen-west-vlaanderen"
    assert t.nace_version == 2008
    assert t.size.balance_total_min is None  # optioneel criterium


def test_missing_nace_rejected(tmp_path):
    p = write(tmp_path, "name: leeg\nnace_codes: []\n")
    with pytest.raises(config.ThesisError, match="NACE"):
        load_thesis(p)


def test_unknown_province_rejected(tmp_path):
    p = write(tmp_path, """
name: fout
nace_codes: ["37.00"]
geography:
  provinces: [Zeeland]
""")
    with pytest.raises(config.ThesisError, match="Zeeland"):
        load_thesis(p)


def test_inverted_size_rejected(tmp_path):
    p = write(tmp_path, """
name: fout
nace_codes: ["37.00"]
size: {fte_min: 50, fte_max: 5}
""")
    with pytest.raises(config.ThesisError, match="fte"):
        load_thesis(p)


def test_weights_must_sum_to_one(tmp_path):
    p = write(tmp_path, """
name: fout
nace_codes: ["37.00"]
weights: {financial: 0.5, signals: 0.2}
""")
    with pytest.raises(config.ThesisError, match="1.0"):
        load_thesis(p)
