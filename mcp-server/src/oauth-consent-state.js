// One Durable Object per unpredictable consent id. KV cannot provide an
// atomic read/delete; this transaction is the sole consumption authority.
export class OAuthConsentState {
  constructor(state) { this.storage = state.storage; }

  async fetch(request) {
    const input = await request.json();
    if (new URL(request.url).pathname === "/create") {
      await this.storage.transaction(async tx => {
        if (await tx.get("pending")) throw new Error("Consent already exists");
        await tx.put("pending", input);
        await tx.setAlarm(input.expiresAt);
      });
      return new Response(null, { status: 204 });
    }
    if (new URL(request.url).pathname !== "/consume") return new Response(null, { status: 404 });
    return this.storage.transaction(async tx => {
      const pending = await tx.get("pending");
      if (!pending || pending.expiresAt <= Date.now()) return new Response(null, { status: 400 });
      if (pending.state !== input.state || pending.browser !== input.browser || pending.csrf !== input.csrf)
        return new Response(null, { status: 403 });
      // Commit before any provider call. Failure after consumption requires a
      // fresh authorization attempt, never another grant from the same consent.
      await tx.delete("pending");
      return Response.json(pending);
    });
  }

  async alarm() { await this.storage.deleteAll(); }
}

export async function consentState(env, id, operation, input) {
  const stub = env.OAUTH_CONSENT_STATE.get(env.OAUTH_CONSENT_STATE.idFromName(id));
  return stub.fetch(new Request(`https://consent.internal/${operation}`, {
    method: "POST", headers: { "content-type": "application/json" }, body: JSON.stringify(input),
  }));
}
