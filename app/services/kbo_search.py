"""Zoeken in de lokale KBO-database (tabel kbo_company)."""

import sqlite3

from ..config import DATABASE_PATH
from ..geo import zipcode_ranges_for_province


def _conn() -> sqlite3.Connection:
    conn = sqlite3.connect(DATABASE_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def kbo_loaded() -> bool:
    with _conn() as conn:
        row = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='kbo_company'"
        ).fetchone()
        return row is not None


def search_companies(
    nace: str = "",
    name: str = "",
    exact_name: bool = False,
    enterprise_number: str = "",
    zipcode: str = "",
    municipality: str = "",
    province: str = "",
    active_only: bool = True,
    limit: int = 500,
    offset: int = 0,
) -> tuple[list[dict], int]:
    """Zoek ondernemingen; retourneert (rijen, totaal aantal)."""
    if not kbo_loaded():
        return [], 0

    where = []
    params: list = []

    if nace:
        # NACE-prefix: '62' vindt 62010, 62020, ...
        where.append("main_nace LIKE ?")
        params.append(nace.replace(".", "").strip() + "%")
    if name:
        if exact_name:
            where.append("name = ? COLLATE NOCASE")
            params.append(name.strip())
        else:
            where.append("name LIKE ? COLLATE NOCASE")
            params.append(f"%{name.strip()}%")
    if enterprise_number:
        # Accepteert ondernemingsnummer én BTW-nummer: BE-prefix, punten en
        # spaties worden genegeerd; oude 9-cijferige nummers krijgen een 0 vooraan.
        num = enterprise_number.upper().replace("BTW", "").replace("BE", "") \
            .replace(".", "").replace(" ", "").strip()
        if len(num) == 9 and num.isdigit():
            num = "0" + num
        formatted = f"{num[0:4]}.{num[4:7]}.{num[7:10]}" if len(num) == 10 else enterprise_number
        where.append("(enterprise_number = ? OR enterprise_number = ? OR enterprise_number = ?)")
        params.extend([enterprise_number.strip(), formatted, num])
    if zipcode:
        where.append("zipcode = ?")
        params.append(zipcode.strip())
    if municipality:
        where.append("municipality LIKE ? COLLATE NOCASE")
        params.append(f"%{municipality.strip()}%")
    if province:
        ranges = zipcode_ranges_for_province(province)
        if ranges:
            clauses = " OR ".join("(CAST(zipcode AS INTEGER) BETWEEN ? AND ?)" for _ in ranges)
            where.append(f"({clauses})")
            for lo, hi in ranges:
                params.extend([lo, hi])
    if active_only:
        where.append("status = 'AC'")

    where_sql = " AND ".join(where) if where else "1=1"
    with _conn() as conn:
        (total,) = conn.execute(
            f"SELECT COUNT(*) FROM kbo_company WHERE {where_sql}", params
        ).fetchone()
        rows = conn.execute(
            f"SELECT * FROM kbo_company WHERE {where_sql} ORDER BY name LIMIT ? OFFSET ?",
            params + [limit, offset],
        ).fetchall()
    return [dict(r) for r in rows], total


def nace_description(code: str, language: str = "NL") -> str:
    if not code or not kbo_loaded():
        return ""
    with _conn() as conn:
        for category in ("Nace2025", "Nace2008", "Nace2003"):
            row = conn.execute(
                "SELECT Description FROM kbo_code WHERE Category = ? AND Code = ? AND Language = ?",
                (category, code, language),
            ).fetchone()
            if row:
                return row["Description"]
    return ""


def company_detail(enterprise_number: str) -> dict | None:
    if not kbo_loaded():
        return None
    with _conn() as conn:
        company = conn.execute(
            "SELECT * FROM kbo_company WHERE enterprise_number = ?", (enterprise_number,)
        ).fetchone()
        if not company:
            return None
        activities = conn.execute(
            "SELECT * FROM kbo_activity WHERE EntityNumber = ? ORDER BY Classification",
            (enterprise_number,),
        ).fetchall()
        addresses = conn.execute(
            "SELECT * FROM kbo_address WHERE EntityNumber = ?", (enterprise_number,)
        ).fetchall()
        denominations = conn.execute(
            "SELECT * FROM kbo_denomination WHERE EntityNumber = ?", (enterprise_number,)
        ).fetchall()
        establishments = conn.execute(
            "SELECT * FROM kbo_establishment WHERE EnterpriseNumber = ?", (enterprise_number,)
        ).fetchall()
    return {
        "company": dict(company),
        "activities": [dict(r) for r in activities],
        "addresses": [dict(r) for r in addresses],
        "denominations": [dict(r) for r in denominations],
        "establishments": [dict(r) for r in establishments],
    }
