import test from 'node:test';
import assert from 'node:assert/strict';
import { createHash } from 'node:crypto';
import { readFile } from 'node:fs/promises';
import { compileSchema } from '../../workspace/contracts/schema-validator.mjs';
import { prepareExplainerRevision } from '../src/client-publications.js';

const schema = JSON.parse(await readFile(new URL('../../client-portal/explainer.v1.schema.json', import.meta.url)));
const validate = compileSchema(schema);
const payload = (section_key = 'transaction') => ({
  kind: 'explainer', section_key, title: 'Synthetic explainer',
  paragraphs: [[{ text: 'Synthetic text.' }, { link: { label: 'Synthetic link', url: 'https://example.com/path' } }]],
});

test('synthetic preparation accepts both optional keys and seals the complete payload', () => {
  for (const key of ['transaction', 'carr']) {
    const input = payload(key);
    assert.equal(validate(input).valid, true);
    const revision = prepareExplainerRevision(input);
    assert.deepEqual(revision.payload, input);
    assert.equal(revision.content_revision, `sha256:${createHash('sha256').update(JSON.stringify(input)).digest('hex')}`);
    assert.deepEqual(Object.keys(revision).sort(), ['content_revision', 'payload']);
    assert.equal(revision.review_state, undefined);
    assert.equal(revision.publication_state, undefined);
  }
});

test('synthetic preparation refuses caller approval and unknown internal fields', () => {
  for (const extra of [{ reviewed: true }, { review_state: 'approved' }, { internal_notes: 'Synthetic private' }, { published: true }]) {
    const input = { ...payload(), ...extra };
    assert.equal(validate(input).valid, false);
    assert.throws(() => prepareExplainerRevision(input), /explainer_payload_invalid/);
  }
  for (const input of [null, [], {}, { ...payload(), section_key: 'roadmap' }])
    assert.throws(() => prepareExplainerRevision(input), /explainer_payload_invalid/);
});

test('edited reviewed fields require different digests without changing the old revision', () => {
  const input = payload();
  const original = prepareExplainerRevision(input);
  const edits = [
    { ...input, section_key: 'carr' }, { ...input, title: 'Changed title' },
    { ...input, paragraphs: [[{ text: 'Changed text' }]] },
    { ...input, paragraphs: [[{ link: { label: 'Changed label', url: 'https://example.com/path' } }]] },
    { ...input, paragraphs: [[{ link: { label: 'Synthetic link', url: 'https://example.com/changed' } }]] },
    { ...input, paragraphs: [...input.paragraphs, [{ text: 'New paragraph' }]] },
  ];
  for (const edit of edits) assert.notEqual(prepareExplainerRevision(edit).content_revision, original.content_revision);
  input.paragraphs[0][0].text = 'Mutated caller input';
  assert.equal(original.payload.paragraphs[0][0].text, 'Synthetic text.');
  assert.throws(() => { original.payload.paragraphs[0][0].text = 'Mutation'; }, TypeError);
  assert.throws(() => original.payload.paragraphs.push([]), TypeError);
});

test('canonical digest ignores object insertion order and preserves exact reviewed text', () => {
  const input = payload();
  const reordered = { paragraphs: [[{ text: 'Synthetic text.' }, { link: { url: 'https://example.com/path', label: 'Synthetic link' } }]], title: input.title, section_key: input.section_key, kind: input.kind };
  assert.equal(prepareExplainerRevision(reordered).content_revision, prepareExplainerRevision(input).content_revision);
  assert.notEqual(prepareExplainerRevision({ ...input, title: `${input.title} ` }).content_revision, prepareExplainerRevision(input).content_revision);
});

test('schema and producer refuse HTML, unsafe links, credentials, and nested unknown fields', () => {
  const invalidRuns = [
    { text: '<script>alert(1)</script>' }, { text: '<!-- comment -->' },
    { text: 'Safe', internal: 'Synthetic private' },
    { link: { label: '<b>HTML</b>', url: 'https://example.com' } },
    { link: { label: 'Safe', url: 'https://example.com', internal: true } },
  ];
  for (const url of ['javascript:alert(1)', 'http://example.com', '//example.com', 'https://user:password@example.com', 'https://user@example.com', 'https://example.com\\@evil.com', 'https://example.com/\npath', 'https://example.com\n', 'https://example.com/<script>'])
    invalidRuns.push({ link: { label: 'Synthetic link', url } });
  for (const run of invalidRuns) {
    const input = { ...payload(), paragraphs: [[run]] };
    assert.equal(validate(input).valid, false, JSON.stringify(run));
    assert.throws(() => prepareExplainerRevision(input), /explainer_payload_invalid/);
  }
  assert.throws(() => prepareExplainerRevision({ ...payload(), title: '<b>Title</b>' }), /explainer_payload_invalid/);
  assert.throws(() => prepareExplainerRevision({ ...payload(), paragraphs: [[{ link: { label: 'Link', url: 'https://example.com:99999' } }]] }), /explainer_payload_invalid/);
});

test('schema bounds paragraphs and runs and producer enforces UTF-8 revision size', () => {
  for (const paragraphs of [[], Array.from({ length: 21 }, () => [{ text: 'x' }]), [[]], [Array.from({ length: 33 }, () => ({ text: 'x' }))]]) {
    const input = { ...payload(), paragraphs };
    assert.equal(validate(input).valid, false);
    assert.throws(() => prepareExplainerRevision(input), /explainer_payload_invalid/);
  }
  assert.doesNotThrow(() => prepareExplainerRevision({ ...payload(), paragraphs: Array.from({ length: 20 }, () => Array.from({ length: 32 }, () => ({ text: 'x' }))) }));
  const sized = size => ({ ...payload(), paragraphs: Array.from({ length: 10 }, () => [{ text: 'x'.repeat(size) }]) });
  assert.doesNotThrow(() => prepareExplainerRevision(sized(3000)));
  assert.throws(() => prepareExplainerRevision(sized(4000)), /explainer_payload_too_large/);
  assert.throws(() => prepareExplainerRevision({ ...payload(), paragraphs: Array.from({ length: 10 }, () => [{ text: '😀'.repeat(1000) }]) }), /explainer_payload_too_large/);
});

test('canonical UTF-8 size accepts exactly 32 KiB and refuses the next byte', () => {
  const input = { ...payload(), paragraphs: Array.from({ length: 9 }, () => [{ text: 'x'.repeat(3500) }]) };
  const remaining = 32768 - Buffer.byteLength(JSON.stringify(input));
  input.paragraphs[0][0].text += 'x'.repeat(Math.floor(remaining / 3));
  input.paragraphs[1][0].text += 'x'.repeat(Math.floor(remaining / 3));
  input.paragraphs[2][0].text += 'x'.repeat(remaining - 2 * Math.floor(remaining / 3));
  assert.equal(Buffer.byteLength(JSON.stringify(input)), 32768);
  assert.doesNotThrow(() => prepareExplainerRevision(input));
  input.paragraphs[0][0].text += 'x';
  assert.throws(() => prepareExplainerRevision(input), /explainer_payload_too_large/);
});
