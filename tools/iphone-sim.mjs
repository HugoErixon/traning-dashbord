#!/usr/bin/env node
// Öppnar Trainyzes mobilapp i ett eget iPhone 15 Pro-fönster.
//
//   node tools/iphone-sim.mjs            påhittad data
//   node tools/iphone-sim.mjs --prod     din riktiga data (skrivskyddat)
//   node tools/iphone-sim.mjs --prod --skriv
//
// Startar tools/dev_shell.py om den inte redan kör, öppnar Chrome som ett
// fristående appfönster och slår på touch-emulering + iPhone-identitet via
// Chrome DevTools Protocol, så att musen beter sig som ett finger (dra för att
// scrolla) och appen ser en iPhone. Fönstret stängs som vanligt.
import { spawn } from 'node:child_process';
import { existsSync, mkdirSync, openSync } from 'node:fs';
import { homedir } from 'node:os';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';

const HERE = dirname(fileURLToPath(import.meta.url));
const PORT = 3100;
const CDP_PORT = 9333;
const BASE = `http://127.0.0.1:${PORT}`;
const CACHE = join(homedir(), '.cache', 'trainyze-iphone');
const args = process.argv.slice(2);
const wantProd = args.includes('--prod');
const serverArgs = [join(HERE, 'dev_shell.py'), ...(wantProd ? ['--prod'] : []), ...(args.includes('--skriv') ? ['--skriv'] : [])];
const IPHONE_UA = 'Mozilla/5.0 (iPhone; CPU iPhone OS 18_1 like Mac OS X) AppleWebKit/605.1.15 '
  + '(KHTML, like Gecko) Version/18.1 Mobile/15E148 Safari/604.1';

const sleep = ms => new Promise(r => setTimeout(r, ms));
mkdirSync(CACHE, { recursive: true });

async function serverInfo() {
  try {
    const res = await fetch(`${BASE}/__dev/info`);
    return res.ok ? await res.json() : null;
  } catch { return null; }
}

async function ensureServer() {
  const info = await serverInfo();
  if (info) {
    if ((info.mode === 'prod') !== wantProd) {
      console.log(`Obs: servern på port ${PORT} kör redan i läget "${info.mode}". Stoppa den först för att byta läge.`);
    }
    return;
  }
  const log = openSync(join(CACHE, 'server.log'), 'a');
  const child = spawn('python3', serverArgs, { detached: true, stdio: ['ignore', log, log] });
  child.unref();
  for (let i = 0; i < 50; i++) {
    if (await serverInfo()) return;
    await sleep(100);
  }
  throw new Error(`Utvecklingsservern startade inte. Se ${join(CACHE, 'server.log')}`);
}

function findChrome() {
  for (const bin of ['google-chrome', 'google-chrome-stable', 'chromium', 'chromium-browser']) {
    for (const dir of (process.env.PATH || '').split(':')) {
      if (dir && existsSync(join(dir, bin))) return join(dir, bin);
    }
  }
  throw new Error('Hittade varken Chrome eller Chromium.');
}

async function cdpTarget() {
  for (let i = 0; i < 60; i++) {
    try {
      const list = await (await fetch(`http://127.0.0.1:${CDP_PORT}/json/list`)).json();
      const page = list.find(t => t.type === 'page' && t.url.includes('/__iphone/'));
      if (page) return page;
    } catch { /* Chrome har inte startat ännu */ }
    await sleep(200);
  }
  return null;
}

async function emulateIphone() {
  const target = await cdpTarget();
  if (!target || typeof WebSocket === 'undefined') {
    console.log('Touch-emulering kunde inte slås på; simulatorn fungerar ändå med mus.');
    return;
  }
  const ws = new WebSocket(target.webSocketDebuggerUrl);
  let id = 0;
  const send = (method, params = {}) => ws.send(JSON.stringify({ id: ++id, method, params }));
  ws.addEventListener('open', () => {
    send('Network.enable');
    send('Network.setUserAgentOverride', { userAgent: IPHONE_UA, platform: 'iPhone' });
    send('Emulation.setTouchEmulationEnabled', { enabled: true, maxTouchPoints: 5 });
    send('Emulation.setEmitTouchEventsForMouse', { enabled: true, configuration: 'mobile' });
    send('Page.enable');
    send('Page.reload', { ignoreCache: true });  // så att appen laddas med iPhone-identiteten
  });
  // Emuleringen gäller bara så länge anslutningen lever, så den hålls öppen
  // tills fönstret stängs.
  return new Promise(resolve => ws.addEventListener('close', resolve));
}

// VS Code som snap lägger sina egna (äldre) GNOME-scheman och GTK-sökvägar i
// miljön. Chrome som startas därifrån läser dem och kraschar direkt på en
// saknad inställning ("antialiasing"). Rensa bort allt som pekar in i snapen.
function cleanEnv() {
  const env = { ...process.env };
  const isSnap = p => /\/snap\/(code|code-insiders)\//.test(p) || /\/snap\/code\//.test(p);
  for (const [key, value] of Object.entries(env)) {
    if (typeof value !== 'string' || !value.includes('/snap/')) continue;
    if (value.includes(':')) {
      const kept = value.split(':').filter(p => p && !isSnap(p));
      if (kept.length) env[key] = kept.join(':'); else delete env[key];
    } else if (isSnap(value)) {
      delete env[key];
    }
  }
  return env;
}

await ensureServer();
const chrome = spawn(findChrome(), [
  `--app=${BASE}/__iphone/`,
  `--user-data-dir=${join(CACHE, 'chrome')}`,
  `--remote-debugging-port=${CDP_PORT}`,
  '--window-size=500,980',
  '--no-first-run',
  '--no-default-browser-check',
  '--class=TrainyzeIphone',
], { stdio: 'ignore', env: cleanEnv() });
chrome.on('exit', () => process.exit(0));
console.log(`iPhone 15 Pro-simulatorn öppnas (${wantProd ? 'riktig data' : 'påhittad data'}). Stäng fönstret för att avsluta.`);
await emulateIphone();
