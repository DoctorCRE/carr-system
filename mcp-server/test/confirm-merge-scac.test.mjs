import test from 'node:test';
import assert from 'node:assert/strict';
import {registeredOperation} from '../src/mutation-registry.js';
import {assertAdmits} from './helpers/registry-admission.mjs';

test('identity merge retains its human-only admission contract', async () => {
  await assertAdmits(['confirm-merge']);
  assert.equal(registeredOperation('confirm-merge').human_only, true);
});
