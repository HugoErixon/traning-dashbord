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
let currentUsername = '';
let garminConnected = false;
let garminMfaStateId = null;
let stravaConfigured = false;
let stravaConnected = false;
let stravaAthlete = null;
let calendarConnected = false;
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
  currentUsername = data.username || '';
  garminConnected = !!data.garminConnected;
  stravaConfigured = !!data.stravaConfigured;
  stravaConnected = !!data.stravaConnected;
  stravaAthlete = data.stravaAthlete || null;
  const usersBtn = document.getElementById('users-btn');
  if (usersBtn) usersBtn.style.display = currentUserIsAdmin ? '' : 'none';
  const mobileUsersBtn = document.getElementById('mobile-users-btn');
  if (mobileUsersBtn) mobileUsersBtn.style.display = currentUserIsAdmin ? '' : 'none';
  const navClimate = document.getElementById('nav-climate');
  if (navClimate) navClimate.style.display = currentUserIsAdmin ? '' : 'none';
  const settingsClimate = document.getElementById('settings-climate-link');
  if (settingsClimate) settingsClimate.style.display = currentUserIsAdmin ? '' : 'none';
  const settingsUsers = document.getElementById('settings-users-link');
  if (settingsUsers) settingsUsers.style.display = currentUserIsAdmin ? '' : 'none';
  updateGarminSidebar();
  updateStravaSidebar();
  loadSettingsPage();
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
  const inputStyle = "width:100%;background:var(--bg3);border:1px solid var(--border2);border-radius:8px;padding:11px 14px;color:var(--text);font-family:'IBM Plex Sans',sans-serif;font-size:14px;outline:none;margin-bottom:10px;box-sizing:border-box;";
  document.body.insertAdjacentHTML('beforeend', `
    <div id="login-screen" style="position:fixed;inset:0;background:var(--bg);display:flex;align-items:center;justify-content:center;z-index:999;">
      <div style="background:var(--bg2);border:1px solid var(--border2);border-radius:8px;padding:40px;width:320px;text-align:center;">
        <div id="login-view">
          <h2 style="font-size:18px;font-weight:800;margin-bottom:6px;">Träningsdashboard</h2>
          <p style="font-size:12.5px;color:var(--muted2);margin-bottom:24px;font-family:'IBM Plex Mono',monospace;">Logga in för att fortsätta</p>
          <input id="login-user" type="text" autocomplete="username" autocapitalize="none" autocorrect="off" spellcheck="false" placeholder="Användarnamn" style="${inputStyle}" />
          <input id="login-input" type="password" autocomplete="current-password" autocapitalize="none" autocorrect="off" spellcheck="false" placeholder="Lösenord" style="${inputStyle}margin-bottom:12px;" />
          <button id="login-submit" type="button" style="width:100%;background:var(--blue);border:none;border-radius:8px;padding:12px;color:#081018;font-family:'IBM Plex Sans',sans-serif;font-size:14px;font-weight:700;cursor:pointer;">Logga in</button>
          <p id="login-error" role="alert" style="font-size:12px;color:var(--red);margin-top:10px;display:none;">Fel användarnamn eller lösenord</p>
          <p style="margin-top:10px;font-size:12.5px;">
            <a id="show-forgot-link" href="#" style="color:var(--blue);">Glömt lösenord?</a>
          </p>
          <p style="margin-top:16px;font-size:12.5px;color:var(--muted2);">
            Inget konto? <a id="show-register-link" href="#" style="color:var(--blue);">Registrera dig</a>
          </p>
        </div>
        <div id="forgot-view" style="display:none;">
          <h2 style="font-size:18px;font-weight:800;margin-bottom:6px;">Glömt lösenord</h2>
          <p style="font-size:12.5px;color:var(--muted2);margin-bottom:24px;font-family:'IBM Plex Mono',monospace;">Vi mejlar en återställningslänk</p>
          <input id="forgot-email" type="email" autocomplete="email" autocapitalize="none" autocorrect="off" spellcheck="false" placeholder="E-postadress" style="${inputStyle}margin-bottom:12px;" />
          <button id="forgot-submit" type="button" style="width:100%;background:var(--blue);border:none;border-radius:8px;padding:12px;color:#081018;font-family:'IBM Plex Sans',sans-serif;font-size:14px;font-weight:700;cursor:pointer;">Skicka återställningslänk</button>
          <p id="forgot-error" role="alert" style="font-size:12px;color:var(--red);margin-top:10px;display:none;"></p>
          <p id="forgot-success" role="status" style="font-size:12.5px;color:var(--green);margin-top:10px;display:none;"></p>
          <p style="margin-top:16px;font-size:12.5px;color:var(--muted2);">
            <a id="forgot-back-link" href="#" style="color:var(--blue);">Tillbaka till inloggning</a>
          </p>
        </div>
        <div id="reset-view" style="display:none;">
          <h2 style="font-size:18px;font-weight:800;margin-bottom:6px;">Nytt lösenord</h2>
          <p style="font-size:12.5px;color:var(--muted2);margin-bottom:24px;font-family:'IBM Plex Mono',monospace;">Välj ett nytt lösenord</p>
          <input id="reset-password" type="password" autocomplete="new-password" autocapitalize="none" autocorrect="off" spellcheck="false" placeholder="Nytt lösenord (minst 8 tecken)" style="${inputStyle}" />
          <input id="reset-password-confirm" type="password" autocomplete="new-password" autocapitalize="none" autocorrect="off" spellcheck="false" placeholder="Upprepa lösenord" style="${inputStyle}margin-bottom:12px;" />
          <button id="reset-submit" type="button" style="width:100%;background:var(--blue);border:none;border-radius:8px;padding:12px;color:#081018;font-family:'IBM Plex Sans',sans-serif;font-size:14px;font-weight:700;cursor:pointer;">Spara nytt lösenord</button>
          <p id="reset-error" role="alert" style="font-size:12px;color:var(--red);margin-top:10px;display:none;"></p>
          <p id="reset-success" role="status" style="font-size:12.5px;color:var(--green);margin-top:10px;display:none;"></p>
        </div>
        <div id="register-view" style="display:none;">
          <h2 style="font-size:18px;font-weight:800;margin-bottom:6px;">Skapa konto</h2>
          <p style="font-size:12.5px;color:var(--muted2);margin-bottom:24px;font-family:'IBM Plex Mono',monospace;">Registrera dig med e-post</p>
          <input id="register-user" type="text" autocomplete="username" autocapitalize="none" autocorrect="off" spellcheck="false" placeholder="Användarnamn" style="${inputStyle}" />
          <input id="register-email" type="email" autocomplete="email" autocapitalize="none" autocorrect="off" spellcheck="false" placeholder="E-postadress" style="${inputStyle}" />
          <input id="register-password" type="password" autocomplete="new-password" autocapitalize="none" autocorrect="off" spellcheck="false" placeholder="Lösenord (minst 8 tecken)" style="${inputStyle}margin-bottom:12px;" />
          <button id="register-submit" type="button" style="width:100%;background:var(--blue);border:none;border-radius:8px;padding:12px;color:#081018;font-family:'IBM Plex Sans',sans-serif;font-size:14px;font-weight:700;cursor:pointer;">Registrera dig</button>
          <p id="register-error" role="alert" style="font-size:12px;color:var(--red);margin-top:10px;display:none;"></p>
          <p id="register-success" role="status" style="font-size:12.5px;color:var(--green);margin-top:10px;display:none;"></p>
          <p style="margin-top:16px;font-size:12.5px;color:var(--muted2);">
            Har du redan ett konto? <a id="show-login-link" href="#" style="color:var(--blue);">Logga in</a>
          </p>
        </div>
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
  document.getElementById('register-submit').addEventListener('click', tryRegister);
  document.getElementById('register-password').addEventListener('keydown', event => {
    if (event.key === 'Enter') tryRegister();
  });
  document.getElementById('show-register-link').addEventListener('click', event => {
    event.preventDefault();
    document.getElementById('login-view').style.display = 'none';
    document.getElementById('register-view').style.display = 'block';
    document.getElementById('register-user').focus();
  });
  document.getElementById('show-login-link').addEventListener('click', event => {
    event.preventDefault();
    document.getElementById('register-view').style.display = 'none';
    document.getElementById('login-view').style.display = 'block';
    document.getElementById('login-user').focus();
  });
  document.getElementById('show-forgot-link').addEventListener('click', event => {
    event.preventDefault();
    document.getElementById('login-view').style.display = 'none';
    document.getElementById('forgot-view').style.display = 'block';
    document.getElementById('forgot-email').focus();
  });
  document.getElementById('forgot-back-link').addEventListener('click', event => {
    event.preventDefault();
    document.getElementById('forgot-view').style.display = 'none';
    document.getElementById('login-view').style.display = 'block';
    document.getElementById('login-user').focus();
  });
  document.getElementById('forgot-submit').addEventListener('click', tryForgotPassword);
  document.getElementById('forgot-email').addEventListener('keydown', event => {
    if (event.key === 'Enter') tryForgotPassword();
  });
  document.getElementById('reset-submit').addEventListener('click', tryResetPassword);
  document.getElementById('reset-password-confirm').addEventListener('keydown', event => {
    if (event.key === 'Enter') tryResetPassword();
  });

  const resetToken = new URLSearchParams(window.location.search).get('reset');
  if (resetToken) {
    document.getElementById('login-view').style.display = 'none';
    document.getElementById('reset-view').style.display = 'block';
    document.getElementById('reset-password').focus();
    return;
  }
  if (new URLSearchParams(window.location.search).get('auth') === 'register') {
    document.getElementById('login-view').style.display = 'none';
    document.getElementById('register-view').style.display = 'block';
    document.getElementById('register-user').focus();
    return;
  }
  document.getElementById('login-user').focus();
}

async function performRegister(username, email, password) {
  const response = await originalFetch('/api/register', {
    method: 'POST',
    credentials: 'same-origin',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({username, email, password}),
  });
  const data = await response.json().catch(() => ({}));
  if (!response.ok || !data.ok) {
    throw new Error(data.error || 'Registreringen misslyckades.');
  }
  return data;
}

async function tryRegister() {
  const username = document.getElementById('register-user').value.trim();
  const email = document.getElementById('register-email').value.trim();
  const password = document.getElementById('register-password').value;
  const button = document.getElementById('register-submit');
  const error = document.getElementById('register-error');
  const success = document.getElementById('register-success');
  error.style.display = 'none';
  success.style.display = 'none';
  if (!username || !email || !password) {
    error.textContent = 'Fyll i alla fält.';
    error.style.display = 'block';
    return;
  }
  button.disabled = true;
  try {
    const data = await performRegister(username, email, password);
    success.textContent = data.message || 'Kolla din inkorg för en verifieringslänk.';
    success.style.display = 'block';
    document.getElementById('register-user').value = '';
    document.getElementById('register-email').value = '';
    document.getElementById('register-password').value = '';
  } catch (registerError) {
    error.textContent = registerError.message;
    error.style.display = 'block';
  } finally {
    button.disabled = false;
  }
}

async function performForgotPassword(email) {
  const response = await originalFetch('/api/forgot-password', {
    method: 'POST',
    credentials: 'same-origin',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({email}),
  });
  const data = await response.json().catch(() => ({}));
  if (!response.ok || !data.ok) {
    throw new Error(data.error || 'Något gick fel. Försök igen.');
  }
  return data;
}

async function tryForgotPassword() {
  const email = document.getElementById('forgot-email').value.trim();
  const button = document.getElementById('forgot-submit');
  const error = document.getElementById('forgot-error');
  const success = document.getElementById('forgot-success');
  error.style.display = 'none';
  success.style.display = 'none';
  if (!email) {
    error.textContent = 'Fyll i din e-postadress.';
    error.style.display = 'block';
    return;
  }
  button.disabled = true;
  try {
    const data = await performForgotPassword(email);
    success.textContent = data.message || 'Om kontot finns har vi skickat en återställningslänk.';
    success.style.display = 'block';
    document.getElementById('forgot-email').value = '';
  } catch (forgotError) {
    error.textContent = forgotError.message;
    error.style.display = 'block';
  } finally {
    button.disabled = false;
  }
}

async function performResetPassword(token, password) {
  const response = await originalFetch('/api/reset-password', {
    method: 'POST',
    credentials: 'same-origin',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({token, password}),
  });
  const data = await response.json().catch(() => ({}));
  if (!response.ok || !data.ok) {
    throw new Error(data.error || 'Länken är ogiltig eller har gått ut.');
  }
  return data;
}

async function tryResetPassword() {
  const token = new URLSearchParams(window.location.search).get('reset') || '';
  const password = document.getElementById('reset-password').value;
  const passwordConfirm = document.getElementById('reset-password-confirm').value;
  const button = document.getElementById('reset-submit');
  const error = document.getElementById('reset-error');
  const success = document.getElementById('reset-success');
  error.style.display = 'none';
  success.style.display = 'none';
  if (!password || password.length < 8) {
    error.textContent = 'Lösenordet måste vara minst 8 tecken.';
    error.style.display = 'block';
    return;
  }
  if (password !== passwordConfirm) {
    error.textContent = 'Lösenorden matchar inte.';
    error.style.display = 'block';
    return;
  }
  button.disabled = true;
  try {
    await performResetPassword(token, password);
    success.textContent = 'Lösenordet har återställts. Du kan nu logga in.';
    success.style.display = 'block';
    document.getElementById('reset-password').value = '';
    document.getElementById('reset-password-confirm').value = '';
    const url = new URL(window.location.href);
    url.searchParams.delete('reset');
    window.history.replaceState({}, '', url);
    setTimeout(() => {
      document.getElementById('reset-view').style.display = 'none';
      document.getElementById('login-view').style.display = 'block';
      document.getElementById('login-user').focus();
    }, 1500);
  } catch (resetError) {
    error.textContent = resetError.message;
    error.style.display = 'block';
  } finally {
    button.disabled = false;
  }
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
        <button type="button" data-action="save-goal-rebuild" id="goal-rebuild-btn" style="width:100%;background:var(--bg3);border:1px solid var(--border2);border-radius:8px;padding:11px;color:var(--text);font-family:'IBM Plex Sans',sans-serif;font-size:13.5px;font-weight:700;cursor:pointer;margin-top:8px;">Spara mål & bygg om schemat</button>
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

function showGoalModalMessage(text, color) {
  const msg = document.getElementById('goal-modal-msg');
  if (!msg) return;
  msg.textContent = text;
  msg.style.color = color || 'var(--red)';
  msg.style.display = text ? 'block' : 'none';
}

async function saveGoalFromForm(keepOpen = false) {
  const title = document.getElementById('goal-title-input').value.trim();
  const button = document.getElementById('goal-save-btn');
  if (!title) {
    showGoalModalMessage('Skriv in ett mål först.');
    return false;
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
      showGoalModalMessage(data.error || 'Kunde inte spara målet.');
      return false;
    }
    userGoal = data.goal;
    renderGoalUi();
    if (!keepOpen) closeGoalModal();
    return true;
  } catch (error) {
    showGoalModalMessage('Servern kunde inte nås. Försök igen.');
    return false;
  } finally {
    button.disabled = false;
  }
}

async function saveGoalAndRebuildPlan() {
  const saved = await saveGoalFromForm(true);
  if (!saved) return;
  if (!confirm('Bygga om hela schemat utifrån målet?\n\nKommande planerade pass ersätts av en ny plan från coachen. Genomförda och missade pass behålls som historik.')) {
    closeGoalModal();
    return;
  }
  const rebuildBtn = document.getElementById('goal-rebuild-btn');
  const saveBtn = document.getElementById('goal-save-btn');
  if (rebuildBtn) { rebuildBtn.disabled = true; rebuildBtn.textContent = 'Coachen bygger din plan…'; }
  if (saveBtn) saveBtn.disabled = true;
  showGoalModalMessage('Coachen bygger din nya plan utifrån målet och din nuvarande form — det kan ta upp till en minut…', 'var(--muted2)');
  try {
    const res = await fetch('/api/plan/generate', {method: 'POST'});
    const data = await res.json().catch(() => ({}));
    if (!res.ok) {
      showGoalModalMessage(data.error || 'Planen kunde inte skapas. Försök igen.');
      return;
    }
    showGoalModalMessage(`Klart! ${data.sessions} pass inlagda t.o.m. vecka ${data.endWeek}. Sidan laddas om…`, 'var(--accent)');
    setTimeout(() => location.reload(), 2000);
  } catch (error) {
    showGoalModalMessage('Servern kunde inte nås. Försök igen.');
  } finally {
    if (rebuildBtn) { rebuildBtn.disabled = false; rebuildBtn.textContent = 'Spara mål & bygg om schemat'; }
    if (saveBtn) saveBtn.disabled = false;
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
  renderSettingsPage();
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

// --- Strava OAuth ---
function updateStravaSidebar() {
  const row = document.querySelector('.strava-sync-row');
  const label = document.getElementById('strava-sync-time');
  renderSettingsPage();
  if (!row || !label) return;
  label.textContent = stravaConnected
    ? `Strava${stravaAthlete ? ` · ${stravaAthlete}` : ' anslutet'}`
    : (stravaConfigured ? 'Koppla Strava' : 'Aktivera Strava');
  row.dataset.action = 'open-strava';
  row.setAttribute('role', 'button');
  row.style.cursor = 'pointer';
  row.title = stravaConnected ? 'Hantera Strava' : 'Koppla ditt Strava-konto';
  row.classList.toggle('is-connected', stravaConnected);
}

function closeStravaModal() {
  document.getElementById('strava-modal')?.remove();
}

function openStravaModal() {
  if (document.getElementById('strava-modal')) return;
  const connectedCopy = stravaAthlete
    ? `Ansluten som ${escapeHtml(stravaAthlete)}.`
    : 'Ditt Strava-konto är anslutet.';
  const body = stravaConnected ? `
    <p>${connectedCopy} Aktiviteter från Garmin fortsätter vara förstahandskälla när båda tjänsterna är anslutna, så passen visas inte dubbelt.</p>
    <button type="button" data-action="sync-strava" id="strava-primary-btn" class="strava-connect-btn">Synka Strava nu</button>
    <button type="button" data-action="disconnect-strava" id="strava-disconnect-btn" class="strava-secondary-btn">Koppla från Strava</button>` : `
    <p>${stravaConfigured
      ? 'Godkänn Trainyze hos Strava för att visa dina aktiviteter, kartor, varv, puls, effekt och annan passdata.'
      : 'Strava-stödet är installerat men administratören behöver lägga in appens Client ID och Secret på servern innan konton kan anslutas.'}</p>
    <button type="button" data-action="connect-strava" id="strava-primary-btn" class="strava-connect-btn" ${stravaConfigured ? '' : 'disabled'}>Anslut med Strava</button>`;
  document.body.insertAdjacentHTML('beforeend', `
    <div id="strava-modal" class="strava-overlay" role="presentation">
      <div class="strava-modal" role="dialog" aria-modal="true" aria-labelledby="strava-title">
        <div class="strava-modal-head"><div><span>STRAVA</span><h2 id="strava-title">${stravaConnected ? 'Strava är anslutet' : 'Koppla Strava'}</h2></div>
          <button type="button" data-action="close-strava" aria-label="Stäng">✕</button></div>
        <div class="strava-modal-body">${body}<p id="strava-modal-msg" role="status"></p></div>
      </div>
    </div>`);
  const overlay = document.getElementById('strava-modal');
  overlay.addEventListener('click', event => {
    if (event.target === overlay) closeStravaModal();
  });
}

function showStravaMessage(text, isError = false) {
  const message = document.getElementById('strava-modal-msg');
  if (!message) return;
  message.textContent = text;
  message.classList.toggle('is-error', isError);
}

async function connectStrava() {
  const button = document.getElementById('strava-primary-btn')
    || document.getElementById('settings-strava-primary');
  if (button) button.disabled = true;
  showStravaMessage('Öppnar Strava…');
  try {
    const response = await fetch('/api/strava/connect', {method: 'POST'});
    const payload = await response.json().catch(() => ({}));
    if (!response.ok) throw new Error(payload.error || 'Strava kunde inte öppnas.');
    const popup = window.open(payload.authorizationUrl, 'trainyze-strava', 'popup,width=620,height=780');
    if (!popup) location.assign(payload.authorizationUrl);
  } catch (error) {
    showStravaMessage(error.message, true);
    if (!document.getElementById('strava-modal')) alert(error.message);
    if (button) button.disabled = false;
  }
}

async function syncStrava() {
  const button = document.getElementById('strava-primary-btn')
    || document.getElementById('settings-strava-primary');
  if (button) button.disabled = true;
  if (button) button.textContent = 'Synkar…';
  showStravaMessage('Hämtar dina senaste aktiviteter…');
  try {
    const response = await fetch('/api/strava/sync', {method: 'POST'});
    const payload = await response.json().catch(() => ({}));
    if (!response.ok) throw new Error(payload.error || 'Strava kunde inte synkas.');
    showStravaMessage(`${payload.activities} aktiviteter hämtade. Kalendern uppdateras nu.`);
    await loadRecentActivities(false);
  } catch (error) {
    showStravaMessage(error.message, true);
    if (!document.getElementById('strava-modal')) alert(error.message);
  } finally {
    if (button) button.disabled = false;
    renderSettingsPage();
  }
}

async function disconnectStrava() {
  if (!confirm('Koppla från Strava? Redan sparad Garmin-data påverkas inte.')) return;
  const button = document.getElementById('strava-disconnect-btn');
  if (button) button.disabled = true;
  try {
    const response = await fetch('/api/strava/disconnect', {method: 'POST'});
    const payload = await response.json().catch(() => ({}));
    if (!response.ok) throw new Error(payload.error || 'Strava kunde inte kopplas från.');
    stravaConnected = false;
    stravaAthlete = null;
    closeStravaModal();
    updateStravaSidebar();
    await loadRecentActivities(false);
  } catch (error) {
    showStravaMessage(error.message, true);
    if (button) button.disabled = false;
  }
}

window.addEventListener('message', async event => {
  if (event.origin !== location.origin || event.data?.type !== 'strava-oauth') return;
  if (event.data.status !== 'connected') {
    showStravaMessage('Strava kunde inte anslutas. Kontrollera behörigheten och försök igen.', true);
    return;
  }
  try {
    const response = await fetch('/api/strava/status');
    const payload = await response.json();
    stravaConnected = !!payload.connected;
    stravaAthlete = payload.athleteName || null;
    closeStravaModal();
    updateStravaSidebar();
    openStravaModal();
    showStravaMessage('Anslutningen lyckades. Dina aktiviteter synkas i bakgrunden.');
    await loadRecentActivities(false);
  } catch (_) {
    showStravaMessage('Strava anslöts, men sidan behöver laddas om för att visa statusen.', true);
  }
});

function setIntegrationState(id, connected, label) {
  const element = document.getElementById(id);
  if (!element) return;
  element.classList.toggle('is-connected', connected);
  element.innerHTML = `<i></i>${escapeHtml(label)}`;
}

function renderSettingsPage() {
  const username = currentUsername || 'Konto';
  const displayName = username ? username.charAt(0).toUpperCase() + username.slice(1) : 'Konto';
  const accountName = document.getElementById('topbar-account-name');
  const settingsName = document.getElementById('settings-username');
  const avatar = document.getElementById('settings-avatar');
  const role = document.getElementById('settings-role');
  const accountTitle = document.getElementById('settings-account-title');
  if (accountName) accountName.textContent = displayName;
  if (settingsName) settingsName.textContent = displayName;
  if (avatar) avatar.textContent = displayName.charAt(0).toUpperCase();
  if (role) role.textContent = currentUserIsAdmin ? 'Administratör' : 'Användare';
  if (accountTitle) accountTitle.textContent = `Inloggad som ${displayName}`;

  setIntegrationState('settings-garmin-state', garminConnected,
    garminConnected ? 'Ansluten' : 'Ej ansluten');
  setIntegrationState('settings-strava-state', stravaConnected,
    stravaConnected ? 'Ansluten' : (stravaConfigured ? 'Ej ansluten' : 'Ej konfigurerad'));
  setIntegrationState('settings-calendar-state', calendarConnected,
    calendarConnected ? 'Ansluten' : 'Ej ansluten');

  const garminPrimary = document.getElementById('settings-garmin-primary');
  const garminDisconnect = document.getElementById('settings-garmin-disconnect');
  if (garminPrimary) garminPrimary.textContent = garminConnected ? 'Synka Garmin nu' : 'Koppla Garmin';
  if (garminDisconnect) garminDisconnect.style.display = garminConnected ? '' : 'none';
  const stravaPrimary = document.getElementById('settings-strava-primary');
  const stravaDisconnect = document.getElementById('settings-strava-disconnect');
  if (stravaPrimary) {
    stravaPrimary.textContent = stravaConnected ? 'Synka Strava nu' : 'Koppla Strava';
    stravaPrimary.disabled = !stravaConnected && !stravaConfigured;
  }
  if (stravaDisconnect) stravaDisconnect.style.display = stravaConnected ? '' : 'none';
  const calendarPrimary = document.getElementById('settings-calendar-primary');
  if (calendarPrimary) calendarPrimary.disabled = !calendarConnected;

  const connectedCount = [garminConnected, stravaConnected, calendarConnected].filter(Boolean).length;
  const summary = document.getElementById('settings-connection-summary');
  if (summary) summary.textContent = `${connectedCount} av 3 anslutna`;
  const topLabel = document.getElementById('topbar-connection-label');
  if (topLabel) topLabel.textContent = `${connectedCount} anslutningar`;
  const topDot = document.getElementById('topbar-connection-dot');
  if (topDot) topDot.classList.toggle('is-connected', connectedCount > 0);
}

async function loadSettingsPage() {
  renderSettingsPage();
  try {
    const [stravaResponse, calendarResponse] = await Promise.all([
      fetch('/api/strava/status'), fetch('/api/calendar/status'),
    ]);
    if (stravaResponse.ok) {
      const status = await stravaResponse.json();
      stravaConfigured = !!status.configured;
      stravaConnected = !!status.connected;
      stravaAthlete = status.athleteName || null;
    }
    if (calendarResponse.ok) {
      const status = await calendarResponse.json();
      calendarConnected = !!status.hasToken;
    }
  } catch (_) {
    // Senast kända status står kvar om en leverantör tillfälligt inte svarar.
  }
  renderSettingsPage();
}

async function disconnectGarmin() {
  if (!confirm('Koppla från Garmin Connect? Strava används då som aktivitetskälla om det är anslutet.')) return;
  try {
    const response = await fetch('/api/garmin/disconnect', {method: 'POST'});
    const payload = await response.json().catch(() => ({}));
    if (!response.ok) throw new Error(payload.error || 'Garmin kunde inte kopplas från.');
    garminConnected = false;
    updateGarminSidebar();
    await loadRecentActivities(false);
  } catch (error) {
    alert(error.message);
  }
}

// --- Användarhantering (admin) ---
function closeUsersPanel() {
  document.getElementById('users-panel')?.remove();
}

async function openUsersPanel() {
  if (document.getElementById('users-panel')) return;
  document.body.insertAdjacentHTML('beforeend', `
    <div id="users-panel" class="users-overlay" role="presentation">
      <div class="users-modal" role="dialog" aria-modal="true" aria-labelledby="users-title">
        <div class="users-modal-header">
          <div>
            <div class="panel-eyebrow">ADMINISTRATION</div>
            <h2 id="users-title">Hantera användare</h2>
          </div>
          <button type="button" data-action="close-users" class="users-close" aria-label="Stäng">✕</button>
        </div>
        <p class="users-modal-intro">Skapa och överblicka konton. Garmin kopplas separat av varje användare.</p>
        <div class="users-summary" id="users-summary" aria-live="polite">Laddar användare…</div>
        <div class="users-list-toolbar">
          <label class="users-search">
            <span aria-hidden="true">⌕</span>
            <input id="users-search" type="search" placeholder="Sök användare" autocomplete="off" aria-label="Sök användare" />
          </label>
        </div>
        <div id="users-list" class="users-list"><p class="users-empty">Laddar…</p></div>
        <div class="users-create">
          <div class="users-section-heading">
            <div>
              <div class="panel-eyebrow">NYTT KONTO</div>
              <h3>Lägg till användare</h3>
            </div>
            <span class="users-section-hint">Lösenordet visas bara nu</span>
          </div>
          <div class="users-form-grid">
            <label class="users-field">Användarnamn<input id="new-user-name" type="text" autocomplete="off" placeholder="t.ex. anna" /></label>
            <label class="users-field">Temporärt lösenord<div class="users-password-field"><input id="new-user-password" type="text" autocomplete="off" placeholder="Minst 8 tecken" /><button type="button" data-action="random-password" title="Slumpa ett starkt lösenord" aria-label="Slumpa lösenord">✦</button></div></label>
          </div>
          <button type="button" data-action="create-user" id="create-user-btn" class="users-create-btn">Skapa användare <span aria-hidden="true">→</span></button>
          <p id="users-panel-msg" role="status" class="users-panel-msg"></p>
        </div>
      </div>
    </div>
  `);
  const overlay = document.getElementById('users-panel');
  overlay.addEventListener('click', event => {
    if (event.target === overlay) closeUsersPanel();
  });
  overlay.addEventListener('keydown', event => {
    if (event.key === 'Escape') closeUsersPanel();
  });
  document.getElementById('users-search').addEventListener('input', filterUsersList);
  document.getElementById('new-user-password').addEventListener('keydown', event => {
    if (event.key === 'Enter') createUserFromForm();
  });
  document.getElementById('users-search').focus();
  await loadUsersList();
}

let usersPanelData = [];

async function loadUsersList() {
  const list = document.getElementById('users-list');
  if (!list) return;
  try {
    const res = await fetch('/api/users');
    const data = await res.json();
    if (!res.ok) throw new Error(data.error || 'Kunde inte hämta användare');
    usersPanelData = data.users;
    updateUsersSummary();
    renderUsersList(usersPanelData);
  } catch (error) {
    list.innerHTML = `<p class="users-empty users-error">${escapeHtml(error.message)}</p>`;
  }
}

function updateUsersSummary() {
  const summary = document.getElementById('users-summary');
  if (!summary) return;
  const connected = usersPanelData.filter(u => u.garminConnected).length;
  summary.innerHTML = `<span><strong>${usersPanelData.length}</strong> ${usersPanelData.length === 1 ? 'konto' : 'konton'}</span><span><i class="users-summary-dot connected"></i>${connected} med Garmin</span>`;
}

function renderUsersList(users) {
  const list = document.getElementById('users-list');
  if (!list) return;
  list.innerHTML = users.map(u => {
    const initial = escapeHtml(u.username.slice(0, 1).toUpperCase());
    return `
      <div class="user-row">
        <div class="user-avatar">${initial}</div>
        <div class="user-main"><strong>${escapeHtml(u.username)}</strong><span class="user-status ${u.garminConnected ? 'is-connected' : ''}"><i></i>${u.garminConnected ? 'Garmin ansluten' : 'Garmin ej ansluten'}</span></div>
        ${u.isAdmin ? '<span class="user-role">ADMIN</span>' : '<span class="user-role user-role-muted">ANVÄNDARE</span>'}
        ${u.isAdmin ? '' : `<button type="button" data-action="delete-user" data-id="${Number(u.id)}" data-username="${escapeHtml(u.username)}" class="user-delete" title="Ta bort ${escapeHtml(u.username)}" aria-label="Ta bort ${escapeHtml(u.username)}">✕</button>`}
      </div>`;
  }).join('') || '<p class="users-empty">Ingen användare matchar sökningen.</p>';
}

function filterUsersList(event) {
  const query = event.target.value.trim().toLowerCase();
  renderUsersList(usersPanelData.filter(u => u.username.toLowerCase().includes(query)));
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
    document.getElementById('users-search')?.focus();
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
  else if (action === 'settings-garmin-primary') garminConnected
    ? refreshData() : openGarminConnectModal();
  else if (action === 'disconnect-garmin') disconnectGarmin();
  else if (action === 'open-strava') openStravaModal();
  else if (action === 'close-strava') closeStravaModal();
  else if (action === 'connect-strava') connectStrava();
  else if (action === 'sync-strava') syncStrava();
  else if (action === 'disconnect-strava') disconnectStrava();
  else if (action === 'settings-strava-primary') stravaConnected
    ? syncStrava() : connectStrava();
  else if (action === 'open-goal-modal') openGoalModal(false);
  else if (action === 'close-goal-modal') closeGoalModal();
  else if (action === 'save-goal') saveGoalFromForm();
  else if (action === 'save-goal-rebuild') saveGoalAndRebuildPlan();
  else if (action === 'logout') performLogout();
  else if (action === 'refresh-data') refreshData();
  else if (action === 'sync-calendar') syncGcal();
  else if (action === 'open-activity') openActivityDetails(
    Number(trigger.dataset.activityId), trigger.dataset.activitySource);
  else if (action === 'close-activity') closeActivityDetails();
  else if (action === 'activity-map-expand') toggleActivityMapExpanded();
  else if (action === 'activity-map-zoom-in') zoomActivityMap(1);
  else if (action === 'activity-map-zoom-out') zoomActivityMap(-1);
  else if (action === 'activity-map-reset') fitActivityMapToRoute();
  else if (action === 'refresh-insights') loadInsights(true);
  else if (action === 'open-trend-breakdown') openTrendBreakdown();
  else if (action === 'close-trend-breakdown') closeTrendBreakdown();
  else if (action === 'toggle-ac-loop') toggleAcLoop();
  else if (action === 'set-ac-setpoint') setAcSetpoint();
  else if (action === 'save-ac-bedtime') saveAcBedtime();
  else if (action === 'clear-ac-bedtime') clearAcBedtime();
  else if (action === 'send-ac-command') sendManualAcCommand();
  else if (action === 'calendar-view') setCalendarView(trigger.dataset.view);
  else if (action === 'analysis-window') setAnalysisWindow(Number(trigger.dataset.days));
  else if (action === 'analysis-metric') selectAnalysisMetric(trigger.dataset.metric);
  else if (action === 'pace-generate') generatePaceProposals();
  else if (action === 'pace-decide') decidePaceProposals(trigger.dataset.decision, trigger.dataset.id);
  else if (action === 'strength-tab') strengthTab(trigger.dataset.tab);
  else if (action === 'save-journal') saveJournalEntry();
  else if (action === 'quick-prompt') qa(trigger.dataset.prompt);
  else if (action === 'send-chat') send();
  else if (action === 'edit-journal') editJournalDate(trigger.dataset.date);
  else if (action === 'delete-journal') deleteJournalEntry(event, Number(trigger.dataset.id));
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
  if (event.key === 'Escape' && document.getElementById('activity-overlay')?.classList.contains('is-open')) {
    if (activityMapState?.element.classList.contains('is-expanded')) {
      toggleActivityMapExpanded(true);
      return;
    }
    closeActivityDetails();
    return;
  }
  const trigger = event.target.closest('[data-action][role="button"]');
  if (trigger && (event.key === 'Enter' || event.key === ' ')) {
    event.preventDefault();
    executeAction(trigger, event);
  }
});

const acSetpointInput = document.getElementById('ac-setpoint-input');
acSetpointInput?.addEventListener('input', () => { acSetpointInput.dataset.dirty = '1'; });
acSetpointInput?.addEventListener('blur', () => { delete acSetpointInput.dataset.dirty; });

  // Navigation
  function goto(id) {
    const page = document.getElementById('page-' + id);
    if (!page) return;
    document.querySelectorAll('.page').forEach(p => p.classList.remove('active'));
    document.querySelectorAll('.nav-item').forEach(n => n.classList.remove('active'));
    page.classList.add('active');
    document.querySelectorAll('.nav-item').forEach(n => {
      if (n.dataset.page === id) n.classList.add('active');
    });
    if (phoneMedia.matches && ['strength', 'sleep', 'journal', 'climate'].includes(id)) {
      document.querySelector('.nav-settings')?.classList.add('active');
    }
    document.querySelector('.topbar-account')?.classList.toggle('is-active', id === 'settings');
    window.scrollTo(0, 0);
    if (id === 'health')   loadHealth();
    if (id === 'sleep')    { loadHealth(); loadSleepOverview(); setTimeout(() => { if (currentHealthData) renderSleepStageChart(currentHealthData.sleep?.levels || [], currentHealthData.sleep?.startGMT, currentHealthData.sleep?.endGMT); }, 50); }
    if (id === 'analysis') loadAnalysis();
    if (id === 'strength') loadStrengthPage();
    if (id === 'journal')  loadJournal();
    if (id === 'upcoming') { checkGcalStatus(); loadPaceProposals(); }
    if (id === 'climate')  { loadWeatherStatus(); loadAcStatus(); loadAcLoopStatus(); loadAcBedtime(); loadHumidityStatus(); loadAcHistory(); }
    if (id === 'settings') loadSettingsPage();
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

  // Dagens rörelse: steg, kalorier, sträcka och intensitetsminuter från
  // Garmins dygnssammanfattning. Kortet döljs helt när dagen saknar data.
  function renderDailyActivity(daily) {
    const card = document.getElementById('hg-daily');
    if (!card) return;
    if (daily.steps == null && daily.caloriesActive == null) {
      card.style.display = 'none';
      return;
    }
    card.style.display = '';

    const setText = (id, value) => { const el = document.getElementById(id); if (el) el.textContent = value; };
    const nf = value => (value == null ? '-' : value.toLocaleString('sv-SE'));

    const pct = daily.stepPct;
    const reached = pct != null && pct >= 100;
    const colour = reached ? 'var(--accent)' : pct != null && pct >= 70 ? 'var(--amber)' : 'var(--muted2)';

    const score = document.getElementById('hg-daily-score');
    if (score) { score.textContent = nf(daily.steps); score.style.color = colour; }

    const badge = document.getElementById('hg-daily-badge');
    if (badge) {
      badge.textContent = pct == null ? 'Inget stegmål' : reached ? 'Mål uppnått' : pct + '% av målet';
      badge.className = 'hg-status ' + (reached ? 'hs-great' : pct != null && pct >= 70 ? 'hs-ok' : 'hs-low');
    }

    setText('hg-daily-summary', daily.stepGoal ? 'Mål ' + nf(daily.stepGoal) : '');

    const bar = document.getElementById('hg-daily-bar');
    if (bar) bar.style.width = Math.max(0, Math.min(100, pct || 0)) + '%';

    setText('hg-daily-desc', daily.steps == null
      ? 'Ingen stegdata från Garmin ännu.'
      : reached
        ? `Stegmålet är passerat med ${nf(daily.steps - daily.stepGoal)} steg.`
        : daily.stepGoal
          ? `${nf(daily.stepGoal - daily.steps)} steg kvar till dagens mål.`
          : 'Steg registrerade, men inget mål är satt i Garmin.');

    setMetric('hd-cal-active', 'hd-cal-active-status', nf(daily.caloriesActive), 'kcal', 'kcal', 'var(--accent)');
    setMetric('hd-cal-total', 'hd-cal-total-status', nf(daily.caloriesTotal), 'kcal', 'kcal', '');
    setText('hd-cal-total-desc', daily.caloriesBmr != null
      ? `Varav ${nf(daily.caloriesBmr)} kcal basalomsättning`
      : 'Inklusive basalomsättning');

    setText('hd-daily-dist', daily.distanceM != null ? (daily.distanceM / 1000).toFixed(1) : '-');

    const goal = daily.intensityGoal;
    setMetric('hd-intensity', 'hd-intensity-status',
      daily.intensityMinutes != null ? daily.intensityMinutes : '-',
      '', goal ? '/ ' + goal + ' i veckan' : '',
      goal && daily.intensityMinutes >= goal ? 'var(--accent)' : '');
  }

  function setMetric(valId, statusId, value, unit, statusText, col) {
    const v = document.getElementById(valId);
    const s = document.getElementById(statusId);
    if (v) { v.textContent = value; v.style.color = col || ''; }
    if (s) { s.textContent = statusText || unit || ''; s.style.color = col || ''; }
  }

  // ─── SÖMNSIDAN ──────────────────────────────────────────────
  // Nattens siffror kommer från /api/health, historik och härledda mått
  // från /api/sleep (se sleep_analysis.py).
  const SL_STAGES = [
    {key: 'deep',  label: 'Djup',  color: '#EC4899', target: '15–25%'},
    {key: 'rem',   label: 'REM',   color: '#38BDF8', target: '20–25%'},
    {key: 'light', label: 'Lätt',  color: '#10B981', target: ''},
    {key: 'awake', label: 'Vaken', color: '#EF4444', target: ''},
  ];

  function slFmtHours(seconds) {
    if (!seconds) return '–';
    const h = Math.floor(seconds / 3600);
    const m = Math.floor((seconds % 3600) / 60);
    return h + ' h ' + String(m).padStart(2, '0') + ' min';
  }

  function slClock(value) {
    if (!value) return null;
    const text = String(value);
    const match = text.match(/(\d{1,2}):(\d{2})/);
    return match ? match[1].padStart(2, '0') + ':' + match[2] : null;
  }

  function renderSleepPage(h) {
    const sleep = h.sleep || {};
    const totalSec = sleep.totalSec || 0;
    const score = sleep.score || 0;
    const deep = sleep.deepPct || 0;
    const rem = sleep.remPct || 0;
    const setText = (id, value) => { const el = document.getElementById(id); if (el) el.textContent = value; };

    // Poängring
    const ring = document.getElementById('sl-score-ring');
    const circumference = 2 * Math.PI * 57;
    const colour = score >= 80 ? 'var(--accent)' : score >= 60 ? 'var(--amber)' : 'var(--red)';
    if (ring) {
      ring.style.strokeDasharray = circumference.toFixed(1);
      ring.style.strokeDashoffset = (circumference * (1 - Math.min(1, score / 100))).toFixed(1);
      ring.style.stroke = colour;
    }
    const scoreEl = document.getElementById('sl-score');
    if (scoreEl) { scoreEl.textContent = score || '–'; scoreEl.style.color = colour; }

    setText('sl-verdict', !totalSec ? 'Ingen sömn registrerad i natt'
      : score >= 80 ? 'God återhämtning'
      : score >= 60 ? 'Godkänt, men det finns mer att hämta'
      : 'Prioritera sömnen i natt');

    // Total sömn mot mål
    const targetSec = 7.5 * 3600;
    setText('sl-total', slFmtHours(totalSec));
    setText('sl-total-sub', !totalSec ? 'Ingen data'
      : totalSec >= targetSec ? 'Mål uppnått'
      : 'Saknar ' + slFmtHours(targetSec - totalSec));
    const totalBar = document.getElementById('sl-total-bar');
    if (totalBar) totalBar.style.width = Math.min(100, totalSec / targetSec * 100) + '%';

    renderSleepStages(sleep);
    renderSleepStageChart(sleep.levels || [], sleep.startGMT, sleep.endGMT);
  }

  function renderSleepStages(sleep) {
    const bar = document.getElementById('sl-stagebar');
    const legend = document.getElementById('sl-stage-legend');
    const note = document.getElementById('sl-stage-note');
    if (!bar || !legend) return;

    const total = sleep.totalSec || 0;
    const values = {
      deep: sleep.deepPct || 0,
      rem: sleep.remPct || 0,
      light: sleep.lightPct != null ? sleep.lightPct : null,
      awake: sleep.awakePct != null ? sleep.awakePct : null,
    };
    // Garmin skickar inte alltid lätt sömn — räkna ut resten så stapeln går ihop.
    if (values.light === null) {
      values.light = Math.max(0, 100 - values.deep - values.rem - (values.awake || 0));
    }

    if (!total) {
      bar.innerHTML = '';
      legend.innerHTML = '<p class="sl-empty">Ingen stadiedata för i natt.</p>';
      if (note) note.textContent = '';
      return;
    }

    bar.innerHTML = SL_STAGES
      .filter(stage => values[stage.key])
      .map(stage => `<span class="sl-stage-seg" style="width:${values[stage.key]}%;background:${stage.color}"`
        + ` data-freetip="${escapeHtml(stage.label + ' ' + values[stage.key] + '%')}"></span>`)
      .join('');

    legend.innerHTML = SL_STAGES.map(stage => {
      const value = values[stage.key];
      if (value === null || value === undefined) return '';
      const low = stage.key === 'deep' ? value < 15 : stage.key === 'rem' ? value < 20 : false;
      return `<div class="sl-stage-item">
        <span class="sl-stage-dot" style="background:${stage.color}"></span>
        <span class="sl-stage-name">${escapeHtml(stage.label)}</span>
        <strong class="sl-stage-val${low ? ' sl-low' : ''}">${value}%</strong>
        ${stage.target ? `<span class="sl-stage-target">mål ${escapeHtml(stage.target)}</span>` : ''}
      </div>`;
    }).join('');

    const shortfalls = [];
    if (values.deep < 15) shortfalls.push('djupsömnen under 15%');
    if (values.rem < 20) shortfalls.push('REM under 20%');
    if (note) note.textContent = shortfalls.length ? shortfalls.join(' · ') : 'Fördelningen ser bra ut';
  }

  // ─── Historik, läggdags och regelbundenhet ───
  async function loadSleepOverview() {
    try {
      const res = await fetch('/api/sleep?days=21');
      if (!res.ok) return;
      const data = await res.json();
      renderSleepTonight(data.tonight);
      renderSleepHistory(data.nights || [], data.summary || {});
      renderSleepSummary(data.summary || {}, data.nights || []);
    } catch (_) {
      // Sidan fungerar ändå med nattens siffror.
    }
  }

  function renderSleepTonight(tonight) {
    const panel = document.getElementById('sl-tonight');
    if (!panel) return;
    const night = tonight && tonight.night;
    if (!night || !night.bedtime) { panel.style.display = 'none'; return; }
    panel.style.display = '';

    document.getElementById('sl-tonight-eyebrow').textContent =
      (tonight.headline || 'I kväll').toUpperCase();
    document.getElementById('sl-tonight-bed').textContent = night.bedtime;
    document.getElementById('sl-tonight-wake').textContent =
      night.wake ? `för att vakna ${night.wake}` : '';
    document.getElementById('sl-tonight-why').textContent =
      night.reason || tonight.summary || '';

    const steps = [];
    if (night.windDown) steps.push(['Varva ner', night.windDown]);
    if (night.acPrecool) steps.push(['Kyl sovrummet', night.acPrecool]);
    if (night.targetHours) steps.push(['Mål i natt', night.targetHours + ' h']);
    document.getElementById('sl-tonight-steps').innerHTML = steps
      .map(([label, value]) => `<div class="sl-step"><span>${escapeHtml(label)}</span>`
        + `<strong>${escapeHtml(String(value))}</strong></div>`)
      .join('');
  }

  function renderSleepHistory(nights, summary) {
    const container = document.getElementById('sl-history');
    if (!container) return;
    const withHours = nights.filter(n => n.sleep_hours != null);
    if (!withHours.length) {
      container.innerHTML = '<p class="sl-empty">Ingen historik ännu.</p>';
      return;
    }

    const target = summary.targetH || 7.5;
    const peak = Math.max(target + 1.5, ...withHours.map(n => n.sleep_hours));
    // Äldst till vänst så tiden löper åt höger.
    const ordered = withHours.slice().reverse();

    container.innerHTML = `<div class="sl-bars" style="--target:${(target / peak * 100).toFixed(1)}%">`
      + ordered.map(n => {
        const height = Math.max(3, n.sleep_hours / peak * 100);
        const score = n.sleep_score;
        const tone = score == null ? 'sl-bar-none'
          : score >= 80 ? 'sl-bar-good' : score >= 60 ? 'sl-bar-ok' : 'sl-bar-low';
        const day = new Date(n.date + 'T00:00:00');
        const tip = `${n.date} · ${n.sleep_hours.toFixed(1)} h`
          + (score != null ? ` · poäng ${score}` : '');
        return `<span class="sl-bar ${tone}" style="height:${height.toFixed(1)}%"`
          + ` data-freetip="${escapeHtml(tip)}">`
          + `<i>${day.getDate()}</i></span>`;
      }).join('')
      + '</div>';
  }

  function renderSleepSummary(summary, nights) {
    const setText = (id, value) => { const el = document.getElementById(id); if (el) el.textContent = value; };

    // Sömnskuld
    const debt = summary.debt;
    if (debt) {
      const owed = debt.debtH > 0;
      setText('sl-debt', owed ? '-' + debt.debtH.toFixed(1) + ' h' : '+' + debt.surplusH.toFixed(1) + ' h');
      setText('sl-debt-sub', `${debt.nights} nätter · snitt ${debt.averageH.toFixed(1)} h`);
      const bar = document.getElementById('sl-debt-bar');
      if (bar) {
        // Full stapel = en hel natts sömn skuldsatt, inte veckomålet.
        const scale = owed ? debt.debtH / 7.5 : debt.surplusH / 7.5;
        bar.style.width = Math.min(100, Math.max(3, scale * 100)) + '%';
        bar.className = owed ? 'sl-meter-amber' : 'sl-meter-good';
      }
      const el = document.getElementById('sl-debt');
      if (el) el.style.color = owed ? 'var(--amber)' : 'var(--green)';
    }

    // Streck
    setText('sl-streak', summary.streak != null ? summary.streak : '–');
    setText('sl-streak-sub', summary.streak === 1 ? 'natt i rad på 7,5 h' : 'nätter i rad på 7,5 h');

    // Sänggående — måste komma från samma natt som siffrorna ovanför, annars
    // visas en tid från en helt annan natt bredvid nattens totalsumma.
    const latest = (nights || [])[0];
    const bed = latest && slClock(latest.sleep_start);
    const wake = latest && slClock(latest.sleep_end);
    if (bed) {
      setText('sl-window', wake ? `${bed} – ${wake}` : bed);
      setText('sl-window-sub', 'sänggående och uppstigning');
    } else {
      setText('sl-window', '–');
      setText('sl-window-sub', 'Garmin har inte rapporterat tider för natten');
    }

    // Trendchips
    const trendsEl = document.getElementById('sl-trends');
    if (trendsEl) {
      const labels = {sleep_score: 'Poäng', sleep_hours: 'Timmar', deep_pct: 'Djup', rem_pct: 'REM'};
      const arrows = {improving: '↑', declining: '↓', stable: '→'};
      trendsEl.innerHTML = Object.entries(summary.trends || {})
        .filter(([, t]) => t)
        .map(([field, t]) => `<span class="sl-trend sl-trend-${t.direction}">`
          + `${arrows[t.direction] || ''} ${escapeHtml(labels[field] || field)}</span>`)
        .join('');
    }

    // Läggdagsrytm
    const consistency = summary.consistency;
    const consistencyEl = document.getElementById('sl-consistency');
    const pill = document.getElementById('sl-consistency-pill');
    if (consistency && consistencyEl) {
      const words = {steady: 'Jämn', drifting: 'Vandrar', irregular: 'Oregelbunden'};
      if (pill) {
        pill.textContent = words[consistency.verdict] || '';
        pill.className = 'sl-pill sl-pill-' + consistency.verdict;
      }
      consistencyEl.innerHTML = `
        <div class="sl-con-main"><strong>${escapeHtml(consistency.averageBedtime || '–')}</strong>
          <span>snittid för sänggående</span></div>
        <div class="sl-con-range">
          <span>Tidigast <b>${escapeHtml(consistency.earliest || '–')}</b></span>
          <span>Senast <b>${escapeHtml(consistency.latest || '–')}</b></span>
        </div>
        <p class="sl-con-note">Spridning ±${consistency.spreadMin} min över ${consistency.nights} nätter.
          ${consistency.verdict === 'steady'
            ? 'Kroppen vet när den ska sova — behåll det.'
            : 'Jämnare tider ger djupare sömn även vid samma antal timmar.'}</p>`;
    }

    // Bästa och sämsta natt
    const extremes = document.getElementById('sl-extremes');
    if (extremes && summary.best) {
      const row = (item, label, tone) => `<div class="sl-extreme">
        <span class="sl-extreme-label">${label}</span>
        <strong class="${tone}">${item.score}</strong>
        <span class="sl-extreme-date">${escapeHtml(item.date)}</span></div>`;
      extremes.innerHTML = row(summary.best, 'Bäst', 'sl-good')
        + (summary.worst ? row(summary.worst, 'Sämst', 'sl-low') : '');
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

      renderDailyActivity(h.daily || {});

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
      await Promise.all([loadHealth(), loadRecentActivities(), loadTrainingLoad(), loadTrainingReview(true), loadInsights(), loadPlan(), loadStrain(), loadSessionVerdict()]);
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
    } catch(e) { console.error('Notes error:', e); }
  }

  // Ladda notes direkt vid start
  loadNotes();

  // Garmin-aktiviteter cached globalt för coachens volyms- och loadberäkning
  let recentActivities = [];
  async function loadRecentActivities(refresh = true) {
    try {
      const res = await fetch(`/api/activities?days=120&refresh=${refresh ? '1' : '0'}&calendar=1`);
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

  // Dagens belastning vägd mot vad kroppen normalt tål (se strain_analysis.py).
  const STRAIN_TONE_CLASS = { good:'good', warn:'bad', watch:'warn', neutral:'' };

  function strainBarClass(strain) {
    if (strain >= 60) return 'is-high';
    if (strain >= 30) return 'is-mid';
    if (strain > 0)   return 'is-low';
    return '';
  }

  function renderStrain(data) {
    const headline = document.getElementById('strain-headline');
    const score    = document.getElementById('strain-score');
    const detail   = document.getElementById('strain-detail');
    if (!headline || !score) return;

    headline.textContent = data.headline || '–';
    score.textContent    = data.strain ?? '--';
    if (detail) detail.textContent = data.detail || '';

    const card = document.getElementById('strain-card');
    if (card) {
      const colors = { good:'var(--green)', warn:'var(--red)', watch:'var(--amber)', neutral:'var(--muted2)' };
      card.style.setProperty('--cockpit-color', colors[data.tone] || 'var(--muted2)');
    }

    const weekAvg = document.getElementById('strain-week-avg');
    const streak  = document.getElementById('strain-streak');
    const load    = document.getElementById('strain-load');
    if (weekAvg) weekAvg.textContent = data.weekAvgStrain ?? '--';
    if (streak)  streak.textContent  = data.consecutiveHighDays ?? '--';
    if (load)    load.textContent    = data.load ? Math.round(data.load) : '0';

    // Var referensen kommer ifrån avgör hur mycket siffran är värd att lita på.
    const note = document.getElementById('strain-reference-note');
    if (note) {
      const sources = {
        garmin:  ref => `Mot din kroniska belastning (${ref})`,
        history: ref => `Mot ditt 28-dagarssnitt (${ref})`,
      };
      const describe = sources[data.referenceSource];
      note.textContent = describe && data.referenceLoad
        ? describe(Math.round(data.referenceLoad))
        : 'För lite historik — siffran är preliminär';
    }

    const bars = document.getElementById('strain-bars');
    if (bars) {
      const series = data.series || [];
      bars.innerHTML = series.map((point, index) => {
        const height = Math.max(2, Math.round(point.strain * 0.56));
        const today = index === series.length - 1 ? ' is-today' : '';
        return `<div class="strain-bar ${strainBarClass(point.strain)}${today}"
                     style="height:${height}px"
                     title="${escapeHtml(point.t)}: belastning ${Math.round(point.load)} → ${point.strain}"></div>`;
      }).join('');
    }
  }

  async function loadStrain() {
    try {
      const res = await fetch('/api/strain');
      const data = await res.json();
      if (data.error) return;
      renderStrain(data);
    } catch(e) {}
  }
  loadStrain();

  // Omdömet om det senast genomförda passet.
  function renderSessionVerdict(verdict) {
    const title   = document.getElementById('verdict-title');
    const tag     = document.getElementById('verdict-tag');
    const detail  = document.getElementById('verdict-detail');
    const timing  = document.getElementById('verdict-timing');
    const actions = document.getElementById('verdict-actions');
    if (!title) return;

    if (!verdict) {
      title.textContent = 'Inget bedömt pass än';
      if (tag) { tag.textContent = 'VÄNTAR'; tag.className = 'cockpit-tag'; }
      if (detail) detail.textContent = 'Ett omdöme skrivs när synken hittar ett nytt pass.';
      if (timing) timing.style.display = 'none';
      if (actions) actions.innerHTML = '';
      return;
    }

    title.textContent = verdict.name || verdict.headline || 'Pass';
    if (tag) {
      const tags = { easy:['', 'LÄTT'], moderate:['good','MÅTTLIGT'], hard:['warn','HÅRT'], very_hard:['bad','MYCKET HÅRT'] };
      const [tone, label] = tags[verdict.intensity] || ['', 'PASS'];
      tag.className = 'cockpit-tag' + (tone ? ' ' + tone : '');
      tag.textContent = label;
    }
    if (detail) detail.textContent = `${verdict.date} · ${verdict.detail || ''}`;
    if (timing) {
      timing.textContent = verdict.timing || '';
      timing.style.display = verdict.timing ? '' : 'none';
    }
    if (actions) {
      actions.innerHTML = (verdict.recovery || []).map(action => `
        <div class="verdict-action">
          <div>
            <div class="verdict-action-title">${escapeHtml(action.title)}</div>
            <div class="verdict-action-why">${escapeHtml(action.why)}</div>
          </div>
        </div>`).join('') || '<div class="verdict-empty">Inga åtgärder föreslagna.</div>';
    }
  }

  async function loadSessionVerdict() {
    try {
      const res = await fetch('/api/session-verdict?limit=1');
      const data = await res.json();
      if (data.error) return;
      renderSessionVerdict(data.latest);
    } catch(e) {}
  }
  loadSessionVerdict();

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

  function normalizeClockInput(value) {
    const raw = String(value || '').trim();
    let match = raw.match(/^(\d{1,2}):(\d{2})$/);
    if (!match && /^\d{3,4}$/.test(raw)) {
      match = [raw, raw.slice(0, -2), raw.slice(-2)];
    }
    if (!match) return null;
    const hour = Number(match[1]);
    const minute = Number(match[2]);
    if (hour < 0 || hour > 23 || minute < 0 || minute > 59) return null;
    return `${String(hour).padStart(2, '0')}:${String(minute).padStart(2, '0')}`;
  }

  async function saveAcBedtime() {
    const inp = document.getElementById('ac-bedtime-input');
    const status = document.getElementById('ac-bedtime-status');
    const btn = document.getElementById('ac-bedtime-save');
    if (!inp || !status || !btn) return;
    const bedtime = normalizeClockInput(inp.value);
    if (!bedtime) {
      status.textContent = 'Skriv en giltig tid, t.ex. 22:00 eller 2200.';
      status.style.color = 'var(--amber)';
      return;
    }
    inp.value = bedtime;
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
      const res = await fetch('/api/assistant', { method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify({ message:msg, context:buildCTX(), history }) });
      const data = await res.json();
      const raw = data.reply || data.error || 'Inget svar.';
      const reply = escapeHtml(raw)
        .replace(/\*\*(.*?)\*\*/gs, '$1')
        .replace(/\*(.*?)\*/gs, '$1')
        .replace(/#{1,3} (.*)/g, '$1')
        .replace(/\n/g, '<br>');
      aDiv.innerHTML = '<div class="msg-from">COACH</div>' + reply;
      history.push({ role:'assistant', content:raw });
      if (data.planAdjusted) loadPlan();
    } catch(e) {
      aDiv.innerHTML = '<div class="msg-from">COACH</div>Kunde inte nå servern.';
    }
    box.scrollTop = box.scrollHeight;
  }

  function qa(t) { send(t); }

  // --- Styrka ---
  const SUGGESTIONS = ['Bänkpress','Marklyft','Knäböj','Axelpress','Latsdrag','Rodd','Dips','Chins','Bicepscurl','Tricepspress','Benpress','Vadpress','Planka','Situps','Rumänsk marklyft','Frontböj','Bulgarisk utfall','Bröststödd rodd','Flyes','Tricepspushdown','Hammarcurl','Face pull','Bål','Ryggresning'];

  const fmtDur = s => { const h=Math.floor(s/3600), m=Math.floor((s%3600)/60); return h>0?h+'h '+m+'m':m+' min'; };
  const fmtDateStr = s => new Date(s).toLocaleDateString('sv-SE',{weekday:'short',day:'numeric',month:'short'});

  // ANALYS — en sammanhållen bild av utveckling, genomförande och mål.
  let analysisData = null;
  let analysisWindowDays = 60;
  let analysisMetricKey = 'vo2max';

  const ANALYSIS_DIRECTIONS = {
    improving: ['↗', 'Förbättras'], declining: ['↘', 'Behöver vändas'],
    stable: ['→', 'Stabil'], rising: ['↑', 'Stiger'], falling: ['↓', 'Sjunker'],
    unknown: ['·', 'Samlar data'],
  };

  function analysisMetricValue(value, fmt) {
    if (value == null) return '–';
    if (fmt === 'pace') {
      const minutes = Math.floor(value / 60);
      return minutes + ':' + String(Math.round(value % 60)).padStart(2, '0');
    }
    if (fmt === 1) return Number(value).toFixed(1);
    return Math.round(value).toLocaleString('sv-SE');
  }

  function analysisUnit(metric) {
    if (metric.latest == null) return '';
    if (metric.fmt === 'pace') return '/km';
    return metric.unit || '';
  }

  function analysisRate(metric) {
    const value = metric.slopePerWeek;
    if (value == null || metric.samples < 2) return metric.samples ? 'Behöver mer historik' : 'Ingen Garmin-data';
    const sign = value > 0 ? '+' : value < 0 ? '−' : '';
    const amount = metric.fmt === 'pace'
      ? Math.abs(value).toFixed(1) + ' s/km'
      : (Math.abs(value) < 1 ? Math.abs(value).toFixed(2) : Math.abs(value).toFixed(1)) + (metric.unit ? ' ' + metric.unit : '');
    return `${sign}${amount} per vecka`;
  }

  function analysisMiniLine(series) {
    if (!series || series.length < 2) return '<svg class="an-mini-line"></svg>';
    const W = 180, H = 28, pad = 2;
    const values = series.map(p => Number(p.v));
    let lo = Math.min(...values), hi = Math.max(...values);
    if (hi === lo) { hi += 1; lo -= 1; }
    const points = values.map((v, i) => [pad + i / (values.length - 1) * (W - pad * 2), pad + (1 - (v - lo) / (hi - lo)) * (H - pad * 2)]);
    const path = points.map((p, i) => `${i ? 'L' : 'M'}${p[0].toFixed(1)} ${p[1].toFixed(1)}`).join(' ');
    return `<svg class="an-mini-line" viewBox="0 0 ${W} ${H}" preserveAspectRatio="none" aria-hidden="true">
      <path d="${path}" fill="none" stroke="var(--muted2)" stroke-width="1.5" opacity=".7" vector-effect="non-scaling-stroke"/>
    </svg>`;
  }

  function renderAnalysisChart() {
    if (!analysisData) return;
    const metrics = (analysisData.metrics || []).filter(m => m.samples > 0);
    if (!metrics.some(m => m.key === analysisMetricKey)) analysisMetricKey = metrics[0]?.key || '';
    const tabs = document.getElementById('an-metric-tabs');
    tabs.innerHTML = metrics.map(m => `<button class="an-metric-tab ${m.key === analysisMetricKey ? 'active' : ''}"
      data-action="analysis-metric" data-metric="${escapeHtml(m.key)}">${escapeHtml(m.label)}</button>`).join('');

    const metric = metrics.find(m => m.key === analysisMetricKey);
    const chart = document.getElementById('an-chart');
    const note = document.getElementById('an-chart-note');
    if (!metric || metric.series.length < 2) {
      chart.innerHTML = '<div class="an-chart-empty">Behöver minst två mätningar för att rita en kurva.</div>';
      note.textContent = metric ? analysisRate(metric) : 'Ingen historik ännu';
      return;
    }
    const W = 720, H = 190, left = 48, right = 16, top = 18, bottom = 28;
    const values = metric.series.map(p => Number(p.v));
    let lo = Math.min(...values), hi = Math.max(...values);
    const margin = Math.max((hi - lo) * .18, metric.fmt === 'pace' ? 2 : .5);
    lo -= margin; hi += margin;
    const t0 = new Date(metric.series[0].t).getTime();
    const t1 = new Date(metric.series.at(-1).t).getTime();
    const x = t => left + ((new Date(t).getTime() - t0) / Math.max(1, t1 - t0)) * (W - left - right);
    const y = v => top + (1 - (v - lo) / Math.max(1, hi - lo)) * (H - top - bottom);
    const pts = metric.series.map(p => [x(p.t), y(Number(p.v))]);
    const line = pts.map((p, i) => `${i ? 'L' : 'M'}${p[0].toFixed(1)} ${p[1].toFixed(1)}`).join(' ');
    const area = `${line} L${pts.at(-1)[0].toFixed(1)} ${H-bottom} L${pts[0][0].toFixed(1)} ${H-bottom} Z`;
    const dateFmt = value => new Date(value).toLocaleDateString('sv-SE', {day:'numeric', month:'short'});
    const unit = analysisUnit(metric);
    const dm = ANALYSIS_DIRECTIONS[metric.direction] || ANALYSIS_DIRECTIONS.unknown;
    note.textContent = `${dm[0]} ${dm[1]} · ${analysisRate(metric)}`;
    chart.innerHTML = `<svg viewBox="0 0 ${W} ${H}" preserveAspectRatio="xMidYMid meet" role="img" aria-label="${escapeHtml(metric.label)} över tid">
      <defs><linearGradient id="an-chart-gradient" x1="0" y1="0" x2="0" y2="1"><stop offset="0" stop-color="#C8F135" stop-opacity=".18"/><stop offset="1" stop-color="#C8F135" stop-opacity="0"/></linearGradient></defs>
      ${[0,.5,1].map(f => `<line class="an-chart-grid" x1="${left}" x2="${W-right}" y1="${(top+f*(H-top-bottom)).toFixed(1)}" y2="${(top+f*(H-top-bottom)).toFixed(1)}"/>`).join('')}
      <path class="an-chart-area" d="${area}"/><path class="an-chart-line" d="${line}" vector-effect="non-scaling-stroke"/>
      <circle class="an-chart-dot" cx="${pts.at(-1)[0].toFixed(1)}" cy="${pts.at(-1)[1].toFixed(1)}" r="4" vector-effect="non-scaling-stroke"/>
      <text class="an-chart-label" x="${left-7}" y="${top+4}" text-anchor="end">${analysisMetricValue(hi-margin, metric.fmt)}</text>
      <text class="an-chart-label" x="${left-7}" y="${H-bottom}" text-anchor="end">${analysisMetricValue(lo+margin, metric.fmt)}</text>
      <text class="an-chart-label" x="${left}" y="${H-7}">${dateFmt(metric.series[0].t)}</text>
      <text class="an-chart-label" x="${W-right}" y="${H-7}" text-anchor="end">${dateFmt(metric.series.at(-1).t)}</text>
      <text class="an-chart-label" x="${W-right}" y="${top+4}" text-anchor="end">${analysisMetricValue(metric.latest, metric.fmt)} ${escapeHtml(unit)}</text>
    </svg>`;
  }

  function renderAnalysisVolume(volume) {
    const weeks = volume.weeks || [];
    const peak = Math.max(1, ...weeks.map(w => w.km));
    document.getElementById('an-volume-bars').innerHTML = weeks.map((week, index) => `
      <div class="an-volume-bar-wrap"><div class="an-volume-bar ${index === weeks.length - 1 ? 'current' : ''}"
        style="height:${Math.max(2, week.km / peak * 125).toFixed(1)}px" title="${week.km} km · ${week.sessions} pass"><span>${week.km || ''}</span></div>
        <span>${escapeHtml(week.label)}</span></div>`).join('');
    const delta = volume.delta7Pct;
    document.getElementById('an-volume-summary').innerHTML = `
      <div><span>7 dygn</span><strong>${volume.current7Km ?? 0} km</strong></div>
      <div><span>4 veckor snitt</span><strong>${volume.average4WeeksKm ?? 0} km</strong></div>
      <div><span>Mot föregående</span><strong>${delta == null ? '–' : (delta > 0 ? '+' : '') + delta + '%'}</strong></div>`;
  }

  function renderAnalysisGoal(goal) {
    const el = document.getElementById('an-goal');
    if (!goal?.title) {
      el.innerHTML = '<p class="an-empty">Sätt ett träningsmål så visar analysen hur nuvarande kapacitet förhåller sig till det.</p>';
      return;
    }
    const f = goal.feasibility;
    const verdicts = {within_reach:['Inom räckhåll','good'], stretch:['Utmanande','warn'], out_of_reach:['Gap kvar','warn']};
    const verdict = verdicts[f?.verdict] || ['Samlar tempodata',''];
    const capable = f?.currentCapablePace || goal.anchor?.ltPace || '–';
    const target = f?.goalPace || goal.goalPace?.pace || '–';
    const progress = f ? Math.max(8, Math.min(100, 100 - Math.max(0, f.gapSec || 0) * 2.5)) : 12;
    const deadline = goal.deadline ? new Date(goal.deadline + 'T12:00:00').toLocaleDateString('sv-SE',{day:'numeric',month:'short',year:'numeric'}) : null;
    el.innerHTML = `<div class="an-goal-title">${escapeHtml(goal.title)}</div>
      <div class="an-goal-meta">${deadline ? `<span class="an-chip">${escapeHtml(deadline)}</span>` : ''}
        ${goal.daysLeft != null ? `<span class="an-chip">${goal.daysLeft} dagar kvar</span>` : ''}
        <span class="an-chip ${verdict[1] ? 'an-chip-' + verdict[1] : ''}">${verdict[0]}</span></div>
      <div class="an-goal-track"><i style="width:${progress}%"></i></div>
      <p class="an-goal-copy">Målfart <strong>${escapeHtml(target)}</strong> · nuvarande uppskattad kapacitet <strong>${escapeHtml(capable)}</strong>.
        ${f?.gapSec > 0 ? `Gapet är cirka ${f.gapSec} sekunder per kilometer.` : f ? 'Kapaciteten stödjer målfarten just nu.' : 'Fler tröskelmätningar krävs för en ärlig bedömning.'}</p>`;
  }

  function renderAnalysisExecution(execution) {
    const adherence = execution.adherencePct;
    const quality = execution.qualityPct;
    document.getElementById('an-execution').innerHTML = `<div class="an-execution-grid">
      <div class="an-execution-stat"><span>Genomförda</span><strong>${execution.completed ?? 0}</strong></div>
      <div class="an-execution-stat"><span>Missade</span><strong>${execution.missed ?? 0}</strong></div>
      <div class="an-execution-stat"><span>Utvärderade</span><strong>${execution.evaluated ?? 0}</strong></div>
      </div><p class="an-execution-note">${adherence == null ? 'Inga avgjorda planpass i perioden ännu.' : `${adherence}% planföljsamhet.`}
      ${quality == null ? ' När fler pass har tempo-, puls- eller styrkedata visas även kvaliteten.' : ` ${quality}% av utvärderade pass genomfördes utan tydliga avvikelser.`}</p>`;
  }

  // Trendpoängen är meningslös utan sin uträkning — posterna kommer från
  // training_analysis.overview() och summerar alltid till rawScore.
  function closeTrendBreakdown() {
    document.getElementById('trend-modal')?.remove();
  }

  function openTrendBreakdown() {
    if (document.getElementById('trend-modal')) return;
    const overview = (analysisData || {}).overview || {};
    const entries = overview.breakdown || [];
    if (!entries.length) return;

    const rows = entries.map(entry => {
      const delta = entry.delta;
      const sign = delta == null ? '–'
        : (delta > 0 ? '+' + delta : (delta === 0 ? '±0' : String(delta)));
      return `<div class="tb-row tb-${escapeHtml(entry.tone || 'neutral')}">
          <span class="tb-delta">${escapeHtml(sign)}</span>
          <span class="tb-text">
            <strong>${escapeHtml(entry.label)}</strong>
            <span>${escapeHtml(entry.detail)}</span>
          </span>
        </div>`;
    }).join('');

    const scale = overview.scale || {};
    const clamped = overview.rawScore != null && overview.rawScore !== overview.score;

    document.body.insertAdjacentHTML('beforeend', `
      <div id="trend-modal" class="tb-backdrop">
        <div class="tb-panel" role="dialog" aria-modal="true" aria-label="Så räknas trendpoängen">
          <div class="tb-head">
            <div>
              <div class="tb-kicker">Trendpoäng</div>
              <h2>${escapeHtml(String(overview.score ?? '–'))}<span class="tb-outof"> / ${escapeHtml(String(scale.max ?? 100))}</span></h2>
              <div class="tb-verdict">${escapeHtml(overview.title || '')}</div>
            </div>
            <button type="button" class="tb-close" data-action="close-trend-breakdown" aria-label="Stäng">✕</button>
          </div>
          <div class="tb-rows">${rows}</div>
          <div class="tb-foot">
            <div><strong>Summa</strong> ${escapeHtml(String(overview.base ?? 60))} i utgångsläge${clamped
              ? ` → ${escapeHtml(String(overview.rawScore))}, begränsat till ${escapeHtml(String(overview.score))}`
              : ` → ${escapeHtml(String(overview.score ?? '–'))}`}.
              Skalan går mellan ${escapeHtml(String(scale.min ?? 20))} och ${escapeHtml(String(scale.max ?? 95))}.</div>
            ${overview.confidenceNote ? `<div class="tb-confidence">${escapeHtml(overview.confidenceNote)}</div>` : ''}
          </div>
        </div>
      </div>`);
  }

  function renderAnalysis(data) {
    analysisData = data;
    const overview = data.overview || {};
    const volume = data.volume || {};
    const execution = data.execution || {};
    const setText = (id, value) => { const el = document.getElementById(id); if (el) el.textContent = value; };
    const colors = {building:'var(--accent)',steady:'var(--blue)',attention:'var(--amber)',collecting:'var(--muted2)'};
    const ring = document.getElementById('an-score-ring');
    ring.style.strokeDashoffset = 358.14 * (1 - (overview.score || 0) / 100);
    ring.style.stroke = colors[overview.status] || 'var(--accent)';
    setText('an-score', overview.score ?? '–');
    setText('an-verdict', overview.title || 'Samlar en tydligare trendbild');
    setText('an-confidence', `UNDERLAG ${overview.confidencePct ?? 0}% · ${overview.signals ?? 0} TRENDMARKÖRER`);
    setText('an-volume', `${volume.current7Km ?? 0} km`);
    const delta = volume.delta7Pct;
    setText('an-volume-sub', `${volume.current7Sessions ?? 0} pass${delta == null ? '' : ` · ${delta > 0 ? '+' : ''}${delta}% mot föregående 7 dygn`}`);
    setText('an-adherence', execution.adherencePct == null ? '–' : execution.adherencePct + '%');
    setText('an-adherence-sub', `${execution.completed ?? 0} genomförda · ${execution.missed ?? 0} missade`);
    setText('an-quality', execution.qualityPct == null ? '–' : execution.qualityPct + '%');
    setText('an-quality-sub', execution.evaluated ? `${execution.onTarget} av ${execution.evaluated} utvärderade pass` : 'Behöver pass med utförandedata');
    const threshold = (data.metrics || []).find(m => m.key === 'lt_pace');
    setText('an-threshold', threshold?.latest != null ? analysisMetricValue(threshold.latest, 'pace') + '/km' : '–');
    setText('an-threshold-sub', threshold ? analysisRate(threshold) : 'Ingen tröskeldata');

    const priorities = overview.priorities || [];
    document.getElementById('an-focus').innerHTML = '<div class="an-focus-label">Fokus nu</div>' + priorities.map(p =>
      `<div class="an-priority an-priority-${escapeHtml(p.tone || 'neutral')}"><strong>${escapeHtml(p.title)}</strong><span>${escapeHtml(p.detail)}</span></div>`).join('');

    document.getElementById('an-metrics').innerHTML = (data.metrics || []).map(metric => {
      const dm = ANALYSIS_DIRECTIONS[metric.direction] || ANALYSIS_DIRECTIONS.unknown;
      return `<div class="an-metric" data-action="analysis-metric" data-metric="${escapeHtml(metric.key)}" role="button" tabindex="0">
        <div class="an-metric-top"><span class="an-metric-name">${escapeHtml(metric.label)}</span>
          <span class="an-direction an-direction-${escapeHtml(metric.direction)}">${dm[0]} ${dm[1]}</span></div>
        <div class="an-metric-value">${analysisMetricValue(metric.latest, metric.fmt)} <small>${escapeHtml(analysisUnit(metric))}</small></div>
        <div class="an-metric-rate">${escapeHtml(analysisRate(metric))}</div>${analysisMiniLine(metric.series)}</div>`;
    }).join('');

    renderAnalysisChart();
    renderAnalysisVolume(volume);
    renderAnalysisGoal(data.goal);
    renderAnalysisExecution(execution);
  }

  function selectAnalysisMetric(key) {
    analysisMetricKey = key || analysisMetricKey;
    renderAnalysisChart();
    document.querySelector('.an-trend-card')?.scrollIntoView({behavior:'smooth', block:'nearest'});
  }

  function setAnalysisWindow(days) {
    if (![30, 60, 90].includes(days) || days === analysisWindowDays) return;
    analysisWindowDays = days;
    document.querySelectorAll('.an-window-btn').forEach(btn => btn.classList.toggle('active', Number(btn.dataset.days) === days));
    loadAnalysis();
  }

  async function loadAnalysis() {
    const loading = document.getElementById('analysis-loading');
    const content = document.getElementById('analysis-content');
    loading.style.display = 'block';
    loading.textContent = 'Bygger din trendbild…';
    content.style.display = 'none';
    try {
      const response = await fetch(`/api/analysis?days=${analysisWindowDays}`);
      const data = await response.json();
      if (!response.ok) throw new Error(data.error || 'Analysen kunde inte hämtas.');
      renderAnalysis(data);
      loading.style.display = 'none';
      content.style.display = 'block';
    } catch (error) {
      loading.textContent = 'Kunde inte ladda analysen: ' + error.message;
      loading.style.color = 'var(--red)';
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
  document.getElementById('chat-input')?.addEventListener('keypress', e => { if (e.key === 'Enter') send(); });

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
    const name = activity.activityName || activity.name || activity.activityType?.typeKey
      || (activity.source === 'strava' ? 'Strava-aktivitet' : 'Garmin-aktivitet');
    const km = activity.distance ? ' · ' + (activity.distance / 1000).toFixed(1) + ' km' : '';
    return name + km;
  }

  function activitySourceLabel(activity) {
    return activity?.source === 'strava' ? 'Strava' : 'Garmin';
  }

  function activityOpenAttrs(activity) {
    const id = Number(activity?.activityId || activity?.id);
    if (!Number.isSafeInteger(id) || id <= 0) return '';
    const source = activity?.source === 'strava' ? 'strava' : 'garmin';
    return ` data-action="open-activity" data-activity-id="${id}" data-activity-source="${source}" role="button" tabindex="0" title="Öppna passdetaljer"`;
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

  // Utvärderingen av ett genomfört pass (se session_analysis.py). Allt utom
  // dessa två flaggor är något att åtgärda, inte att berömma.
  const EXECUTION_POSITIVE_FLAGS = new Set([
    'negative_split_reps', 'strength_on_target', 'easy_run_slower_than_target',
  ]);

  function executionIsPositive(execution) {
    const flags = execution?.flags || [];
    return !flags.length || flags.every(flag => EXECUTION_POSITIVE_FLAGS.has(flag));
  }

  function executionDetailLines(execution) {
    if (!execution) return [];
    const lines = [];

    if (execution.discipline === 'strength') {
      (execution.exercises || []).forEach(item => {
        if (!item.weight) return;
        const target = item.targetWeight ? ` mot mål ${item.targetWeight} kg` : '';
        const delta = item.deltaPct != null ? ` (${item.deltaPct > 0 ? '+' : ''}${item.deltaPct}%)` : '';
        lines.push(`${item.exercise}: ${item.weight} kg${target}${delta}`);
      });
      return lines;
    }

    if (execution.avgPace) {
      const target = execution.targetPace ? ` mot mål ${execution.targetPace.text}` : '';
      const delta = execution.paceDeltaPct != null
        ? ` (${execution.paceDeltaPct > 0 ? '+' : ''}${execution.paceDeltaPct}%)` : '';
      lines.push(`Snittempo ${execution.avgPace}${target}${delta}`);
    }
    if (execution.reps?.length) {
      const paces = execution.reps.map(rep => rep.pace).filter(Boolean).join(', ');
      if (paces) lines.push(`Rep: ${paces}`);
      if (execution.fadePct != null) {
        lines.push(`Första till sista rep: ${execution.fadePct > 0 ? '+' : ''}${execution.fadePct}%`);
      }
    }
    if (execution.hrDrift) {
      lines.push(`Pulsdrift ${execution.hrDrift.firstHalf} → ${execution.hrDrift.secondHalf} slag`
        + ` (${execution.hrDrift.pct > 0 ? '+' : ''}${execution.hrDrift.pct}%)`);
    }
    if (execution.plannedKm && execution.distanceKm) {
      lines.push(`${execution.distanceKm} km av planerade ${execution.plannedKm} km`);
    }
    return lines;
  }

  function executionBadgeHtml(execution) {
    if (!execution?.headline) return '';
    const cls = executionIsPositive(execution) ? 'cal-verdict-good' : 'cal-verdict-warn';
    const tip = [execution.headline, ...executionDetailLines(execution)].join(' - ');
    return `<span class="cal-verdict ${cls}" data-freetip="${escapeHtml(tip)}">`
      + `${escapeHtml(execution.headline)}</span>`;
  }

  function calendarActualPills(dayActivities, plannedSession) {
    if (!dayActivities.length) return '';
    const runs = dayActivities.filter(a => calendarActivityType(a) === 'run');
    const lifts = dayActivities.filter(a => calendarActivityType(a) === 'lift');
    const totalRunKm = runs.reduce((sum, a) => sum + ((a.distance || 0) / 1000), 0);
    const totalSec = dayActivities.reduce((sum, a) => sum + (a.duration || a.elapsedDuration || 0), 0);
    const minutes = totalSec ? Math.round(totalSec / 60) : null;

    const verdict = executionBadgeHtml(plannedSession?.execution);

    if (plannedSession?.type === 'lift' && lifts.length) {
      const label = plannedSession.title || 'Styrkepass';
      const tip = [activitySourceLabel(lifts[0]), label, minutes != null ? minutes + ' min' : ''].filter(Boolean).join(' - ');
      return `<span class="cal-session-pill csp-lift csp-done csp-actual"${activityOpenAttrs(lifts[0])} data-freetip="${escapeHtml(tip)}">${escapeHtml(label)}</span>${verdict}`;
    }

    if (runs.length) {
      if (runs.length > 1) {
        const pills = runs.map(run => {
          const label = calendarActivityLabel(run);
          const seconds = run.duration || run.elapsedDuration || 0;
          const tip = [activitySourceLabel(run), label, seconds ? Math.round(seconds / 60) + ' min' : ''].filter(Boolean).join(' - ');
          return `<span class="cal-session-pill csp-run csp-done csp-actual"${activityOpenAttrs(run)} data-freetip="${escapeHtml(tip)}">${escapeHtml(label)}</span>`;
        }).join('');
        return pills + verdict;
      }
      const interval = runs.find(a => a.calendarSummary?.kind === 'interval');
      const label = interval?.calendarSummary?.label
        ? `${interval.calendarSummary.label}${totalRunKm ? ' · ' + totalRunKm.toFixed(1) + ' km' : ''}`
        : calendarActivityLabel(runs[0]);
      const names = runs.map(calendarActivityLabel).join(' - ');
      const tip = [activitySourceLabel(runs[0]), names, minutes != null ? minutes + ' min' : ''].filter(Boolean).join(' - ');
      return `<span class="cal-session-pill csp-run csp-done csp-actual"${activityOpenAttrs(runs[0])} data-freetip="${escapeHtml(tip)}">${escapeHtml(label)}</span>${verdict}`;
    }

    return dayActivities.map(activity => {
      const actualType = calendarActivityType(activity);
      const cls = actualType === 'lift' ? 'csp-lift' : actualType === 'race' ? 'csp-race' : 'csp-rest';
      const label = calendarActivityLabel(activity);
      const seconds = activity.duration || activity.elapsedDuration || 0;
      const mins = seconds ? Math.round(seconds / 60) : null;
      const tip = [activitySourceLabel(activity), label, mins != null ? mins + ' min' : ''].filter(Boolean).join(' - ');
      return `<span class="cal-session-pill ${cls} csp-done csp-actual"${activityOpenAttrs(activity)} data-freetip="${escapeHtml(tip)}">${escapeHtml(label)}</span>`;
    }).join('');
  }

  // Planerat pass för ett visst datum. Ett genomfört pass bär utvärderingen
  // i .execution, så både startsidan och kalendern går via den här.
  function findPlanSessionForDate(dateKey, planYear = new Date().getFullYear()) {
    let found = null;
    for (const session of PLAN_SESSIONS) {
      const monday = getMondayOfISOWeek(session.week, planYear);
      const sessionDate = new Date(monday);
      sessionDate.setDate(monday.getDate() + session.dow);
      if (localDateKey(sessionDate) !== dateKey) continue;
      if (!found || (session.status === 'planned' && found.status !== 'planned')) found = session;
    }
    return found;
  }

  function renderTodaySession() {
    const card  = document.getElementById('today-session-card');
    const dot   = document.getElementById('today-session-dot');
    const title = document.getElementById('today-session-title');
    const detail= document.getElementById('today-session-detail');
    const km    = document.getElementById('today-session-km');
    const type  = document.getElementById('today-session-type');
    if (!card || !dot || !title || !detail || !km || !type) return;

    // Rendering can switch between a completed activity and a planned session;
    // always clear the previous interaction before deciding what today contains.
    card.classList.remove('is-clickable');
    for (const attribute of ['data-action', 'data-activity-id', 'data-activity-source',
                             'role', 'tabindex', 'title']) {
      card.removeAttribute(attribute);
    }

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
      const activityId = Number(longest.activityId || longest.id);
      const typeKey  = longest.activityType?.typeKey || '';
      let   planType = 'run';
      if (/strength|fitness_equipment|weight/i.test(typeKey)) planType = 'lift';
      else if (/track/i.test(typeKey))                         planType = 'run';

      const col = typeColors[planType] || 'var(--green)';

      if (Number.isSafeInteger(activityId) && activityId > 0) {
        card.classList.add('is-clickable');
        card.dataset.action = 'open-activity';
        card.dataset.activityId = String(activityId);
        card.dataset.activitySource = longest.source === 'strava' ? 'strava' : 'garmin';
        card.setAttribute('role', 'button');
        card.setAttribute('tabindex', '0');
        card.title = todayActs.length > 1
          ? 'Öppna den längsta av dagens aktiviteter' : 'Öppna passdetaljer';
      }

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
      km.textContent         = totalKm > 0 ? totalKm.toFixed(1) + ' km' : timeStr;
      km.style.color         = col;

      // Säg hur passet gick, inte bara att det blev av. Rubriken ovanför
      // namnger redan passet, så raden används till siffrorna i stället.
      const execution = findPlanSessionForDate(todayKey)?.execution;
      const verdictLines = executionDetailLines(execution);
      detail.textContent = verdictLines.length ? verdictLines.join('  ·  ') : detailStr;
      detail.title = verdictLines.length
        ? [detailStr, ...verdictLines].join('\n')
        : detailStr;
      if (execution?.headline) {
        type.textContent  = execution.headline.toUpperCase();
        type.style.color  = executionIsPositive(execution) ? 'var(--green)' : 'var(--amber)';
      } else {
        type.textContent  = 'KLART';
        type.style.color  = '';
      }
      return;
    }

    // ── 2. Fall back to today's planned session ───────────────────────────
    const s = findPlanSessionForDate(todayKey);

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

  // ─── PASSDETALJ ──────────────────────────────────────────────
  let activityDetailRequest = 0;
  let activityModalPreviousFocus = null;
  let activityMapState = null;
  const ACTIVITY_MAP_TILE_SIZE = 256;
  const ACTIVITY_MAP_MIN_ZOOM = 3;
  const ACTIVITY_MAP_MAX_ZOOM = 18;

  function formatActivityDuration(seconds) {
    if (seconds == null) return '–';
    const total = Math.max(0, Math.round(Number(seconds)));
    const h = Math.floor(total / 3600);
    const m = Math.floor((total % 3600) / 60);
    const s = total % 60;
    return h ? `${h}:${String(m).padStart(2, '0')}:${String(s).padStart(2, '0')}`
      : `${m}:${String(s).padStart(2, '0')}`;
  }

  function formatActivityPace(seconds) {
    if (seconds == null || !Number.isFinite(Number(seconds))) return '–';
    const total = Math.max(0, Math.round(Number(seconds)));
    return `${Math.floor(total / 60)}:${String(total % 60).padStart(2, '0')}`;
  }

  function formatActivityDate(value) {
    if (!value) return '';
    const parsed = new Date(String(value).replace(' ', 'T'));
    if (Number.isNaN(parsed.getTime())) return String(value).slice(0, 16);
    return parsed.toLocaleString('sv-SE', {
      weekday:'long', day:'numeric', month:'long', year:'numeric',
      hour:'2-digit', minute:'2-digit'
    });
  }

  function formatActivityType(value) {
    const key = String(value || '').toLowerCase();
    if (/track/.test(key)) return 'Banlöpning';
    if (/trail/.test(key)) return 'Traillöpning';
    if (/treadmill/.test(key)) return 'Löpband';
    if (/run/.test(key)) return 'Löpning';
    if (/strength|weight|fitness/.test(key)) return 'Styrka';
    if (/cycling|bike/.test(key)) return 'Cykling';
    return key ? key.replaceAll('_', ' ') : 'Garmin-aktivitet';
  }

  function activityMetric(label, value, unit = '') {
    return `<div class="ad-metric"><span>${escapeHtml(label)}</span><strong>${escapeHtml(value)}${unit ? ` <small>${escapeHtml(unit)}</small>` : ''}</strong></div>`;
  }

  function normalizedActivityRoute(route) {
    if (!Array.isArray(route)) return [];
    return route.map(point => ({lat:Number(point?.lat), lon:Number(point?.lon)}))
      .filter(point => Number.isFinite(point.lat) && Number.isFinite(point.lon)
        && point.lat >= -85.05112878 && point.lat <= 85.05112878
        && point.lon >= -180 && point.lon <= 180);
  }

  function activityMapWorldPoint(point, zoom) {
    const sinLatitude = Math.sin(point.lat * Math.PI / 180);
    const worldSize = ACTIVITY_MAP_TILE_SIZE * (2 ** zoom);
    return {
      x: ((point.lon + 180) / 360) * worldSize,
      y: (.5 - Math.log((1 + sinLatitude) / (1 - sinLatitude)) / (4 * Math.PI)) * worldSize,
    };
  }

  function activityRouteMap(route) {
    if (normalizedActivityRoute(route).length < 2) {
      return '<div class="ad-map-empty">Ingen GPS-rutt registrerades för passet.</div>';
    }
    return `<svg class="ad-map-canvas" role="img" aria-label="Interaktiv GPS-rutt för passet">
        <g class="ad-map-tiles"></g>
        <rect class="ad-map-shade"/>
        <path class="ad-route-line-under" vector-effect="non-scaling-stroke"/>
        <path class="ad-route-line" vector-effect="non-scaling-stroke"/>
        <circle class="ad-route-pin ad-route-start" r="6" fill="var(--green)" vector-effect="non-scaling-stroke"/>
        <circle class="ad-route-pin ad-route-end" r="6" fill="var(--red)" vector-effect="non-scaling-stroke"/>
      </svg>
      <div class="ad-map-controls" aria-label="Kartkontroller">
        <button type="button" data-action="activity-map-zoom-in" aria-label="Zooma in" title="Zooma in">+</button>
        <button type="button" data-action="activity-map-zoom-out" aria-label="Zooma ut" title="Zooma ut">−</button>
        <button type="button" data-action="activity-map-reset" aria-label="Visa hela rutten" title="Visa hela rutten">⌖</button>
      </div>
      <button class="ad-map-expand" type="button" data-action="activity-map-expand" aria-label="Öppna stor karta"><span>Öppna karta</span><b>↗</b></button>
      <span class="ad-map-hint">Dra kartan · scrolla eller nyp för zoom</span>
      <span class="ad-map-attribution"><a href="https://www.openstreetmap.org/copyright" target="_blank" rel="noopener noreferrer">© OpenStreetMap</a> · GARMIN GPS</span>`;
  }

  function renderActivityMap() {
    const state = activityMapState;
    if (!state?.element?.isConnected) return;
    const svg = state.element.querySelector('.ad-map-canvas');
    if (!svg) return;
    const width = Math.max(1, state.element.clientWidth);
    const height = Math.max(1, state.element.clientHeight);
    const worldSize = ACTIVITY_MAP_TILE_SIZE * (2 ** state.zoom);
    state.centerX = ((state.centerX % worldSize) + worldSize) % worldSize;
    state.centerY = Math.max(Math.min(height / 2, worldSize / 2),
      Math.min(worldSize - height / 2, state.centerY));
    const originX = state.centerX - width / 2;
    const originY = state.centerY - height / 2;
    const tileCount = 2 ** state.zoom;
    const tileLayer = svg.querySelector('.ad-map-tiles');
    const visibleTiles = new Set();
    const existingTiles = new Map([...tileLayer.querySelectorAll('.ad-map-tile')]
      .map(tile => [tile.dataset.tileKey, tile]));
    const firstTileX = Math.floor(originX / ACTIVITY_MAP_TILE_SIZE);
    const lastTileX = Math.floor((originX + width) / ACTIVITY_MAP_TILE_SIZE);
    const firstTileY = Math.max(0, Math.floor(originY / ACTIVITY_MAP_TILE_SIZE));
    const lastTileY = Math.min(tileCount - 1, Math.floor((originY + height) / ACTIVITY_MAP_TILE_SIZE));
    for (let tileY = firstTileY; tileY <= lastTileY; tileY += 1) {
      for (let tileX = firstTileX; tileX <= lastTileX; tileX += 1) {
        const wrappedTileX = ((tileX % tileCount) + tileCount) % tileCount;
        const tileKey = `${state.zoom}/${tileX}/${tileY}`;
        visibleTiles.add(tileKey);
        let tile = existingTiles.get(tileKey);
        if (!tile) {
          tile = document.createElementNS('http://www.w3.org/2000/svg', 'image');
          tile.classList.add('ad-map-tile');
          tile.dataset.tileKey = tileKey;
          tile.setAttribute('href', `https://tile.openstreetmap.org/${state.zoom}/${wrappedTileX}/${tileY}.png`);
          tile.setAttribute('width', String(ACTIVITY_MAP_TILE_SIZE));
          tile.setAttribute('height', String(ACTIVITY_MAP_TILE_SIZE));
          tileLayer.appendChild(tile);
        }
        tile.setAttribute('x', (tileX * ACTIVITY_MAP_TILE_SIZE - originX).toFixed(1));
        tile.setAttribute('y', (tileY * ACTIVITY_MAP_TILE_SIZE - originY).toFixed(1));
      }
    }
    existingTiles.forEach((tile, key) => { if (!visibleTiles.has(key)) tile.remove(); });
    const routePoints = state.route.map(point => activityMapWorldPoint(point, state.zoom)).map(point => {
      let x = point.x;
      if (x - state.centerX > worldSize / 2) x -= worldSize;
      if (state.centerX - x > worldSize / 2) x += worldSize;
      return [x - originX, point.y - originY];
    });
    const path = routePoints.map((point, index) => `${index ? 'L' : 'M'}${point[0].toFixed(1)} ${point[1].toFixed(1)}`).join(' ');
    const start = routePoints[0], end = routePoints.at(-1);
    svg.setAttribute('viewBox', `0 0 ${width} ${height}`);
    const shade = svg.querySelector('.ad-map-shade');
    shade.setAttribute('width', String(width));
    shade.setAttribute('height', String(height));
    svg.querySelector('.ad-route-line-under').setAttribute('d', path);
    svg.querySelector('.ad-route-line').setAttribute('d', path);
    const startPin = svg.querySelector('.ad-route-start');
    const endPin = svg.querySelector('.ad-route-end');
    startPin.setAttribute('cx', start[0].toFixed(1));
    startPin.setAttribute('cy', start[1].toFixed(1));
    endPin.setAttribute('cx', end[0].toFixed(1));
    endPin.setAttribute('cy', end[1].toFixed(1));
  }

  function scheduleActivityMapRender() {
    const state = activityMapState;
    if (!state || state.renderFrame) return;
    state.renderFrame = requestAnimationFrame(() => {
      if (activityMapState === state) {
        state.renderFrame = 0;
        renderActivityMap();
      }
    });
  }

  function fitActivityMapToRoute() {
    const state = activityMapState;
    if (!state) return;
    const width = Math.max(280, state.element.clientWidth);
    const height = Math.max(240, state.element.clientHeight);
    const padding = state.element.classList.contains('is-expanded') ? 70 : 34;
    let projected = [];
    for (let zoom = ACTIVITY_MAP_MAX_ZOOM; zoom >= ACTIVITY_MAP_MIN_ZOOM; zoom -= 1) {
      const candidate = state.route.map(point => activityMapWorldPoint(point, zoom));
      const xs = candidate.map(point => point.x), ys = candidate.map(point => point.y);
      if (Math.max(...xs) - Math.min(...xs) <= width - padding * 2
          && Math.max(...ys) - Math.min(...ys) <= height - padding * 2) {
        state.zoom = zoom;
        projected = candidate;
        break;
      }
    }
    if (!projected.length) {
      state.zoom = ACTIVITY_MAP_MIN_ZOOM;
      projected = state.route.map(point => activityMapWorldPoint(point, state.zoom));
    }
    const xs = projected.map(point => point.x), ys = projected.map(point => point.y);
    state.centerX = (Math.min(...xs) + Math.max(...xs)) / 2;
    state.centerY = (Math.min(...ys) + Math.max(...ys)) / 2;
    scheduleActivityMapRender();
  }

  function zoomActivityMap(change, clientX, clientY) {
    const state = activityMapState;
    if (!state) return;
    const nextZoom = Math.max(ACTIVITY_MAP_MIN_ZOOM,
      Math.min(ACTIVITY_MAP_MAX_ZOOM, state.zoom + change));
    if (nextZoom === state.zoom) return;
    const rect = state.element.getBoundingClientRect();
    const anchorX = Number.isFinite(clientX) ? clientX - rect.left : rect.width / 2;
    const anchorY = Number.isFinite(clientY) ? clientY - rect.top : rect.height / 2;
    const scale = 2 ** (nextZoom - state.zoom);
    state.centerX = (state.centerX + anchorX - rect.width / 2) * scale - anchorX + rect.width / 2;
    state.centerY = (state.centerY + anchorY - rect.height / 2) * scale - anchorY + rect.height / 2;
    state.zoom = nextZoom;
    scheduleActivityMapRender();
  }

  function initializeActivityMap(route) {
    const element = document.querySelector('#activity-detail-content .ad-route');
    const cleanRoute = normalizedActivityRoute(route);
    if (!element || cleanRoute.length < 2) return;
    const state = {element, route:cleanRoute, zoom:ACTIVITY_MAP_MIN_ZOOM, centerX:0, centerY:0,
      pointers:new Map(), dragStart:null, pinchDistance:0, renderFrame:0, resizeObserver:null};
    activityMapState = state;
    element.addEventListener('wheel', event => {
      event.preventDefault();
      zoomActivityMap(event.deltaY < 0 ? 1 : -1, event.clientX, event.clientY);
    }, {passive:false});
    element.addEventListener('dblclick', event => {
      if (event.target.closest('button,a')) return;
      event.preventDefault();
      zoomActivityMap(1, event.clientX, event.clientY);
    });
    element.addEventListener('pointerdown', event => {
      if (event.target.closest('button,a')) return;
      element.setPointerCapture(event.pointerId);
      state.pointers.set(event.pointerId, {x:event.clientX, y:event.clientY});
      if (state.pointers.size === 1) {
        state.dragStart = {id:event.pointerId, x:event.clientX, y:event.clientY,
          centerX:state.centerX, centerY:state.centerY};
        element.classList.add('is-dragging');
      } else if (state.pointers.size === 2) {
        const [first, second] = [...state.pointers.values()];
        state.pinchDistance = Math.hypot(second.x - first.x, second.y - first.y);
        state.dragStart = null;
      }
    });
    element.addEventListener('pointermove', event => {
      if (!state.pointers.has(event.pointerId)) return;
      state.pointers.set(event.pointerId, {x:event.clientX, y:event.clientY});
      if (state.pointers.size === 1 && state.dragStart?.id === event.pointerId) {
        state.centerX = state.dragStart.centerX - (event.clientX - state.dragStart.x);
        state.centerY = state.dragStart.centerY - (event.clientY - state.dragStart.y);
        scheduleActivityMapRender();
      } else if (state.pointers.size === 2) {
        const [first, second] = [...state.pointers.values()];
        const distance = Math.hypot(second.x - first.x, second.y - first.y);
        const midpointX = (first.x + second.x) / 2;
        const midpointY = (first.y + second.y) / 2;
        if (distance > state.pinchDistance * 1.2) {
          zoomActivityMap(1, midpointX, midpointY);
          state.pinchDistance = distance;
        } else if (distance < state.pinchDistance * .8) {
          zoomActivityMap(-1, midpointX, midpointY);
          state.pinchDistance = distance;
        }
      }
    });
    const endPointer = event => {
      state.pointers.delete(event.pointerId);
      if (state.pointers.size === 1) {
        const [id, point] = [...state.pointers.entries()][0];
        state.dragStart = {id, x:point.x, y:point.y, centerX:state.centerX, centerY:state.centerY};
      } else if (!state.pointers.size) {
        state.dragStart = null;
        element.classList.remove('is-dragging');
      }
    };
    element.addEventListener('pointerup', endPointer);
    element.addEventListener('pointercancel', endPointer);
    state.resizeObserver = new ResizeObserver(scheduleActivityMapRender);
    state.resizeObserver.observe(element);
    fitActivityMapToRoute();
  }

  function toggleActivityMapExpanded(forceClose = false) {
    const state = activityMapState;
    if (!state) return false;
    const expanded = forceClose ? false : !state.element.classList.contains('is-expanded');
    state.element.classList.toggle('is-expanded', expanded);
    document.body.classList.toggle('activity-map-open', expanded);
    const button = state.element.querySelector('.ad-map-expand');
    if (button) {
      button.setAttribute('aria-label', expanded ? 'Stäng stor karta' : 'Öppna stor karta');
      button.querySelector('span').textContent = expanded ? 'Stäng karta' : 'Öppna karta';
      button.querySelector('b').textContent = expanded ? '×' : '↗';
    }
    requestAnimationFrame(scheduleActivityMapRender);
    return expanded;
  }

  function destroyActivityMap() {
    if (activityMapState?.resizeObserver) activityMapState.resizeObserver.disconnect();
    if (activityMapState?.renderFrame) cancelAnimationFrame(activityMapState.renderFrame);
    activityMapState = null;
    document.body.classList.remove('activity-map-open');
  }

  function activityChartSvg(series, key, color, formatter, invert = false) {
    let data = (series || []).filter(point => point.elapsed != null && point[key] != null
      && Number.isFinite(Number(point[key])));
    if (key === 'pace') data = data.filter(point => point.pace >= 120 && point.pace <= 1200);
    if (data.length < 2) return '<div class="ad-chart-empty">Ingen mätserie registrerad.</div>';
    if (data.length > 500) {
      const step = Math.ceil(data.length / 500);
      const last = data.at(-1);
      data = data.filter((_, index) => index % step === 0);
      if (data.at(-1) !== last) data.push(last);
    }
    const W = 560, H = 190, left = 42, right = 12, top = 15, bottom = 25;
    const sorted = data.map(point => Number(point[key])).sort((a, b) => a - b);
    let lo = sorted[Math.floor((sorted.length - 1) * .03)];
    let hi = sorted[Math.ceil((sorted.length - 1) * .97)];
    if (lo === hi) { lo -= 1; hi += 1; }
    const margin = Math.max((hi - lo) * .08, .5);
    lo -= margin; hi += margin;
    const t0 = data[0].elapsed, t1 = data.at(-1).elapsed;
    const x = elapsed => left + ((elapsed - t0) / Math.max(1, t1 - t0)) * (W - left - right);
    const y = value => {
      const ratio = Math.max(0, Math.min(1, (value - lo) / (hi - lo)));
      return top + (invert ? ratio : 1 - ratio) * (H - top - bottom);
    };
    const points = data.map(point => [x(point.elapsed), y(Number(point[key]))]);
    const line = points.map((point, index) => `${index ? 'L' : 'M'}${point[0].toFixed(1)} ${point[1].toFixed(1)}`).join(' ');
    const area = `${line} L${points.at(-1)[0].toFixed(1)} ${H-bottom} L${points[0][0].toFixed(1)} ${H-bottom} Z`;
    const gradient = `ad-grad-${key}`;
    const topValue = invert ? lo : hi, bottomValue = invert ? hi : lo;
    return `<svg viewBox="0 0 ${W} ${H}" preserveAspectRatio="none" aria-hidden="true">
      <defs><linearGradient id="${gradient}" x1="0" y1="0" x2="0" y2="1"><stop offset="0" stop-color="${color}"/><stop offset="1" stop-color="${color}" stop-opacity="0"/></linearGradient></defs>
      ${[0,.5,1].map(f => `<line class="ad-chart-gridline" x1="${left}" x2="${W-right}" y1="${top+f*(H-top-bottom)}" y2="${top+f*(H-top-bottom)}"/>`).join('')}
      <path class="ad-chart-area" d="${area}" fill="url(#${gradient})"/>
      <path class="ad-chart-line" d="${line}" stroke="${color}"/>
      <text class="ad-chart-label" x="${left-6}" y="${top+4}" text-anchor="end">${escapeHtml(formatter(topValue))}</text>
      <text class="ad-chart-label" x="${left-6}" y="${H-bottom}" text-anchor="end">${escapeHtml(formatter(bottomValue))}</text>
      <text class="ad-chart-label" x="${left}" y="${H-6}">0:00</text>
      <text class="ad-chart-label" x="${W-right}" y="${H-6}" text-anchor="end">${escapeHtml(formatActivityDuration(t1-t0))}</text>
    </svg>`;
  }

  function activityChartCard(title, subtitle, series, key, color, formatter, invert = false) {
    return `<section class="ad-card"><div class="ad-card-head"><div><span class="ad-card-title">${escapeHtml(title)}</span><span class="ad-card-sub">${escapeHtml(subtitle)}</span></div></div>
      <div class="ad-chart">${activityChartSvg(series, key, color, formatter, invert)}</div></section>`;
  }

  function activityZones(title, unit, zones, colors) {
    const total = (zones || []).reduce((sum, zone) => sum + Number(zone.seconds || 0), 0);
    const rows = (zones || []).map((zone, index) => {
      const pct = total ? zone.seconds / total * 100 : 0;
      const boundary = zone.low != null ? ` · från ${Math.round(zone.low)} ${unit}` : '';
      return `<div class="ad-zone-row"><span class="ad-zone-name">Z${zone.zone}${escapeHtml(boundary)}</span>
        <div class="ad-zone-track"><div class="ad-zone-fill" style="width:${pct.toFixed(1)}%;background:${colors[index] || colors.at(-1)}"></div></div>
        <span class="ad-zone-time">${formatActivityDuration(zone.seconds)}</span></div>`;
    }).join('');
    return `<section class="ad-card"><div class="ad-card-head"><div><span class="ad-card-title">${escapeHtml(title)}</span><span class="ad-card-sub">Tid i zon · ${formatActivityDuration(total)}</span></div></div>
      <div class="ad-zone-list">${rows || '<div class="ad-chart-empty" style="height:90px">Inga zoner registrerade.</div>'}</div></section>`;
  }

  function activityLaps(laps) {
    if (!laps?.length) return '<div class="ad-chart-empty" style="height:110px">Inga varv registrerades.</div>';
    const rows = laps.map(lap => `<tr>
      <td>${escapeHtml(String(lap.index))}${lap.type ? ` <small>${escapeHtml(lap.type)}</small>` : ''}</td>
      <td>${lap.distance != null ? (lap.distance / 1000).toFixed(2) : '–'} km</td>
      <td>${formatActivityDuration(lap.duration)}</td><td>${formatActivityPace(lap.pace)} /km</td>
      <td>${lap.averageHR ?? '–'}</td><td>${lap.averagePower ?? '–'}</td>
      <td>${lap.elevationGain != null ? '+' + Math.round(lap.elevationGain) : '–'}</td>
    </tr>`).join('');
    return `<div class="ad-table-wrap"><table class="ad-laps"><thead><tr><th>Varv</th><th>Distans</th><th>Tid</th><th>Tempo</th><th>Puls</th><th>Watt</th><th>Höjd</th></tr></thead><tbody>${rows}</tbody></table></div>`;
  }

  function activitySecondaryMetrics(activity) {
    const o = activity.overview || {};
    const items = [
      ['Maxpuls', o.maxHR, ' bpm'], ['Maxeffekt', o.maxPower, ' W'],
      ['Kadens', o.averageCadence, ' spm'], ['Maxkadens', o.maxCadence, ' spm'],
      ['Kalorier', o.calories, ' kcal'], ['Träningsbelastning', o.trainingLoad, ''],
      ['VO₂ max', o.vo2max, ''], ['Steglängd', o.strideLength, ' cm'],
      ['Markkontakttid', o.groundContactTime, ' ms'], ['Vertikal rörelse', o.verticalOscillation, ' cm'],
      ['Vertikal kvot', o.verticalRatio, ' %'], ['Andning', o.averageRespiration, ' /min'],
      ['Body Battery', o.bodyBatteryImpact, ''], ['Steg', o.steps, ''],
      ['Vätskeförlust', o.waterEstimated, ' ml'],
    ];
    if (activity.weather) {
      const weather = [
        activity.weather.description,
        activity.weather.temperature != null ? `${activity.weather.temperature} °C` : '',
        activity.weather.windSpeed != null ? `${activity.weather.windSpeed} m/s ${activity.weather.windDirection || ''}` : '',
      ].filter(Boolean).join(' · ');
      if (weather) items.push(['Väder', weather, '']);
    }
    if (activity.gear?.length) {
      items.push(['Utrustning', activity.gear.map(item => item.name || item.model).filter(Boolean).join(', '), '']);
    }
    return items.filter(([, value]) => value !== null && value !== undefined && value !== '')
      .map(([label, value, unit]) => `<div class="ad-secondary-item"><span>${escapeHtml(label)}</span><strong>${escapeHtml(String(value))}${escapeHtml(unit)}</strong></div>`).join('');
  }

  function isStrengthActivity(activity) {
    return /strength|fitness|weight|gym/i.test(String(activity?.type || ''));
  }

  function activityStrengthWorkout(activity) {
    const logged = Array.isArray(activity.strengthExercises) ? activity.strengthExercises : [];
    const garminSets = Array.isArray(activity.exerciseSets) ? activity.exerciseSets : [];
    const activeSets = garminSets.filter(set => set.type === 'active');
    const restSets = garminSets.filter(set => set.type === 'rest');
    let rows = '';
    if (logged.length) {
      rows = logged.map(item => `<tr>
        <td><strong>${escapeHtml(item.exercise)}</strong>${item.note ? `<small>${escapeHtml(item.note)}</small>` : ''}</td>
        <td>${item.sets ?? '–'}</td><td>${escapeHtml(item.reps || '–')}</td>
        <td>${item.weight != null ? `${escapeHtml(String(item.weight))} kg` : '–'}</td>
      </tr>`).join('');
    } else if (activeSets.length) {
      rows = activeSets.map((set, index) => {
        const next = garminSets[garminSets.indexOf(set) + 1];
        const rawName = set.exercise || '';
        const name = rawName ? rawName.replaceAll('_', ' ').toLowerCase() : `Arbetsset ${index + 1}`;
        return `<tr><td><strong>${escapeHtml(name)}</strong></td>
          <td>1</td><td>${set.reps ?? '–'}</td><td>${set.weight != null ? `${set.weight} kg` : '–'}</td>
          <td>${formatActivityDuration(set.duration)}</td>
          <td>${next?.type === 'rest' ? formatActivityDuration(next.duration) : '–'}</td></tr>`;
      }).join('');
    }
    const table = logged.length
      ? `<table class="ad-strength-table"><thead><tr><th>Övning</th><th>Set</th><th>Reps</th><th>Vikt</th></tr></thead><tbody>${rows}</tbody></table>`
      : activeSets.length
        ? `<table class="ad-strength-table"><thead><tr><th>Övning</th><th>Set</th><th>Reps</th><th>Vikt</th><th>Arbete</th><th>Vila</th></tr></thead><tbody>${rows}</tbody></table>`
        : '<div class="ad-strength-empty">Inga övningar har loggats för det här passet.</div>';
    const sourceNote = logged.length
      ? `${logged.length} loggade övningar${activeSets.length ? ` · Garmin registrerade ${activeSets.length} arbetsset och ${restSets.length} viloperioder` : ''}`
      : activeSets.length && activeSets.every(set => !set.exercise && set.reps == null && set.weight == null)
        ? 'Klockan registrerade setens tider, men inga övningsnamn, reps eller vikter.'
        : 'Setdata från Garmin';
    return `<section class="ad-card ad-strength-card"><div class="ad-card-head"><div><span class="ad-card-title">Övningar & set</span><span class="ad-card-sub">${escapeHtml(sourceNote)}</span></div></div>
      <div class="ad-strength-table-wrap">${table}</div></section>`;
  }

  function renderStrengthActivityDetail(activity, heading) {
    const o = activity.overview || {};
    const logged = activity.strengthExercises || [];
    const activeSets = (activity.exerciseSets || []).filter(set => set.type === 'active');
    const exerciseCount = new Set(logged.map(item => item.exercise).filter(Boolean)).size;
    const loggedSetCount = logged.reduce((sum, item) => sum + Number(item.sets || 0), 0);
    const totalVolume = logged.reduce((sum, item) => {
      const reps = Number.parseFloat(String(item.reps || '').replace(',', '.'));
      return sum + (Number(item.sets || 0) * (Number.isFinite(reps) ? reps : 0) * Number(item.weight || 0));
    }, 0);
    const primary = [
      activityMetric('Tid', formatActivityDuration(o.duration || o.movingDuration)),
      activityMetric('Övningar', exerciseCount || '–'),
      activityMetric('Arbetsset', loggedSetCount || activeSets.length || '–'),
      activityMetric('Volym', totalVolume ? Math.round(totalVolume).toLocaleString('sv-SE') : '–', totalVolume ? 'kg' : ''),
      activityMetric('Snittpuls', o.averageHR ?? '–', 'bpm'),
      activityMetric('Kalorier', o.calories ?? '–', 'kcal'),
    ].join('');
    const summaryRows = [
      ['Förfluten tid', formatActivityDuration(o.elapsedDuration || o.duration)],
      ['Aktiv tid', formatActivityDuration(o.movingDuration)],
      ['Maxpuls', o.maxHR != null ? `${o.maxHR} bpm` : '–'],
      ['Belastning', o.trainingLoad ?? '–'],
      ['Body Battery', o.bodyBatteryImpact ?? '–'],
      ['Enhet', activity.device || '–'],
    ].map(([label, value]) => `<div class="ad-summary-row"><span>${escapeHtml(label)}</span><strong>${escapeHtml(String(value))}</strong></div>`).join('');
    const zoneColors = ['#7fd6c4','#2dd4bf','#c8f135','#ffb84d','#ff4d6d'];
    return `${heading}<div class="ad-metrics">${primary}</div>
      <div class="ad-grid ad-strength-grid">${activityStrengthWorkout(activity)}
        <section class="ad-card"><div class="ad-card-head"><div><span class="ad-card-title">Passöversikt</span><span class="ad-card-sub">Sammanfattning</span></div></div><div class="ad-summary-list">${summaryRows}</div></section></div>
      <div class="ad-chart-grid ad-strength-charts">
        ${activityChartCard('Puls', 'Slag per minut under styrkepasset', activity.series, 'heartRate', '#ff4d6d', value => String(Math.round(value)))}
        ${activityZones('Pulszoner', 'bpm', activity.heartRateZones, zoneColors)}
      </div>`;
  }

  function renderActivityDetail(activity) {
    const o = activity.overview || {};
    const type = formatActivityType(activity.type);
    const sourceName = activity.source === 'strava' ? 'Strava' : 'Garmin';
    const meta = [formatActivityDate(activity.date), activity.location, activity.device, sourceName].filter(Boolean).join(' · ');
    const sourceUrl = activity.source === 'strava'
      && /^https:\/\/www\.strava\.com\/activities\/\d+$/.test(activity.sourceUrl || '')
      ? activity.sourceUrl : '';
    const primary = [
      activityMetric('Distans', o.distance != null ? (o.distance / 1000).toFixed(2) : '–', 'km'),
      activityMetric('Tid', formatActivityDuration(o.movingDuration)),
      activityMetric('Snittempo', formatActivityPace(o.pace), '/km'),
      activityMetric('Snittpuls', o.averageHR ?? '–', 'bpm'),
      activityMetric('Höjdmeter', o.elevationGain != null ? Math.round(o.elevationGain) : '–', 'm'),
      activityMetric('Snitteffekt', o.averagePower ?? '–', 'W'),
    ].join('');
    const effect = [
      o.aerobicEffect != null ? `<div class="ad-effect"><span>Aerob effekt</span><strong>${o.aerobicEffect}</strong></div>` : '',
      o.anaerobicEffect != null ? `<div class="ad-effect"><span>Anaerob effekt</span><strong>${o.anaerobicEffect}</strong></div>` : '',
    ].join('');
    const summaryRows = [
      ['Förfluten tid', formatActivityDuration(o.elapsedDuration)],
      ['Rörelsetid', formatActivityDuration(o.movingDuration)],
      ['Maxpuls', o.maxHR != null ? `${o.maxHR} bpm` : '–'],
      ['Kalorier', o.calories != null ? `${o.calories} kcal` : '–'],
      ['Höjd ner', o.elevationLoss != null ? `${Math.round(o.elevationLoss)} m` : '–'],
      ['Belastning', o.trainingLoad ?? '–'],
      ['Kadens', o.averageCadence != null ? `${o.averageCadence} spm` : '–'],
      ['VO₂ max', o.vo2max ?? '–'],
    ].map(([label, value]) => `<div class="ad-summary-row"><span>${escapeHtml(label)}</span><strong>${escapeHtml(String(value))}</strong></div>`).join('');
    const zoneColors = ['#7fd6c4','#2dd4bf','#c8f135','#ffb84d','#ff4d6d'];
    const secondary = activitySecondaryMetrics(activity);
    const heading = `<div class="ad-head"><div><div class="ad-kicker">${escapeHtml(type)}</div>
        <h2 id="activity-detail-title">${escapeHtml(activity.name || 'Aktivitet')}</h2><div class="ad-meta">${escapeHtml(meta)}${sourceUrl ? ` · <a href="${escapeHtml(sourceUrl)}" target="_blank" rel="noopener noreferrer">Visa på Strava</a>` : ''}</div></div>
        ${effect ? `<div class="ad-effort">${effect}</div>` : ''}</div>`;
    if (isStrengthActivity(activity)) return renderStrengthActivityDetail(activity, heading);
    return `${heading}
      <div class="ad-metrics">${primary}</div>
      <div class="ad-grid"><section class="ad-card"><div class="ad-card-head"><div><span class="ad-card-title">Rutt</span><span class="ad-card-sub">GPS-spår från ${sourceName}</span></div></div>
          <div class="ad-route">${activityRouteMap(activity.route)}</div></section>
        <section class="ad-card"><div class="ad-card-head"><div><span class="ad-card-title">Passöversikt</span><span class="ad-card-sub">Sammanfattning</span></div></div><div class="ad-summary-list">${summaryRows}</div></section></div>
      <div class="ad-chart-grid">
        ${activityChartCard('Tempo', 'Minuter per kilometer', activity.series, 'pace', '#c8f135', value => formatActivityPace(value), true)}
        ${activityChartCard('Puls', 'Slag per minut', activity.series, 'heartRate', '#ff4d6d', value => String(Math.round(value)))}
        ${activityChartCard('Höjdprofil', 'Meter över havet', activity.series, 'elevation', '#7fd6c4', value => String(Math.round(value)))}
        ${activityChartCard('Löpeffekt', 'Watt', activity.series, 'power', '#ffb84d', value => String(Math.round(value)))}
      </div>
      <div class="ad-zones">
        ${activityZones('Pulszoner', 'bpm', activity.heartRateZones, zoneColors)}
        ${activityZones('Effektzoner', 'W', activity.powerZones, zoneColors)}
      </div>
      ${secondary ? `<div class="ad-secondary">${secondary}</div>` : ''}
      <section class="ad-card"><div class="ad-card-head"><div><span class="ad-card-title">Varv</span><span class="ad-card-sub">Tempo, puls, effekt och höjd per varv</span></div></div>${activityLaps(activity.laps)}</section>`;
  }

  async function openActivityDetails(activityId, source = 'garmin') {
    if (!Number.isSafeInteger(activityId) || activityId <= 0) return;
    const overlay = document.getElementById('activity-overlay');
    const content = document.getElementById('activity-detail-content');
    if (!overlay || !content) return;
    activityModalPreviousFocus = document.activeElement;
    const requestNumber = ++activityDetailRequest;
    overlay.classList.add('is-open');
    overlay.setAttribute('aria-hidden', 'false');
    document.body.classList.add('activity-modal-open');
    const normalizedSource = source === 'strava' ? 'strava' : 'garmin';
    content.innerHTML = '<div class="activity-loading"><span></span>Laddar passdetaljer…</div>';
    overlay.querySelector('.activity-dialog').scrollTop = 0;
    setTimeout(() => overlay.querySelector('.activity-close')?.focus(), 0);
    try {
      const endpoint = normalizedSource === 'strava'
        ? `/api/strava/activities/${activityId}` : `/api/activities/${activityId}`;
      const response = await fetch(endpoint);
      const payload = await response.json().catch(() => ({}));
      if (!response.ok) throw new Error(payload.error || 'Passet kunde inte laddas.');
      if (requestNumber !== activityDetailRequest) return;
      content.innerHTML = renderActivityDetail(payload.activity || {});
      initializeActivityMap(payload.activity?.route);
      overlay.querySelector('.activity-dialog').scrollTop = 0;
    } catch (error) {
      if (requestNumber !== activityDetailRequest) return;
      content.innerHTML = `<div class="ad-error"><strong>Kunde inte öppna passet</strong><p>${escapeHtml(error.message)}</p></div>`;
    }
  }

  function closeActivityDetails() {
    const overlay = document.getElementById('activity-overlay');
    if (!overlay?.classList.contains('is-open')) return;
    activityDetailRequest += 1;
    destroyActivityMap();
    overlay.classList.remove('is-open');
    overlay.setAttribute('aria-hidden', 'true');
    document.body.classList.remove('activity-modal-open');
    if (activityModalPreviousFocus?.focus) activityModalPreviousFocus.focus();
    activityModalPreviousFocus = null;
  }

  const linkedActivityId = Number(new URLSearchParams(window.location.search).get('activity'));
  if (Number.isSafeInteger(linkedActivityId) && linkedActivityId > 0) {
    setTimeout(() => openActivityDetails(linkedActivityId), 0);
  }

  // ─── MÅLTEMPON ──────────────────────────────────────────────
  // Förslagen ändrar aldrig planen av sig själva — de väntar här tills
  // de godkänts. Se pace_progression.py för hur banden räknas fram.
  const PACE_DAY_NAMES = ['mån', 'tis', 'ons', 'tors', 'fre', 'lör', 'sön'];
  const PACE_KIND_LABELS = {
    interval: 'Intervall', threshold: 'Tröskel', race: 'Loppfart',
    long: 'Långpass', easy: 'Lugnt', run: 'Löpning',
  };

  async function loadPaceProposals() {
    const panel = document.getElementById('pace-panel');
    if (!panel) return;
    try {
      const res = await fetch('/api/plan/pace-proposals');
      if (!res.ok) { panel.style.display = 'none'; return; }
      renderPacePanel(await res.json());
    } catch (_) {
      panel.style.display = 'none';
    }
  }

  function renderPacePanel(data) {
    const panel = document.getElementById('pace-panel');
    const anchor = data.anchor || {};
    if (!anchor.ltPaceSec) { panel.style.display = 'none'; return; }
    panel.style.display = '';

    document.getElementById('pace-anchor-sub').textContent =
      `Tröskel ${anchor.ltPace} · källa: ${anchor.source} · konfidens: ${anchor.confidence}`;

    document.getElementById('pace-bands').innerHTML = Object.entries(data.bands || {})
      .map(([kind, band]) => `<span class="pace-band">`
        + `<b>${escapeHtml(PACE_KIND_LABELS[kind] || kind)}</b> ${escapeHtml(band.text)}</span>`)
      .join('');

    const goalEl = document.getElementById('pace-goal');
    const goal = data.goalFeasibility;
    if (goal && goal.verdict === 'out_of_reach') {
      goalEl.style.display = '';
      goalEl.className = 'pace-goal pace-goal-warn';
      goalEl.innerHTML = `Ditt mål kräver <b>${escapeHtml(goal.goalPace)}</b>, men din uppmätta`
        + ` tröskel räcker i dag till <b>${escapeHtml(goal.currentCapablePace)}</b> på loppdistansen`
        + ` — ${goal.gapSec} s/km ifrån.`;
    } else if (goal) {
      goalEl.style.display = '';
      goalEl.className = 'pace-goal';
      goalEl.innerHTML = `Målet <b>${escapeHtml(goal.goalPace)}</b> ligger inom räckhåll`
        + ` (nuvarande kapacitet ${escapeHtml(goal.currentCapablePace)}).`;
    } else {
      goalEl.style.display = 'none';
    }

    const list = document.getElementById('pace-proposals');
    const proposals = data.proposals || [];
    if (!proposals.length) {
      list.innerHTML = '<div class="pace-empty">Inga väntande förslag. '
        + 'Räkna om när du vill se om planens tempon stämmer med din form.</div>';
      return;
    }

    list.innerHTML = proposals.map(p => {
      const day = `V${p.week} ${PACE_DAY_NAMES[p.dow] || ''}`;
      const clamped = p.validation !== 'accepted'
        ? `<span class="pace-clamped" data-freetip="${escapeHtml(p.reason || '')}">justerat av motorn</span>` : '';
      return `<div class="pace-row">
        <div class="pace-row-main">
          <div class="pace-row-title">${escapeHtml(day)} · ${escapeHtml(p.title || '')}
            <span class="pace-kind">${escapeHtml(PACE_KIND_LABELS[p.kind] || p.kind || '')}</span>${clamped}</div>
          <div class="pace-row-change">
            <span class="pace-old">${escapeHtml(p.oldPace || 'inget mål')}</span>
            <span class="pace-arrow">→</span>
            <span class="pace-new">${escapeHtml(p.newPace || '')}</span>
          </div>
          ${p.rationale ? `<div class="pace-row-why">${escapeHtml(p.rationale)}</div>` : ''}
        </div>
        <div class="pace-row-actions">
          <button class="pace-btn pace-btn-ok" type="button" data-action="pace-decide" data-decision="approve" data-id="${p.id}">Godkänn</button>
          <button class="pace-btn" type="button" data-action="pace-decide" data-decision="reject" data-id="${p.id}">Avfärda</button>
        </div>
      </div>`;
    }).join('') + `<div class="pace-bulk">
      <button class="pace-btn pace-btn-ok" type="button" data-action="pace-decide" data-decision="approve">Godkänn alla (${proposals.length})</button>
      <button class="pace-btn" type="button" data-action="pace-decide" data-decision="reject">Avfärda alla</button>
    </div>`;
  }

  async function generatePaceProposals() {
    const button = document.getElementById('pace-generate-btn');
    const list = document.getElementById('pace-proposals');
    if (button) { button.disabled = true; button.textContent = 'Räknar…'; }
    try {
      const res = await fetch('/api/plan/pace-proposals/generate', {method: 'POST'});
      const data = await res.json().catch(() => ({}));
      if (!res.ok || data.error) {
        list.innerHTML = `<div class="pace-empty">${escapeHtml(data.message || 'Kunde inte räkna om just nu.')}</div>`;
      } else if (!data.proposals) {
        list.innerHTML = '<div class="pace-empty">Planens tempon stämmer redan med din form.</div>';
      } else {
        await loadPaceProposals();
      }
    } catch (_) {
      list.innerHTML = '<div class="pace-empty">Servern kunde inte nås.</div>';
    } finally {
      if (button) { button.disabled = false; button.textContent = 'Räkna om måltempon'; }
    }
  }

  async function decidePaceProposals(decision, id) {
    const body = {decision};
    if (id) body.ids = [Number(id)];
    try {
      const res = await fetch('/api/plan/pace-proposals/decide', {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify(body),
      });
      if (!res.ok) return;
      await loadPaceProposals();
      if (decision === 'approve') await loadPlan();  // kalendern visar den nya texten
    } catch (_) {
      // Nästa laddning rättar vyn.
    }
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
      calendarConnected = !!d.hasToken;
      renderSettingsPage();
      if (d.hasToken) await syncGcal();
    } catch(e) {}
  }

  async function syncGcal() {
    const syncIds = ['gcal-sync-btn', 'mobile-gcal-sync-btn', 'settings-calendar-primary'];
    setButtons(syncIds, 'Synkar…', 'var(--blue)', true);
    try {
      const r = await fetch('/api/calendar');
      const d = await r.json();
      if (!d.ok) throw new Error(d.error || 'Fel');
      calendarConnected = true;
      gcalEvents = d.events || [];
      setButtons(syncIds, 'Synkad', 'var(--green)', true);
      setTimeout(() => setButtons(syncIds, 'Synka kalender', '', false), 2500);
      buildCalendar();
      renderTodaySession();
      renderSettingsPage();
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
