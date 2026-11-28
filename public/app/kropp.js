// Kropp-fliken i mobilappen: Läget, Känningar (i app.js) och Vanor.
//
// Laddas efter app.js och använder dess globala hjälpare (escapeHtml, goto,
// currentHealthData). Inget här frågar en AI: beredskapen och belastningen
// kommer färdiga från servern (/api/today, /api/strain), normalnivåerna är
// enkel statistik på dina egna nätter och vanornas effekter räknas av
// lifestyle.analyze_impacts på servern.
(() => {
  const $ = id => document.getElementById(id);
  const esc = value => escapeHtml(String(value ?? ''));
  const isoDay = date => date.toLocaleDateString('sv-SE');
  const shortDate = iso => new Date(iso + 'T12:00:00').toLocaleDateString('sv-SE', { day: 'numeric', month: 'short' });
  const num = (value, digits = 0) => value == null ? '–'
    : Number(value).toLocaleString('sv-SE', { minimumFractionDigits: digits, maximumFractionDigits: digits });
  const signed = (value, digits = 0) => (value > 0 ? '+' : value < 0 ? '−' : '±') + num(Math.abs(value), digits);

  async function getJson(url) {
    const res = await fetch(url);
    const data = await res.json().catch(() => ({}));
    if (!res.ok) throw new Error(data.error || 'Kunde inte hämtas.');
    return data;
  }

  // ─── LÄGET ──────────────────────────────────────────────────

  // Anropas av loadHealth() i app.js med /api/health.
  function renderBodyHealth(h) {
    const label = $('h-date-label');
    if (label) label.textContent = new Date().toLocaleDateString('sv-SE', { weekday: 'long', day: 'numeric', month: 'long' });
    const fresh = $('bd-fresh');
    if (fresh) {
      fresh.textContent = h.notConnected
        ? 'Garmin är inte kopplat. Koppla det under Profil → Anslutningar.'
        : h.sleep?.fallback && h.sleep?.sourceDate
          ? `Klockan har inte synkat i natt än. Sömnen gäller natten till ${shortDate(h.sleep.sourceDate)}`
          : '';
      fresh.hidden = !fresh.textContent;
    }
    renderMove(h.daily || {});
  }

  async function loadBody() {
    const [today, sleep, stress, strain, niggles] = await Promise.allSettled([
      getJson('/api/today'),
      getJson('/api/sleep?days=30'),
      getJson('/api/health/stress-history?days=30'),
      getJson('/api/strain'),
      getJson('/api/niggles'),
    ]);
    renderReady(today.status === 'fulfilled' ? today.value : null);
    renderMetrics(sleep.status === 'fulfilled' ? sleep.value.nights || [] : [],
      stress.status === 'fulfilled' ? stress.value.values || [] : []);
    renderLoad(strain.status === 'fulfilled' ? strain.value : null);
    renderNiggleSummary(niggles.status === 'fulfilled' ? niggles.value : null);
  }

  function toneOf(t) {
    if (t.tone === 'good') return 'good';
    if (t.tone === 'bad' || t.tone === 'rest') return 'bad';
    if (t.tone) return 'watch';
    return t.readiness >= 70 ? 'good' : t.readiness >= 45 ? 'watch' : 'bad';
  }

  function renderReady(t) {
    const card = $('bd-ready');
    if (!card) return;
    if (!t) {
      $('bd-ready-title').textContent = 'Beredskapen kunde inte hämtas';
      $('bd-ready-detail').textContent = '';
      return;
    }
    card.dataset.tone = toneOf(t);
    $('bd-ready-score').textContent = t.readiness ?? '–';
    $('bd-ready-title').textContent = t.summary || t.headline || '';
    $('bd-ready-detail').textContent = t.detail || '';
    const signals = t.signals || [];
    const quality = t.dataQuality;
    const thin = quality && quality.level === 'low'
      ? `<li class="bd-signal" data-state="neutral"><span>Underlag</span>${esc(quality.availableSignals)} av ${esc(quality.possibleSignals)} kroppssignaler finns i dag, så bedömningen lutar mot planen.</li>`
      : '';
    $('bd-signals').innerHTML = signals.map(s =>
      `<li class="bd-signal" data-state="${esc(s.state)}"><span>${esc(s.label)}</span>${esc(s.reason)}</li>`).join('')
      + thin || '<li class="bd-signal" data-state="positive"><span>Signaler</span>Inget avviker från din normalnivå.</li>';
  }

  // Vad varje mått heter, var det finns och åt vilket håll det är bra.
  const METRICS = [
    { key: 'hrv_avg', label: 'HRV', unit: 'ms', better: 'high' },
    { key: 'resting_hr', label: 'Vilopuls', unit: 'bpm', better: 'low' },
    { key: 'sleep_score', label: 'Sömnpoäng', unit: '', better: 'high' },
    { key: 'sleep_hours', label: 'Sömn', unit: 'h', better: 'high', digits: 1 },
    { key: 'body_battery', label: 'Body Battery', unit: '', better: 'high' },
    { key: 'stress', label: 'Stress', unit: '', better: 'low', note: 'dagssnitt' },
  ];

  // Normalnivå = medel ± en standardavvikelse av de tidigare dagarna (minst 7).
  // Ett värde utanför bandet är ovanligt för just dig, inte för folk i allmänhet.
  function baseline(values) {
    if (values.length < 7) return null;
    const mean = values.reduce((a, b) => a + b, 0) / values.length;
    const sd = Math.sqrt(values.reduce((a, b) => a + (b - mean) ** 2, 0) / values.length);
    return { mean, sd: Math.max(sd, Math.abs(mean) * 0.02, 0.1) };
  }

  function metricStatus(metric, latest, base) {
    if (!base) return { state: 'neutral', text: 'Samlar data' };
    // Jämför med de avrundade siffrorna som visas, så att "51, normalt 46–51"
    // aldrig står bredvid "högre än vanligt".
    const factor = 10 ** (metric.digits || 0);
    const round = v => Math.round(v * factor) / factor;
    const z = (latest - base.mean) / base.sd;
    const shown = round(latest);
    if (shown >= round(base.mean - base.sd) && shown <= round(base.mean + base.sd)) return { state: 'ok', text: 'Normalt' };
    const good = metric.better === 'high' ? z > 0 : z < 0;
    const word = z > 0 ? 'Högre' : 'Lägre';
    return { state: good ? 'good' : Math.abs(z) >= 2 ? 'bad' : 'watch', text: `${word} än vanligt` };
  }

  function sparkline(points, base, state) {
    const w = 120, h = 34, pad = 3;
    const values = points.map(p => p.value);
    const lo = Math.min(...values, base ? base.mean - base.sd : Infinity);
    const hi = Math.max(...values, base ? base.mean + base.sd : -Infinity);
    const span = hi - lo || 1;
    const x = i => pad + (i / Math.max(points.length - 1, 1)) * (w - 2 * pad);
    const y = v => h - pad - ((v - lo) / span) * (h - 2 * pad);
    const band = base
      ? `<rect class="bd-band" x="0" width="${w}" y="${y(base.mean + base.sd).toFixed(1)}" height="${(y(base.mean - base.sd) - y(base.mean + base.sd)).toFixed(1)}"/>`
      : '';
    const line = points.map((p, i) => `${x(i).toFixed(1)},${y(p.value).toFixed(1)}`).join(' ');
    const last = points.length - 1;
    return `<svg class="bd-spark" viewBox="0 0 ${w} ${h}" preserveAspectRatio="none" aria-hidden="true">${band}
      <polyline points="${line}"/><circle data-state="${state}" cx="${x(last).toFixed(1)}" cy="${y(points[last].value).toFixed(1)}" r="2.6"/></svg>`;
  }

  function renderMetrics(nights, stress) {
    const root = $('bd-metrics');
    if (!root) return;
    const series = {};
    for (const m of METRICS) {
      const source = m.key === 'stress' ? stress.map(s => ({ date: s.date, value: s.value }))
        : nights.map(n => ({ date: n.date, value: n[m.key] }));
      series[m.key] = source.filter(p => p.value != null).map(p => ({ date: p.date, value: Number(p.value) }))
        .sort((a, b) => a.date.localeCompare(b.date)).slice(-30);
    }
    const rows = METRICS.filter(m => series[m.key].length).map(m => {
      const points = series[m.key];
      const latest = points[points.length - 1];
      const base = baseline(points.slice(0, -1).map(p => p.value));
      const status = metricStatus(m, latest.value, base);
      const week = points.slice(-7).map(p => p.value);
      const weekAvg = week.reduce((a, b) => a + b, 0) / week.length;
      const unit = m.unit ? ` ${m.unit}` : '';
      const digits = m.digits || 0;
      const normal = base ? `normalt ${num(base.mean - base.sd, digits)}–${num(base.mean + base.sd, digits)}${unit}` : 'behöver 7 dagar';
      return `<div class="bd-metric">
        <div class="bd-metric-name"><strong>${esc(m.label)}</strong><small>${esc(m.note || shortDate(latest.date))}</small></div>
        ${sparkline(points.slice(-14), base, status.state)}
        <div class="bd-metric-val"><strong>${num(latest.value, digits)}</strong><small>${esc(m.unit)}</small></div>
        <div class="bd-metric-foot"><span class="bd-state" data-state="${status.state}">${esc(status.text)}</span>
          <span>${esc(normal)} · 7 d ${num(weekAvg, digits)}</span></div>
      </div>`;
    });
    root.innerHTML = rows.join('') || '<p class="sl-empty">Ingen historik från klockan än.</p>';
  }

  const WEEKDAY = ['S', 'M', 'T', 'O', 'T', 'F', 'L'];

  function renderLoad(s) {
    const chart = $('bd-load-chart');
    if (!chart) return;
    if (!s || !s.series?.length) {
      chart.innerHTML = '<p class="sl-empty">Ingen belastning registrerad.</p>';
      return;
    }
    const pill = $('bd-load-pill');
    pill.textContent = s.headline || '';
    pill.dataset.tone = s.tone || '';
    $('bd-load-text').textContent = [s.detail, s.weekAvgStrain != null ? `Snitt senaste veckan: ${s.weekAvgStrain}.` : '']
      .filter(Boolean).join(' ');
    const max = Math.max(100, ...s.series.map(p => p.strain || 0));
    chart.innerHTML = s.series.map(p => {
      const day = new Date(p.t + 'T12:00:00');
      const pct = Math.round(((p.strain || 0) / max) * 100);
      const level = p.strain >= 60 ? 'high' : p.strain >= 30 ? 'mid' : 'low';
      return `<div class="bd-bar${p.t === s.date ? ' is-today' : ''}" title="${esc(shortDate(p.t))}: ${esc(p.strain)}">
        <i data-level="${level}" style="height:${Math.max(pct, 2)}%"></i><span>${WEEKDAY[day.getDay()]}</span></div>`;
    }).join('');
  }

  function renderNiggleSummary(data) {
    const card = $('bd-niggle-summary');
    if (!card) return;
    const active = data?.active || [];
    card.hidden = !active.length;
    $('bd-niggle-list').innerHTML = active.map(n =>
      `<div class="bd-niggle"><strong>${esc(n.label)}</strong><span data-level="${n.severity >= 7 ? 'high' : n.severity >= 4 ? 'mid' : 'low'}">${esc(n.severity)}/10</span></div>`).join('');
  }

  function renderMove(daily) {
    const card = $('bd-move');
    if (!card) return;
    if (daily.steps == null && daily.caloriesActive == null) { card.hidden = true; return; }
    card.hidden = false;
    const items = [
      ['Steg', num(daily.steps), daily.stepGoal ? `mål ${num(daily.stepGoal)}` : ''],
      ['Aktiva kcal', num(daily.caloriesActive), daily.caloriesTotal ? `totalt ${num(daily.caloriesTotal)}` : ''],
      ['Intensitet', daily.intensityMinutes == null ? '–' : `${num(daily.intensityMinutes)} min`, daily.intensityGoal ? `veckomål ${num(daily.intensityGoal)}` : ''],
      ['Sträcka', daily.distanceM == null ? '–' : `${num(daily.distanceM / 1000, 1)} km`, ''],
    ];
    $('bd-move-grid').innerHTML = items.map(([label, value, sub]) =>
      `<div><span>${esc(label)}</span><strong>${esc(value)}</strong><small>${esc(sub)}</small></div>`).join('');
  }

  // ─── VANOR ──────────────────────────────────────────────────

  // Fälten finns i lifestyle.normalize_entry på servern. Tomt (null) betyder
  // "inte loggat" och skiljer sig från 0: bara loggade dagar räknas i analysen.
  const HABITS = [
    { key: 'alcohol_drinks', label: 'Alkohol', type: 'count', unit: 'glas', step: 1, max: 30 },
    { key: 'caffeine_servings', label: 'Koffein', type: 'count', unit: 'koppar', step: 1, max: 20,
      time: { key: 'caffeine_last_time', label: 'senast' } },
    { key: 'water_liters', label: 'Vätska', type: 'count', unit: 'liter', step: 0.5, max: 15, digits: 1 },
    { key: 'outdoor_minutes', label: 'Utomhus', type: 'count', unit: 'min', step: 10, max: 1440 },
    { key: 'meditation_minutes', label: 'Nedvarvning', type: 'count', unit: 'min', step: 5, max: 600 },
    { key: 'late_meal', label: 'Sen måltid', hint: 'mindre än 2 h före sänggåendet', type: 'bool' },
    { key: 'screen_before_bed', label: 'Skärm sista timmen', type: 'bool' },
    { key: 'protein_target', label: 'Proteinmålet nått', type: 'bool' },
    { key: 'nutrition_quality', label: 'Kosten', hint: '1 = dålig, 5 = mycket bra', type: 'scale', min: 1, max: 5 },
    { key: 'travel', label: 'Resa', type: 'bool' },
    { key: 'medication_change', label: 'Ändrad medicin', type: 'bool' },
  ];

  let habitOffset = 1;
  let habitEntry = {};
  let habitDirty = false;

  const habitDate = () => isoDay(new Date(Date.now() - habitOffset * 86400000));

  async function loadHabits() {
    const msg = $('bd-habit-msg');
    document.querySelectorAll('[data-kropp="habit-day"]').forEach(b =>
      b.classList.toggle('is-active', Number(b.dataset.offset) === habitOffset));
    $('bd-habit-title').textContent = `Logga ${habitOffset ? 'i går' : 'i dag'} · ${shortDate(habitDate())}`;
    try {
      const data = await getJson(`/api/lifestyle?date=${habitDate()}`);
      habitEntry = data.entry || {};
      habitDirty = false;
      if (msg) msg.textContent = Object.values(habitEntry).some(v => v != null && v !== '') ? 'Sparad tidigare' : '';
      renderHabitForm();
      renderImpacts(data.insights);
    } catch (error) {
      habitEntry = {};
      renderHabitForm();
      $('bd-impacts').innerHTML = `<p class="sl-empty">${esc(error.message)}</p>`;
    }
  }

  function habitControl(h) {
    const v = habitEntry[h.key];
    if (h.type === 'count') {
      const time = h.time ? `<label class="bd-habit-time">${esc(h.time.label)}<input type="time" data-habit-time="${h.time.key}" value="${esc(habitEntry[h.time.key] || '')}"></label>` : '';
      return `<div class="bd-stepper">
        <button type="button" data-kropp="habit-step" data-key="${h.key}" data-dir="-1" aria-label="Minska ${esc(h.label)}">−</button>
        <output>${v == null ? '–' : num(v, h.digits || 0)} <small>${esc(h.unit)}</small></output>
        <button type="button" data-kropp="habit-step" data-key="${h.key}" data-dir="1" aria-label="Öka ${esc(h.label)}">+</button>
      </div>${time}`;
    }
    if (h.type === 'bool') {
      return `<div class="bd-choice">${[[true, 'Ja'], [false, 'Nej']].map(([val, text]) =>
        `<button type="button" class="bd-chip${v === val ? ' is-active' : ''}" data-kropp="habit-set" data-key="${h.key}" data-value="${val}">${text}</button>`).join('')}</div>`;
    }
    return `<div class="bd-choice">${Array.from({ length: h.max - h.min + 1 }, (_, i) => h.min + i).map(n =>
      `<button type="button" class="bd-chip${v === n ? ' is-active' : ''}" data-kropp="habit-set" data-key="${h.key}" data-value="${n}">${n}</button>`).join('')}</div>`;
  }

  function renderHabitForm() {
    const form = $('bd-habit-form');
    if (!form) return;
    form.innerHTML = HABITS.map(h => `<div class="bd-habit">
        <div class="bd-habit-label"><strong>${esc(h.label)}</strong>${h.hint ? `<small>${esc(h.hint)}</small>` : ''}</div>
        <div class="bd-habit-input">${habitControl(h)}</div>
      </div>`).join('')
      + `<label class="bd-habit-note">Anteckning<input type="text" maxlength="1000" data-habit-note value="${esc(habitEntry.note || '')}" placeholder="t.ex. bröllop, sov borta, sjuk"></label>`;
  }

  function changeHabit(key, value) {
    habitEntry[key] = value;
    habitDirty = true;
    $('bd-habit-msg').textContent = 'Inte sparat';
    renderHabitForm();
  }

  async function saveHabits() {
    const msg = $('bd-habit-msg');
    const form = $('bd-habit-form');
    form.querySelectorAll('[data-habit-time]').forEach(input => { habitEntry[input.dataset.habitTime] = input.value || null; });
    habitEntry.note = form.querySelector('[data-habit-note]')?.value.trim() || '';
    msg.textContent = 'Sparar…';
    try {
      const res = await fetch('/api/lifestyle', {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ ...habitEntry, date: habitDate() }),
      });
      const data = await res.json().catch(() => ({}));
      if (!res.ok) throw new Error(data.error || 'Kunde inte spara.');
      habitEntry = data.entry || habitEntry;
      habitDirty = false;
      msg.textContent = 'Sparat';
      renderHabitForm();
      renderImpacts(data.insights);
    } catch (error) {
      msg.textContent = error.message;
    }
  }

  const COMPONENTS = [['sleep_score', 'Sömnpoäng', '', 1], ['hrv', 'HRV', ' ms', 1], ['resting_hr', 'Vilopuls', ' bpm', -1]];

  function renderImpacts(data) {
    const root = $('bd-impacts');
    if (!root || !data) return;
    $('bd-impact-note').textContent = `${data.daysWithOutcomes} dagar med mätdata · ${data.windowDays} dagar`;
    const items = [...data.insights].sort((a, b) =>
      (b.ready - a.ready) || Math.abs(b.impact || 0) - Math.abs(a.impact || 0) || (b.yesDays + b.noDays) - (a.yesDays + a.noDays));
    root.innerHTML = items.map(i => {
      if (!i.ready) {
        const need = data.minimumPerGroup;
        const progress = Math.round(((Math.min(i.yesDays, need) + Math.min(i.noDays, need)) / (2 * need)) * 100);
        return `<div class="bd-impact" data-state="collecting">
          <div class="bd-impact-head"><strong>${esc(i.label)}</strong><span>Samlar data</span></div>
          <div class="bd-progress"><i style="width:${progress}%"></i></div>
          <small>${i.yesDays}/${need} dagar med · ${i.noDays}/${need} dagar utan</small>
        </div>`;
      }
      // impact är skillnaden i ett sammanvägt återhämtningsvärde (50 = ditt snitt,
      // 10 = en standardavvikelse). Positivt betyder bättre nätter efter dagen.
      const state = Math.abs(i.impact) < 2 ? 'neutral' : i.impact > 0 ? 'good' : 'bad';
      const verdict = state === 'neutral' ? 'Ingen tydlig skillnad' : state === 'good' ? 'Bättre återhämtning' : 'Sämre återhämtning';
      const parts = COMPONENTS.filter(([key]) => i.components?.[key] != null)
        .map(([key, label, unit]) => `${label} ${signed(i.components[key], key === 'sleep_score' ? 0 : 1)}${unit}`);
      return `<div class="bd-impact" data-state="${state}">
        <div class="bd-impact-head"><strong>${esc(i.label)}</strong><span>${esc(verdict)} ${signed(i.impact, 1)}</span></div>
        <small>${esc(parts.join(' · '))}</small>
        <small>${i.yesDays} dagar med, ${i.noDays} utan · ${i.confidence === 'medium' ? 'rimligt underlag' : 'tidigt underlag'}</small>
      </div>`;
    }).join('');
  }

  document.addEventListener('click', event => {
    const trigger = event.target.closest('[data-kropp]');
    if (!trigger) return;
    const action = trigger.dataset.kropp;
    if (action === 'habit-day') {
      if (habitDirty && !confirm('Du har osparade ändringar. Byta dag ändå?')) return;
      habitOffset = Number(trigger.dataset.offset);
      loadHabits();
    } else if (action === 'habit-step') {
      const h = HABITS.find(x => x.key === trigger.dataset.key);
      const current = habitEntry[h.key];
      const dir = Number(trigger.dataset.dir);
      // Första trycket loggar: minus ger 0 ("inget"), plus ger ett steg.
      const next = current == null ? (dir > 0 ? h.step : 0) : Math.min(h.max, Math.max(0, current + dir * h.step));
      changeHabit(h.key, next);
    } else if (action === 'habit-set') {
      const raw = trigger.dataset.value;
      const value = raw === 'true' ? true : raw === 'false' ? false : Number(raw);
      changeHabit(trigger.dataset.key, habitEntry[trigger.dataset.key] === value ? null : value);
    } else if (action === 'habit-save') {
      saveHabits();
    }
  });

  // Tid och anteckning skrivs in direkt, så att de inte tappas när formuläret
  // ritas om efter ett tryck på en annan vana.
  document.addEventListener('input', event => {
    const el = event.target;
    if (el.dataset?.habitTime) habitEntry[el.dataset.habitTime] = el.value || null;
    else if (el.dataset && 'habitNote' in el.dataset) habitEntry.note = el.value;
    else return;
    habitDirty = true;
    $('bd-habit-msg').textContent = 'Inte sparat';
  });

  window.renderBodyHealth = renderBodyHealth;
  window.loadBody = loadBody;
  window.loadHabits = loadHabits;

  // app.js hämtar /api/health direkt vid start; kom svaret innan den här filen
  // laddades ritar vi det nu.
  if (typeof currentHealthData !== 'undefined' && currentHealthData) renderBodyHealth(currentHealthData);
})();
