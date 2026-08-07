# Bronnen — status en verificatie

Regel: geen endpoint wordt als vaststaand behandeld tot hij met een echte
request is getest. Status: **verified** (live getest, met datum) of
**unverified** (gevonden via documentatie/zoekopdracht, nog te testen bij
eerste run buiten de bouw-omgeving — die blokkeert *.fgov.be/*.nbb.be).

## KBO Open Data (FOD Economie)

| | |
|---|---|
| Wat | Maandelijkse Full- en Update-dumps (zip met CSV: enterprise, establishment, activity, denomination, address, code, meta) |
| Portaal | `https://kbopub.economie.fgov.be/kbo-open-data` — gratis registratie vereist (aparte registratie; de "BCE Public Search Webservice"-logins werken hier NIET) |
| Login-flow | Loginpagina → form-action uitlezen → POST `j_username`/`j_password` (zelfde werkwijze als officiële Fedict-downloadtool, github.com/Fedict/cbe-download) |
| Bestandslijst | `…/affiliation/xml/?files`, hrefs `files/KboOpenData_<nr>_<jaar>_<maand>_(Full|Update).zip` |
| Status | **unverified** vanuit deze omgeving (egress geblokkeerd); flow gebaseerd op Fedict-tool, geverifieerd t.o.v. die broncode op 2026-08-07. Live test bij eerste `screen ingest kbo`. |
| Licentie | Gratis; licentievoorwaarden aanvaard bij registratie. |

## NBB Balanscentrale (CBSO)

| | |
|---|---|
| Wat | Neergelegde jaarrekeningen (XBRL/JSON-XBRL, PDF), consultatie-API + Authentic Data Daily Extract |
| Developer portal | `https://developer.cbso.nbb.be` (API-keys); testomgeving `https://developer.uat2.cbso.nbb.be` |
| Auth | Header `NBB-CBSO-Subscription-Key` + `X-Request-Id` per request |
| Abonnement | Bestelformulier "CBSO Webservices" op nbb.be; producten "Consult authentic financial statements data" en "Authentic Data Daily Extract" zijn gratis |
| Rate limits / bulk-historiek | **te verifiëren** in de technische gids op het developer portal vóór enige bulk-afname |
| Status | **unverified** — geen subscription key beschikbaar; fase 1–2 draaien tegen fixtures (afspraak 2026-08-07) |
| Technische gids | `https://www.nbb.be/doc/ba/cbso2022/cbso_webservices_technical guide_0.94.pdf` (gevonden 2026-08-07) |

## NBB — taxonomie en rubriekcodes (fase 2)

| | |
|---|---|
| Wat | Officiële XBRL-taxonomie en modellen van de jaarrekening (volledig/verkort/micro) met rubriekcodes — de bron van waarheid voor elke rubriekcode |
| Overzichtspagina | `https://www.nbb.be/en/central-balance-sheet-office/preparation-and-filing/technical-information-and-taxonomy-3` (gevonden 2026-08-07) |
| Protocoldocumentatie | o.a. `https://www.nbb.be/doc/ba/xbrl/taxo/protocole/ba_protocolexbrl_v12_nl.pdf` en jaarversies onder `nbb.be/doc/ba/xbrl/...` |
| Modellen (PDF) | Pagina "Modellen vennootschappen": de modellen van de jaarrekening als **PDF** — `https://www.nbb.be/en/central-balance-sheet-office/preparation-and-filing/what-file/models/models-companies-0` (door gebruiker geverifieerd 2026-08-07: PDF, geen xlsx) |
| Taxonomie-download (zip) | `https://www.nbb.be/en/central-balance-sheet-office/drawing-and-filing/technical-information-and-taxonomy/taxonomy` — actuele versie Nbb-cbso-26.0.8 (sinds 02-01-2026) |
| Import | `screen ingest taxonomy --local <bestand>` accepteert **.pdf** (model), **.zip** (taxonomie), .xlsx en .csv; herhaal `--local` om meerdere modellen (volledig/verkort/micro/conso) in één keer samen te voegen; scant de volledige inhoud op rubriekcode-patronen en bouwt `data/raw/nbb/taxonomy/rubrics.csv`. Vals-positieven (bv. jaartallen) zijn onschadelijk: extra codes maken de validatie hoogstens milder — de poortwachter-test (`test_seed_codes_in_official_taxonomy`) faalt alleen als een door ons gebruikte code ONTBREEKT. Skipt met waarschuwing zolang het bestand er niet is. |
| Status | URL's **unverified** vanuit de bouwomgeving (egress geblokkeerd); download gebeurt in de browser van de gebruiker. |

## Statbel — NACE-BEL 2025

| | |
|---|---|
| Wat | NACE-BEL 2025-nomenclatuur + conversietabel 2008↔2025 (nodig voor tijdreeksen over de breuk van 01/01/2025) |
| Overzichtspagina | `https://statbel.fgov.be/nl/over-statbel/methodologie/classificaties/nace-bel-2025` |
| Conversietabel (kandidaat) | `https://economie.fgov.be/sites/default/files/Files/Entreprises/KBO/conversion-NACEBEL-2008-2025.xlsx` — gevonden via zoekopdracht 2026-08-07 |
| Nomenclatuurbestand 2025 | exacte bestands-URL nog niet gevonden; `screen ingest statbel` ondersteunt daarom ook een handmatig gedropt bestand (`--local`) |
| Status | **unverified**; beide URL's zijn overschrijfbaar via CLI-opties |

## NBB sectorstatistieken jaarrekeningen

Referentie-benchmark (kwartielen per sectorale groepering). Wordt pas in
fase 4 relevant; conform plan §6 bouwen we primair eigen kwartielen op de
eigen peer set. Bron nog niet opgezocht — **todo fase 4**.

## Belgisch Staatsblad (ejustice)

Publicaties rechtspersonen (bestuurders, kapitaal, fusie, ontbinding).
**Nog niet geverifieerd**: vóór er ook maar iets gebouwd wordt, checken we
robots.txt en gebruiksvoorwaarden (todo fase 5). Tot dan alleen de
handmatige-invoerinterface.

## Commerciële bronnen (Companyweb, Graydon/Creditsafe, Bel-first/Orbis)

Niet scrapen (contractueel verboden). De module `screen.imports` leest
geëxporteerde CSV's van die providers in en joint op ondernemingsnummer
(fase 3+).
