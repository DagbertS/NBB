"""Belgisch Staatsblad (ejustice) — bewust NIET geautomatiseerd.

Check uitgevoerd op 2026-08-07 (robots.txt door gebruiker opgehaald):
ejustice.just.fgov.be zet alle relevante paden voor rechtspersonen-
publicaties op Disallow voor alle user-agents (o.a. /cgi_tsv/, /tsv_pub/,
/tsv_pdf/). De gebruiksvoorwaarden verklaren de informatie zelf vrij
herbruikbaar, maar dat betreft de inhoud, niet geautomatiseerde afname.
Conform spec §7 (respecteer robots.txt) blijft deze module dus een stub.

De route voor Staatsblad-signalen (bestuurderswissels, kapitaalbewegingen,
zetelverplaatsing, fusie/splitsing, ontbinding): opzoeken in de browser en
invoeren via signals/manual_input.py, met de publicatiereferentie als
source en de opzoekdatum als as_of. Voor bulk: FOD Justitie om toestemming
of een officieel kanaal vragen.

Deze module bestaat zodat de interface vastligt: mocht er ooit een
legitiem kanaal komen, implementeert fetch_publications() dezelfde
signaalvorm en verandert er stroomafwaarts niets.
"""

AUTOMATION_STATUS = (
    "niet geautomatiseerd: robots.txt van ejustice verbiedt de relevante "
    "paden (gecontroleerd 2026-08-07, zie docs/SOURCES.md) — gebruik "
    "handmatige invoer (manual_signals.yaml)"
)


def fetch_publications(enterprise_number: str) -> list[dict]:
    """Interface-stub. Automatisering is per robots.txt niet toegestaan."""
    raise NotImplementedError(AUTOMATION_STATUS)
