"""Afleiding van provincie/gewest uit Belgische postcode."""

PROVINCES = [
    "Brussel",
    "Waals-Brabant",
    "Vlaams-Brabant",
    "Antwerpen",
    "Limburg",
    "Luik",
    "Namen",
    "Henegouwen",
    "Luxemburg",
    "West-Vlaanderen",
    "Oost-Vlaanderen",
]


def province_for_zipcode(zipcode: str) -> str:
    try:
        z = int(str(zipcode).strip()[:4])
    except (ValueError, TypeError):
        return ""
    if 1000 <= z <= 1299:
        return "Brussel"
    if 1300 <= z <= 1499:
        return "Waals-Brabant"
    if 1500 <= z <= 1999 or 3000 <= z <= 3499:
        return "Vlaams-Brabant"
    if 2000 <= z <= 2999:
        return "Antwerpen"
    if 3500 <= z <= 3999:
        return "Limburg"
    if 4000 <= z <= 4999:
        return "Luik"
    if 5000 <= z <= 5999:
        return "Namen"
    if 6000 <= z <= 6599 or 7000 <= z <= 7999:
        return "Henegouwen"
    if 6600 <= z <= 6999:
        return "Luxemburg"
    if 8000 <= z <= 8999:
        return "West-Vlaanderen"
    if 9000 <= z <= 9999:
        return "Oost-Vlaanderen"
    return ""


def zipcode_ranges_for_province(province: str) -> list[tuple[int, int]]:
    mapping = {
        "Brussel": [(1000, 1299)],
        "Waals-Brabant": [(1300, 1499)],
        "Vlaams-Brabant": [(1500, 1999), (3000, 3499)],
        "Antwerpen": [(2000, 2999)],
        "Limburg": [(3500, 3999)],
        "Luik": [(4000, 4999)],
        "Namen": [(5000, 5999)],
        "Henegouwen": [(6000, 6599), (7000, 7999)],
        "Luxemburg": [(6600, 6999)],
        "West-Vlaanderen": [(8000, 8999)],
        "Oost-Vlaanderen": [(9000, 9999)],
    }
    return mapping.get(province, [])
