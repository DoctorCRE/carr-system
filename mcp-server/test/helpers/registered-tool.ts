import assert from "node:assert/strict";

export { ToolError } from "../../src/tool-error.ts";

export function record(value: unknown): Record<string, unknown> {
  assert.ok(value !== null && typeof value === "object" && !Array.isArray(value), "Expected object");
  // The object guard proves an indexable record; retain identity for fixture mutation.
  return value as Record<string, unknown>;
}
export function records(value: unknown): Record<string, unknown>[] {
  assert.ok(Array.isArray(value), "Expected array");
  return value.map(record);
}
export function parseJson(value: unknown): unknown {
  assert.ok(typeof value === "string", "Expected JSON text");
  return JSON.parse(value);
}
export type TestDb = { query(sql: string, params?: unknown[]): Promise<{ rows: unknown[] }> };

export type QueryCall = { sql: string; params: unknown[] };
export type Replay = {
  request_hash: unknown;
  response: unknown;
  actor_id?: unknown;
  organization_tenant_id?: unknown;
  application_session_id?: unknown;
};

// The unconverted registry imports deferred production modules. Validate its
// dynamic seam here and keep unknown at the runtime boundary.
const registry = record(await import(new URL("../../src/tools.js", import.meta.url).href));
const tools = record(registry.TOOLS);
export function tool(name: string) {
  const value = record(tools[name]);
  const callable = value.handler;
  assert.equal(typeof callable, "function", `Expected handler for ${name}`);
  const handler = async (
    db: TestDb,
    actor: Record<string, unknown>,
    args: Record<string, unknown>,
  ): Promise<Record<string, unknown>> => {
    assert.ok(typeof callable === "function");
    const result: unknown = await Reflect.apply(callable, value, [db, actor, args]);
    return record(result);
  };
  return {
    write: value.write,
    humanOnly: value.humanOnly,
    authorityOnly: value.authorityOnly,
    handler,
    inputSchema: record(value.inputSchema),
  };
}
export function hasTool(name: string): boolean {
  return tools[name] !== undefined;
}
export async function executeRegisteredTool(
  db: TestDb,
  actor: Record<string, unknown>,
  name: string,
  args: Record<string, unknown>,
): Promise<Record<string, unknown>> {
  const callable = registry.executeRegisteredTool;
  assert.ok(typeof callable === "function");
  const result: unknown = await Reflect.apply(callable, registry, [db, actor, name, args]);
  return record(result);
}
export function array(value: unknown): unknown[] {
  assert.ok(Array.isArray(value), "Expected array");
  return value;
}
export function first<T>(items: readonly T[]): T {
  const item = items[0];
  assert.ok(item !== undefined, "Expected first item");
  return item;
}
export function text(value: unknown): string {
  assert.ok(typeof value === "string", "Expected string");
  return value;
}
const keys = record(await import(new URL("../../src/derived-key.ts", import.meta.url).href));
export function derivedIdempotencyKey(namespace: string, key: string): string {
  const callable = keys.derivedIdempotencyKey;
  assert.ok(typeof callable === "function");
  const result: unknown = Reflect.apply(callable, keys, [namespace, key]);
  return text(result);
}
