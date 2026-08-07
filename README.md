# NBB M&A Screening

Webapplicatie voor het screenen van de Belgische markt op overnamekandidaten,
op basis van:

- **KBO Open Data** — lokale, doorzoekbare database van alle Belgische
  ondernemingen (NACE-code, naam, postcode, gemeente, provincie)
- **NBB Balanscentrale (CBSO)** — officiële neerleggingen/jaarrekeningen per
  onderneming, lokaal opgeslagen en doorzoekbaar
- **Authentic Data Daily Extract** — dagelijkse automatische opbouw van een
  eigen database van alle nieuwe neerleggingen
- **Claude-analyses** — gedetailleerde bedrijfsrapporten en dagelijkse
  promptanalyses waarvan de conclusie automatisch gemaild wordt

De interface is responsive en werkt op laptop en gsm. Er zijn twee gebruikersrollen:
gewone gebruikers en admins (admins beheren gebruikers en zien alle
queries/lijsten/prompts van iedereen via de auditlog).

## Functionaliteit

| Onderdeel | Beschrijving |
|---|---|
| Zoeken | Zoek in de lokale KBO-database op NACE-code (prefix), naam (deel of exact), ondernemingsnummer, postcode, gemeente en provincie |
| Longlists | Bewaar een zoekresultaat als lijst; haal daarna per lijst automatisch alle NBB-documenten op (één bedrijf per keer, op de achtergrond) |
| Bedrijfsfiche | KBO-gegevens + alle lokaal opgehaalde NBB-neerleggingen, met download |
| Analyserapport | Gedetailleerd M&A-rapport per bedrijf, gegenereerd door Claude op basis van KBO-data + jaarrekeningen |
| Prompts | Schrijf eigen analyseprompts die elke ochtend over de nieuwe neerleggingen draaien; conclusie wordt gemaild naar jezelf en/of anderen |
| Automatisch bijwerken | Dagelijkse controle op nieuwe KBO Open Data update-zips (05:30) en de NBB daily extract (07:00) |
| Beheer | Gebruikersbeheer (user/admin), wachtwoordreset, volledige auditlog |

## Installatie

```bash
python -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate
pip install -r requirements.txt

cp .env.example .env               # en vul de waarden in (zie hieronder)

# eerste beheerder aanmaken
python scripts/create_admin.py jouw@email.be

# eenmalig: KBO Full-zip importeren (download van kbopub.economie.fgov.be)
python scripts/import_kbo.py /pad/naar/KboOpenData_0143_2025_12_Full.zip

# starten
python run.py                      # http://localhost:8000
```

## Configuratie (.env)

| Variabele | Uitleg |
|---|---|
| `SECRET_KEY` | Willekeurige lange string voor sessie-cookies |
| `KBO_USERNAME` / `KBO_PASSWORD` | Login voor het KBO Open Data-portaal (voor automatische updates) |
| `NBB_CBSO_SUBSCRIPTION_KEY` | Subscription key uit het CBSO-portaal van de NBB (nodig voor het ophalen van neerleggingen en de daily extract) |
| `ANTHROPIC_API_KEY` | API-sleutel voor de Claude-analyses |
| `SMTP_*` | Mailserver voor het versturen van promptconclusies |

> **Belangrijk:** zet nooit wachtwoorden of sleutels in de code of in git.
> Het `.env`-bestand staat in `.gitignore`.

De NBB CBSO API vereist een **subscription key**, geen gebruikersnaam/wachtwoord.
Log in op het CBSO-portaal van de NBB, abonneer je op de producten
"Consult authentic financial statements data" en "Authentic Data Daily Extract"
en kopieer de key naar `.env`.

## Architectuur

```
app/
├── main.py            FastAPI-app + startup (DB, scheduler)
├── config.py          .env-configuratie
├── models.py          SQLAlchemy-modellen (users, lijsten, deposits, prompts, audit)
├── security.py        Wachtwoord-hashing + sessiecookies + rollen
├── geo.py             Postcode → provincie
├── scheduler.py       Geplande taken (APScheduler)
├── routers/           Webroutes (auth, zoeken, lijsten, nbb, prompts, admin)
├── services/
│   ├── kbo_import.py     Import Full-zip → SQLite (kbo_*-tabellen + zoektabel)
│   ├── kbo_update.py     Automatische delta-updates van het KBO-portaal
│   ├── kbo_search.py     Zoekquery's op de lokale database
│   ├── cbso_client.py    NBB CBSO API-client (referenties + documenten)
│   ├── daily_extract.py  Dagelijkse extract-zip verwerken
│   ├── analyzer.py       Bedrijfsrapporten (Claude)
│   ├── prompt_runner.py  Dagelijkse promptanalyses + mail (Claude)
│   └── mailer.py         SMTP
└── templates/ static/  Responsive webinterface (NL)
```

Alle data staat in `data/` (SQLite-database + documentenopslag), buiten git.

## Hosting in de cloud (Railway)

1. Ga naar [railway.app](https://railway.app), log in met GitHub en kies
   **New Project → Deploy from GitHub repo** → selecteer deze repository en
   de juiste branch. Railway bouwt automatisch via de `Dockerfile`.
2. **Volume toevoegen** (verplicht — anders is de database na elke deploy weg):
   klik op de service → **Settings/rechtsklik → Attach Volume** en kies als
   mount path `/var/data`.
3. **Variables** instellen op de service:

   | Variabele | Waarde |
   |---|---|
   | `DATABASE_PATH` | `/var/data/nbb.db` |
   | `DOCUMENT_STORE` | `/var/data/documents` |
   | `SECRET_KEY` | lange willekeurige string |
   | `ADMIN_EMAIL` / `ADMIN_PASSWORD` | jouw login — de eerste admin wordt bij het opstarten automatisch aangemaakt |
   | `KBO_USERNAME` / `KBO_PASSWORD` | login KBO Open Data-portaal |
   | `NBB_CBSO_SUBSCRIPTION_KEY` | key uit het CBSO-portaal |
   | `ANTHROPIC_API_KEY` | voor de Claude-analyses |
   | `SMTP_HOST` / `SMTP_PORT` / `SMTP_USERNAME` / `SMTP_PASSWORD` / `SMTP_FROM` | voor de prompt-mails |

4. **Netwerk**: klik op de service → **Settings → Networking → Generate
   Domain**. Je krijgt een `*.up.railway.app`-adres met HTTPS.
5. **KBO-data laden**: log in als admin en klik op het dashboard op
   **"Download & importeer KBO-data"** — de server haalt zelf de nieuwste
   Full-zip op en importeert die op de achtergrond (30–60 min; status
   verschijnt op het dashboard).
6. **Vrienden toevoegen**: menu **Gebruikers** → account aanmaken per persoon.

Elke push naar de gekoppelde branch deployt automatisch; het volume met de
database en documenten blijft staan.

Alternatieven: `render.yaml` staat klaar voor Render.com, en de `Dockerfile`
werkt op elke VPS (`docker run -p 8000:8000 -v ./data:/app/data --env-file
.env nbb` — zet er dan HTTPS voor met bv. Caddy).
