# Samordning

## 2026-11-28: Codex bygger löparprofil på Hugos uttryckliga uppdrag

Hugo gav Codex Profil och preciserade att löparprofilen ska fungera utan AI;
andra agenter sköter coach/planering och resten. Arbetet finns bara på `app/codex`.
Ingen merge eller deploy görs av Codex.

Gemini/integrationsagent: samordna ändringarna i Profil-delen av `public/app/index.html`.
Ny logik och CSS ligger i separata `runner-profile.js` / `runner-profile.css` för att
minska konflikter. Befintliga `styles.css` ändras inte.

Planeringsagent: API-kontrakt, enheter, lagring och avgränsning finns i
[LOPARPROFIL.md](LOPARPROFIL.md). Användarens egna zoner och föredragna dagar är
sparade men ännu inte kopplade till träningsplanen. Målet är samma `/api/goals`
som tidigare. Race-predictorns separata loppmål är inte ändrat av detta arbete.
