"""Eenmalige import van de KBO Open Data Full-zip.

Gebruik:
  python scripts/import_kbo.py <pad-naar-KboOpenData_..._Full.zip>
  python scripts/import_kbo.py --download
      Downloadt de nieuwste Full-zip rechtstreeks van het KBO-portaal
      (KBO_USERNAME/KBO_PASSWORD in .env vereist) en importeert die.
      Handig op een cloudserver: geen grote upload nodig.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.services.kbo_import import import_full_zip  # noqa: E402


def download_latest_full() -> Path:
    from app.services.kbo_update import (  # noqa: E402
        _extract_number,
        download_file,
        list_available_files,
    )

    print("Beschikbare bestanden opvragen bij het KBO-portaal ...")
    available = list_available_files()
    fulls = sorted(
        {f for f in available if f.endswith("_Full.zip")}, key=_extract_number
    )
    if not fulls:
        print("Geen Full-zip gevonden op het portaal — controleer KBO_USERNAME/KBO_PASSWORD in .env")
        sys.exit(1)
    latest = fulls[-1]
    print(f"Downloaden: {latest} (enkele GB, kan lang duren) ...")
    return download_file(latest)


def main() -> None:
    if len(sys.argv) != 2:
        print(__doc__)
        sys.exit(1)

    if sys.argv[1] == "--download":
        zip_path = download_latest_full()
    else:
        zip_path = Path(sys.argv[1])
        if not zip_path.exists():
            print(f"Bestand niet gevonden: {zip_path}")
            sys.exit(1)

    print(f"Import van {zip_path.name} gestart ...")
    results = import_full_zip(zip_path)
    print("Klaar:")
    for table, count in results.items():
        print(f"  {table}: {count:,} rijen")


if __name__ == "__main__":
    main()
