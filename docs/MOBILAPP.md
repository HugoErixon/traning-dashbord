# Trainyze mobilapp – läge och hur man fortsätter

Senast uppdaterad 2026-11-28. Gäller grenen **`feature/mobilapp`**, som är en sidogren.
**Ingenting här är live**: `main` och produktionen på G3 står på `f737d5f`.

## Mål

Trainyze delas i två delar mot samma API (`garmin_server.py`):

- **Webbsidan** på `/`: `public/index.html`, `public/app.js`, `public/styles.css`. Den rörs inte av appjobbet.
- **Mobilappen (PWA)** på `/app/`: `public/app/`. Den installeras på iPhone via Safari → Dela →
  *Lägg till på hemskärmen* och utvecklas för sig, med sikte på "världens bästa träningsapp".

## Så kom vi hit (commits på grenen, äldst först)

| Commit | Vad |
|---|---|
| `29fb888` | `/app/`-route i backend, eget manifest (scope `/app/`), dev-server och iPhone-simulator. Den första appen var en omskrivning. |
| `f4c5fd6` | AGENTS.md beskriver de två frontendarna. |
| `920900f` | Simulatorn: fönstret är bara en telefon och skalas för att rymmas på en 1280×800-skärm. |
| `a7e54ec` | **Appen ersattes med en exakt kopia av webbsidan som körs i produktion** (`f737d5f`). `app.js`, `styles.css` och `ambient-fibers.js` var byte-identiska, `index.html` skilde bara i manifestlänken. Omskrivningen finns kvar i historiken (`29fb888`). |
| `3f4ed9f` | **Fyra flikar** i bottenmenyn, bara omstrukturering. |
| `9b53fec` | **Anpassad för flera användare**: personliga funktioner flyttade till Profil, inget hårdkodat "Hugo". |
| `f593783` | Simulatorn kan visa **riktig data direkt från G3** (`--g3`). |

## Appens struktur nu

| Flik | Underflikar (befintliga sidor) |
|---|---|
| **Start** | Idag (startsida) · Översikt |
| **Träning** | Plan (veckorapport, säsongsplan, Planera själv) · Styrka · Utveckling |
| **Kropp** | **Läget** (beredskap från `/api/today`, nyckelvärden mot egen normalnivå, belastning 14 d) · Sömn · **Känningar** · **Vanor** (livsstilslogg + effektanalys, dagbok). Koden i `public/app/kropp.js` och `kropp.css`. |
| **Profil** | Profil · **Din fysiologi** (Labbtest, Måltempon & zoner med VDOT/kalkylator) · Anslutningar · Notiser · Genvägar (**Mitt mål**, Uppdatera data, dag/natt-läge; för admin även Klimat, Hantera användare, AI Control) · Konto |

- Flik ↔ sida-kartan finns i `NAV_SECTION` i `public/app/app.js`. Underflikarna är `.subnav`-rader i `public/app/index.html`.
- Laddningen följer sidan i `goto()`: Profil laddar labbtest och tempon. Kropp-sidorna laddas via `window.loadBody`/`loadHabits` i `kropp.js`.
- Admin-saker visas bara för admin (`isAdmin`). Hälsningen använder inloggad användare.
- Hittills har vi **bara strukturerat om, inga nya funktioner**. Användaren har uttryckligen bett om det.

## Utveckla och testa på laptopen

Repo: `~/trainyze` (laptop). Det kräver bara Python 3 och Node, ingen Postgres eller venv.

| Starta | Data |
|---|---|
| Programmenyn **Trainyze iPhone (data från G3)**, eller `node tools/iphone-sim.mjs --g3` | Riktig data direkt från Trainyze på G3, port 3000, via Tailscale `100.82.234.62` och annars LAN `192.168.1.195`. **Rekommenderat.** |
| **Trainyze iPhone (data via trainyze.com)**, eller `--prod` | Riktig data via internet |
| **Trainyze iPhone**, eller utan flagga | Påhittad data. Många kort blir tomma eftersom testdatan byggdes för den första appen. |

- Alla riktiga lägen är **skrivskyddade**. Bara inloggning och utloggning släpps igenom, och coachfrågor
  stoppas också eftersom de kan flytta pass. `--skriv` tillåter ändringar.
- Man loggar in en gång. Kakan sparas i simulatorns Chrome-profil `~/.cache/trainyze-iphone/` i upp till 30 dagar.
- Simulatorn: iPhone 15 Pro, 393×852. Innehållet ligger under statusfältet precis som en hemskärmsapp
  utan `viewport-fit=cover`, och statusfältet har sidans `theme-color`. Den har touch-emulering och
  iPhone-UA via DevTools-protokollet och laddar om när en fil sparas. Verktygsfältet visas när musen
  når fönstrets överkant.
- Dev-servern (`tools/dev_shell.py`, port 3100) skickar samma CSP som produktionen, utom
  `frame-ancestors 'self'`.
- VS Code-uppgifter finns i `.vscode/tasks.json`. Det är samma verktyg i VS Code som i programmenyn.
- Fallgrop: VS Code är en snap. Chrome som startas därifrån kraschar på GNOME-schemat ("antialiasing").
  `iphone-sim.mjs` rensar därför snap-sökvägar ur miljön innan Chrome startas.

## Tester

Hela sviten (633 tester, varav 7 för appen i `tests/test_mobile_app.py`) körs på G3 i en
tillfällig klon med produktionens venv, så att produktionen inte rörs:

```bash
ssh g3 'T=/tmp/tz-test; rm -rf $T; git clone -q ~/traning-dashbord $T && cd $T &&
  git fetch -q git@github.com-traning-dashbord:HugoErixon/traning-dashbord.git feature/mobilapp &&
  git checkout -q FETCH_HEAD && ~/traning-dashbord/venv/bin/python -m unittest discover -s tests 2>&1 | tail -3;
  cd / && rm -rf $T'
```

## Att veta innan man fortsätter

- **Inget går live utan att användaren säger till.** Live-flödet: slå ihop `feature/mobilapp` →
  `main`, sedan på G3 `git pull --ff-only` och `sudo systemctl restart dashboard.service`. Därefter
  öppnar man trainyze.com/app/ i Safari → Lägg till på hemskärmen.
- Appen är en *fork* av webbsidan från `f737d5f`. Ändringar på webbsidan följer inte med automatiskt, och tvärtom.
- CSP blockerar inline-skript, så temaskriptet i `index.html` körs aldrig, varken i produktion eller i simulatorn.
- På laptopen finns git-identiteten bara satt för det här repot (`HugoErixon@users.noreply.github.com`).

## Nästa steg (förslag)

1. Gå igenom varje flik med riktig data i simulatorn och notera vad som känns fel på mobil.
2. Bestämma vad som ska bli **nytt** i appen. Hittills har vi bara strukturerat om.
3. När appen är bättre än webbens mobilvy: driftsätt `/app/` och installera den på iPhone 15 Pro.
