// Run with node --test tests/test_coach_ui.js.
const {test} = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const source = fs.readFileSync('public/app.js', 'utf8');
const start = source.indexOf('  async function loadPlan() {');
const end = source.indexOf('\n  loadPlan();', start);

for (const sessions of [[], [{id: 2}]]) {
  test(`refresh plan with ${sessions.length} sessions replaces old sessions`, async () => {
    const rendered = [];
    const ctx = vm.createContext({
      PLAN_SESSIONS: [{id: 99}], console,
      fetch: async () => ({ok: true, json: async () => ({sessions})}),
      normalizePlanSession: s => s,
      buildCalendar: () => rendered.push('calendar'),
      renderTodaySession: () => rendered.push('today'),
      safeRenderTrainingCockpit: () => rendered.push('cockpit'),
    });
    vm.runInContext(source.slice(start, end), ctx);
    await ctx.loadPlan();
    assert.deepEqual(ctx.PLAN_SESSIONS, sessions);
    assert.deepEqual(rendered, ['calendar', 'today', 'cockpit']);
  });
}

test('failed plan fetch preserves current view', async () => {
  const current = [{id: 99}];
  const ctx = vm.createContext({PLAN_SESSIONS: current, console,
    fetch: async () => ({ok: false, json: async () => ({error: 'unavailable'})})});
  vm.runInContext(source.slice(start, end), ctx);
  await ctx.loadPlan();
  assert.equal(ctx.PLAN_SESSIONS, current);
});
