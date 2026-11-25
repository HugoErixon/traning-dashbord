// Run with node --test tests/test_coach_markdown.js.
const {test} = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const source = fs.readFileSync('public/app.js', 'utf8');
const escape = source.match(/function escapeHtml\(value\) \{[\s\S]*?\n  \}/)[0];
const start = source.indexOf('  function formatCoachInline');
const end = source.indexOf('  function appendChatMessage');
const ctx = vm.createContext({});
vm.runInContext(escape + source.slice(start, end), ctx);

test('coach markdown renders headings, bold and lists', () => {
  const html = ctx.formatCoachReply('### Idag\nKör **lugnt** 8 km.\n\n- Puls under 150\n- Avbryt om foten gör ont\n1. ett\n2. två');
  assert.match(html, /<h4>Idag<\/h4>/);
  assert.match(html, /<strong>lugnt<\/strong>/);
  assert.match(html, /<ul><li>Puls under 150<\/li><li>Avbryt om foten gör ont<\/li><\/ul>/);
  assert.match(html, /<ol><li>ett<\/li><li>två<\/li><\/ol>/);
});

test('coach markdown never lets html through', () => {
  const html = ctx.formatCoachReply('<img src=x onerror=alert(1)> **<b>x</b>**');
  assert.doesNotMatch(html, /<img|<b>/);
  assert.match(html, /&lt;img/);
});
