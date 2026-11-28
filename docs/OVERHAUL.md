# Trainyze-overhaul: elitnivå på forskningsgrund

Hugos mål (2026-11-28, ordagrant): *"Gör trainyze till ett top klass app som har relevanta
funktioner för både proffs och elitidrottare. Den ska vara bra byggd på forskning som vdot,
träning baserad på tröskel med tydlig plan etc. Gör en stor overhaul på sidan och sluta inte
jobba förrän bara vettiga funktioner är med."*

Och: **när det är färdigt ska det gå live** (merge till `main` och driftsättning på G3).
Hugo gav det klartecknet uttryckligen 2026-11-28. Arbetet ska delegeras till Codex och agy
(Gemini) där det går, för att spara på Claude.

Allt nytt ska vara **regelbaserat, inte AI**. Det gäller planer, tempon, analyser och texter.

## Var arbetet görs
- Gren: `feature/overhaul`, från `feature/mobilapp` plus planmotorn (`training_engine.py`).
- Klon på G3: `~/trainyze-overhaul` (egen klon, inte produktionens `~/traning-dashbord`).
- Mobilappen ligger i `public/app/` och serveras på `/app/`. Webbplatsen i `public/` ska inte
  brytas.
- Läs `AGENTS.md` och `docs/MOBILAPP.md` först. Ny kod för ett område går i egna filer, som
  `public/app/traning.js` och `traning.css`. Gör inte `app.js` ännu större.

## Forskningsgrunden (det appen ska följa och förklara)
- **Daniels (VDOT):** alla tempon (E/M/T/I/R) från ett tal, finns i `vdot.py`. Per-pass-tak:
  T ≤ 10 % av veckans km, I ≤ min(8 %, 10 km), R ≤ min(5 %, 8 km). Långpass ≤ 30 % och ≤ 150 min.
- **Seiler:** cirka 80 % lugnt, högst 3 hårda dagar, minst 48 h mellan hårda pass.
- **Norska modellen:** sub-tröskel (lite långsammare än T, cirka 2–3 mmol) i drag med kort
  vila, alltså mycket kontrollerad tröskeltid. Dubbeltröskel för elit.
- **Periodisering:** bas, specifik, skärpning, nedtrappning, lopp. Finns i `season_plan.py`.
  Avlastning var 4:e vecka.
- **Nedtrappning (Bosquet 2007):** −40–60 % volym, behåll intensiteten.
- **Styrka (Rønnestad och Mujika):** tung styrka på hårda dagar.
- **Belastning (Banister):** CTL (42 d), ATL (7 d), TSB = CTL − ATL, och ramp per vecka.
  Underlaget är Garmins `activityTrainingLoad` i `activities.raw`.

## Klart
- `training_engine.py`: planmotorn (rena funktioner).
  - `build_week(frame, zones, profile, distance, lthr, goal_zones, from_dow, race_dow, race_label)`
    ger alla dagens pass för en vecka i säsongsramen. Varje pass har tempo, puls, syfte och
    struktur, och veckan får regelkontroller (`checks`).
  - Övrigt: `library()` (passbanken), `hr_zones()`, `RESEARCH` (texter om forskningen).
  - Provkört mot Hugos riktiga siffror: VDOT 55,2, halvmara 2027-03-07.

## Kvar att göra (i ordning)
1. **Rätta planmotorn**, med tester i `tests/test_training_engine.py`.
   - Med 7 dagar och nivån `competitive` försvinner lätta dagar, eftersom km inte räcker till
     `MIN_EASY_KM = 4`. Fördela om: korta kvalitetspassens uppvärmning, eller tillåt 3 km.
     Hellre fler korta lätta pass än vilodagar för en elitlöpare.
  - Kontrollen av 80/20-fördelningen räknas per km. Räkna även per tid.
  - Dubbeltröskel (`sessionsPerWeek >= 9`) skapar två rader samma dag. Kontrollera att
    kalendern i appen och `/api/plan` hanterar det.
   - Testa alla distanser, alla nivåer, 3–7 dagar, avlastnings-, nedtrappnings- och
     loppvecka, och att långpasset hamnar på `longRunDay`.
2. **API i `garmin_server.py`** (regelbaserat, inga LLM-anrop):
   - `GET /api/training-plan/preview?weeks=4&from=next`: kör säsongsramen, sedan profilen
     (`runner_profile`, cache-nyckeln finns i `/api/runner-profile`), sedan VDOT
     (`_pace_context`), sedan LTHR (metric_history `lactate_hr`, annars
     `race_predictor.estimate_lthr`), sedan `build_week` för varje vecka. `indexInPhase`
     räknas per fas.
   - `POST /api/training-plan/apply`: ersätter bara `status='planned'` från och med
     startdagen i de valda veckorna. Behåll completed, missed och skipped, och sätt
     `ai_note='Trainyze planmotor'`. **Standard: från nästa vecka.** Innevarande vecka
     ingår bara om användaren väljer det, för Hugo har ett labbtest inplanerat.
   - `GET /api/physiology`: VDOT, alla zoner med tempo och puls, loppekvivalenter (1500 m,
     3 km, 5 km, 10 km, halvmara, maraton), målets VDOT och glappet per vecka till loppet,
     samt VDOT-trend från `metric_history.lactate_pace` via `vdot.from_threshold_pace`.
   - `GET /api/intensity?weeks=12`: per vecka lågt, tröskel och högt. Underlaget är Garmins
     `hrTimeInZone_1..5` (Z1–3 lågt, Z4 tröskel, Z5 högt). Visa antagandet i appen.
     Inkludera tröskelminuter per vecka och CTL/ATL/TSB med ramp.
   - `GET /api/training-library`: `training_engine.library()` med dagens tempon och
     `RESEARCH`.
