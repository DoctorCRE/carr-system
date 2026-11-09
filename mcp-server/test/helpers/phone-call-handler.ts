import assert from "node:assert/strict";

const handlerPath = "../../src/phone-call-sessions.js";
const legacyHandler: unknown = await import(handlerPath);
assert.ok(typeof legacyHandler === "object" && legacyHandler !== null && "createPhoneCallHandler" in legacyHandler);
const factory = legacyHandler.createPhoneCallHandler;
assert.ok(typeof factory === "function");
export type Handler = {
  fetch(request: Request, env: object, actor: unknown): Promise<Response>;
  studioFetch(request: Request, env: object): Promise<Response>;
};
export function createPhoneCallHandler(options: object): Handler {
  assert.ok(typeof factory === "function");
  const handler: unknown = factory(options);
  assert.ok(typeof handler === "object" && handler !== null && "fetch" in handler && "studioFetch" in handler);
  const fetch = handler.fetch,
    studioFetch = handler.studioFetch;
  assert.ok(typeof fetch === "function" && typeof studioFetch === "function");
  return {
    async fetch(request, env, actor) {
      const result: unknown = await fetch(request, env, actor);
      assert.ok(result instanceof Response);
      return result;
    },
    async studioFetch(request, env) {
      const result: unknown = await studioFetch(request, env);
      assert.ok(result instanceof Response);
      return result;
    },
  };
}
