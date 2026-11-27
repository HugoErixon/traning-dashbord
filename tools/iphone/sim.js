// iPhone 15 Pro-simulator för Trainyzes mobilapp.
//
// Visar /app/ i en iframe med iPhone 15 Pros mått (393 × 852) och beter sig
// som iOS gör med en webbapp på hemskärmen:
//   - Utan viewport-fit=cover + statusfältsstil black-translucent börjar
//     innehållet UNDER statusfältet (59 pt), och statusfältet får appens
//     theme-color. Det är så trainyze.com-koden ser ut på en riktig iPhone.
//   - Med cover + black-translucent ligger appen bakom statusfältet och får
//     själv ta hänsyn till säkra marginaler.
// Laddar om appen automatiskt när en fil i public/ eller tools/iphone/ ändras.
'use strict';

(() => {
  const STATUS_BAR = 59;
  const frame = document.getElementById('app');
  const screen = document.querySelector('.screen');
  const root = document.documentElement;
  const store = {
    get(key, fallback) { try { return localStorage.getItem(key) ?? fallback; } catch (_) { return fallback; } },
    set(key, value) { try { localStorage.setItem(key, value); } catch (_) { /* ignoreras */ } },
  };

  // Behåll var man var mellan omladdningar (om appen använder hash-adresser).
  frame.src = '/app/' + store.get('sim:hash', '');

  function reloadApp() {
    try {
      store.set('sim:hash', frame.contentWindow.location.hash || '');
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
    layoutLikeIos();
  });

  // ── Placering som iOS ───────────────────────────────────────────────────
  function layoutLikeIos() {
    try {
      const doc = frame.contentDocument;
      const viewport = doc.querySelector('meta[name="viewport"]')?.content || '';
      const barStyle = doc.querySelector('meta[name="apple-mobile-web-app-status-bar-style"]')?.content || 'default';
      const cover = /viewport-fit\s*=\s*cover/.test(viewport) && barStyle === 'black-translucent';
      screen.classList.toggle('inset', !cover);
      root.style.setProperty('--status-bar', STATUS_BAR + 'px');
    } catch (_) { /* ignoreras */ }
  }

  // ── Klocka och statusfältets färg ───────────────────────────────────────
  const clock = document.getElementById('clock');
  function tick() {
    const now = new Date();
    clock.textContent = `${now.getHours()}:${String(now.getMinutes()).padStart(2, '0')}`;
  }
  tick();
  setInterval(tick, 10000);

  const luminance = color => {
    const m = String(color).match(/\d+(\.\d+)?/g);
    if (!m) return null;
    const [r, g, b] = m.map(Number);
    return 0.2126 * r + 0.7152 * g + 0.0722 * b;
  };

  // Statusfältets bakgrund = appens theme-color (den som matchar aktuellt läge),
  // annars sidans bakgrund. Texten blir mörk på ljust och ljus på mörkt.
  function syncStatusBar() {
    try {
      const win = frame.contentWindow;
      const doc = frame.contentDocument;
      if (!doc || !doc.body) return;
      let color = '';
      for (const meta of doc.querySelectorAll('meta[name="theme-color"]')) {
        if (!meta.media || win.matchMedia(meta.media).matches) { color = meta.content; break; }
      }
      // Ett uttryckligt data-theme på sidan väger tyngre än systemläget.
      const forced = doc.documentElement.getAttribute('data-theme');
      if (forced) color = getComputedStyle(doc.body).backgroundColor;
      if (!color) color = getComputedStyle(doc.body).backgroundColor;
      root.style.setProperty('--status-bg', color);
      const probe = document.createElement('span');
      probe.style.color = color;
      document.body.appendChild(probe);
      const lum = luminance(getComputedStyle(probe).color);
      probe.remove();
      if (lum != null) root.style.setProperty('--status-ink', lum > 140 ? '#000' : '#fff');
    } catch (_) { /* ignoreras */ }
  }
  setInterval(syncStatusBar, 400);

  // ── Verktygsfältet ─────────────────────────────────────────────────────
  document.getElementById('btn-reload').addEventListener('click', reloadApp);
  document.getElementById('btn-home').addEventListener('click', () => {
    store.set('sim:hash', '');
    frame.src = '/app/';
  });
  window.addEventListener('keydown', e => {
    if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === 'r') { e.preventDefault(); reloadApp(); }
  });

  // Tema: använder appens egen inställning (localStorage 'trainyze:theme'),
  // precis som temaväljaren i appen gör, och laddar om.
  const THEMES = [['system', 'Auto'], ['light', 'Ljust'], ['dark', 'Mörkt']];
  const themeBtn = document.getElementById('btn-theme');
  function currentTheme() {
    try { return frame.contentWindow.localStorage.getItem('trainyze:theme') || 'system'; } catch (_) { return 'system'; }
  }
  frame.addEventListener('load', () => {
    const k = currentTheme();
    themeBtn.textContent = (THEMES.find(([key]) => key === k) || THEMES[0])[1];
  });
  themeBtn.addEventListener('click', () => {
    const i = (Math.max(0, THEMES.findIndex(([k]) => k === currentTheme())) + 1) % THEMES.length;
    try {
      const ls = frame.contentWindow.localStorage;
      if (THEMES[i][0] === 'system') ls.removeItem('trainyze:theme'); else ls.setItem('trainyze:theme', THEMES[i][0]);
    } catch (_) { /* ignoreras */ }
    reloadApp();
  });

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