3. **Appen: Träning-fliken görs om** (`public/app/traning.js` och `traning.css`, kopplas in
   i `index.html` och via `goto()` i `app.js`).
   - Undernavigering: **Plan · Fysiologi · Intensitet · Styrka · Utveckling**.
   - **Plan:** fasbanner med vecka x av y, fas, km-mål och dagar kvar till loppet. Veckans pass
     med struktur och syfte. Knappen "Bygg plan" förhandsvisar 1–4 veckor med regelkontroller
     och lägger sedan in passen. Ta bort AI-panelen "Planera själv" (`#week-planner`) från appen.
   - **Fysiologi:** VDOT-kort, zontabell med tempo och puls, loppekvivalenter, målglapp,
     VDOT-trend och passbanken.
   - **Intensitet:** staplar för fördelningen per vecka mot 80/20, tröskelminuter och
     CTL/ATL/TSB-kurva med en tolkning i text.
   - Följ designen i `kropp.css` och `morgon.css` (`.sl-card`, `.bd-chip` och temats
     variabler).
4. **Rensa irrelevant.** Det som inte tillför en seriös löpare tas bort från appen. AI-knappar
   ersätts av regelmotorn.
5. **Tester:** `python3 -m pytest -q` i en temporär kopia. Kör aldrig mot produktionens
   databas. Testerna använder `127.0.0.1:1` och ska inte slå DNS.
6. **Visuell kontroll** i betan: `~/trainyze-beta`, `trainyze-beta.service`, port 3010,
   https://g3.tail91d6c1.ts.net:8444/app/.
7. **Go-live** (Hugo har sagt ja). Stegen står nedan.

## Delegering
Claude på G3 orkestrerar och granskar. Tunga, avgränsade uppgifter delegeras:
- **Codex:** `codex exec --cd ~/trainyze-overhaul --full-auto "<uppgift>"` (backend, API, tester).
- **agy (Gemini):** `agy -p "<uppgift>" --dangerously-skip-permissions --add-dir ~/trainyze-overhaul`
  (frontend-sidor och CSS).
- Ge varje agent en uppgift med tydliga filer, som en API-route med tester eller en sida.
  Låt dem inte ändra samma fil samtidigt. Claude läser diffen, kör testerna och committar.

## Go-live
1. Alla tester gröna i `~/trainyze-overhaul`, och betan visuellt kontrollerad.
2. `feature/overhaul` → `feature/mobilapp` → `main` (merge, aldrig `--force`), pusha.
3. `cd ~/traning-dashbord && git pull --ff-only` (produktionen står på `main`).
4. `sudo systemctl restart dashboard.service`, sedan
   `sudo systemctl is-active dashboard.service` och `curl -sI https://trainyze.com` och
   `curl -sI https://trainyze.com/app/`.
5. Kör **inte** `migrate_db.py`. Nya kolumner skapas med `ADD COLUMN IF NOT EXISTS` vid start.
6. Om något går fel: `git -C ~/traning-dashbord reset --hard <förra main-sha>` är **inte**
   tillåtet. Gör en `git revert` av merge-commiten, pusha, pulla och starta om.
7. Skriv vad som gjordes i `~/bryggan-2/docs/logg/<datum>.md` och uppdatera `docs/MOBILAPP.md`.

## Läge
- 2026-11-28: G3-agenten stoppades innan den hann skriva kod. Arbetet gjordes i stället från
  laptopen. Codex skrev rättningarna av planmotorn och API:erna tills användningsgränsen tog
  slut, agy byggde Träning-fliken, och Claude granskade, rättade och testade.
  - **Klart:** punkt 1–5.
    - Planmotorn har tester.
    - Alla API:er finns: preview, apply, physiology, intensity och training-library.
    - Träning-fliken har undersidorna Plan, Fysiologi och Intensitet.
    - AI är borta ur appen: chatten, AI-insikterna, AI-motiveringen och AI-knapparna. När
      man byter mål byggs schemat med planmotorn.
    - Alla 690 tester är gröna.
    - En bugg är rättad: modulen `training_load` skuggades av route-funktionen
      `training_load()`, så den importeras nu som `load_model`.
  - Kontrollerat i simulatorn mot betan (`node tools/iphone-sim.mjs --beta`) med riktiga data.
  - **Nästa steg:** go-live (punkt 7).
