"""Postcode -> provincie (Belgische postcoderanges)."""

_RANGES = [
    (1000, 1299, "Brussel"),
    (1300, 1499, "Waals-Brabant"),
    (1500, 1999, "Vlaams-Brabant"),
    (2000, 2999, "Antwerpen"),
    (3000, 3499, "Vlaams-Brabant"),
    (3500, 3999, "Limburg"),
    (4000, 4999, "Luik"),
    (5000, 5999, "Namen"),
    (6000, 6599, "Henegouwen"),
    (6600, 6999, "Luxemburg"),
    (7000, 7999, "Henegouwen"),
    (8000, 8999, "West-Vlaanderen"),
    (9000, 9999, "Oost-Vlaanderen"),
]


def province_for_zipcode(zipcode: str | None) -> str:
    try:
        z = int(str(zipcode).strip()[:4])
    except (ValueError, TypeError):
        return ""
    for lo, hi, name in _RANGES:
        if lo <= z <= hi:
            return name
    return ""
