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

## Owner-adjustment-vlaggen (§5) — status

Management fees, huur aan patrimoniumvennootschap, bestuurdersbezoldiging
boven markt, rekening-courant zaakvoerder: dit zijn **vlaggen, geen
correcties**, en ze vereisen peer-benchmarks (fase 4) of toelichtings-
rubrieken die eerst taxonomie-validatie nodig hebben. In `metrics.parquet`
bestaan de kolommen al (`flag_*`) maar staan ze op `null` met een reason —
er wordt niets "gedetecteerd" dat we niet kunnen onderbouwen.

## Nog niet gebouwd

Peer-kwartielen (fase 4), signalen (fase 5), score en one-pagers (fase 6)
— zie docs/PLAN.md.
