import { TOOLS, callTool } from './tools.mjs';
import { PLANNER_TOOLS, callPlanner, intake, PANEL_URI } from './planner.mjs';
import { RESOURCES, readResource } from './resources.mjs';

export const MODERN_VERSION = '2026-07-28';
export const PUBLIC_TOOLS = [...TOOLS.map(({ input, output, calculate, ...tool }) => tool.name === 'plan_practice_space'
  ? { ...tool, _meta: { ui: { resourceUri: PANEL_URI } } } : tool), ...PLANNER_TOOLS];
export const capabilities = { tools: {}, resources: {}, extensions: { 'io.modelcontextprotocol/ui': { mimeTypes: ['text/html;profile=mcp-app'] } } };
const object = v => v !== null && typeof v === 'object' && !Array.isArray(v);
export function dispatchTool(params) {
  return PLANNER_TOOLS.some(t => t.name === params.name)
    ? callPlanner(params.name, params.arguments) : callTool(params.name, params.arguments);
}
export function modernResponse(message, request) {
  const id = typeof message?.id === 'string' || Number.isSafeInteger(message?.id) ? message.id : undefined;
  const error = (code, text, status = 400, data) => ({ status, body: { jsonrpc: '2.0', ...(id === undefined ? {} : { id }), error: { code, message: text, ...(data ? { data } : {}) } } });
  if (!object(message) || message.jsonrpc !== '2.0' || id === undefined || typeof message.method !== 'string') return error(-32600, 'Invalid request.');
  const p = message.params ?? {}, meta = p._meta;
  if (!object(p) || !object(meta) || typeof meta['io.modelcontextprotocol/protocolVersion'] !== 'string' || !object(meta['io.modelcontextprotocol/clientCapabilities'])) return error(-32602, 'Required per-request metadata is missing.');
  const version = meta['io.modelcontextprotocol/protocolVersion'];
  if (request.headers.get('MCP-Protocol-Version') !== version || request.headers.get('Mcp-Method') !== message.method) return error(-32020, 'Required headers must match request metadata and method.');
  if (version !== MODERN_VERSION) return error(-32022, 'Unsupported protocol version.', 400, { supported: [MODERN_VERSION] });
  const expectedName = message.method === 'tools/call' ? p.name : message.method === 'resources/read' ? p.uri : undefined;
  if (expectedName !== undefined && request.headers.get('Mcp-Name') !== expectedName) return error(-32020, 'Required name header must match the request.');
  const complete = result => ({ status: 200, body: { jsonrpc: '2.0', id, result: { resultType: 'complete', ...result } } });
  if (message.method === 'server/discover') return complete({ supportedVersions: [MODERN_VERSION], capabilities,
    _meta: { 'io.modelcontextprotocol/serverInfo': { name: 'Practice Owner Planning', version: '0.2.0' } } });
  if (message.method === 'ping') return complete({});
  if (message.method === 'tools/list') return complete({ tools: PUBLIC_TOOLS });
  if (message.method === 'resources/list') return complete({ resources: RESOURCES });
  if (message.method === 'resources/read') {
    const resource = readResource(p.uri);
    return resource ? complete(resource) : error(-32602, 'Unknown UI resource.');
  }
  if (message.method === 'tools/call') {
    if (typeof p.name !== 'string' || !PUBLIC_TOOLS.some(t => t.name === p.name)) return error(-32602, 'Unknown planning tool.');
    if (p.arguments !== undefined && !object(p.arguments)) return error(-32602, 'Arguments must be an object.');
    if (p.name === 'intake_practice_space_plan') {
      const response = intake(p, meta['io.modelcontextprotocol/clientCapabilities']);
      if (response === null) return error(-32021, 'Native intake requires OpenAI form capability.', 400,
        { requiredCapabilities: { extensions: { 'openai/elicitation': { form: {} } } } });
      return complete(response);
    }
    if (p.inputResponses !== undefined || p.requestState !== undefined) return error(-32602, 'Unexpected continuation fields.');
    return complete(dispatchTool(p));
  }
  return error(-32601, 'Method not found.', 404);
}
