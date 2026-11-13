import assert from 'node:assert/strict';
import test from 'node:test';
import { callerPartition, proveSnapshotService, proveSnapshotCleanup, proveSnapshotMigration } from './snapshot-service-proof.mjs';

test('CI-service snapshots clone named databases on the provided service and clean up role state', proveSnapshotService);
test('snapshot cleanup preserves runner templates and rejects a leaked suite database', proveSnapshotCleanup);
test('the real child suite receives a branch migration, and rejecting stale pickup fails its SQL assertion', proveSnapshotMigration);

test('snapshot caller partitions cover every caller exactly once as inventory grows', () => {
  for (const count of [1, 2, 3, 101]) {
    const callers = Array.from({ length: count }, (_, index) => ({ file: `${index}.test.mjs` }));
    const partitions = [callerPartition(callers, 0), callerPartition(callers, 1)];
    assert.deepEqual(partitions.flat().toSorted((a, b) => a.file.localeCompare(b.file)),
      callers.toSorted((a, b) => a.file.localeCompare(b.file)));
    assert.equal(new Set(partitions.flat().map(row => row.file)).size, count);
  }
  assert.throws(() => callerPartition([], 2), /partition/);
});
