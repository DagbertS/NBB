"""Signalen uit de NBB-neerlegging zelf (spec §7) — vaak over het hoofd gezien.

Elke signaalrij draagt: enterprise_number, fiscal_year, signal, value,
source, as_of, kind ('derived' — afgeleid uit officiële neerleggings-
metadata), note. Aannames staan in docs/METHODOLOGY.md.

Deadline-aanname: neerlegging uiterlijk 7 maanden na afsluiting van het
boekjaar (30 dagen na de algemene vergadering, die uiterlijk 6 maanden na
afsluiting valt). Gemarkeerd als aanname; wettelijke basis te verifiëren
in de definitieve METHODOLOGY-review.

Commissariswissel (spec §7) is hier bewust NIET geïmplementeerd: dat
vereist toelichtingsrubrieken die eerst tegen de officiële taxonomie
gevalideerd moeten worden — gedocumenteerd in METHODOLOGY in plaats van
half gedetecteerd.
"""

from datetime import date

import polars as pl

DEADLINE_MONTHS = 7
NORMAL_YEAR_RANGE = (350, 380)  # dagen; daarbuiten = afwijkend boekjaar
SOURCE = "nbb-filing-metadata"


def add_months(d: date, months: int) -> date:
    month = d.month - 1 + months
    year = d.year + month // 12
    month = month % 12 + 1
    # dagoverloop netjes afkappen (31 jan + 1m -> 28/29 feb)
    for day in (d.day, 30, 29, 28):
        try:
            return date(year, month, day)
        except ValueError:
            continue
    raise ValueError(f"onmogelijke datum: {year}-{month}")


def _parse_date(value: str | None) -> date | None:
    try:
        return date.fromisoformat(str(value)[:10])
    except (ValueError, TypeError):
        return None


def _deposits(facts: pl.DataFrame) -> pl.DataFrame:
    """Eén rij per neerlegging (uit de feitrijen)."""
    return (
        facts.group_by(["enterprise_number", "fiscal_year", "deposit_reference"])
        .agg(
            pl.col("schema_type").first(),
            pl.col("deposit_date").first(),
            pl.col("exercise_start").first(),
            pl.col("exercise_end").first(),
            pl.col("is_latest").first(),
            pl.col("superseded").first(),
        )
        .sort(["enterprise_number", "fiscal_year"])
    )


def _signal(enterprise: str, year: int | None, signal: str, value: str,
            as_of: str, note: str = "") -> dict:
    return {
        "enterprise_number": enterprise,
        "fiscal_year": year,
        "signal": signal,
        "value": value,
        "source": SOURCE,
        "as_of": as_of,
        "kind": "derived",
        "note": note,
    }


def late_filing_signals(deposits: pl.DataFrame) -> list[dict]:
    """Dagen te laat t.o.v. de deadline (afsluiting + 7 maanden), en
    chronische laattijdigheid (>= 2 van de laatste 3 boekjaren te laat)."""
    signals: list[dict] = []
    late_years: dict[str, dict[int, int]] = {}

    for row in deposits.filter(pl.col("is_latest")).iter_rows(named=True):
        end = _parse_date(row["exercise_end"])
        deposited = _parse_date(row["deposit_date"])
        if not (end and deposited):
            continue
        deadline = add_months(end, DEADLINE_MONTHS)
        days_late = (deposited - deadline).days
        if days_late > 0:
            signals.append(_signal(
                row["enterprise_number"], row["fiscal_year"], "late_filing",
                str(days_late), row["deposit_date"],
                note=f"deadline {deadline.isoformat()} (afsluiting + {DEADLINE_MONTHS}m, aanname)",
            ))
            late_years.setdefault(row["enterprise_number"], {})[row["fiscal_year"]] = days_late

    # chronisch: >= 2 te late boekjaren binnen de laatste 3 aanwezige jaren
    all_years = (
        deposits.filter(pl.col("is_latest"))
        .group_by("enterprise_number")
        .agg(pl.col("fiscal_year").unique().sort().alias("years"))
    )
    for row in all_years.iter_rows(named=True):
        recent = row["years"][-3:]
        late = [y for y in recent if y in late_years.get(row["enterprise_number"], {})]
        if len(late) >= 2:
            signals.append(_signal(
                row["enterprise_number"], None, "chronic_late_filing",
                f"{len(late)} van laatste {len(recent)} boekjaren te laat",
                as_of=str(max(recent)), note=f"boekjaren: {late}",
            ))
    return signals


