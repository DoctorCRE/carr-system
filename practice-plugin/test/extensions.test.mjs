import test from 'node:test';
import assert from 'node:assert/strict';
import Ajv from 'ajv';
import { OpenAIFormSchema } from '@openai/mcp-extensions/server';
import worker from '../src/worker.mjs';
import { MODERN_VERSION, PUBLIC_TOOLS } from '../src/modern.mjs';
import { CATALOG, INTAKE_FORM, formContent, calculatePlan, PANEL_URI, PLANNER_URI } from '../src/planner.mjs';
import { RESOURCES } from '../src/resources.mjs';

export const dental = { practice_type: 'dental_gp', providers: 1, operatories: 5, exam_rooms: 0, market: 'mobile_downtown' };
export const medical = { practice_type: 'medical', providers: 2, operatories: 0, exam_rooms: 6, market: 'other_market' };
const caps = { extensions: { 'openai/elicitation': { form: {} } } };
async function rpc(method, params = {}, options = {}) {
  const p = { ...params, _meta: { 'io.modelcontextprotocol/protocolVersion': MODERN_VERSION,
    'io.modelcontextprotocol/clientCapabilities': options.caps ?? caps } };
  const name = params.name ?? params.uri;
  const headers = { 'Content-Type': 'application/json', Accept: 'application/json', 'MCP-Protocol-Version': MODERN_VERSION,
    'Mcp-Method': method, ...(name ? { 'Mcp-Name': name } : {}), ...options.headers };
  for (const key of options.omit ?? []) delete headers[key];
  const response = await worker.fetch(new Request('https://practice.synthetic.invalid/mcp', { method: 'POST', headers,
    body: JSON.stringify({ jsonrpc: '2.0', id: 1, method, params: p }) }));
  return { status: response.status, body: await response.json() };
}

test('tool metadata snapshot: only global and thread entrypoints, with exact UI resources', async () => {
  const { body } = await rpc('tools/list');
  assert.equal(body.result.resultType, 'complete');
  const snapshot = body.result.tools.filter(t => t._meta).map(t => ({ name: t.name, ...t._meta }));
  assert.deepEqual(snapshot, [
    { name: 'plan_practice_space', ui: { resourceUri: PANEL_URI } },
    { name: 'open_practice_space_planner', ui: { resourceUri: PLANNER_URI, visibility: ['app'] }, 'openai/ui': { entrypoints: [{ type: 'global' }, { type: 'thread' }] } },
    { name: 'update_practice_space_plan', ui: { resourceUri: PANEL_URI } },
    { name: 'intake_practice_space_plan', ui: { resourceUri: PANEL_URI } },
  ]);
  assert.deepEqual((await rpc('resources/list')).body.result.resources, RESOURCES);
  for (const uri of [PLANNER_URI, PANEL_URI]) {
    const r = (await rpc('resources/read', { uri })).body.result.contents[0];
    assert.equal(r.mimeType, 'text/html;profile=mcp-app');
    assert.deepEqual(r._meta['openai/ui'], { preferredDisplayMode: 'fullscreen', availableDisplayModes: ['inline', 'fullscreen'] });
    assert.deepEqual(r._meta.ui.csp, { connectDomains: [], resourceDomains: [] });
    assert.match(r.text, /Practice space planner/);
    assert.equal(/PLANNER_(CSS|SCRIPT|CATALOG)/.test(r.text), false, 'all template slots must be replaced');
  }
  assert.equal((await rpc('resources/read', { uri: 'ui://practice/unknown' })).status, 400);
  for (const tool of PUBLIC_TOOLS.filter(t => t._meta?.['openai/ui'])) {
    assert.deepEqual(tool.inputSchema, { type: 'object', properties: {}, additionalProperties: false });
    assert.ok(tool.icons[0].src.startsWith('data:image/svg+xml;base64,'));
    assert.equal((await rpc('tools/call', { name: tool.name, arguments: {} })).body.result.isError, undefined);
  }
});

test('native form validates catalog, inline SVG icons and bounded answers', () => {
  assert.ok(OpenAIFormSchema.safeParse(INTAKE_FORM).success);
  const choices = INTAKE_FORM.properties.practice_type.oneOf;
  assert.deepEqual(choices.map(x => x.const), CATALOG.map(x => x.value));
  assert.equal(new Set(CATALOG.map(x => x.group)).size, 6);
  for (const choice of choices) {
    const svg = Buffer.from(choice['x-openai-thumbnail'].src.split(',')[1], 'base64').toString();
    assert.match(svg, /viewBox="0 0 20 20"/); assert.match(svg, /currentColor/);
    assert.doesNotMatch(svg, /<image|<script|href=/);
  }
  const ajv = new Ajv({ strict: false });
  const schema = PUBLIC_TOOLS.find(t => t.name === 'update_practice_space_plan').inputSchema;
  const validate = ajv.compile(schema);
  for (const fixture of [dental, medical]) { assert.ok(formContent.safeParse(fixture).success); assert.ok(validate(fixture)); }
  for (const invalid of [{ ...dental, practice_type: 'free text' }, { ...dental, providers: 0 }, { ...dental, market: 'unbounded' }, { ...dental, operatories: 1.5 }]) {
    assert.equal(formContent.safeParse(invalid).success, false); assert.equal(validate(invalid), false);
  }
});

test('registered form uses multi-round-trip input requests, validates continuation and cancellation', async () => {
  const params = { name: 'intake_practice_space_plan', arguments: {} };
  const first = (await rpc('tools/call', params)).body.result;
  assert.equal(first.resultType, 'input_required'); assert.equal(first.requestState, undefined);
  assert.deepEqual(first.inputRequests.practice_intake, { method: 'openai/elicitation/create', params: {
    mode: 'form', message: 'Choose the practice program. Enter counts only; no names, addresses or patient information.', requestedSchema: INTAKE_FORM,
  } });
  for (const fixture of [dental, medical]) {
    const result = (await rpc('tools/call', { ...params, inputResponses: { practice_intake: { action: 'accept', content: fixture } } })).body.result;
    assert.equal(result.resultType, 'complete'); assert.deepEqual(result.structuredContent, calculatePlan(fixture).structuredContent);
  }
  for (const action of ['cancel', 'decline']) assert.deepEqual((await rpc('tools/call', { ...params, inputResponses: { practice_intake: { action } } })).body.result.structuredContent, { status: action, plan: null });
  for (const content of [{ ...dental, practice_type: 'REJECTED_INPUT' }, { ...dental, extra: 'REJECTED_INPUT' }, { ...dental, operatories: 0 }]) {
    const result = (await rpc('tools/call', { ...params, inputResponses: { practice_intake: { action: 'accept', content } } })).body.result;
    assert.ok(result.isError); assert.doesNotMatch(JSON.stringify(result), /REJECTED_INPUT/);
  }
  assert.equal((await rpc('tools/call', params, { caps: {} })).body.error.code, -32021);
});

test('modern protocol headers and metadata are required; no initialize or server-initiated requests', async () => {
  assert.equal((await rpc('server/discover')).body.result.supportedVersions[0], MODERN_VERSION);
  for (const omit of [['MCP-Protocol-Version'], ['Mcp-Method']]) assert.equal((await rpc('ping', {}, { omit })).body.error.code, -32020);
  assert.equal((await rpc('tools/call', { name: 'update_practice_space_plan', arguments: dental }, { omit: ['Mcp-Name'] })).body.error.code, -32020);
  assert.equal((await rpc('ping', {}, { headers: { 'Mcp-Method': 'tools/list' } })).body.error.code, -32020);
  assert.equal((await rpc('initialize')).status, 404);
});
