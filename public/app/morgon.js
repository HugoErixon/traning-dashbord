// Morgonsammanfattningen i mobilappen (sidan #page-morning).
//
// Morgonnotisen skickas av servern när nattens sömn har synkat
// (maybe_send_morning_report) och öppnar /app/?vy=morgon. Sidan visar natten,
// låter användaren checka in och visar dagens pass, omräknat efter
// incheckningen. Allt är regelbaserat: beslutet kommer från samma
// /api/today-utvärdering som Idag, och incheckningen sparas till
// /api/adaptive-plan/checkin som den utvärderingen redan läser.
(() => {
  const $ = id => document.getElementById(id);
  const esc = value => escapeHtml(String(value ?? ''));
  const todayIso = () => new Date().toLocaleDateString('sv-SE');
  const num = (value, digits = 0) => value == null ? '–'
    : Number(value).toLocaleString('sv-SE', { minimumFractionDigits: digits, maximumFractionDigits: digits });
  const clock = stamp => (stamp && stamp.length >= 16 ? stamp.slice(11, 16) : null);
  const hm = hours => {
    if (hours == null) return '–';
    const total = Math.round(hours * 60);
    return `${Math.floor(total / 60)} h ${String(total % 60).padStart(2, '0')} min`;
  };

  async function getJson(url, options) {
    const res = await fetch(url, options);
    const data = await res.json().catch(() => ({}));
    if (!res.ok) throw new Error(data.error || 'Kunde inte hämtas.');
    return data;
  }

  // Svarsalternativen översätts till incheckningens skalor
  // (adaptive_plan.normalize_checkin): energi och stress 1–10, ömhet och smärta 0–10.
  const QUESTIONS = [
    { key: 'energy', label: 'Energi', options: [[2, 'Slut'], [4, 'Låg'], [6, 'Okej'], [8, 'Bra'], [10, 'Topp']] },
    { key: 'soreness', label: 'Träningsvärk / stelhet', options: [[0, 'Ingen'], [3, 'Lite'], [6, 'Märkbar'], [9, 'Mycket']] },
    { key: 'stress', label: 'Stress', options: [[2, 'Låg'], [5, 'Normal'], [8, 'Hög']] },
    { key: 'illness', label: 'Sjuk?', options: [[false, 'Nej'], [true, 'Ja']] },
    { key: 'pain', label: 'Smärta eller skada', options: [[0, 'Ingen'], [3, 'Lätt'], [6, 'Tydlig'], [8, 'Stark']] },
    { key: 'available_minutes', label: 'Tid för träning i dag', options: [[null, 'Som planerat'], [30, '30 min'], [45, '45'], [60, '60'], [90, '90']] },
  ];

  let checkin = {};
  let niggleAreas = [];
  let activeNiggles = [];
  let dirty = false;

  async function loadMorning() {
    const now = new Date();
    $('mo-date').textContent = now.toLocaleDateString('sv-SE', { weekday: 'long', day: 'numeric', month: 'long' });
    const hour = now.getHours();
    $('mo-greeting').textContent = hour < 10 ? 'God morgon' : hour < 18 ? 'Hej' : 'God kväll';
    const [today, sleep, niggles] = await Promise.allSettled([
      getJson('/api/today'), getJson('/api/sleep?days=21'), getJson('/api/niggles'),
    ]);
    const t = today.status === 'fulfilled' ? today.value : null;
    niggleAreas = niggles.status === 'fulfilled' ? niggles.value.areas || [] : [];
    activeNiggles = niggles.status === 'fulfilled' ? niggles.value.active || [] : [];
    renderSleep(sleep.status === 'fulfilled' ? sleep.value : null);
    if (!dirty) {
      checkin = { ...(t?.checkin || {}) };
      renderCheckin();
    }
    renderPlan(t);
    renderLead(t, sleep.status === 'fulfilled' ? sleep.value : null);
  }

  function lastNight(data) {
    const night = (data?.nights || [])[0];
    return night && night.date === todayIso() ? night : null;
  }

  function renderLead(t, sleep) {
    const night = lastNight(sleep);
    const bits = [];
    if (night) bits.push(`Du sov ${hm(night.sleep_hours)}`);
    else bits.push('Nattens sömn har inte synkat från klockan än');
    if (t?.readiness != null) bits.push(`beredskap ${t.readiness}`);
    const has = Object.values(t?.checkin || {}).some(v => v !== null && v !== '' && v !== false);
    $('mo-lead').textContent = bits.join(', ') + '. ' + (has ? 'Du har checkat in i dag.' : 'Checka in så anpassas dagens pass efter hur du mår.');
  }

  function renderSleep(data) {
    const body = $('mo-sleep-body');
    const night = lastNight(data);
    const note = $('mo-sleep-note');
    if (!night) {
      const prev = (data?.nights || [])[0];
      note.textContent = '';
      body.innerHTML = `<p class="sl-empty">Natten har inte synkat än. Öppna Garmin Connect på telefonen så kommer den inom en kvart.${
        prev ? ` Senaste natten (${esc(prev.date)}): ${esc(hm(prev.sleep_hours))}, sömnpoäng ${esc(prev.sleep_score ?? '–')}.` : ''}</p>`;
      return;
    }
    const trends = data.summary?.trends || {};
    const avgH = trends.sleep_hours?.average;
    const avgScore = trends.sleep_score?.average;
    const start = clock(night.sleep_start), end = clock(night.sleep_end);
    note.textContent = start && end ? `${start}–${end}` : '';
    const diff = avgH != null ? night.sleep_hours - avgH : null;
    const vs = diff == null ? '' : Math.abs(diff) < 0.25 ? 'som vanligt'
      : `${diff > 0 ? '+' : '−'}${hm(Math.abs(diff))} mot ditt snitt`;
    const tonight = data.tonight?.night?.bedtime;
    const facts = [
      ['Sömnpoäng', night.sleep_score ?? '–', avgScore != null ? `snitt ${num(avgScore)}` : ''],
      ['Djupsömn', night.deep_pct != null ? `${night.deep_pct} %` : '–', 'mål 15–25 %'],
      ['REM', night.rem_pct != null ? `${night.rem_pct} %` : '–', 'mål 20–25 %'],
      ['HRV', night.hrv_avg != null ? `${night.hrv_avg} ms` : '–', ''],
      ['Vilopuls', night.resting_hr != null ? `${night.resting_hr} bpm` : '–', ''],
      ['Body Battery', night.body_battery ?? '–', ''],
    ];
    body.innerHTML = `<div class="mo-sleep-top"><strong>${esc(hm(night.sleep_hours))}</strong><span>${esc(vs)}</span></div>
      <div class="mo-facts">${facts.map(([label, value, sub]) =>
        `<div><span>${esc(label)}</span><strong>${esc(value)}</strong>${sub ? `<small>${esc(sub)}</small>` : ''}</div>`).join('')}</div>
      ${tonight ? `<p class="mo-tonight">I kväll: lägg dig runt <strong>${esc(tonight)}</strong> för att få din sömn.</p>` : ''}`;
  }

  function renderCheckin() {
    const form = $('mo-checkin-form');
    if (!form) return;
    const rows = QUESTIONS.map(q => `<div class="mo-q">
        <span class="mo-q-label">${esc(q.label)}</span>
        <div class="mo-q-options">${q.options.map(([value, text]) =>
          `<button type="button" class="bd-chip${checkin[q.key] === value || (value === null && checkin[q.key] == null && q.key === 'available_minutes') ? ' is-active' : ''}" data-morgon="pick" data-key="${q.key}" data-value="${esc(JSON.stringify(value))}">${esc(text)}</button>`).join('')}</div>
      </div>`);
    const symptoms = checkin.illness ? `<label class="mo-text">Symtom<input type="text" maxlength="300" data-morgon-field="illness_symptoms" value="${esc(checkin.illness_symptoms || '')}" placeholder="t.ex. feber, halsont, förkyld"></label>` : '';
    const area = checkin.pain > 0 ? `<label class="mo-text">Var?<select data-morgon-field="pain_area"><option value="">Välj område</option>${
      niggleAreas.map(a => `<option value="${esc(a.key)}"${checkin.pain_area === a.key || checkin.pain_area === a.label ? ' selected' : ''}>${esc(a.label)}</option>`).join('')}</select>
      <small>Sparas också som en känning, så att styrkeprogrammet tar hänsyn.</small></label>` : '';
    const niggles = activeNiggles.length
      ? `<p class="mo-niggles">Aktiva känningar: ${activeNiggles.map(n => `${esc(n.label)} ${esc(n.severity)}/10`).join(', ')} · <button type="button" class="ov-link" data-action="goto" data-page="niggles">Uppdatera</button></p>`
      : '';
    form.innerHTML = rows.slice(0, 4).join('') + symptoms + rows[4] + area + niggles + rows[5]
      + `<label class="mo-text">Något mer?<input type="text" maxlength="1000" data-morgon-field="note" value="${esc(checkin.note || '')}" placeholder="t.ex. sov dåligt, lång arbetsdag"></label>`;
    $('mo-checkin-note').textContent = Object.keys(checkin).length ? 'Ändra om något har ändrats' : '';
  }

  const ACTION_TONE = { keep: 'good', no_session: 'neutral', reduce: 'watch', reschedule: 'watch', rest: 'bad' };
  const ACTION_LABEL = { keep: 'Enligt plan', no_session: 'Vilodag', reduce: 'Lättare', reschedule: 'Flytta', rest: 'Vila' };

  function renderPlan(t) {
    const body = $('mo-plan-body');
    const pill = $('mo-plan-pill');
    if (!t) { body.innerHTML = '<p class="sl-empty">Dagens plan kunde inte hämtas.</p>'; return; }
    pill.textContent = ACTION_LABEL[t.action] || '';
    pill.dataset.tone = ACTION_TONE[t.action] || '';
    const s = t.session;
    const brief = t.coachBriefing || {};
    const change = t.proposedChange;
    const changeText = !change ? '' : change.action === 'reduce'
      ? `Förslag: kör ${change.km ? num(change.km, 1) + ' km' : 'kortare'} och lugnt i stället.`
      : change.action === 'reschedule' ? `Förslag: flytta passet till ${esc(change.suggestedDate || 'i morgon')}.`
      : change.action === 'rest' ? 'Förslag: stryk dagens pass.' : '';
    const meta = s ? [s.km ? `${num(s.km, s.km % 1 ? 1 : 0)} km` : '', s.estimated_minutes ? `ca ${s.estimated_minutes} min` : '']
      .filter(Boolean).join(' · ') : '';
    body.innerHTML = `
      ${s ? `<div class="mo-session"><strong>${esc(s.title || 'Dagens pass')}</strong>${meta ? `<span>${esc(meta)}</span>` : ''}</div>` : ''}
      <p class="mo-verdict"><strong>${esc(t.headline || t.summary || '')}</strong> ${esc(t.detail || '')}</p>
      ${changeText ? `<p class="mo-change">${changeText}</p>` : ''}
      ${(t.warnings || []).map(w => `<p class="mo-warning">${esc(w)}</p>`).join('')}
      ${s && brief.execution ? `<dl class="mo-brief">
        ${brief.purpose ? `<dt>Syfte</dt><dd>${esc(brief.purpose)}</dd>` : ''}
        <dt>Utförande</dt><dd>${esc(brief.execution)}</dd>
        ${brief.rpe ? `<dt>Ansträngning</dt><dd>${esc(brief.rpe)}</dd>` : ''}
      </dl>` : ''}
      ${(t.reasons || []).length ? `<ul class="mo-reasons">${t.reasons.slice(0, 3).map(r => `<li>${esc(r)}</li>`).join('')}</ul>` : ''}`;
  }

  async function saveCheckin() {
    const msg = $('mo-checkin-msg');
    const form = $('mo-checkin-form');
    form.querySelectorAll('[data-morgon-field]').forEach(el => { checkin[el.dataset.morgonField] = el.value.trim(); });
    const payload = {
      energy: checkin.energy ?? null, soreness: checkin.soreness ?? null, stress: checkin.stress ?? null,
      pain: checkin.pain ?? null, available_minutes: checkin.available_minutes ?? null,
      illness: checkin.illness === true, illness_symptoms: checkin.illness ? checkin.illness_symptoms || '' : '',
      pain_area: checkin.pain > 0 ? (niggleAreas.find(a => a.key === checkin.pain_area)?.label || checkin.pain_area || '') : '',
      note: checkin.note || '',
    };
    msg.textContent = 'Sparar…';
    try {
      await getJson('/api/adaptive-plan/checkin', {
        method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(payload),
      });
      // En smärta med område blir också en känning, som styrkeprogrammet läser.
      if (checkin.pain > 0 && niggleAreas.some(a => a.key === checkin.pain_area)) {
        await getJson('/api/niggles', {
          method: 'POST', headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ area: checkin.pain_area, severity: checkin.pain, note: 'Från morgonincheckningen' }),
        });
      }
      dirty = false;
      msg.textContent = 'Sparat. Dagens plan är omräknad.';
      await loadMorning();
    } catch (error) {
      msg.textContent = error.message;
    }
  }

  document.addEventListener('click', event => {
    const trigger = event.target.closest('[data-morgon]');
    if (!trigger) return;
    if (trigger.dataset.morgon === 'pick') {
      const key = trigger.dataset.key;
      const value = JSON.parse(trigger.dataset.value);
      checkin[key] = checkin[key] === value && key !== 'available_minutes' ? null : value;
      dirty = true;
      $('mo-checkin-msg').textContent = 'Inte sparat';
      renderCheckin();
    } else if (trigger.dataset.morgon === 'save') {
      saveCheckin();
    }
  });

  // Fritextfälten skrivs in direkt så att de överlever en omritning.
  document.addEventListener('input', event => {
    const field = event.target.dataset?.morgonField;
    if (!field) return;
    checkin[field] = event.target.value;
    dirty = true;
    $('mo-checkin-msg').textContent = 'Inte sparat';
  });

  // ─── Öppna rätt vy från en notis ──────────────────────────────
  function openFromUrl(href) {
    const url = new URL(href, location.origin);
    const view = url.searchParams.get('vy');
    if (view === 'morgon') { goto('morning'); return true; }
    return false;
  }


  // Appen var redan öppen: service workern skickar länken hit.
  navigator.serviceWorker?.addEventListener('message', event => {
    if (event.data?.type !== 'open-url') return;
    if (!openFromUrl(event.data.url)) location.href = event.data.url;
  });

  window.loadMorning = loadMorning;

  // Appen startades från notisen: /app/?vy=morgon. Körs sist så att
  // loadMorning finns när goto() anropar den.
  if (openFromUrl(location.href)) history.replaceState(null, '', location.pathname);
})();
