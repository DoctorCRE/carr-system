// Serve the engine's comparison reads: one named partner, registered reads only.

/** index.js binds the browser door's own handlers, the verb registry and identity.js's actor builder. */
export type ComparisonDoor<Actor> = {
  actorFor(partner: unknown): Actor | null;
  tools: Readonly<Record<string, object>>;
  mcp(request: Request, actor: Actor): Promise<Response>;
  pipeline(request: Request, actor: Actor): Promise<Response>;
  business(request: Request, actor: Actor): Promise<Response>;
};

const refuse = (status: number, error: string) =>
  Response.json({ error }, { status, headers: { "cache-control": "no-store" } });

const SESSION_METHODS = new Set(["initialize", "notifications/initialized", "ping", "tools/list"]);

// Partner-in-person reads stay off a machine door the comparison never needs.
function refusedCall(rpc: unknown, tools: ComparisonDoor<unknown>["tools"]): string | null {
  if (typeof rpc !== "object" || rpc === null || !("method" in rpc) || typeof rpc.method !== "string")
    return "invalid_request";
  if (SESSION_METHODS.has(rpc.method)) return null;
  if (rpc.method !== "tools/call") return "method_not_allowed";
  const params = "params" in rpc && typeof rpc.params === "object" && rpc.params !== null ? rpc.params : {};
  const name = "name" in params && typeof params.name === "string" ? params.name : "";
  const tool = Object.hasOwn(tools, name) ? tools[name] : undefined;
  const read = tool !== undefined && !("write" in tool && tool.write) && !("humanOnly" in tool && tool.humanOnly);
  return read ? null : "read_only";
}

export async function serveComparisonRead<Actor>(
  partner: unknown,
  request: Request,
  door: ComparisonDoor<Actor>,
): Promise<Response> {
  const actor = door.actorFor(partner);
  if (actor === null) return refuse(403, "unknown_partner");
  const { pathname } = new URL(request.url);
  if (pathname === "/mcp") {
    if (request.method !== "POST") return refuse(405, "method_not_allowed");
    const body = await request.text();
    let rpc: unknown;
    try {
      rpc = JSON.parse(body);
    } catch {
      return refuse(400, "invalid_json");
    }
    const refused = refusedCall(rpc, door.tools);
    return refused ? refuse(403, refused) : door.mcp(new Request(request, { body }), actor);
  }
  if (request.method !== "GET") return refuse(405, "method_not_allowed");
  if (pathname === "/pipeline/changes") return door.pipeline(request, actor);
  if (pathname.startsWith("/api/v1/business/")) return door.business(request, actor);
  return refuse(404, "not_found");
}
