// Test the generated request contract at the invoice handler.
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { mkdtempSync, readFileSync, writeFileSync, rmSync } from 'node:fs';
import { spawnSync } from 'node:child_process';
import { fileURLToPath } from 'node:url';
import { tmpdir } from 'node:os';
import path from 'node:path';

const root = fileURLToPath(new URL('../', import.meta.url));
test('invoice receipt handler rejects a string commission version before deployment', () => {
  const dir = mkdtempSync(path.join(tmpdir(), 'worker-invoice-types-'));
  try {
    const file = path.join(dir, 'receipt.ts');
    const config = path.join(dir, 'tsconfig.json');
    writeFileSync(config, JSON.stringify({ extends: root + 'tsconfig.json',
      include: [root + '.generated/**/*.d.ts', file] }));
    writeFileSync(file, `import { invoiceTrackerTools } from ${JSON.stringify(root + 'src/invoice-tracker.ts')};
declare const tools: ReturnType<typeof invoiceTrackerTools>;
declare const db: Parameters<typeof tools['record-commission-receipt']['handler']>[0];
declare const actor: Parameters<typeof tools['record-commission-receipt']['handler']>[1];
tools['record-commission-receipt'].handler(db, actor, {
  idempotency_key: 'synthetic', commission_id: 'synthetic', base_version: '1', received_on: '2026-01-01',
});
`);
    const check = () => spawnSync('node', ['node_modules/typescript/bin/tsc', '--project', config],
      { cwd: root, encoding: 'utf8' });
    const result = check();
    assert.notEqual(result.status, 0, 'a string commission version must fail typechecking');
    assert.match(result.stdout + result.stderr, /receipt\.ts.*TS2322.*string.*number/);
    writeFileSync(file, readFileSync(file, 'utf8').replace("base_version: '1'", 'base_version: 1'));
    const accepted = check();
    assert.equal(accepted.status, 0, accepted.stdout + accepted.stderr);
  } finally {
    rmSync(dir, { recursive: true, force: true });
  }
});
