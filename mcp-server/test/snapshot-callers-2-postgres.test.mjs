import test from 'node:test';
import { proveSnapshotCallers } from './snapshot-service-proof.mjs';

test('all direct snapshot callers use distinct databases on their actual CI-service connections (partition 2 of 2)', () => proveSnapshotCallers(1));
