/* DOM integration test against the isolated mock server (never production).
   NODE_PATH=/tmp/trainyze-profile-dom/node_modules node tests/test_runner_profile_ui.js
   Requires tools/dev_shell.py in mock mode on port 3102. */
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const {JSDOM} = require('jsdom');
const root = path.resolve(__dirname, '..');
const base = 'http://127.0.0.1:3102';
const html = fs.readFileSync(path.join(root, 'public/app/index.html'), 'utf8');
const script = fs.readFileSync(path.join(root, 'public/app/runner-profile.js'), 'utf8');
const calls = [];
let failNextSave = false;
async function until(check) {
  for (let i = 0; i < 100; i++) {
    if (check()) return;
    await new Promise(resolve => setTimeout(resolve, 10));
  }
  throw new Error('Timed out waiting for profile UI');
}
function page() {
  const dom = new JSDOM(html, {url: base + '/app/', runScripts: 'outside-only'});
  const w = dom.window;
  w.fetch = async (url, options) => {
    calls.push([url, options?.method || 'GET']);
    if (url === '/api/runner-profile' && options?.method === 'PUT' && failNextSave) {
      failNextSave = false;
      return new Response(JSON.stringify({error: 'Tillfälligt avbrott'}), {status: 503});
    }
    return fetch(base + url, options);
  };
  w.eval(script);
  w.document.getElementById('page-settings').classList.add('active');
  return dom;
}
function edit(w, name, value) {
  const el = w.document.querySelector(`[name="${name}"]`);
  el.value = value;
  el.dispatchEvent(new w.Event('input', {bubbles: true}));
}
function submit(w, id) {
  w.document.getElementById(id).dispatchEvent(new w.Event('submit', {bubbles: true, cancelable: true}));
}
(async () => {
  const info = await (await fetch(base + '/__dev/info')).json();
  assert.equal(info.mode, 'mock', 'UI test must only modify isolated mock data');
  const reset = await fetch(base + '/api/runner-profile', {method: 'PUT', headers: {'Content-Type': 'application/json'}, body: '{}'});
  assert.equal(reset.status, 200);
  let dom = page(), w = dom.window, doc = w.document;
  await until(() => !doc.getElementById('rp-fields').disabled && !doc.getElementById('rp-goal-fields').disabled);
  const ids = Array.from(doc.querySelectorAll('[id]'), el => el.id);
  assert.equal(new Set(ids).size, ids.length, 'No duplicate HTML IDs');
  assert.equal(doc.getElementById('pace-generate-btn'), null, 'No AI generation action on profile');
  edit(w, 'displayName', 'Testlöpare <b>text</b>');
  edit(w, 'weeklyKm', '32,5');
  for (const day of [1, 3, 6]) {
    const box = doc.querySelector(`[name="preferredDays"][value="${day}"]`);
    box.checked = true;
    box.dispatchEvent(new w.Event('input', {bubbles: true}));
  }
  edit(w, 'longRunDay', '6');
  edit(w, 'paceFast_easy', '5:10');
  edit(w, 'paceSlow_easy', '6:00');
  submit(w, 'runner-profile-form');
  await until(() => doc.getElementById('rp-save-status').textContent === 'Löparprofilen är sparad.');
  assert.equal(doc.getElementById('rp-summary').querySelector('b'), null, 'User content is text, not markup');
  assert.equal(doc.getElementById('rp-days-summary').textContent, '3 / vecka');
  // A new DOM represents a reload; data must come from the API, not browser state.
  dom.window.close();
  dom = page(); w = dom.window; doc = w.document;
  await until(() => !doc.getElementById('rp-fields').disabled);
  assert.equal(doc.querySelector('[name="displayName"]').value, 'Testlöpare <b>text</b>');
  assert.equal(doc.querySelector('[name="longRunDay"]').value, '6');
  assert.equal(doc.querySelector('[name="paceFast_easy"]').value, '5:10');
  edit(w, 'about', 'Osparat utkast');
  doc.getElementById('page-settings').classList.remove('active');
  doc.getElementById('page-settings').classList.add('active');
  await new Promise(resolve => setTimeout(resolve, 20));
  assert.equal(doc.querySelector('[name="about"]').value, 'Osparat utkast');
  failNextSave = true;
  submit(w, 'runner-profile-form');
  await until(() => doc.getElementById('rp-save-status').dataset.error === 'true');
  assert.equal(doc.querySelector('[name="about"]').value, 'Osparat utkast');
  assert.equal(doc.getElementById('rp-save').disabled, false);
  // Invalid input must reach a visible validation error, never a fake success.
  edit(w, 'paceFast_easy', '6:30');
  submit(w, 'runner-profile-form');
  await until(() => doc.getElementById('rp-save-status').dataset.error === 'true');
  assert.match(doc.getElementById('rp-save-status').textContent, /snabbaste/);
  edit(w, 'paceFast_easy', '5:10');
  submit(w, 'runner-profile-form');
  await until(() => doc.getElementById('rp-save-status').textContent === 'Löparprofilen är sparad.');
  await until(() => !doc.getElementById('rp-goal-fields').disabled);
  edit(w, 'goalTitle', 'Springa tre gånger per vecka');
  edit(w, 'goalDeadline', '2027-04-01');
  submit(w, 'rp-goal-form');
  await until(() => doc.getElementById('rp-goal-status').textContent === 'Ditt mål är sparat.');
  assert.equal((await (await fetch(base + '/api/goals')).json()).goal.goal_title, 'Springa tre gånger per vecka');
  assert(calls.every(([url]) => ['/api/runner-profile', '/api/goals'].includes(url)), 'Profile performs no AI or planning calls');
  dom.window.close();
  console.log('PASS: profile save/reload, validation, retry, safe text, draft retention, shared goal, no AI calls');
})().catch(error => { console.error(error); process.exitCode = 1; });