def model_switch_signals(deposits: pl.DataFrame) -> list[dict]:
    """Wissel volledig -> verkort/micro tussen opeenvolgende boekjaren
    (omzet verbergen vóór verkoop is een klassiek motief)."""
    signals = []
    latest = deposits.filter(pl.col("is_latest")).sort("fiscal_year")
    for (enterprise,), sub in latest.group_by(["enterprise_number"]):
        rows = sub.sort("fiscal_year").iter_rows(named=True)
        prev = None
        for row in rows:
            if prev is not None and prev["schema_type"] == "volledig" \
                    and row["schema_type"] in ("verkort", "micro"):
                signals.append(_signal(
                    enterprise, row["fiscal_year"], "model_switch_full_to_abbreviated",
                    f"{prev['fiscal_year']}: volledig -> {row['fiscal_year']}: {row['schema_type']}",
                    row["deposit_date"],
                ))
            prev = row
    return signals


def correction_signals(deposits: pl.DataFrame) -> list[dict]:
    """Verbeterende neerleggingen: er bestaan verdrongen (superseded)
    neerleggingen voor hetzelfde boekjaar."""
    signals = []
    corrected = (
        deposits.filter(pl.col("superseded"))
        .group_by(["enterprise_number", "fiscal_year"])
        .agg(pl.col("deposit_reference").alias("refs"), pl.len().alias("n"))
    )
    latest_dates = (
        deposits.filter(pl.col("is_latest"))
        .select("enterprise_number", "fiscal_year", "deposit_date")
    )
    corrected = corrected.join(latest_dates, on=["enterprise_number", "fiscal_year"], how="left")
    for row in corrected.iter_rows(named=True):
        signals.append(_signal(
            row["enterprise_number"], row["fiscal_year"], "correction_filing",
            f"{row['n']} eerdere neerlegging(en) vervangen",
            row["deposit_date"] or "", note=f"vervangen referenties: {row['refs']}",
        ))
    return signals


def fiscal_year_signals(deposits: pl.DataFrame) -> list[dict]:
    """Boekjaarwijzigingen: afwijkende boekjaarlengte, of een verschoven
    afsluitmaand tussen opeenvolgende boekjaren."""
    signals = []
    latest = deposits.filter(pl.col("is_latest")).sort("fiscal_year")
    for (enterprise,), sub in latest.group_by(["enterprise_number"]):
        prev_end = None
        for row in sub.sort("fiscal_year").iter_rows(named=True):
            start = _parse_date(row["exercise_start"])
            end = _parse_date(row["exercise_end"])
            if start and end:
                length = (end - start).days + 1
                lo, hi = NORMAL_YEAR_RANGE
                if not lo <= length <= hi:
                    signals.append(_signal(
                        enterprise, row["fiscal_year"], "irregular_fiscal_year_length",
                        f"{length} dagen", row["deposit_date"],
                        note=f"{row['exercise_start']} t/m {row['exercise_end']}",
                    ))
            if end and prev_end and end.month != prev_end.month:
                signals.append(_signal(
                    enterprise, row["fiscal_year"], "fiscal_year_end_changed",
                    f"afsluitmaand {prev_end.month} -> {end.month}",
                    row["deposit_date"],
                ))
            prev_end = end or prev_end
    return signals


def build_filing_signals(facts: pl.DataFrame) -> list[dict]:
    deposits = _deposits(facts)
    return (
        late_filing_signals(deposits)
        + model_switch_signals(deposits)
        + correction_signals(deposits)
        + fiscal_year_signals(deposits)
    )
