# Plan: `screen` — reproduceerbare screening-pipeline

Goedgekeurd door opdrachtgever op 2026-08-07, met aanvulling:
**thesis-parameters zijn vrij invulbare zoekcriteria** (`thesis.yaml` per dossier);
de meegeleverde thesis is slechts een ingevuld voorbeeld.

## Positionering

Zelfde repo als de webapp (`app/`), apart package `screen/` met eigen
`pyproject.toml` (uv). De webapp blijft onaangeroerd; hij kan later
`data/marts/longlist.parquet` uitlezen.

## Kernkeuzes

| Keuze | Beslissing | Motivatie |
|---|---|---|
| Dataframes | Polars (lazy) voor transformaties, DuckDB voor joins/kwartielen | KBO-CSV's zijn 2–3 GB: Polars streamt met lage memory-footprint; kwartielen per peer set = SQL (`quantile_cont`) rechtstreeks op parquet. |
| Storage | `data/raw/` (onveranderlijk, gehasht) → `data/interim/` → `data/marts/` | Reproduceerbaar; DuckDB queryt parquet zonder DB-server. |
| Datamodel | Smalle feitentabel `facts(enterprise_number, fiscal_year, schema_type, rubric_code, value, deposit_date, model, source, as_of)`; `signals(enterprise_number, signal, value, source, as_of)` | Scheiding feit/afleiding zit in het schema: afgeleide waarden krijgen `source='derived'` + betrouwbaarheid. |
| CLI | typer: `screen ingest`, `screen build`, `screen rank`, `screen report <nr>` | Idempotent; elke download in een manifest met sha256 + datum. |
| Thesis | `thesis.yaml` (zoekcriteria: sector, NACE, geografie, groottevork, gewichten) + `overrides.yaml` | Per dossier aanpasbaar zonder codewijziging; `--thesis` wijst naar elk gewenst bestand. |

## Fasering (akkoord vereist vóór elke volgende fase)

- **Fase 0 — fundament**: skelet, uv-project, CLI, thesis-loader, manifest, SOURCES.md. ✔
- **Fase 1 — ingest**: KBO full+delta, Statbel-nomenclatuur/conversietabel, NBB (fixtures zolang CBSO-key ontbreekt). Tests op manifest, loginflow, delta. ✔
- **Fase 2 — parse**: XBRL/JSON-XBRL → tidy `facts`; taxonomie-validatietest. ✔
- **Fase 3 — normalize**: metrics (EBITDA-proxy, netto financiële schuld, omzetproxy met betrouwbaarheid, owner-adjustment-vlaggen, werkkapitaaldagen, capex-intensiteit, sociale balans). Handberekende unit tests per metric. ✔
- **Fase 4 — peer-set + benchmark**: NACE-brug 2008↔2025 (1-op-veel-vlag), vervuilingsfilter, overrides, kwartielen op eigen peer set. ✔
- **Fase 5 — signals**: neerleggingssignalen, Staatsblad (na juridische check → handmatige invoer), handmatige invoer met verplichte source/as_of. ✔
- **Fase 6 — score + report**: transparante scorekaart, `longlist.parquet`, markdown one-pagers, METHODOLOGY.md. ✔

Alle zes fasen zijn opgeleverd. Openstaand voor de eerste echte run:
officiële taxonomielijst plaatsen (poortwachter-test), CBSO-key (live
NBB-ingest, fase-2-parser valideren op echte neerlegging), Statbel-
bestanden ophalen, en de ejustice-robots.txt-check vóór eventuele
Staatsblad-automatisering.

## Bekende onzekerheden

1. NBB bulk-XBRL-product en licentie: verifiëren op het CBSO-portaal vóór bulk.
2. Deze bouw-omgeving kan *.fgov.be en *.nbb.be niet rechtstreeks bereiken;
   endpoint-URL's staan in SOURCES.md met status *verified*/*unverified* en
   worden bij de eerste echte run gevalideerd.
3. Staatsblad: robots.txt en voorwaarden checken vóór er iets gebouwd wordt;
   anders alleen handmatige invoer.
4. Rubriekcodes worden pas gebruikt na validatie tegen de officiële
   NBB-taxonomie (test in fase 2).
