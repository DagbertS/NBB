"""NACE-BEL-brug 2008 <-> 2025 (spec §6).

Sinds 01/01/2025 geldt NACE Rev. 2.1; oude codes zijn automatisch
geconverteerd en bij een 1-op-veel-mapping werd er slechts één toegekend.
Elke onderneming waarvan de 2008-code zo'n 1-op-veel-mapping had, krijgt
een vlag: haar toegewezen 2025-code kan de verkeerde tak zijn.

Bron: de Statbel/FOD-conversietabel (docs/SOURCES.md). Deze loader leest
een CSV- of parquet-versie met kolommen die '2008' en '2025' in de naam
dragen (kolomnaam-tolerant, want het officiële bestand is nog unverified).
Zolang de tabel ontbreekt is de brug None en blijft de vlag null (onbekend)
— nooit stilzwijgend 'geen probleem'.
"""

from dataclasses import dataclass
from pathlib import Path

import polars as pl

from ..config import RAW_DIR

DEFAULT_BRIDGE_CANDIDATES = (
    RAW_DIR / "statbel" / "conversion_2008_2025.csv",
    RAW_DIR / "statbel" / "conversion_2008_2025.parquet",
)


class BridgeError(Exception):
    pass


def _normalize(code: str) -> str:
    return str(code).replace(".", "").replace(" ", "").strip()


@dataclass
class NaceBridge:
    mapping: dict[str, list[str]]   # 2008-code -> [2025-codes]
    source_path: str

    def targets_2025(self, code_2008: str) -> list[str]:
        return self.mapping.get(_normalize(code_2008), [])

    def is_one_to_many(self, code_2008: str) -> bool | None:
        """True/False als de 2008-code in de tabel zit, None als onbekend."""
        targets = self.targets_2025(code_2008)
        if not targets:
            return None
        return len(set(targets)) > 1


def _find_columns(columns: list[str]) -> tuple[str, str]:
    col_2008 = next((c for c in columns if "2008" in c), None)
    col_2025 = next((c for c in columns if "2025" in c), None)
    if not (col_2008 and col_2025):
        raise BridgeError(
            f"Conversietabel mist herkenbare kolommen (gevonden: {columns}) — "
            "verwacht kolomnamen met '2008' en '2025'"
        )
    return col_2008, col_2025


def load_bridge(path: str | Path | None = None) -> NaceBridge | None:
    """Laad de conversietabel. None als er (nog) geen bestand is."""
    candidates = [Path(path)] if path else [Path(p) for p in DEFAULT_BRIDGE_CANDIDATES]
    found = next((p for p in candidates if p.exists()), None)
    if not found:
        return None

    if found.suffix == ".parquet":
        df = pl.read_parquet(found)
    else:
        df = pl.read_csv(found, infer_schema=False)
    col_2008, col_2025 = _find_columns(df.columns)

    mapping: dict[str, list[str]] = {}
    for code_2008, code_2025 in df.select(col_2008, col_2025).iter_rows():
        if code_2008 is None or code_2025 is None:
            continue
        mapping.setdefault(_normalize(code_2008), []).append(_normalize(code_2025))
    if not mapping:
        raise BridgeError(f"Conversietabel {found} bevat geen bruikbare rijen")
    return NaceBridge(mapping=mapping, source_path=str(found))
