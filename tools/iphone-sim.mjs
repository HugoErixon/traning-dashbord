#!/usr/bin/env node
// Öppnar Trainyzes mobilapp i ett eget iPhone 15 Pro-fönster.
//
//   node tools/iphone-sim.mjs            påhittad data
//   node tools/iphone-sim.mjs --g3       din riktiga data direkt från G3 (skrivskyddat)
//   node tools/iphone-sim.mjs --prod     din riktiga data via trainyze.com (skrivskyddat)
//   lägg till --skriv för att tillåta ändringar
//   --slot N väljer port 3100+N (annars från mappnamnet, se nedan)
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
const args = process.argv.slice(2);
// Varje worktree får egen port, egen Chrome-profil och eget CDP-läge, så att flera
// agenter kan köra varsin simulator samtidigt. Platsen följer mappnamnet:
// ~/trainyze → 0, trainyze-claude → 1, trainyze-codex → 2, trainyze-gemini → 3.
// Eget nummer: --slot N.
const SLOTS = { claude: 1, codex: 2, gemini: 3 };
const slotArg = args.indexOf('--slot');
const SLOT = slotArg >= 0 ? Number(args[slotArg + 1])
  : (SLOTS[(dirname(HERE).split('/').pop().match(/-([a-z]+)$/) || [])[1]] ?? 0);
const PORT = 3100 + SLOT;
const CDP_PORT = 9333 + SLOT;
const BASE = `http://127.0.0.1:${PORT}`;
const CACHE = join(homedir(), '.cache', SLOT ? `trainyze-iphone-${SLOT}` : 'trainyze-iphone');
const wantMode = args.includes('--g3') ? 'g3' : args.includes('--prod') ? 'prod' : 'mock';
const serverArgs = [join(HERE, 'dev_shell.py'), '--port', String(PORT), ...(wantMode === 'mock' ? [] : [`--${wantMode}`]), ...(args.includes('--skriv') ? ['--skriv'] : [])];
const MODE_TEXT = { mock: 'påhittad data', g3: 'riktig data från G3', prod: 'riktig data via trainyze.com' };
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

async function stopServer() {
  const { execFileSync } = await import('node:child_process');
  try { execFileSync('pkill', ['-f', `${join(HERE, 'dev_shell.py')} --port ${PORT}`]); } catch { /* inget att stoppa */ }
  for (let i = 0; i < 30 && await serverInfo(); i++) await sleep(100);
}

async function ensureServer() {
  const info = await serverInfo();
  if (info) {
    if (info.mode === wantMode) return;
    // Fel läge: stoppa den gamla servern (den startades av det här verktyget)
    // och starta om i rätt läge.
    console.log(`Byter utvecklingsserverns läge från "${info.mode}" till "${wantMode}".`);
    await stopServer();
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
  const pending = new Map();
  const send = (method, params = {}) => new Promise(resolve => {
    pending.set(++id, resolve);
    ws.send(JSON.stringify({ id, method, params }));
  });
  ws.addEventListener('message', event => {
    const msg = JSON.parse(event.data);
    if (msg.id && pending.has(msg.id)) { pending.get(msg.id)(msg.result || {}); pending.delete(msg.id); }
  });
  ws.addEventListener('open', async () => {
    send('Network.enable');
    send('Network.setUserAgentOverride', { userAgent: IPHONE_UA, platform: 'iPhone' });
    send('Emulation.setTouchEmulationEnabled', { enabled: true, maxTouchPoints: 5 });
    send('Emulation.setEmitTouchEventsForMouse', { enabled: true, configuration: 'mobile' });
    send('Emulation.setScrollbarsHidden', { hidden: true });  // iOS har inga synliga rullningslister
    send('Page.enable');
    await send('Page.reload', { ignoreCache: true });  // så att appen laddas med iPhone-identiteten
    await fitWindow(send);
  });
  // Emuleringen gäller bara så länge anslutningen lever, så den hålls öppen
  // tills fönstret stängs.
  return new Promise(resolve => ws.addEventListener('close', resolve));
}

// Fönstret ska vara precis en telefon, så stort som skärmen tillåter. Telefonen
// med titankant och knappar är 429 × 888 punkter; den skalas ner på små skärmar
// (t.ex. 1280 × 800) i stället för att klippas.
const PHONE_W = 429;
const PHONE_H = 888;
const TOP_PANEL = 40;  // GNOMEs toppanel – Wayland talar inte om arbetsytan

async function fitWindow(send) {
  try {
    const { result } = await send('Runtime.evaluate', {
      returnByValue: true,
      expression: '({ah: screen.availHeight, aw: screen.availWidth, dw: outerWidth - innerWidth, dh: outerHeight - innerHeight})',
    });
    const s = result.value;
    const innerH = Math.min(PHONE_H, s.ah - TOP_PANEL - s.dh - 8);
    const scale = innerH / PHONE_H;
    const innerW = Math.ceil(PHONE_W * scale);
    const { windowId } = await send('Browser.getWindowForTarget');
    // Ett maximerat fönster måste först bli "normal" innan storleken kan sättas;
    // Chrome minns annars att fönstret var maximerat förra gången.
    await send('Browser.setWindowBounds', { windowId, bounds: { windowState: 'normal' } });
    await new Promise(r => setTimeout(r, 300));
    await send('Browser.setWindowBounds', {
      windowId,
      bounds: { width: innerW + s.dw, height: Math.ceil(innerH) + s.dh },
    });
  } catch { /* fönstret får behålla sin storlek; telefonen skalas ändå in */ }
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
  '--window-size=360,740',
  '--no-first-run',
  '--no-default-browser-check',
  '--class=TrainyzeIphone',
], { stdio: 'ignore', env: cleanEnv() });
chrome.on('exit', () => process.exit(0));
console.log(`iPhone 15 Pro-simulatorn öppnas (${MODE_TEXT[wantMode]}). Stäng fönstret för att avsluta.`);
await emulateIphone();
