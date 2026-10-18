import { ToolError } from "./tool-error.js";

export function safeInteger(value, error, { allowString = true } = {}) {
  const normalized = allowString && typeof value === "string" && /^[1-9][0-9]*$/.test(value)
    ? Number(value) : value;
  if (!Number.isSafeInteger(normalized) || normalized < 1)
    throw new ToolError({ error });
  return normalized;
}

export function integerFields(record, fields, error, { nullable = [] } = {}) {
  if (record === undefined || record === null) return record;
  const normalized = { ...record };
  for (const field of fields)
    if (normalized[field] !== undefined && !(normalized[field] === null && nullable.includes(field)))
      normalized[field] = safeInteger(normalized[field], error);
  return normalized;
}

export function normalizedEnvelope(withEnvelope, normalize) {
  return async (c, actor, verb, args, fn) => normalize(await withEnvelope(
    c, actor, verb, args, async () => normalize(await fn())));
}
