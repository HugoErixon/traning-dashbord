/* Löparprofil: kontoanknuten data, explicit sparning och inga AI-anrop. */
(() => {
  'use strict';
  const form = document.getElementById('runner-profile-form');
  if (!form) return;
  const goalForm = document.getElementById('rp-goal-form');
  const fields = document.getElementById('rp-fields');
  const goalFields = document.getElementById('rp-goal-fields');
  const save = document.getElementById('rp-save');
  const status = document.getElementById('rp-save-status');
  const loadStatus = document.getElementById('rp-load-status');
  const goalStatus = document.getElementById('rp-goal-status');
  const keys = ['easy', 'steady', 'marathon', 'threshold', 'interval'];
  let loaded = false, loading = false, dirty = false, goalLoaded = false, goalLoading = false, goalDirty = false;
  const value = name => form.elements.namedItem(name).value.trim();
  const nullable = name => value(name) === '' ? null : value(name);
  const chosenDays = () => Array.from(form.querySelectorAll('[name="preferredDays"]:checked'), el => Number(el.value));

  async function request(url, options) {
    let response;
    try { response = await fetch(url, options); }
    catch (_) { throw new Error('Servern kunde inte nås. Dina ändringar finns kvar här; försök igen.'); }
    const data = await response.json().catch(() => ({}));
    if (!response.ok) {
      if (response.status === 404 && url.includes('runner-profile')) {
        throw new Error('Löparprofilens API finns inte på den här servern ännu. Använd förhandsvisningen för att prova profilen.');
      }
      throw new Error(data.message || data.error || 'Kunde inte spara. Försök igen.');
    }
    return data;
  }

  function syncLongDay() {
    const days = chosenDays();
    const select = form.elements.longRunDay;
    for (const option of select.options) option.disabled = option.value !== '' && !days.includes(Number(option.value));
    if (select.value !== '' && !days.includes(Number(select.value))) select.value = '';
  }

  function summarize(profile) {
    document.getElementById('rp-days-summary').textContent = profile.preferredDays.length ? `${profile.preferredDays.length} / vecka` : 'Valfria';
    const count = Object.keys(profile.paceZones).length;
    document.getElementById('rp-zones-summary').textContent = [profile.hrZones.length ? 'Puls' : '', count ? 'Tempo' : ''].filter(Boolean).join(' + ') || 'Ej angivna';
    document.getElementById('rp-summary').textContent = profile.displayName
      ? `${profile.displayName} · ${profile.weeklyKm === null ? 'Din löpning, på dina villkor' : profile.weeklyKm + ' km per vecka just nu'}`
      : 'Din löpning, på dina villkor. Fyll i det som är relevant för dig.';
  }

  function render(profile) {
    for (const name of ['displayName', 'about', 'experience', 'trainingNotes', 'weeklyKm', 'sessionsPerWeek', 'maxHr', 'restingHr']) {
      form.elements.namedItem(name).value = profile[name] ?? '';
    }
    form.querySelectorAll('[name="preferredDays"]').forEach(el => { el.checked = profile.preferredDays.includes(Number(el.value)); });
    syncLongDay();
    form.elements.longRunDay.value = profile.longRunDay ?? '';
    for (let i = 0; i < 5; i++) {
      form.elements.namedItem(`hrMin${i}`).value = profile.hrZones[i]?.min ?? '';
      form.elements.namedItem(`hrMax${i}`).value = profile.hrZones[i]?.max ?? '';
    }
    for (const key of keys) {
      form.elements.namedItem(`paceFast_${key}`).value = profile.paceZones[key]?.fast ?? '';
      form.elements.namedItem(`paceSlow_${key}`).value = profile.paceZones[key]?.slow ?? '';
    }
    summarize(profile);
  }

  async function load() {
    if (loading || loaded) return;
    loading = true;
    loadStatus.textContent = 'Laddar din löparprofil…';
    document.getElementById('rp-retry').hidden = true;
    try {
      const data = await request('/api/runner-profile');
      if (!data.profile || !Array.isArray(data.profile.preferredDays)) throw new Error('Servern stöder inte löparprofilen ännu.');
      render(data.profile);
      loaded = true;
      fields.disabled = false;
      save.disabled = false;
      loadStatus.textContent = '';
    } catch (error) {
      loadStatus.textContent = error.message;
      document.getElementById('rp-retry').hidden = false;
    } finally { loading = false; }
  }

  async function loadGoal(force = false) {
    if (goalLoading || (goalLoaded && !force) || goalDirty) return;
    goalLoading = true;
    document.getElementById('rp-goal-retry').hidden = true;
    try {
      const data = await request('/api/goals');
      if (goalDirty) return; // Do not replace edits made while the request was in flight.
      if (!Object.hasOwn(data, 'goal')) throw new Error('Målet kunde inte läsas. Försök igen.');
      const goal = data.goal || {};
      for (const [input, key] of Object.entries({goalTitle: 'goal_title', goalDeadline: 'goal_deadline', currentBest: 'current_best', secondaryGoal: 'secondary_goal'})) {
        // Flask may serialize SQL DATE as an HTTP date; date inputs need ISO.
        let text = goal[key] || '';
        if (input === 'goalDeadline' && text && !/^\d{4}-\d{2}-\d{2}$/.test(text)) {
          const date = new Date(text);
          text = Number.isNaN(date.valueOf()) ? '' : date.toISOString().slice(0, 10);
        }
        goalForm.elements.namedItem(input).value = text;
      }
      goalLoaded = true;
      goalFields.disabled = false;
      goalStatus.textContent = 'Samma mål används i resten av Trainyze. Att spara skapar ingen träningsplan.';
    } catch (error) {
      goalStatus.textContent = error.message;
      document.getElementById('rp-goal-retry').hidden = false;
    } finally { goalLoading = false; }
  }

  form.addEventListener('input', () => {
    dirty = true;
    syncLongDay();
    status.textContent = 'Du har osparade ändringar.';
    status.dataset.error = 'false';
  });
  goalForm.addEventListener('input', () => {
    goalDirty = true;
    goalStatus.textContent = 'Målet har osparade ändringar.';
  });
  form.addEventListener('submit', async event => {
    event.preventDefault();
    if (!loaded || save.disabled) return;
    const profile = {};
    for (const key of ['displayName', 'about', 'experience', 'trainingNotes']) profile[key] = value(key);
    for (const key of ['weeklyKm', 'sessionsPerWeek', 'maxHr', 'restingHr']) profile[key] = nullable(key);
    profile.preferredDays = chosenDays();
    profile.longRunDay = value('longRunDay') === '' ? null : Number(value('longRunDay'));
    const hrZones = Array.from({length: 5}, (_, i) => ({min: nullable(`hrMin${i}`), max: nullable(`hrMax${i}`)}));
    profile.hrZones = hrZones.every(zone => zone.min === null && zone.max === null) ? [] : hrZones;
    profile.paceZones = Object.fromEntries(keys.map(key => [key, {fast: value(`paceFast_${key}`), slow: value(`paceSlow_${key}`)}]));
    fields.disabled = true;
    save.disabled = true;
    status.textContent = 'Sparar…';
    status.dataset.error = 'false';
    try {
      const data = await request('/api/runner-profile', {method: 'PUT', headers: {'Content-Type': 'application/json'}, body: JSON.stringify(profile)});
      if (!data.profile) throw new Error('Servern bekräftade inte sparningen. Försök igen.');
      render(data.profile);
      dirty = false;
      status.textContent = 'Löparprofilen är sparad.';
    } catch (error) {
      status.textContent = error.message;
      status.dataset.error = 'true';
    } finally { fields.disabled = false; save.disabled = false; }
  });
  goalForm.addEventListener('submit', async event => {
    event.preventDefault();
    if (!goalLoaded || goalFields.disabled) return;
    const data = Object.fromEntries(new FormData(goalForm));
    goalFields.disabled = true;
    goalStatus.textContent = 'Sparar målet…';
    try {
      const saved = await request('/api/goals', {method: 'PUT', headers: {'Content-Type': 'application/json'}, body: JSON.stringify(data)});
      if (!saved.goal) throw new Error('Servern bekräftade inte målet. Försök igen.');
      goalDirty = false;
      goalStatus.textContent = 'Ditt mål är sparat.';
      // Refresh the existing goal display without invoking plan generation.
      if (typeof loadUserGoal === 'function') loadUserGoal();
    } catch (error) { goalStatus.textContent = error.message; }
    finally { goalFields.disabled = false; }
  });
  document.getElementById('rp-retry').addEventListener('click', load);
  document.getElementById('rp-goal-retry').addEventListener('click', () => loadGoal(true));
  document.getElementById('rp-goal-shortcut').addEventListener('click', () => {
    document.getElementById('rp-goal-title').scrollIntoView({behavior: 'smooth', block: 'start'});
    goalForm.elements.goalTitle.focus({preventScroll: true});
  });
  window.addEventListener('beforeunload', event => {
    if (dirty || goalDirty) { event.preventDefault(); event.returnValue = ''; }
  });
  // Load only on entry. Keep unsaved edits when switching between app tabs.
  const page = document.getElementById('page-settings');
  const onEntry = () => { if (page.classList.contains('active')) { load(); loadGoal(true); } };
  new MutationObserver(onEntry).observe(page, {attributes: true, attributeFilter: ['class']});
  onEntry();
})();
