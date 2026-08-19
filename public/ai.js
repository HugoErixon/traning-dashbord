let csrfToken = '';
let pollTimer = null;
// Kon ritas om var tredje sekund. Utan det har skulle ett oppnat uppdrag fallas
// ihop vid varje omritning, precis medan man foljer korningen som pagar.
const openJobs = new Set();
const jobBodies = new Map();
let renderedSignature = null;

const byId = id => document.getElementById(id);
const escapeHtml = value => String(value ?? '').replace(/[&<>'"]/g, char => ({
  '&':'&amp;','<':'&lt;','>':'&gt;',"'":'&#39;','"':'&quot;'
})[char]);

function base64urlToBytes(value) {
  const padding = '='.repeat((4 - value.length % 4) % 4);
  const binary = atob(value.replace(/-/g, '+').replace(/_/g, '/') + padding);
  return Uint8Array.from(binary, char => char.charCodeAt(0));
}

function bytesToBase64url(value) {
  const bytes = new Uint8Array(value);
  let binary = '';
  bytes.forEach(byte => { binary += String.fromCharCode(byte); });
  return btoa(binary).replace(/\+/g, '-').replace(/\//g, '_').replace(/=+$/, '');
}

function creationOptions(options) {
  return {
    ...options,
    challenge: base64urlToBytes(options.challenge),
    user: {...options.user, id: base64urlToBytes(options.user.id)},
    excludeCredentials: (options.excludeCredentials || []).map(item => ({
      ...item, id: base64urlToBytes(item.id)
    })),
  };
}

function requestOptions(options) {
  return {
    ...options,
    challenge: base64urlToBytes(options.challenge),
    allowCredentials: (options.allowCredentials || []).map(item => ({
      ...item, id: base64urlToBytes(item.id)
    })),
  };
}

function registrationCredential(credential) {
  return {
    id: credential.id,
    rawId: bytesToBase64url(credential.rawId),
    type: credential.type,
    authenticatorAttachment: credential.authenticatorAttachment,
    clientExtensionResults: credential.getClientExtensionResults(),
    response: {
      attestationObject: bytesToBase64url(credential.response.attestationObject),
      clientDataJSON: bytesToBase64url(credential.response.clientDataJSON),
      transports: credential.response.getTransports ? credential.response.getTransports() : [],
    },
  };
}

function authenticationCredential(credential) {
  return {
    id: credential.id,
    rawId: bytesToBase64url(credential.rawId),
    type: credential.type,
    authenticatorAttachment: credential.authenticatorAttachment,
    clientExtensionResults: credential.getClientExtensionResults(),
    response: {
      authenticatorData: bytesToBase64url(credential.response.authenticatorData),
      clientDataJSON: bytesToBase64url(credential.response.clientDataJSON),
      signature: bytesToBase64url(credential.response.signature),
      userHandle: credential.response.userHandle ? bytesToBase64url(credential.response.userHandle) : null,
    },
  };
}

async function api(path, options = {}) {
  const request = {...options, credentials:'same-origin'};
  const method = String(request.method || 'GET').toUpperCase();
  const headers = new Headers(request.headers || {});
  if (['POST','PUT','PATCH','DELETE'].includes(method)) headers.set('X-CSRF-Token', csrfToken);
  if (request.body && !headers.has('Content-Type')) headers.set('Content-Type', 'application/json');
  request.headers = headers;
  const response = await fetch(path, request);
  const data = await response.json().catch(() => ({}));
  if (!response.ok) {
    const error = new Error(data.error || 'Något gick fel.');
    error.code = data.code;
    error.status = response.status;
    throw error;
  }
  return data;
}

function notice(message, error = false) {
  const element = byId('notice');
  element.textContent = message;
  element.classList.toggle('error', error);
  element.hidden = !message;
}

function setButtonBusy(button, busy, label) {
  if (!button.dataset.label) button.dataset.label = button.textContent;
  button.disabled = busy;
  button.textContent = busy ? label : button.dataset.label;
}

function renderStatus(status) {
  const security = byId('security-state');
  security.className = `security-state${status.unlocked ? ' unlocked' : ''}`;
  security.querySelector('span').textContent = status.unlocked ? 'Face ID verifierat' : 'AI Control låst';
  byId('setup-card').hidden = !status.enabled || status.configured;
  byId('unlock-card').hidden = !status.enabled || !status.configured || status.unlocked;
  byId('workspace').hidden = !status.unlocked;
  byId('lock-button').hidden = !status.unlocked;
  byId('agent-pill').textContent = status.agentConfigured ? 'G3 REDO' : 'G3 EJ KONFIGURERAD';
  if (!status.enabled) notice('AI Control är ännu inte aktiverat i serverkonfigurationen.');
  else if (!status.webauthnAvailable) notice('Servern saknar WebAuthn-paketet.', true);
  else if (!window.PublicKeyCredential) notice('Den här webbläsaren stöder inte passkeys.', true);
  else notice('');
  if (status.unlocked) startPolling(); else stopPolling();
}

async function loadStatus() {
  try {
    const session = await api('/api/session');
    if (!session.authenticated) {
      location.href = '/';
      return;
    }
    if (!session.isAdmin || session.userId !== 1) {
      throw new Error('Endast Trainyzes ägare har åtkomst till den här sidan.');
    }
    csrfToken = session.csrfToken;
    renderStatus(await api('/api/ai/status'));
  } catch (error) {
    byId('security-state').classList.add('error');
    notice(error.message, true);
  }
}

async function registerPasskey() {
  const button = byId('register-button');
  setButtonBusy(button, true, 'Väntar på Face ID…');
  notice('');
  try {
    const options = await api('/api/ai/passkeys/register/options', {
      method:'POST', body:JSON.stringify({bootstrapToken:byId('bootstrap-token').value})
    });
    const credential = await navigator.credentials.create({publicKey:creationOptions(options)});
    await api('/api/ai/passkeys/register/verify', {
      method:'POST', body:JSON.stringify({credential:registrationCredential(credential), label:'Face ID'})
    });
    byId('bootstrap-token').value = '';
    await loadStatus();
    await loadJobs();
  } catch (error) {
    notice(error.name === 'NotAllowedError' ? 'Face ID avbröts eller tog för lång tid.' : error.message, true);
  } finally {
    setButtonBusy(button, false);
  }
}

async function unlock() {
  const button = byId('unlock-button');
  setButtonBusy(button, true, 'Väntar på Face ID…');
  notice('');
  try {
    const options = await api('/api/ai/passkeys/auth/options', {method:'POST'});
    const credential = await navigator.credentials.get({publicKey:requestOptions(options)});
    await api('/api/ai/passkeys/auth/verify', {
      method:'POST', body:JSON.stringify({credential:authenticationCredential(credential)})
    });
    await loadStatus();
    await loadJobs();
  } catch (error) {
    notice(error.name === 'NotAllowedError' ? 'Face ID avbröts eller tog för lång tid.' : error.message, true);
  } finally {
    setButtonBusy(button, false);
  }
}

async function lock() {
  await api('/api/ai/lock', {method:'POST'});
  await loadStatus();
}

function statusLabel(status) {
  return ({pending:'Väntar',running:'Kör på G3',completed:'Klar',failed:'Misslyckades',cancelled:'Avbruten'})[status] || status;
}

function formatTime(timestamp) {
  return timestamp ? new Date(timestamp * 1000).toLocaleString('sv-SE', {dateStyle:'short',timeStyle:'short'}) : '';
}

function renderJobs(jobs) {
  const container = byId('jobs');
  const signature = jobs.map(job => `${job.id}:${job.status}`).join('|');
  if (signature === renderedSignature) return;
  renderedSignature = signature;
  const known = new Set(jobs.map(job => job.id));
  openJobs.forEach(id => { if (!known.has(id)) openJobs.delete(id); });
  jobBodies.forEach((_, id) => { if (!known.has(id)) jobBodies.delete(id); });
  if (!jobs.length) {
    container.innerHTML = '<p class="empty">Inga uppdrag ännu.</p>';
    return;
  }
  container.innerHTML = jobs.map(job => `
    <details class="job ${escapeHtml(job.status)}" data-job-id="${escapeHtml(job.id)}">
      <summary>
        <i class="job-dot"></i>
        <div><div class="job-title">${escapeHtml(job.prompt)}</div><div class="job-meta">/${escapeHtml(job.provider || 'codex')} · ${escapeHtml(formatTime(job.created_at))} · ${escapeHtml(job.id.slice(0,8))}</div></div>
        <span class="job-status">${escapeHtml(statusLabel(job.status))}</span>
      </summary>
      <div class="job-body"><p class="empty">Öppna för att ladda körningen…</p></div>
    </details>`).join('');
  container.querySelectorAll('details').forEach(element => {
    const jobId = element.dataset.jobId;
    element.addEventListener('toggle', () => {
      if (element.open) { openJobs.add(jobId); loadJobDetail(element); }
      else openJobs.delete(jobId);
    });
    if (!openJobs.has(jobId)) return;
    // Aterstall det tidigare innehallet direkt, sa omritningen inte blinkar
    // tillbaka till "Oppna for att ladda korningen" medan hamtningen pagar.
    const cached = jobBodies.get(jobId);
    if (cached) element.querySelector('.job-body').innerHTML = cached;
    element.open = true;
  });
}

async function loadJobs() {
  try {
    const data = await api('/api/ai/jobs');
    const jobs = data.jobs || [];
    renderJobs(jobs);
    // Ett uppdrag som fortfarande kor far nya handelser aven nar sjalva
    // listraden ser oforandrad ut, sa dess detaljvy maste hamtas om.
    await Promise.all(jobs
      .filter(job => openJobs.has(job.id) && (job.status === 'pending' || job.status === 'running'))
      .map(job => {
        const element = byId('jobs').querySelector(`details[data-job-id="${CSS.escape(job.id)}"]`);
        return element ? loadJobDetail(element) : null;
      }));
  } catch (error) {
    if (error.code === 'passkey_required') await loadStatus();
    else notice(error.message, true);
  }
}

async function loadJobDetail(element) {
  try {
    const data = await api(`/api/ai/jobs/${element.dataset.jobId}`);
    const job = data.job;
    const events = (job.events || []).map(event => `[${statusLabel(event.kind)}] ${event.message}`).join('\n\n');
    const body = `
      ${job.result ? `<h3>RESULTAT</h3><div class="job-result">${escapeHtml(job.result)}</div>` : ''}
      ${job.error ? `<h3>FEL</h3><div class="job-result">${escapeHtml(job.error)}</div>` : ''}
      <h3>HÄNDELSER</h3><div class="job-events">${escapeHtml(events || 'Inga händelser ännu.')}</div>`;
    jobBodies.set(element.dataset.jobId, body);
    element.querySelector('.job-body').innerHTML = body;
  } catch (error) {
    element.querySelector('.job-body').textContent = error.message;
  }
}

async function submitJob() {
  const prompt = byId('prompt').value.trim();
  if (!prompt) { notice('Skriv vad G3 ska göra först.', true); return; }
  const button = byId('submit-job');
  setButtonBusy(button, true, 'Skickar…');
  try {
    await api('/api/ai/jobs', {method:'POST', body:JSON.stringify({prompt})});
    const selected = prompt.match(/^\/(codex|claude)(?:\s|$)/i)?.[1]?.toLowerCase() || 'codex';
    selectProvider(selected);
    byId('prompt').value = `/${selected} `;
    notice('Uppdraget ligger i kön på G3.');
    await loadJobs();
  } catch (error) {
    if (error.code === 'passkey_required') await loadStatus();
    notice(error.message, true);
  } finally {
    setButtonBusy(button, false);
  }
}

function selectProvider(provider) {
  const prompt = byId('prompt');
  const value = prompt.value.replace(/^\/(codex|claude)(?:\s+|$)/i, '').trimStart();
  prompt.value = `/${provider} ${value}`;
  document.querySelectorAll('.provider-button').forEach(button => {
    button.classList.toggle('active', button.dataset.provider === provider);
  });
  prompt.focus();
}

function startPolling() {
  if (pollTimer) return;
  loadJobs();
  pollTimer = setInterval(loadJobs, 3000);
}

function stopPolling() {
  clearInterval(pollTimer);
  pollTimer = null;
}

byId('register-button').addEventListener('click', registerPasskey);
byId('unlock-button').addEventListener('click', unlock);
byId('lock-button').addEventListener('click', lock);
byId('submit-job').addEventListener('click', submitJob);
byId('refresh-jobs').addEventListener('click', loadJobs);
document.querySelectorAll('.provider-button').forEach(button => {
  button.addEventListener('click', () => selectProvider(button.dataset.provider));
});
loadStatus();
