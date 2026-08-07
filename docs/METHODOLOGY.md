# Methodologie — elke afleiding, aanname en proxy expliciet

Statusregels die overal gelden:

1. **Herkomst is verplicht.** Elke metric draagt `source` ('nbb' = gerapporteerd
   feit, 'derived' = rekenkundig afgeleid, 'estimate' = schatting) en
   `confidence` ('high'/'medium'/'low'). Een geschatte waarde wordt nooit als
   feit gepresenteerd (acceptatiecriterium §9).
2. **Ontbrekende input ≠ stille nul.** Waar een ontbrekende rubriek als 0
   geteld wordt, staat dat in de `note` van de metric en zakt de confidence.
   Waar de metric zonder de input betekenisloos is, is de waarde `None` met
   uitleg.
3. **Rubriekcodes zijn werkhypothesen** tot de poortwachter-test
   (`test_seed_codes_in_official_taxonomy`) tegen de officiële NBB-taxonomie
   gedraaid heeft (zie docs/SOURCES.md). Dat geldt ook voor de codes hieronder.
4. Metrics worden berekend op de **laatste neerlegging** per (onderneming,
   boekjaar) — `is_latest` — en op periode **N**. Verbeterende neerleggingen
   verdringen dus de oorspronkelijke; dat feit zelf wordt een signaal (fase 5).

## Metrics (fase 3)

### EBITDA-proxy
`EBITDA ≈ EBIT (9901) + afschrijvingen (630) + waardeverminderingen (631/4) + voorzieningen (635/8)`

- Kostenrubrieken worden teruggeteld zoals gerapporteerd; 631/4 en 635/8
  kunnen negatief zijn (terugnemingen) en tellen dan negatief mee.
- EBIT verplicht; ontbrekende terugtel-rubrieken → als 0 geteld, note +
  confidence 'medium'.

### Netto financiële schuld
`NFD = 170/4 + 42 + 43 − 50/53 − 54/58`

- None als géén van de vijf componenten gerapporteerd is.
- Anders: ontbrekende componenten als 0 met note; confidence 'high' (alles
  aanwezig) / 'medium' (≤2 ontbrekend) / 'low'.

### Omzet en omzetproxy (kritieke stap)
- Volledig schema: rubriek 70, source 'nbb', confidence 'high'.
- Verkort/micro: omzet wordt **niet gepubliceerd**. Proxy:
  `omzet ≈ brutomarge (9900) × ratio`, waarbij de ratio expliciet moet worden
  aangeleverd — de peer-mediaan (omzet/brutomarge van volledig-schema-peers,
  fase 4) of een bewuste gebruikersaanname via `--revenue-ratio`.
  Resultaat: source **'estimate'**, confidence **'low'**, note met de
  gebruikte ratio en herkomst.
- Zonder ratio wordt **niet geschat**: waarde None met uitleg.

### Toegevoegde-waardeproxy
- Volledig: `TW ≈ 70 − 60 − 61`. Aanname: andere bedrijfsopbrengsten (74)
  buiten beschouwing — vereenvoudiging, daarom 'derived'/'medium'.
- Verkort/micro: `TW ≈ 9900` (brutomarge als benadering).

### Werkkapitaaldagen
- Klantenkrediet (DSO) = `40 / omzet × 365`. Op geschatte omzet wordt de
  uitkomst zelf source 'estimate'/confidence 'low'.
- Leverancierskrediet (DPO) = `44 / (60 + 61) × 365` — in verkort schema
  ontbreken 60/61 meestal → None met uitleg.
- Voorraaddagen = `3 / 60 × 365` (noemer = aankopen handelsgoederen; bij
  ontbreken → None).

### Capex-intensiteit
`aanschaffingen MVA (8169, toelichting) / toegevoegde-waardeproxy`
— confidence 'low' omdat de noemer een proxy is; None zonder 8169.

### Sociale balans
- VTE = 9087 (gemiddeld personeelsbestand), passthrough feit.
- Personeelskost per VTE = `62 / 9087`.
- VTE-evolutie en aandeel tijdelijke contracten: fase 4+ (tijdreeks resp.
  toelichtingsrubriek nog te valideren).

