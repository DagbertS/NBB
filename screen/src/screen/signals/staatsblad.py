"""Belgisch Staatsblad (ejustice) — bewust nog NIET geautomatiseerd.

Spec §3/§7: respecteer robots.txt en throttle agressief; automatiseer
alleen wat legitiem kan. De juridische/technische check (robots.txt en
gebruiksvoorwaarden van ejustice.just.fgov.be) kon vanuit de bouwomgeving
niet uitgevoerd worden (egress geblokkeerd — zie docs/SOURCES.md).

Tot die check gedaan en gedocumenteerd is, is de route voor Staatsblad-
signalen (bestuurderswissels, kapitaalbewegingen, zetelverplaatsing,
fusie/splitsing, ontbinding) de handmatige invoer via
signals/manual_input.py, met de publicatiereferentie als source.

Deze module bestaat zodat de interface vastligt: zodra automatisering
legitiem blijkt, implementeert fetch_publications() dezelfde signaalvorm
en verandert er stroomafwaarts niets.
"""

AUTOMATION_STATUS = (
    "niet geautomatiseerd: robots.txt/voorwaarden-check van ejustice nog "
    "niet uitgevoerd (zie docs/SOURCES.md) — gebruik handmatige invoer"
)


def fetch_publications(enterprise_number: str) -> list[dict]:
    """Interface-stub. Automatisering pas na de juridische check."""
    raise NotImplementedError(AUTOMATION_STATUS)
