// node --test tests/test_potential_ui.js
const {test} = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const source = fs.readFileSync('public/app.js', 'utf8');
const start = source.indexOf('  let potentialData = null;');
const end = source.indexOf("  document.getElementById('potential-card')?.addEventListener", start);

function context(fetch) {
  const elements = new Map();
  const get = id => {
    if (!elements.has(id)) elements.set(id, {value: '', hidden: false, textContent: '', innerHTML: ''});
    return elements.get(id);
  };
  get('potential-weeks').value = '12';
  get('potential-km').value = '40';
  get('potential-method').value = 'history';
  const ctx = vm.createContext({URLSearchParams, fetch, document: {getElementById: get}});
  vm.runInContext(source.slice(start, end), ctx);
  return {ctx, get};
}

test('race parser accepts long race times and rejects malformed values', () => {
  const {ctx} = context();
  assert.equal(ctx.potentialRaceSeconds('22:30'), 1350);
  assert.equal(ctx.potentialRaceSeconds('210:00'), 12600);
  for (const value of ['', '22:60', '1:30:00', '-10:00', 'NaN', '0:00', '721:00', '<script>']) {
    assert.equal(ctx.potentialRaceSeconds(value), null, value);
  }
});

test('formatting rounds across minute and hour boundaries', () => {
  const {ctx} = context();
  assert.equal(ctx.potentialTime(1196), '20:00');
  assert.equal(ctx.potentialTime(3596), '1:00:00');
});

test('slower old response cannot replace a newer scenario', async () => {
  const pending = [], rendered = [];
  const {ctx, get} = context(() => new Promise(resolve => pending.push(resolve)));
  ctx.renderPotential = data => rendered.push(data);
  ctx.potentialLabels = () => {};
  vm.runInContext('potentialData = {};', ctx);
  const first = ctx.loadPotential();
  get('potential-weeks').value = '26';
  const second = ctx.loadPotential();
  pending[1]({ok: true, json: async () => ({weeks: 26})});
  await second;
  pending[0]({ok: true, json: async () => ({weeks: 12})});
  await first;
  assert.deepEqual(rendered, [{weeks: 26}]);
});

test('fetch errors clear stale predictions and offer retry', async () => {
  const {ctx, get} = context(async () => { throw new Error('offline'); });
  get('potential-races').innerHTML = 'old race predictions';
  await ctx.loadPotential();
  assert.equal(get('potential-results').hidden, true);
  assert.equal(get('potential-races').innerHTML, '');
  assert.match(get('potential-status').textContent, /Uppdatera beräkningen/);
});

test('a malformed reference time is omitted, not silently used', async () => {
  let url;
  const {ctx, get} = context(async path => {
    url = path;
    return {ok: true, json: async () => ({weeklyKm: 40, baselineKm: 40})};
  });
  ctx.renderPotential = () => {};
  ctx.potentialLabels = () => {};
  get('potential-race-time').value = '22:90';
  await ctx.loadPotential();
  assert.equal(new URLSearchParams(url.split('?')[1]).has('race_seconds'), false);
  assert.match(get('potential-race-error').textContent, /minuter:sekunder/);
});