## Peer-set en benchmark (fase 4)

### Universe-selectie (KBO)
Criteria — allemaal expliciet terug te vinden in `marts/peer_criteria.json`:
hoofdactiviteit (`Classification == MAIN`) in de NACE-versie van de thesis,
prefixmatch op de thesis-codes, provincie afgeleid uit postcode, status
actief (`AC`), vervuilingsfilter op holdings (64.20) en management-
vennootschappen (70.10). `overrides.yaml` kan bedrijven forceren
(`include: true`, telt ook buiten de criteria), uitsluiten
(`include: false`) of herclassificeren (`nace_override`).

### NACE-conversievlag
Per onderneming wordt de 2008-hoofdcode tegen de Statbel-conversietabel
gehouden: had die code meerdere 2025-tegenhangers, dan is de automatisch
toegekende 2025-code mogelijk de verkeerde tak →
`nace_conversion_ambiguous = true`. Zonder geladen conversietabel blijft de
vlag null (onbekend) — nooit stilzwijgend "geen probleem".

### Peers en financiële filters
`peers = universe ∩ beschikbare metrics`, met personeelskost (62) > 0 en
materiële vaste activa (22/27) > drempel (`--mva-min`, default 0). Bedrijven
zonder financials kunnen niet gebenchmarkt worden en vallen dus buiten de
peer set (maar blijven in de universe zichtbaar). Streefgrootte 20-40 peers;
daarbuiten volgt een expliciete waarschuwing in de output en in
`peer_criteria.json`.

### Peer-omzetratio (voedt de omzetproxy)
`ratio = mediaan( 70 / (70 − 60 − 61) )` over volledig-schema-peers met
positieve noemer — de noemer is de brutomarge-proxy (zie boven). Minimum 5
bruikbare peers, anders geen ratio en dus geen omzetschattingen. De note
bij elke geschatte omzet vermeldt n en IQR van de ratio. Een handmatige
`--revenue-ratio` overschrijft de peer-mediaan en wordt als handmatige
aanname genoteerd.

### Kwartielen, nooit gemiddelden
`marts/benchmark.parquet`: per metric × boekjaar n, Q1, mediaan, Q3
(DuckDB `quantile_cont`), berekend op de eigen peer set. Geschatte waarden
(source 'estimate') doen NIET mee aan de kwartielen — kwartielen op
schattingen zouden schijnzekerheid zijn. `marts/peer_positions.parquet`
geeft per onderneming het percentiel (percent_rank) binnen de peers.

## Owner-adjustment-vlaggen (§5) — status

Management fees, huur aan patrimoniumvennootschap, bestuurdersbezoldiging
boven markt, rekening-courant zaakvoerder: dit zijn **vlaggen, geen
correcties**, en ze vereisen peer-benchmarks (fase 4) of toelichtings-
rubrieken die eerst taxonomie-validatie nodig hebben. In `metrics.parquet`
bestaan de kolommen al (`flag_*`) maar staan ze op `null` met een reason —
er wordt niets "gedetecteerd" dat we niet kunnen onderbouwen.

## Signalen (fase 5)

Alle signalen landen in `marts/signals.parquet` met per rij:
`enterprise_number, fiscal_year, signal, value, source, as_of, kind, note`.
`kind` scheidt strikt: **'derived'** (afgeleid uit officiële neerleggings-
metadata) en **'manual'** (handmatig ingevoerd, source + as_of verplicht —
zonder die velden wordt het invoerbestand geweigerd).

### Uit de neerlegging zelf (kind='derived', source='nbb-filing-metadata')

