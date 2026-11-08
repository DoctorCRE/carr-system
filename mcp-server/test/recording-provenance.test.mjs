import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { TOOLS } from '../src/tools.js';
import { recordingProvenance, assertRecordingProducer } from './helpers/recording-provenance.mjs';

const recordings = ['postgres-date-verbs', 'quiet-follow-up-owner-filter'].map(name =>
  JSON.parse(readFileSync(new URL(`../../tools/fixtures/${name}.json`, import.meta.url))));

test('all producer recordings bind real registered handlers and contracts', () => {
  for (const recording of recordings) assertRecordingProducer(recording);
});

test('unrelated registrations and edits leave each producer digest unchanged', () => {
  const registry = { ...TOOLS, 'unrelated-recording-probe': {
    write: false, inputSchema: {}, handler: async () => ({ unrelated: true }) },
    'accept-outcome-feedback': { ...TOOLS['accept-outcome-feedback'], handler: async () => ({ changed: true }) } };
  for (const recording of recordings) {
    const { verbs } = recording.provenance.producer_scope;
    assert.deepEqual(recordingProvenance(verbs, registry), recordingProvenance(verbs));
  }
});

test('every recorded verb rejects its own handler, contract and write-mode drift', () => {
  for (const recording of recordings) {
    const { verbs } = recording.provenance.producer_scope;
    for (const verb of verbs) for (const change of [
      { handler: async () => ({ changed: true }) },
      { inputSchema: { type: 'object', required: ['changed'] } },
      { write: !TOOLS[verb].write },
    ]) {
      const changed = recordingProvenance(verbs, { ...TOOLS, [verb]: { ...TOOLS[verb], ...change } });
      assert.notEqual(changed.producer_sha256, recording.provenance.producer_sha256, verb);
    }
  }
});

test('legacy, misattributed and unknown producer scopes fail closed', () => {
  const recording = recordings[0];
  const legacy = structuredClone(recording);
  delete legacy.provenance.producer_scope;
  assert.throws(() => assertRecordingProducer(legacy));
  const wrong = structuredClone(recording);
  wrong.provenance.producer_scope.verbs = ['lead-hot'];
  assert.throws(() => assertRecordingProducer(wrong));
  assert.throws(() => recordingProvenance(['missing-producer']));
});
