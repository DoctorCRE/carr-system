import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';

test('financial lifecycle callers share one strict calendar-date rule', async () => {
  const { isCalendarDate } = await import('../src/calendar-date.js');
  for (const date of ['2024-02-29', '2026-09-01', '0001-01-01', '9999-12-31'])
    assert.equal(isCalendarDate(date), true, date);
  for (const date of [null, undefined, 20260901, '', '2026-2-01', '2026-02-30',
    '2025-02-29', '2026-13-01', '2026-00-01', '2026-09-00', '2026-09-31',
    '2026-09-01T00:00:00Z', ' 2026-09-01', '2026-09-01\n'])
    assert.equal(isCalendarDate(date), false, String(date));
  for (const file of ['tools.js', 'invoice-tracker.js']) {
    const source = readFileSync(new URL(`../src/${file}`, import.meta.url), 'utf8');
    assert.match(source, /import \{ isCalendarDate \} from ["']\.\/calendar-date\.js["']/);
    const caller = file === 'tools.js' ? source.slice(source.indexOf('"update-deal": {'), source.indexOf('"reassign-deal": {')) : source;
    assert.ok(!/Date\.parse\(.*T00:00:00Z/.test(caller), `${file} delegates the calendar rule`);
  }
});

test('registered receipt transaction proof is required by the migration check', () => {
  const ci = readFileSync(new URL('../../ops/ci.sh', import.meta.url), 'utf8');
  assert.ok(/CARR_INVOICE_TEST_DATABASE_URL="\$dsn"/.test(ci));
  assert.ok(/CARR_INVOICE_TEST_REQUIRED=1/.test(ci));
  assert.ok(/node --test mcp-server\/test\/invoice-tracker-transaction\.test\.mjs/.test(ci));
});
