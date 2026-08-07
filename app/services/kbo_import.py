"""Import van de KBO Open Data Full-zip (KboOpenData_<nr>_<jaar>_<maand>_Full.zip).

De zip bevat CSV's die op EnterpriseNumber aan elkaar gekoppeld worden:
  enterprise.csv, activity.csv, address.csv, denomination.csv,
  establishment.csv, code.csv, meta.csv

We laden de relevante bestanden in ruwe SQLite-tabellen (kbo_*) met indexen
op de zoekvelden, plus een gedenormaliseerde tabel `kbo_company` waarmee de
applicatie snel kan zoeken op NACE-code, naam, postcode, gemeente en provincie.
"""

import csv
import io
import sqlite3
import sys
import zipfile
from pathlib import Path

from ..config import DATABASE_PATH
from ..geo import province_for_zipcode

BATCH = 20000

# bestandsnaam in zip -> (tabelnaam, kolommen die we bewaren)
FILES = {
    "enterprise.csv": ("kbo_enterprise", ["EnterpriseNumber", "Status", "JuridicalSituation", "TypeOfEnterprise", "JuridicalForm", "StartDate"]),
    "activity.csv": ("kbo_activity", ["EntityNumber", "ActivityGroup", "NaceVersion", "NaceCode", "Classification"]),
    "address.csv": ("kbo_address", ["EntityNumber", "TypeOfAddress", "Zipcode", "MunicipalityNL", "MunicipalityFR", "StreetNL", "HouseNumber"]),
    "denomination.csv": ("kbo_denomination", ["EntityNumber", "Language", "TypeOfDenomination", "Denomination"]),
    "establishment.csv": ("kbo_establishment", ["EstablishmentNumber", "StartDate", "EnterpriseNumber"]),
    "code.csv": ("kbo_code", ["Category", "Code", "Language", "Description"]),
}


def _open_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(DATABASE_PATH)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=OFF")
    return conn


def _import_csv(conn: sqlite3.Connection, zf: zipfile.ZipFile, member: str, table: str, columns: list[str]) -> int:
    conn.execute(f"DROP TABLE IF EXISTS {table}")
    cols_sql = ", ".join(f'"{c}" TEXT' for c in columns)
    conn.execute(f"CREATE TABLE {table} ({cols_sql})")

    placeholders = ", ".join("?" for _ in columns)
    insert_sql = f"INSERT INTO {table} VALUES ({placeholders})"

    count = 0
    with zf.open(member) as raw:
        reader = csv.DictReader(io.TextIOWrapper(raw, encoding="utf-8-sig"))
        batch = []
        for row in reader:
            batch.append(tuple(row.get(c, "") or "" for c in columns))
            if len(batch) >= BATCH:
                conn.executemany(insert_sql, batch)
                count += len(batch)
                batch = []
        if batch:
            conn.executemany(insert_sql, batch)
            count += len(batch)
    conn.commit()
    return count


def _create_indexes(conn: sqlite3.Connection) -> None:
    stmts = [
        "CREATE INDEX IF NOT EXISTS ix_ent_num ON kbo_enterprise(EnterpriseNumber)",
        "CREATE INDEX IF NOT EXISTS ix_act_num ON kbo_activity(EntityNumber)",
        "CREATE INDEX IF NOT EXISTS ix_act_nace ON kbo_activity(NaceCode)",
        "CREATE INDEX IF NOT EXISTS ix_addr_num ON kbo_address(EntityNumber)",
        "CREATE INDEX IF NOT EXISTS ix_addr_zip ON kbo_address(Zipcode)",
        "CREATE INDEX IF NOT EXISTS ix_den_num ON kbo_denomination(EntityNumber)",
        "CREATE INDEX IF NOT EXISTS ix_est_ent ON kbo_establishment(EnterpriseNumber)",
        "CREATE INDEX IF NOT EXISTS ix_code ON kbo_code(Category, Code, Language)",
    ]
    for s in stmts:
        conn.execute(s)
    conn.commit()


