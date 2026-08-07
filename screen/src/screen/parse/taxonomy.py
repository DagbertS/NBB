"""Validatie van rubriekcodes tegen de officiële NBB-taxonomie.

Bron van waarheid = een door de gebruiker van de NBB-site gedownloade
rubriekenlijst (data/raw/nbb/taxonomy/rubrics.csv, kolom 1 = code; zie
docs/SOURCES.md). Zolang dat bestand ontbreekt is er GEEN taxonomie en
worden codes niet stilzwijgend goedgekeurd: facts krijgen dan
code_in_taxonomy = null (onbekend), nooit true.

SEED_CODES is het vertrekpunt uit de projectspecificatie (§5) en is
uitdrukkelijk NIET de bron van waarheid — de test
test_seed_codes_in_official_taxonomy faalt als een seed-code niet in de
officiële taxonomie blijkt te zitten.
"""

import csv
from dataclasses import dataclass
from pathlib import Path

from ..config import RAW_DIR

DEFAULT_TAXONOMY_PATH = RAW_DIR / "nbb" / "taxonomy" / "rubrics.csv"

# Vertrekpunt uit de specificatie — te valideren, geen bron van waarheid.
SEED_CODES = {
    "10/15",   # eigen vermogen
    "20/58",   # balanstotaal
    "70",      # omzet (enkel volledig schema)
    "9900",    # brutomarge (verkort schema)
    "9901",    # bedrijfswinst/-verlies (EBIT)
    "62",      # bezoldigingen en sociale lasten
    "61",      # diensten en diverse goederen
    "630",     # afschrijvingen
    "631/4",   # waardeverminderingen
    "635/8",   # voorzieningen
    "170/4",   # LT financiële schulden
    "42",      # KT-deel LT-schulden
    "43",      # KT financiële schulden
    "50/53",   # geldbeleggingen
    "54/58",   # liquide middelen
    # Aanvullend voor normalize (fase 3) — even ongevalideerd als de rest:
    "60",      # handelsgoederen, grond- en hulpstoffen
    "3",       # voorraden en bestellingen in uitvoering
    "40",      # handelsvorderingen (<= 1 jaar)
    "44",      # handelsschulden
    "8169",    # aanschaffingen materiële vaste activa (toelichting)
    "9087",    # gemiddeld personeelsbestand in VTE (sociale balans)
}


class TaxonomyError(Exception):
    pass


@dataclass
class Taxonomy:
    codes: frozenset[str]
    source_path: str

    def __contains__(self, code: str) -> bool:
        return normalize_code(code) in self.codes

    def unknown(self, codes) -> list[str]:
        return sorted({c for c in codes if c not in self})


def normalize_code(code: str) -> str:
    """Rubriekcodes uniformeren: spaties weg, zonder leidende nullen-varianten."""
    return str(code).strip().replace(" ", "")


def load_taxonomy(path: str | Path | None = None) -> Taxonomy | None:
    """Laad de officiële rubriekenlijst. None als het bestand (nog) ontbreekt."""
    path = Path(path) if path else DEFAULT_TAXONOMY_PATH
    if not path.exists():
        return None
    codes: set[str] = set()
    with open(path, newline="", encoding="utf-8-sig") as f:
        for row in csv.reader(f):
            if not row:
                continue
            code = normalize_code(row[0])
            if not code or code.lower() in ("code", "rubriek", "rubric"):
                continue  # kopregel
            codes.add(code)
    if not codes:
        raise TaxonomyError(f"Taxonomiebestand {path} bevat geen codes")
    return Taxonomy(codes=frozenset(codes), source_path=str(path))
