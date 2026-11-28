// Träning-fliken i mobilappen: Plan, Fysiologi och Intensitet.
//
// Laddas efter app.js och använder dess globala hjälpare (escapeHtml, loadPlan,
// loadSeasonPlan). Allt här är regelbaserat utan AI-anrop.
(() => {
  const $ = id => document.getElementById(id);
  const esc = value => escapeHtml(String(value ?? ''));
  const num = (value, digits = 0) => (value == null || Number.isNaN(Number(value)))
    ? '–'
    : Number(value).toLocaleString('sv-SE', { minimumFractionDigits: digits, maximumFractionDigits: digits });
  const signed = (value, digits = 1) => {
    if (value == null || Number.isNaN(Number(value))) return '–';
    const n = Number(value);
    return (n > 0 ? '+' : n < 0 ? '−' : '±') + num(Math.abs(n), digits);
  };
  const shortDate = iso => {
    if (!iso) return '';
    const d = new Date(iso + 'T12:00:00');
    return Number.isNaN(d.getTime()) ? String(iso)
      : d.toLocaleDateString('sv-SE', { day: 'numeric', month: 'short' });
  };
  const pct = value => value == null ? '–' : `${Math.round(Number(value) * 100)}%`;

  async function requestJson(url, opts) {
    const res = await fetch(url, opts);
    const data = await res.json().catch(() => ({}));
    if (!res.ok) throw new Error(data.error || data.reason || `Servern svarade ${res.status}`);
    return data;
  }

  function formatPaceSec(sec) {
    if (sec == null || !Number.isFinite(Number(sec))) return '';
    const s = Math.round(Number(sec));
    const m = Math.floor(s / 60);
    const r = s % 60;
    return `${m}:${String(r).padStart(2, '0')}`;
  }

  function formatPaceRange(z) {
    if (!z) return '–';
    if (z.pace) return z.pace;
    if (z.lowSec != null && z.highSec != null) {
      const a = formatPaceSec(z.lowSec);
      const b = formatPaceSec(z.highSec);
      return a === b ? `${a}/km` : `${a}–${b}/km`;
    }
    return '–';
  }

  // ─── PLAN (page-upcoming) ──────────────────────────────────

  const PHASES = [
    { key: 'bas', label: 'Bas' },
    { key: 'specifik', label: 'Specifik' },
    { key: 'skärpning', label: 'Skärpning' },
    { key: 'nedtrappning', label: 'Nedtrappning' },
    { key: 'lopp', label: 'Lopp' },
  ];

  function normalizePhaseKey(phase) {
    const p = String(phase || '').toLowerCase();
    if (p.includes('lopp') || p.includes('race')) return 'lopp';
    if (p.includes('nedtrapp') || p.includes('taper')) return 'nedtrappning';
    if (p.includes('skärp') || p.includes('sharpen') || p.includes('peak') || p.includes('loppfart')) return 'skärpning';
    if (p.includes('specifik') || p.includes('specific')) return 'specifik';
    return 'bas';
  }

  const PART_LABEL = { warmup: 'Uppvärmning', work: 'Huvuddel', cooldown: 'Nedjogg' };
  const ZONE_ORDER = ['E', 'M', 'HM', 'SUBT', 'T', 'I', 'R'];
  const ZONE_DESC = {
    E: 'Aerob bas och återhämtning',
    M: 'Maratonspecifik uthållighet',
    HM: 'Halvmaratonfart',
    SUBT: 'Norska modellen 2–3 mmol, mycket kontrollerad tröskeltid',
    T: 'Laktattröskel, ~1 h-fart',
    I: 'VO2max 3–5 min drag',
    R: 'Snabbhet och löpekonomi',
  };

  let planWeeks = 1;
  let planFrom = 'next';
  let activeWeekIdx = 0;
  let lastPreview = null;
  let controlsBound = false;

  function setPlanStatus(text, tone = '') {
    const el = $('tr-plan-status');
    if (!el) return;
    el.textContent = text || '';
    if (tone) el.dataset.tone = tone;
    else delete el.dataset.tone;
  }

  function renderPhaseCard(preview, seasonFallback) {
    const root = $('tr-phase-card');
    if (!root) return;

    const week = preview?.weeks?.[0];
    const race = preview?.race || seasonFallback?.race;
    if (!week && !seasonFallback?.available) {
      const reason = preview?.reason || seasonFallback?.reason || 'Sätt ett lopp eller mål under Profil för att se säsongsfasen.';
      root.innerHTML = `<div class="tr-empty">${esc(reason)}</div>`;
      return;
    }

    const curWeek = week || seasonFallback?.currentWeek || seasonFallback?.weeks?.[0] || {};
    const phaseKey = normalizePhaseKey(curWeek.phase || curWeek.phaseLabel);
    const activeIdx = PHASES.findIndex(p => p.key === phaseKey);
    const raceTitle = race?.label || race?.name || 'Mållopp';
    const daysLeft = race?.daysLeft != null ? `${num(race.daysLeft)} dagar kvar` : '';
    const inPhase = (curWeek.indexInPhase && curWeek.phaseWeeks)
      ? `Vecka ${curWeek.indexInPhase} av ${curWeek.phaseWeeks} i fasen`
      : (curWeek.phaseWeekNumber && curWeek.phaseWeeks ? `Vecka ${curWeek.phaseWeekNumber} av ${curWeek.phaseWeeks} i fasen` : '');

    const timeline = PHASES.map((p, idx) => {
      const cls = idx < activeIdx ? 'is-done' : idx === activeIdx ? 'is-active' : '';
      return `<div class="tr-tl-step ${cls}"><div class="tr-tl-bar"></div><span>${esc(p.label)}</span></div>`;
    }).join('');

    root.innerHTML = `<section class="tr-phase" aria-label="Aktuell träningsfas">
      <div class="tr-phase-top">
        <div>
          <div class="tr-phase-race">${esc(raceTitle)}${curWeek.recovery ? ' · Avlastningsvecka' : ''}</div>
          <h3 class="tr-phase-name">${esc(curWeek.phaseLabel || curWeek.phase || 'Träningsfas')}</h3>
        </div>
        ${daysLeft ? `<span class="sl-pill" data-tone="good">${esc(daysLeft)}</span>` : ''}
      </div>
      ${inPhase || curWeek.aim ? `<p class="tr-phase-sub">${esc([inPhase, curWeek.aim].filter(Boolean).join(' · '))}</p>` : ''}
      <div class="tr-phase-stats">
        <div class="tr-stat"><span>Veckomål</span><strong>${curWeek.targetKm != null ? `${num(curWeek.targetKm, 0)} km` : '–'}</strong></div>
        <div class="tr-stat"><span>Långpass</span><strong>${curWeek.longRunKm != null ? `${num(curWeek.longRunKm, 0)} km` : '–'}</strong></div>
        <div class="tr-stat"><span>VDOT</span><strong>${preview?.vdot != null ? num(preview.vdot, 1) : '–'}</strong></div>
      </div>
      <div class="tr-timeline" aria-label="Fas-tidslinje">${timeline}</div>
    </section>`;
  }

  function zoneBadge(zone, type) {
    const raw = String(zone || (type === 'lift' ? 'LIFT' : type === 'race' ? 'RACE' : '')).toUpperCase();
    if (!raw) return '';
    const label = raw === 'LIFT' ? 'Styrka' : raw === 'RACE' ? 'Lopp' : raw;
    return `<span class="tr-zone" data-zone="${esc(raw)}">${esc(label)}</span>`;
  }

  function renderSessionCard(s) {
    const slotBadge = s.slot === 'am' ? '<span class="tr-slot">AM</span>'
      : s.slot === 'pm' ? '<span class="tr-slot">PM</span>' : '';
    const metaParts = [];
    if (s.km != null && Number(s.km) > 0) metaParts.push(`${num(s.km, 1)} km`);
    if (s.minutes != null && Number(s.minutes) > 0) metaParts.push(`~${num(s.minutes)} min`);

    const structureHtml = Array.isArray(s.structure) && s.structure.length
      ? `<ul class="tr-structure">${s.structure.map(st =>
          `<li><span class="tr-part">${esc(PART_LABEL[st.part] || st.part)}</span><span>${esc(st.text)}</span></li>`
        ).join('')}</ul>`
      : '';

    const whyHtml = s.purpose
      ? `<details class="tr-why"><summary>Varför?</summary><p>${esc(s.purpose)}</p></details>`
      : '';

    return `<article class="tr-session">
      <div class="tr-session-top">
        <div class="tr-session-day">${esc(s.day || '')} ${esc(shortDate(s.date))}${slotBadge}</div>
        ${zoneBadge(s.zone, s.type)}
      </div>
      <div class="tr-session-title">
        <strong>${esc(s.title)}</strong>
        ${metaParts.length ? `<span class="tr-session-meta">${esc(metaParts.join(' · '))}</span>` : ''}
      </div>
      ${s.detail ? `<p class="tr-session-detail">${esc(s.detail)}</p>` : ''}
      ${structureHtml}
      ${whyHtml}
    </article>`;
  }

  function renderPreview(data) {
    const root = $('tr-plan-preview');
    if (!root) return;
    if (!data || data.available === false) {
      const msg = data?.reason || 'Planmotorn kunde inte bygga ett förslag just nu.';
      root.innerHTML = `<div class="tr-empty">${esc(msg)}</div>`;
      return;
    }

    const weeks = data.weeks || [];
    if (!weeks.length) {
      root.innerHTML = '<div class="tr-empty">Inga veckor att visa.</div>';
      return;
    }
    if (activeWeekIdx >= weeks.length) activeWeekIdx = 0;

    const tabsHtml = weeks.length > 1
      ? `<div class="tr-week-tabs">${weeks.map((w, i) =>
          `<button type="button" class="bd-chip${i === activeWeekIdx ? ' is-active' : ''}" data-tr-week-idx="${i}">v.${esc(w.isoWeek || w.week)}</button>`
        ).join('')}</div>`
      : '';

    const w = weeks[activeWeekIdx];
    const sum = w.summary || {};
    const easyPct = sum.easyTimeShare != null ? pct(sum.easyTimeShare) : pct(sum.easyShare);
    const summaryChips = [
      sum.km != null ? `<span class="tr-sum-chip">Totalt <strong>${num(sum.km, 0)} km</strong></span>` : '',
      sum.qualityKm != null ? `<span class="tr-sum-chip">Kvalitet <strong>${num(sum.qualityKm, 1)} km</strong></span>` : '',
      easyPct !== '–' ? `<span class="tr-sum-chip">Lugnt <strong>${esc(easyPct)}</strong></span>` : '',
      sum.hardDays != null ? `<span class="tr-sum-chip">Hårda dagar <strong>${num(sum.hardDays)}</strong></span>` : '',
    ].filter(Boolean).join('');

    const sessionsHtml = (w.sessions || []).length
      ? w.sessions.map(renderSessionCard).join('')
      : '<p class="sl-empty">Inga pass för denna vecka.</p>';

    const checksHtml = Array.isArray(w.checks) && w.checks.length
      ? `<ul class="tr-checks" aria-label="Regelkontroller">${w.checks.map(c =>
          `<li class="tr-check" data-ok="${c.ok ? 'true' : 'false'}">
            <span class="tr-check-icon" aria-hidden="true">${c.ok ? '✓' : '!'}</span>
            <span class="tr-check-body">${esc(c.text)}${c.source ? `<span class="tr-check-src">${esc(c.source)}</span>` : ''}</span>
          </li>`
        ).join('')}</ul>`
      : '';

    root.innerHTML = `${tabsHtml}
      <div class="tr-week-head">
        <strong>Vecka ${esc(w.isoWeek || w.week)} · ${esc(w.phaseLabel || w.phase || '')}</strong>
        <span>${esc(shortDate(w.monday))}${w.recovery ? ' · Avlastning' : ''}</span>
      </div>
      ${summaryChips ? `<div class="tr-week-summary">${summaryChips}</div>` : ''}
      <div class="tr-days">${sessionsHtml}</div>
      ${checksHtml}`;
  }

  function syncControlChips() {
    document.querySelectorAll('[data-tr-weeks]').forEach(btn => {
      btn.classList.toggle('is-active', Number(btn.dataset.trWeeks) === planWeeks);
    });
    document.querySelectorAll('[data-tr-from]').forEach(btn => {
      btn.classList.toggle('is-active', btn.dataset.trFrom === planFrom);
    });
  }

  async function fetchPreview() {
    const root = $('tr-plan-preview');
    if (root) root.innerHTML = '<p class="tr-loading">Bygger förslag från planmotorn…</p>';
    setPlanStatus('');
    try {
      const data = await requestJson(`/api/training-plan/preview?weeks=${planWeeks}&from=${encodeURIComponent(planFrom)}`);
      lastPreview = data;
      activeWeekIdx = 0;
      renderPreview(data);
      return data;
    } catch (err) {
      lastPreview = null;
      if (root) root.innerHTML = `<div class="tr-empty">${esc(err.message)}</div>`;
      return null;
    }
  }

  async function applyPlan() {
    const btn = $('tr-apply-btn');
    if (btn) btn.disabled = true;
    try {
      let preview = lastPreview;
      if (!preview || preview.from !== planFrom || (preview.weeks || []).length !== planWeeks) {
        preview = await fetchPreview();
      }
      if (!preview || preview.available === false) {
        setPlanStatus(preview?.reason || 'Kunde inte hämta förslag att lägga in.', 'bad');
        return;
      }
      const planned = preview.existing?.planned ?? 0;
      const kept = preview.existing?.kept ?? 0;
      const msg = `Detta ersätter ${planned} planerade pass och behåller ${kept} genomförda pass. Lägg in ${planWeeks} ${planWeeks === 1 ? 'vecka' : 'veckor'} i kalendern?`;
      if (!window.confirm(msg)) return;

      setPlanStatus('Lägger in passen i kalendern…');
      const res = await requestJson('/api/training-plan/apply', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ weeks: planWeeks, from: planFrom }),
      });
      setPlanStatus(`Inlagt: ${num(res.applied)} pass (${num(res.removed)} ersatta).`, 'good');
      if (typeof loadPlan === 'function') {
        await loadPlan();
        if (typeof loadSeasonPlan === 'function') loadSeasonPlan();
      } else if (typeof loadCalendar === 'function') {
        await loadCalendar();
      } else if (typeof renderCalendar === 'function') {
        renderCalendar();
      } else {
        window.location.reload();
      }
    } catch (err) {
      setPlanStatus(`Kunde inte lägga in: ${err.message}`, 'bad');
    } finally {
      if (btn) btn.disabled = false;
    }
  }

  function bindPlanControls() {
    if (controlsBound) return;
    const container = $('tr-engine-card');
    if (!container) return;
    controlsBound = true;
    container.addEventListener('click', e => {
      const weeksBtn = e.target.closest('[data-tr-weeks]');
      if (weeksBtn) {
        planWeeks = Number(weeksBtn.dataset.trWeeks) || 1;
        syncControlChips();
        fetchPreview();
        return;
      }
      const fromBtn = e.target.closest('[data-tr-from]');
      if (fromBtn) {
        planFrom = fromBtn.dataset.trFrom === 'current' ? 'current' : 'next';
        syncControlChips();
        fetchPreview();
        return;
      }
      const weekTab = e.target.closest('[data-tr-week-idx]');
      if (weekTab && lastPreview) {
        activeWeekIdx = Number(weekTab.dataset.trWeekIdx) || 0;
        renderPreview(lastPreview);
        return;
      }
      if (e.target.closest('#tr-preview-btn')) {
        fetchPreview();
        return;
      }
      if (e.target.closest('#tr-apply-btn')) {
        applyPlan();
      }
    });
  }

  async function loadTraining() {
    bindPlanControls();
    syncControlChips();
    const [curPreview, season] = await Promise.allSettled([
      requestJson('/api/training-plan/preview?weeks=1&from=current'),
      requestJson('/api/season-plan'),
    ]);
    renderPhaseCard(
      curPreview.status === 'fulfilled' ? curPreview.value : null,
      season.status === 'fulfilled' ? season.value : null
    );
    await fetchPreview();
  }

  // ─── FYSIOLOGI (page-physiology) ───────────────────────────

  function renderVdotSpark(trend) {
    if (!Array.isArray(trend) || trend.length < 2) return '';
    const pts = trend.filter(p => p && p.vdot != null).slice(-24);
    if (pts.length < 2) return '';
    const w = 140, h = 36, pad = 4;
    const vals = pts.map(p => Number(p.vdot));
    const lo = Math.min(...vals);
    const hi = Math.max(...vals);
    const span = hi - lo || 1;
    const x = i => pad + (i / (pts.length - 1)) * (w - 2 * pad);
    const y = v => h - pad - ((v - lo) / span) * (h - 2 * pad);
    const line = pts.map((p, i) => `${x(i).toFixed(1)},${y(Number(p.vdot)).toFixed(1)}`).join(' ');
    const last = pts[pts.length - 1];
    return `<div class="tr-spark-wrap">
      <div class="tr-spark-label">VDOT-trend (${num(pts[0].vdot, 1)} → ${num(last.vdot, 1)})</div>
      <svg class="tr-spark" viewBox="0 0 ${w} ${h}" aria-hidden="true">
        <polyline points="${line}"/>
        <circle cx="${x(pts.length - 1).toFixed(1)}" cy="${y(Number(last.vdot)).toFixed(1)}" r="2.8"/>
      </svg>
    </div>`;
  }

  function renderPhysiology(data) {
    const vdotCard = $('tr-phys-vdot');
    const zonesEl = $('tr-phys-zones');
    const equivsEl = $('tr-phys-equivs');
    const goalEl = $('tr-phys-goal');
    if (!vdotCard) return;

    if (!data || data.available === false) {
      const msg = data?.reason || 'Fysiologidata saknas ännu.';
      vdotCard.innerHTML = `<div class="tr-empty">${esc(msg)}</div>`;
      if (zonesEl) zonesEl.innerHTML = '';
      if (equivsEl) equivsEl.innerHTML = '';
      if (goalEl) goalEl.innerHTML = '';
      return;
    }

    const v = data.vdot || {};
    const lthr = data.lthr || {};
    vdotCard.innerHTML = `<div class="tr-vdot-hero">
        <div class="tr-vdot-num">
          <strong>${num(v.vdot, 1)}</strong>
          <span>${esc(v.sourceLabel || v.source || 'Aktuell VDOT')}</span>
        </div>
        <div class="tr-vdot-meta">
          <div class="tr-vdot-lthr">
            <strong>${lthr.value != null ? `${num(lthr.value)} bpm` : '–'}</strong>
            <span>Tröskelpuls (LTHR)${lthr.source ? ` · ${esc(lthr.source)}` : ''}</span>
          </div>
        </div>
      </div>
      ${renderVdotSpark(data.trend)}`;

    if (zonesEl) {
      const zMap = data.zones || {};
      const rows = ZONE_ORDER.filter(k => zMap[k]).map(k => {
        const z = zMap[k];
        const hrText = z.hr && (z.hr.low != null || z.hr.high != null)
          ? (z.hr.low == null ? `under ${num(z.hr.high)} bpm` : `${num(z.hr.low)}–${num(z.hr.high)} bpm`)
          : '–';
        return `<div class="tr-zone-row">
          <span class="tr-zone" data-zone="${esc(k)}">${esc(k)}</span>
          <span class="tr-zone-pace">${esc(formatPaceRange(z))}</span>
          <span class="tr-zone-hr">${esc(hrText)}</span>
          <span class="tr-zone-desc">${esc(ZONE_DESC[k] || z.text || '')}</span>
        </div>`;
      });
      zonesEl.innerHTML = rows.join('') || '<p class="sl-empty">Inga zoner tillgängliga.</p>';
    }

    if (equivsEl) {
      const equivs = data.equivalents || [];
      equivsEl.innerHTML = equivs.length
        ? `<div class="tr-equivs">${equivs.map(eq =>
            `<div class="tr-equiv"><span>${esc(eq.label)}</span><strong>${esc(eq.text)}</strong><small>${esc(eq.pace || '')}</small></div>`
          ).join('')}</div>`
        : '<p class="sl-empty">Inga loppekvivalenter.</p>';
    }

    if (goalEl) {
      const g = data.goal;
      if (!g) {
        goalEl.innerHTML = '<p class="sl-empty">Inget mållopp satt ännu.</p>';
      } else {
        const lvl = g.realistic?.level;
        const tone = lvl === 'realistic' ? 'good' : lvl === 'unrealistic' ? 'bad' : 'watch';
        const lvlText = lvl === 'realistic' ? 'Realistiskt' : lvl === 'unrealistic' ? 'Orealistiskt' : 'Ambitiöst';
        goalEl.innerHTML = `<div class="sl-card-head" style="margin-bottom:10px;">
            <strong>${esc(g.label || 'Mål')}</strong>
            <span class="sl-pill" data-tone="${tone}">${esc(lvlText)}</span>
          </div>
          <div class="tr-goal-grid">
            <div class="tr-stat"><span>Mål-VDOT</span><strong>${num(g.vdot, 1)}</strong></div>
            <div class="tr-stat"><span>Glapp</span><strong>${signed(g.gap, 1)}</strong></div>
            <div class="tr-stat"><span>Per vecka</span><strong>${g.perWeek != null ? num(g.perWeek, 2) : '–'}</strong></div>
          </div>
          ${g.realistic?.text ? `<p class="tr-goal-text">${esc(g.realistic.text)}</p>` : ''}`;
      }
    }
  }

  function renderLibrary(data) {
    const libEl = $('tr-phys-library');
    const resEl = $('tr-phys-research');
    if (libEl) {
      const groups = data?.groups || [];
      if (!data || data.available === false || !groups.length) {
        libEl.innerHTML = '<p class="sl-empty">Passbanken kunde inte hämtas.</p>';
      } else {
        libEl.innerHTML = groups.map(g => `<details class="tr-lib-group">
          <summary>${esc(g.title)}</summary>
          ${g.purpose ? `<p class="tr-lib-purpose">${esc(g.purpose)}</p>` : ''}
          <div class="tr-lib-list">${(g.sessions || []).map(s => `<div class="tr-lib-item">
            <div class="tr-lib-item-head">
              <strong>${esc(s.title)}</strong>
              <span>${[s.km ? `${num(s.km, 0)} km` : '', s.qualityMin ? `${num(s.qualityMin)} min kval.` : ''].filter(Boolean).join(' · ')}</span>
            </div>
            ${s.detail ? `<p>${esc(s.detail)}</p>` : ''}
          </div>`).join('')}</div>
        </details>`).join('');
      }
    }
    if (resEl) {
      const items = data?.research || [];
      resEl.innerHTML = items.length
        ? `<div class="tr-research-list">${items.map(r =>
            `<div class="tr-research-item"><strong>${esc(r.title)}</strong><p>${esc(r.text)}</p></div>`
          ).join('')}</div>`
        : '<p class="sl-empty">Ingen forskningssammanfattning tillgänglig.</p>';
    }
  }

  async function loadPhysiology() {
    const [phys, lib] = await Promise.allSettled([
      requestJson('/api/physiology'),
      requestJson('/api/training-library'),
    ]);
    renderPhysiology(phys.status === 'fulfilled' ? phys.value : { available: false, reason: phys.reason?.message });
    renderLibrary(lib.status === 'fulfilled' ? lib.value : null);
  }

  // ─── INTENSITET (page-intensity) ───────────────────────────

  function renderIntensity(data) {
    const distEl = $('tr-int-dist');
    const loadEl = $('tr-int-load');
    const assumpEl = $('tr-int-assumptions');
    if (!distEl) return;

    const weeks = data?.weeks || [];
    if (!weeks.length) {
      distEl.innerHTML = '<div class="tr-empty">Ingen intensitetsdata för de senaste 12 veckorna.</div>';
    } else {
      const withZones = weeks.filter(w => w.hasZones && w.totalMin > 0);
      const latest = withZones[withZones.length - 1];
      const avgLow = withZones.length
        ? withZones.reduce((a, w) => a + (w.lowShare || 0), 0) / withZones.length
        : null;
      const avgThreshMin = withZones.length
        ? withZones.reduce((a, w) => a + (w.thresholdMin || 0), 0) / withZones.length
        : null;

      const targetLow = data?.target?.low ?? 0.8;
      const wSvg = 340, hSvg = 140, padT = 10, padB = 18, plotH = hSvg - padT - padB;
      const colW = wSvg / weeks.length;
      const barW = Math.max(10, colW - 6);
      const y80 = padT + (1 - targetLow) * plotH;

      const bars = weeks.map((w, idx) => {
        const x = idx * colW + (colW - barW) / 2;
        const wkLbl = `v.${w.isoWeek ?? w.week ?? ''}`;
        if (!w.hasZones || !w.totalMin) {
          return `<rect x="${x.toFixed(1)}" y="${padT}" width="${barW.toFixed(1)}" height="${plotH}" rx="3" fill="var(--bg4)"/>
            <text class="tr-svg-label" x="${(x + barW / 2).toFixed(1)}" y="${hSvg - 4}" text-anchor="middle">${esc(wkLbl)}</text>`;
        }
        const lowH = Math.max(0, (w.lowShare || 0) * plotH);
        const thrH = Math.max(0, (w.thresholdShare || 0) * plotH);
        const hiH = Math.max(0, plotH - lowH - thrH);
        const yHi = padT;
        const yThr = yHi + hiH;
        const yLow = yThr + thrH;
        return `<rect x="${x.toFixed(1)}" y="${yHi.toFixed(1)}" width="${barW.toFixed(1)}" height="${hiH.toFixed(1)}" fill="var(--red)" rx="2"/>
          <rect x="${x.toFixed(1)}" y="${yThr.toFixed(1)}" width="${barW.toFixed(1)}" height="${thrH.toFixed(1)}" fill="var(--amber)"/>
          <rect x="${x.toFixed(1)}" y="${yLow.toFixed(1)}" width="${barW.toFixed(1)}" height="${lowH.toFixed(1)}" fill="var(--green)" rx="2"/>
          <text class="tr-svg-label" x="${(x + barW / 2).toFixed(1)}" y="${hSvg - 4}" text-anchor="middle">${esc(wkLbl)}</text>`;
      }).join('');

      const threshRow = weeks.map(w =>
        `<span class="tr-thresh-cell" title="Tröskelminuter">${w.hasZones && w.thresholdMin != null ? `${Math.round(w.thresholdMin)}m` : '–'}</span>`
      ).join('');

      distEl.innerHTML = `<div class="tr-int-summary">
          <div class="tr-stat"><span>Senaste lugnt</span><strong>${latest ? pct(latest.lowShare) : '–'}</strong></div>
          <div class="tr-stat"><span>Snitt 12 v lugnt</span><strong>${avgLow != null ? pct(avgLow) : '–'}</strong></div>
          <div class="tr-stat"><span>Tröskel / v</span><strong>${avgThreshMin != null ? `${Math.round(avgThreshMin)} min` : '–'}</strong></div>
        </div>
        <div class="tr-legend">
          <span class="tr-legend-item"><i class="tr-dot" style="background:var(--green)"></i>Lågt (Z1–3)</span>
          <span class="tr-legend-item"><i class="tr-dot" style="background:var(--amber)"></i>Tröskel (Z4)</span>
          <span class="tr-legend-item"><i class="tr-dot" style="background:var(--red)"></i>Högt (Z5)</span>
          <span class="tr-legend-item"><i class="tr-dot" style="background:var(--bg4)"></i>Saknar puls</span>
        </div>
        <svg class="tr-svg" viewBox="0 0 ${wSvg} ${hSvg}" role="img" aria-label="Intensitetsfördelning senaste 12 veckorna">
          ${bars}
          <line x1="0" y1="${y80.toFixed(1)}" x2="${wSvg}" y2="${y80.toFixed(1)}" stroke="currentColor" stroke-opacity="0.45" stroke-dasharray="4 3" stroke-width="1"/>
          <text class="tr-svg-label" x="${wSvg - 2}" y="${(y80 - 3).toFixed(1)}" text-anchor="end">${Math.round(targetLow * 100)}%</text>
        </svg>
        <div class="tr-thresh-list" aria-label="Tröskelminuter per vecka">${threshRow}</div>`;
    }

    if (loadEl) {
      const load = data?.load || {};
      const s = load.summary || {};
      const series = load.series || [];
      if (!series.length && s.ctl == null) {
        loadEl.innerHTML = '<div class="tr-empty">Ingen belastningshistorik tillgänglig.</div>';
      } else {
        let chartHtml = '';
        if (series.length >= 2) {
          const wSvg = 340, hSvg = 140, pad = 8, padB = 18, plotH = hSvg - pad - padB;
          const ctls = series.map(p => Number(p.ctl ?? 0));
          const atls = series.map(p => Number(p.atl ?? 0));
          const tsbs = series.map(p => Number(p.tsb ?? 0));
          const maxLoad = Math.max(10, ...ctls, ...atls);
          const maxAbsTsb = Math.max(10, ...tsbs.map(Math.abs));
          const x = i => pad + (i / (series.length - 1)) * (wSvg - 2 * pad);
          const yLoad = v => pad + (1 - Math.max(0, v) / maxLoad) * plotH;
          const zeroTsb = pad + plotH * 0.55;
          const tsbScale = (plotH * 0.35) / maxAbsTsb;
          const barW = Math.max(1.5, (wSvg - 2 * pad) / series.length - 1);

          const tsbBars = series.map((p, i) => {
            const val = Number(p.tsb ?? 0);
            const bh = Math.abs(val) * tsbScale;
            const by = val >= 0 ? zeroTsb - bh : zeroTsb;
            const fill = val >= 0 ? 'var(--green)' : 'var(--red)';
            return `<rect x="${(x(i) - barW / 2).toFixed(1)}" y="${by.toFixed(1)}" width="${barW.toFixed(1)}" height="${Math.max(1, bh).toFixed(1)}" fill="${fill}" fill-opacity="0.28"/>`;
          }).join('');
          const ctlLine = series.map((p, i) => `${x(i).toFixed(1)},${yLoad(Number(p.ctl ?? 0)).toFixed(1)}`).join(' ');
          const atlLine = series.map((p, i) => `${x(i).toFixed(1)},${yLoad(Number(p.atl ?? 0)).toFixed(1)}`).join(' ');

          chartHtml = `<div class="tr-legend" style="margin-top:4px;">
              <span class="tr-legend-item"><i class="tr-dot" style="background:var(--accent)"></i>CTL (form)</span>
              <span class="tr-legend-item"><i class="tr-dot" style="background:var(--amber)"></i>ATL (trötthet)</span>
              <span class="tr-legend-item"><i class="tr-dot" style="background:var(--green)"></i>TSB (balans)</span>
            </div>
            <svg class="tr-svg" viewBox="0 0 ${wSvg} ${hSvg}" role="img" aria-label="CTL, ATL och TSB över tid">
              ${tsbBars}
              <polyline fill="none" stroke="var(--accent)" stroke-width="2" points="${ctlLine}"/>
              <polyline fill="none" stroke="var(--amber)" stroke-width="1.6" stroke-dasharray="3 2" points="${atlLine}"/>
              <text class="tr-svg-label" x="${pad}" y="${hSvg - 4}">${esc(shortDate(series[0].date))}</text>
              <text class="tr-svg-label" x="${wSvg - pad}" y="${hSvg - 4}" text-anchor="end">${esc(shortDate(series[series.length - 1].date))}</text>
            </svg>`;
        }

        loadEl.innerHTML = `<div class="tr-load-kpis">
            <div class="tr-kpi"><span>CTL · Form</span><strong>${num(s.ctl, 0)}</strong><small>42 d snitt</small></div>
            <div class="tr-kpi"><span>ATL · Trötthet</span><strong>${num(s.atl, 0)}</strong><small>7 d snitt</small></div>
            <div class="tr-kpi"><span>TSB · Balans</span><strong>${signed(s.tsb, 0)}</strong><small>${s.rampPerWeek != null ? `Ramp ${signed(s.rampPerWeek, 1)}/v` : 'CTL − ATL'}</small></div>
          </div>
          ${s.interpretation ? `<p class="tr-interp">${esc(s.interpretation)}</p>` : ''}
          ${chartHtml}`;
      }
    }

    if (assumpEl) {
      const list = data?.assumptions || [];
      assumpEl.innerHTML = list.length
        ? `<ul class="tr-assumptions">${list.map(a => `<li>${esc(a)}</li>`).join('')}</ul>`
        : '';
    }
  }

  async function loadIntensity() {
    try {
      const data = await requestJson('/api/intensity?weeks=12');
      renderIntensity(data);
    } catch (err) {
      const distEl = $('tr-int-dist');
      if (distEl) distEl.innerHTML = `<div class="tr-empty">${esc(err.message)}</div>`;
    }
  }

  window.loadTraining = loadTraining;
  window.loadPhysiology = loadPhysiology;
  window.loadIntensity = loadIntensity;
})();
