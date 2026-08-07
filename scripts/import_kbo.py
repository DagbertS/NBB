"""Eenmalige import van de KBO Open Data Full-zip.

Gebruik: python scripts/import_kbo.py <pad-naar-KboOpenData_..._Full.zip>
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.services.kbo_import import import_full_zip  # noqa: E402


def main() -> None:
    if len(sys.argv) != 2:
        print("Gebruik: python scripts/import_kbo.py <pad-naar-Full.zip>")
        sys.exit(1)
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
