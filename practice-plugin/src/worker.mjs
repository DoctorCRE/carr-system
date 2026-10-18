import { Server } from '@modelcontextprotocol/sdk/server/index.js';
import { WebStandardStreamableHTTPServerTransport } from '@modelcontextprotocol/sdk/server/webStandardStreamableHttp.js';
import { ListToolsRequestSchema, CallToolRequestSchema, SUPPORTED_PROTOCOL_VERSIONS } from '@modelcontextprotocol/sdk/types.js';
import { TOOLS, callTool } from './tools.mjs';

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
  if (protocolVersion !== null && !SUPPORTED_PROTOCOL_VERSIONS.includes(protocolVersion)) {
    return Response.json({
      jsonrpc: '2.0', error: { code: -32000, message: 'Unsupported protocol version.' }, id: null,
    }, { status: 400, headers });
  }
  const server = new Server({ name: 'Practice Owner Planning', version: '0.1.0' }, { capabilities: { tools: {} } });
  server.setRequestHandler(ListToolsRequestSchema, () => ({ tools: TOOLS.map(({ input, output, calculate, ...publicTool }) => publicTool) }));
  server.setRequestHandler(CallToolRequestSchema, req => callTool(req.params.name, req.params.arguments));
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
