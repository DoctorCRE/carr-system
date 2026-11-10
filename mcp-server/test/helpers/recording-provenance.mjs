import assert from 'node:assert/strict';
import { createHash } from 'node:crypto';
import { readFileSync } from 'node:fs';
import { parse } from '@babel/parser';
import { TOOLS } from '../../src/tools.js';
import { SCAC_MUTATION_OPERATIONS } from '../../src/scac-mutation-registry.current.generated.ts';

const scheme = 'registered-handler-contract/v1';

function leadDependencies(names) {
  const source = readFileSync(new URL('../../src/lead-follow-up.ts', import.meta.url), 'utf8');
  const declarations = new Map();
  for (const item of parse(source, { sourceType: 'module', plugins: ['typescript'] }).program.body) {
    const node = item.declaration || item;
    if (node.type === 'VariableDeclaration') {
      for (const declaration of node.declarations) declarations.set(declaration.id.name, declaration);
    } else if (node.type === 'FunctionDeclaration') declarations.set(node.id.name, node);
  }
  const selected = new Map();
  const visit = node => {
    if (!node || typeof node !== 'object') return;
    if (node.type === 'Identifier' && declarations.has(node.name) && !selected.has(node.name)) {
      const declaration = declarations.get(node.name);
      selected.set(node.name, source.slice(declaration.start, declaration.end));
      visit(declaration);
    }
    for (const child of Object.values(node)) {
      if (Array.isArray(child)) child.forEach(visit);
      else if (child && typeof child === 'object') visit(child);
    }
  };
  for (const name of names) {
    assert.ok(declarations.has(name), `producer dependency ${name}`);
    visit({ type: 'Identifier', name });
  }
  return [...selected].sort(([a], [b]) => a.localeCompare(b));
}

export function recordingProvenance(verbs, registry = TOOLS) {
  const names = [...new Set(verbs)].sort();
  assert.ok(names.length, 'a recording must name its producer verbs');
  const handlers = names.map(name => {
    const tool = registry[name];
    assert.equal(typeof tool?.handler, 'function', `registered producer ${name}`);
    return { name, handler: tool.handler.toString(), inputSchema: tool.inputSchema,
      write: tool.write, registry_entry: SCAC_MUTATION_OPERATIONS[name] };
  });
  const dependencies = leadDependencies([
    ...(names.includes('quiet-follow-ups') ? ['CONTACTABLE_STATES'] : []),
    ...(names.includes('lead-board') ? ['leadAdvice'] : []),
  ]);
  const contracts = names.includes('quiet-follow-ups')
    ? { 'quiet-follow-ups.v1': JSON.parse(readFileSync(new URL('../../contracts/quiet-follow-ups.v1.json', import.meta.url))) } : {};
  return { producer_scope: { scheme, verbs: names },
    producer_sha256: createHash('sha256').update(JSON.stringify({ scheme, handlers, dependencies, contracts })).digest('hex') };
}

export function assertRecordingProducer(recording) {
  const verbs = recording.responses ? Object.keys(recording.responses) : [recording.provenance.query.verb];
  const expected = recordingProvenance(verbs);
  assert.deepEqual(recording.provenance.producer_scope, expected.producer_scope);
  assert.equal(recording.provenance.producer_sha256, expected.producer_sha256);
}
