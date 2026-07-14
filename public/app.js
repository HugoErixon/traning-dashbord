  // Device mode for behavior hooks; layout itself is handled by responsive CSS.
  const phoneMedia = window.matchMedia('(max-width: 720px), (pointer: coarse)');
  function applyDeviceMode() {
    document.documentElement.dataset.device = phoneMedia.matches ? 'phone' : 'desktop';
  }
  applyDeviceMode();
  phoneMedia.addEventListener?.('change', applyDeviceMode);

const originalFetch = window.fetch.bind(window);
const dashboardShell = document.querySelector('.shell');
let csrfToken = '';
let currentUserIsAdmin = false;
let garminConnected = false;
let garminMfaStateId = null;
let userGoal = null;
let goalPromptShownThisLoad = false;
let authResolved = false;
let sessionExpired = false;
let resolveAuth;
const authReady = new Promise(resolve => { resolveAuth = resolve; });
dashboardShell.style.display = 'none';

function clearLegacyCredentials() {
  localStorage.removeItem('sitePassword');
  localStorage.removeItem('site_user');
}

function completeAuth(data) {
  csrfToken = data.csrfToken || '';
  currentUserIsAdmin = !!data.isAdmin;
  garminConnected = !!data.garminConnected;
  const usersBtn = document.getElementById('users-btn');
  if (usersBtn) usersBtn.style.display = currentUserIsAdmin ? '' : 'none';
  updateGarminSidebar();
  loadUserGoal();
  const screen = document.getElementById('login-screen');
  if (screen) screen.remove();
  dashboardShell.style.display = 'flex';
  if (!authResolved) {
    authResolved = true;
    resolveAuth();
  } else if (sessionExpired) {
    location.reload();
  }
  sessionExpired = false;
}

function whileAuthenticated(callback) {
  return () => {
    if (authResolved && !sessionExpired && !document.getElementById('login-screen')) callback();
  };
}

function showLogin(message) {
  dashboardShell.style.display = 'none';
  const existing = document.getElementById('login-screen');
  if (existing) {
    const error = document.getElementById('login-error');
    if (message && error) {
      error.textContent = message;
      error.style.display = 'block';
    }
    return;
  }
  document.body.insertAdjacentHTML('beforeend', `
    <div id="login-screen" style="position:fixed;inset:0;background:var(--bg);display:flex;align-items:center;justify-content:center;z-index:999;">
      <div style="background:var(--bg2);border:1px solid var(--border2);border-radius:8px;padding:40px;width:320px;text-align:center;">
        <h2 style="font-size:18px;font-weight:800;margin-bottom:6px;">Träningsdashboard</h2>
        <p style="font-size:12.5px;color:var(--muted2);margin-bottom:24px;font-family:'IBM Plex Mono',monospace;">Logga in för att fortsätta</p>
        <input id="login-user" type="text" autocomplete="username" placeholder="Användarnamn" style="width:100%;background:var(--bg3);border:1px solid var(--border2);border-radius:8px;padding:11px 14px;color:var(--text);font-family:'IBM Plex Sans',sans-serif;font-size:14px;outline:none;margin-bottom:10px;box-sizing:border-box;" />
        <input id="login-input" type="password" autocomplete="current-password" placeholder="Lösenord" style="width:100%;background:var(--bg3);border:1px solid var(--border2);border-radius:8px;padding:11px 14px;color:var(--text);font-family:'IBM Plex Sans',sans-serif;font-size:14px;outline:none;margin-bottom:12px;box-sizing:border-box;" />
        <button id="login-submit" type="button" style="width:100%;background:var(--blue);border:none;border-radius:8px;padding:12px;color:#081018;font-family:'IBM Plex Sans',sans-serif;font-size:14px;font-weight:700;cursor:pointer;">Logga in</button>
        <p id="login-error" role="alert" style="font-size:12px;color:var(--red);margin-top:10px;display:none;">Fel användarnamn eller lösenord</p>
      </div>
    </div>
  `);
  document.getElementById('login-submit').addEventListener('click', tryLogin);
  document.getElementById('login-input').addEventListener('keydown', event => {
    if (event.key === 'Enter') tryLogin();
  });
  document.getElementById('login-user').addEventListener('keydown', event => {
    if (event.key === 'Enter') document.getElementById('login-input').focus();
  });
  document.getElementById('login-user').focus();
}

async function performLogin(username, password) {
  const response = await originalFetch('/api/login', {
    method: 'POST',
    credentials: 'same-origin',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({username, password}),
  });
  const data = await response.json().catch(() => ({}));
  if (!response.ok || !data.ok) {
    const error = new Error(data.error || 'Fel användarnamn eller lösenord.');
    error.status = response.status;
    throw error;
  }
  completeAuth(data);
}

async function tryLogin() {
  const username = document.getElementById('login-user').value.trim();
  const password = document.getElementById('login-input').value;
  const button = document.getElementById('login-submit');
  const error = document.getElementById('login-error');
  button.disabled = true;
  error.style.display = 'none';
  try {
    await performLogin(username, password);
  } catch (loginError) {
    error.textContent = loginError.message;
    error.style.display = 'block';
    document.getElementById('login-input').value = '';
    document.getElementById('login-input').focus();
  } finally {
    button.disabled = false;
  }
}

window.fetch = async (input, options = {}) => {
  const url = typeof input === 'string' ? input : input.url;
  const isApi = url.startsWith('/api/');
  const isAuthEndpoint = url === '/api/login' || url === '/api/session';
  if (isApi && !isAuthEndpoint) await authReady;

  const requestOptions = {...options, credentials: 'same-origin'};
  const method = String(requestOptions.method || 'GET').toUpperCase();
  if (isApi && ['POST', 'PUT', 'PATCH', 'DELETE'].includes(method) && !isAuthEndpoint) {
    const headers = new Headers(requestOptions.headers || {});
    headers.set('X-CSRF-Token', csrfToken);
    requestOptions.headers = headers;
  }
  const response = await originalFetch(input, requestOptions);
  if (isApi && !isAuthEndpoint && response.status === 401) {
    sessionExpired = true;
    showLogin('Sessionen har gått ut. Logga in igen.');
  }
  return response;
};

async function initializeAuth() {
  const legacyUsername = localStorage.getItem('site_user') || '';
  const legacyPassword = localStorage.getItem('sitePassword') || '';
  clearLegacyCredentials();
  try {
    const response = await originalFetch('/api/session', {credentials: 'same-origin'});
    const data = await response.json();
    if (response.ok && data.authenticated) {
      completeAuth(data);
      return;
    }
    if (legacyPassword) {
      try {
        await performLogin(legacyUsername, legacyPassword);
        return;
      } catch (_) {
        // The old credential is deliberately discarded even when migration fails.
      }
    }
    showLogin();
  } catch (_) {
    showLogin('Servern kunde inte nås. Försök igen om en stund.');
  }
}

initializeAuth();

// --- Träningsmål per användare ---
async function loadUserGoal() {
  try {
    const res = await fetch('/api/goals');
    const data = await res.json();
    if (res.ok) userGoal = data.goal;
  } catch (_) {
    userGoal = null;
  }
  renderGoalUi();
  if (!userGoal && !goalPromptShownThisLoad) {
    goalPromptShownThisLoad = true;
    openGoalModal(true);
  }
}

function formatGoalDate(iso) {
  try {
    return new Date(iso + 'T00:00:00').toLocaleDateString('sv-SE', {day: 'numeric', month: 'short', year: 'numeric'});
  } catch (_) {
    return iso;
  }
}

function renderGoalUi() {
  const text = document.getElementById('goal-days-text');
  const bar = document.getElementById('days-bar');
  const calSub = document.getElementById('calendar-goal-sub');
  if (!userGoal) {
    if (text) text.textContent = 'Sätt ditt träningsmål →';
    if (bar) bar.style.width = '0%';
    if (calSub) calSub.textContent = 'Träningsplan och kalender';
    return;
  }
  const g = userGoal;
  if (text) {
    if (g.goal_deadline) {
      const left = Math.max(0, Math.ceil((new Date(g.goal_deadline + 'T00:00:00') - new Date()) / 86400000));
      text.innerHTML = `<span style="color:var(--accent);font-weight:700;font-family:var(--font-num);">${left}</span> dagar till mål · ${escapeHtml(formatGoalDate(g.goal_deadline))}`;
      if (bar && g.start_date) {
        const total = Math.ceil((new Date(g.goal_deadline + 'T00:00:00') - new Date(g.start_date + 'T00:00:00')) / 86400000);
        bar.style.width = total > 0 ? Math.min(100, Math.max(0, (1 - left / total) * 100)) + '%' : '0%';
      }
    } else {
      text.textContent = `Mål: ${g.goal_title}`;
      if (bar) bar.style.width = '0%';
    }
  }
  if (calSub) calSub.textContent = g.goal_title + (g.goal_deadline ? ' – ' + formatGoalDate(g.goal_deadline) : '');
}

function closeGoalModal() {
  document.getElementById('goal-modal')?.remove();
}

function openGoalModal(isOnboarding) {
  if (document.getElementById('goal-modal')) return;
  const g = userGoal || {};
  const heading = isOnboarding ? 'Välkommen! Vad tränar du mot?' : 'Ditt träningsmål';
  const intro = isOnboarding
    ? 'Sätt ditt eget träningsmål — det styr vad coachen och dashboarden fokuserar på. Du kan ändra det när som helst.'
    : 'Målet styr coachens råd och nedräkningen på startsidan.';
  document.body.insertAdjacentHTML('beforeend', `
    <div id="goal-modal" style="position:fixed;inset:0;background:rgba(0,0,0,0.55);display:flex;align-items:center;justify-content:center;z-index:998;">
      <div style="background:var(--bg2);border:1px solid var(--border2);border-radius:12px;padding:28px;width:420px;max-width:92vw;max-height:85vh;overflow-y:auto;">
        <div style="display:flex;align-items:center;justify-content:space-between;margin-bottom:4px;">
          <h2 style="font-size:16px;font-weight:800;">${heading}</h2>
          <button type="button" data-action="close-goal-modal" style="background:none;border:none;color:var(--muted);cursor:pointer;font-size:18px;line-height:1;padding:4px;">✕</button>
        </div>
        <p style="font-size:12px;color:var(--muted2);margin-bottom:16px;font-family:'IBM Plex Mono',monospace;line-height:1.5;">${intro}</p>
        <label style="display:block;font-size:11px;font-weight:700;letter-spacing:0.05em;color:var(--muted2);text-transform:uppercase;margin-bottom:6px;font-family:'IBM Plex Mono',monospace;">Mål *</label>
        <input id="goal-title-input" type="text" maxlength="200" placeholder="t.ex. Milen under 45 min" value="${escapeHtml(g.goal_title || '')}" style="width:100%;background:var(--bg3);border:1px solid var(--border2);border-radius:8px;padding:10px 12px;color:var(--text);font-family:'IBM Plex Sans',sans-serif;font-size:13.5px;outline:none;margin-bottom:12px;box-sizing:border-box;" />
        <label style="display:block;font-size:11px;font-weight:700;letter-spacing:0.05em;color:var(--muted2);text-transform:uppercase;margin-bottom:6px;font-family:'IBM Plex Mono',monospace;">Deadline (valfritt)</label>
        <input id="goal-deadline-input" type="date" value="${escapeHtml(g.goal_deadline || '')}" style="width:100%;background:var(--bg3);border:1px solid var(--border2);border-radius:8px;padding:10px 12px;color:var(--text);font-family:'IBM Plex Mono',monospace;font-size:13px;outline:none;margin-bottom:12px;box-sizing:border-box;" />
        <label style="display:block;font-size:11px;font-weight:700;letter-spacing:0.05em;color:var(--muted2);text-transform:uppercase;margin-bottom:6px;font-family:'IBM Plex Mono',monospace;">Nuvarande bästa (valfritt)</label>
        <input id="goal-best-input" type="text" maxlength="200" placeholder="t.ex. 48:30 (Vårruset)" value="${escapeHtml(g.current_best || '')}" style="width:100%;background:var(--bg3);border:1px solid var(--border2);border-radius:8px;padding:10px 12px;color:var(--text);font-family:'IBM Plex Sans',sans-serif;font-size:13.5px;outline:none;margin-bottom:12px;box-sizing:border-box;" />
        <label style="display:block;font-size:11px;font-weight:700;letter-spacing:0.05em;color:var(--muted2);text-transform:uppercase;margin-bottom:6px;font-family:'IBM Plex Mono',monospace;">Sekundärt mål (valfritt)</label>
        <input id="goal-secondary-input" type="text" maxlength="300" placeholder="t.ex. Styrka 2 pass/vecka" value="${escapeHtml(g.secondary_goal || '')}" style="width:100%;background:var(--bg3);border:1px solid var(--border2);border-radius:8px;padding:10px 12px;color:var(--text);font-family:'IBM Plex Sans',sans-serif;font-size:13.5px;outline:none;margin-bottom:16px;box-sizing:border-box;" />
        <button type="button" data-action="save-goal" id="goal-save-btn" style="width:100%;background:var(--blue);border:none;border-radius:8px;padding:11px;color:#081018;font-family:'IBM Plex Sans',sans-serif;font-size:13.5px;font-weight:700;cursor:pointer;">Spara mål</button>
        ${isOnboarding ? '<button type="button" data-action="close-goal-modal" style="width:100%;background:none;border:none;color:var(--muted2);font-size:12px;margin-top:10px;cursor:pointer;font-family:\'IBM Plex Mono\',monospace;">Hoppa över — jag sätter det senare</button>' : ''}
        <p id="goal-modal-msg" role="alert" style="font-size:12px;margin-top:10px;display:none;color:var(--red);"></p>
      </div>
    </div>
  `);
  const overlay = document.getElementById('goal-modal');
  overlay.addEventListener('click', event => {
    if (event.target === overlay) closeGoalModal();
  });
  document.getElementById('goal-title-input').focus();
}

async function saveGoalFromForm() {
  const title = document.getElementById('goal-title-input').value.trim();
  const msg = document.getElementById('goal-modal-msg');
  const button = document.getElementById('goal-save-btn');
  if (!title) {
    msg.textContent = 'Skriv in ett mål först.';
    msg.style.display = 'block';
    return;
  }
  button.disabled = true;
  try {
    const res = await fetch('/api/goals', {
      method: 'PUT',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({
        goalTitle: title,
        goalDeadline: document.getElementById('goal-deadline-input').value,
        currentBest: document.getElementById('goal-best-input').value.trim(),
        secondaryGoal: document.getElementById('goal-secondary-input').value.trim(),
      }),
    });
    const data = await res.json().catch(() => ({}));
    if (!res.ok) {
      msg.textContent = data.error || 'Kunde inte spara målet.';
      msg.style.display = 'block';
      return;
    }
    userGoal = data.goal;
    renderGoalUi();
    closeGoalModal();
  } catch (error) {
    msg.textContent = 'Servern kunde inte nås. Försök igen.';
    msg.style.display = 'block';
  } finally {
    button.disabled = false;
  }
}

async function performLogout() {
  try {
    await fetch('/api/logout', {method: 'POST'});
  } catch (_) {
    // Sessionen rensas ändå lokalt via omladdningen.
  }
  location.reload();
}

// --- Garmin-koppling ---
function updateGarminSidebar() {
  const row = document.querySelector('.garmin-sync-row');
  const label = document.getElementById('garmin-sync-time');
  if (!row || !label) return;
  if (garminConnected) {
    row.removeAttribute('data-action');
    row.removeAttribute('role');
    row.style.cursor = '';
    row.title = '';
  } else {
    label.textContent = 'Ej kopplad — klicka här';
    row.dataset.action = 'open-garmin-connect';
    row.setAttribute('role', 'button');
    row.style.cursor = 'pointer';
    row.title = 'Koppla ditt Garmin-konto';
  }
}

function closeGarminConnectModal() {
  garminMfaStateId = null;
  document.getElementById('garmin-modal')?.remove();
}

function openGarminConnectModal() {
  if (garminConnected || document.getElementById('garmin-modal')) return;
  garminMfaStateId = null;
  document.body.insertAdjacentHTML('beforeend', `
    <div id="garmin-modal" style="position:fixed;inset:0;background:rgba(0,0,0,0.55);display:flex;align-items:center;justify-content:center;z-index:998;">
      <div style="background:var(--bg2);border:1px solid var(--border2);border-radius:12px;padding:28px;width:400px;max-width:92vw;">
        <div style="display:flex;align-items:center;justify-content:space-between;margin-bottom:4px;">
          <h2 style="font-size:16px;font-weight:800;">Koppla Garmin Connect</h2>
          <button type="button" data-action="close-garmin-connect" style="background:none;border:none;color:var(--muted);cursor:pointer;font-size:18px;line-height:1;padding:4px;">✕</button>
        </div>
        <div id="garmin-step-credentials">
          <p style="font-size:12px;color:var(--muted2);margin-bottom:16px;font-family:'IBM Plex Mono',monospace;line-height:1.5;">Logga in med ditt Garmin-konto. Lösenordet används en gång för att skapa en nyckel och sparas aldrig.</p>
          <input id="garmin-email" type="email" autocomplete="off" placeholder="E-post (Garmin)" style="width:100%;background:var(--bg3);border:1px solid var(--border2);border-radius:8px;padding:10px 12px;color:var(--text);font-family:'IBM Plex Sans',sans-serif;font-size:13.5px;outline:none;margin-bottom:8px;box-sizing:border-box;" />
          <input id="garmin-password" type="password" autocomplete="off" placeholder="Lösenord (Garmin)" style="width:100%;background:var(--bg3);border:1px solid var(--border2);border-radius:8px;padding:10px 12px;color:var(--text);font-family:'IBM Plex Sans',sans-serif;font-size:13.5px;outline:none;margin-bottom:10px;box-sizing:border-box;" />
          <button type="button" data-action="garmin-connect-submit" id="garmin-connect-btn" style="width:100%;background:var(--blue);border:none;border-radius:8px;padding:11px;color:#081018;font-family:'IBM Plex Sans',sans-serif;font-size:13.5px;font-weight:700;cursor:pointer;">Anslut</button>
        </div>
        <div id="garmin-step-mfa" style="display:none;">
          <p style="font-size:12px;color:var(--muted2);margin-bottom:16px;font-family:'IBM Plex Mono',monospace;line-height:1.5;">Garmin har skickat en engångskod till din e-post. Ange den här.</p>
          <input id="garmin-mfa-code" type="text" inputmode="numeric" autocomplete="one-time-code" placeholder="Engångskod" style="width:100%;background:var(--bg3);border:1px solid var(--border2);border-radius:8px;padding:10px 12px;color:var(--text);font-family:'IBM Plex Mono',monospace;font-size:15px;letter-spacing:0.2em;text-align:center;outline:none;margin-bottom:10px;box-sizing:border-box;" />
          <button type="button" data-action="garmin-mfa-submit" id="garmin-mfa-btn" style="width:100%;background:var(--blue);border:none;border-radius:8px;padding:11px;color:#081018;font-family:'IBM Plex Sans',sans-serif;font-size:13.5px;font-weight:700;cursor:pointer;">Verifiera</button>
        </div>
        <div id="garmin-step-done" style="display:none;text-align:center;">
          <p style="font-size:14px;font-weight:700;margin-bottom:8px;">Garmin kopplat! ✓</p>
          <p style="font-size:12px;color:var(--muted2);margin-bottom:16px;font-family:'IBM Plex Mono',monospace;line-height:1.5;">Din träningsdata hämtas nu i bakgrunden — sidan laddas om automatiskt om en stund.</p>
          <button type="button" data-action="garmin-reload-now" style="width:100%;background:var(--blue);border:none;border-radius:8px;padding:11px;color:#081018;font-family:'IBM Plex Sans',sans-serif;font-size:13.5px;font-weight:700;cursor:pointer;">Ladda om nu</button>
        </div>
        <p id="garmin-modal-msg" role="alert" style="font-size:12px;margin-top:10px;display:none;"></p>
      </div>
    </div>
  `);
  const overlay = document.getElementById('garmin-modal');
  overlay.addEventListener('click', event => {
    if (event.target === overlay) closeGarminConnectModal();
  });
  document.getElementById('garmin-password').addEventListener('keydown', event => {
    if (event.key === 'Enter') submitGarminCredentials();
  });
  document.getElementById('garmin-mfa-code').addEventListener('keydown', event => {
    if (event.key === 'Enter') submitGarminMfaCode();
  });
  document.getElementById('garmin-email').focus();
}

function showGarminModalMessage(text, isError) {
  const msg = document.getElementById('garmin-modal-msg');
  if (!msg) return;
  msg.textContent = text;
  msg.style.color = isError ? 'var(--red)' : 'var(--muted2)';
  msg.style.display = text ? 'block' : 'none';
}

function garminModalShowStep(step) {
  for (const name of ['credentials', 'mfa', 'done']) {
    const el = document.getElementById(`garmin-step-${name}`);
    if (el) el.style.display = name === step ? '' : 'none';
  }
}

function garminConnectSucceeded() {
  garminConnected = true;
  garminMfaStateId = null;
  updateGarminSidebar();
  const label = document.getElementById('garmin-sync-time');
  if (label) label.textContent = 'Hämtar din data…';
  garminModalShowStep('done');
  showGarminModalMessage('', false);
  setTimeout(() => { if (document.getElementById('garmin-modal')) location.reload(); }, 25000);
}

async function submitGarminCredentials() {
  const email = document.getElementById('garmin-email').value.trim();
  const password = document.getElementById('garmin-password').value;
  const button = document.getElementById('garmin-connect-btn');
  if (!email || !password) {
    showGarminModalMessage('Fyll i både e-post och lösenord.', true);
    return;
  }
  button.disabled = true;
  button.textContent = 'Kontaktar Garmin…';
  showGarminModalMessage('Detta kan ta upp till en minut.', false);
  try {
    const res = await fetch('/api/garmin/connect', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({email, password}),
    });
    const data = await res.json().catch(() => ({}));
    if (!res.ok) {
      showGarminModalMessage(data.error || 'Kopplingen misslyckades. Försök igen.', true);
      return;
    }
    document.getElementById('garmin-password').value = '';
    if (data.mfaRequired) {
      garminMfaStateId = data.stateId;
      garminModalShowStep('mfa');
      showGarminModalMessage('', false);
      document.getElementById('garmin-mfa-code').focus();
      return;
    }
    garminConnectSucceeded();
  } catch (error) {
    showGarminModalMessage('Servern kunde inte nås. Försök igen.', true);
  } finally {
    button.disabled = false;
    button.textContent = 'Anslut';
  }
}

async function submitGarminMfaCode() {
  const code = document.getElementById('garmin-mfa-code').value.trim();
  const button = document.getElementById('garmin-mfa-btn');
  if (!code) {
    showGarminModalMessage('Ange engångskoden från Garmin.', true);
    return;
  }
  button.disabled = true;
  button.textContent = 'Verifierar…';
  try {
    const res = await fetch('/api/garmin/mfa', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({stateId: garminMfaStateId, code}),
    });
    const data = await res.json().catch(() => ({}));
    if (!res.ok) {
      showGarminModalMessage(data.error || 'Verifieringen misslyckades.', true);
      if (res.status === 410 || data.code === 'invalid_mfa_code') {
        garminMfaStateId = null;
        garminModalShowStep('credentials');
      }
      return;
    }
    garminConnectSucceeded();
  } catch (error) {
    showGarminModalMessage('Servern kunde inte nås. Försök igen.', true);
  } finally {
    button.disabled = false;
    button.textContent = 'Verifiera';
  }
}

// --- Användarhantering (admin) ---
function closeUsersPanel() {
  document.getElementById('users-panel')?.remove();
}

async function openUsersPanel() {
  if (document.getElementById('users-panel')) return;
  document.body.insertAdjacentHTML('beforeend', `
    <div id="users-panel" style="position:fixed;inset:0;background:rgba(0,0,0,0.55);display:flex;align-items:center;justify-content:center;z-index:998;">
      <div style="background:var(--bg2);border:1px solid var(--border2);border-radius:12px;padding:28px;width:420px;max-width:92vw;max-height:85vh;overflow-y:auto;">
        <div style="display:flex;align-items:center;justify-content:space-between;margin-bottom:4px;">
          <h2 style="font-size:16px;font-weight:800;">Användare</h2>
          <button type="button" data-action="close-users" style="background:none;border:none;color:var(--muted);cursor:pointer;font-size:18px;line-height:1;padding:4px;">✕</button>
        </div>
        <p style="font-size:12px;color:var(--muted2);margin-bottom:16px;font-family:'IBM Plex Mono',monospace;">Konton för dashboarden. Garmin kopplas separat per konto.</p>
        <div id="users-list" style="margin-bottom:20px;"><p style="font-size:12.5px;color:var(--muted2);">Laddar…</p></div>
        <div style="border-top:1px solid var(--border2);padding-top:16px;">
          <div style="font-size:11px;font-weight:700;letter-spacing:0.06em;color:var(--muted2);text-transform:uppercase;margin-bottom:10px;font-family:'IBM Plex Mono',monospace;">Lägg till användare</div>
          <input id="new-user-name" type="text" autocomplete="off" placeholder="Användarnamn" style="width:100%;background:var(--bg3);border:1px solid var(--border2);border-radius:8px;padding:10px 12px;color:var(--text);font-family:'IBM Plex Sans',sans-serif;font-size:13.5px;outline:none;margin-bottom:8px;box-sizing:border-box;" />
          <div style="display:flex;gap:8px;margin-bottom:10px;">
            <input id="new-user-password" type="text" autocomplete="off" placeholder="Lösenord (minst 8 tecken)" style="flex:1;background:var(--bg3);border:1px solid var(--border2);border-radius:8px;padding:10px 12px;color:var(--text);font-family:'IBM Plex Mono',monospace;font-size:13px;outline:none;box-sizing:border-box;" />
            <button type="button" data-action="random-password" title="Slumpa ett starkt lösenord" style="background:var(--bg3);border:1px solid var(--border2);border-radius:8px;padding:0 12px;color:var(--text);cursor:pointer;font-size:14px;">🎲</button>
          </div>
          <button type="button" data-action="create-user" id="create-user-btn" style="width:100%;background:var(--blue);border:none;border-radius:8px;padding:11px;color:#081018;font-family:'IBM Plex Sans',sans-serif;font-size:13.5px;font-weight:700;cursor:pointer;">Skapa användare</button>
          <p id="users-panel-msg" role="alert" style="font-size:12px;margin-top:10px;display:none;"></p>
        </div>
      </div>
    </div>
  `);
  const overlay = document.getElementById('users-panel');
  overlay.addEventListener('click', event => {
    if (event.target === overlay) closeUsersPanel();
  });
  await loadUsersList();
}

async function loadUsersList() {
  const list = document.getElementById('users-list');
  if (!list) return;
  try {
    const res = await fetch('/api/users');
    const data = await res.json();
    if (!res.ok) throw new Error(data.error || 'Kunde inte hämta användare');
    list.innerHTML = data.users.map(u => `
      <div style="display:flex;align-items:center;gap:10px;padding:9px 2px;border-bottom:1px solid var(--border2);">
        <span style="width:8px;height:8px;border-radius:50%;flex-shrink:0;background:${u.garminConnected ? 'var(--accent)' : 'var(--border2)'};" title="${u.garminConnected ? 'Garmin kopplad' : 'Garmin ej kopplad'}"></span>
        <span style="font-size:13.5px;font-weight:600;flex:1;">${escapeHtml(u.username)}</span>
        ${u.isAdmin ? '<span style="font-size:10px;font-weight:700;letter-spacing:0.05em;color:var(--accent);font-family:\'IBM Plex Mono\',monospace;">ADMIN</span>' : ''}
        <span style="font-size:11px;color:var(--muted2);font-family:'IBM Plex Mono',monospace;">${u.garminConnected ? 'Garmin ✓' : 'Ingen Garmin'}</span>
        ${u.isAdmin ? '' : `<button type="button" data-action="delete-user" data-id="${Number(u.id)}" data-username="${escapeHtml(u.username)}" title="Ta bort" style="background:none;border:none;color:var(--muted);cursor:pointer;font-size:14px;padding:2px 4px;">✕</button>`}
      </div>
    `).join('') || '<p style="font-size:12.5px;color:var(--muted2);">Inga användare.</p>';
  } catch (error) {
    list.innerHTML = `<p style="font-size:12.5px;color:var(--red);">${escapeHtml(error.message)}</p>`;
  }
}

