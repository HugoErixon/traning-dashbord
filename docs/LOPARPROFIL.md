# Löparprofil i mobilappen

2026-11-28 · utvecklas på **app/codex** i `~/trainyze-codex`.
Hugo bad uttryckligen Codex bygga Profil trots den tidigare flikfördelningen.
Omfattningen är löparprofilen; andra agenter arbetar med resten. Inte driftsatt.

## Funktionen

Profil börjar med löparens namn, bakgrund och presentation, följt av träningsdagar,
önskat antal löppass, långpassdag, nuvarande veckovolym och vardagsönskemål.
Alla dessa fält är frivilliga. Egna puls- och tempozoner sparas uttryckligen;
inget räknas fram eller ersätts automatiskt i dessa fält.

- Puls: valfri max-/vilopuls och fem sammanhängande intervall i bpm. Båda ändpunkterna
  ingår. Z2 börjar därför på Z1:s max + 1. Tomma zoner betyder inte angivet.
- Tempo: separata intervall för `easy`, `steady`, `marathon`, `threshold`, `interval`.
  Snabbaste till långsammaste, `min:sek/km`. Båda fälten behövs per angiven intensitet;
  intensiteter får överlappa. De är inte exklusiva pulszoner.
- Måleditorn använder befintliga `GET/PUT /api/goals`: `goalTitle`, `goalDeadline`,
  `currentBest`, `secondaryGoal`. Ingen ny kopia av målet och ingen plangenerering.
- Labbtest, VDOT och kalkylator finns kvar. Referenstempon hämtas via nya
  `GET /api/training-paces`, som bara anropar den befintliga deterministiska
  `_pace_context`. Profilen hämtar inte AI-tempoförslag och har ingen genereringsknapp.
- Egna zoner och preferenser **ändrar ännu inte planen**. UI säger detta uttryckligen.
  Nästa planeringsarbete behöver koppla in dem; gamla AI-coachen är inte omskriven här.

## API för nästa agent

`GET /api/runner-profile` returnerar `profile` och `summary`.
`PUT /api/runner-profile` ersätter **hela** profilen med samma fält som `profile`:

```json
{
  "displayName": "Exempellöpare",
  "experience": "recreational",
  "about": "Jag gillar skogsstigar.",
  "preferredDays": [1, 3, 6],
  "sessionsPerWeek": 3,
  "longRunDay": 6,
  "weeklyKm": 25,
  "trainingNotes": "Korta pass på vardagar.",
  "maxHr": null,
  "restingHr": null,
  "hrZones": [],
  "paceZones": {"easy": {"fast": "5:30", "slow": "6:15"}}
}
```

Veckodagar är **0 = måndag … 6 = söndag**. Långpassdagen måste finnas bland valda dagar.
Bakgrund är `""`, `new`, `recreational`, `experienced` eller `competitive`.
`summary.paceRanges` ger intervallen som `fastSecPerKm` / `slowSecPerKm`.
`hrSource` / `paceSource` är `manual` eller `unset`; detta är inte automatisk zonprioritering.

Lagring: befintliga `cache`, nyckel `<uid>:runner-profile:v1` (ingen schemaändring).
Auth och CSRF följer samma skydd som övriga API:t. Validering sker i `runner_profile.py`
före skrivning. Ogiltiga värden ger 400 utan att röra tidigare profil.
Använd alltid aktuell användares id när profilen läses från en framtida planeringsmotor.
För användare utan profil: `runner_profile.normalize({})` ger tomma standardvärden.

## Filer och integration

Ny UI-logik ligger i `public/app/runner-profile.js`, ny CSS i
`public/app/runner-profile.css`, båda med i asset-versioneringen.
`public/app/index.html` har den nya profilsektionen. `app.js` har små ändringar i
Profil-laddningen och referenstempopanelen; övriga flikars beteenden är kvar.
Webbens filer i `public/` har inte ändrats.

## Prova och verifiera

```bash
cd ~/trainyze-codex
node tools/iphone-sim.mjs
```

Välj **Profil**. Mockläget kan spara profil, mål och VDOT i utvecklingsserverns minne,
så omladdning av sidan behåller dem. Omstart av servern nollställer testdata.
Detta läge skriver aldrig mot G3. G3/prod-lägena kan inte spara den nya profilen
förrän backendändringen har driftsatts; 404 visas som ett tydligt meddelande och
sparknappen förblir avstängd. Slå inte på `--skriv` för att lösa ett saknat API.

Verifierat lokalt med isolerad Pythonmiljö: 643 Python-tester passerade, inklusive
10 nya tester för validering, användarisolering, CSRF/auth, lagringsfel och frånvaro
av AI-anrop. JavaScript har syntaxkontrollerats.

Formulärens DOM-integrationstest använder mockservern på **3102**:

```bash
npm install --prefix /tmp/trainyze-profile-dom --no-audit --no-fund jsdom@26.1.0
NODE_PATH=/tmp/trainyze-profile-dom/node_modules node tests/test_runner_profile_ui.js
```

Testet kontrollerar mockläget innan skrivning, sparar endast fiktiv testdata och
verifierar återläsning, fel/omförsök, validering, oförändrade utkast vid flikbyte,
säker textrendering och det befintliga mål-API:t.

Visuell granskning kvar: simulatorn startade, men CUA kunde inte ansluta till Chrome
(`Browser attachment requires a network policy`); inbyggd webbläsare saknades också.
DOM-test är inte en kontroll av hur layouten faktiskt ser ut på en iPhone.