| Signaal | Regel | Aanname |
|---|---|---|
| `late_filing` | neerleggingsdatum > afsluiting boekjaar + 7 maanden; waarde = dagen te laat | Deadline-regel (AV uiterlijk 6m na afsluiting + 30 dagen neerleggingstermijn ⇒ 7m) is een **aanname, wettelijke basis te verifiëren** |
| `chronic_late_filing` | ≥ 2 van de laatste 3 aanwezige boekjaren te laat | sterkere distress-indicator dan de meeste ratio's (spec §7) |
| `model_switch_full_to_abbreviated` | schema volledig → verkort/micro tussen opeenvolgende boekjaren | klassiek motief: omzet verbergen vóór verkoop |
| `correction_filing` | er bestaan verdrongen (superseded) neerleggingen voor het boekjaar | telt aantal + referenties |
| `irregular_fiscal_year_length` | boekjaar < 350 of > 380 dagen | boekjaarwijziging |
| `fiscal_year_end_changed` | afsluitmaand wijkt af van het vorige boekjaar | idem |

**Commissariswissel** (spec §7) is bewust nog niet geïmplementeerd: vereist
toelichtingsrubrieken die eerst taxonomie-validatie nodig hebben. Liever
eerlijk afwezig dan half gedetecteerd.

### Staatsblad en externe signalen (kind='manual')

De robots.txt/voorwaarden-check van ejustice kon vanuit de bouwomgeving
niet uitgevoerd worden; tot die check gedaan is wordt het Staatsblad NIET
gescrapet (interface ligt klaar in `signals/staatsblad.py`). Staatsblad-
publicaties, vergunningen, RSZ-dagvaardingen, opvolgingssignalen e.d. gaan
via het handmatige YAML-bestand (`--manual-signals`, voorbeeld in
`screen/manual_signals.example.yaml`) met de publicatiereferentie als source.

## Scorekaart en longlist (fase 6)

Geen black box: elke (deel)score krijgt een `*_basis`-kolom in
`marts/longlist.parquet` die letterlijk zegt waaruit hij is opgebouwd, en
alle constanten staan in `score/scorecard.py` bovenaan.

| Component | Regel | Constanten |
|---|---|---|
| `score_financial` | gemiddelde van de beschikbare peer-percentielen × 100: ebitda_proxy ↑, revenue ↑, net_financial_debt ↓ (geïnverteerd) | — |
| `score_growth` | CAGR van EBITDA-proxy (fallback: eigen vermogen) over de beschikbare boekjaren, lineair geschaald 0% → 0 t/m ≥ 20%/jaar → 100; vereist ≥ 2 jaren met positieve waarden | `GROWTH_FULL_SCALE = 0.20` |
| `score_signals` | start 100, minus distress-penalty's: chronic_late_filing 25, late_filing 10/boekjaar (plafond 20), modelwissel 15, correction 5, boekjaarsignalen 5; vloer 0. Handmatige signalen hebben onbekende polariteit en krijgen bewust géén automatische penalty (wel geteld en in de one-pager getoond) | `SIGNAL_PENALTIES` |
| `score_total` | gewogen som met de gewichten uit thesis.yaml (default 0.5/0.2/0.3); ontbreekt een component, dan hernormaliseren over de rest — en dat staat in de basis. **Uitzondering:** zonder financiële component (geen peer-percentielen) is er géén totaalscore — anders zou een bedrijf met minder data boven volledig gescoorde bedrijven ranken; die bedrijven sorteren onderaan met "onvoldoende data om te ranken" | `DEFAULT_WEIGHTS` |

Classificatie platform/bolt-on: op de thesis-criteria (nu VTE:
`targets.platform.fte_min` / `targets.bolt_on.fte_max`; ertussen =
"tussenmaat"; zonder VTE-data = onbekend). `in_size_range` toetst de
groottevork uit de thesis (balanstotaal/VTE) en is null bij ontbrekende data.

De one-pager (`screen report <nr>`) markeert elke geschatte waarde met
*(schatting)*, toont Q1/mediaan/Q3 + percentiel per metric, alle signalen
met bron en datum, de volledige score-opbouw, en genereert de openstaande
dataroom-vragen uit wat ontbreekt (omzet niet gepubliceerd, owner-
adjustments onbeoordeeld, NACE-conversie ambigu, laattijdigheid, ...).
