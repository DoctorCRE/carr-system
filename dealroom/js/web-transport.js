const browserFetch = (path, init) => fetch(path, init);

function postJson(fetchImpl, path, body, headers) {
  return fetchImpl(path, {
    method: "POST", credentials: "same-origin", headers, body: JSON.stringify(body),
  });
}

/** Cookie-authenticated MCP. Each instance owns its request sequence. */
export function createMcpTransport({ fetchImpl, surface = "deal-room" } = {}) {
  fetchImpl ||= browserFetch;
  const leads = surface === "lead-board";
  function leadError(payload, fallback = "The lead board request was refused.") {
    const error = new Error(payload?.message || payload?.hint || fallback);
    error.code = payload?.error || payload?.code || "tool_error";
    error.payload = payload || {};
    return error;
  }
  let rpcId = 0;
  return async (verb, args = {}) => {
    let response;
    try {
      response = await postJson(fetchImpl, "/mcp", {
        jsonrpc: "2.0", id: ++rpcId, method: "tools/call", params: { name: verb, arguments: args },
      }, { "content-type": "application/json", ...(leads ? { accept: "application/json" } : {}) });
    } catch (cause) {
      if (!leads) throw cause;
      const error = new Error("The Lead Board could not reach the server.");
      error.code = "network_error";
      error.cause = cause;
      throw error;
    }
    if (!response.ok && !leads) {
      // HTTP status distinguishes a refusal from an unanswered write. Keep server details out of the message.
      const body = await response.text().catch(() => "");
      const error = new Error(`live ${verb} -> HTTP ${response.status}`);
      error.status = response.status;
      error.body = body.slice(0, 500);
      throw error;
    }
    const envelope = await response.json().catch((cause) => {
      if (leads) return null;
      throw cause;
    });
    if (leads) {
      if (!response.ok) throw leadError(envelope, `The Lead Board request failed (${response.status}).`);
      if (envelope?.error) throw leadError(envelope.error, "The Lead Board request was refused.");
    } else if (envelope.error) {
      throw new Error(`live ${verb} rpc error: ${envelope.error.message}`);
    }
    const text = leads
      ? envelope?.result?.content?.find((item) => item.type === "text")?.text
      : envelope.result?.content?.[0]?.text;
    let payload;
    try { payload = JSON.parse(leads ? (text || "null") : (text ?? "null")); }
    catch (cause) {
      if (leads) throw leadError(null, "The Lead Board returned an unreadable response.");
      throw cause;
    }
    if (leads && (envelope?.result?.isError || payload?.error || payload?.ok === false)) throw leadError(payload);
    if (!leads && envelope.result?.isError) {
      const error = new Error(`live ${verb} refused: ${payload?.error || "tool_error"}`);
      error.payload = payload;
      throw error;
    }
    return payload;
  };
}

/** System Work uses JSON routes with a session-bound CSRF token, rather than MCP. */
export function createSystemWorkTransport({ fetchImpl } = {}) {
  fetchImpl ||= browserFetch;
  let session = null;
  async function decode(response) {
    const body = await response.json().catch(() => ({}));
    if (!response.ok) {
      const error = new Error(body.message || body.hint || body.error || `Request failed (${response.status})`);
      error.status = response.status;
      error.code = body.error || "request_failed";
      error.payload = body;
      throw error;
    }
    return body;
  }
  async function read(path) {
    return decode(await fetchImpl(path, {
      credentials: "same-origin", headers: { accept: "application/json" },
    }));
  }
  return {
    get session() { return session; },
    async bootstrap() {
      session = await read("/api/system-work/session");
      return session;
    },
    async get(path) {
      const envelope = await read(path);
      return envelope.data ?? envelope;
    },
    async post(path, body, challenge) {
      if (!session?.csrf_token) throw new Error("System work session is not ready.");
      const headers = { "content-type": "application/json", accept: "application/json",
        "x-carr-csrf": session.csrf_token,
        ...(challenge ? { "x-carr-action-challenge": challenge } : {}) };
      const envelope = await decode(await postJson(fetchImpl, path, body, headers));
      return envelope.data ?? envelope;
    },
  };
}
