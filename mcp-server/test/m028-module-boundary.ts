import assert from "node:assert/strict";

type MailClient = { query: (sql: string, values: unknown[]) => Promise<{ rows: unknown[] }> };
type MailResponse = { state: "ready" | "empty" | "unavailable"; items: unknown[]; next_cursor: string | null };

type MailActor = { id?: string; slug?: string; human?: boolean; sponsoring_human_slug?: string };

function callable(value: unknown): (...args: unknown[]) => unknown {
  assert.ok(typeof value === "function");
  return (...args: unknown[]): unknown => Reflect.apply(value, undefined, args);
}

export function parseDerivedKeyModule(value: unknown) {
  assert.ok(value && typeof value === "object" && "derivedIdempotencyKey" in value);
  const derive = callable(value.derivedIdempotencyKey);
  return (namespace: string, input: string): string => {
    const result = derive(namespace, input);
    assert.ok(typeof result === "string");
    return result;
  };
}

export function parseMailModule(value: unknown) {
  assert.ok(value && typeof value === "object");
  assert.ok(
    "readMailNeedsPartner" in value &&
      "parseMailNeedsPartnerQuery" in value &&
      "parseMailNeedsPartnerResponse" in value,
  );
  const read = callable(value.readMailNeedsPartner);
  const query = callable(value.parseMailNeedsPartnerQuery);
  const response = callable(value.parseMailNeedsPartnerResponse);
  function parseMailNeedsPartnerResponse(input: unknown): MailResponse {
    const result = response(input);
    assert.ok(
      result && typeof result === "object" && "state" in result && "items" in result && "next_cursor" in result,
    );
    assert.ok(result.state === "ready" || result.state === "empty" || result.state === "unavailable");
    const items: unknown = result.items;
    assert.ok(Array.isArray(items));
    const checkedItems: unknown[] = items;
    assert.ok(typeof result.next_cursor === "string" || result.next_cursor === null);
    return { ...result, state: result.state, items: checkedItems, next_cursor: result.next_cursor };
  }
  return {
    parseMailNeedsPartnerResponse,
    parseMailNeedsPartnerQuery(input: unknown) {
      const result = query(input);
      assert.ok(result && typeof result === "object" && "limit" in result && "cursor" in result);
      assert.ok(typeof result.limit === "number");
      assert.ok(typeof result.cursor === "string" || result.cursor === null);
      return { limit: result.limit, cursor: result.cursor };
    },
    async readMailNeedsPartner(client: MailClient, actor: MailActor, input: Record<string, unknown>) {
      const result: unknown = await read(client, actor, input);
      return parseMailNeedsPartnerResponse(result);
    },
  };
}