def _build_company_table(conn: sqlite3.Connection) -> int:
    """Gedenormaliseerde zoektabel: 1 rij per onderneming."""
    conn.execute("DROP TABLE IF EXISTS kbo_company")
    conn.execute(
        """
        CREATE TABLE kbo_company AS
        SELECT
            e.EnterpriseNumber   AS enterprise_number,
            e.Status             AS status,
            e.JuridicalForm      AS juridical_form,
            e.StartDate          AS start_date,
            (SELECT d.Denomination FROM kbo_denomination d
              WHERE d.EntityNumber = e.EnterpriseNumber
              ORDER BY CASE d.TypeOfDenomination WHEN '001' THEN 0 ELSE 1 END
              LIMIT 1)           AS name,
            (SELECT a.Zipcode FROM kbo_address a
              WHERE a.EntityNumber = e.EnterpriseNumber LIMIT 1)       AS zipcode,
            (SELECT COALESCE(NULLIF(a.MunicipalityNL, ''), a.MunicipalityFR)
               FROM kbo_address a
              WHERE a.EntityNumber = e.EnterpriseNumber LIMIT 1)       AS municipality,
            (SELECT act.NaceCode FROM kbo_activity act
              WHERE act.EntityNumber = e.EnterpriseNumber
                AND act.Classification = 'MAIN'
              ORDER BY act.NaceVersion DESC LIMIT 1)                   AS main_nace,
            ''                   AS province
        FROM kbo_enterprise e
        """
    )
    # provincie afleiden uit postcode
    conn.execute("SELECT enterprise_number, zipcode FROM kbo_company")
    cur = conn.execute("SELECT DISTINCT zipcode FROM kbo_company WHERE zipcode != ''")
    for (zipcode,) in cur.fetchall():
        prov = province_for_zipcode(zipcode)
        if prov:
            conn.execute("UPDATE kbo_company SET province = ? WHERE zipcode = ?", (prov, zipcode))
    conn.execute("CREATE INDEX ix_comp_nace ON kbo_company(main_nace)")
    conn.execute("CREATE INDEX ix_comp_zip ON kbo_company(zipcode)")
    conn.execute("CREATE INDEX ix_comp_name ON kbo_company(name)")
    conn.execute("CREATE INDEX ix_comp_prov ON kbo_company(province)")
    conn.commit()
    (n,) = conn.execute("SELECT COUNT(*) FROM kbo_company").fetchone()
    return n


def import_full_zip(zip_path: str | Path, progress=print) -> dict:
    """Importeer een KboOpenData Full-zip. Retourneert aantallen per tabel."""
    zip_path = Path(zip_path)
    results: dict[str, int] = {}
    conn = _open_conn()
    try:
        with zipfile.ZipFile(zip_path) as zf:
            names = {Path(n).name.lower(): n for n in zf.namelist()}
            for filename, (table, columns) in FILES.items():
                member = names.get(filename)
                if not member:
                    progress(f"  ! {filename} niet gevonden in zip, overgeslagen")
                    continue
                progress(f"  → import {filename} ...")
                results[table] = _import_csv(conn, zf, member, table, columns)
                progress(f"    {results[table]:,} rijen")
        progress("  → indexen aanmaken ...")
        _create_indexes(conn)
        progress("  → zoektabel kbo_company opbouwen (kan even duren) ...")
        results["kbo_company"] = _build_company_table(conn)
        progress(f"    {results['kbo_company']:,} ondernemingen")
        # metadata bewaren
        conn.execute(
            "CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT DEFAULT '')"
        )
        conn.execute(
            "INSERT OR REPLACE INTO settings (key, value) VALUES ('kbo_last_full_import', ?)",
            (zip_path.name,),
        )
        conn.commit()
    finally:
        conn.close()
    return results


if __name__ == "__main__":
    if len(sys.argv) != 2:
        print("Gebruik: python -m app.services.kbo_import <pad-naar-Full.zip>")
        sys.exit(1)
    import_full_zip(sys.argv[1])
