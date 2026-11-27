// Trainyze mobilapp (PWA).
//
// En egen, mobil-först frontend mot samma API som webbsidan. Webbsidan på "/"
// lämnas orörd; appen bor under /app/ och installeras på iPhone via Safari →
// Dela → "Lägg till på hemskärmen".
//
// Vyerna (Idag, Plan, Coach, Hälsa, Mer) styrs med hashen så att bakåt-gesten
// och omladdning fungerar. Allt som visas kommer från API:t; inget räknas om
// här som servern redan bestämmer (t.ex. dagens besked och beredskapstalet).
'use strict';

(() => {
  const $ = (sel, root = document) => root.querySelector(sel);
  const params = new URLSearchParams(location.search);

  // ── Simulatorläge ───────────────────────────────────────────────────────
  // I iPhone-simulatorn på datorn ligger appen i en iframe, där env(safe-area-*)
  // alltid är 0. Simulatorn skickar därför med iPhone 15 Pros marginaler.
  if (params.has('sim')) {
    const root = document.documentElement;
    root.classList.add('is-sim');
    root.style.setProperty('--safe-top', (params.get('top') || '59') + 'px');
    root.style.setProperty('--safe-bottom', (params.get('bottom') || '34') + 'px');
    window.addEventListener('message', event => {
      if (event.origin !== location.origin || !event.data) return;
      if (event.data.type === 'trainyze:theme') applyTheme(event.data.value);
    });
  }

  // ── Tema ────────────────────────────────────────────────────────────────
  const THEME_KEY = 'trainyze:app-theme';
  function storedTheme() {
    try { return localStorage.getItem(THEME_KEY) || 'system'; } catch (_) { return 'system'; }
  }
  function applyTheme(value) {
    const theme = ['light', 'dark'].includes(value) ? value : 'system';
    if (theme === 'system') document.documentElement.removeAttribute('data-theme');
    else document.documentElement.dataset.theme = theme;
    try { localStorage.setItem(THEME_KEY, theme); } catch (_) { /* privat läge */ }
  }
  applyTheme(storedTheme());

  // ── Hjälpare ────────────────────────────────────────────────────────────
  const esc = value => String(value ?? '').replace(/[&<>"']/g, ch => (
    { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[ch]));
  const num = value => (typeof value === 'number' && Number.isFinite(value) ? value : null);
  const fmtDuration = sec => {
    if (!num(sec)) return '–';
    const h = Math.floor(sec / 3600), m = Math.round((sec % 3600) / 60);
    return h ? `${h} h ${m} min` : `${m} min`;
  };
  const fmtKm = km => (num(km) ? `${km.toLocaleString('sv-SE', { maximumFractionDigits: 1 })} km` : '');
  const DAY_SHORT = ['mån', 'tis', 'ons', 'tor', 'fre', 'lör', 'sön'];
  const isoDate = d => `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`;
  const todayIso = () => isoDate(new Date());

  // plan_sessions lagrar planvecka + veckodag. Planveckor räknas från måndagen
  // i ISO-vecka 1 2026 (plan_calendar.PLAN_EPOCH) och fortsätter över nyår.
  const PLAN_EPOCH = new Date(2025, 11, 29);
  const sessionDate = (week, dow) => {
    const d = new Date(PLAN_EPOCH);
    d.setDate(d.getDate() + 7 * (Number(week) - 1) + Number(dow));
    return d;
  };
  const isoWeekNumber = date => {
    const d = new Date(Date.UTC(date.getFullYear(), date.getMonth(), date.getDate()));
    const day = d.getUTCDay() || 7;
    d.setUTCDate(d.getUTCDate() + 4 - day);
    const yearStart = new Date(Date.UTC(d.getUTCFullYear(), 0, 1));
    return Math.ceil(((d - yearStart) / 86400000 + 1) / 7);
  };

  let toastTimer;
  function toast(text) {
    const el = $('#toast');
    el.textContent = text;
    el.classList.add('show');
    clearTimeout(toastTimer);
    toastTimer = setTimeout(() => el.classList.remove('show'), 2800);
  }

  // ── API ─────────────────────────────────────────────────────────────────
  let csrfToken = '';
  let me = null;

  class ApiError extends Error {
    constructor(message, status, code) { super(message); this.status = status; this.code = code; }
  }

  async function api(path, { method = 'GET', body } = {}) {
    const headers = { Accept: 'application/json' };
    if (body !== undefined) headers['Content-Type'] = 'application/json';
    if (method !== 'GET') headers['X-CSRF-Token'] = csrfToken;
    let res;
    try {
      res = await fetch(path, {
        method, headers, credentials: 'same-origin',
        body: body === undefined ? undefined : JSON.stringify(body),
      });
    } catch (_) {
      throw new ApiError('Ingen kontakt med servern. Kolla uppkopplingen.', 0, 'network');
    }
    let data = null;
    try { data = await res.json(); } catch (_) { data = null; }
    if (res.status === 401 && !path.startsWith('/api/login')) {
      showLogin('Sessionen har gått ut. Logga in igen.');
      throw new ApiError('Inte inloggad.', 401, 'unauthorized');
    }
    if (!res.ok) {
      const message = (data && (data.message || data.error)) || `Servern svarade ${res.status}.`;
      throw new ApiError(message, res.status, data && data.code);
    }
    return data || {};
  }

  // Samma svar återanvänds mellan flikarna i två minuter, så att det går
  // snabbt att hoppa fram och tillbaka utan att belasta servern.
  const cache = new Map();
  async function cached(path, maxAgeMs = 120000) {
    const hit = cache.get(path);
    if (hit && Date.now() - hit.at < maxAgeMs) return hit.data;
    const data = await api(path);
    cache.set(path, { at: Date.now(), data });
    return data;
  }

  // ── Inloggning ──────────────────────────────────────────────────────────
  function showLogin(message = '') {
    $('#boot').hidden = true;
    $('#app').hidden = true;
    $('#login').hidden = false;
    $('#login-error').textContent = message;
  }

  function showApp() {
    $('#boot').hidden = true;
    $('#login').hidden = true;
    $('#app').hidden = false;
    route();
  }

  $('#login-form').addEventListener('submit', async event => {
    event.preventDefault();
    const form = event.currentTarget;
    const button = form.querySelector('button');
    button.disabled = true;
    $('#login-error').textContent = '';
    try {
      const data = await api('/api/login', {
        method: 'POST',
        body: { username: form.username.value.trim(), password: form.password.value },
      });
      csrfToken = data.csrfToken || '';
      me = data;
      form.password.value = '';
      cache.clear();
      showApp();
    } catch (error) {
      $('#login-error').textContent = error.message;
    } finally {
      button.disabled = false;
    }
  });

  async function boot() {
    try {
      const data = await api('/api/session');
      if (data.authenticated) {
        csrfToken = data.csrfToken || '';
        me = data;
        showApp();
      } else {
        showLogin();
      }
    } catch (error) {
      showLogin(error.status === 401 ? '' : error.message);
    }
  }

  // ── Router ──────────────────────────────────────────────────────────────
  const VIEWS = {
    idag: { title: 'Idag', render: renderToday },
    plan: { title: 'Plan', render: renderPlan },
    coach: { title: 'Coach', render: renderCoach },
    halsa: { title: 'Hälsa', render: renderHealth },
    mer: { title: 'Mer', render: renderMore },
  };
  let currentView = '';

  function route() {
    const name = location.hash.replace('#', '') || 'idag';
    const view = VIEWS[name] ? name : 'idag';
    currentView = view;
    document.querySelectorAll('.tabbar a').forEach(a => {
      if (a.dataset.tab === view) a.setAttribute('aria-current', 'page');
      else a.removeAttribute('aria-current');
    });
    $('#view-title').textContent = VIEWS[view].title;
    const dateText = new Date().toLocaleDateString('sv-SE', { weekday: 'long', day: 'numeric', month: 'long' });
    $('#view-eyebrow').textContent = dateText.charAt(0).toUpperCase() + dateText.slice(1);
    $('#refresh-btn').hidden = view === 'mer';
    window.scrollTo(0, 0);
    const main = $('#view');
    main.classList.toggle('has-composer', view === 'coach');
    main.replaceChildren();
    VIEWS[view].render(main);
  }

  window.addEventListener('hashchange', () => { if (!$('#app').hidden) route(); });
  window.addEventListener('scroll', () => {
    document.querySelector('.topbar').classList.toggle('scrolled', window.scrollY > 4);
  }, { passive: true });

  $('#refresh-btn').addEventListener('click', async () => {
    const btn = $('#refresh-btn');
    btn.classList.add('spinning');
    cache.clear();
    route();
    setTimeout(() => btn.classList.remove('spinning'), 700);
  });

  // Kommer man tillbaka till appen efter en stund ska siffrorna vara färska.
  let hiddenAt = 0;
  document.addEventListener('visibilitychange', () => {
    if (document.hidden) { hiddenAt = Date.now(); return; }
    if (hiddenAt && Date.now() - hiddenAt > 5 * 60000 && !$('#app').hidden && currentView !== 'coach') {
      cache.clear();
      route();
    }
  });

  function skeleton(main, heights) {
    main.innerHTML = heights.map(h => `<div class="skeleton" style="height:${h}px"></div>`).join('');
  }

  function errorCard(main, error, retry) {
    main.innerHTML = `<div class="card error-card"><h2>Något gick fel</h2><p>${esc(error.message)}</p>
      <button class="btn-secondary" type="button">Försök igen</button></div>`;
    main.querySelector('button').addEventListener('click', retry);
  }

  // ── Ikoner för passtyper ────────────────────────────────────────────────
  const TYPE_ICON = {
    run: '<svg viewBox="0 0 24 24"><circle cx="14" cy="4.5" r="2"/><path d="M8 21l3-6 3 2v5M6 12l3-4 4 1 3 3 3 1M11 15l-1-4"/></svg>',
    easy: '<svg viewBox="0 0 24 24"><path d="M3 17c3 0 3-4 6-4s3 4 6 4 3-4 6-4"/></svg>',
    lift: '<svg viewBox="0 0 24 24"><path d="M6 7v10M18 7v10M3 9v6M21 9v6M6 12h12"/></svg>',
    race: '<svg viewBox="0 0 24 24"><path d="M5 21V4M5 4h11l-2 4 2 4H5"/></svg>',
    rest: '<svg viewBox="0 0 24 24"><path d="M20 14.5A8 8 0 1 1 9.5 4a6.5 6.5 0 0 0 10.5 10.5z"/></svg>',
  };
  const TYPE_LABEL = { run: 'Löpning', easy: 'Lugnt', lift: 'Styrka', race: 'Lopp', rest: 'Vila' };
  const STATUS_LABEL = { planned: 'planerat', completed: 'klart', missed: 'missat', skipped: 'hoppat', rescheduled: 'flyttat' };

  // ── Idag ────────────────────────────────────────────────────────────────
  const DECISION_BADGE = { keep: 'KÖR', reduce: 'LÄTTA', reschedule: 'FLYTTA', rest: 'VILA', no_session: 'VILODAG' };
  const CONFIDENCE_TEXT = { high: 'starkt underlag', medium: 'tunt underlag', low: 'svagt underlag' };
  const TONE_CLASS = { good: 'tone-good', warn: 'tone-warn', bad: 'tone-bad' };

  async function renderToday(main) {
    skeleton(main, [190, 150, 110, 110]);
    let today, health;
    try {
      [today, health] = await Promise.all([
        cached('/api/today'),
        cached('/api/health').catch(() => null),
      ]);
    } catch (error) {
      if (error.status !== 401) errorCard(main, error, () => { cache.clear(); renderToday(main); });
      return;
    }
    if (currentView !== 'idag') return;
    const tone = TONE_CLASS[today.tone] || 'tone-neutral';
    const readiness = num(today.readiness);
    const circumference = 2 * Math.PI * 46;
    const offset = circumference * (1 - Math.max(0, Math.min(100, readiness ?? 0)) / 100);
    const readinessTone = readiness == null ? '' : readiness >= 70 ? 'tone-good' : readiness >= 45 ? 'tone-warn' : 'tone-bad';
    const readinessText = readiness == null ? 'Väntar på nattens data'
      : readiness >= 70 ? 'Redo för kvalitet' : readiness >= 45 ? 'Normalt pass går bra' : 'Prioritera återhämtning';

    const s = today.session;
    const type = (s && s.type) || 'rest';
    const cb = today.coachBriefing || {};
    const briefingRows = [['Syfte', cb.purpose], ['Utförande', cb.execution], ['Ansträngning', cb.rpe], ['Tips', cb.tips], ['Energi', cb.fueling]]
      .filter(([, v]) => v);

    const sleep = health && health.sleep;
    const hrv = health && health.hrv;
    const rhr = health && health.restingHR;
    const bb = health && health.bodyBattery;

    main.innerHTML = `
      <article class="card decision ${tone}">
        <div class="decision-top">
          <span class="badge toned">${esc(DECISION_BADGE[today.action] || '–')}</span>
          <span class="confidence">${esc(CONFIDENCE_TEXT[today.confidence] || '')}</span>
        </div>
        <h3>${esc(today.headline || today.summary || 'Väntar på underlag')}</h3>
        ${today.detail ? `<p>${esc(today.detail)}</p>` : ''}
        ${(today.reasons || []).length ? `<div class="chips">${today.reasons.slice(0, 4).map(r => `<span class="chip">${esc(r)}</span>`).join('')}</div>` : ''}
        ${(today.warnings || []).length ? `<div class="warning">${esc(today.warnings.join(' '))}</div>` : ''}
      </article>

      <article class="card ${readinessTone}">
        <div class="readiness">
          <div class="ring">
            <svg viewBox="0 0 112 112" aria-hidden="true">
              <circle class="track" cx="56" cy="56" r="46"/>
              <circle class="prog" cx="56" cy="56" r="46" stroke-dasharray="${circumference.toFixed(1)}" stroke-dashoffset="${offset.toFixed(1)}"/>
            </svg>
            <div class="ring-val"><strong>${readiness == null ? '–' : Math.round(readiness)}</strong><span>CNS</span></div>
          </div>
          <div class="readiness-text">
            <h3>${esc(readinessText)}</h3>
            <p>Beredskap räknad ur HRV, sömn, Garmins beredskap och stress.</p>
          </div>
        </div>
      </article>

      <div class="stats">
        ${stat('Sömn', sleep && sleep.totalSec ? fmtDuration(sleep.totalSec) : '–', sleep && num(sleep.score) != null ? `poäng ${sleep.score}${sleep.fallback ? ' · förra natten' : ''}` : '')}
        ${stat('HRV', hrv && num(hrv.lastNightAvg) != null ? `${hrv.lastNightAvg}<small>ms</small>` : '–', hrv && hrv.weeklyAvg ? `snitt ${hrv.weeklyAvg} ms` : '', true)}
        ${stat('Vilopuls', rhr && num(rhr.value) != null ? `${rhr.value}<small>bpm</small>` : '–', rhr && rhr.sevenDayAvg ? `7 d snitt ${rhr.sevenDayAvg}` : '', true)}
        ${stat('Body Battery', bb && num(bb.current) != null ? `${bb.current}` : '–', bb && num(bb.max) != null ? `max ${bb.max} i dag` : '', true)}
      </div>

      <h2 class="section-title">Dagens pass</h2>
      <article class="card">
        ${s ? `
          <div class="session type-${esc(type)}">
            <div class="session-icon">${TYPE_ICON[type] || TYPE_ICON.run}</div>
            <div>
              <h3>${esc(s.title || TYPE_LABEL[type] || 'Pass')}</h3>
              ${s.detail ? `<p>${esc(s.detail)}</p>` : ''}
              <div class="session-meta">${esc([TYPE_LABEL[type], fmtKm(num(s.km))].filter(Boolean).join(' · '))}</div>
            </div>
          </div>
          ${briefingRows.length ? `<details class="briefing"><summary>Coachens genomgång</summary><dl>
            ${briefingRows.map(([k, v]) => `<dt>${esc(k)}</dt><dd>${esc(v)}</dd>`).join('')}</dl></details>` : ''}
        ` : `<div class="session type-rest"><div class="session-icon">${TYPE_ICON.rest}</div><div><h3>Inget pass i dag</h3><p>Vila och återhämtning.</p></div></div>`}
      </article>

      <h2 class="section-title">Senaste passen</h2>
      <div id="recent"><div class="skeleton" style="height:120px"></div></div>
    `;
    renderRecent($('#recent', main));
  }

  function stat(label, value, sub, raw = false) {
    return `<div class="stat"><div class="stat-label">${esc(label)}</div>
      <div class="stat-value">${raw ? value : esc(value)}</div>
      ${sub ? `<div class="stat-sub">${esc(sub)}</div>` : ''}</div>`;
  }

  async function renderRecent(box) {
    try {
      const data = await cached('/api/activities?days=14');
      const items = (data.activities || []).slice(0, 4);
      if (!items.length) { box.innerHTML = '<div class="card empty">Inga pass de senaste två veckorna.</div>'; return; }
      box.innerHTML = `<div class="plan-list">${items.map(a => {
        const typeKey = (a.activityType && a.activityType.typeKey) || a.type || '';
        const t = /strength|lift/.test(typeKey) ? 'lift' : /run/.test(typeKey) ? 'run' : 'easy';
        const when = new Date(String(a.startTimeLocal || a.date || '').replace(' ', 'T'));
        const km = num(a.distance) ? a.distance / 1000 : null;
        const meta = [fmtKm(km ? Math.round(km * 10) / 10 : null), fmtDuration(num(a.duration)), num(a.averageHR) ? `${Math.round(a.averageHR)} bpm` : '']
          .filter(v => v && v !== '–').join(' · ');
        return `<div class="plan-row type-${t}"><summary>
          <div class="plan-day"><strong>${Number.isNaN(when.getTime()) ? '' : when.getDate()}</strong><span>${Number.isNaN(when.getTime()) ? '' : DAY_SHORT[(when.getDay() + 6) % 7]}</span></div>
          <div class="plan-title">${esc(a.activityName || a.name || 'Pass')}<small>${esc(meta)}</small></div>
          <span class="plan-dot"></span></summary></div>`;
      }).join('')}</div>`;
    } catch (error) {
      if (error.status !== 401) box.innerHTML = `<div class="card empty">${esc(error.message)}</div>`;
    }
  }

  // ── Plan ────────────────────────────────────────────────────────────────
  async function renderPlan(main) {
    skeleton(main, [60, 260, 60, 260]);
    let data;
    try {
      data = await cached('/api/plan');
    } catch (error) {
      if (error.status !== 401) errorCard(main, error, () => { cache.clear(); renderPlan(main); });
      return;
    }
    if (currentView !== 'plan') return;
    const now = new Date();
    const monday = new Date(now.getFullYear(), now.getMonth(), now.getDate() - ((now.getDay() + 6) % 7));
    const end = new Date(monday); end.setDate(end.getDate() + 21);
    const today = todayIso();

    const weeks = new Map();
    for (const s of data.sessions || []) {
      const d = sessionDate(s.week, s.dow);
      if (d < monday || d >= end) continue;
      const key = s.week;
      if (!weeks.has(key)) weeks.set(key, []);
      weeks.get(key).push({ ...s, _date: d });
    }
    if (!weeks.size) {
      main.innerHTML = '<div class="card empty">Ingen plan de kommande tre veckorna. Be coachen lägga upp en.</div>';
      return;
    }
    main.innerHTML = [...weeks.entries()].sort((a, b) => a[0] - b[0]).map(([week, sessions]) => {
      sessions.sort((a, b) => a._date - b._date);
      const km = sessions.reduce((sum, s) => sum + (num(s.km) || 0), 0);
      const first = sessionDate(week, 0);
      const label = first <= now && now < new Date(first.getTime() + 7 * 86400000) ? 'Den här veckan' : `Vecka ${isoWeekNumber(first)}`;
      return `<section class="week">
        <div class="week-head"><h2>${esc(label)}</h2><span>${km ? esc(fmtKm(Math.round(km * 10) / 10)) : ''}</span></div>
        <div class="plan-list">${sessions.map(s => {
          const type = TYPE_LABEL[s.type] ? s.type : 'run';
          const iso = isoDate(s._date);
          const status = s.status && s.status !== 'planned'
            ? `<span class="status status-${esc(s.status)}">${esc(STATUS_LABEL[s.status] || s.status)}</span>` : '<span class="plan-dot"></span>';
          const detail = [s.detail, s.strength_recommendation_text].filter(Boolean).join('\n');
          return `<details class="plan-row type-${type}${iso === today ? ' is-today' : ''}">
            <summary>
              <div class="plan-day"><strong>${s._date.getDate()}</strong><span>${DAY_SHORT[s.dow] || ''}</span></div>
              <div class="plan-title">${esc(s.title || TYPE_LABEL[type])}<small>${esc([TYPE_LABEL[type], fmtKm(num(s.km))].filter(Boolean).join(' · '))}</small></div>
              ${status}
            </summary>
            <div class="plan-detail">${esc(detail || 'Ingen beskrivning.')}${s.ai_note ? `<div class="note">${esc(s.ai_note)}</div>` : ''}</div>
          </details>`;
        }).join('')}</div></section>`;
    }).join('');
  }

  // ── Coach ───────────────────────────────────────────────────────────────
  const CHAT_KEY = 'trainyze:app-chat';
  const SUGGESTIONS = ['Hur ligger jag till mot målet?', 'Vad ska jag tänka på i dagens pass?', 'Hur har jag sovit i veckan?', 'Är jag överbelastad?'];

  function loadChat() {
    try { return JSON.parse(sessionStorage.getItem(CHAT_KEY) || '[]'); } catch (_) { return []; }
  }
  function saveChat(history) {
    try { sessionStorage.setItem(CHAT_KEY, JSON.stringify(history.slice(-20))); } catch (_) { /* privat läge */ }
  }

  // Enkel och säker formatering av coachens svar: allt escapas först, sedan
  // blir **fet**, punktlistor och stycken till HTML.
  function formatReply(text) {
    const blocks = esc(text).replace(/\*\*(.+?)\*\*/g, '<strong>$1</strong>').split(/\n{2,}/);
    return blocks.map(block => {
      const lines = block.split('\n');
      if (lines.every(l => /^\s*[-•*]\s+/.test(l))) {
        return `<ul>${lines.map(l => `<li>${l.replace(/^\s*[-•*]\s+/, '')}</li>`).join('')}</ul>`;
      }
      return `<p>${lines.join('<br>')}</p>`;
    }).join('');
  }

  function renderCoach(main) {
    const history = loadChat();
    main.innerHTML = `
      <div class="chat" id="chat">
        <div class="msg msg-coach"><p>Hej${me && me.username ? ' ' + esc(me.username) : ''}! Fråga om din träning, sömn eller plan – eller be mig flytta ett pass.</p></div>
      </div>
      <div class="suggestions" id="suggestions">${SUGGESTIONS.map(s => `<button type="button">${esc(s)}</button>`).join('')}</div>
      <form class="composer" id="composer">
        <textarea id="chat-input" rows="1" placeholder="Skriv till coachen…" enterkeyhint="send"></textarea>
        <button type="submit" aria-label="Skicka"><svg viewBox="0 0 24 24"><path d="M5 12h14M13 6l6 6-6 6"/></svg></button>
      </form>`;
    const chat = $('#chat', main);
    for (const turn of history) addMsg(chat, turn.role === 'user' ? 'user' : 'coach', turn.content);
    $('#suggestions', main).hidden = history.length > 0;

    const input = $('#chat-input', main);
    const form = $('#composer', main);
    input.addEventListener('input', () => { input.style.height = 'auto'; input.style.height = Math.min(input.scrollHeight, 120) + 'px'; });
    input.addEventListener('keydown', e => {
      if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); form.requestSubmit(); }
    });
    $('#suggestions', main).addEventListener('click', e => {
      if (e.target.matches('button')) { input.value = e.target.textContent; form.requestSubmit(); }
    });
    form.addEventListener('submit', async e => {
      e.preventDefault();
      const message = input.value.trim();
      if (!message) return;
      const send = form.querySelector('button');
      send.disabled = true;
      input.value = '';
      input.style.height = '';
      $('#suggestions', main).hidden = true;
      addMsg(chat, 'user', message);
      const typing = addMsg(chat, 'coach', null);
      typing.innerHTML = '<span class="typing" aria-label="Coachen skriver"><i></i><i></i><i></i></span>';
      scrollChat();
      const prior = loadChat();
      const requestId = (crypto.randomUUID && crypto.randomUUID()) || String(Date.now());
      try {
        // Samma requestId vid ett nytt försök: servern lämnar då ut svaret
        // den redan räknat fram i stället för att köra frågan två gånger.
        let data;
        try {
          data = await api('/api/assistant', { method: 'POST', body: { message, history: prior, requestId } });
        } catch (error) {
          if (error.code !== 'network') throw error;
          data = await api('/api/assistant', { method: 'POST', body: { message, history: prior, requestId } });
        }
        if (!data.reply) throw new Error(data.error || 'Inget svar.');
        typing.innerHTML = formatReply(data.reply);
        saveChat([...prior, { role: 'user', content: message }, { role: 'assistant', content: data.reply }]);
        if (data.planChanged || data.changes) cache.delete('/api/plan');
      } catch (error) {
        typing.remove();
        if (error.status !== 401) addMsg(chat, 'error', error.message);
      } finally {
        send.disabled = false;
        scrollChat();
      }
    });
  }

  function addMsg(chat, kind, text) {
    const el = document.createElement('div');
    el.className = `msg msg-${kind}`;
    if (text != null) el.innerHTML = kind === 'coach' ? formatReply(text) : `<p>${esc(text)}</p>`;
    chat.appendChild(el);
    return el;
  }
  const scrollChat = () => window.scrollTo({ top: document.body.scrollHeight, behavior: 'smooth' });

  // ── Hälsa ───────────────────────────────────────────────────────────────
  function sparkline(values) {
    const v = (values || []).filter(x => num(x) != null);
    if (v.length < 2) return '';
    const min = Math.min(...v), max = Math.max(...v), span = max - min || 1;
    const pts = v.map((x, i) => `${(i / (v.length - 1)) * 100},${26 - ((x - min) / span) * 22}`).join(' ');
    return `<svg class="spark" viewBox="0 0 100 28" preserveAspectRatio="none" aria-hidden="true"><polyline points="${pts}" vector-effect="non-scaling-stroke"/></svg>`;
  }

  async function renderHealth(main) {
    skeleton(main, [200, 110, 110, 110]);
    let health, spark;
    try {
      [health, spark] = await Promise.all([cached('/api/health'), cached('/api/health/spark').catch(() => ({}))]);
    } catch (error) {
      if (error.status !== 401) errorCard(main, error, () => { cache.clear(); renderHealth(main); });
      return;
    }
    if (currentView !== 'halsa') return;
    const s = health.sleep || {};
    const hrv = health.hrv || {};
    const rhr = health.restingHR || {};
    const stress = health.stress || {};
    const bb = health.bodyBattery || {};
    const spo2 = health.spo2 || {};
    const resp = health.respiration || {};
    const ready = health.readiness || {};
    const phases = [['ph-deep', 'Djup', s.deepPct], ['ph-rem', 'REM', s.remPct], ['ph-light', 'Lätt', s.lightPct], ['ph-awake', 'Vaken', s.awakePct]];

    main.innerHTML = `
      <article class="card">
        <h2>Sömn${s.fallback ? ' · förra natten' : ''}</h2>
        <div class="big-number">${s.totalSec ? esc(fmtDuration(s.totalSec)) : '–'}${num(s.score) != null ? `<small>poäng ${s.score}</small>` : ''}</div>
        ${s.totalSec ? `<div class="phases">${phases.map(([c, , p]) => `<span class="${c}" style="width:${num(p) || 0}%"></span>`).join('')}</div>
        <div class="legend">${phases.map(([c, label, p]) => `<span><i class="${c}"></i>${label} ${num(p) ?? 0}%</span>`).join('')}</div>` : '<p class="muted">Ingen sömn registrerad i natt.</p>'}
      </article>

      <div class="stats">
        <div class="stat"><div class="stat-label">HRV</div>
          <div class="stat-value">${num(hrv.lastNightAvg) ?? '–'}<small>ms</small></div>
          <div class="stat-sub">${esc(hrv.balancedLow != null ? `baslinje ${hrv.balancedLow}–${hrv.balancedUpper}` : hrv.status || '')}</div>${sparkline(spark.hrv)}</div>
        <div class="stat"><div class="stat-label">Vilopuls</div>
          <div class="stat-value">${num(rhr.value) ?? '–'}<small>bpm</small></div>
          <div class="stat-sub">${rhr.sevenDayAvg ? `7 d snitt ${esc(rhr.sevenDayAvg)}` : ''}</div>${sparkline(spark.rhr)}</div>
        <div class="stat"><div class="stat-label">Sömnpoäng</div>
          <div class="stat-value">${num(s.score) ?? '–'}</div>
          <div class="stat-sub">senaste 7 nätterna</div>${sparkline(spark.sleep)}</div>
        <div class="stat"><div class="stat-label">Belastning</div>
          <div class="stat-value">${spark.strain && spark.strain.length ? esc(spark.strain[spark.strain.length - 1]) : '–'}</div>
          <div class="stat-sub">strain i dag (0–100)</div>${sparkline(spark.strain)}</div>
        <div class="stat"><div class="stat-label">Stress</div>
          <div class="stat-value">${num(stress.avg) ?? '–'}</div>
          <div class="stat-sub">${num(stress.max) != null ? `max ${stress.max}` : ''}</div></div>
        <div class="stat"><div class="stat-label">Body Battery</div>
          <div class="stat-value">${num(bb.current) ?? '–'}</div>
          <div class="stat-sub">${num(bb.charged) != null ? `+${bb.charged} / −${num(bb.drained) ?? 0}` : ''}</div></div>
        <div class="stat"><div class="stat-label">Beredskap</div>
          <div class="stat-value">${num(ready.score) ?? '–'}</div>
          <div class="stat-sub">${esc(ready.level ? String(ready.level).toLowerCase() : 'Garmin')}</div></div>
        <div class="stat"><div class="stat-label">SpO₂ · andning</div>
          <div class="stat-value">${num(spo2.avg) != null ? `${spo2.avg}<small>%</small>` : '–'}</div>
          <div class="stat-sub">${num(resp.sleepAvg) != null ? `${resp.sleepAvg} andetag/min i sömn` : ''}</div></div>
      </div>`;
  }

  // ── Mer ─────────────────────────────────────────────────────────────────
  function isStandalone() {
    return window.matchMedia('(display-mode: standalone)').matches || window.navigator.standalone === true;
  }

  function renderMore(main) {
    const theme = storedTheme();
    main.innerHTML = `
      <div class="list">
        <div><span>Inloggad som</span><strong>${esc((me && me.username) || '')}</strong></div>
        <div><span>Garmin</span><span class="muted">${me && me.garminConnected ? 'Kopplad' : 'Inte kopplad'}</span></div>
      </div>
      <div class="list">
        <div><span>Utseende</span>
          <div class="segmented" id="theme">
            ${[['system', 'Auto'], ['light', 'Ljust'], ['dark', 'Mörkt']].map(([k, l]) =>
              `<button type="button" data-theme="${k}" aria-pressed="${theme === k}">${l}</button>`).join('')}
          </div>
        </div>
      </div>
      ${isStandalone() ? '' : `<p class="hint">Installera appen: tryck på <strong>Dela</strong> i Safari och välj <strong>Lägg till på hemskärmen</strong>. Då öppnas Trainyze i helskärm och kan skicka notiser.</p>`}
      <div class="list">
        <a href="/" rel="noopener">Öppna webbversionen <span class="muted">›</span></a>
        <button type="button" class="danger" id="logout">Logga ut</button>
      </div>`;
    $('#theme', main).addEventListener('click', e => {
      const btn = e.target.closest('button');
      if (!btn) return;
      applyTheme(btn.dataset.theme);
      main.querySelectorAll('#theme button').forEach(b => b.setAttribute('aria-pressed', String(b === btn)));
    });
    $('#logout', main).addEventListener('click', async () => {
      try { await api('/api/logout', { method: 'POST' }); } catch (_) { /* utloggad ändå lokalt */ }
      try { sessionStorage.removeItem(CHAT_KEY); } catch (_) { /* privat läge */ }
      cache.clear();
      csrfToken = '';
      me = null;
      showLogin();
    });
  }

  // ── Notiser kräver en service worker; den delas med webbsidan (scope "/"). ─
  if ('serviceWorker' in navigator && !params.has('sim')) {
    navigator.serviceWorker.register('/sw.js').catch(() => { /* notiser blir bara otillgängliga */ });
  }

  window.addEventListener('trainyze:toast', e => toast(e.detail));
  boot();
})();