function showUsersPanelMessage(text, isError) {
  const msg = document.getElementById('users-panel-msg');
  if (!msg) return;
  msg.textContent = text;
  msg.style.color = isError ? 'var(--red)' : 'var(--accent)';
  msg.style.display = 'block';
}

function fillRandomPassword() {
  const input = document.getElementById('new-user-password');
  if (!input) return;
  const alphabet = 'abcdefghjkmnpqrstuvwxyzABCDEFGHJKMNPQRSTUVWXYZ23456789';
  const bytes = new Uint8Array(14);
  crypto.getRandomValues(bytes);
  input.value = Array.from(bytes, b => alphabet[b % alphabet.length]).join('');
}

async function createUserFromForm() {
  const nameInput = document.getElementById('new-user-name');
  const passwordInput = document.getElementById('new-user-password');
  const button = document.getElementById('create-user-btn');
  const username = nameInput.value.trim();
  const password = passwordInput.value;
  if (!username || !password) {
    showUsersPanelMessage('Fyll i både användarnamn och lösenord.', true);
    return;
  }
  button.disabled = true;
  try {
    const res = await fetch('/api/users', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({username, password}),
    });
    const data = await res.json().catch(() => ({}));
    if (!res.ok) {
      showUsersPanelMessage(data.error || 'Kunde inte skapa användaren.', true);
      return;
    }
    showUsersPanelMessage(`${username} skapad. Dela lösenordet på ett säkert sätt — det visas inte igen.`, false);
    nameInput.value = '';
    passwordInput.value = '';
    await loadUsersList();
  } catch (error) {
    showUsersPanelMessage('Servern kunde inte nås.', true);
  } finally {
    button.disabled = false;
  }
}

async function deleteUser(userId, username) {
  if (!confirm(`Ta bort användaren "${username}"? Kontot försvinner men träningsdatan ligger kvar i databasen.`)) return;
  try {
    const res = await fetch(`/api/users/${userId}`, {method: 'DELETE'});
    const data = await res.json().catch(() => ({}));
    if (!res.ok) {
      showUsersPanelMessage(data.error || 'Kunde inte ta bort användaren.', true);
      return;
    }
    await loadUsersList();
  } catch (error) {
    showUsersPanelMessage('Servern kunde inte nås.', true);
  }
}

function executeAction(trigger, event) {
  const action = trigger.dataset.action;
  if (action === 'goto') goto(trigger.dataset.page);
  else if (action === 'open-users') openUsersPanel();
  else if (action === 'close-users') closeUsersPanel();
  else if (action === 'create-user') createUserFromForm();
  else if (action === 'delete-user') deleteUser(Number(trigger.dataset.id), trigger.dataset.username);
  else if (action === 'random-password') fillRandomPassword();
  else if (action === 'open-garmin-connect') openGarminConnectModal();
  else if (action === 'close-garmin-connect') closeGarminConnectModal();
  else if (action === 'garmin-connect-submit') submitGarminCredentials();
  else if (action === 'garmin-mfa-submit') submitGarminMfaCode();
  else if (action === 'garmin-reload-now') location.reload();
  else if (action === 'open-goal-modal') openGoalModal(false);
  else if (action === 'close-goal-modal') closeGoalModal();
  else if (action === 'save-goal') saveGoalFromForm();
  else if (action === 'logout') performLogout();
  else if (action === 'refresh-data') refreshData();
  else if (action === 'sync-calendar') syncGcal();
  else if (action === 'coach-request') sendCoachRequest();
  else if (action === 'refresh-insights') loadInsights(true);
  else if (action === 'toggle-ac-loop') toggleAcLoop();
  else if (action === 'set-ac-setpoint') setAcSetpoint();
  else if (action === 'save-ac-bedtime') saveAcBedtime();
  else if (action === 'clear-ac-bedtime') clearAcBedtime();
  else if (action === 'send-ac-command') sendManualAcCommand();
  else if (action === 'refresh-sleep-insights') loadSleepInsights(true);
  else if (action === 'calendar-view') setCalendarView(trigger.dataset.view);
  else if (action === 'strength-tab') strengthTab(trigger.dataset.tab);
  else if (action === 'save-journal') saveJournalEntry();
  else if (action === 'quick-prompt') qa(trigger.dataset.prompt);
  else if (action === 'send-chat') send();
  else if (action === 'save-note') saveNote();
  else if (action === 'edit-journal') editJournalDate(trigger.dataset.date);
  else if (action === 'delete-journal') deleteJournalEntry(event, Number(trigger.dataset.id));
  else if (action === 'delete-note') deleteNote(Number(trigger.dataset.id));
  else if (action === 'apply-strength-rx') applyStrengthRecommendation(trigger.dataset.context, Number(trigger.dataset.index));
  else if (action === 'toggle-session') toggleSession(trigger.dataset.session);
  else if (action === 'add-exercise') {
    const context = trigger.dataset.context;
    context ? addExercise(trigger.dataset.session, context) : addExercise(trigger.dataset.session);
  } else if (action === 'delete-exercise') {
    const context = trigger.dataset.context;
    context
      ? deleteExercise(Number(trigger.dataset.id), trigger.dataset.session, context)
      : deleteExercise(Number(trigger.dataset.id), trigger.dataset.session);
  }
}

document.addEventListener('click', event => {
  const trigger = event.target.closest('[data-action]');
  if (trigger) executeAction(trigger, event);
});

document.addEventListener('keydown', event => {
  const trigger = event.target.closest('[data-action][role="button"]');
  if (trigger && (event.key === 'Enter' || event.key === ' ')) {
    event.preventDefault();
    executeAction(trigger, event);
  }
});

document.getElementById('coach-request-input')?.addEventListener('keydown', event => {
  if (event.key === 'Enter') sendCoachRequest();
});
const acSetpointInput = document.getElementById('ac-setpoint-input');
acSetpointInput?.addEventListener('input', () => { acSetpointInput.dataset.dirty = '1'; });
acSetpointInput?.addEventListener('blur', () => { delete acSetpointInput.dataset.dirty; });

  // Navigation
  function goto(id) {
    document.querySelectorAll('.page').forEach(p => p.classList.remove('active'));
    document.querySelectorAll('.nav-item').forEach(n => n.classList.remove('active'));
    document.getElementById('page-' + id).classList.add('active');
    document.querySelectorAll('.nav-item').forEach(n => {
      if (n.dataset.page === id) n.classList.add('active');
    });
    window.scrollTo(0, 0);
    if (id === 'health')   loadHealth();
    if (id === 'sleep')    { loadHealth(); loadSleepCoach(); loadSleepInsights(); setTimeout(() => { if (currentHealthData) renderSleepStageChart(currentHealthData.sleep?.levels || [], currentHealthData.sleep?.startGMT, currentHealthData.sleep?.endGMT); }, 50); }
    if (id === 'analysis') loadAnalysis();
    if (id === 'strength') loadStrengthPage();
    if (id === 'journal')  loadJournal();
    if (id === 'coach')    loadNotes();
    if (id === 'upcoming') checkGcalStatus();
    if (id === 'climate')  { loadWeatherStatus(); loadAcStatus(); loadAcLoopStatus(); loadAcBedtime(); loadHumidityStatus(); loadAcHistory(); }
  }

  // Nedräkning och målrad ritas av renderGoalUi() när målet laddats.
  loadHealth();


