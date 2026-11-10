import assert from "node:assert/strict";

export function record(value: unknown): Record<string, unknown> {
  assert.ok(typeof value === "object" && value !== null && !Array.isArray(value));
  return Object.fromEntries(Object.entries(value));
}
export function array(value: unknown): unknown[] {
  assert.ok(Array.isArray(value));
  return value;
}
const registered = record(await import(new URL("../../src/tools.js", import.meta.url).href));
export const registry = record(registered.TOOLS);
export async function handler(name: string, ...args: unknown[]): Promise<Record<string, unknown>> {
  const fn = record(registry[name]).handler;
  assert.equal(typeof fn, "function");
  if (typeof fn !== "function") throw new TypeError(`Missing handler ${name}`);
  const result: unknown = await fn(...args);
  return record(result);
}
