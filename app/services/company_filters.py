"""Filters op bedrijfskenmerken voor de Analyses-pagina en de
longlist-detailpagina: VTE en EBITDA (proxy) met een operator (< <= = >= >)
plus een getal, en provincie.

De cijfers komen uit het meest recente boekjaar met een waarde in
metrics.parquet (de machinaal geparste pipeline-kerncijfers); de provincie
uit de KBO-zetelpostcode. Eerlijkheidsregel: een bedrijf zónder cijfer kan
nooit aan een numerieke filter voldoen — het valt dan buiten de selectie,
en de kolom toont "—" zodat zichtbaar blijft dat het cijfer ontbreekt.
"""

import sqlite3

from ..config import DATABASE_PATH
from ..geo import PROVINCES, province_for_zipcode

OPERATORS = ["<", "<=", "=", ">=", ">"]

__all__ = ["OPERATORS", "PROVINCES", "lookup", "parse_filters", "matches",
           "fmt_fte", "fmt_eur"]


def _normalize(number: str) -> str:
    return (number or "").upper().replace("BTW", "").replace("BE", "") \
        .replace(".", "").replace(" ", "").strip()


def lookup(numbers) -> dict[str, dict]:
    """{genormaliseerd nummer: {"fte", "ebitda", "province"}} voor een hele
    set bedrijven in één keer: één scan over metrics.parquet en één
    KBO-query, geen lookup per rij."""
    import polars as pl

    from .screening import pipeline_config

    nums = sorted({_normalize(n) for n in numbers if n})
    out: dict[str, dict] = {n: {"fte": None, "ebitda": None, "province": ""}
                            for n in nums}
    if not nums:
        return out

    metrics_path = pipeline_config.INTERIM_DIR / "metrics.parquet"
    if metrics_path.exists():
        df = pl.read_parquet(
            metrics_path,
            columns=["enterprise_number", "fiscal_year", "fte", "ebitda_proxy"],
        )
        df = (df.filter(pl.col("enterprise_number").is_in(nums))
                .sort("fiscal_year", descending=True))
        # meest recente boekjaar mét waarde wint, per metric apart
        for row in df.iter_rows(named=True):
            entry = out.get(_normalize(str(row["enterprise_number"])))
            if entry is None:
                continue
            if entry["fte"] is None and row["fte"] is not None:
                entry["fte"] = float(row["fte"])
            if entry["ebitda"] is None and row["ebitda_proxy"] is not None:
                entry["ebitda"] = float(row["ebitda_proxy"])

    # provincie via de KBO-zetelpostcode (nummer staat daar soms met punten)
    variants = []
    for n in nums:
        variants.append(n)
        if len(n) == 10:
            variants.append(f"{n[0:4]}.{n[4:7]}.{n[7:10]}")
    try:
        with sqlite3.connect(DATABASE_PATH) as conn:
            for i in range(0, len(variants), 400):
                chunk = variants[i:i + 400]
                rows = conn.execute(
                    "SELECT enterprise_number, zipcode FROM kbo_company "
                    f"WHERE enterprise_number IN ({','.join('?' * len(chunk))})",
                    chunk,
                ).fetchall()
                for number, zipcode in rows:
                    n = _normalize(str(number))
                    if n in out and zipcode:
                        out[n]["province"] = province_for_zipcode(str(zipcode))
    except sqlite3.OperationalError:
        pass   # KBO-tabel nog niet geladen — provincie blijft leeg
    return out


def parse_filters(params) -> dict:
    """Filtervelden uit de query-parameters; ongeldige invoer telt gewoon
    niet mee (geen foutpagina om een tikfout)."""
    def _num(value):
        import re

        s = str(value or "").strip().replace(" ", "")
        if not s:
            return None
        if "," in s:
            # Belgische notatie: punt = duizendtal, komma = decimaal
            s = s.replace(".", "").replace(",", ".")
        elif re.fullmatch(r"-?\d{1,3}(\.\d{3})+", s):
            s = s.replace(".", "")   # 250.000 → 250000
        try:
            return float(s)
        except ValueError:
            return None

    def _op(value):
        return value if value in OPERATORS else ">="

    filters = {
        "vte_op": _op(params.get("vte_op", "")),
        "vte": _num(params.get("vte", "")),
        "ebitda_op": _op(params.get("ebitda_op", "")),
        "ebitda": _num(params.get("ebitda", "")),
        "province": params.get("province", "").strip()
        if params.get("province", "").strip() in PROVINCES else "",
    }
    filters["active"] = (filters["vte"] is not None
                         or filters["ebitda"] is not None
                         or bool(filters["province"]))
    return filters


def _cmp(value, op: str, target: float) -> bool:
    if value is None:
        return False
    if op == "<":
        return value < target
    if op == "<=":
        return value <= target
    if op == ">=":
        return value >= target
    if op == ">":
        return value > target
    return value == target


def matches(entry: dict | None, filters: dict,
            province_override: str = "") -> bool:
    """Voldoet één bedrijf aan de actieve filters? province_override (bv. de
    postcode-provincie van een lijstitem) wint van de KBO-provincie."""
    entry = entry or {}
    if filters["vte"] is not None and \
            not _cmp(entry.get("fte"), filters["vte_op"], filters["vte"]):
        return False
    if filters["ebitda"] is not None and \
            not _cmp(entry.get("ebitda"), filters["ebitda_op"],
                     filters["ebitda"]):
        return False
    if filters["province"]:
        province = province_override or entry.get("province") or ""
        if province != filters["province"]:
            return False
    return True


def fmt_fte(value) -> str:
    if value is None:
        return "—"
    return f"{value:,.1f}".rstrip("0").rstrip(".").replace(",", " ")


def fmt_eur(value) -> str:
    if value is None:
        return "—"
    return "€ " + f"{value:,.0f}".replace(",", ".")
