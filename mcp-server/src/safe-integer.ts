import { ToolError } from "./tool-error.ts";
import type { Actor, Db } from "./verb-contract.ts";

type IntegerOptions = { allowString?: boolean; minimum?: number };

export function safeInteger(value: unknown, error: string, { allowString = true, minimum = 1 }: IntegerOptions = {}): number {
  const normalized = allowString && typeof value === "string" && /^(0|[1-9][0-9]*)$/.test(value)
    ? Number(value) : value;
  // Number.isSafeInteger is not a type guard; past it, normalized is a number.
  if (!Number.isSafeInteger(normalized) || (normalized as number) < minimum)
    throw new ToolError({ error });
  return normalized as number;
}

export function integerFields<T extends Record<string, unknown> | null | undefined>(record: T, fields: string[], error: string,
  { nullable = [], ...options }: IntegerOptions & { nullable?: string[] } = {}): T {
  if (record === undefined || record === null) return record;
  const normalized: Record<string, unknown> = { ...record };
  for (const field of fields)
    if (normalized[field] !== undefined && !(normalized[field] === null && nullable.includes(field)))
      normalized[field] = safeInteger(normalized[field], error, options);
  return normalized as T;
}

type Envelope = (c: Db, actor: Actor, verb: string, args: object, fn: () => Promise<unknown>) => Promise<unknown>;

export function normalizedEnvelope(withEnvelope: Envelope, normalize: (result: unknown) => unknown) {
  return async (c: Db, actor: Actor, verb: string, args: object, fn: () => Promise<unknown>) => normalize(await withEnvelope(
    c, actor, verb, args, async () => normalize(await fn())));
}
