import assert from "node:assert/strict";
// Verify canonical Tour deal decisions and installed HTTP access boundaries.
import test from "node:test";
import { assertDisposableDatabase } from "./helpers/disposable-database-url.ts";

const installTourDealAccess = await legacyExport("../src/tour-deal-access.js", "installTourDealAccess");
const evaluateActorAuthority = await legacyExport("../src/global-boundaries.v5.js", "evaluateActorAuthority");

for (const slug of ["joe", "dell"])
  test(`Tour decisions reuse ordinary deal access for ${slug}`, async () => {
    const actor = { slug, human: true, id: `synthetic-${slug}` };
    const rows = [
      { id: "one", owner: "joe", version: 1 },
      { id: "two", owner: "dell", version: 2 },
      { id: "unowned", owner: null, version: 1 },
    ];
    let context: { slug?: string; id?: string; deals: Record<string, unknown> } = { deals: {} };
    const client = {
      query: async (sql: string, params?: string[]) => {
        if (sql.startsWith("select set_config")) {
          context = JSON.parse(params?.[0] ?? "null");
          return { rows: [] };
        }
        assert.equal(params, undefined);
        return { rows };
      },
    };
    await installTourDealAccess(client, actor);
    assert.equal(context.slug, slug);
    assert.equal(context.id, actor.id);
    for (const deal of rows) {
      const allowed =
        deal.owner &&
        evaluateActorAuthority({
          actor,
          action: "business.read_deal",
          tenant: "carr-internal",
          now: "2026-11-03T16:06:17Z",
          controls: { deal_owner_slug: deal.owner, policy_scope: ["business.read_deal"], capabilities: ["deal.read"] },
        }).decision === "allow";
      assert.equal(Object.hasOwn(context.deals, deal.id), Boolean(allowed));
    }
  });

test("an active non-partner receives no deal decisions", async () => {
  let context: { slug?: string; id?: string; deals: Record<string, unknown> } = { deals: {} };
  await installTourDealAccess(
    {
      query: async (sql: string, params?: string[]) => {
        if (sql.startsWith("select set_config")) {
          context = JSON.parse(params?.[0] ?? "null");
          return { rows: [] };
        }
        return { rows: [{ id: "private", owner: "joe", version: 1 }] };
      },
    },
    { slug: "codex", human: false },
  );
  assert.deepEqual(context.deals, {});
});

