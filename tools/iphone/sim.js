// iPhone 15 Pro-simulator för Trainyzes mobilapp.
//
// Visar /app/ i en iframe med iPhone 15 Pros mått (393 × 852) och säkra
// marginaler (59 pt upptill, 34 pt nedtill). Laddar om appen automatiskt när
// en fil i public/ eller tools/iphone/ ändras.
'use strict';

(() => {
  const SAFE_TOP = 59;
  const SAFE_BOTTOM = 34;
  const frame = document.getElementById('app');
  const root = document.documentElement;
  const store = {
    get(key, fallback) { try { return localStorage.getItem(key) ?? fallback; } catch (_) { return fallback; } },
    set(key, value) { try { localStorage.setItem(key, value); } catch (_) { /* ignoreras */ } },
  };

  // Behåll var man var (#plan, #coach …) mellan omladdningar.
  const startHash = store.get('sim:hash', '#idag');
  frame.src = `/app/?sim=1&top=${SAFE_TOP}&bottom=${SAFE_BOTTOM}${startHash}`;

  function reloadApp() {
    try {
      store.set('sim:hash', frame.contentWindow.location.hash || '#idag');
      frame.contentWindow.location.reload();
    } catch (_) {
      frame.src = frame.src;
    }
  }

  frame.addEventListener('load', () => {
    try {
      frame.contentWindow.addEventListener('hashchange', () => {
        store.set('sim:hash', frame.contentWindow.location.hash);
      });
    } catch (_) { /* annan origin */ }
  });

  // ── Klocka och statusfältets färg ───────────────────────────────────────
  const clock = document.getElementById('clock');
  function tick() {
    const now = new Date();
    clock.textContent = `${now.getHours()}:${String(now.getMinutes()).padStart(2, '0')}`;
  }
  tick();
  setInterval(tick, 10000);

  // Statusfältet ska vara ljust på mörk app och mörkt på ljus app, precis som
  // iOS gör med "black-translucent".
  function syncInk() {
    try {
      const doc = frame.contentDocument;
      if (!doc || !doc.body) return;
      const bg = getComputedStyle(doc.body).backgroundColor.match(/\d+(\.\d+)?/g);
      if (!bg) return;
      const [r, g, b] = bg.map(Number);
      const lum = 0.2126 * r + 0.7152 * g + 0.0722 * b;
      root.style.setProperty('--status-ink', lum > 140 ? '#000' : '#fff');
    } catch (_) { /* ignoreras */ }
  }
  setInterval(syncInk, 400);

  // ── Verktygsfältet ─────────────────────────────────────────────────────
  document.getElementById('btn-reload').addEventListener('click', reloadApp);
  document.getElementById('btn-home').addEventListener('click', () => {
    try { frame.contentWindow.location.hash = '#idag'; } catch (_) { /* ignoreras */ }
  });
  window.addEventListener('keydown', e => {
    if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === 'r') { e.preventDefault(); reloadApp(); }
  });

  const THEMES = [['system', 'Auto'], ['light', 'Ljust'], ['dark', 'Mörkt']];
  const themeBtn = document.getElementById('btn-theme');
  let themeIndex = Math.max(0, THEMES.findIndex(([k]) => k === store.get('sim:theme', 'system')));
  themeBtn.textContent = THEMES[themeIndex][1];
  function sendTheme() {
    try { frame.contentWindow.postMessage({ type: 'trainyze:theme', value: THEMES[themeIndex][0] }, location.origin); } catch (_) { /* ignoreras */ }
  }
  themeBtn.addEventListener('click', () => {
    themeIndex = (themeIndex + 1) % THEMES.length;
    themeBtn.textContent = THEMES[themeIndex][1];
    store.set('sim:theme', THEMES[themeIndex][0]);
    sendTheme();
  });
  frame.addEventListener('load', sendTheme);

  // Storlek: "Anpassa" krymper telefonen så att den får plats i fönstret.
  const SCALES = [['fit', 'Anpassa'], ['1', '100 %'], ['0.75', '75 %']];
  const scaleBtn = document.getElementById('btn-scale');
  // Startar alltid i "Anpassa", så att hela telefonen syns oavsett skärm.
  let scaleIndex = 0;
  function applyScale() {
    const [key, label] = SCALES[scaleIndex];
    scaleBtn.textContent = label;
    let scale = Number(key);
    if (key === 'fit') {
      const phone = document.getElementById('phone');
      // +12 så att titankanten och sidoknapparna får plats.
      const w = phone.offsetWidth + 12, h = phone.offsetHeight + 12;
      scale = Math.min(1, window.innerWidth / w, window.innerHeight / h);
    }
    root.style.setProperty('--scale', scale.toFixed(3));
  }
  scaleBtn.addEventListener('click', () => {
    scaleIndex = (scaleIndex + 1) % SCALES.length;
    applyScale();
  });
  window.addEventListener('resize', applyScale);
  applyScale();

  // ── Gömt verktygsfält ──────────────────────────────────────────────────
  // Musen över appen syns inte för den här sidan (den ligger i en iframe), så
  // en tunn zon i överkanten tänder fältet och det släcks när musen lämnar det.
  const toolbar = document.getElementById('toolbar');
  let hideTimer;
  const showTools = ms => {
    toolbar.classList.add('show');
    clearTimeout(hideTimer);
    if (ms) hideTimer = setTimeout(() => toolbar.classList.remove('show'), ms);
  };
  document.getElementById('hotzone').addEventListener('mouseenter', () => showTools(0));
  toolbar.addEventListener('mouseenter', () => showTools(0));
  toolbar.addEventListener('mouseleave', () => showTools(600));
  showTools(2500);  // visa en kort stund vid start, så att man vet att det finns

  // ── Läge och automatisk omladdning ─────────────────────────────────────
  const mode = document.getElementById('mode');
  let version = null;
  async function poll() {
    try {
      const res = await fetch('/__dev/info', { cache: 'no-store' });
      const info = await res.json();
      mode.textContent = info.mode === 'prod' ? (info.readonly ? 'Riktig data · skrivskyddat' : 'Riktig data · SKRIV PÅ') : 'Påhittad data';
      mode.className = 'mode' + (info.mode === 'prod' ? (info.readonly ? ' prod' : ' write') : '');
      if (version && info.version !== version) {
        // En ändring i själva simulatorn kräver att ramen laddas om, annars räcker appen.
        reloadApp();
      }
      version = info.version;
    } catch (_) {
      mode.textContent = 'Servern svarar inte';
      mode.className = 'mode write';
    }
  }
  poll();
  setInterval(poll, 1000);
})();
