"""Handmatig ingevoerde en externe signalen (spec §7).

Voor bronnen die (nog) niet legitiem geautomatiseerd kunnen worden —
Staatsblad-publicaties, vergunningsdossiers, RSZ/fiscus-dagvaardingen,
opvolgings- en pensioensignalen, lokale pers — is dit de invoerinterface:
een YAML-bestand met één lijst van signalen.

Strikte regel (spec §7): elk signaal MOET een `source` en `as_of` dragen.
Ontbreekt een van beide, dan wordt het bestand geweigerd met een leesbare
fout — geen ongesourcete vlaggen in de datamodellen.

Voorbeeld (zie ook screen/manual_signals.example.yaml):

  - enterprise_number: "0123456789"
    signal: bestuurderswissel
    value: "2 nieuwe bestuurders benoemd"
    source: "Belgisch Staatsblad 2026-05-12, publicatie 2026xxxxx"
    as_of: "2026-05-12"
    note: "opvolging mogelijk in gang gezet"
  - enterprise_number: "0123456789"
    signal: opvolgingssignaal
    value: "zaakvoerder 64 jaar, geen familiale opvolging bekend"
    source: "gesprek sectorcontact"
    as_of: "2026-06-01"
"""

from pathlib import Path

import yaml

REQUIRED_FIELDS = ("enterprise_number", "signal", "source", "as_of")


class ManualSignalError(Exception):
    pass


def load_manual_signals(path: str | Path) -> list[dict]:
    path = Path(path)
    if not path.exists():
        return []
    raw = yaml.safe_load(path.read_text())
    if raw is None:
        return []
    if not isinstance(raw, list):
        raise ManualSignalError(
            f"{path.name}: verwacht een YAML-lijst van signalen, geen {type(raw).__name__}"
        )

    signals = []
    for i, entry in enumerate(raw):
        if not isinstance(entry, dict):
            raise ManualSignalError(f"{path.name}: item {i} is geen mapping")
        missing = [f for f in REQUIRED_FIELDS if not str(entry.get(f, "")).strip()]
        if missing:
            raise ManualSignalError(
                f"{path.name}: item {i} ({entry.get('signal', '?')}) mist verplichte "
                f"velden {missing} — elk signaal vereist een source en as_of"
            )
        signals.append({
            "enterprise_number": str(entry["enterprise_number"]).replace(".", "").strip(),
            "fiscal_year": int(entry["fiscal_year"]) if entry.get("fiscal_year") else None,
            "signal": str(entry["signal"]).strip(),
            "value": str(entry.get("value", "")).strip(),
            "source": str(entry["source"]).strip(),
            "as_of": str(entry["as_of"]).strip(),
            "kind": "manual",
            "note": str(entry.get("note", "")).strip(),
        })
    return signals