const databaseUrl = process.env.CARR_TOUR_DEAL_TEST_DATABASE_URL;
test("installed HTTP create, detail, cart and cross-deal library enforce canonical deal access", {
  skip: !databaseUrl,
}, async () => {
  assert.ok(databaseUrl);
  assertDisposableDatabase(databaseUrl);
  const { default: pg } = await import("pg");
  const { Client } = pg;
  const { createTourInternalWebHandler } = await import("../src/tour-internal-web.ts");
  const tourDomainTools = await legacyExport("../src/tour-domain.js", "tourDomainTools");
  const createTourRuntimeAdapters = await legacyExport("../src/tour-runtime.js", "createTourRuntimeAdapters");
  const { ToolError } = await import("../src/tool-error.ts");
  const c = new Client({ connectionString: databaseUrl });
  await c.connect();
  try {
    await c.query("begin");
    const actor: { slug: string; human: boolean; id?: string } = { slug: "joe", human: true };
    actor.id = (
      await c.query(`insert into public.actor(slug,kind,display_name) values('joe','human','Synthetic Owner')
      on conflict(slug) do update set active=true returning id`)
    ).rows[0].id;
    await c.query(`insert into public.actor(slug,kind,display_name) values('dell','human','Synthetic Other')
      on conflict(slug) do update set active=true`);
    const cases: { kind: string; d: string; t: string }[] = [];
    for (const kind of ["accessible", "inaccessible", "deleted", "unknown"]) {
      const p = (
        await c.query(
          `insert into public.party(kind,name,created_by,updated_by)
        values('person',$1,$2,$2) returning id`,
          [`Synthetic ${kind}`, actor.id],
        )
      ).rows[0].id;
      const cl = (
        await c.query(`insert into public.client(party_id,created_by,updated_by) values($1,$2,$2) returning id`, [
          p,
          actor.id,
        ])
      ).rows[0].id;
      const d = (
        await c.query(
          `insert into public.deal(client_id,name,deal_type,phase,owner,created_by,updated_by)
        values($1,$2,'other','legal','joe',$3,$3) returning id`,
          [cl, `Synthetic ${kind}`, actor.id],
        )
      ).rows[0].id;
      if (kind === "unknown") {
        await c.query("delete from public.deal where id=$1", [d]);
        cases.push({ kind, d, t: crypto.randomUUID() });
        continue;
      }
      await c.query("set local session authorization carr_writer");
      await c.query("select set_config('carr.acting_actor_slug','joe',true)");
      await installTourDealAccess(c, actor);
      const t = (
        await c.query("select ops.create_tour_domain('carr-internal',$1,'deal',$2,'proof','{}','{}') id", [
          `HTTP ${kind}`,
          d,
        ])
      ).rows[0].id;
      await c.query("select ops.append_tour_selection_cart_version('carr-internal',$1,null,'[]',0,$2)", [
        t,
        `sha256:${"a".repeat(64)}`,
      ]);
      await c.query("reset session authorization");
      if (kind === "inaccessible") await c.query("update public.deal set owner='dell' where id=$1", [d]);
      if (kind === "deleted") await c.query("update public.party set deleted_at=now() where id=$1", [p]);
      cases.push({ kind, d, t: kind === "unknown" ? crypto.randomUUID() : t });
    }
    const tools = tourDomainTools({
      ToolError,
      withEnvelope: async (_c: unknown, _a: unknown, _v: unknown, _x: unknown, fn: () => Promise<unknown>) => fn(),
      writeEvent: async () => {},
    });
    async function read(sql: string, params: unknown[]) {
      await installTourDealAccess(c, actor);
      return (await c.query(sql, [...params, actor.id])).rows[0]?.data;
    }
    const runtime = createTourRuntimeAdapters({
      internalReadFn: (_context: unknown, sql: string, params: unknown[]) => read(sql, params),
    });
    const handler = createTourInternalWebHandler({
      createTourFn: async ({ input }) => {
        await installTourDealAccess(c, actor);
        const result = await tools["create-tour-domain"].handler(c, actor, input);
        return { ok: true, data: result };
      },
      listToursFn: runtime.listToursFn,
      readTourFn: runtime.readTourFn,
      readTourSelectionCartFn: async ({ input }) => ({
        ok: true,
        data: await read("select ops.read_tour_selection_cart($1,$2,$3) data", ["carr-internal", input?.tour_id]),
      }),
    });
    const origin = "https://app.doctorcre.com";
    const session = { key: "synthetic-session", csrfToken: "synthetic-csrf" };
    const fetch = async (request: Request) => {
      await c.query("savepoint http_request");
      try {
        return await handler.fetch(request, { APP_HOST: "app.doctorcre.com" }, {}, actor, session);
      } finally {
        await c.query("rollback to savepoint http_request");
        await c.query("release savepoint http_request");
      }
    };
    await c.query("set local session authorization carr_writer");
    await c.query("select set_config('carr.acting_actor_slug','joe',true)");
    let unknownDetail: number | undefined, unknownCart: number | undefined;
    for (const fixture of cases.toReversed()) {
      const detail = await fetch(new Request(`${origin}/api/tours/detail?tour_id=${fixture.t}`));
      const cart = await fetch(new Request(`${origin}/api/tours/selection-cart?tour_id=${fixture.t}`));
      if (fixture.kind === "unknown") {
        unknownDetail = detail.status;
        unknownCart = cart.status;
      }
      if (fixture.kind === "accessible") {
        assert.equal(detail.status, 200);
        assert.equal(cart.status, 200);
      } else {
        assert.equal(detail.status, unknownDetail, fixture.kind);
        assert.equal(cart.status, unknownCart, fixture.kind);
      }
      const point = (role: string) => ({
        latitude: 30,
        longitude: -87,
        position_role: role,
        precision_class: "approximate",
        source_ref: "fixture:point",
      });
      // An unknown deal UUID, rather than an unknown Tour UUID, exercises creation.
      const create = await fetch(
        new Request(`${origin}/api/tours/create`, {
          method: "POST",
          headers: {
            origin,
            "sec-fetch-site": "same-origin",
            "content-type": "application/json",
            "x-carr-csrf": session.csrfToken,
          },
          body: JSON.stringify({
            idempotency_key: crypto.randomUUID(),
            tour_name: "HTTP creation",
            subject_type: "deal",
            subject_id: fixture.kind === "unknown" ? crypto.randomUUID() : fixture.d,
            canonical_dataset_version: "proof",
            start_point: point("start"),
            end_point: point("end"),
          }),
        }),
      );
      assert.equal(create.status, fixture.kind === "accessible" ? 200 : 400, fixture.kind);
    }
    const library = await fetch(new Request(`${origin}/api/tours/library`));
    assert.equal(library.status, 200);
    const tours = (await library.json()).data.tours.filter((t: { tour_name: string; deal_id: string }) =>
      t.tour_name.startsWith("HTTP "),
    );
    assert(tours.length >= 1);
    assert(tours.every((t: { deal_id: string }) => t.deal_id === cases[0]?.d));
    await c.query("reset session authorization");
    await c.query("update public.actor set active=false where slug='joe'");
    await c.query("set local session authorization carr_writer");
    await installTourDealAccess(c, actor);
    assert.equal(
      await read("select ops.read_tour_internal_detail($1,$2,$3) data", ["carr-internal", cases[0]?.t]),
      null,
      "an inactive actor cannot consume newly installed decisions",
    );
    const inactiveLibrary = await read("select ops.list_tour_library($1,$2) data", ["carr-internal"]);
    assert.equal(
      inactiveLibrary.tours.some((t: { deal_id: string }) => t.deal_id === cases[0]?.d),
      false,
    );
  } finally {
    await c.query("rollback");
    await c.end();
  }
});

test("verified sponsored native agents reuse their existing authority subject without crossing deals", async () => {
  let context: { slug?: string; id?: string; deals: Record<string, unknown> } = { deals: {} };
  const actor = { slug: "codex", human: false, native_agent_verified: true, sponsoring_human_slug: "joe" };
  await installTourDealAccess(
    {
      query: async (sql: string, params?: string[]) => {
        if (sql.startsWith("select set_config")) {
          context = JSON.parse(params?.[0] ?? "null");
          return { rows: [] };
        }
        return {
          rows: [
            { id: "mine", owner: "joe", version: 1 },
            { id: "other", owner: "dell", version: 1 },
          ],
        };
      },
    },
    actor,
  );
  assert.equal(context.slug, "codex");
  assert.deepEqual(Object.keys(context.deals), ["mine"]);
});

async function legacyExport(path: string, name: string) {
  const loaded: unknown = await import(new URL(path, import.meta.url).href);
  assert.ok(loaded !== null && typeof loaded === "object" && name in loaded);
  const value: unknown = Reflect.get(loaded, name);
  assert.equal(typeof value, "function");
  assert.ok(typeof value === "function");
  return value;
}
