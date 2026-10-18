import { Server } from '@modelcontextprotocol/sdk/server/index.js';
import { WebStandardStreamableHTTPServerTransport } from '@modelcontextprotocol/sdk/server/webStandardStreamableHttp.js';
import { ListToolsRequestSchema, CallToolRequestSchema, ListResourcesRequestSchema, ReadResourceRequestSchema, SUPPORTED_PROTOCOL_VERSIONS, McpError, ErrorCode } from '@modelcontextprotocol/sdk/types.js';
import { MODERN_VERSION, PUBLIC_TOOLS, capabilities, dispatchTool, modernResponse } from './modern.mjs';
import { RESOURCES, readResource } from './resources.mjs';

const BODY_LIMIT = 16384;
const headers = { 'Cache-Control': 'no-store', 'X-Content-Type-Options': 'nosniff' };
const fail = (status, error, extra = {}) => Response.json({ error }, { status, headers: { ...headers, ...extra } });

async function handle(request) {
  const url = new URL(request.url);
  if (url.pathname !== '/mcp') return fail(404, 'Endpoint not found.');
  const origin = request.headers.get('Origin');
  if (origin && ![url.origin, 'https://chatgpt.com'].includes(origin)) return fail(403, 'Origin is not allowed.');
  if (request.method !== 'POST') return fail(405, 'Only MCP POST requests are supported.', { Allow: 'POST' });
  if (request.headers.get('Content-Type')?.split(';')[0].trim().toLowerCase() !== 'application/json') {
    return fail(415, 'Use application/json.');
  }
  const protocolVersion = request.headers.get('MCP-Protocol-Version');
  {
    let bytes = 0, chunks = [];
    const reader = request.body?.getReader();
    if (!reader) return fail(400, 'Invalid MCP request.');
    while (true) {
      const { value, done } = await reader.read();
      if (done) break;
      bytes += value.length;
      if (bytes > BODY_LIMIT) { await reader.cancel(); return fail(413, 'Request body too large.'); }
      chunks.push(value);
    }
    const body = new Uint8Array(bytes); let offset = 0;
    for (const chunk of chunks) { body.set(chunk, offset); offset += chunk.length; }
    let message;
    try { message = JSON.parse(new TextDecoder().decode(body)); }
    catch { return Response.json({ jsonrpc: '2.0', error: { code: -32700, message: 'Invalid JSON.' } }, { status: 400, headers }); }
    if (protocolVersion === MODERN_VERSION || message?.params?._meta?.['io.modelcontextprotocol/protocolVersion'] === MODERN_VERSION) {
      if (!(request.headers.get('Accept') || '').includes('application/json')) return fail(406, 'Accept application/json.');
      const response = modernResponse(message, request);
      return Response.json(response.body, { status: response.status, headers });
    }
    request = new Request(request.url, { method: request.method, headers: request.headers, body });
  }
  if (protocolVersion !== null && !SUPPORTED_PROTOCOL_VERSIONS.includes(protocolVersion)) {
    return Response.json({
      jsonrpc: '2.0', error: { code: -32000, message: 'Unsupported protocol version.' }, id: null,
    }, { status: 400, headers });
  }
  const server = new Server({ name: 'Practice Owner Planning', version: '0.2.0' }, { capabilities });
  server.setRequestHandler(ListToolsRequestSchema, () => ({ tools: PUBLIC_TOOLS }));
  server.setRequestHandler(CallToolRequestSchema, req => dispatchTool(req.params));
  server.setRequestHandler(ListResourcesRequestSchema, () => ({ resources: RESOURCES }));
  server.setRequestHandler(ReadResourceRequestSchema, req => {
    const resource = readResource(req.params.uri);
    if (!resource) throw new McpError(ErrorCode.InvalidParams, 'Unknown UI resource.');
    return resource;
  });
  const transport = new WebStandardStreamableHTTPServerTransport({
    sessionIdGenerator: undefined, enableJsonResponse: true, maxRequestBodySize: BODY_LIMIT,
  });
  try {
    await server.connect(transport);
    const response = await transport.handleRequest(request);
    for (const [key, value] of Object.entries(headers)) response.headers.set(key, value);
    return response;
  } catch {
    return fail(400, 'Invalid MCP request.');
  } finally {
    await server.close();
  }
}

export default { fetch: handle };
