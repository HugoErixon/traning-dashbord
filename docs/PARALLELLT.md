# Parallellt arbete: Claude, Codex och Gemini på mobilappen

Tre agenter jobbar samtidigt, var och en i **sin egen worktree och gren**. Alla utgår från
`feature/mobilapp`. Ingen agent slår ihop, driftsätter eller rör `main`; det gör användaren.

| Agent | Mapp | Gren | Simulator (port) |
|---|---|---|---|
| Claude | `~/trainyze-claude` | `app/claude` | 3101 |
| Codex | `~/trainyze-codex` | `app/codex` | 3102 |
| Gemini | `~/trainyze-gemini` | `app/gemini` | 3103 |
| (integration) | `~/trainyze` | `feature/mobilapp` | 3100 |

Simulatorn väljer port och Chrome-profil efter mappnamnet, så `node tools/iphone-sim.mjs --g3`
fungerar parallellt i alla tre mapparna. Första gången måste man logga in i varje simulator.

## Ansvarsområden (uppdaterat 2026-11-28)

| Agent | Område | Huvudsakliga filer |
|---|---|---|
| Gemini | **Träningslogiken**: planen, veckorapport, säsongsplan, dagens bedömning, så att allt bygger på inbyggda regler och inte på AI-nycklar | backend (`garmin_server.py`, `adaptive_plan.py`, plan-moduler), Träning-flikens sektioner |
| Codex | **Profil** som löparprofil: om mig, föredragna träningsdagar, mål, egna puls- och tempozoner | Profil-sidan, profil-API |
| Claude | **Kropp**: Läget, Sömn, Känningar, Vanor | `public/app/kropp.js`, `public/app/kropp.css`, Kropp-sidorna i `index.html` |

Gemensam princip: allt nytt ska bygga på inbyggda funktioner och regler, inte på AI-anrop.

### Lägg nytt i egna filer

`public/app/app.js` är ett vanligt skript där all kod ligger på toppnivå, så alla funktioner är
globala. Ny kod för ett område läggs därför i en **egen fil** som laddas efter `app.js`
(`<script src="kropp.js?v=__ASSETV__"></script>`), med egen CSS-fil. Filen måste läggas till i
`_asset_version()` i `garmin_server.py` och i `tests/test_mobile_app.py`. Då rör man bara några
rader i `app.js` (`NAV_SECTION`, `goto()`), och de andra agenterna krockar inte med en.

Regler:
1. Rör inte en annan agents sektion. Behövs en ändring där: skriv en rad i `docs/PARALLELLT-KO.md`
   (skapa filen vid behov) i stället för att göra den.
2. **Gemensamma filer** (`NAV_SECTION`, `goto()`, `index.html`-skalet, bottenmenyn): bara små, riktade
   ändringar, och pusha dem direkt så att de andra kan rebasa tidigt.
3. `styles.css`: lägg nya regler i ett eget block med en kommentar `/* === <agent>: <flik> === */`
   i slutet av filen, så flyttas inga andra rader.
4. Ändra aldrig webbsidan (`public/index.html`, `public/app.js`, `public/styles.css`). Appen är en fork.
5. Pusha ofta: `git pull --ff-only` (eller `git rebase feature/mobilapp`) först, committa, `git push`.
   Aldrig `--force`.
6. Kör testerna innan du säger att något är klart (se `docs/MOBILAPP.md`, avsnittet Tester).
7. Visa resultatet i den egna simulatorn och beskriv vad som ändrats och hur man ser det.

## Integrering (användaren eller en integrationsagent i `~/trainyze`)

```bash
cd ~/trainyze && git switch feature/mobilapp && git pull --ff-only
git merge --no-ff app/claude      # en gren i taget
# kör testerna, öppna simulatorn, sedan nästa gren
git merge --no-ff app/codex
git merge --no-ff app/gemini
git push
```

Efter varje sammanslagning rebasar de andra agenterna: `git fetch && git rebase origin/feature/mobilapp`.
Konflikter löses i integrationsmappen, inte av agenterna ensamma. Först när `feature/mobilapp` är bra
beslutar användaren om sammanslagning med `main` och driftsättning (se `docs/MOBILAPP.md`).

## Starta

```bash
cd ~/trainyze-claude && claude        # tillägg: node tools/iphone-sim.mjs --g3
cd ~/trainyze-codex  && codex
cd ~/trainyze-gemini && gemini        # eller agy
```

Be varje agent börja med: *"Läs AGENTS.md, docs/MOBILAPP.md och docs/PARALLELLT.md. Du är <namn>, ditt
område står i PARALLELLT.md. Uppgift: …"*

## Städa efter sig

```bash
cd ~/trainyze && git worktree remove ../trainyze-claude   # när grenen är sammanslagen
git branch -d app/claude
```
