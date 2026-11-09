import { ToolError } from "../../src/tool-error.ts";

export function object(value: unknown): Record<string, unknown> {
  if (value === null || typeof value !== "object" || Array.isArray(value))
    throw new TypeError("Expected a registry object");
  return Object.fromEntries(Object.entries(value));
}

export function array(value: unknown): unknown[] {
  if (!Array.isArray(value)) throw new TypeError("Expected a registry array");
  return value;
}

export function required<T>(value: T | null | undefined): T {
  if (value === null || value === undefined) throw new TypeError("Missing fixture value");
  return value;
}

// Keep the legacy registry outside the strict graph until its own conversion.
const registryPath = "../../src/tools.js";
const legacy = object(await import(registryPath));
const registry = object(legacy.TOOLS);
export function tool(name: string) {
  const entry = object(registry[name]);
  const handler = entry.handler;
  if (typeof handler !== "function") throw new TypeError(`Missing handler ${name}`);
  return {
    write: entry.write,
    description: String(entry.description),
    handler: async (...args: unknown[]) => object(await handler(...args)),
  };
}

export async function executeRegisteredTool(...args: unknown[]): Promise<unknown> {
  const execute = legacy.executeRegisteredTool;
  if (typeof execute !== "function") throw new TypeError("Missing registered dispatch");
  return execute(...args);
}

export async function assertRegisteredToolInput(name: string, args: object): Promise<void> {
  const validate = legacy.assertRegisteredToolInput;
  if (typeof validate !== "function") throw new TypeError("Missing input validator");
  await validate(name, registry[name], args);
}

export function linkedInActivityPublishedAt(ref: string): Date | null {
  const decode = legacy.linkedInActivityPublishedAt;
  if (typeof decode !== "function") throw new TypeError("Missing activity decoder");
  const value: unknown = decode(ref);
  if (value === null || value instanceof Date) return value;
  throw new TypeError("Invalid activity date");
}

const keyPath = "../../src/derived-key.ts";
const keys = object(await import(keyPath));
export function derivedIdempotencyKey(namespace: string, value: string): string {
  const derive = keys.derivedIdempotencyKey;
  if (typeof derive !== "function") throw new TypeError("Missing key derivation");
  const result: unknown = derive(namespace, value);
  if (typeof result !== "string") throw new TypeError("Invalid derived key");
  return result;
}

export { ToolError };

const clockPath = "../../src/journey-one-clock.v5.ts";
const clock = object(await import(clockPath));
export const DEADLINE_PLAIN = clock.DEADLINE_PLAIN;
export const DEADLINE_GAP_SHIFTED = clock.DEADLINE_GAP_SHIFTED;
export const DEADLINE_OVERLAP_ORIGIN_OFFSET = clock.DEADLINE_OVERLAP_ORIGIN_OFFSET;
export function chicagoThirtyDayDeadline(origin: string) {
  const calculate = clock.chicagoThirtyDayDeadline;
  if (typeof calculate !== "function") throw new TypeError("Missing Chicago deadline calculator");
  return object(calculate(origin));
}
