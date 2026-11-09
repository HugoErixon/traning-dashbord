// Run with node --test tests/test_plan_blocks_ui.js.
const {test} = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const source = fs.readFileSync('public/app.js', 'utf8');
const ctx = vm.createContext({});
vm.runInContext(source.slice(source.indexOf('  function planSessionBlocks('), source.indexOf('  function renderPlanSessionBlocks(')), ctx);
const blocks = (detail, type = 'run') => JSON.parse(JSON.stringify(ctx.planSessionBlocks({detail, type})));

test('two interval speeds stay with their own recovery and decimal distances', () => {
  const result = blocks('1,5 km uppv. 3×800 m i 4:05/km med 90 s gångvila, +60 s extra vila. 4×400 m i 3:55/km med 90 s gångvila. 1,5 km nedjogg. Cirka 7 km.');
  assert.deepEqual(result.map(b => b.label), ['Uppvärmning', 'Intervaller', 'Intervaller', 'Nedjogg']);
  assert.equal(result[0].text, '1,5 km uppv');
  assert.deepEqual(result.map(b => b.paces), [[], ['4:05/km'], ['3:55/km'], []]);
  assert.match(result[1].text, /90 s gångvila, \+60 s extra vila/);
  assert.match(result[2].text, /90 s gångvila/);
});

test('pace ranges are preserved and fallback workouts are not extra scheduled blocks', () => {
  const result = blocks('2,5 km uppv med 4 stegringar. 4×1200 m i 3:24–3:27/km med 2:30 joggvila. 2,5 km nedjogg. Cirka 11 km. Rep 1 över 3:28 eller ont i benhinnorna → kör 5×1000 m i 3:28 i stället.');
  assert.equal(result.length, 3);
  assert.deepEqual(result[1].paces, ['3:24–3:27/km']);
  assert.match(result[1].text, /2:30 joggvila/);
});

test('alternating paces inside repeated parentheses remain one coherent block', () => {
  const result = blocks('2,5 km uppvärmning, 7 × (300 m i 4:15/km + 300 m i 5:05/km), 2,3 km nedjogg.');
  assert.equal(result.length, 3);
  assert.deepEqual(result[1].paces, ['4:15/km', '5:05/km']);
  assert.equal(result[1].text, '7 × (300 m i 4:15/km + 300 m i 5:05/km)');
});

test('comma-separated rep sets and their parenthesized rests stay distinct', () => {
  const result = blocks('2×1000 m i 3:30/km (90 s joggvila), 2×800 m i 3:25/km (90 s), 2×400 m i 3:15/km (60 s).');
  assert.equal(result.length, 3);
  assert.deepEqual(result.map(b => b.paces), [['3:30/km'], ['3:25/km'], ['3:15/km']]);
  assert.match(result[2].text, /\(60 s\)/);
});

test('conditional sentences cannot leak an alternative through a comma or plus', () => {
  assert.deepEqual(blocks('Om benen känns bra, 3×1000 m i 4:00/km + 2×400 m i 3:40/km.'), []);
  assert.deepEqual(blocks('Sista 2 km gärna i 5:20/km om benen känns bra, annars lugnt.'), []);
});

test('no guessed pace for easy runs, free warmups or intervals without targets', () => {
  assert.deepEqual(blocks('8 km lugnt, puls under ~145.', 'easy')[0].paces, []);
  assert.deepEqual(blocks('Fri uppvärmning. Block 3: 5 × 60/60. Fri nedjogg.').map(b => b.paces), [[], [], []]);
  assert.deepEqual(blocks('Långpass 15 km lugnt, 5:30–6:00/km, puls under ~150.', 'easy')[0].paces, ['5:30–6:00/km']);
});

test('missing descriptions, rest and strength do not create running blocks', () => {
  assert.deepEqual(blocks(''), []);
  assert.deepEqual(blocks(null), []);
  assert.deepEqual(blocks('3×10 reps, vila 90 s.', 'lift'), []);
  assert.deepEqual(blocks('Domare kl 19.30.', 'rest'), []);
  assert.deepEqual(blocks('Ingen uppvärmning. Totalt 10 km i 5:00/km.'), []);
});