function setHG(scoreId, barId, badgeId, descId, score, desc) {
    const el = document.getElementById(scoreId);
    const bar = document.getElementById(barId);
    const badge = document.getElementById(badgeId);
    const descEl = document.getElementById(descId);
    if (!el) return;
    el.textContent = Math.round(score);
    bar.style.width = Math.min(score, 100) + '%';
    descEl.textContent = desc;
    if (score >= 75) {
      el.style.color = 'var(--green)';
      badge.className = 'hg-status hs-great';
      badge.textContent = 'Good';
    } else if (score >= 50) {
      el.style.color = 'var(--amber)';
      badge.className = 'hg-status hs-ok';
      badge.textContent = 'Ok';
    } else {
      el.style.color = 'var(--red)';
      badge.className = 'hg-status hs-low';
      badge.textContent = 'Rest';
    }
  }

  function setMetric(valId, statusId, value, unit, statusText, col) {
    const v = document.getElementById(valId);
    const s = document.getElementById(statusId);
    if (v) { v.textContent = value; v.style.color = col || ''; }
    if (s) { s.textContent = statusText || unit || ''; s.style.color = col || ''; }
  }

  // Hälsodata
  function renderSleepPage(h) {
    const sleep = h.sleep || {};
    const totalSec = sleep.totalSec || 0;
    const score = sleep.score || 0;
    const deep = sleep.deepPct || 0;
    const rem = sleep.remPct || 0;
    const fmt = s => {
      const hours = Math.floor(s / 3600);
      const minutes = Math.floor((s % 3600) / 60);
      return hours + 'h ' + minutes + 'm';
    };
    const setText = (id, value) => { const el = document.getElementById(id); if (el) el.textContent = value; };
    const setWidth = (id, value) => { const el = document.getElementById(id); if (el) el.style.width = Math.max(0, Math.min(100, value)) + '%'; };

    if (!totalSec) {
      ['sleep-page-score', 'sleep-page-total', 'sleep-page-deep', 'sleep-page-rem'].forEach(id => setText(id, '–'));
      setText('sleep-page-score-sub', 'Ingen sömn registrerad i natt');
      setText('sleep-page-total-sub', 'Ingen data');
      setText('sleep-page-deep-sub', 'Ingen data');
      setText('sleep-page-rem-sub', 'Ingen data');
      ['sleep-page-score-bar', 'sleep-page-total-bar', 'sleep-page-deep-bar', 'sleep-page-rem-bar'].forEach(id => setWidth(id, 0));
      return;
    }

    const targetSleepSec = 7.5 * 3600;
    const debtSec = Math.max(0, targetSleepSec - totalSec);

    setText('sleep-page-score', score || '-');
    setText('sleep-page-score-sub', score >= 80 ? 'God återhämtning' : score >= 60 ? 'Okej, men kan bli bättre' : 'Prioritera sömn i natt');
    setWidth('sleep-page-score-bar', score || 0);

    setText('sleep-page-total', fmt(totalSec));
    setText('sleep-page-total-sub', debtSec < 900 ? 'Mål uppnått' : 'Saknar ' + fmt(debtSec));
    setWidth('sleep-page-total-bar', totalSec / targetSleepSec * 100);

    setText('sleep-page-deep', deep + '%');
    setText('sleep-page-deep-sub', deep >= 15 ? 'Inom målintervall' : 'Under mål 15–25%');
    setWidth('sleep-page-deep-bar', deep / 25 * 100);

    setText('sleep-page-rem', rem + '%');
    setText('sleep-page-rem-sub', rem >= 20 ? 'Inom målintervall' : 'Under mål 20–25%');
    setWidth('sleep-page-rem-bar', rem / 25 * 100);

    // Update arc gauge for score
    const scoreArc = document.getElementById('sleep-score-arc');
    if (scoreArc) {
      const total = 175.9;
      const col = score >= 80 ? '#C8F135' : score >= 60 ? '#F59E0B' : '#FF6B6B';
      scoreArc.style.strokeDashoffset = (total * (1 - Math.min(1, (score || 0) / 100))).toFixed(1);
      scoreArc.style.stroke = col;
      const scoreVal = document.getElementById('sleep-page-score');
      if (scoreVal) scoreVal.style.fill = col;
    }

    // Update radial rings
    const deepRing = document.getElementById('sleep-deep-ring');
    if (deepRing) {
      const circ = 188.5;
      deepRing.style.strokeDashoffset = (circ * (1 - Math.min(1, (deep || 0) / 25))).toFixed(1);
    }
    const remRing = document.getElementById('sleep-rem-ring');
    if (remRing) {
      const circ = 188.5;
      remRing.style.strokeDashoffset = (circ * (1 - Math.min(1, (rem || 0) / 25))).toFixed(1);
    }

    renderSleepStageChart(sleep.levels || [], sleep.startGMT, sleep.endGMT);
  }

  function renderSleepCoach(data) {
    const title = document.getElementById('sleep-coach-title');
    const badge = document.getElementById('sleep-coach-badge');
    const summary = document.getElementById('sleep-coach-summary');
    const meta = document.getElementById('sleep-coach-meta');
    const list = document.getElementById('sleep-coach-list');
    if (!title || !summary || !meta || !list) return;

    if (!data || data.error) {
      title.textContent = 'Sömncoach otillgänglig';
      badge.textContent = 'FEL';
      badge.className = 'today-badge badge-amber';
      summary.textContent = data?.error || 'Kunde inte bygga ett sömnschema just nu.';
      meta.innerHTML = '';
      list.innerHTML = '';
      return;
    }

    title.textContent = data.headline || 'Sömncoach';
    badge.textContent = data.calendarSynced ? 'KALENDER' : 'INGEN KALENDER';
    badge.className = data.calendarSynced ? 'today-badge badge-green' : 'today-badge badge-amber';
    summary.textContent = data.summary || 'Rekommenderad läggdags i natt.';

    const fmtHours = h => h == null ? '-' : Number(h).toFixed(1).replace('.0', '') + 'h';
    meta.innerHTML = [
      { label: 'Mål', value: fmtHours(data.targetHours) },
      { label: 'Senaste sömn', value: fmtHours(data.lastSleepHours) },
      { label: '7-dagars snitt', value: fmtHours(data.avgSleepHours) },
      { label: 'Kalender', value: data.calendarSynced ? 'Synkad' : 'Synk behövs' },
    ].map(m => `
      <span style="display:inline-flex;gap:6px;align-items:center;background:var(--bg3);border:1px solid var(--border);border-radius:999px;padding:6px 9px;font-size:11px;font-family:'IBM Plex Mono',monospace;color:var(--muted2);">
        <span style="color:var(--muted);">${escapeHtml(m.label)}</span>
        <strong style="color:var(--text);font-weight:700;">${escapeHtml(m.value)}</strong>
      </span>`).join('');

    const night = data.night || (data.nights || [])[0];
    if (!night) {
      list.innerHTML = '<div style="font-size:12px;color:var(--muted3);">Ingen läggdags kunde beräknas.</div>';
      return;
    }

    const anchor = night.anchor
      ? `${escapeHtml(night.anchor.title)} kl ${escapeHtml(night.anchor.time)}`
      : 'Inga tidiga kalenderhändelser ändrar morgondagen';
    list.innerHTML = `
      <div style="display:grid;grid-template-columns:120px 1fr;gap:16px;background:var(--bg2);border:1px solid var(--border);border-left:3px solid var(--blue);border-radius:10px;padding:15px 16px;">
        <div>
          <div style="font-size:11px;font-family:'IBM Plex Mono',monospace;color:var(--muted);text-transform:uppercase;letter-spacing:0.06em;">I natt</div>
          <div style="font-size:28px;font-weight:800;margin-top:4px;color:var(--text);">${escapeHtml(night.bedtime)}</div>
          <div style="font-size:11px;color:var(--muted2);font-family:'IBM Plex Mono',monospace;">lägg dig</div>
        </div>
        <div style="min-width:0;">
          <div style="display:flex;gap:16px;flex-wrap:wrap;margin-bottom:8px;">
            <span style="font-size:12px;color:var(--muted2);"><strong style="color:var(--green);">Varva ner</strong> ${escapeHtml(night.windDown)}</span>
            <span style="font-size:12px;color:var(--muted2);"><strong style="color:var(--blue);">Vakna</strong> ${escapeHtml(night.wake)}</span>
            <span style="font-size:12px;color:var(--muted2);"><strong style="color:var(--amber);">AC</strong> ${escapeHtml(night.acPrecool)}</span>
          </div>
          <div style="font-size:13px;color:var(--text);line-height:1.45;">${escapeHtml(night.reason)}</div>
          <div style="font-size:11px;color:var(--muted);margin-top:4px;font-family:'IBM Plex Mono',monospace;">${anchor}</div>
        </div>
      </div>`;
  }

  async function loadSleepCoach() {
    const title = document.getElementById('sleep-coach-title');
    const summary = document.getElementById('sleep-coach-summary');
    const list = document.getElementById('sleep-coach-list');
    if (title) title.textContent = 'Sömncoach';
    if (summary) summary.textContent = 'Bygger ett sömnschema från din kalender…';
    if (list) list.innerHTML = '<div style="font-size:12px;color:var(--muted3);font-family:\'IBM Plex Mono\',monospace;">Laddar schema…</div>';
    try {
      const res = await fetch('/api/sleep-coach');
      const contentType = res.headers.get('content-type') || '';
      if (!res.ok) {
        throw new Error(res.status === 404
          ? 'Sleep coach API saknas på servern. Kör git pull och starta om dashboarden på Pi:n.'
          : 'Sleep coach API svarade med felkod ' + res.status + '.');
      }
      if (!contentType.includes('application/json')) {
        throw new Error('Servern svarade inte med JSON. Starta om Flask-dashboarden efter git pull.');
      }
      renderSleepCoach(await res.json());
    } catch (e) {
      renderSleepCoach({ error: e.message });
    }
  }

  function renderSleepStageChart(levels, startGMT, endGMT) {
    const container = document.getElementById('sleep-stage-canvas');
    const empty = document.getElementById('sleep-chart-empty');
    if (!container) return;

    const timesEl = document.getElementById('sleep-chart-times');

    if (!levels || levels.length === 0) {
      container.innerHTML = '';
      container.style.display = 'none';
      if (timesEl) timesEl.style.display = 'none';
      if (empty) empty.style.display = 'block';
      return;
    }
    container.style.display = 'block';
    if (empty) empty.style.display = 'none';

    const parseGMT = s => {
      if (!s) return null;
      if (typeof s === 'number') return new Date(s);
      return new Date(s.replace(' ', 'T') + 'Z');
    };

    const sorted = [...levels].sort((a, b) => parseGMT(a.startGMT) - parseGMT(b.startGMT));
    const chartStart = parseGMT(startGMT) || parseGMT(sorted[0].startGMT);
    const chartEnd   = parseGMT(endGMT)   || parseGMT(sorted[sorted.length - 1].endGMT);
    if (!chartStart || !chartEnd) return;
    const totalMs = chartEnd - chartStart;

    // All times shown in Swedish local time (Europe/Stockholm), DST-aware.
    const TZ = 'Europe/Stockholm';
    // Offset (ms) between an absolute instant and its Stockholm wall-clock.
    const tzOffsetMs = d => {
      const p = new Intl.DateTimeFormat('en-US', { timeZone: TZ, hour12: false,
        year: 'numeric', month: '2-digit', day: '2-digit',
        hour: '2-digit', minute: '2-digit', second: '2-digit' })
        .formatToParts(d).reduce((a, x) => (a[x.type] = x.value, a), {});
      const h = p.hour === '24' ? 0 : +p.hour;
      return Date.UTC(+p.year, +p.month - 1, +p.day, h, +p.minute, +p.second) - d.getTime();
    };

    // Sleep start/end times below the chart
    const fmtLocal = d => d.toLocaleTimeString('sv-SE', { hour: '2-digit', minute: '2-digit', timeZone: TZ });
    if (timesEl) {
      timesEl.style.display = 'flex';
      const sEl = document.getElementById('sleep-chart-t-start');
      const eEl = document.getElementById('sleep-chart-t-end');
      if (sEl) sEl.innerHTML = `<span style="color:var(--muted3);">Somnade</span> <span style="color:#CBD5E1;">${fmtLocal(chartStart)}</span>`;
      if (eEl) eEl.innerHTML = `<span style="color:var(--muted3);">Vaknade</span> <span style="color:#CBD5E1;">${fmtLocal(chartEnd)}</span>`;
    }

    const STAGE = {
      0: { color: '#EC4899', name: 'Djup' },
      1: { color: '#10B981', name: 'Lätt' },
      2: { color: '#EF4444', name: 'Vaken' },
      3: { color: '#38BDF8', name: 'REM' },
    };

    const W = container.clientWidth || 600;
    const BAR_H = 68;
    const TICK_H = 20;
    const H = BAR_H + TICK_H;

    const fmtTime = d => d.toLocaleTimeString('sv-SE', { hour: '2-digit', minute: '2-digit', timeZone: TZ });
    const fmtDur = ms => { const m = Math.round(ms / 60000); return m >= 60 ? Math.floor(m/60)+'h '+(m%60)+'m' : m+'m'; };

    const parts = [
      `<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 ${W} ${H}" width="${W}" height="${H}" style="display:block;overflow:visible;">`,
      `<defs><clipPath id="sleep-bar-clip"><rect x="0" y="0" width="${W}" height="${BAR_H}" rx="7"/></clipPath></defs>`,
      `<rect x="0" y="0" width="${W}" height="${BAR_H}" rx="7" fill="rgba(255,255,255,0.05)"/>`,
      `<g clip-path="url(#sleep-bar-clip)">`,
    ];

    // Single-row segments
    for (const seg of sorted) {
      const level = Math.round(seg.activityLevel ?? seg.level ?? 1);
      const info  = STAGE[level] ?? STAGE[1];
      const t0 = parseGMT(seg.startGMT);
      const t1 = parseGMT(seg.endGMT);
      if (!t0 || !t1) continue;
      const x = ((t0 - chartStart) / totalMs) * W;
      const w = Math.max(1, ((t1 - t0) / totalMs) * W);
      parts.push(`<rect class="sleep-seg" data-stage="${level}" x="${x.toFixed(1)}" y="0" width="${w.toFixed(1)}" height="${BAR_H}" fill="${info.color}" data-name="${info.name}" data-t0="${fmtTime(t0)}" data-t1="${fmtTime(t1)}" data-dur="${fmtDur(t1 - t0)}" data-color="${info.color}" style="cursor:pointer;transition:opacity 0.12s;"/>`);
    }

    parts.push(`</g>`);

    // Hour ticks every 2h, aligned to round *local* hours (DST-aware)
    const startMs = chartStart.getTime();
    const offset = tzOffsetMs(chartStart);             // local = utc + offset
    const STEP = 2 * 3600000;
    const firstTickMs = Math.ceil((startMs + offset) / STEP) * STEP - offset;
    for (let t = firstTickMs; t <= startMs + totalMs; t += STEP) {
      const tx = (((t - startMs) / totalMs) * W).toFixed(1);
      if (parseFloat(tx) < 0 || parseFloat(tx) > W) continue;
      const label = fmtLocal(new Date(t));
      parts.push(`<line x1="${tx}" y1="${BAR_H}" x2="${tx}" y2="${BAR_H + 5}" stroke="#64748B" stroke-width="1"/>`);
      parts.push(`<text x="${tx}" y="${BAR_H + 16}" text-anchor="middle" font-size="11" fill="#CBD5E1" font-family="var(--font-mono,monospace)">${label}</text>`);
    }

    parts.push('</svg>');
    container.innerHTML = parts.join('');

    // Interactions
    const svgEl = container.querySelector('svg');
    if (!svgEl) return;
    const allSegs = svgEl.querySelectorAll('.sleep-seg');

    svgEl.addEventListener('mouseover', e => {
      const seg = e.target.closest('.sleep-seg');
      if (seg) {
        const activeStage = seg.dataset.stage;
        allSegs.forEach(s => { s.style.opacity = s.dataset.stage === activeStage ? '1' : '0.18'; });
        clearTimeout(tipTimeout);
        tipBox.innerHTML = `
          <div class="tip-title" style="color:${seg.dataset.color}">${seg.dataset.name}</div>
          <div class="tip-desc">${seg.dataset.t0} – ${seg.dataset.t1}</div>
          <div class="tip-desc" style="color:var(--muted2);margin-top:2px;">${seg.dataset.dur}</div>`;
        const vw = window.innerWidth;
        let left = e.clientX + 12;
        if (left + 180 > vw - 8) left = e.clientX - 192;
        tipBox.style.left = left + 'px';
        tipBox.style.top  = (e.clientY - 40) + 'px';
        tipBox.classList.add('visible');
      } else {
        allSegs.forEach(s => { s.style.opacity = '1'; });
        hideTip();
      }
    });

    svgEl.addEventListener('mouseleave', () => {
      allSegs.forEach(s => { s.style.opacity = '1'; });
      hideTip();
    });
  }

  let currentHealthData = null;

  function clamp(n, lo, hi) {
    return Math.max(lo, Math.min(hi, n));
  }

  function escapeHtml(value) {
    return String(value ?? '').replace(/[&<>"']/g, c => ({
      '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;'
    }[c]));
  }

  function todayLocalDate() {
    const d = new Date();
    d.setMinutes(d.getMinutes() - d.getTimezoneOffset());
    return d.toISOString().slice(0, 10);
  }

  function getISOWeekInfo(date = new Date()) {
    const d = new Date(Date.UTC(date.getFullYear(), date.getMonth(), date.getDate()));
    const dayNum = d.getUTCDay() || 7;
    d.setUTCDate(d.getUTCDate() + 4 - dayNum);
    const yearStart = new Date(Date.UTC(d.getUTCFullYear(), 0, 1));
    const week = Math.ceil((((d - yearStart) / 86400000) + 1) / 7);
    return { week, dow: (date.getDay() + 6) % 7, year: d.getUTCFullYear() };
  }

  // Init appbar immediately (before health data)
  (function initAppbar() {
    const hiEl = document.getElementById('appbar-hi');
    if (hiEl) {
      const hr = new Date().getHours();
      const greet = hr < 12 ? 'God morgon' : hr < 17 ? 'God eftermiddag' : 'God kväll';
      hiEl.textContent = greet + ', Hugo';
    }
    const dateEl = document.getElementById('appbar-date');
    if (dateEl) {
      const d = new Date();
      const days = ['Söndag','Måndag','Tisdag','Onsdag','Torsdag','Fredag','Lördag'];
      const { week } = getISOWeekInfo();
      dateEl.textContent = days[d.getDay()] + ' · vecka ' + week;
    }
  })();

  function getWeekBounds(date = new Date()) {
    const start = new Date(date);
    start.setDate(date.getDate() - ((date.getDay() || 7) - 1));
    start.setHours(0,0,0,0);
    const end = new Date(start);
    end.setDate(start.getDate() + 7);
    return { start, end };
  }

  function isRunActivity(a) {
    return ['running','track_running','treadmill_running','trail_running'].includes(a.activityType?.typeKey);
  }

  function sessionLoadEstimate(s) {
    if (!s) return 0;
    if (s.type === 'lift') return 40;
    const perKm = { easy: 7, run: 22, race: 25, rest: 0 };
    return (s.km || 0) * (perKm[s.type] ?? 9);
  }

  function computeCnsScore(h) {
    if (!h) return null;
    const hrvPct = h.hrv?.component ?? h.hrv?.pct ?? 50;
    const sleepScore = h.sleep?.score ?? 50;
    const readiness = h.readiness?.score ?? 50;
    const stressVal = h.stress?.avg ?? 50;
    return Math.round(
      0.40 * Math.min(hrvPct, 100) +
      0.30 * sleepScore +
      0.20 * readiness +
      0.10 * (100 - Math.min(stressVal, 100))
    );
  }

  function getHrvBaselineText(hrv) {
    if (!hrv) return 'Ingen baslinje';
    if (hrv.balancedLow != null && hrv.balancedUpper != null) {
      return `baslinje ${hrv.balancedLow}-${hrv.balancedUpper} ms`;
    }
    if (hrv.weeklyAvg != null) return `snitt ${hrv.weeklyAvg} ms`;
    return 'Ingen baslinje';
  }

  function getHrvStatusLabel(status) {
    const key = String(status || '').toUpperCase();
    return {
      BALANCED: 'HRV balanserad',
      UNBALANCED: 'HRV i obalans',
      LOW: 'HRV låg',
      POOR: 'HRV mycket låg',
    }[key] || '';
  }

  function getHrvStatusText(hrv) {
    if (!hrv) return 'HRV otillgängligt';
    const status = hrv.status && hrv.status !== 'NONE' ? getHrvStatusLabel(hrv.status) : null;
    if (status) return status;
    if (hrv.pct != null) return `HRV ${hrv.pct}%`;
    return 'HRV otillgängligt';
  }

  function getHrvVerdictText(hrv) {
    const statusText = getHrvStatusLabel(hrv?.status);
    if (statusText) {
      const key = String(hrv.status || '').toUpperCase();
      return {
        BALANCED: 'HRV balanserad — autonoma nervsystemet ligger i ditt normala spann',
        UNBALANCED: 'HRV i obalans — utanför ditt normala spann, träna med viss försiktighet',
        LOW: 'HRV låg — under baslinjen, prioritera återhämtning',
        POOR: 'HRV mycket låg — längre låg trend, vila rekommenderas',
      }[key] || statusText;
    }
    if (!hrv?.verdict) return 'HRV-data saknas';
    return String(hrv.verdict)
      .replace(/Balanced\s*[—-]\s*autonomic system in your normal range/i, 'HRV balanserad — autonoma nervsystemet ligger i ditt normala spann')
      .replace(/Unbalanced\s*[—-]\s*outside your normal range,\s*train with caution/i, 'HRV i obalans — utanför ditt normala spann, träna med viss försiktighet')
      .replace(/Low\s*[—-]\s*below baseline,\s*prioritize recovery/i, 'HRV låg — under baslinjen, prioritera återhämtning')
      .replace(/Poor\s*[—-]\s*sustained low HRV,\s*rest needed/i, 'HRV mycket låg — längre låg trend, vila rekommenderas')
      .replace(/Not enough baseline data yet/i, 'Inte tillräckligt med baslinjedata ännu');
  }

  function getHrvClass(hrv) {
    if (hrv?.light === 'green') return 'good';
    if (hrv?.light === 'red') return 'bad';
    if (hrv?.light === 'amber') return 'warn';
    const pct = hrv?.component ?? hrv?.pct;
    if (pct == null) return 'warn';
    return pct >= 85 ? 'good' : pct >= 70 ? 'warn' : 'bad';
  }

  function getHrvColor(hrv) {
    if (hrv?.light === 'green') return 'var(--green)';
    if (hrv?.light === 'red') return 'var(--red)';
    if (hrv?.light === 'amber') return 'var(--amber)';
    const pct = hrv?.component ?? hrv?.pct ?? 0;
    return pct >= 85 ? 'var(--green)' : pct >= 70 ? 'var(--amber)' : 'var(--red)';
  }

  function stressMeta(value) {
    const n = Number(value);
    if (!Number.isFinite(n)) return { color:'var(--muted2)', status:'Ingen data', badge:'Ingen data', pct:0 };
    if (n <= 25) return { color:'var(--green)', status:'Vila', badge:'Vila', pct:n };
    if (n <= 50) return { color:'var(--green)', status:'Låg stress', badge:'Låg', pct:n };
    if (n <= 75) return { color:'var(--amber)', status:'Måttlig stress', badge:'Måttlig', pct:n };
    return { color:'var(--red)', status:'Hög stress', badge:'Hög', pct:n };
  }

  function setCnsDriver(key, value, pct, color) {
    const val = document.getElementById(`cns-driver-${key}-val`);
    const bar = document.getElementById(`cns-driver-${key}-bar`);
    if (val) {
      val.textContent = value ?? '-';
      val.style.color = color || 'var(--muted2)';
    }
    if (bar) {
      bar.style.width = Math.max(0, Math.min(100, pct || 0)) + '%';
      bar.style.background = color || 'var(--muted2)';
    }
  }

  function drawStressHistory(points, current, avg) {
    const svg = document.getElementById('stress-history-chart');
    if (!svg) return;
    const values = (points || []).map(p => Number(p.value)).filter(Number.isFinite);
    if (values.length < 2) {
      svg.innerHTML = '<text x="18" y="72" fill="currentColor" style="color:var(--muted);font-size:11px;">Mer historik visas efter några synkar.</text>';
      return;
    }
    const W = 320, H = 132, PX = 18, PT = 28, PB = 24;
    const chartH = H - PT - PB;
    const max = Math.min(100, Math.max(70, ...values, current ?? 0, avg ?? 0) + 8);
    const min = Math.max(0, Math.min(15, ...values, current ?? 100, avg ?? 100) - 8);
    const span = Math.max(1, max - min);
    const pts = values.map((v, i) => ({
      x: PX + (i / Math.max(1, values.length - 1)) * (W - PX * 2),
      y: PT + (1 - ((v - min) / span)) * chartH
    }));
    const lineD = pts.map((p, i) => {
      if (i === 0) return `M${p.x.toFixed(1)},${p.y.toFixed(1)}`;
      const prev = pts[i - 1];
      const cx = (prev.x + p.x) / 2;
      return `C${cx.toFixed(1)},${prev.y.toFixed(1)} ${cx.toFixed(1)},${p.y.toFixed(1)} ${p.x.toFixed(1)},${p.y.toFixed(1)}`;
    }).join(' ');
    const baseY = H - PB;
    const areaD = `${lineD} L${pts[pts.length - 1].x.toFixed(1)},${baseY} L${pts[0].x.toFixed(1)},${baseY} Z`;
    const avgY = avg == null ? null : PT + (1 - ((avg - min) / span)) * chartH;
    const last = pts[pts.length - 1];
    const first = pts[0];
    const avgLabel = avg == null ? '-' : Number(avg).toFixed(1);
    const lastValue = values[values.length - 1];
    const stressColor = stressMeta(lastValue).color;
    const grid = [0.25, 0.5, 0.75].map(t => {
      const y = PT + chartH * t;
      return `<line x1="${PX}" y1="${y.toFixed(1)}" x2="${W - PX}" y2="${y.toFixed(1)}" class="stress-grid-line"/>`;
    }).join('');
    svg.innerHTML = `
      <defs>
        <linearGradient id="stress-area-grad" x1="0" y1="0" x2="0" y2="1">
          <stop offset="0%" stop-color="#FBBF24" stop-opacity="0.28"/>
          <stop offset="72%" stop-color="#FBBF24" stop-opacity="0.04"/>
          <stop offset="100%" stop-color="#FBBF24" stop-opacity="0"/>
        </linearGradient>
        <filter id="stress-line-glow" x="-10%" y="-80%" width="120%" height="260%">
          <feGaussianBlur stdDeviation="2.2" result="blur"/>
          <feMerge><feMergeNode in="blur"/><feMergeNode in="SourceGraphic"/></feMerge>
        </filter>
      </defs>
      <rect x="0" y="0" width="${W}" height="${H}" rx="12" class="stress-chart-bg"/>
      ${grid}
      ${avgY == null ? '' : `<line x1="${PX}" y1="${avgY.toFixed(1)}" x2="${W - PX}" y2="${avgY.toFixed(1)}" class="stress-avg-line"/>`}
      <path d="${areaD}" class="stress-area"/>
      <path d="${lineD}" class="stress-line-shadow"/>
      <path d="${lineD}" class="stress-line" filter="url(#stress-line-glow)"/>
      <circle cx="${first.x.toFixed(1)}" cy="${first.y.toFixed(1)}" r="2" class="stress-end-dot stress-start-dot"/>
      <circle cx="${last.x.toFixed(1)}" cy="${last.y.toFixed(1)}" r="6.5" class="stress-current-halo"/>
      <circle cx="${last.x.toFixed(1)}" cy="${last.y.toFixed(1)}" r="3.4" fill="${stressColor}" class="stress-current-dot"/>
      <text x="${PX}" y="15" class="stress-chart-label">Senaste 30 dagar</text>
      <text x="${W - PX}" y="15" text-anchor="end" class="stress-chart-label">Snitt ${avgLabel}</text>
      <text x="${last.x.toFixed(1)}" y="${Math.max(25, last.y - 12).toFixed(1)}" text-anchor="middle" class="stress-current-label">${Math.round(lastValue)}</text>`;
  }

  function getSessionDate(session, year) {
    const monday = getMondayOfISOWeek(session.week, year);
    const d = new Date(monday);
    d.setDate(monday.getDate() + (session.dow || 0));
    return d;
  }

  function getNextSessions(limit = 5) {
    const now = new Date();
    now.setHours(0,0,0,0);
    const year = now.getFullYear();
    return PLAN_SESSIONS
      .map(s => ({ ...s, date: getSessionDate(s, year) }))
      .filter(s => s.date >= now && s.status !== 'completed' && s.status !== 'skipped')
      .sort((a,b) => a.date - b.date)
      .slice(0, limit);
  }

  function getWeekTrainingStats() {
    const { week } = getISOWeekInfo();
    const { start, end } = getWeekBounds();
    const planned = PLAN_SESSIONS.filter(s => s.week === week);

    // Deduplicate by dow: after reseed, DB can have both a 'completed' and a
    // 'planned' row for the same slot. Prefer 'planned'; fall back to any.
    const dedupMap = new Map();
    for (const s of planned) {
      if (!dedupMap.has(s.dow) || s.status === 'planned') dedupMap.set(s.dow, s);
    }
    const uniquePlanned = [...dedupMap.values()];
    const plannedKm = uniquePlanned.reduce((sum, s) => sum + (s.km || 0), 0);

    const completedRuns = recentActivities.filter(a => {
      const d = new Date(a.startTimeLocal || a.beginTimestamp);
      return d >= start && d < end && isRunActivity(a);
    });
    const completedKm = completedRuns.reduce((sum, a) => sum + ((a.distance || 0) / 1000), 0);
    const completedLoad = recentActivities
      .filter(a => {
        const d = new Date(a.startTimeLocal || a.beginTimestamp);
        return d >= start && d < end;
      })
      .reduce((sum, a) => sum + (a.activityTrainingLoad || 0), 0);
    const todayDow = (new Date().getDay() + 6) % 7;
    const remaining = uniquePlanned.filter(s => s.dow >= todayDow && s.status !== 'completed' && s.status !== 'skipped');
    const remainingLoad = remaining.reduce((sum, s) => sum + sessionLoadEstimate(s), 0);
    return { week, planned: uniquePlanned, plannedKm, completedKm, completedLoad, remaining, remainingLoad };
  }

  function renderTrainingCockpit() {
    const primary = document.getElementById('cockpit-primary');
    if (!primary) return;

    const h = currentHealthData;
    const cns = computeCnsScore(h);
    const stats = getWeekTrainingStats();
    const next = getNextSessions(5);
    const nextKey = next.find(s => ['run','race'].includes(s.type)) || next[0];
    const ratio = trainingLoadData?.ratio != null ? Number(trainingLoadData.ratio) : null;
    const projectedRatio = trainingLoadData?.chronic
      ? (Number(trainingLoadData.acute || 0) + stats.remainingLoad) / Number(trainingLoadData.chronic)
      : null;
    let color = 'var(--amber)';
    let title = 'Bygg, men håll måtta';
    let copy = 'Kör det planerade passet om inte sömn, HRV eller belastningsrisk säger annat.';
    let tagClass = 'warn';
    if (cns == null) {
      title = 'Väntar på återhämtningsdata';
      copy = 'Synka Garmin för att låsa upp dagens beslut, belastningsrisk och planjusteringar.';
    } else if (cns >= 70 && (ratio == null || ratio <= 1.3)) {
      color = 'var(--green)';
      title = 'Grönt ljus för kvalitet';
      copy = nextKey && ['run','race'].includes(nextKey.type)
        ? 'Återhämtning och belastning ser bra ut. Kör nyckelpasset, men skydda de lugna dagarna runt det.'
        : 'Återhämtningen är bra. Du kan träna normalt och lägga till kvalitet bara om planen kräver det.';
      tagClass = 'good';
    } else if (cns < 45 || (ratio && ratio > 1.5)) {
      color = 'var(--red)';
      title = 'Växla ner idag';
      copy = 'Återhämtning eller belastningsrisk är hög. Byt intensitet mot vila, rörlighet eller mycket lugn Z2.';
      tagClass = 'bad';
    } else if (ratio && ratio > 1.3) {
      color = 'var(--amber)';
      title = 'Håll volymen kontrollerad';
      copy = 'Aktuell ACWR ligger över det normala. Håll lugna pass lugna och undvik extra volym.';
      tagClass = 'warn';
    }

    primary.style.setProperty('--cockpit-color', color);
    document.getElementById('cockpit-score').textContent = cns == null ? '--' : cns;
    document.getElementById('cockpit-decision-title').textContent = title;
    document.getElementById('cockpit-decision-copy').textContent = copy;
    document.getElementById('cockpit-week-volume').textContent =
      stats.plannedKm ? `${stats.completedKm.toFixed(1)} / ${stats.plannedKm} km` : `${stats.completedKm.toFixed(1)} km`;
    document.getElementById('cockpit-load-risk').textContent =
      ratio != null ? `ACWR ${ratio.toFixed(2)}` : 'Ingen belastning';
    document.getElementById('cockpit-key-session').textContent = nextKey ? nextKey.title : 'Inget pass';

    const compliance = document.getElementById('cockpit-compliance');
    const progress = stats.plannedKm ? stats.completedKm / stats.plannedKm : 0;
    compliance.textContent = stats.plannedKm ? `${Math.round(clamp(progress, 0, 1.2) * 100)}% av veckan` : 'Ingen plan';
    compliance.className = 'cockpit-tag ' + (progress > 1.1 ? 'warn' : progress >= 0.6 ? 'good' : 'warn');

    document.getElementById('cockpit-week-title').textContent =
      stats.plannedKm ? `V${stats.week}: ${stats.plannedKm} km planerat` : `V${stats.week}: plan saknas`;

    const limiters = [];
    if (h?.hrv) limiters.push({ text: getHrvStatusText(h.hrv), cls: getHrvClass(h.hrv) });
    if (h?.sleep?.score != null) limiters.push({ text: `Sömn ${h.sleep.score}`, cls: h.sleep.score >= 80 ? 'good' : h.sleep.score >= 60 ? 'warn' : 'bad' });
    if (h?.bodyBattery?.current != null) limiters.push({ text: `Batteri ${h.bodyBattery.current}`, cls: h.bodyBattery.current >= 60 ? 'good' : h.bodyBattery.current >= 30 ? 'warn' : 'bad' });
    if (h?.stress?.avg != null) limiters.push({ text: `Stress ${h.stress.avg}`, cls: h.stress.avg <= 35 ? 'good' : h.stress.avg <= 60 ? 'warn' : 'bad' });
    if (ratio != null) limiters.push({ text: `Belastning ${ratio.toFixed(2)}`, cls: ratio <= 1.3 ? 'good' : ratio <= 1.5 ? 'warn' : 'bad' });
    if (projectedRatio && projectedRatio > ratio + 0.15) limiters.push({ text: `Veckoprognos ${projectedRatio.toFixed(2)}`, cls: 'warn' });
    if (!limiters.length) limiters.push({ text: 'Synka Garmin för begränsningar', cls: 'warn' });
    document.getElementById('cockpit-limiters').innerHTML = limiters
      .map(l => `<span class="cockpit-limiter ${l.cls}">${escapeHtml(l.text)}</span>`)
      .join('');

    const typeClass = s => s.type === 'race' ? 'bad' : s.type === 'run' ? 'warn' : s.type === 'lift' ? 'warn' : 'good';
    const typeLabel = s => ({ run:'KVALITET', easy:'LUGNT', lift:'STYRKA', race:'LOPP', rest:'VILA' }[s.type] || String(s.type || 'PLAN').toUpperCase());
    const dayFmt = d => d.toLocaleDateString('sv-SE', { weekday:'short', day:'numeric' });
    document.getElementById('cockpit-next-list').innerHTML = (next.length ? next : [{ date:new Date(), title:'Inget kommande pass', detail:'Lägg till eller synka din plan.', type:'rest', km:0 }])
      .map(s => `
        <div class="cockpit-row">
          <div class="cockpit-row-day">${escapeHtml(dayFmt(s.date))}</div>
          <div>
            <div class="cockpit-row-title">${escapeHtml(s.title)}</div>
            <div class="cockpit-row-sub">${escapeHtml(
              (s.type === 'lift' && s.strength_recommendation_text) || s.detail || (s.km ? `${s.km} km` : '')
            )}</div>
          </div>
          <span class="cockpit-tag ${typeClass(s)}">${escapeHtml(typeLabel(s))}</span>
        </div>
      `).join('');
  }

  function safeRenderTrainingCockpit() {
    try {
      renderTrainingCockpit();
      // Update appbar volume
      try {
        const stats = getWeekTrainingStats();
        const volEl = document.getElementById('appbar-volume');
        if (volEl) {
          volEl.innerHTML = stats.completedKm.toFixed(1) + '<span style="font-size:11px;color:var(--muted);font-weight:500"> km</span>';
        }
      } catch(e2) {}
    } catch(e) {
      const titleEl = document.getElementById('cockpit-decision-title');
      const copyEl = document.getElementById('cockpit-decision-copy');
      if (titleEl) titleEl.textContent = 'Översikten behöver ses över';
      if (copyEl) copyEl.textContent = e.message || 'Kunde inte rita upp översikten.';
      console.error('Cockpit render error:', e);
    }
  }

  async function loadHealth() {
    try {
      const res = await fetch('/api/health');
      const h = await res.json();
      if (h.error) return;
      currentHealthData = h;
      renderSleepPage(h);
      const fmtTime = s => { const h=Math.floor(s/3600), m=Math.floor((s%3600)/60); return h+'h '+m+'m'; };

      // ── CNS-SCORE (ersätter Recovery) ──
      // Formel: 0.40×HRV% + 0.30×sömnpoäng + 0.20×Garmin-beredskap + 0.10×(100-stress)
      // Baserat på Flatt & Esco (2016)
      const hrvPct = h.hrv?.component ?? h.hrv?.pct ?? 50;
      const sleepScoreR = h.sleep?.score ?? 50;
      const readiness = h.readiness?.score ?? 50;
      const stressVal = h.stress?.avg ?? 50;

      const cnsScore = Math.round(
        0.40 * Math.min(hrvPct, 100) +
        0.30 * sleepScoreR +
        0.20 * readiness +
        0.10 * (100 - Math.min(stressVal, 100))
      );

      const cnsCol   = cnsScore >= 70 ? 'var(--green)' : cnsScore >= 45 ? 'var(--amber)' : 'var(--red)';
      const cnsTitle = cnsScore >= 70 ? 'Redo för kvalitetspass' : cnsScore >= 45 ? 'Normalt pass ok' : 'Vila eller Z2 idag';
      const cnsDesc  = cnsScore >= 70
        ? 'CNS fullt återhämtat. HRV, sömn och beredskap är gröna – perfekt dag för intervaller eller tröskelpass.'
        : cnsScore >= 45
        ? 'Acceptabel CNS-status. Planerat pass går bra, men undvik maxansträngning.'
        : 'CNS visar tydliga tecken på otillräcklig återhämtning. Prioritera vila, lugn Z2, eller flytta fram kvalitetspasset.';

      const sleepMissing = !(h.sleep && h.sleep.totalSec);
      if (sleepMissing) {
        const cs = document.getElementById('cns-score');
        cs.textContent = '–'; cs.style.color = 'var(--muted2)';
        document.getElementById('cns-title').textContent = 'Ingen sömndata';
        document.getElementById('cns-desc').textContent  = 'I natt registrerades inte (klockan av eller slut på batteri), så CNS-poängen kan inte beräknas. Den kommer tillbaka automatiskt efter nästa synkade natt.';
        document.getElementById('cns-bar').style.width   = '0%';
        document.getElementById('hg-recovery').style.setProperty('--cns-color', 'var(--muted2)');
      } else {
        document.getElementById('cns-score').textContent = cnsScore;
        document.getElementById('cns-score').style.color = cnsCol;
        document.getElementById('cns-title').textContent = cnsTitle;
        document.getElementById('cns-desc').textContent  = cnsDesc;
        document.getElementById('cns-bar').style.width   = cnsScore + '%';
        document.getElementById('cns-bar').style.background = cnsCol;
        document.getElementById('hg-recovery').style.setProperty('--cns-color', cnsCol);
      }

      // HRV Traffic Light (Kiviniemi-metoden: ±5% från veckoavg)
      const hrvDiff = h.hrv?.lastNightAvg && h.hrv?.weeklyAvg
        ? ((h.hrv.lastNightAvg - h.hrv.weeklyAvg) / h.hrv.weeklyAvg) * 100
        : null;
      let hrvLight = h.hrv?.light || 'amber';
      let hrvLightText = getHrvVerdictText(h.hrv);
      if (!h.hrv?.light && hrvDiff !== null) {
        if (hrvDiff >= 5)       { hrvLight = 'green'; hrvLightText = `HRV +${hrvDiff.toFixed(0)}% – kvalitetspass går bra`; }
        else if (hrvDiff <= -5) { hrvLight = 'red';   hrvLightText = `HRV ${hrvDiff.toFixed(0)}% – vila eller Z2`; }
        else                    { hrvLight = 'amber'; hrvLightText = `HRV +/-${Math.abs(hrvDiff).toFixed(0)}% – normalt pass`; }
      }
      ['green','amber','red'].forEach(c => document.getElementById('hrv-dot-' + c).className = 'hrv-dot');
      document.getElementById('hrv-dot-' + hrvLight).classList.add('active-' + hrvLight);
      document.getElementById('hrv-light-label').textContent = hrvLightText;

      // CNS delmetriker
      if (h.hrv?.lastNightAvg != null) {
        const pct = h.hrv.component ?? h.hrv.pct ?? 0;
        const pctCol = getHrvColor(h.hrv);
        document.getElementById('cns-hrv-val').textContent = h.hrv.lastNightAvg + ' ms';
        document.getElementById('cns-hrv-val').style.color = pctCol;
        const statusText = h.hrv.status && h.hrv.status !== 'NONE' ? `${getHrvStatusLabel(h.hrv.status)} - ` : '';
        document.getElementById('cns-hrv-sub').textContent = `${statusText}${getHrvBaselineText(h.hrv)}`;
      } else {
        document.getElementById('cns-hrv-val').textContent = '–';
        document.getElementById('cns-hrv-val').style.color = 'var(--muted2)';
        document.getElementById('cns-hrv-sub').textContent = 'Ingen data i natt';
      }
      if (h.readiness?.score != null) {
        const rc = h.readiness.score >= 70 ? 'var(--green)' : h.readiness.score >= 40 ? 'var(--amber)' : 'var(--red)';
        document.getElementById('cns-readiness-val').textContent = h.readiness.score;
        document.getElementById('cns-readiness-val').style.color = rc;
        const lblMap = { VERY_HIGH:'Mycket hög', HIGH:'Hög', MODERATE:'Måttlig', LOW:'Låg', VERY_LOW:'Mycket låg' };
        document.getElementById('cns-readiness-sub').textContent = lblMap[h.readiness.level] || '/ 100';
      } else {
        document.getElementById('cns-readiness-val').textContent = '–';
        document.getElementById('cns-readiness-val').style.color = 'var(--muted2)';
        document.getElementById('cns-readiness-sub').textContent = 'Ingen data';
      }
      if (h.restingHR?.value != null) {
        const rhr = h.restingHR.value;
        const rc = rhr <= 50 ? 'var(--green)' : rhr <= 65 ? 'var(--amber)' : 'var(--red)';
        document.getElementById('cns-rhr-val').textContent = rhr;
        document.getElementById('cns-rhr-val').style.color = rc;
        document.getElementById('cns-rhr-sub').textContent = `snitt ${h.restingHR.sevenDayAvg || '-'} bpm`;
      } else {
        document.getElementById('cns-rhr-val').textContent = '–';
        document.getElementById('cns-rhr-val').style.color = 'var(--muted2)';
        document.getElementById('cns-rhr-sub').textContent = 'Ingen data';
      }

      // CNS-drivare
      const sleepDriver = h.sleep?.score;
      const sleepDriverColor = sleepDriver == null ? 'var(--muted2)' : sleepDriver >= 80 ? 'var(--green)' : sleepDriver >= 60 ? 'var(--amber)' : 'var(--red)';
      setCnsDriver('sleep', sleepDriver == null ? '-' : `${sleepDriver}/100`, sleepDriver || 0, sleepDriverColor);

      const stressDriver = h.stress?.avg;
      const stressRecovery = stressDriver == null ? null : Math.max(0, 100 - stressDriver);
      const stressDriverColor = stressDriver == null ? 'var(--muted2)' : stressDriver <= 35 ? 'var(--green)' : stressDriver <= 60 ? 'var(--amber)' : 'var(--red)';
      setCnsDriver('stress', stressDriver == null ? '-' : `${stressDriver}/100`, stressRecovery || 0, stressDriverColor);

      const bbDriver = h.bodyBattery?.current;
      const bbDriverColor = bbDriver == null ? 'var(--muted2)' : bbDriver >= 60 ? 'var(--green)' : bbDriver >= 30 ? 'var(--amber)' : 'var(--red)';
      setCnsDriver('bb', bbDriver == null ? '-' : `${bbDriver}/100`, bbDriver || 0, bbDriverColor);

      const driverSummary = document.getElementById('cns-driver-summary');
      if (driverSummary) {
        const weakSignals = [
          sleepDriver != null && sleepDriver < 60 ? 'sömn' : '',
          stressDriver != null && stressDriver > 60 ? 'stress' : '',
          bbDriver != null && bbDriver < 35 ? 'batteri' : ''
        ].filter(Boolean);
        driverSummary.textContent = weakSignals.length ? `Begränsas av ${weakSignals.join(', ')}` : 'Stabil helhetsbild';
      }

      // ── SÖMN ──
      let sleepScore = 50;
      if (h.sleep?.totalSec) {
        sleepScore = h.sleep.score || 50;
        const totalH = Math.floor(h.sleep.totalSec / 3600);
        const totalM = Math.floor((h.sleep.totalSec % 3600) / 60);

        // Sleep score
        const sc = h.sleep.score;
        const scCol = sc >= 80 ? 'var(--green)' : sc >= 60 ? 'var(--amber)' : 'var(--red)';
        const scStatus = sc >= 90 ? 'Utmärkt' : sc >= 80 ? 'Bra' : sc >= 60 ? 'Acceptabel' : 'Dålig';
        setMetric('hd-sscore-val', 'hd-sscore-status', sc || '-', '/ 100', scStatus, scCol);

        // Deep sleep
        const deep = h.sleep.deepPct;
        const deepCol = deep >= 15 ? 'var(--green)' : deep >= 10 ? 'var(--amber)' : 'var(--red)';
        const deepStatus = deep >= 20 ? 'Utmärkt' : deep >= 15 ? 'Normal' : deep >= 10 ? 'Något lågt' : 'För lite';
        setMetric('hd-deep-val', 'hd-deep-status', deep + '%', '%', `${deepStatus}  ·  mål 15–25%`, deepCol);
        document.getElementById('hd-deep-desc').textContent = fmtTime(h.sleep.deepSec) + '  ·  mål: 15–25% av sömnen';

        // REM
        const rem = h.sleep.remPct;
        const remCol = rem >= 20 ? 'var(--green)' : rem >= 15 ? 'var(--amber)' : 'var(--red)';
        const remStatus = rem >= 20 ? 'Utmärkt' : rem >= 15 ? 'Normal' : rem >= 10 ? 'Något lågt' : 'För lite';
        setMetric('hd-rem-val', 'hd-rem-status', rem + '%', '%', `${remStatus}  ·  mål 20–25%`, remCol);
        document.getElementById('hd-rem-desc').textContent = fmtTime(h.sleep.remSec) + '  ·  mål: 20–25% av sömnen';

        // Total sleep
        const totalCol = totalH >= 7 ? 'var(--green)' : totalH >= 6 ? 'var(--amber)' : 'var(--red)';
        const totalStatus = totalH >= 8 ? 'Utmärkt' : totalH >= 7 ? 'Bra' : totalH >= 6 ? 'Lite kort' : 'För lite';
        setMetric('hd-stotal-val', 'hd-stotal-status', `${totalH}h ${totalM}m`, '', totalStatus, totalCol);

        const sleepDesc = sleepScore >= 80
          ? `${totalH}h ${totalM}m sömn – god återhämtning under natten.`
          : sleepScore >= 60
          ? `${totalH}h ${totalM}m sömn – acceptabelt, men kan bli bättre.`
          : `${totalH}h ${totalM}m sömn – prioritera mer sömn i natt.`;
        setHG('hg-sleep-score', 'hg-sleep-bar', 'hg-sleep-badge', 'hg-sleep-desc', sleepScore, sleepDesc);

        // Sömnbrist - 7,5 h/natt mål = 52,5 h/vecka
        // Beräkna baserat på dagensömnstid × dagar hittills i veckan
        const SLEEP_TARGET_H = 7.5;
        const todayDowSleep = new Date().getDay() || 7; // 1=mån
        const daysIntoWeek = todayDowSleep;
        const targetSoFar = SLEEP_TARGET_H * daysIntoWeek * 3600;
        const actualSoFar = h.sleep.totalSec; // förenklat: ger i alla fall dagens underskott
        const dailyDebt = Math.max(0, SLEEP_TARGET_H * 3600 - h.sleep.totalSec);
        const dailyDebtH = Math.floor(dailyDebt / 3600);
        const dailyDebtM = Math.round((dailyDebt % 3600) / 60);
        const debtEl = document.getElementById('sleep-debt-val');
        if (dailyDebt < 900) {
          debtEl.textContent = 'Inget underskott';
          debtEl.style.color = 'var(--green)';
        } else {
          debtEl.textContent = `-${dailyDebtH > 0 ? dailyDebtH + 'h ' : ''}${dailyDebtM}m idag`;
          debtEl.style.color = dailyDebt > 3600 ? 'var(--red)' : 'var(--amber)';
        }

        // Sömnflaggor (djupsömn, REM, CNS-konsekvenser)
        const flags = [];
        if (deep < 10)  flags.push({ text: ' Låg djupsömn – hoppa över styrka', cls: 'bad' });
        else if (deep >= 15) flags.push({ text: ' Djupsömn ok', cls: 'ok' });
        else            flags.push({ text: '~ Djupsömn låg', cls: 'warn' });
        if (rem < 15)   flags.push({ text: ' Låg REM – undvik intervaller', cls: 'bad' });
        else if (rem >= 20) flags.push({ text: ' REM ok', cls: 'ok' });
        else            flags.push({ text: '~ REM något låg', cls: 'warn' });
        const flagRow = document.getElementById('sleep-flag-row');
        flagRow.innerHTML = flags.map(f => `<span class="sleep-flag ${f.cls}">${f.text}</span>`).join('');
        document.getElementById('hg-sleep-score').style.color = sleepScore >= 80 ? 'var(--purple)' : sleepScore >= 60 ? 'var(--amber)' : 'var(--red)';
        const badge = document.getElementById('hg-sleep-badge');
        badge.className = sleepScore >= 80 ? 'hg-status hs-purple' : sleepScore >= 60 ? 'hg-status hs-ok' : 'hg-status hs-low';
        badge.textContent = sleepScore >= 80 ? 'Bra' : sleepScore >= 60 ? 'Ok' : 'Dålig';
      } else {
        // Ingen sömndata (klockan synkade ingen natt) — skriv ut det istället för att låta korten ladda
        const muted = 'var(--muted2)';
        const sEl = document.getElementById('hg-sleep-score');
        sEl.textContent = '–'; sEl.style.color = muted;
        document.getElementById('hg-sleep-bar').style.width = '0%';
        const badge = document.getElementById('hg-sleep-badge');
        badge.className = 'hg-status'; badge.style.color = muted; badge.textContent = 'Ingen data';
        document.getElementById('hg-sleep-summary').textContent = '';
        document.getElementById('hg-sleep-desc').textContent =
          'Ingen sömn registrerad i natt — klockan synkade ingen natt (av eller slut på batteri). Sömnvärden återkommer automatiskt efter nästa registrerade natt.';
        ['hd-sscore', 'hd-deep', 'hd-rem', 'hd-stotal'].forEach(id => setMetric(id + '-val', id + '-status', '–', '', '', muted));
        const debtEl = document.getElementById('sleep-debt-val');
        debtEl.textContent = '–'; debtEl.style.color = muted;
        document.getElementById('sleep-flag-row').innerHTML = '<span class="sleep-flag warn">Ingen sömndata för i natt</span>';
      }

      // ── ENERGI & STRESS ──
      let energyScore = 50;
      if (h.bodyBattery?.current != null || h.stress?.avg != null) {
        const bb = h.bodyBattery?.current ?? 50;
        const stress = h.stress?.avg ?? 50;
        energyScore = Math.round(bb * 0.6 + (100 - stress) * 0.4);

        // Body Battery
        const bbCol = bb >= 60 ? 'var(--green)' : bb >= 30 ? 'var(--amber)' : 'var(--red)';
        const bbStatus = bb >= 75 ? 'Hög energi' : bb >= 50 ? 'Måttlig' : bb >= 25 ? 'Låg' : 'Tom – vila';
        setMetric('hd-bb-val', 'hd-bb-status', bb, '/ 100', bbStatus, bbCol);
        document.getElementById('hd-bb-desc').textContent = `Max idag: ${h.bodyBattery?.max || '-'}  ·  Min: ${h.bodyBattery?.drained ? bb : '-'}`;

        // Stress
        const stressCol = stress <= 25 ? 'var(--green)' : stress <= 50 ? 'var(--amber)' : 'var(--red)';
        const stressStatus = stress <= 25 ? 'Avslappnad' : stress <= 50 ? 'Låg stress' : stress <= 75 ? 'Måttlig' : 'Hög stress';
        setMetric('hd-stress-val', 'hd-stress-status', stress, '/ 100', stressStatus, stressCol);

        // SpO2
        if (h.spo2?.avg != null) {
          const spo2 = h.spo2.avg;
          const spo2Col = spo2 >= 97 ? 'var(--green)' : spo2 >= 95 ? 'var(--green)' : spo2 >= 90 ? 'var(--amber)' : 'var(--red)';
          const spo2Status = spo2 >= 97 ? 'Optimal' : spo2 >= 95 ? 'Normal' : spo2 >= 90 ? 'Något lågt – bevaka' : 'Kritiskt lågt!';
          setMetric('hd-spo2-val', 'hd-spo2-status', spo2 + '%', '%', spo2Status, spo2Col);
          document.getElementById('hd-spo2-desc').textContent = `Lägst: ${h.spo2.min ? h.spo2.min + '%' : '-'}  ·  normalt: 95–100%`;
        }

        // Andning
        if (h.respiration?.avg != null) {
          const resp = h.respiration.avg;
          const respCol = resp <= 16 ? 'var(--green)' : resp <= 20 ? 'var(--amber)' : 'var(--red)';
          const respStatus = resp <= 12 ? 'Atlet – utmärkt' : resp <= 16 ? 'Normal' : resp <= 20 ? 'Något förhöjd' : 'Förhöjd';
          setMetric('hd-resp-val', 'hd-resp-status', resp, '/min', respStatus, respCol);
          document.getElementById('hd-resp-desc').textContent = `Under sömn: ${h.respiration.sleepAvg || '-'}/min  ·  normalt: 12–20/min`;
        }

        const energyDesc = energyScore >= 70
          ? `Kroppsbatteri ${bb}/100 – kroppen har energi för ett bra pass.`
          : energyScore >= 45
          ? `Kroppsbatteri ${bb}/100 – måttlig energinivå; håll träningen lagom.`
          : `Kroppsbatteri ${bb}/100 – kroppen är trött. Prioritera vila och återhämtning.`;
        setHG('hg-energy-score', 'hg-energy-bar', 'hg-energy-badge', 'hg-energy-desc', energyScore, energyDesc);
      }

      const healthStressAvg = h.stress?.avg;
      const healthStressInfo = stressMeta(healthStressAvg);
      const healthStressScoreEl = document.getElementById('hg-stress-score');
      if (healthStressScoreEl) {
        healthStressScoreEl.textContent = healthStressAvg ?? '-';
        healthStressScoreEl.style.color = healthStressInfo.color;
      }
      const healthStressBadge = document.getElementById('hg-stress-badge');
      if (healthStressBadge) {
        healthStressBadge.textContent = healthStressInfo.badge;
        healthStressBadge.className = healthStressInfo.color === 'var(--red)' ? 'hg-status hs-low' : healthStressInfo.color === 'var(--amber)' ? 'hg-status hs-ok' : 'hg-status hs-great';
      }
      const healthStressBar = document.getElementById('hg-stress-bar');
      if (healthStressBar) healthStressBar.style.width = Math.max(0, Math.min(100, healthStressInfo.pct)) + '%';
      setMetric('hd-stress-val', 'hd-stress-status', healthStressAvg ?? '-', '/ 100', healthStressInfo.status, healthStressInfo.color);
      const healthStressDesc = document.getElementById('hg-stress-desc');
      if (healthStressDesc) {
        healthStressDesc.textContent = healthStressAvg == null
          ? 'Ingen stressdata från Garmin ännu.'
          : healthStressAvg <= 25 ? 'Låg fysiologisk belastning idag. Kroppen ser lugn ut.'
          : healthStressAvg <= 50 ? 'Stressnivån är låg till normal. Bra läge för planerad träning.'
          : healthStressAvg <= 75 ? 'Måttlig stress idag. Var uppmärksam på återhämtning och passintensitet.'
          : 'Hög stress idag. Prioritera återhämtning och undvik extra belastning.';
      }
      const healthStressSummary = document.getElementById('hg-stress-summary');
      if (healthStressSummary) healthStressSummary.textContent = h.stress?.max != null ? `Max ${h.stress.max}` : '';
      try {
        const sr = await fetch('/api/health/stress-history?days=30');
        const sd = await sr.json();
        const histAvg = sd.avg;
        const delta = healthStressAvg != null && histAvg != null ? Math.round((healthStressAvg - histAvg) * 10) / 10 : null;
        const deltaColor = delta == null ? 'var(--muted2)' : delta <= -5 ? 'var(--green)' : delta <= 5 ? 'var(--amber)' : 'var(--red)';
        const histVal = document.getElementById('hd-stress-hist-val');
        if (histVal) histVal.textContent = histAvg ?? '-';
        const histStatus = document.getElementById('hd-stress-hist-status');
        if (histStatus) histStatus.textContent = histAvg == null ? '' : '/ 100';
        const deltaVal = document.getElementById('hd-stress-delta-val');
        if (deltaVal) {
          deltaVal.textContent = delta == null ? '-' : (delta > 0 ? '+' : '') + delta;
          deltaVal.style.color = deltaColor;
        }
        const deltaStatus = document.getElementById('hd-stress-delta-status');
        if (deltaStatus) deltaStatus.textContent = delta == null ? '' : delta <= -5 ? 'lägre än vanligt' : delta <= 5 ? 'nära normalt' : 'högre än vanligt';
        const deltaDesc = document.getElementById('hd-stress-delta-desc');
        if (deltaDesc && delta != null) deltaDesc.textContent = `Mot 30-dagars snitt ${histAvg}`;
        drawStressHistory(sd.values || [], healthStressAvg, histAvg);
      } catch(e) {
        drawStressHistory([], healthStressAvg, null);
      }

      const d = new Date();
      document.getElementById('h-date-label').textContent = d.toLocaleDateString('sv-SE', {day:'numeric',month:'long',year:'numeric'}) + '  ·  Garmin live-data';

      // Snapshot på hemsidan
      const snapSets = [
        { valId:'snap-readiness-val', subId:'snap-readiness-sub', barId:'snap-readiness-bar',
          val: h.readiness?.score, sub: (()=>{ const m={VERY_HIGH:'Mycket hög',HIGH:'Hög',MODERATE:'Måttlig',LOW:'Låg',VERY_LOW:'Mycket låg'}; return m[h.readiness?.level]||''; })(),
          col: h.readiness?.score >= 70 ? 'var(--green)' : h.readiness?.score >= 40 ? 'var(--amber)' : 'var(--red)', pct: h.readiness?.score },
        { valId:'snap-sleep-val', subId:'snap-sleep-sub', barId:'snap-sleep-bar',
          val: h.sleep?.score, sub: h.sleep?.totalSec ? fmtTime(h.sleep.totalSec) : '',
          col: h.sleep?.score >= 80 ? 'var(--green)' : h.sleep?.score >= 60 ? 'var(--amber)' : 'var(--red)', pct: h.sleep?.score },
        { valId:'snap-rhr-val', subId:'snap-rhr-sub', barId:'snap-rhr-bar',
          val: h.restingHR?.value, sub: 'Snitt 7d: ' + (h.restingHR?.sevenDayAvg || '-') + ' bpm',
          col: h.restingHR?.value <= (h.restingHR?.sevenDayAvg || h.restingHR?.value) + 2 ? 'var(--green)' : h.restingHR?.value <= (h.restingHR?.sevenDayAvg || h.restingHR?.value) + 6 ? 'var(--amber)' : 'var(--red)',
          pct: Math.max(0, Math.min(100, 100 - ((h.restingHR?.value || 60) - 35) / 45 * 100)) },
        { valId:'snap-hrv-val', subId:'snap-hrv-sub', barId:'snap-hrv-bar',
          val: h.hrv?.lastNightAvg, sub: h.hrv?.status && h.hrv.status !== 'NONE' ? `${getHrvStatusLabel(h.hrv.status)} - ${getHrvBaselineText(h.hrv)}` : getHrvBaselineText(h.hrv),
          col: getHrvColor(h.hrv), pct: Math.min(h.hrv?.component ?? h.hrv?.pct ?? 0, 100) },
      ];
      snapSets.forEach(s => {
        if (s.val == null) return;
        const v = document.getElementById(s.valId); if (v) { v.textContent = s.val; v.style.color = s.col; }
        const b = document.getElementById(s.subId); if (b) b.textContent = s.sub;
        const r = document.getElementById(s.barId);  if (r) { r.style.width = (s.pct||0) + '%'; r.style.background = s.col; }
      });

      // CNS-poäng i hem-hero (ersätter Garmin-beredskap)
      const cnsHero = computeCnsScore(h);
      const ringVal = document.getElementById('readiness-ring-val');
      const ringProg = document.getElementById('readiness-ring-prog');
      if (ringVal && ringProg && cnsHero != null) {
        const col = cnsHero >= 70 ? 'var(--accent)' : cnsHero >= 45 ? 'var(--amber)' : 'var(--red)';
        const circ = 239;
        ringVal.textContent = cnsHero;
        ringVal.style.color = col;
        // Keep gradient stroke — only update dashoffset
        ringProg.style.strokeDashoffset = circ * (1 - Math.max(0, Math.min(100, cnsHero)) / 100);
        const sub = document.getElementById('snap-readiness-sub');
        if (sub) {
          sub.textContent = cnsHero >= 70 ? 'Redo för kvalitetspass' : cnsHero >= 45 ? 'Normalt pass ok' : 'Vila eller Z2 idag';
          sub.style.color = col;
        }
      }

      safeRenderTrainingCockpit();

      // Update appbar with live data
      updateAppbar(h);

      // Draw sparklines from real 7-day history (needs >=2 days of data)
      try {
        const sp = await (await fetch('/api/health/spark')).json();
        if (sp.sleep?.length >= 2) drawSparkline(document.getElementById('spark-sleep'), sp.sleep, 'var(--green)');
        if (sp.rhr?.length >= 2)   drawSparkline(document.getElementById('spark-rhr'),   sp.rhr,   'var(--green)');
        if (sp.hrv?.length >= 2)   drawSparkline(document.getElementById('spark-hrv'),   sp.hrv,   'var(--accent)');
      } catch (e) { /* sparklines are optional decoration */ }

    } catch(e) { console.error('Health error:', e); }
  }

  function setButtons(ids, text, color, disabled) {
    ids.forEach(id => {
      const btn = document.getElementById(id);
      if (!btn) return;
      btn.textContent = text;
      btn.style.color = color || '';
      btn.disabled = disabled;
    });
  }

  async function refreshData() {
    const refreshIds = ['refresh-btn', 'mobile-refresh-btn'];
    setButtons(refreshIds, 'Uppdaterar…', 'var(--amber)', true);
    try {
      await fetch('/api/sync', { method: 'POST' });
      await Promise.all([loadHealth(), loadSleepCoach(), loadRecentActivities(), loadTrainingLoad(), loadTrainingReview(true), loadInsights(), loadPlan()]);
      const res = await fetch('/api/refresh', { method: 'POST' });
      const data = await res.json();
      if (data.error) throw new Error(data.error);
      if (data.todayRecommendation) {
        const titles = { easy:'Lugnt pass idag', quality:'Kvalitetspass idag', rest:'Vilodag idag' };
        const badges = { easy:'badge-green', quality:'badge-amber', rest:'badge-red' };
        const badgeText = { easy:'LUGNT', quality:'KVALITET', rest:'VILA' };
        const todayTitle = document.getElementById('today-title');
        const todayBody = document.getElementById('today-body');
        const badge = document.getElementById('today-badge');
        if (todayTitle) todayTitle.textContent = titles[data.todayType] || 'Rekommendation';
        if (todayBody) todayBody.textContent = data.todayRecommendation;
        if (badge) {
          badge.className = 'today-badge ' + (badges[data.todayType] || 'badge-amber');
          badge.textContent = badgeText[data.todayType] || 'IDAG';
        }
      }
      setButtons(refreshIds, 'Uppdaterat', 'var(--green)', true);
      setTimeout(() => setButtons(refreshIds, 'Uppdatera data', '', false), 3000);
    } catch(e) {
      setButtons(refreshIds, e.message || 'Uppdatering misslyckades', 'var(--red)', true);
      setTimeout(() => setButtons(refreshIds, 'Uppdatera data', '', false), 4000);
    }
  }

  // Noteringar - laddas från DB och används i varje AI-anrop
  let userNotes = [];
  let userJournal = [];

  function baseCtx() {
    let goalLines;
    if (userGoal) {
      goalLines = `GOAL: ${userGoal.goal_title}`;
      if (userGoal.goal_deadline) goalLines += `  -  Deadline: ${userGoal.goal_deadline}`;
      if (userGoal.current_best) goalLines += `  -  Current best: ${userGoal.current_best}`;
      if (userGoal.secondary_goal) goalLines += `\nSECONDARY GOAL: ${userGoal.secondary_goal}`;
    } else {
      goalLines = 'GOAL: Inget uttalat mål ännu - coacha för allmän form, hälsa och kontinuitet.';
    }
    return `Du är en personlig träningscoach. Svara alltid på svenska.

${goalLines}

STRENGTH PRINCIPLE: progressive overload - strength training supports running, reduces injury risk.

HEALTH DATA (current):
(Updated dynamically below with current values and CNS score)`;
  }

  function buildCTX() {
    let ctx = baseCtx();

    // Lägg in arbetsschema för kommande 7 dagar
    if (gcalEvents.length > 0) {
      const today = new Date();
      const in7 = new Date(today); in7.setDate(today.getDate() + 7);
      const upcoming = gcalEvents.filter(ev => {
        const d = new Date(ev.start);
        return d >= today && d <= in7;
      });
      if (upcoming.length > 0) {
        ctx += '\n\nARBETS- OCH AKTIVITETSSCHEMA (kommande 7 dagar från Google Calendar):';
        const earlyDays = [];
        upcoming.forEach(ev => {
          const timeStr = ev.allDay ? 'Heldag' : fmtEventTime(ev.start) + '-' + fmtEventTime(ev.end);
          const dayName = new Date(ev.start).toLocaleDateString('sv-SE', { weekday:'long', day:'numeric', month:'short' });
          ctx += `\n- ${dayName}: ${ev.title} (${timeStr})${ev.desc ? ' - ' + ev.desc : ''}`;
          if (!ev.allDay) {
            const hour = new Date(ev.start).getHours();
            if (hour < 7) earlyDays.push(dayName);
          }
        });
        ctx += '\nAlways adapt training recommendations to the schedule, for example by moving hard sessions to free days.';
        if (earlyDays.length > 0) {
          ctx += `\nEARLY WORK WARNING: The following days have events starting before 07:00 - this likely means shortened sleep and reduced recovery: ${earlyDays.join(', ')}. Avoid quality sessions (intervals, threshold, race) on these days and the day after. Prioritize rest or easy sessions (Z1-Z2).`;
        }
      }
    }

    // CNS-score och HRV traffic light - dynamisk hälsostatus
    const cnsEl = document.getElementById('cns-score');
    const cnsVal = cnsEl ? parseInt(cnsEl.textContent) : null;
    if (cnsVal && !isNaN(cnsVal)) {
      const cnsTitle = document.getElementById('cns-title')?.textContent || '';
      const hrvLabel = document.getElementById('hrv-light-label')?.textContent || '';
      const sleepDebt = document.getElementById('sleep-debt-val')?.textContent || '';
      const flags = [...(document.getElementById('sleep-flag-row')?.querySelectorAll('.sleep-flag') || [])]
        .map(f => f.textContent).join('  -  ');
      ctx += `\n\nCNS SCORE (daily readiness analysis): ${cnsVal}/100 - ${cnsTitle}`;
      ctx += `\nHRV-SIGNAL: ${hrvLabel}`;
      ctx += `\nSLEEP DEFICIT TODAY: ${sleepDebt}`;
      if (flags) ctx += `\nSLEEP FLAGS: ${flags}`;
      ctx += `\nSESSION RULE: CNS >=70 -> quality session ok  -  CNS 45-69 -> normal/easy session  -  CNS <45 -> rest or Z2 obligatoriskt`;
    }

    // Sparade notes
    if (userNotes.length > 0) {
  const catLabels = { body:'Body & injuries', nutrition:'Nutrition & recovery', goals:'Goals & focus', gear:'Gear', kropp:'Body & injuries', kost:'Nutrition & recovery', ['m\u00e5l']:'Goals & focus', utrustning:'Gear', general:'Other' };
      ctx += '\n\nSAVED USER NOTES (always take these into account):';
      userNotes.forEach(n => {
        const cat = catLabels[n.category] || n.category;
        ctx += `\n- [${cat}] ${n.text}`;
      });
    }
    if (userJournal.length > 0) {
      ctx += '\n\nRECENT JOURNAL ENTRIES (how the days felt; use gently as context):';
      userJournal.slice(0, 5).forEach(j => {
        const meta = [j.mood, j.energy ? `energy ${j.energy}/5` : ''].filter(Boolean).join(', ');
        ctx += `\n- ${j.date}${meta ? ` (${meta})` : ''}: ${j.text}`;
      });
    }
    // Volymsanalys för innevarande vecka
    const now = new Date();
    const isoWeek = (() => {
      const d = new Date(Date.UTC(now.getFullYear(), now.getMonth(), now.getDate()));
      const day = d.getUTCDay() || 7;
      d.setUTCDate(d.getUTCDate() + 4 - day);
      const yearStart = new Date(Date.UTC(d.getUTCFullYear(), 0, 1));
      return Math.ceil((((d - yearStart) / 86400000) + 1) / 7);
    })();
    const weekMonday = new Date(now);
    weekMonday.setDate(now.getDate() - ((now.getDay() || 7) - 1));
    weekMonday.setHours(0,0,0,0);
    const weekSunday = new Date(weekMonday);
    weekSunday.setDate(weekMonday.getDate() + 7);

    const plannedKmWeek = PLAN_SESSIONS
      .filter(s => s.week === isoWeek)
      .reduce((sum, s) => sum + (s.km || 0), 0);

    const completedKmWeek = recentActivities
      .filter(a => {
        const d = new Date(a.startTimeLocal || a.beginTimestamp);
        return d >= weekMonday && d < weekSunday &&
          ['running','track_running','treadmill_running','trail_running'].includes(a.activityType?.typeKey);
      })
      .reduce((sum, a) => sum + ((a.distance || 0) / 1000), 0);

    const remainingKm = Math.max(0, plannedKmWeek - completedKmWeek);
    const weekCap = Math.round(plannedKmWeek * 1.1); // max 10% över plan

    if (PLAN_SESSIONS.length === 0) {
      ctx += `\n\nWEEKLY VOLUME W${isoWeek}: No training plan set up  -  Completed ${completedKmWeek.toFixed(1)} km this week. Base advice on recovery, recent load and the athlete's goal.`;
    } else {
      ctx += `\n\nWEEKLY VOLUME W${isoWeek}: Planned ${plannedKmWeek} km  -  Completed ${completedKmWeek.toFixed(1)} km  -  Remaining by plan ${remainingKm.toFixed(1)} km  -  Weekly cap ${weekCap} km. If a missed session is suggested for rescheduling, ensure total weekly distance does not exceed ${weekCap} km - otherwise recommend rest or move the session to next week.`;
    }

    // Training load (ACWR) - estimera load för planerade kvarvarande pass
    if (trainingLoadData && trainingLoadData.acute != null) {
      // Load/km-schabloner baserade på historiska Garmin-värden
      // easy (Z2): ~7/km, run (intervall/tröskel): ~22/km, race: ~25/km, lift: 40 fast
      const loadPerKm = { easy: 7, run: 22, race: 25, lift: 0 };
      const loadFlat  = { lift: 40 };

      const todayDow = (now.getDay() + 6) % 7; // 0=mån
      const remainingSessions = PLAN_SESSIONS.filter(s =>
        s.week === isoWeek && s.dow > todayDow
      );

      const estimatedRemainingLoad = remainingSessions.reduce((sum, s) => {
        if (s.type === 'lift') return sum + 40;
        return sum + (s.km || 0) * (loadPerKm[s.type] || 7);
      }, 0);

      // Completed load this week från Garmin
      const completedLoadWeek = recentActivities
        .filter(a => {
          const d = new Date(a.startTimeLocal || a.beginTimestamp);
          return d >= weekMonday && d < weekSunday;
        })
        .reduce((sum, a) => sum + (a.activityTrainingLoad || 0), 0);

      const projectedAcute  = trainingLoadData.acute + estimatedRemainingLoad;
      const chronic         = trainingLoadData.chronic || 1;
      const projectedRatio  = (projectedAcute / chronic).toFixed(2);
      const projectedSafe   = projectedRatio <= 1.3;

      const statusMap = {
        RECOVERY_2: 'Återhämtning', MAINTAINING: 'Bibehåller',
        IMPROVING: 'Förbättras', PRODUCTIVE: 'Produktiv', PEAKING: 'Toppform',
        OVERREACHING: 'Överbelastning', UNPRODUCTIVE: 'Improduktiv'
      };
      const statusLabel = statusMap[trainingLoadData.statusPhrase] || trainingLoadData.statusPhrase || '-';

      const feedbackMap = {
        AEROBIC_LOW_SHORTAGE: 'för lite lågintensiv aerob träning',
        AEROBIC_HIGH_SHORTAGE: 'för lite högintensiv aerob träning',
        ANAEROBIC_SHORTAGE: 'för lite anaerob träning',
        OPTIMAL: 'optimal belastningsbalans'
      };
      const feedbackLabel = feedbackMap[trainingLoadData.loadBalanceFeedback] || trainingLoadData.loadBalanceFeedback || '-';

      ctx += `\n\nTRAINING LOAD (ACWR model):`;
      ctx += `\n- Acute load (7 days): ${trainingLoadData.acute}  -  Chronic load (28 days): ${trainingLoadData.chronic}`;
      ctx += `\n- Current ACWR ratio: ${trainingLoadData.ratio} (${trainingLoadData.acwrStatus})  -  Training status: ${statusLabel}`;
      ctx += `\n- Completed load this week: ${Math.round(completedLoadWeek)}  -  Estimated load for remaining planned sessions: ${Math.round(estimatedRemainingLoad)}`;
      ctx += `\n- Projected ACWR if all remaining planned sessions are completed: ${projectedRatio} -> ${projectedSafe ? 'inside safe zone (<=1.3)' : 'could exceed safe zone (>1.3)'}`;
      ctx += `\n- Load balance: ${feedbackLabel}`;
      ctx += `\nRULE: Base today's risk on CURRENT ACWR, not the projection. Optimal current ACWR is 0.8-1.3; if current ACWR >1.3, avoid adding extra volume. Use projected ACWR only to suggest trimming later optional sessions if the full remaining plan would push load high. Estimated load/km: Z2=7, interval/threshold=22, race=25, strength=40 fixed.`;
    }

    ctx += '\n\nSvara alltid på svenska. Var konkret och personlig. Väg alltid in BÅDA målen i svaret. Max 3-4 meningar.';
    return ctx;
  }

  function setupJournalDefaults() {
    const dateInput = document.getElementById('journal-date');
    const energyInput = document.getElementById('journal-energy');
    const textInput = document.getElementById('journal-text');
    if (dateInput && !dateInput.value) dateInput.value = todayLocalDate();
    if (energyInput) {
      const syncEnergy = () => {
        const label = document.getElementById('journal-energy-label');
        if (label) label.textContent = energyInput.value + '/5';
      };
      energyInput.oninput = syncEnergy;
      syncEnergy();
    }
    if (textInput) {
      textInput.oninput = () => {
        const count = document.getElementById('journal-char-count');
        const status = document.getElementById('journal-save-status');
        if (count) count.textContent = textInput.value.length + ' tecken';
        if (status) {
          status.textContent = 'OSPARAT';
          status.className = 'today-badge badge-amber';
        }
      };
    }
    if (dateInput && !dateInput.dataset.bound) {
      dateInput.dataset.bound = '1';
      dateInput.addEventListener('change', fillJournalEditorForDate);
    }
  }

  async function loadJournal() {
    setupJournalDefaults();
    try {
      const res = await fetch('/api/journal?limit=45');
      const data = await res.json();
      userJournal = data.entries || [];
      renderJournalList();
      fillJournalEditorForDate();
    } catch(e) {
      console.error('Journal error:', e);
      const list = document.getElementById('journal-list');
      if (list) list.innerHTML = '<div class="journal-empty">Kunde inte ladda dagboken.</div>';
    }
  }

  function fillJournalEditorForDate() {
    const dateInput = document.getElementById('journal-date');
    const moodInput = document.getElementById('journal-mood');
    const energyInput = document.getElementById('journal-energy');
    const textInput = document.getElementById('journal-text');
    if (!dateInput || !textInput) return;
    const entry = userJournal.find(j => j.date === dateInput.value);
    if (moodInput) moodInput.value = entry?.mood || '';
    if (energyInput) {
      energyInput.value = entry?.energy || 3;
      document.getElementById('journal-energy-label').textContent = energyInput.value + '/5';
    }
    textInput.value = entry?.text || '';
    document.getElementById('journal-char-count').textContent = textInput.value.length + ' tecken';
    const status = document.getElementById('journal-save-status');
    if (status) {
      status.textContent = entry ? 'SPARAD' : 'NY';
      status.className = 'today-badge ' + (entry ? 'badge-green' : 'badge-amber');
    }
  }

  function renderJournalList() {
    const list = document.getElementById('journal-list');
    const count = document.getElementById('journal-count');
    if (!list) return;
    if (count) count.textContent = userJournal.length + (userJournal.length === 1 ? ' dag' : ' dagar');
    if (!userJournal.length) {
      list.innerHTML = '<div class="journal-empty">Inga dagboksinlägg än. Börja med dagens incheckning.</div>';
      return;
    }
    list.innerHTML = userJournal.map(j => {
      const d = new Date(j.date + 'T12:00:00');
      const dateLabel = d.toLocaleDateString('sv-SE', { weekday:'short', day:'numeric', month:'short' });
      const meta = [j.mood, j.energy ? `Energi ${j.energy}/5` : ''].filter(Boolean);
      return `<article class="journal-entry" data-action="edit-journal" data-date="${escapeHtml(j.date)}">
        <div class="journal-entry-top">
          <span class="journal-entry-date">${escapeHtml(dateLabel)}</span>
          ${meta.map(m => `<span class="journal-pill">${escapeHtml(m)}</span>`).join('')}
          <button class="journal-delete" data-action="delete-journal" data-id="${Number(j.id)}">x</button>
        </div>
        <div class="journal-entry-text">${escapeHtml(j.text)}</div>
      </article>`;
    }).join('');
  }

  function editJournalDate(date) {
    const dateInput = document.getElementById('journal-date');
    if (!dateInput) return;
    dateInput.value = date;
    fillJournalEditorForDate();
    document.getElementById('journal-text')?.focus();
  }

  async function saveJournalEntry() {
    setupJournalDefaults();
    const dateInput = document.getElementById('journal-date');
    const moodInput = document.getElementById('journal-mood');
    const energyInput = document.getElementById('journal-energy');
    const textInput = document.getElementById('journal-text');
    const btn = document.getElementById('journal-save-btn');
    const status = document.getElementById('journal-save-status');
    const text = textInput.value.trim();
    if (!text) { textInput.focus(); return; }
    if (btn) { btn.disabled = true; btn.textContent = 'Sparar…'; }
    try {
      await fetch('/api/journal', {
        method: 'POST',
        headers: {'Content-Type':'application/json'},
        body: JSON.stringify({
          date: dateInput.value || todayLocalDate(),
          mood: moodInput.value,
          energy: energyInput.value,
          text
        })
      });
      if (status) {
        status.textContent = 'SPARAD';
        status.className = 'today-badge badge-green';
      }
      await loadJournal();
    } finally {
      if (btn) { btn.disabled = false; btn.textContent = 'Spara dagbok'; }
    }
  }

  async function deleteJournalEntry(event, id) {
    event.stopPropagation();
    await fetch('/api/journal/' + id, { method: 'DELETE' });
    await loadJournal();
  }

  loadJournal();

  async function loadNotes() {
    try {
      const res = await fetch('/api/notes');
      const data = await res.json();
      userNotes = data.notes || [];
      renderNotes();
    } catch(e) { console.error('Notes error:', e); }
  }

  const catEmoji = { body:'', nutrition:'', goals:'', gear:'', kropp:'', kost:'', ['m\u00e5l']:'', utrustning:'', general:'' };
  const catLabel = { body:'Kropp & skador', nutrition:'Kost & återhämtning', goals:'Mål & fokus', gear:'Utrustning', kropp:'Kropp & skador', kost:'Kost & återhämtning', ['m\u00e5l']:'Mål & fokus', utrustning:'Utrustning', general:'Övrigt' };
  const catColor = { body:'var(--red)', nutrition:'var(--green)', goals:'var(--blue)', gear:'var(--amber)', kropp:'var(--red)', kost:'var(--green)', ['m\u00e5l']:'var(--blue)', utrustning:'var(--amber)', general:'var(--muted2)' };

  function renderNotes() {
    const list = document.getElementById('notes-list');
    const count = document.getElementById('notes-count');
    if (!list) return;
    count.textContent = userNotes.length + (userNotes.length === 1 ? ' anteckning' : ' anteckningar');
    if (!userNotes.length) {
      list.innerHTML = '<div style="color:var(--muted);font-size:12px;font-family:\'IBM Plex Mono\',monospace;padding:4px 0;">Inga anteckningar än. Lägg till sådant coachen bör veta.</div>';
      return;
    }
    list.innerHTML = userNotes.map(n => {
      const emoji = catEmoji[n.category] || '';
      const col   = catColor[n.category]  || 'var(--muted2)';
      const label = catLabel[n.category]  || n.category;
      const date  = new Date(n.created_at * 1000).toLocaleDateString('sv-SE', {day:'numeric', month:'short'});
      return `<div style="background:var(--bg3);border:1px solid var(--border);border-radius:10px;padding:10px 12px;position:relative;">
        <div style="display:flex;align-items:center;gap:6px;margin-bottom:5px;">
          <span style="font-size:11px;color:${col};font-family:'IBM Plex Mono',monospace;font-weight:600;">${escapeHtml(emoji)} ${escapeHtml(label)}</span>
          <span style="font-size:10px;color:var(--muted);margin-left:auto;font-family:'IBM Plex Mono',monospace;">${escapeHtml(date)}</span>
          <button class="note-delete" data-action="delete-note" data-id="${Number(n.id)}" style="background:none;border:none;color:var(--muted);cursor:pointer;font-size:15px;line-height:1;padding:0 2px;transition:color 0.15s;">x</button>
        </div>
        <div style="font-size:13px;color:var(--muted3);line-height:1.5;">${escapeHtml(n.text)}</div>
      </div>`;
    }).join('');
  }

  async function saveNote() {
    const input = document.getElementById('note-input');
    const category = document.getElementById('note-category').value;
    const text = input.value.trim();
    if (!text) { input.focus(); return; }
    await fetch('/api/notes', {
      method: 'POST',
      headers: {'Content-Type':'application/json'},
      body: JSON.stringify({ text, category })
    });
    input.value = '';
    await loadNotes();
  }

  async function deleteNote(id) {
    await fetch('/api/notes/' + id, { method: 'DELETE' });
    await loadNotes();
  }

  // Ladda notes direkt vid start
  loadNotes();

  // Garmin-aktiviteter cached globalt för coachens volyms- och loadberäkning
  let recentActivities = [];
  async function loadRecentActivities() {
    try {
      const res = await fetch('/api/activities?days=120&refresh=1&calendar=1');
      const data = await res.json();
      recentActivities = data.activities || [];
      safeRenderTrainingCockpit();
      renderTodaySession();
      buildCalendar();
    } catch(e) {}
  }
  loadRecentActivities();

  // Training load (ACWR) cached globalt
  let trainingLoadData = null;
  async function loadTrainingLoad() {
    try {
      const res = await fetch('/api/training-load');
      const data = await res.json();
      if (!data.error) trainingLoadData = data;
      safeRenderTrainingCockpit();
    } catch(e) {}
  }
  loadTrainingLoad();

  // AI-analys av senaste passen (planerat vs faktiskt gjort)
  async function loadTrainingReview(force) {
    try {
      const res = await fetch('/api/training-review' + (force ? '?force=1' : ''));
      const d = await res.json();
      if (d.error) return;
      if (d.headline) document.getElementById('review-headline').textContent = d.headline;
      if (d.body)     document.getElementById('review-body').textContent = d.body;
      const map = { done:['badge-green','DONE'], pending:['badge-amber','TO DO'], missed:['badge-red','MISSED'], rest:['badge-green','REST'], other:['badge-amber','OTHER'] };
      const m = map[d.status] || ['badge-amber','TODAY'];
      const badge = document.getElementById('review-badge');
      badge.className = 'today-badge ' + m[0];
      badge.textContent = m[1];
    } catch(e) {}
  }
  loadTrainingReview();

  let acLoopEnabled = false;

  function renderAcLoopControl(status) {
    const label = document.getElementById('ac-loop-status');
    const btn = document.getElementById('ac-loop-toggle');
    if (!label || !btn) return;

    if (!status || status.available === false) {
      acLoopEnabled = false;
      label.textContent = 'Automatisk styrning: otillgänglig';
      btn.textContent = 'Av';
      btn.className = 'ac-loop-btn is-off';
      btn.disabled = true;
      return;
    }

    acLoopEnabled = !!status.enabled;
    label.textContent = 'Automatisk styrning: ' + (acLoopEnabled ? 'på' : 'av') + (status.running === false ? ' – loggningsloop NERE' : '');
    btn.textContent = acLoopEnabled ? 'På' : 'Av';
    btn.className = 'ac-loop-btn ' + (acLoopEnabled ? 'is-on' : 'is-off');
    btn.disabled = false;
  }

  async function loadAcLoopStatus() {
    try {
      const res = await fetch('/api/ac/loop');
      const status = await res.json();
      renderAcLoopControl(status);
    } catch(e) {
      renderAcLoopControl({ available: false });
    }
  }

  async function toggleAcLoop() {
    const btn = document.getElementById('ac-loop-toggle');
    const label = document.getElementById('ac-loop-status');
    if (!btn) return;
    const nextEnabled = !acLoopEnabled;
    btn.disabled = true;
    btn.textContent = nextEnabled ? 'På…' : 'Av…';
    if (label) label.textContent = 'Automatisk styrning: uppdaterar…';

    try {
      const res = await fetch('/api/ac/loop', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ enabled: nextEnabled })
      });
      const status = await res.json();
      if (!res.ok || status.ok === false) throw new Error(status.error || 'Kunde inte uppdatera AC-styrningen');
      renderAcLoopControl(status);
      loadAcStatus();
    } catch(e) {
      if (label) label.textContent = 'Automatisk styrning: ' + e.message;
      btn.textContent = acLoopEnabled ? 'På' : 'Av';
      btn.disabled = false;
    }
  }

  async function loadAcBedtime() {
    const inp = document.getElementById('ac-bedtime-input');
    const body = document.getElementById('ac-bedtime-body');
    const badge = document.getElementById('ac-bedtime-badge');
    if (!inp || !body || !badge) return;
    try {
      const res = await fetch('/api/ac/bedtime');
      const d = await res.json();
      if (!res.ok || d.available === false) throw new Error(d.error || 'otillgänglig');
      inp.value = d.bedtime || '';
      if (d.bedtime) {
        badge.className = 'today-badge badge-blue';
        badge.textContent = 'MANUELL';
        body.textContent = 'AC:n planerar för att rummet ska vara vid måltemperatur till ' + d.bedtime + '.';
      } else {
        badge.className = 'today-badge badge-green';
        badge.textContent = 'AUTO';
        body.textContent = 'Ingen manuell läggtid satt. Förkylningen använder den uträknade sömntiden.';
      }
    } catch(e) {
      badge.className = 'today-badge badge-red';
      badge.textContent = 'NERE';
      body.textContent = 'Kunde inte läsa läggtidsstyrningen.';
    }
  }

  async function saveAcBedtime() {
    const inp = document.getElementById('ac-bedtime-input');
    const status = document.getElementById('ac-bedtime-status');
    const btn = document.getElementById('ac-bedtime-save');
    if (!inp || !status || !btn) return;
    const bedtime = inp.value;
    if (!bedtime) {
      status.textContent = 'Välj en tid eller tryck Auto.';
      status.style.color = 'var(--amber)';
      return;
    }
    btn.disabled = true;
    status.textContent = 'Sparar...';
    status.style.color = 'var(--muted)';
    try {
      const res = await fetch('/api/ac/bedtime', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ bedtime })
      });
      const d = await res.json();
      if (!res.ok || !d.ok) throw new Error(d.error || 'Kunde inte spara');
      status.textContent = '✓ Sparad';
      status.style.color = 'var(--green)';
      loadAcBedtime();
      setTimeout(() => { status.textContent = ''; }, 3500);
    } catch(e) {
      status.textContent = e.message;
      status.style.color = 'var(--red)';
    } finally {
      btn.disabled = false;
    }
  }

  async function clearAcBedtime() {
    const status = document.getElementById('ac-bedtime-status');
    const btn = document.getElementById('ac-bedtime-clear');
    if (!status || !btn) return;
    btn.disabled = true;
    status.textContent = 'Återställer...';
    status.style.color = 'var(--muted)';
    try {
      const res = await fetch('/api/ac/bedtime', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ bedtime: null })
      });
      const d = await res.json();
      if (!res.ok || !d.ok) throw new Error(d.error || 'Kunde inte återställa');
      status.textContent = '✓ Auto';
      status.style.color = 'var(--green)';
      loadAcBedtime();
      setTimeout(() => { status.textContent = ''; }, 3500);
    } catch(e) {
      status.textContent = e.message;
      status.style.color = 'var(--red)';
    } finally {
      btn.disabled = false;
    }
  }

  async function sendManualAcCommand() {
    const temp = document.getElementById('ac-manual-temp');
    const mode = document.getElementById('ac-manual-mode');
    const btn = document.getElementById('ac-manual-send');
    const status = document.getElementById('ac-manual-status');
    const badge = document.getElementById('ac-manual-badge');
    if (!temp || !mode || !btn || !status) return;
    const payload = { mode: mode.value };
    if (payload.mode !== 'off') {
      const setpoint = parseFloat(temp.value);
      if (isNaN(setpoint) || setpoint < 10 || setpoint > 35) {
        status.textContent = 'Ange 10-35 °C.';
        status.style.color = 'var(--red)';
        return;
      }
      payload.setpoint_c = setpoint;
    }
    btn.disabled = true;
    status.textContent = 'Skickar...';
    status.style.color = 'var(--muted)';
    try {
      const res = await fetch('/api/ac/manual-control', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload)
      });
      const d = await res.json();
      if (!res.ok || !d.ok) throw new Error(d.error || 'Kunde inte styra AC:n');
      status.textContent = '✓ Manuellt kommando skickat. Automatisk styrning är av.';
      status.style.color = 'var(--green)';
      if (badge) {
        badge.className = 'today-badge badge-red';
        badge.textContent = 'AUTO AV';
      }
      loadAcLoopStatus();
      setTimeout(loadAcStatus, 1500);
      setTimeout(loadAcHistory, 5000);
    } catch(e) {
      status.textContent = e.message;
      status.style.color = 'var(--red)';
    } finally {
      btn.disabled = false;
    }
  }

  async function setAcSetpoint() {
    const inp = document.getElementById('ac-setpoint-input');
    const btn = document.getElementById('ac-setpoint-btn');
    const status = document.getElementById('ac-setpoint-status');
    if (!inp || !btn) return;
    const val = parseFloat(inp.value);
    if (isNaN(val) || val < 10 || val > 35) {
      status.textContent = 'Ange 10–35 °C';
      status.style.color = 'var(--red)';
      return;
    }
    btn.disabled = true;
    status.textContent = 'Uppdaterar...';
    status.style.color = 'var(--muted)';
    inp.dataset.dirty = '1';
    try {
      const res = await fetch('/api/ac/setpoint', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ target_c: val })
      });
      const d = await res.json();
      if (!res.ok || !d.ok) throw new Error(d.error || 'Misslyckades');
      inp.value = d.target_c;
      status.textContent = '✓ Satt till ' + d.target_c + ' °C';
      status.style.color = 'var(--green)';
      delete inp.dataset.dirty;
      setTimeout(() => { status.textContent = ''; }, 4000);
      loadAcStatus();
      setTimeout(loadAcHistory, 5000);
    } catch(e) {
      status.textContent = e.message;
      status.style.color = 'var(--red)';
      delete inp.dataset.dirty;
    } finally {
      btn.disabled = false;
    }
  }

  // Outdoor weather - fetched through the dashboard proxy (/api/weather/current)
  async function loadWeatherStatus() {
    const hl = document.getElementById('weather-headline');
    const body = document.getElementById('weather-body');
    const badge = document.getElementById('weather-badge');
    if (!hl || !body || !badge) return;
    try {
      const res = await fetch('/api/weather/current');
      const d = await res.json();
      if (!res.ok || !d.ok) throw new Error(d.error || 'Weather unavailable');
      const temp = Number(d.temperature_c);
      const feels = Number(d.apparent_temperature_c);
      const wind = Number(d.wind_speed_ms);
      const humidity = Number(d.humidity_pct);
      const fmt = n => Number.isFinite(n) ? n.toFixed(1) : '-';
      const updated = d.time ? new Date(d.time).toLocaleTimeString('sv-SE', { hour:'2-digit', minute:'2-digit' }) : '-';
      hl.textContent = 'Ute ' + fmt(temp) + '\u00B0C';
      badge.className = 'today-badge badge-green';
      badge.textContent = (d.location || 'UTE').toUpperCase();
      body.textContent =
        (d.weather_text || 'Aktuellt väder') +
        '. Känns som ' + fmt(feels) + '\u00B0C' +
        (Number.isFinite(wind) ? ', vind ' + fmt(wind) + ' m/s' : '') +
        (Number.isFinite(humidity) ? ', luftfuktighet ' + humidity.toFixed(0) + '%' : '') +
        '. Uppdaterat ' + updated + ' via ' + (d.source || 'väder-API') + '.';
    } catch(e) {
      hl.textContent = 'Väder otillgängligt';
      body.textContent = 'Kunde inte hämta aktuell utetemperatur just nu.';
      badge.className = 'today-badge badge-red';
      badge.textContent = 'OFFLINE';
    }
  }
  loadWeatherStatus();
  setInterval(whileAuthenticated(loadWeatherStatus), 300000);

  function formatAcNumber(value, digits) {
    return Number(value).toLocaleString('sv-SE', {
      minimumFractionDigits: digits,
      maximumFractionDigits: digits
    });
  }

  function formatAcMode(mode) {
    const modes = {
      cool: 'kyla',
      cold: 'kyla',
      heat: 'värme',
      hot: 'värme',
      dry: 'avfuktning',
      fan: 'fläkt',
      auto: 'auto'
    };
    const key = String(mode || '').toLowerCase();
    return modes[key] || (mode || '-');
  }

  function formatAcReason(reason) {
    if (!reason) return '';

    let m = reason.match(/^Room ([\d.]+)C vs target ([\d.]+)C -> cool, AC setpoint ([\d.]+)C\.$/);
    if (m) {
      return `Rum ${formatAcNumber(m[1], 2)} °C jämfört med mål ${formatAcNumber(m[2], 1)} °C → kyler, AC-mål ${formatAcNumber(m[3], 1)} °C.`;
    }

    m = reason.match(/^Room ([\d.]+)C at\/below target ([\d.]+)C -> keep AC on at target for stable overnight temperature\.$/);
    if (m) {
      return `Rum ${formatAcNumber(m[1], 2)} °C är vid eller under målet ${formatAcNumber(m[2], 1)} °C → behåller AC:n på för stabil nattemperatur.`;
    }

    m = reason.match(/^Room ([\d.]+)C at\/below target ([\d.]+)C -> AC off\.$/);
    if (m) {
      return `Rum ${formatAcNumber(m[1], 2)} °C är vid eller under målet ${formatAcNumber(m[2], 1)} °C → AC av.`;
    }

    m = reason.match(/^Pre-cool waits until ([\d:]+) for bedtime ([\d:]+) \(wake ([\d:]+), room ([\d.]+)C, target ([\d.]+)C(.*)\)$/);
    if (m) {
      return `Förkylning väntar till ${m[1]} inför läggdags ${m[2]} (uppstigning ${m[3]}, rum ${formatAcNumber(m[4], 2)} °C, mål ${formatAcNumber(m[5], 1)} °C).`;
    }

    if (reason.toLowerCase().includes('water') && reason.toLowerCase().includes('lockout')) {
      return 'Vattenlås aktivt → tvingar AC:n av tills dunken är tömd och styrningen kvitteras.';
    }

    return reason
      .replaceAll('Room', 'Rum')
      .replaceAll('target', 'mål')
      .replaceAll('AC setpoint', 'AC-mål')
      .replaceAll('cooling rate', 'kylhastighet')
      .replaceAll('cool', 'kyler')
      .replaceAll('AC off', 'AC av')
      .replaceAll('C', ' °C');
  }

  function formatAcMarkerLabel(label) {
    if (!label) return '';
    let m = label.match(/^Setpoint → ([\d.]+)°$/);
    if (m) return `Mål → ${formatAcNumber(m[1], 0)}°`;
    m = label.match(/^AC on, setpoint ([\d.]+)°$/);
    if (m) return `AC på, mål ${formatAcNumber(m[1], 0)}°`;
    if (label === 'AC on') return 'AC på';
    if (label === 'AC off') return 'AC av';
    return label;
  }

  function ensureHumidityCard() {
    let card = document.getElementById('humidity-card');
    if (card) return card;
    const graph = document.getElementById('ac-graph');
    const graphCard = graph ? graph.closest('.bigcard') : null;
    const page = document.getElementById('page-climate');
    if (!page) return null;
    card = document.createElement('div');
    card.className = 'bigcard accent-blue humidity-card';
    card.id = 'humidity-card';
    card.innerHTML = `
      <div class="today-header">
        <h3 id="humidity-headline">Laddar luftfuktighet...</h3>
        <span class="today-badge badge-amber" id="humidity-badge">FUKT</span>
      </div>
      <p id="humidity-body">L&auml;ser luftfuktighet fr&aring;n tempsensorerna...</p>
      <div class="humidity-meter" aria-hidden="true"><div class="humidity-fill" id="humidity-fill"></div></div>
      <div class="humidity-meta">
        <span id="humidity-average">24h snitt: -</span>
        <span id="humidity-range">spann: -</span>
      </div>`;
    if (graphCard) page.insertBefore(card, graphCard);
    else page.appendChild(card);
    return card;
  }

  function humidityVerdict(value) {
    if (!Number.isFinite(value)) return ['badge-amber', 'OKANT', 'Ingen luftfuktighet fr\u00e5n sensorerna \u00e4n.'];
    if (value < 30) return ['badge-amber', 'TORRT', 'Torr luft. Sikta helst p\u00e5 40-55% f\u00f6r sovrumskomfort.'];
    if (value <= 60) return ['badge-green', 'BRA', 'Inom ett bra spann f\u00f6r komfort och \u00e5terh\u00e4mtning.'];
    if (value <= 70) return ['badge-amber', 'FUKTIGT', 'Lite h\u00f6g luftfuktighet. Ventilation eller avfuktning kan hj\u00e4lpa.'];
    return ['badge-red', 'HOGT', 'H\u00f6g luftfuktighet. Risk f\u00f6r kvav k\u00e4nsla och s\u00e4mre komfort.'];
  }

  async function loadHumidityStatus() {
    const card = ensureHumidityCard();
    if (!card) return;
    const hl = document.getElementById('humidity-headline');
    const body = document.getElementById('humidity-body');
    const badge = document.getElementById('humidity-badge');
    const fill = document.getElementById('humidity-fill');
    const avgEl = document.getElementById('humidity-average');
    const rangeEl = document.getElementById('humidity-range');
    try {
      const [currentRes, historyRes] = await Promise.all([fetch('/api/ac'), fetch('/api/ac/history')]);
      const current = await currentRes.json();
      const history = await historyRes.json();
      const latestReadings = (current.latest_readings || [])
        .filter(r => r.humidity_pct != null)
        .sort((a, b) => new Date(b.ts) - new Date(a.ts));
      const points = (history.humidity_points || []).filter(p => p.humidity != null);
      const latestVals = latestReadings.map(r => Number(r.humidity_pct)).filter(Number.isFinite);
      const value = latestVals.length
        ? latestVals.reduce((a, b) => a + b, 0) / latestVals.length
        : (points.length ? Number(points[points.length - 1].humidity) : NaN);
      const [badgeClass, badgeText, verdict] = humidityVerdict(value);
      badge.className = 'today-badge ' + badgeClass;
      badge.textContent = badgeText;
      if (Number.isFinite(value)) {
        hl.textContent = 'Luftfuktighet ' + value.toFixed(0) + '%';
        const sensorText = latestVals.length > 1 ? ' Snitt fr\u00e5n ' + latestVals.length + ' sensorer.' :
          latestVals.length === 1 ? ' Fr\u00e5n ' + (latestReadings[0].sensor_name || '1 sensor') + '.' : '';
        body.textContent = verdict + sensorText;
        if (fill) fill.style.width = Math.max(0, Math.min(100, value)).toFixed(0) + '%';
      } else {
        hl.textContent = 'Luftfuktighet saknas';
        body.textContent = 'Sensorerna skickar temperatur, men ingen luftfuktighet \u00e4nnu.';
        if (fill) fill.style.width = '0%';
      }
      if (points.length) {
        const vals = points.map(p => Number(p.humidity)).filter(Number.isFinite);
        const avg = vals.reduce((a, b) => a + b, 0) / vals.length;
        avgEl.textContent = '24h snitt: ' + avg.toFixed(0) + '%';
        rangeEl.textContent = 'spann: ' + Math.min(...vals).toFixed(0) + '-' + Math.max(...vals).toFixed(0) + '%';
      } else {
        avgEl.textContent = '24h snitt: -';
        rangeEl.textContent = 'spann: -';
      }
    } catch(e) {
      hl.textContent = 'Luftfuktighet otillg\u00e4nglig';
      body.textContent = 'Kunde inte h\u00e4mta luftfuktighet fr\u00e5n AC-keeper just nu.';
      badge.className = 'today-badge badge-red';
      badge.textContent = 'NERE';
    }
  }

  // AC / room temperature - fetched from ac-keeper through the dashboard proxy (/api/ac)
  async function loadAcStatus() {
    try {
      const res = await fetch('/api/ac');
      const d = await res.json();
      const hl = document.getElementById('ac-headline');
      const body = document.getElementById('ac-body');
      const badge = document.getElementById('ac-badge');
      const ev = d.latest_control_event;
      if (d.error || !ev) {
        hl.textContent = 'AC otillgänglig';
        body.textContent = 'Kunde inte nå AC-styrenheten på Pi:n.';
        badge.className = 'today-badge badge-red'; badge.textContent = 'NERE';
        return;
      }
      const ac = d.latest_ac_status || {};
      const measured = ev.measured_c;
      hl.textContent = 'Rum ' + (measured != null ? measured.toFixed(1) : '-') + '\u00B0C → mål ' + ev.target_c + '\u00B0C';
      const inp = document.getElementById('ac-setpoint-input');
      if (inp && !inp.dataset.dirty) inp.value = ev.target_c;
      const action = ev.action || '';
      const dry = action.indexOf('dry_run_') === 0;
      const base = action.replace('dry_run_', '');
      const map = {
        cool:['badge-amber','KYLER'],
        hold_cool:['badge-amber','HÅLLER KYLA'],
        heat:['badge-amber','VÄRMER'],
        off:['badge-green','AV'],
        hold:['badge-green','OK'],
        defer:['badge-amber','VÄNTAR'],
        pre_cool_wait:['badge-amber','VÄNTAR'],
        no_sensor_data:['badge-red','INGEN DATA'],
        water_lockout:['badge-red','VATTENLÅS']
      };
      const m = map[base] || ['badge-amber', base.toUpperCase()];
      badge.className = 'today-badge ' + m[0];
      badge.textContent = (dry ? 'TEST – ' : '') + m[1];
      const acState = ac.power ? ('AC på (' + formatAcMode(ac.mode) + ')') : 'AC av';
      body.textContent = acState + '. ' + (dry ? 'Testläge – styr inte den riktiga AC:n än. ' : '') + formatAcReason(ev.reason);
    } catch(e) {}
  }
  loadAcStatus();
  loadHumidityStatus();
  loadAcLoopStatus();
  loadAcBedtime();
  setInterval(whileAuthenticated(loadAcStatus), 60000);
  setInterval(whileAuthenticated(loadHumidityStatus), 60000);
  setInterval(whileAuthenticated(loadAcLoopStatus), 60000);

  // 24h rumstemperatur-graf (inline SVG, ingen extern lib) — med klockslag + hover/touch
  async function loadAcHistory() {
    const el = document.getElementById('ac-graph');
    if (!el) return;
    try {
      const res = await fetch('/api/ac/history');
      const d = await res.json();
      const raw = (d.points || []).filter(p => p.temp != null);
      if (!raw.length) { el.textContent = d.error ? 'Temperaturhistorik otillgänglig.' : 'Samlar temperaturdata...'; return; }
      const outsideRaw = (d.outside_points || []).filter(p => p.temp != null);
      const humidityRaw = (d.humidity_points || []).filter(p => p.humidity != null);
      const temps = raw.map(p => p.temp);
      const outsideTemps = outsideRaw.map(p => p.temp);
      const humidityVals = humidityRaw.map(p => Number(p.humidity)).filter(Number.isFinite);
      const allTemps = temps.concat(outsideTemps);
      let lo = Math.min(...allTemps), hi = Math.max(...allTemps);
      if (d.target != null) { lo = Math.min(lo, d.target); hi = Math.max(hi, d.target); }
      const pad = Math.max(0.5, (hi - lo) * 0.15);
      const yLo = lo - pad, yHi = hi + pad;
      const W = 600, H = 195, padL = 34, padR = humidityVals.length ? 44 : 12, padT = 10, padB = 30;
      const innerW = W - padL - padR, innerH = H - padT - padB;
      const t0 = new Date(raw[0].t).getTime(), t1 = new Date(raw[raw.length-1].t).getTime();
      const tspan = Math.max(1, t1 - t0);
      const X = ms => padL + ((ms - t0) / tspan) * innerW;
      const Y = v => padT + (1 - (v - yLo) / (yHi - yLo)) * innerH;
      let hLo = 30, hHi = 70;
      if (humidityVals.length) {
        hLo = Math.max(0, Math.min(...humidityVals) - 4);
        hHi = Math.min(100, Math.max(...humidityVals) + 4);
        if ((hHi - hLo) < 12) {
          const mid = (hHi + hLo) / 2;
          hLo = Math.max(0, mid - 6);
          hHi = Math.min(100, mid + 6);
        }
      }
      const YH = v => padT + (1 - (v - hLo) / Math.max(1, hHi - hLo)) * innerH;
      const fmt = ms => new Date(ms).toLocaleTimeString('sv-SE', { hour:'2-digit', minute:'2-digit' });
      const P = raw.map(p => { const ms = new Date(p.t).getTime(); return { ms, temp: p.temp, x: X(ms), y: Y(p.temp) }; });
      const OP = outsideRaw.map(p => { const ms = new Date(p.t).getTime(); return { ms, temp: p.temp, x: X(ms), y: Y(p.temp) }; }).filter(p => p.ms >= t0 && p.ms <= t1);
      const HP = humidityRaw.map(p => { const ms = new Date(p.t).getTime(); const humidity = Number(p.humidity); return { ms, humidity, x: X(ms), y: YH(humidity), sensors: p.sensors || [], samples: p.samples || 1 }; }).filter(p => Number.isFinite(p.humidity) && p.ms >= t0 && p.ms <= t1);
      const outsidePath = OP.map((p,i) => (i === 0 ? 'M' : 'L') + p.x.toFixed(1) + ' ' + p.y.toFixed(1)).join(' ');
      const humidityPath = HP.map((p,i) => (i === 0 ? 'M' : 'L') + p.x.toFixed(1) + ' ' + p.y.toFixed(1)).join(' ');
      // Bryt linjen där det finns ett glapp i datan (annars ritas en falsk "trendlinje" över hål)
      const dts = []; for (let i = 1; i < P.length; i++) dts.push(P[i].ms - P[i-1].ms);
      const sortedDt = dts.slice().sort((a,b) => a - b);
      const medDt = sortedDt.length ? sortedDt[Math.floor(sortedDt.length/2)] : 0;
      const gapMs = Math.max(medDt * 3.5, 20*60*1000); // glapp = >3.5x normal takt, minst 20 min
      const path = P.map((p,i) => {
        const gap = i > 0 && (p.ms - P[i-1].ms) > gapMs;
        return (i === 0 || gap ? 'M' : 'L') + p.x.toFixed(1) + ' ' + p.y.toFixed(1);
      }).join(' ');
      const cur = temps[temps.length-1];
      const outsideCur = outsideTemps.length ? outsideTemps[outsideTemps.length-1] : null;
      const humidityCur = HP.length ? HP[HP.length - 1].humidity : null;
      // AC-kylperioder som mjuka band i bakgrunden (istället för en massa streck per på/av)
      const trans = (d.markers || []).filter(m => m.kind === 'on' || m.kind === 'off')
        .map(m => ({ ms: new Date(m.t).getTime(), kind: m.kind })).sort((a,b) => a.ms - b.ms);
      const bands = []; let openTs = null;
      if (trans.length && trans[0].kind === 'off') openTs = t0; // var på redan vid start
      for (const tr of trans) {
        if (tr.kind === 'on' && openTs === null) openTs = tr.ms;
        else if (tr.kind === 'off' && openTs !== null) { bands.push([openTs, tr.ms]); openTs = null; }
      }
      if (openTs !== null) bands.push([openTs, t1]);
      const bandHtml = bands.map(([a,b]) => {
        const x1 = X(Math.max(a, t0)), x2 = X(Math.min(b, t1));
        const w = Math.max(0, x2 - x1);
        return `<rect x="${x1.toFixed(1)}" y="${padT}" width="${w.toFixed(1)}" height="${innerH}" fill="var(--blue)" opacity="0.10"/>`;
      }).join('');
      const inBand = ms => bands.some(([a,b]) => ms >= a && ms <= b);
      // Bara setpoint-ändringar markeras som små prickar (på/av syns redan via banden)
      const mcolor = () => 'var(--amber)';
      const yAt = ms => { let b = P[0], bd = Infinity; for (const p of P) { const dd = Math.abs(p.ms - ms); if (dd < bd) { bd = dd; b = p; } } return b.y; };
      const MK = (d.markers || []).filter(m => m.kind === 'setpoint')
        .map(m => { const ms = new Date(m.t).getTime(); return { ms, x: X(ms), y: yAt(ms), kind: m.kind, label: formatAcMarkerLabel(m.label) }; });
      const mhtml = MK.map(m =>
        `<circle cx="${m.x.toFixed(1)}" cy="${m.y.toFixed(1)}" r="2.5" fill="var(--amber)" stroke="var(--bg2)" stroke-width="1"/>`
      ).join('');
      let tline = '';
      if (d.target != null) {
        const ty = Y(d.target).toFixed(1);
        tline = `<line x1="${padL}" y1="${ty}" x2="${W-padR}" y2="${ty}" stroke="var(--blue)" stroke-width="1" stroke-dasharray="4 3" opacity="0.6"/><text x="${W-padR}" y="${(+ty)-3}" text-anchor="end" font-size="9" fill="var(--blue)">mål ${d.target}°</text>`;
      }
      // tidsaxel med klockslag (5 markeringar)
      let xaxis = '', N = 4;
      for (let i = 0; i <= N; i++) {
        const ms = t0 + tspan * i / N, xx = X(ms).toFixed(1);
        const anchor = i === 0 ? 'start' : i === N ? 'end' : 'middle';
        xaxis += `<line x1="${xx}" y1="${padT}" x2="${xx}" y2="${H-padB}" stroke="var(--border2)" stroke-width="0.5" opacity="0.4"/>`;
        xaxis += `<text x="${xx}" y="${H-12}" text-anchor="${anchor}" font-size="9" fill="var(--muted)">${fmt(ms)}</text>`;
      }
      const hAxis = HP.length ? `
            <text x="${W-padR+8}" y="${YH(hHi).toFixed(1)}" text-anchor="start" font-size="9" fill="var(--amber)">${hHi.toFixed(0)}%</text>
            <text x="${W-padR+8}" y="${YH(hLo).toFixed(1)}" text-anchor="start" font-size="9" fill="var(--amber)">${hLo.toFixed(0)}%</text>` : '';
      el.innerHTML = `
        <div style="display:flex;justify-content:space-between;align-items:baseline;margin-bottom:6px;">
          <span style="font-size:22px;font-weight:800;">${cur.toFixed(1)}°C</span>
          <span style="font-size:11px;color:var(--muted);display:flex;gap:10px;align-items:center;">
            ${bands.length ? '<span style="display:inline-flex;align-items:center;gap:4px;"><span style="width:10px;height:10px;border-radius:2px;background:var(--blue);opacity:0.25;display:inline-block;"></span>kyler</span>' : ''}
            ${humidityCur != null ? `<span style="display:inline-flex;align-items:center;gap:4px;"><span style="width:12px;height:2px;background:var(--amber);display:inline-block;"></span>fukt ${humidityCur.toFixed(0)}%</span>` : ''}
            <span style="display:inline-flex;align-items:center;gap:4px;"><span style="width:12px;height:2px;background:var(--green);display:inline-block;"></span>inne ${cur.toFixed(1)}°C</span>
            ${outsideCur != null ? `<span style="display:inline-flex;align-items:center;gap:4px;"><span style="width:12px;height:2px;background:var(--blue);display:inline-block;"></span>ute ${outsideCur.toFixed(1)}°C</span>` : ''}
            <span>spann ${Math.min(...allTemps).toFixed(1)}–${Math.max(...allTemps).toFixed(1)}°C</span>
          </span>
        </div>
        <div style="position:relative;">
          <svg id="ac-svg" viewBox="0 0 ${W} ${H}" width="100%" style="display:block;touch-action:none;cursor:crosshair;">
            ${bandHtml}
            ${xaxis}
            <text x="${padL-5}" y="${Y(hi).toFixed(1)}" text-anchor="end" font-size="9" fill="var(--muted)">${hi.toFixed(1)}</text>
            <text x="${padL-5}" y="${Y(lo).toFixed(1)}" text-anchor="end" font-size="9" fill="var(--muted)">${lo.toFixed(1)}</text>
            ${hAxis}
            ${tline}
            ${outsidePath ? `<path d="${outsidePath}" fill="none" stroke="var(--blue)" stroke-width="1.8" stroke-linejoin="round" stroke-linecap="round" opacity="0.85"/>` : ''}
            ${humidityPath ? `<path d="${humidityPath}" fill="none" stroke="var(--amber)" stroke-width="1.8" stroke-linejoin="round" stroke-linecap="round" opacity="0.9"/>` : ''}
            <path d="${path}" fill="none" stroke="var(--green)" stroke-width="2" stroke-linejoin="round" stroke-linecap="round"/>
            ${mhtml}
            <line id="ac-cross" y1="${padT}" y2="${H-padB}" stroke="var(--muted2)" stroke-width="1" opacity="0"/>
            <circle id="ac-dot" r="3.5" fill="var(--green)" stroke="var(--bg2)" stroke-width="1.5" opacity="0"/>
            <circle id="humidity-dot" r="3.2" fill="var(--amber)" stroke="var(--bg2)" stroke-width="1.5" opacity="0"/>
          </svg>
          <div id="ac-tip" style="position:absolute;pointer-events:none;background:var(--bg2);border:1px solid var(--border2);border-radius:6px;padding:4px 8px;font-size:11px;white-space:nowrap;opacity:0;transform:translate(-50%,-135%);z-index:5;"></div>
        </div>`;
      const svg = document.getElementById('ac-svg');
      const cross = document.getElementById('ac-cross'), dot = document.getElementById('ac-dot'), humDot = document.getElementById('humidity-dot'), tip = document.getElementById('ac-tip');
      const at = clientX => {
        const rect = svg.getBoundingClientRect();
        const vbX = ((clientX - rect.left) / rect.width) * W;
        let best = P[0], bd = Infinity;
        for (const p of P) { const dd = Math.abs(p.x - vbX); if (dd < bd) { bd = dd; best = p; } }
        let outside = null, od = Infinity;
        for (const p of OP) { const dd = Math.abs(p.x - vbX); if (dd < od) { od = dd; outside = p; } }
        let humidity = null, hd = Infinity;
        for (const p of HP) { const dd = Math.abs(p.x - vbX); if (dd < hd) { hd = dd; humidity = p; } }
        cross.setAttribute('x1', best.x); cross.setAttribute('x2', best.x); cross.setAttribute('opacity', '0.5');
        dot.setAttribute('cx', best.x); dot.setAttribute('cy', best.y); dot.setAttribute('opacity', '1');
        if (humDot && humidity && hd < 12) {
          humDot.setAttribute('cx', humidity.x);
          humDot.setAttribute('cy', humidity.y);
          humDot.setAttribute('opacity', '1');
        } else if (humDot) {
          humDot.setAttribute('opacity', '0');
        }
        tip.style.left = (best.x / W * rect.width) + 'px';
        tip.style.top = (best.y / H * rect.height) + 'px';
        tip.style.opacity = '1';
        let mk = null, md = Infinity;
        for (const m of MK) { const dd = Math.abs(m.x - vbX); if (dd < md) { md = dd; mk = m; } }
        const humidityLabel = (humidity && hd < 12) ? `<br><span style="color:var(--amber);">fukt ${humidity.humidity.toFixed(0)}%${humidity.sensors.length ? ' · ' + humidity.sensors.length + ' sensorer' : ''}</span>` : '';
        const mkLabel = humidityLabel + ((mk && md < 7) ? `<br><span style="color:var(--amber);">${escapeHtml(mk.label)}</span>`
          : (inBand(best.ms) ? '<br><span style="color:var(--blue);">kyler</span>' : ''));
        const outsideLabel = outside ? `<br><span style="color:var(--blue);">ute ${outside.temp.toFixed(1)}°C</span>` : '';
        tip.innerHTML = `<strong>inne ${best.temp.toFixed(1)}°C</strong> · ${fmt(best.ms)}${outsideLabel}${mkLabel}`;
      };
      const hide = () => { cross.setAttribute('opacity','0'); dot.setAttribute('opacity','0'); if (humDot) humDot.setAttribute('opacity','0'); tip.style.opacity='0'; };
      svg.addEventListener('pointermove', e => at(e.clientX));
      svg.addEventListener('pointerdown', e => at(e.clientX));
      svg.addEventListener('pointerleave', hide);
    } catch(e) { el.textContent = 'Temperaturhistorik otillgänglig.'; }
  }
  loadAcHistory();
  setInterval(whileAuthenticated(loadAcHistory), 300000);

  function renderInsightCards(items) {
    if (!items || !items.length) return '<div style="font-size:12px;color:var(--muted3);">Inga mönster hittade ännu.</div>';
    return items.map(it => {
      const col = it.color === 'green' ? 'var(--green)' : it.color === 'red' ? 'var(--red)' : 'var(--amber)';
      return `<div class="insight-row">
        <span class="insight-dot" style="background:${col}"></span>
        <div>
          <div class="insight-row-title">${escapeHtml(it.title || '')}</div>
          <div class="insight-row-body">${escapeHtml(it.detail || '')}${it.action ? ' <span style="color:var(--accent);font-size:11px;font-weight:700">→ ' + escapeHtml(it.action) + '</span>' : ''}</div>
        </div>
      </div>`;
    }).join('');
  }

  function drawSparkline(svgEl, data, color, _tries) {
    if (!svgEl || !data || data.length < 2) return;
    // If layout isn't ready yet, clientWidth is 0 — wait a frame and retry
    // (otherwise the curve only fills a tiny fallback width).
    const W = Math.round(svgEl.getBoundingClientRect().width);
    if (W < 10) {
      if ((_tries || 0) < 30) requestAnimationFrame(() => drawSparkline(svgEl, data, color, (_tries || 0) + 1));
      return;
    }
    const H = svgEl.clientHeight || 28;
    const min = Math.min(...data), max = Math.max(...data), span = max - min || 1;
    const pad = 2;
    const pts = data.map((v, i) => {
      const x = pad + (i / (data.length - 1)) * (W - pad*2);
      const y = pad + (1 - (v - min) / span) * (H - pad*2);
      return [x, y];
    });
    const line = pts.reduce((acc, [x, y], i) => {
      if (i === 0) return `M${x.toFixed(1)} ${y.toFixed(1)}`;
      const [px, py] = pts[i-1];
      const cx = (px + x) / 2;
      return `${acc} C${cx.toFixed(1)} ${py.toFixed(1)} ${cx.toFixed(1)} ${y.toFixed(1)} ${x.toFixed(1)} ${y.toFixed(1)}`;
    }, '');
    const [ex, ey] = pts[pts.length - 1];
    const gradId = 'sg-' + Math.random().toString(36).slice(2, 7);
    svgEl.innerHTML = `
      <defs><linearGradient id="${gradId}" x1="0" y1="0" x2="0" y2="1">
        <stop offset="0%" stop-color="${color}" stop-opacity="0.22"/>
        <stop offset="100%" stop-color="${color}" stop-opacity="0"/>
      </linearGradient></defs>
      <path d="${line} L${ex.toFixed(1)} ${H} L${pts[0][0].toFixed(1)} ${H} Z" fill="url(#${gradId})" stroke="none"/>
      <path d="${line}" fill="none" stroke="${color}" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/>
      <circle cx="${ex.toFixed(1)}" cy="${ey.toFixed(1)}" r="2.5" fill="${color}"/>`;
  }

  function updateAppbar(h) {
    const greet = (() => {
      const hr = new Date().getHours();
      return hr < 12 ? 'God morgon' : hr < 17 ? 'God eftermiddag' : 'God kväll';
    })();
    const hiEl = document.getElementById('appbar-hi');
    if (hiEl) hiEl.textContent = greet + ', Hugo';
    const dateEl = document.getElementById('appbar-date');
    if (dateEl) {
      const d = new Date();
      const days = ['Söndag','Måndag','Tisdag','Onsdag','Torsdag','Fredag','Lördag'];
      const { week } = getISOWeekInfo();
      dateEl.textContent = days[d.getDay()] + ' · vecka ' + week;
    }
    const rEl = document.getElementById('appbar-readiness');
    if (rEl && h?.readiness?.score != null) {
      rEl.textContent = h.readiness.score;
      const col = h.readiness.score >= 70 ? 'var(--accent)' : h.readiness.score >= 40 ? 'var(--amber)' : 'var(--red)';
      rEl.style.color = col;
    }
    const rhrEl = document.getElementById('appbar-rhr');
    if (rhrEl && h?.restingHR?.value != null) rhrEl.textContent = h.restingHR.value;
  }

  async function loadInsights(force) {
    const list  = document.getElementById('insights-list');
    const hl    = document.getElementById('insights-headline');
    const badge = document.getElementById('insights-badge');
    if (!list) return;
    try {
      const res = await fetch('/api/insights' + (force ? '?force=1' : ''));
      const d = await res.json();
      if (d.error) { list.innerHTML = `<div style="font-size:12px;color:var(--red);">${escapeHtml(d.error)}</div>`; return; }
      if (d.headline && hl) hl.textContent = d.headline;
      const map = { good:['badge-green','GOOD'], watch:['badge-amber','WATCH'], caution:['badge-red','CAUTION'] };
      const m = map[d.status] || ['badge-amber','AI'];
      if (badge) { badge.className = 'today-badge ' + m[0]; badge.textContent = m[1]; }
      list.innerHTML = renderInsightCards(d.insights);
    } catch(e) { list.innerHTML = '<div style="font-size:12px;color:var(--muted3);">Could not load insights.</div>'; }
  }
  loadInsights();

  async function loadSleepInsights(force) {
    const list  = document.getElementById('sleep-ai-list');
    const hl    = document.getElementById('sleep-ai-headline');
    const badge = document.getElementById('sleep-ai-badge');
    if (!list) return;
    try {
      const res = await fetch('/api/sleep-insights' + (force ? '?force=1' : ''));
      const d = await res.json();
      if (d.error) { list.innerHTML = `<div style="font-size:12px;color:var(--red);">${escapeHtml(d.error)}</div>`; return; }
      if (d.headline && hl) hl.textContent = d.headline;
      const map = { good:['badge-green','GOOD'], watch:['badge-amber','WATCH'], caution:['badge-red','CAUTION'] };
      const m = map[d.status] || ['badge-amber','AI'];
      if (badge) { badge.className = 'today-badge ' + m[0]; badge.textContent = m[1]; }
      list.innerHTML = renderInsightCards(d.insights);
    } catch(e) { list.innerHTML = '<div style="font-size:12px;color:var(--muted3);">Could not load sleep analysis.</div>'; }
  }

  const history = [];

  async function send(txt) {
    const inp = document.getElementById('chat-input');
    const msg = txt || inp.value.trim();
    if (!msg) return;
    inp.value = '';
    const box = document.getElementById('messages');
    const uDiv = document.createElement('div');
    uDiv.className = 'msg user';
    uDiv.innerHTML = '<div class="msg-from">DU</div>' + escapeHtml(msg);
    box.appendChild(uDiv);
    const aDiv = document.createElement('div');
    aDiv.className = 'msg ai';
    aDiv.innerHTML = '<div class="msg-from">COACH</div><span style="color:var(--muted)">Thinking...</span>';
    box.appendChild(aDiv);
    box.scrollTop = box.scrollHeight;
    history.push({ role:'user', content:msg });
    try {
      const res = await fetch('/api/chat', { method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify({ message:msg, context:buildCTX(), history }) });
      const data = await res.json();
      const raw = data.reply || data.error || 'Inget svar.';
      const reply = escapeHtml(raw)
        .replace(/\*\*(.*?)\*\*/gs, '$1')
        .replace(/\*(.*?)\*/gs, '$1')
        .replace(/#{1,3} (.*)/g, '$1')
        .replace(/\n/g, '<br>');
      aDiv.innerHTML = '<div class="msg-from">COACH</div>' + reply;
      history.push({ role:'assistant', content:raw });
    } catch(e) {
      aDiv.innerHTML = '<div class="msg-from">COACH</div>Kunde inte nå servern.';
    }
    box.scrollTop = box.scrollHeight;
  }

  function qa(t) { goto('coach'); setTimeout(() => send(t), 100); }

  // --- Styrka ---
  const SUGGESTIONS = ['Bänkpress','Marklyft','Knäböj','Axelpress','Latsdrag','Rodd','Dips','Chins','Bicepscurl','Tricepspress','Benpress','Vadpress','Planka','Situps','Rumänsk marklyft','Frontböj','Bulgarisk utfall','Bröststödd rodd','Flyes','Tricepspushdown','Hammarcurl','Face pull','Bål','Ryggresning'];

  const fmtDur = s => { const h=Math.floor(s/3600), m=Math.floor((s%3600)/60); return h>0?h+'h '+m+'m':m+' min'; };
  const fmtDateStr = s => new Date(s).toLocaleDateString('sv-SE',{weekday:'short',day:'numeric',month:'short'});

  // ANALYSIS — fitness-trender + förändringstakt (derivata)
  function fmtMetric(v, fmt) {
    if (v == null) return '–';
    if (fmt === 'pace') { const m = Math.floor(v/60), s = Math.round(v%60); return m + ':' + String(s).padStart(2,'0'); }
    if (fmt === 'load') return Math.round(v).toString();
    if (fmt === 1) return v.toFixed(1);
    return Math.round(v).toString();
  }
  function sparkline(series, fmt, good) {
    if (!series || series.length < 2) return '';
    const W = 200, H = 46, p = 4;
    const vs = series.map(d => d.v);
    let lo = Math.min(...vs), hi = Math.max(...vs);
    if (hi === lo) { hi += 1; lo -= 1; }
    const t0 = new Date(series[0].t).getTime(), t1 = new Date(series[series.length-1].t).getTime();
    const tspan = Math.max(1, t1 - t0);
    const X = ms => p + ((ms - t0) / tspan) * (W - 2*p);
    const Y = v => p + (1 - (v - lo) / (hi - lo)) * (H - 2*p);
    const pts = series.map(d => ({ x: X(new Date(d.t).getTime()), y: Y(d.v), ms: new Date(d.t).getTime() }));
    let path = '';
    for (let i = 0; i < pts.length; i++) {
      const gap = i > 0 && (pts[i].ms - pts[i-1].ms) > 5*86400000; // bryt vid >5 dagars glapp
      path += (i === 0 || gap ? 'M' : 'L') + pts[i].x.toFixed(1) + ' ' + pts[i].y.toFixed(1) + ' ';
    }
    const last = pts[pts.length-1];
    const col = 'var(--muted2)';
    return `<svg viewBox="0 0 ${W} ${H}" width="100%" height="${H}" preserveAspectRatio="none" style="display:block;">
      <path d="${path}" fill="none" stroke="${col}" stroke-width="1.5" stroke-linejoin="round" stroke-linecap="round" opacity="0.8"/>
      <circle cx="${last.x.toFixed(1)}" cy="${last.y.toFixed(1)}" r="2.5" fill="var(--text)"/>
    </svg>`;
  }
  async function loadAnalysis() {
    const grid = document.getElementById('analysis-grid');
    const summary = document.getElementById('analysis-summary');
    grid.innerHTML = '<div style="color:var(--muted);font-size:13px;font-family:\'IBM Plex Mono\',monospace;">Laddar trender…</div>';
    try {
      const res = await fetch('/api/analysis');
      const d = await res.json();
      const statusMap = {
        BALANCED:   ['badge-green','Balanserad'],
        UNBALANCED: ['badge-amber','HRV i obalans'],
        LOW:        ['badge-red','Låg'],
        POOR:       ['badge-red','Dålig'],
      };
      const st = (d.hrv_status || '').toUpperCase();
      const sm = statusMap[st];
      if (sm) sm[1] = getHrvStatusLabel(st) || sm[1];
      summary.innerHTML = `
        <div style="display:flex;align-items:center;gap:12px;flex-wrap:wrap;font-size:12px;color:var(--muted);">
          <span>Trender över de senaste ${d.window_days} dagarna. Pilarna visar förändringens riktning (derivatan), inte bara dagens värde.</span>
          <span class="today-badge badge-amber">Hälso-rader: ${d.health_rows ?? 0}</span>
          <span class="today-badge badge-amber">Mätvärdes-rader: ${d.metric_rows ?? 0}</span>
          ${sm ? `<span class="today-badge ${sm[0]}">HRV-status: ${sm[1]}</span>` : ''}
        </div>`;
      const dirMeta = {
        improving: ['↗', 'var(--green)', 'Förbättras'],
        declining: ['↘', 'var(--red)',   'Försämras'],
        stable:    ['→', 'var(--muted2)','Stabil'],
        unknown:   ['·', 'var(--muted)', 'Samlar…'],
      };
      grid.innerHTML = d.metrics.map(m => {
        const dm = dirMeta[m.direction] || dirMeta.unknown;
        const samples = m.samples ?? (m.series ? m.series.length : 0);
        const hasValue = samples >= 1;
        const hasTrend = samples >= 2;
        let rate = '';
        if (hasTrend && m.slopePerWeek != null) {
          const sign = m.slopePerWeek > 0 ? '+' : '';
          const rateVal = m.fmt === 'pace'
            ? (m.slopePerWeek > 0 ? '+' : '−') + fmtMetric(Math.abs(m.slopePerWeek), 'pace')
            : sign + (Math.abs(m.slopePerWeek) < 1 ? m.slopePerWeek.toFixed(2) : m.slopePerWeek.toFixed(1));
          rate = `${rateVal} ${m.unit === 'pace' ? '/km' : m.unit}/wk`;
        }
        const pct = (hasTrend && m.pctChange != null) ? `${m.pctChange > 0 ? '+' : ''}${m.pctChange}% över perioden` : (hasValue ? `${samples} mätning${samples === 1 ? '' : 'ar'}` : 'Ingen Garmin-data');
        const valStr = m.latest != null ? fmtMetric(m.latest, m.fmt) : '–';
        const unitStr = m.unit && m.unit !== 'pace' ? ` <span style="font-size:13px;color:var(--muted);font-weight:500;">${m.unit}</span>` : (m.unit === 'pace' && m.latest != null ? ' <span style="font-size:13px;color:var(--muted);font-weight:500;">/km</span>' : '');
        return `
          <div style="background:var(--bg2);border:1px solid var(--border);border-radius:14px;padding:16px 18px;display:flex;flex-direction:column;gap:10px;">
            <div style="display:flex;justify-content:space-between;align-items:flex-start;gap:8px;">
              <span style="font-size:12px;font-weight:600;letter-spacing:0.02em;color:var(--muted3);">${escapeHtml(m.label)}</span>
              <span style="font-size:11px;font-weight:700;color:${dm[1]};white-space:nowrap;">${dm[0]} ${dm[2]}</span>
            </div>
            <div style="font-size:26px;font-weight:800;letter-spacing:-0.5px;font-variant-numeric:tabular-nums;">${valStr}${unitStr}</div>
            ${hasTrend ? sparkline(m.series, m.fmt, m.good) : `<div style="height:46px;display:flex;align-items:center;color:var(--muted);font-size:11px;font-family:'IBM Plex Mono',monospace;">${hasValue ? 'Behöver mer historik för trend' : 'Väntar på Garmin-mätvärde'}</div>`}
            <div style="display:flex;justify-content:space-between;font-size:11px;color:var(--muted);font-family:'IBM Plex Mono',monospace;">
              <span style="color:${dm[1]};">${rate}</span>
              <span>${pct}</span>
            </div>
          </div>`;
      }).join('');
      if (!grid.style.display) {
        grid.style.display = 'grid';
        grid.style.gridTemplateColumns = 'repeat(auto-fill, minmax(220px, 1fr))';
        grid.style.gap = '14px';
      }
    } catch(e) {
      grid.innerHTML = '<div style="color:var(--red);font-size:13px;">Kunde inte ladda analys: ' + escapeHtml(e.message) + '</div>';
    }
  }

  // ─── STRENGTH: sub-tabs (today's live workout vs history) ───
  let strengthCurrentTab = 'today';
  let activeStrengthRecommendations = [];
  function loadStrengthPage() { strengthTab(strengthCurrentTab); }
  function strengthTab(which) {
    strengthCurrentTab = which;
    document.getElementById('stab-today').classList.toggle('active', which === 'today');
    document.getElementById('stab-history').classList.toggle('active', which === 'history');
    document.getElementById('stab-analysis').classList.toggle('active', which === 'analysis');
    document.getElementById('strength-today').style.display   = which === 'today'   ? 'block' : 'none';
    document.getElementById('strength-history').style.display = which === 'history' ? 'block' : 'none';
    document.getElementById('strength-analysis').style.display = which === 'analysis' ? 'block' : 'none';
    if (which === 'today') loadTodayWorkout();
    else if (which === 'history') loadStrength();
    else loadStrengthAnalysis();
  }

  function fmtKg(value) {
    if (value === null || value === undefined || value === '') return '-';
    return Number(value).toLocaleString('sv-SE', { maximumFractionDigits: 1 }) + ' kg';
  }

  function fmtSignedKg(value) {
    if (value === null || value === undefined) return '-';
    const n = Number(value);
    const sign = n > 0 ? '+' : '';
    return sign + n.toLocaleString('sv-SE', { maximumFractionDigits: 1 }) + ' kg';
  }

  function fmtVolume(value) {
    const n = Number(value || 0);
    if (n >= 1000) return (n / 1000).toLocaleString('sv-SE', { maximumFractionDigits: 1 }) + ' ton';
    return n.toLocaleString('sv-SE', { maximumFractionDigits: 0 }) + ' kg';
  }

  function strengthPrescriptionHtml(session, contextId) {
    const recommendations = session?.strength_recommendations || [];
    if (!recommendations.length) return '';
    activeStrengthRecommendations = recommendations;
    const rows = recommendations.map((rec, index) => {
      const status = rec.confidence === 'caution' ? 'VARNING'
        : rec.confidence === 'none' ? 'NY ÖVNING'
        : rec.confidence === 'planned' ? 'UTAN VIKT'
        : 'HISTORIK';
      const statusClass = rec.confidence === 'caution' ? ' caution'
        : rec.confidence === 'none' ? ' new' : '';
      const lastRepLabel = rec.lastRepsMax != null && Number(rec.lastRepsMax) !== Number(rec.lastReps)
        ? `${rec.lastReps}–${rec.lastRepsMax}`
        : (rec.lastReps || '-');
      const last = rec.lastWeight != null
        ? `Senast ${rec.lastSets || 1}×${lastRepLabel} @ ${fmtKg(rec.lastWeight)} · ${fmtDateStr(rec.lastDate)}`
        : rec.reason || '';
      return `<div class="strength-rx-row${statusClass}">
        <div class="strength-rx-main">
          <div class="strength-rx-name">${escapeHtml(rec.exercise || '')}<span>${status}</span></div>
          <div class="strength-rx-value">${escapeHtml(rec.prescription || '')}</div>
          <div class="strength-rx-last">${escapeHtml(last)}</div>
        </div>
        <button type="button" class="strength-rx-use" data-action="apply-strength-rx" data-context="${escapeHtml(contextId)}" data-index="${index}">Fyll i</button>
      </div>`;
    }).join('');
    return `<div class="strength-rx">
      <div class="strength-rx-head">
        <span>Rekommenderad progression</span>
        <em>från din logg</em>
      </div>
      <div class="strength-rx-list">${rows}</div>
    </div>`;
  }

  function applyStrengthRecommendation(contextId, index) {
    const rec = activeStrengthRecommendations[index];
    if (!rec) return;
    const name = document.getElementById('ex-name-' + contextId);
    const sets = document.getElementById('ex-sets-' + contextId);
    const reps = document.getElementById('ex-reps-' + contextId);
    const weight = document.getElementById('ex-weight-' + contextId);
    if (!name || !sets || !reps || !weight) return;
    name.value = rec.exercise || '';
    sets.value = rec.sets || '';
    reps.value = rec.reps == null ? '' : String(rec.reps) + (rec.unit === 'seconds' ? ' sek' : '');
    weight.value = rec.weight == null ? '' : rec.weight;
    name.focus();
  }

  function strengthSessionTitle(session) {
    const d = new Date(session.date);
    const dateLabel = Number.isNaN(d.getTime())
      ? ''
      : d.toLocaleDateString('sv-SE', { day:'numeric', month:'short' });
    let title = '';
    if (!Number.isNaN(d.getTime())) {
      const info = getISOWeekInfo(d);
      const plannedLift = (PLAN_SESSIONS || []).find(p =>
        p.type === 'lift' && p.week === info.week && p.dow === info.dow
      );
      title = plannedLift?.title || '';
    }
    const garminName = String(session.name || '').trim();
    if (!title && garminName && !/^strength$/i.test(garminName)) title = garminName;
    if (!title) title = 'Styrka';
    return [title, dateLabel].filter(Boolean).join(' ');
  }

  async function loadStrengthAnalysis() {
    const el = document.getElementById('strength-analysis-content');
    if (!el) return;
    el.innerHTML = '<div style="color:var(--muted);font-size:13px;font-family:\'IBM Plex Mono\',monospace;">Analyserar styrkeloggar...</div>';
    try {
      const res = await fetch('/api/strength/analysis');
      const data = await res.json();
      const summary = data.summary || {};
      const exercises = data.exercises || [];
      if (!summary.exerciseLogs) {
        el.innerHTML = '<div class="no-sessions">Logga några övningar först, så börjar analysen räkna progression, volym och personbästan.</div>';
        return;
      }

      const maxWeekVolume = Math.max(1, ...(data.weeks || []).map(w => Number(w.volume || 0)));
      const weeksHtml = (data.weeks || []).map(w => {
        const h = Math.max(8, Math.round((Number(w.volume || 0) / maxWeekVolume) * 100));
        const label = new Date(w.weekStart).toLocaleDateString('sv-SE', { month:'short', day:'numeric' });
        return `<div class="strength-week">
          <div class="strength-week-bar" style="height:${h}%"></div>
          <div class="strength-week-label">${escapeHtml(label)}</div>
        </div>`;
      }).join('');

      const bestHtml = (data.bestLifts || []).map(ex => `
        <div class="strength-rank-row">
          <span>${escapeHtml(ex.exercise)}</span>
          <strong>${fmtKg(ex.bestE1rm)}</strong>
        </div>`).join('') || '<div class="strength-empty">Ingen viktdata ännu.</div>';

      const prsHtml = (data.recentPrs || []).map(pr => `
        <div class="strength-pr">
          <div>
            <strong>${escapeHtml(pr.exercise)}</strong>
            <span>${escapeHtml(fmtDateStr(pr.date))} · ${escapeHtml(pr.reps || '')} @ ${fmtKg(pr.weight)}</span>
          </div>
          <b>${fmtKg(pr.e1rm)}</b>
        </div>`).join('') || '<div class="strength-empty">Inga nya personbästan i senaste loggen.</div>';

      const rowsHtml = exercises.map(ex => `
        <tr>
          <td>
            <strong>${escapeHtml(ex.exercise)}</strong>
            <span>${escapeHtml(fmtDateStr(ex.lastDate))}</span>
          </td>
          <td>${ex.sessions}</td>
          <td>${fmtVolume(ex.totalVolume)}</td>
          <td>${fmtKg(ex.currentE1rm)}</td>
          <td class="trend-${ex.trend}">${fmtSignedKg(ex.deltaE1rm)}</td>
        </tr>`).join('');

      el.innerHTML = `
        <div class="strength-analysis-grid">
          <div class="strength-metric-card">
            <span>Senaste 28 dagar</span>
            <strong>${summary.recentSessions28d || 0}</strong>
            <em>styrkepass</em>
          </div>
          <div class="strength-metric-card">
            <span>Total volym</span>
            <strong>${fmtVolume(summary.totalVolume)}</strong>
            <em>${summary.exerciseLogs || 0} loggade övningar</em>
          </div>
          <div class="strength-metric-card">
            <span>Övningsbredd</span>
            <strong>${summary.uniqueExercises || 0}</strong>
            <em>unika övningar</em>
          </div>
        </div>

        <div class="strength-analysis-layout">
          <div class="strength-panel">
            <div class="strength-panel-head">
              <h3>Volym per vecka</h3>
              <span>senaste 8 veckorna</span>
            </div>
            <div class="strength-week-chart">${weeksHtml}</div>
          </div>
          <div class="strength-panel">
            <div class="strength-panel-head">
              <h3>Starkaste lyften</h3>
              <span>estimerad 1RM</span>
            </div>
            <div class="strength-rank-list">${bestHtml}</div>
          </div>
        </div>

        <div class="strength-panel">
          <div class="strength-panel-head">
            <h3>Nya toppnoteringar</h3>
            <span>senaste loggade bästa per övning</span>
          </div>
          <div class="strength-pr-list">${prsHtml}</div>
        </div>

        <div class="strength-panel">
          <div class="strength-panel-head">
            <h3>Progression per övning</h3>
            <span>nuvarande e1RM mot förra loggen</span>
          </div>
          <div class="strength-table-wrap">
            <table class="strength-analysis-table">
              <thead><tr><th>Övning</th><th>Pass</th><th>Volym</th><th>e1RM</th><th>Trend</th></tr></thead>
              <tbody>${rowsHtml}</tbody>
            </table>
          </div>
        </div>`;
    } catch(e) {
      el.innerHTML = '<div class="no-sessions">Kunde inte ladda styrkeanalys: ' + escapeHtml(e.message) + '</div>';
    }
  }

  async function loadTodayWorkout() {
    const el = document.getElementById('strength-today');
    if (!el) return;
    const today = new Date().toLocaleDateString('sv-SE'); // YYYY-MM-DD, used as session id
    // Hitta dagens lift-pass i planen (samma vecko-/dagberäkning som renderTodaySession)
    const now = new Date();
    const jan4 = new Date(now.getFullYear(), 0, 4);
    const startDay = jan4.getDay() || 7;
    const monday = new Date(jan4); monday.setDate(jan4.getDate() - startDay + 1);
    const isoWeek = Math.ceil(((now - monday) / 86400000 + 1) / 7);
    const dow = (now.getDay() + 6) % 7;
    const todays = (PLAN_SESSIONS || []).filter(p => p.week === isoWeek && p.dow === dow);
    const lift = todays.find(p => p.type === 'lift');
    const dateLabel = now.toLocaleDateString('sv-SE', { weekday:'long', day:'numeric', month:'long' });

    // Om dagens Garmin-styrkepass redan synkat: logga direkt mot det (backend länkar
    // även ihop tidigare datum-loggade övningar med passet vid synk).
    let sessionId = today, linkedActivity = null;
    try {
      const sr = await fetch('/api/strength');
      const sess = (await sr.json()).sessions || [];
      const todayAct = sess.find(s => (s.date || '').slice(0, 10) === today);
      if (todayAct) { sessionId = String(todayAct.id); linkedActivity = todayAct; }
    } catch(e) {}
    const linkNote = linkedActivity
      ? `<div style="font-size:11px;color:var(--green);margin-top:8px;">✓ Kopplat till Garmin-aktivitet "${escapeHtml(linkedActivity.name)}" — övningar sparas på det passet.</div>`
      : `<div style="font-size:11px;color:var(--muted);margin-top:8px;">Inte synkat från Garmin än. Övningar loggas under dagens datum och kopplas automatiskt när klockan laddar upp passet.</div>`;

    const contextId = 'today-' + sessionId;
    activeStrengthRecommendations = [];
    let ctx;
    if (lift) {
      ctx = `<div style="background:var(--bg2);border:1px solid rgba(245,158,11,0.25);border-left:3px solid var(--amber);border-radius:12px;padding:16px 18px;margin-bottom:16px;">
        <div style="font-size:10px;font-weight:700;letter-spacing:0.12em;color:var(--amber);margin-bottom:6px;">DAGENS GYMPASS · ${dateLabel}</div>
        <div style="font-size:16px;font-weight:700;margin-bottom:4px;">${escapeHtml(lift.title || 'Styrka')}</div>
        ${lift.detail ? `<div style="font-size:13px;color:var(--muted2);line-height:1.5;">${escapeHtml(lift.detail)}</div>` : ''}
        ${strengthPrescriptionHtml(lift, contextId)}
        ${lift.ai_note ? `<div style="font-size:12px;color:var(--blue);margin-top:6px;">Coach: ${escapeHtml(lift.ai_note)}</div>` : ''}
        ${linkNote}
      </div>`;
    } else {
      const other = todays.find(p => p.type !== 'rest');
      ctx = `<div style="background:var(--bg2);border:1px solid var(--border);border-radius:12px;padding:16px 18px;margin-bottom:16px;">
        <div style="font-size:13px;color:var(--text);">Inget gympass planerat idag (${dateLabel}).</div>
        ${other ? `<div style="font-size:12px;color:var(--muted2);margin-top:4px;">Dagens plan: ${escapeHtml(other.title)}.</div>` : ''}
        ${linkNote}
      </div>`;
    }

    el.innerHTML = ctx + `
      <div class="add-ex-form">
        <div style="font-size:10px;font-family:'IBM Plex Mono',monospace;color:var(--muted);letter-spacing:0.12em;margin-bottom:12px;font-weight:500;">LOG EXERCISE</div>
        <div class="form-row">
          <input class="ex-input-name" id="ex-name-${contextId}" list="ex-suggestions" placeholder="Exercise, e.g. bench press" autocomplete="off">
          <input class="ex-input-sm" id="ex-sets-${contextId}" type="number" min="1" placeholder="Set">
          <input class="ex-input-sm" id="ex-reps-${contextId}" placeholder="Reps">
          <input class="ex-input-sm" id="ex-weight-${contextId}" type="number" step="0.5" placeholder="kg">
          <input class="ex-input-note" id="ex-note-${contextId}" placeholder="Note (optional)">
        </div>
        <button class="add-ex-btn" data-action="add-exercise" data-session="${escapeHtml(sessionId)}" data-context="${escapeHtml(contextId)}">+ Add</button>
      </div>
      <div style="font-size:10px;font-family:'IBM Plex Mono',monospace;color:var(--muted);letter-spacing:0.12em;margin:18px 0 10px;font-weight:500;">TODAY'S LOG</div>
      <div class="ex-list" id="exlist-${contextId}"></div>`;

    if (!document.getElementById('ex-suggestions')) {
      const dl = document.createElement('datalist');
      dl.id = 'ex-suggestions';
      dl.innerHTML = SUGGESTIONS.map(s => `<option value="${s}">`).join('');
      document.body.appendChild(dl);
    }
    // Enter i valfritt fält = lägg till snabbt under passet
    ['ex-name-','ex-reps-','ex-weight-','ex-note-'].forEach(p => {
      const inp = document.getElementById(p + contextId);
      if (inp) inp.addEventListener('keydown', e => { if (e.key === 'Enter') addExercise(sessionId, contextId); });
    });
    loadExercises(sessionId, contextId);
  }

  async function loadStrength() {
    const container = document.getElementById('strength-list');
    container.innerHTML = '<div style="color:var(--muted);font-size:13px;font-family:\'IBM Plex Mono\',monospace;">Loading sessions...</div>';
    try {
      const res = await fetch('/api/strength');
      const data = await res.json();
      const sessions = data.sessions || [];
      if (!sessions.length) {
        container.innerHTML = '<div class="no-sessions">No strength sessions found in Garmin. Sync data to update.</div>';
        return;
      }
      const today = new Date().toLocaleDateString('sv-SE');
      const initialSession = sessions.find(s => (s.date || '').slice(0, 10) === today) || sessions[0];
      container.innerHTML = sessions.map(s => `
        <div class="strength-session ${initialSession && s.id === initialSession.id ? 'open' : ''}" id="sess-${s.id}">
          <div class="strength-header" data-action="toggle-session" data-session="${escapeHtml(s.id)}">
            <div class="strength-header-left">
              <div class="strength-title">${escapeHtml(strengthSessionTitle(s))}</div>
              <div class="strength-meta">${fmtDateStr(s.date)} &nbsp; - &nbsp; ${fmtDur(s.duration)} &nbsp; - &nbsp; ${Math.round(s.calories||0)} kcal${s.avgHR?' &nbsp; - &nbsp;  '+Math.round(s.avgHR)+' bpm':''}</div>
            </div>
            <span class="strength-chevron">▾</span>
          </div>
          <div class="strength-body">
            <div class="ex-list" id="exlist-${s.id}"><div style="color:var(--muted);font-size:12px;font-family:'IBM Plex Mono',monospace;">Loading...</div></div>
            <div class="add-ex-form">
              <div style="font-size:10px;font-family:'IBM Plex Mono',monospace;color:var(--muted);letter-spacing:0.12em;margin-bottom:12px;font-weight:500;">LOG EXERCISE</div>
              <div class="form-row">
                <input class="ex-input-name" id="ex-name-${s.id}" list="ex-suggestions" placeholder="Exercise, e.g. bench press" autocomplete="off">
                <input class="ex-input-sm" id="ex-sets-${s.id}" type="number" min="1" placeholder="Set">
                <input class="ex-input-sm" id="ex-reps-${s.id}" placeholder="Reps">
                <input class="ex-input-sm" id="ex-weight-${s.id}" type="number" step="0.5" placeholder="kg">
                <input class="ex-input-note" id="ex-note-${s.id}" placeholder="Note (optional)">
              </div>
              <button class="add-ex-btn" data-action="add-exercise" data-session="${escapeHtml(s.id)}">+ Add</button>
            </div>
          </div>
        </div>`).join('');

      if (!document.getElementById('ex-suggestions')) {
        const dl = document.createElement('datalist');
        dl.id = 'ex-suggestions';
        dl.innerHTML = SUGGESTIONS.map(s => `<option value="${s}">`).join('');
        document.body.appendChild(dl);
      }
      if (initialSession) await loadExercises(initialSession.id);
    } catch(e) {
      container.innerHTML = '<div class="no-sessions">Error: ' + escapeHtml(e.message) + '</div>';
    }
  }

  async function toggleSession(id) {
    const el = document.getElementById('sess-' + id);
    const wasOpen = el.classList.contains('open');
    el.classList.toggle('open');
    if (!wasOpen) await loadExercises(id);
  }

  async function loadExercises(sessionId, contextId = sessionId) {
    const list = document.getElementById('exlist-' + contextId);
    if (!list) return;
    try {
      const res = await fetch('/api/strength/' + sessionId + '/exercises');
      const data = await res.json();
      renderExercises(sessionId, data.exercises || [], contextId);
    } catch(e) { list.innerHTML = '<div style="color:var(--red);font-size:12px;">Could not load exercises</div>'; }
  }

  function renderExercises(sessionId, exercises, contextId = sessionId) {
    const list = document.getElementById('exlist-' + contextId);
    if (!list) return;
    if (!exercises.length) {
      list.innerHTML = '<div style="color:var(--muted);font-size:12px;font-family:\'IBM Plex Mono\',monospace;padding:8px 0 12px;">No exercises logged yet.</div>';
      return;
    }
    list.innerHTML = exercises.map(ex => {
      const detail = [ex.sets ? ex.sets+'x' : '', ex.reps || '', ex.weight ? ex.weight+'kg' : '', ex.note || ''].filter(Boolean).join(' ');
      return `<div class="ex-row">
        <span class="ex-name">${escapeHtml(ex.exercise)}</span>
        <span class="ex-detail">${escapeHtml(detail)}</span>
        <button class="ex-del" data-action="delete-exercise" data-id="${Number(ex.id)}" data-session="${escapeHtml(sessionId)}" data-context="${escapeHtml(contextId)}" title="Ta bort">x</button>
      </div>`;
    }).join('');
  }

  async function addExercise(sessionId, contextId = sessionId) {
    const nameEl = document.getElementById('ex-name-' + contextId);
    const name   = nameEl.value.trim();
    const sets   = document.getElementById('ex-sets-'   + contextId).value;
    const reps   = document.getElementById('ex-reps-'   + contextId).value.trim();
    const weight = document.getElementById('ex-weight-' + contextId).value;
    const note   = document.getElementById('ex-note-'   + contextId).value.trim();
    if (!name) { nameEl.focus(); return; }
    await fetch('/api/strength/' + sessionId + '/exercises', {
      method: 'POST', headers: {'Content-Type':'application/json'},
      body: JSON.stringify({ exercise: name, sets: sets ? parseInt(sets) : null, reps, weight: weight ? parseFloat(weight) : null, note })
    });
    ['ex-name-','ex-sets-','ex-reps-','ex-weight-','ex-note-'].forEach(p => document.getElementById(p + contextId).value = '');
    await loadExercises(sessionId, contextId);
    await loadPlan();
    nameEl.focus();
  }

  async function deleteExercise(exId, sessionId, contextId = sessionId) {
    await fetch('/api/strength/exercises/' + exId, { method: 'DELETE' });
    await loadExercises(sessionId, contextId);
    await loadPlan();
  }
  document.getElementById('chat-input').addEventListener('keypress', e => { if (e.key === 'Enter') send(); });

  // ─── TOOLTIPS ───────────────────────────────────────────────
  const TIPS = {
    readiness: {
      title: 'Träningsberedskap',
      desc: 'Garmins samlade uppskattning av hur redo kroppen är för hård träning, baserat på HRV, sömn, stress och aktivitetshistorik.',
      ranges: [
        { label: '75 - 100', val: 'Toppform – kvalitetspass ok', col: 'var(--green)' },
        { label: '50 - 74',  val: 'Redo – normal träning',  col: 'var(--amber)' },
        { label: '25 - 49',  val: 'Återhämtar',         col: 'var(--amber)' },
        { label: '0 - 24',   val: 'Låg – vila/Z2 max',      col: 'var(--red)'   },
      ]
    },
    hrv: {
      title: 'HRV – Hjärtfrekvensvariabilitet',
      desc: 'Variationen mellan hjärtslag under sömnen. Hög HRV betyder att kroppen är i parasympatiskt läge och återhämtar sig väl. Mycket individuellt – jämför alltid mot ditt eget snitt.',
      ranges: [
        { label: '> 100% av snitt', val: 'Utmärkt återhämtning', col: 'var(--green)' },
        { label: '80 - 100%',       val: 'Bra',                  col: 'var(--green)' },
        { label: '50 - 79%',        val: 'Acceptabel',           col: 'var(--amber)' },
        { label: '< 50%',           val: 'Låg – vila rekommenderas', col: 'var(--red)' },
      ]
    },
    rhr: {
      title: 'Vilopuls (bpm)',
      desc: 'Lägsta hjärtfrekvens under vila/sömn. Lägre betyder oftast bättre form. Sjunker ofta med aerob träning. En plötslig ökning kan signalera sjukdom eller överträning.',
      ranges: [
        { label: '< 45 bpm',  val: 'Elitidrottare',    col: 'var(--green)' },
        { label: '45 - 55',   val: 'Vältränad',        col: 'var(--green)' },
        { label: '56 - 70',   val: 'Genomsnitt',       col: 'var(--amber)' },
        { label: '> 70 bpm',  val: 'Förhöjd',          col: 'var(--red)'   },
      ]
    },
    vo2max: {
      title: 'VO2max (ml/kg/min)',
      desc: 'Maximalt syreupptag – det viktigaste måttet på kondition. Garmin uppskattar det via löpdata. Ökar gradvis med aerob träning.',
      ranges: [
        { label: '> 60',   val: 'Elitlöpare',        col: 'var(--green)' },
        { label: '55 - 60', val: 'Utmärkt (män 20–35)', col: 'var(--green)' },
        { label: '46 - 54', val: 'Bra – över snitt', col: 'var(--amber)' },
        { label: '< 46',   val: 'Snitt/under',  col: 'var(--red)'   },
      ]
    },
    'sleep-score': {
      title: 'Sömnpoäng',
      desc: 'Garmins samlade uppskattning av sömnkvalitet baserat på längd, sömncykler, HRV och andning under natten.',
      ranges: [
        { label: '90 - 100', val: 'Utmärkt',  col: 'var(--green)' },
        { label: '80 - 89',  val: 'Bra',      col: 'var(--green)' },
        { label: '60 - 79',  val: 'Acceptabel', col: 'var(--amber)' },
        { label: '< 60',     val: 'Dålig – prioritera sömn', col: 'var(--red)' },
      ]
    },
    deep: {
      title: 'Djupsömn (slow-wave)',
      desc: 'Den mest fysiskt återuppbyggande sömnfasen – kroppen reparerar muskler och vävnad. Särskilt viktig för idrottare. Minskar naturligt med åldern.',
      ranges: [
        { label: '20 - 25%', val: 'Utmärkt (ca 1,5–2h)', col: 'var(--green)' },
        { label: '13 - 19%', val: 'Normal',              col: 'var(--green)' },
        { label: '8 - 12%',  val: 'Något lågt',             col: 'var(--amber)' },
        { label: '< 8%',     val: 'För lite',             col: 'var(--red)'   },
      ]
    },
    rem: {
      title: 'REM-sömn',
      desc: 'Rapid Eye Movement-sömn – hjärnan befäster minnen och bearbetar intryck. Viktig för mental återhämtning, motorisk inlärning och motivation.',
      ranges: [
        { label: '20 - 25%', val: 'Utmärkt (ca 1,5–2h)',  col: 'var(--green)'  },
        { label: '15 - 19%', val: 'Normal',               col: 'var(--green)'  },
        { label: '10 - 14%', val: 'Något lågt',              col: 'var(--amber)'  },
        { label: '< 10%',    val: 'För lite',              col: 'var(--red)'    },
      ]
    },
    'hrv-sleep': {
      title: 'HRV under sömn (ms)',
      desc: 'Genomsnittlig HRV mätt under hela natten. Stabilare än dagtidsmätningar. Stiger oftast under djupsömn och REM. Absoluta värden varierar mycket mellan personer.',
      ranges: [
        { label: 'Vältränad', val: 'Vanligtvis 55–100+ ms',    col: 'var(--green)' },
        { label: 'Genomsnitt', val: 'Vanligtvis 25–55 ms',      col: 'var(--amber)' },
        { label: 'Trend',      val: 'Jämför med ditt snitt', col: 'var(--blue)'  },
        { label: 'Obs',       val: 'Plötsligt fall = vila', col: 'var(--red)' },
      ]
    },
    bb: {
      title: 'Kroppsbatteri',
      desc: 'Garmins uppskattning av energireserv baserat på HRV, stress och sömn. Laddas under sömn och vila, töms av aktivitet och stress. Bra vägledning för om du klarar ett hårt pass.',
      ranges: [
        { label: '75 - 100', val: 'Hög energi – kör hårt',   col: 'var(--green)' },
        { label: '50 - 74',  val: 'Måttlig – normal träning', col: 'var(--green)' },
        { label: '25 - 49',  val: 'Låg – ta det lugnt',     col: 'var(--amber)' },
        { label: '0 - 24',   val: 'Tom – prioritera vila',   col: 'var(--red)'   },
      ]
    },
    stress: {
      title: 'Stressnivå',
      desc: 'Garmin uppskattar stress från HRV-variation under dagen. Hög stress aktiverar det sympatiska nervsystemet och bromsar återhämtningen. Inkluderar fysisk och mental stress.',
      ranges: [
        { label: '0 - 25',  val: 'Vila / avslappnad',     col: 'var(--green)' },
        { label: '26 - 50', val: 'Låg stress',            col: 'var(--green)' },
        { label: '51 - 75', val: 'Måttlig stress',        col: 'var(--amber)' },
        { label: '76 - 100', val: 'Hög stress – bromsar återhämtning', col: 'var(--red)' },
      ]
    },
    spo2: {
      title: 'SpO2 – Syremättnad (%)',
      desc: 'Andel hemoglobin i blodet som bär syre. Mäts med pulsoximeter. Normalt stabilt hos friska – sjunker på hög höjd eller vid andningsproblem.',
      ranges: [
        { label: '97 - 100%', val: 'Optimal',           col: 'var(--green)' },
        { label: '95 - 96%',  val: 'Normal',            col: 'var(--green)' },
        { label: '90 - 94%',  val: 'Något lågt – bevaka', col: 'var(--amber)' },
        { label: '< 90%',     val: 'Kritiskt lågt',      col: 'var(--red)'   },
      ]
    },
    resp: {
      title: 'Andningsfrekvens (andetag/min)',
      desc: 'Andetag per minut i vila. Lägre frekvens är vanligt hos vältränade. Förhöjd andning under sömn kan signalera sjukdom eller dålig sömnkvalitet.',
      ranges: [
        { label: '8 - 12/min',  val: 'Vältränad idrottare', col: 'var(--green)' },
        { label: '12 - 16/min', val: 'Normal vuxen',       col: 'var(--green)' },
        { label: '17 - 20/min', val: 'Något förhöjd',        col: 'var(--amber)' },
        { label: '> 20/min',    val: 'Förhöjd – undersök',  col: 'var(--red)'   },
      ]
    },
  };

  // Skapa tooltip-elementet
  const tipBox = document.createElement('div');
  tipBox.className = 'tip-box';
  document.body.appendChild(tipBox);

  let tipTimeout;

  function showTip(key, rect) {
    const data = TIPS[key];
    if (!data) return;
    clearTimeout(tipTimeout);

    const rangesHtml = data.ranges.map(r =>
      `<div class="tip-range">
        <span class="tip-range-label">${r.label}</span>
        <span class="tip-range-val" style="color:${r.col}">${r.val}</span>
      </div>`
    ).join('');

    tipBox.innerHTML = `
      <div class="tip-title">${data.title}</div>
      <div class="tip-desc">${data.desc}</div>
      <div class="tip-ranges">${rangesHtml}</div>`;

    // Positionera - försök visa under kortet, annars ovan
    const vw = window.innerWidth;
    const vh = window.innerHeight;
    const W  = 260;
    const H  = tipBox.offsetHeight || 180;

    let left = rect.left;
    let top  = rect.bottom + 8;

    if (left + W > vw - 12) left = vw - W - 12;
    if (left < 12) left = 12;
    if (top + H > vh - 12) top = rect.top - H - 8;

    tipBox.style.left = left + 'px';
    tipBox.style.top  = top  + 'px';
    tipBox.classList.add('visible');
  }

  function hideTip() {
    tipTimeout = setTimeout(() => tipBox.classList.remove('visible'), 80);
  }

  document.querySelectorAll('.has-tip').forEach(card => {
    card.style.cursor = 'default';
    card.addEventListener('mouseenter', e => {
      showTip(card.dataset.tip, card.getBoundingClientRect());
    });
    card.addEventListener('mouseleave', hideTip);
  });

  // Kalender-pills: visa detalj-text via tipBox
  function showFreeTip(text, rect) {
    clearTimeout(tipTimeout);
    tipBox.innerHTML = `<div class="tip-desc" style="margin:0">${escapeHtml(text)}</div>`;
    const vw = window.innerWidth;
    const vh = window.innerHeight;
    const W  = 240;
    const H  = tipBox.offsetHeight || 70;
    let left = rect.left;
    let top  = rect.bottom + 8;
    if (left + W > vw - 12) left = vw - W - 12;
    if (left < 12) left = 12;
    if (top + H > vh - 12) top = rect.top - H - 8;
    tipBox.style.left = left + 'px';
    tipBox.style.top  = top  + 'px';
    tipBox.classList.add('visible');
  }

  document.addEventListener('mouseover', e => {
    const pill = e.target.closest('[data-freetip]');
    if (pill) showFreeTip(pill.dataset.freetip, pill.getBoundingClientRect());
  });
  document.addEventListener('mouseout', e => {
    if (e.target.closest('[data-freetip]')) hideTip();
  });

  // ─── KALENDER ───────────────────────────────────────────────
  // PLAN_SESSIONS laddas dynamiskt från DB via /api/plan
  // Fallback till hårdkodad array om API-anropet misslyckas
  let PLAN_SESSIONS = [];

  async function sendCoachRequest() {
    const input = document.getElementById('coach-request-input');
    const btn = document.getElementById('coach-request-btn');
    const out = document.getElementById('coach-request-result');
    const text = (input.value || '').trim();
    if (!text) { input.focus(); return; }
    btn.disabled = true; btn.textContent = 'Tänker…';
    out.style.display = 'block';
    out.innerHTML = '<span style="font-size:12px;color:var(--muted);font-family:\'IBM Plex Mono\',monospace;">Coachen bygger om ditt schema…</span>';
    try {
      const res = await fetch('/api/plan/request', {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ text })
      });
      const d = await res.json();
      if (!res.ok || d.error) throw new Error(d.error || 'Kunde inte justera planen');
      const r = d.result || {};
      const n = r.changes || 0;
      const msg = r.summary || (n ? 'Planen justerad.' : 'Inga ändringar behövdes.');
      out.innerHTML = `
        <div style="font-size:11px;font-weight:700;letter-spacing:0.04em;color:var(--green);margin-bottom:6px;">Planen justerad · ${n} ändring${n === 1 ? '' : 'ar'}</div>
        <div style="font-size:13px;line-height:1.5;color:var(--text);">${escapeHtml(msg)}</div>`;
      input.value = '';
      // Uppdatera schemat överallt (kalender, dagens pass, cockpit)
      loadPlan();
    } catch(e) {
      out.innerHTML = `<span style="font-size:12px;color:var(--red);">${escapeHtml(e.message)}</span>`;
    } finally {
      btn.disabled = false; btn.textContent = 'Justera plan';
    }
  }


  function translatePlanText(value) {
    // Backend levererar redan svenska – ingen översättning behövs.
    return value || '';
  }

  function normalizePlanSession(session) {
    return {
      ...session,
      title: translatePlanText(session.title),
      detail: translatePlanText(session.detail),
      ai_note: translatePlanText(session.ai_note),
      strength_recommendations: Array.isArray(session.strength_recommendations) ? session.strength_recommendations : [],
      strength_recommendation_text: session.strength_recommendation_text || '',
    };
  }

  function compactCalendarText(value, maxLen = 120) {
    let text = (value || '').replace(/\s+/g, ' ').trim();
    if (!text) return '';
    const firstUseful = text.split(/\s+[–—-]\s+/).find(part => part.trim().length >= 8);
    text = firstUseful || text;
    const sentence = text.match(/^(.+?[.!?])\s/);
    if (sentence && sentence[1].length <= maxLen) text = sentence[1];
    if (text.length <= maxLen) return text;
    return text.slice(0, maxLen - 1).trimEnd().replace(/[,\-–—;:]+$/, '') + '…';
  }

  function activityDateKey(activity) {
    return (activity.startTimeLocal || activity.beginTimestamp || activity.date || '').slice(0, 10);
  }

  function calendarActivityType(activity) {
    const key = String(activity.activityType?.typeKey || activity.type || '').toLowerCase();
    if (/strength|fitness|weight/.test(key)) return 'lift';
    if (/race/.test(key)) return 'race';
    if (/track|running|treadmill|trail/.test(key)) return 'run';
    return 'rest';
  }

  function calendarActivityLabel(activity) {
    const name = activity.activityName || activity.name || activity.activityType?.typeKey || 'Garmin-aktivitet';
    const km = activity.distance ? ' · ' + (activity.distance / 1000).toFixed(1) + ' km' : '';
    return name + km;
  }

  function activitiesByDate() {
    const map = {};
    (recentActivities || []).forEach(activity => {
      const key = activityDateKey(activity);
      if (!key) return;
      (map[key] ||= []).push(activity);
    });
    return map;
  }

  function calendarActualPills(dayActivities, plannedSession) {
    if (!dayActivities.length) return '';
    const runs = dayActivities.filter(a => calendarActivityType(a) === 'run');
    const lifts = dayActivities.filter(a => calendarActivityType(a) === 'lift');
    const totalRunKm = runs.reduce((sum, a) => sum + ((a.distance || 0) / 1000), 0);
    const totalSec = dayActivities.reduce((sum, a) => sum + (a.duration || a.elapsedDuration || 0), 0);
    const minutes = totalSec ? Math.round(totalSec / 60) : null;

    if (plannedSession?.type === 'lift' && lifts.length) {
      const label = plannedSession.title || 'Styrkepass';
      const tip = ['Garmin', label, minutes != null ? minutes + ' min' : ''].filter(Boolean).join(' - ');
      return `<span class="cal-session-pill csp-lift csp-done csp-actual" data-freetip="${escapeHtml(tip)}">${escapeHtml(label)}</span>`;
    }

    if (runs.length) {
      const interval = runs.find(a => a.calendarSummary?.kind === 'interval');
      const label = interval?.calendarSummary?.label
        ? `${interval.calendarSummary.label}${totalRunKm ? ' · ' + totalRunKm.toFixed(1) + ' km' : ''}`
        : runs.length > 1
          ? `${runs.length} löpdelar${totalRunKm ? ' · ' + totalRunKm.toFixed(1) + ' km' : ''}`
          : calendarActivityLabel(runs[0]);
      const names = runs.map(calendarActivityLabel).join(' - ');
      const tip = ['Garmin', names, minutes != null ? minutes + ' min' : ''].filter(Boolean).join(' - ');
      return `<span class="cal-session-pill csp-run csp-done csp-actual" data-freetip="${escapeHtml(tip)}">${escapeHtml(label)}</span>`;
    }

    return dayActivities.map(activity => {
      const actualType = calendarActivityType(activity);
      const cls = actualType === 'lift' ? 'csp-lift' : actualType === 'race' ? 'csp-race' : 'csp-rest';
      const label = calendarActivityLabel(activity);
      const seconds = activity.duration || activity.elapsedDuration || 0;
      const mins = seconds ? Math.round(seconds / 60) : null;
      const tip = ['Garmin', label, mins != null ? mins + ' min' : ''].filter(Boolean).join(' - ');
      return `<span class="cal-session-pill ${cls} csp-done csp-actual" data-freetip="${escapeHtml(tip)}">${escapeHtml(label)}</span>`;
    }).join('');
  }

  function renderTodaySession() {
    const card  = document.getElementById('today-session-card');
    const dot   = document.getElementById('today-session-dot');
    const title = document.getElementById('today-session-title');
    const detail= document.getElementById('today-session-detail');
    const km    = document.getElementById('today-session-km');
    const type  = document.getElementById('today-session-type');
    if (!card || !dot || !title || !detail || !km || !type) return;

    const typeColors = { run:'var(--green)', easy:'var(--muted2)', lift:'var(--orange)', race:'var(--red)', rest:'var(--muted)' };
    const typeLabels = { run:'LÖPNING', easy:'LUGN LÖPNING', lift:'STYRKA', race:'LOPP', rest:'VILA' };

    // ── 1. Check today's completed Garmin activities ──────────────────────
    const todayKey = localDateKey(new Date());
    const todayActs = recentActivities.filter(a => {
      const d = (a.startTimeLocal || a.beginTimestamp || '').slice(0, 10);
      return d === todayKey;
    });

    if (todayActs.length > 0) {
      // Merge all into one combined session
      const totalKm  = todayActs.reduce((s, a) => s + ((a.distance || 0) / 1000), 0);
      const totalSec = todayActs.reduce((s, a) => s + (a.duration || a.elapsedDuration || 0), 0);
      const totalMin = Math.round(totalSec / 60);

      // Pick dominant type from the longest activity
      const longest = todayActs.reduce((a, b) => (a.distance||0) >= (b.distance||0) ? a : b);
      const typeKey  = longest.activityType?.typeKey || '';
      let   planType = 'run';
      if (/strength|fitness_equipment|weight/i.test(typeKey)) planType = 'lift';
      else if (/track/i.test(typeKey))                         planType = 'run';

      const col = typeColors[planType] || 'var(--green)';

      // Build detail: individual activity names on one line
      const actNames = todayActs.map(a => {
        const n = a.activityName || a.name || (a.activityType?.typeKey || 'activity');
        const km2 = a.distance ? ' ' + (a.distance / 1000).toFixed(1) + ' km' : '';
        return n + km2;
      });
      const detailStr = actNames.join('  ·  ');

      // Time string
      const h = Math.floor(totalMin / 60), m = totalMin % 60;
      const timeStr = h > 0 ? `${h}h ${m}m` : `${m} min`;

      dot.style.background   = col;
      card.style.borderColor = col.replace('var(--','rgba(').replace(')',',0.25)');
      title.textContent      = todayActs.length > 1
        ? `${todayActs.length} aktiviteter  —  ${timeStr} totalt`
        : (todayActs[0].activityName || todayActs[0].name || 'Aktivitet idag');
      title.style.color      = col;
      detail.textContent     = detailStr;
      km.textContent         = totalKm > 0 ? totalKm.toFixed(1) + ' km' : timeStr;
      km.style.color         = col;
      type.textContent       = 'KLART';
      return;
    }

    // ── 2. Fall back to today's planned session ───────────────────────────
    const PLAN_YEAR = 2026;
    let s = null;
    for (const p of PLAN_SESSIONS) {
      const mon = getMondayOfISOWeek(p.week, PLAN_YEAR);
      const sessionDate = new Date(mon);
      sessionDate.setDate(mon.getDate() + p.dow);
      if (localDateKey(sessionDate) !== todayKey) continue;
      if (!s || (p.status === 'planned' && s.status !== 'planned')) s = p;
    }

    if (!s) {
      title.textContent    = 'Vilodag';
      detail.textContent   = 'Inget pass schemalagt idag';
      km.textContent       = '';
      type.textContent     = 'REST';
      dot.style.background = 'var(--muted)';
      card.style.borderColor = '';
      title.style.color    = '';
      return;
    }

    const col = typeColors[s.type] || 'var(--green)';
    dot.style.background   = col;
    card.style.borderColor = col.replace('var(--','rgba(').replace(')',',0.25)');
    title.textContent      = s.title;
    title.style.color      = col;
    detail.textContent     = (s.type === 'lift' && s.strength_recommendation_text) || s.detail || '';
    km.textContent         = s.km > 0 ? s.km + ' km' : '';
    km.style.color         = col;
    const statusSuffix = s.status && s.status !== 'planned' ? '  -  ' + s.status.toUpperCase() : '';
    type.textContent       = (typeLabels[s.type] || String(s.type || 'PLAN').toUpperCase()) + statusSuffix;
  }

  async function reseedPlan() {
    const btn = document.getElementById('reseed-btn');
    const res = document.getElementById('reseed-result');
    if (btn) { btn.textContent = 'Återställer…'; btn.disabled = true; }
    if (res) res.style.display = 'none';
    try {
      const r = await fetch('/api/plan/reseed', { method: 'POST' });
      const d = await r.json();
      if (d.error) throw new Error(d.error);
      if (res) { res.textContent = `✓ ${d.sessions} pass laddade på svenska.`; res.style.display = 'block'; res.style.color = 'var(--green)'; }
      await loadPlan();
    } catch(e) {
      if (res) { res.textContent = 'Fel: ' + e.message; res.style.display = 'block'; res.style.color = 'var(--red)'; }
    } finally {
      if (btn) { btn.textContent = 'Återställ plan till svenska (reseed)'; btn.disabled = false; }
    }
  }

  async function loadPlan() {
    try {
      const r = await fetch('/api/plan');
      const d = await r.json();
      if (d.sessions && d.sessions.length > 0) {
        PLAN_SESSIONS = d.sessions.map(normalizePlanSession);
        buildCalendar();
        renderTodaySession();
        safeRenderTrainingCockpit();
      }
    } catch(e) {
      console.warn('Plan fetch failed', e);
    }
  }
  loadPlan();

  function getISOWeek(date) {
    const d = new Date(Date.UTC(date.getFullYear(), date.getMonth(), date.getDate()));
    const dayNum = d.getUTCDay() || 7;
    d.setUTCDate(d.getUTCDate() + 4 - dayNum);
    const yearStart = new Date(Date.UTC(d.getUTCFullYear(), 0, 1));
    return Math.ceil((((d - yearStart) / 86400000) + 1) / 7);
  }

  function getMondayOfISOWeek(week, year) {
    const Jan4 = new Date(year, 0, 4);
    const startDay = Jan4.getDay() || 7;
    const monday = new Date(Jan4);
    monday.setDate(Jan4.getDate() - startDay + 1 + (week - 1) * 7);
    return monday;
  }

  let calendarView = 'current';

  // Lokal datumnyckel "YYYY-MM-DD" - toISOString() räknar om till UTC, vilket
  // gör att lokal midnatt i svensk tidszon hamnar på föregående dygn.
  function localDateKey(d) {
    const m = String(d.getMonth() + 1).padStart(2, '0');
    const day = String(d.getDate()).padStart(2, '0');
    return d.getFullYear() + '-' + m + '-' + day;
  }

  function buildCalendar() {
    const container = document.getElementById('cal-container');
    if (!container) return;
    container.innerHTML = '';

    const today = new Date();
    today.setHours(0,0,0,0);
    const dayNames = ['Mån','Tis','Ons','Tor','Fre','Lör','Sön'];
    const monthNames = ['jan','feb','mar','apr','maj','jun','jul','aug','sep','okt','nov','dec'];

    // Veckospann härleds från användarens egen plan; utan plan visas veckorna
    // runt dagens datum (Garmin-aktiviteter och kalenderhändelser syns ändå).
    const planWeeks = PLAN_SESSIONS.map(s => s.week);
    const isoNow = getISOWeek(today);
    const START_WEEK = planWeeks.length ? Math.min(...planWeeks) : Math.max(1, isoNow - 2);
    const END_WEEK   = planWeeks.length ? Math.max(...planWeeks) : Math.min(52, isoNow + 4);
    const YEAR       = today.getFullYear();
    const currentWeek = Math.min(Math.max(isoNow, START_WEEK), END_WEEK);
    const currentTab = document.getElementById('cal-tab-current');
    const pastTab = document.getElementById('cal-tab-past');

    if (currentTab && pastTab) {
      const showingPast = calendarView === 'past';
      currentTab.classList.toggle('active', !showingPast);
      currentTab.setAttribute('aria-selected', String(!showingPast));
      pastTab.classList.toggle('active', showingPast);
      pastTab.setAttribute('aria-selected', String(showingPast));
    }

    const visibleWeeks = [];
    for (let w = START_WEEK; w <= END_WEEK; w++) {
      const isPastWeek = w < currentWeek;
      if ((calendarView === 'past' && isPastWeek) || (calendarView !== 'past' && !isPastWeek)) {
        visibleWeeks.push(w);
      }
    }
    if (calendarView === 'past') visibleWeeks.reverse();

    if (!visibleWeeks.length) {
      const empty = document.createElement('div');
      empty.className = 'cal-empty';
      empty.textContent = calendarView === 'past'
        ? 'Inga tidigare veckor i planen än.'
        : 'Inga kommande veckor kvar i planen.';
      container.appendChild(empty);
      return;
    }

    const actualByDate = activitiesByDate();

    // Index sessions by week+dow — keep only the best one per slot.
    // Priority: completed > planned/adjusted > skipped/missed
    const statusPriority = s => {
      if (s.status === 'completed') return 0;
      if (s.status === 'planned')   return 1;
      if (s.status === 'rescheduled') return 1;
      if (s.status === 'missed')    return 2;
      if (s.status === 'skipped')   return 2;
      return 1;
    };
    const sessionMap = {};
    PLAN_SESSIONS.forEach(s => {
      const key = s.week + '-' + s.dow;
      const existing = sessionMap[key];
      if (!existing || statusPriority(s) < statusPriority(existing)) {
        sessionMap[key] = s;
      }
    });

    visibleWeeks.forEach((w, idx) => {
      const monday = getMondayOfISOWeek(w, YEAR);

      // Indexera Google Calendar-events per datum för denna vecka
      // Flerdagarsevent expanderas så varje dag i spannet får en entry
      const gcalMap = {}; // 'YYYY-MM-DD' -> [events]
      gcalEvents.forEach(ev => {
        const startKey = gcalDateKey(ev.start);
        const endRaw   = gcalDateKey(ev.end);
        // För heldagsevent är end exklusivt (Google-format), dra tillbaka ett dygn
        let endKey = endRaw;
        if (ev.allDay && endRaw > startKey) {
          const d = new Date(endRaw);
          d.setDate(d.getDate() - 1);
          endKey = d.toISOString().substring(0, 10);
        }
        // Lägg eventet på varje dag från start t.o.m. end
        const cur = new Date(startKey);
        const last = new Date(endKey);
        while (cur <= last) {
          const key = cur.toISOString().substring(0, 10);
          if (!gcalMap[key]) gcalMap[key] = [];
          gcalMap[key].push(ev);
          cur.setDate(cur.getDate() + 1);
        }
      });

      // Räkna pass och km (ett pass per dag efter dedup)
      let runCount = 0, liftCount = 0, raceCount = 0, totalKm = 0, workCount = 0;
      for (let d = 0; d < 7; d++) {
        const s = sessionMap[w + '-' + d];
        if (s) {
          if (s.type === 'run' || s.type === 'easy') runCount++;
          if (s.type === 'lift')  liftCount++;
          if (s.type === 'race')  raceCount++;
          totalKm += s.km || 0;
        }
        const dayDate = new Date(monday);
        dayDate.setDate(monday.getDate() + d);
        const dayKey = localDateKey(dayDate);
        workCount += (gcalMap[dayKey] || []).length;
      }

      const sunday = new Date(monday);
      sunday.setDate(monday.getDate() + 6);
      const rangeStr =
        monday.getDate() + ' ' + monthNames[monday.getMonth()] +
        ' - ' +
        sunday.getDate() + ' ' + monthNames[sunday.getMonth()];

      // Veckokort
      const weekEl = document.createElement('div');
      weekEl.className = 'cal-week';

      // Header
      const headerEl = document.createElement('div');
      headerEl.className = 'cal-week-header';
      let badgesHtml = '';
      if (runCount)  badgesHtml += `<span class="cal-week-badge cwb-run"> ${runCount} run</span>`;
      if (liftCount) badgesHtml += `<span class="cal-week-badge cwb-lift"> ${liftCount} strength</span>`;
      if (raceCount) badgesHtml += `<span class="cal-week-badge cwb-race"> race</span>`;
      if (workCount) badgesHtml += `<span class="cal-week-badge cwb-work"> ${workCount} work</span>`;
      if (totalKm > 0) badgesHtml += `<span class="cal-week-badge cwb-km">~${totalKm} km</span>`;
      headerEl.innerHTML = `
        <span class="cal-week-num">V.${w}</span>
        <span class="cal-week-range">${rangeStr}</span>
        <div class="cal-week-badges">${badgesHtml}</div>`;
      weekEl.appendChild(headerEl);

      // Dagar
      const daysEl = document.createElement('div');
      daysEl.className = 'cal-days';

      for (let d = 0; d < 7; d++) {
        const date = new Date(monday);
        date.setDate(monday.getDate() + d);
        date.setHours(0,0,0,0);

        const isToday = date.getTime() === today.getTime();
        const isPast  = date < today;

        const dayEl = document.createElement('div');
        dayEl.className = 'cal-day' + (isToday ? ' today' : '') + (isPast ? ' past' : '');

        // Google Calendar-events för denna dag (visas först)
        const dateKey = localDateKey(date);
        const dayGcal = gcalMap[dateKey] || [];
        const dayActivities = actualByDate[dateKey] || [];

        let pillsHtml = '';
        dayGcal.forEach(ev => {
          const timeStr = ev.allDay ? 'Heldag' : fmtEventTime(ev.start) + '-' + fmtEventTime(ev.end);
          const tip = `${ev.title}  -  ${timeStr}${ev.location ? '  -  ' + ev.location : ''}`;
          pillsHtml += `<span class="cal-session-pill csp-work" data-freetip="${escapeHtml(tip)}"> ${escapeHtml(ev.title)}</span>`;
        });

        const s = sessionMap[w + '-' + d];
        pillsHtml += calendarActualPills(dayActivities, s);
        if (s && !dayActivities.length) {
          const cls = s.type === 'run' ? 'csp-run' : s.type === 'easy' ? 'csp-easy' : s.type === 'lift' ? 'csp-lift' : s.type === 'race' ? 'csp-race' : 'csp-rest';
          const compactDetail = compactCalendarText(s.detail);
          const strengthDetail = s.type === 'lift'
            ? compactCalendarText(s.strength_recommendation_text, 180)
            : '';
          const isModified = s.ai_note && s.status === 'planned' && s.modified_at;
          const statusNote = s.status === 'missed'      ? ' - Missed'
                           : s.status === 'skipped'     ? ' - Skipped'
                           : s.status === 'completed'   ? ' - Done'
                           : s.status === 'rescheduled' ? ' - Rescheduled'
                           : isModified                 ? ' - Adjusted'
                           : '';
          const tipText = [s.title, strengthDetail || compactDetail, statusNote.trim()].filter(Boolean).join(' - ');
          const opacity = s.status === 'missed' || s.status === 'skipped' ? 'opacity:0.45;text-decoration:line-through;' : '';
          const modCls  = isModified ? ' csp-modified' : '';
          const doneCls = s.status === 'completed' ? ' csp-done' : '';
          pillsHtml += `<span class="cal-session-pill ${cls}${modCls}${doneCls}" style="${opacity}" data-freetip="${escapeHtml(tipText)}">${escapeHtml(s.title)}${escapeHtml(statusNote)}</span>`;
        }

        dayEl.innerHTML = `
          <div class="cal-day-header">
            <span class="cal-day-name">${dayNames[d]}</span>
            <span class="cal-day-num">${date.getDate()}</span>
          </div>
          <div class="cal-session-list">${pillsHtml}</div>`;
        daysEl.appendChild(dayEl);
      }

      weekEl.appendChild(daysEl);
      container.appendChild(weekEl);
    });
  }

  function setCalendarView(view) {
    calendarView = view === 'past' ? 'past' : 'current';
    buildCalendar();
  }

  // ─── GOOGLE CALENDAR ────────────────────────────────────────
  let gcalEvents = [];   // { title, start, end, allDay }

  function gcalDateKey(isoStr) {
    // Returnerar "YYYY-MM-DD" oavsett om det är dateTime eller date
    return isoStr ? isoStr.substring(0, 10) : '';
  }

  function fmtEventTime(isoStr) {
    if (!isoStr || isoStr.length === 10) return 'Heldag';
    try {
      const d = new Date(isoStr);
      return d.toLocaleTimeString('sv-SE', { hour:'2-digit', minute:'2-digit' });
    } catch { return ''; }
  }

  async function checkGcalStatus() {
    try {
      const r = await fetch('/api/calendar/status');
      const d = await r.json();
      if (d.hasToken) await syncGcal();
    } catch(e) {}
  }

  async function syncGcal() {
    const syncIds = ['gcal-sync-btn', 'mobile-gcal-sync-btn'];
    setButtons(syncIds, 'Synkar…', 'var(--blue)', true);
    try {
      const r = await fetch('/api/calendar');
      const d = await r.json();
      if (!d.ok) throw new Error(d.error || 'Fel');
      gcalEvents = d.events || [];
      setButtons(syncIds, 'Synkad', 'var(--green)', true);
      setTimeout(() => setButtons(syncIds, 'Synka kalender', '', false), 2500);
      buildCalendar();
      renderTodaySession();
      if (document.getElementById('page-sleep').classList.contains('active')) loadSleepCoach();
    } catch(e) {
      setButtons(syncIds, 'Försök igen', 'var(--red)', false);
    }
  }

  // Bygg kalendern direkt + när Plan-fliken öppnas
  buildCalendar();
  renderTodaySession();
  safeRenderTrainingCockpit();
  if (document.getElementById('page-upcoming').classList.contains('active')) {
    checkGcalStatus();
  }
