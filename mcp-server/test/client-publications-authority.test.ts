import assert from "node:assert/strict";
import { randomUUID } from "node:crypto";
import test from "node:test";

const { executeRegisteredTool } = await import(new URL("../src/tools.js", import.meta.url).href);

for (const sponsor of ["joe", "dell"]) {
  for (const published of [true, false]) {
    test(`verified ${sponsor} agent reaches publication SQL for published=${published}`, async () => {
      const actor = {
        id: randomUUID(),
        slug: "codex",
        human: false,
        native_agent_verified: true,
        sponsoring_human_slug: sponsor,
        via: "local-token",
      };
      const queries: unknown[][] = [];
      const db = {
        query: async (sql: string, params: unknown[]) => {
          queries.push(params);
          if (sql.includes("select d.owner")) return { rows: [{ owner: sponsor }] };
          throw new Error("publication_sql_reached");
        },
      };
      await assert.rejects(
        executeRegisteredTool(db, actor, "set-client-publication", {
          idempotency_key: randomUUID(),
          deal_id: randomUUID(),
          publication_id: randomUUID(),
          base_version: 0,
          published,
          kind: "milestone",
          title: "Publish",
          content: "Text",
        }),
        /publication_sql_reached/,
      );
      assert.equal(queries[0]?.[1], sponsor);
    });
  }
}
for (const actor of [
  { slug: "codex", human: false, sponsoring_human_slug: "joe" },
  { slug: "codex", human: false, native_agent_verified: true, sponsoring_human_slug: "outsider" },
  { slug: "client", human: true },
]) {
  test(`refuses publication by ${JSON.stringify(actor)}`, async () => {
    let queries = 0;
    await assert.rejects(
      executeRegisteredTool(
        {
          query: async () => {
            queries++;
            throw Error("SQL reached");
          },
        },
        { id: randomUUID(), ...actor },
        "set-client-publication",
        {
          idempotency_key: randomUUID(),
          deal_id: randomUUID(),
          publication_id: randomUUID(),
          base_version: 0,
          published: true,
          kind: "milestone",
          title: "Publish",
          content: "Text",
        },
      ),
    );
    assert.equal(queries, 0);
  });
}
