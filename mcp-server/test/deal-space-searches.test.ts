// Exercise saved briefs through registered verbs on disposable PostgreSQL.
import assert from "node:assert/strict";
import { randomUUID } from "node:crypto";
import { readdirSync, readFileSync, writeFileSync } from "node:fs";
import test from "node:test";
import { Validator } from "@cfworker/json-schema";
import pg from "pg";
import { publicError } from "../src/public-errors.ts";
import { dealSpaceSearchContracts } from "../src/deal-space-searches.ts";

const { authenticatedIdentity, agentActorForToken } = await import(new URL("../src/identity.js", import.meta.url).href);

import { acquireDisposablePostgres } from "./helpers/disposable-postgres.ts";

const { executeRegisteredTool, TOOLS } = await import(new URL("../src/tools.js", import.meta.url).href);
const { setWriterActorContext } = await import(new URL("../src/mcp.js", import.meta.url).href);
const { installTourDealAccess } = await import(new URL("../src/tour-deal-access.js", import.meta.url).href);

const filters = {
  query: null,
  counties: ["Escambia"],
  property_types: [],
  min_square_feet: 2000,
  max_square_feet: 8000,
  availability: [],
  entrance_verified: null,
  public_projection_ready: null,
  photos_available: null,
  sort: "updated_desc",
  cursor: null,
  limit: 100,
};

test("recorded saved-search responses obey the exported consumer contract", () => {
  const contracts = JSON.parse(
    readFileSync(new URL("../contracts/deal-space-searches.v1.json", import.meta.url), "utf8"),
  );
  assert.deepEqual(
    contracts,
    dealSpaceSearchContracts(TOOLS["save-deal-space-search-v1"].inputSchema.properties.criteria),
  );
  const fixture: unknown = JSON.parse(
    readFileSync(new URL("./fixtures/deal-space-searches.json", import.meta.url), "utf8"),
  );
  assert.ok(fixture && typeof fixture === "object");
  for (const [name, response] of Object.entries(fixture)) {
    const validator = new Validator(
      structuredClone(name === "reopened" ? contracts.list : contracts.one),
      "2020-12",
      false,
    );
    assert.equal(validator.validate(response).valid, true, name);
    assert.equal(validator.validate({ ok: true }).valid, false);
  }
});

test("saved search lifecycle, canonical run, reopen, conflict, archive and authority", async (t) => {
  const fixture = await acquireDisposablePostgres({ prefix: "deal-space-search-", snapshot: true, baseline: true });
  const c = new pg.Client({ connectionString: fixture.dsn });
  try {
    await c.connect();
    const applied = new Set((await c.query("select filename from schema_migrations")).rows.map((row) => row.filename));
    for (const name of readdirSync(new URL("../../migrations/", import.meta.url))
      .filter((name) => name.endsWith(".sql"))
      .sort()) {
      if (!applied.has(name)) await c.query(readFileSync(new URL(`../../migrations/${name}`, import.meta.url), "utf8"));
    }
    const actor = (
      await c.query(`insert into actor(slug,kind,display_name) values('joe','human','Synthetic partner')
      on conflict(slug) do update set active=true returning id`)
    ).rows[0];
    Object.assign(actor, { slug: "joe", human: true });
    const party = (
      await c.query(
        `insert into party(kind,name,created_by,updated_by) values('person','Synthetic client',$1,$1) returning id`,
        [actor.id],
      )
    ).rows[0].id;
    const client = (
      await c.query("insert into client(party_id,created_by,updated_by) values($1,$2,$2) returning id", [
        party,
        actor.id,
      ])
    ).rows[0].id;
    const deal = (
      await c.query(
        `insert into deal(client_id,name,deal_type,phase,owner,created_by,updated_by)
      values($1,'Synthetic search','other','legal','joe',$2,$2) returning id`,
        [client, actor.id],
      )
    ).rows[0].id;
    const invoke = async (verb: string, args: object, who = actor) => {
      await c.query("begin");
      try {
        await setWriterActorContext(c, who, { partnerAuthorityAct: true });
        await c.query(TOOLS[verb].authorityOnly ? "set local role carr_authority" : "set local role carr_writer");
        await installTourDealAccess(c, who);
        const value = await executeRegisteredTool(c, who, verb, args);
        await c.query("commit");
        return value;
      } catch (e) {
        await c.query("rollback");
        throw e;
      }
    };
    for (const owner of ["joe", "dell", null]) {
      await t.test(`${owner ?? "ownerless"} deal supports partner saved-search read, save and run`, async () => {
        const target = (
          await c.query(
            `insert into deal(client_id,name,deal_type,phase,owner,created_by,updated_by)
           values($1,'Synthetic shared search','other','pending',$2,$3,$3) returning id`,
            [client, owner, actor.id],
          )
        ).rows[0].id;
        const args = {
          deal_id: target,
          search_id: randomUUID(),
          expected_version: 0,
          idempotency_key: randomUUID(),
          name: "Shared partner search",
          status: "saved",
          criteria: filters,
          requirements: "Shared deal",
          client_prefill_source: null,
        };
        await invoke("save-deal-space-search-v1", args);
        for (const slug of ["joe", "dell", "joe-local", "dell-local", "codex", "claude"]) {
          const human = slug === "joe" || slug === "dell";
          const row = (
            await c.query(
              `insert into actor(slug,kind,display_name) values($1,$2,'Synthetic partner access')
             on conflict(slug) do update set active=true returning id`,
              [slug, human ? "human" : "automation"],
            )
          ).rows[0];
          const who = human
            ? { ...row, slug, human }
            : slug.endsWith("-local")
              ? agentActorForToken(
                  "Bearer synthetic-search",
                  JSON.stringify({ [slug]: "synthetic-search" }),
                  "local-token",
                )
              : authenticatedIdentity.connectionForGrant(
                  { slug, human: false, client_id: "synthetic-search-client", sponsoring_human_slug: "joe" },
                  JSON.stringify({ "synthetic-search-client": slug }),
                );
          Object.assign(who, { id: row.id });
          const saved = await invoke(
            "save-deal-space-search-v1",
            {
              ...args,
              search_id: randomUUID(),
              idempotency_key: randomUUID(),
            },
            who,
          );
          const ran = await invoke(
            "run-deal-space-search-v1",
            {
              deal_id: target,
              search_id: saved.search.id,
              expected_version: 1,
              idempotency_key: randomUUID(),
            },
            who,
          );
          assert.equal(ran.search.version, 2, slug);
          const read = await invoke(
            "read-deal-space-searches-v1",
            { deal_id: target, search_id: saved.search.id },
            who,
          );
          assert.deepEqual(read.searches, [ran.search], slug);
        }
        for (const verb of ["read-deal-space-searches-v1", "save-deal-space-search-v1", "run-deal-space-search-v1"]) {
          const input = verb.startsWith("read-") ? { deal_id: target, search_id: null } : args;
          await assert.rejects(invoke(verb, input, { id: actor.id, slug: "untrusted", human: false }), /human_only/);
        }
        for (const change of [
          "select set_config('carr.organization_tenant_id','wrong-tenant',true)",
          "select set_config('carr.acting_actor_slug','dell',true)",
          "select set_config('carr.verified_human_actor_slug','dell',true)",
          "update actor set active=false where slug='joe'",
          "update deal set version=version+1 where id=$1",
          "update client set merged_into=(select client_id from deal where id=$1) where id=(select client_id from deal where id=$1)",
          "update party set deleted_at=now() where id=(select c.party_id from client c join deal d on d.client_id=c.id where d.id=$1)",
        ]) {
          for (const sql of [
            "select ops.read_deal_space_searches($1,$2,$3,null)",
            "select ops.save_deal_space_search($1,$2,$3::jsonb)",
            "select ops.run_deal_space_search($1,$2,$3,$4,1)",
          ]) {
            await c.query("begin");
            try {
              await setWriterActorContext(c, actor, { partnerAuthorityAct: true });
              await c.query("set local role carr_authority");
              await executeRegisteredTool(c, actor, "read-deal-space-searches-v1", {
                deal_id: target,
                search_id: null,
              });
              await c.query("reset role");
              await c.query(change, change.includes("$1") ? [target] : []);
              await c.query("set local role carr_authority");
              // Each SQL entry point must enforce the bound decision even after TypeScript authorization.
              const params = sql.includes("save_")
                ? [JSON.stringify(args)]
                : sql.includes("run_")
                  ? [target, args.search_id]
                  : [target];
              await assert.rejects(
                c.query(sql, ["carr-internal", actor.id, ...params]),
                /space_search_unavailable/,
                change,
              );
            } finally {
              await c.query("rollback");
            }
          }
        }
      });
    }
    const search_id = randomUUID();
    const save = {
      idempotency_key: randomUUID(),
      deal_id: deal,
      search_id,
      expected_version: 0,
      name: "Medical space",
      status: "draft",
      criteria: filters,
      requirements: "Lease, budget $30/SF/year NNN, occupy next summer. Parking and ground floor required.",
      client_prefill_source: {
        client_id: client,
        source_ref: "client-intake:synthetic",
        observed_at: "2026-11-10T00:00:00Z",
      },
    };
    const concurrentSearch = randomUUID();
    const outcomes = [];
    for (const [phase, verb, args] of [
      ["create", "save-deal-space-search-v1", { ...save, search_id: concurrentSearch, status: "saved" }],
      [
        "update",
        "save-deal-space-search-v1",
        { ...save, search_id: concurrentSearch, expected_version: 1, status: "saved" },
      ],
      ["run", "run-deal-space-search-v1", { deal_id: deal, search_id: concurrentSearch, expected_version: 2 }],
    ] as const) {
      const request = { ...args, idempotency_key: randomUUID() };
      let arrivals = 0;
      let release!: () => void;
      const barrier = new Promise<void>((resolve) => {
        release = resolve;
      });
      // Correct serialization permits only one receipt read until the first transaction commits.
      const timeout = setTimeout(() => release(), 1000);
      const concurrent = async () => {
        const connection = new pg.Client({ connectionString: fixture.dsn });
        await connection.connect();
        const query = connection.query.bind(connection);
        connection.query = (async (text: string, values?: unknown[]) => {
          const result = await query(text, values);
          if (text.startsWith("select request_hash, response from tool_call")) {
            if (++arrivals === 2) release();
            await barrier;
          }
          return result;
        }) as typeof connection.query;
        try {
          await connection.query("begin");
          await setWriterActorContext(connection, actor, { partnerAuthorityAct: true });
          await connection.query("set local role carr_authority");
          await installTourDealAccess(connection, actor);
          const response = await executeRegisteredTool(connection, actor, verb, request);
          await connection.query("commit");
          return response;
        } catch (error) {
          await connection.query("rollback");
          throw error;
        } finally {
          await connection.end();
        }
      };
      try {
        const responses = await Promise.allSettled([concurrent(), concurrent()]);
        const row = (await c.query("select version from ops.deal_space_search where id=$1", [concurrentSearch])).rows;
        const receipts = (
          await c.query("select count(*)::int as count from tool_call where idempotency_key=$1", [
            request.idempotency_key,
          ])
        ).rows[0].count;
        outcomes.push({ phase, responses, row, receipts });
      } finally {
        clearTimeout(timeout);
      }
    }
    for (const [index, { phase, responses, row, receipts }] of outcomes.entries()) {
      await t.test(`overlapping same-key ${phase} replays one mutation`, () => {
        assert.equal(
          responses.filter((result) => result.status === "fulfilled").length,
          2,
          `${phase}: both overlapping same-key calls must succeed: ${JSON.stringify(responses)}`,
        );
        const [first, second] = responses.map((result) => (result.status === "fulfilled" ? result.value : null));
        assert.deepEqual(first.search, second.search, `${phase}: identical committed result`);
        assert.equal([first, second].filter((result) => result.replayed === true).length, 1, `${phase}: one replay`);
        assert.deepEqual(row, [{ version: index + 1 }], `${phase}: one mutation`);
        assert.equal(receipts, 1, `${phase}: one receipt`);
      });
    }
    await c.query("delete from ops.deal_space_search where id=$1", [concurrentSearch]);
    const created = await invoke("save-deal-space-search-v1", save);
    assert.equal(created.search.state, "draft");
    assert.equal(created.search.version, 1);
    const replay = await invoke("save-deal-space-search-v1", save);
    assert.equal(replay.replayed, true);
    assert.deepEqual(replay.search, created.search, "idempotent create");
    const saved = await invoke("save-deal-space-search-v1", {
      ...save,
      idempotency_key: randomUUID(),
      expected_version: 1,
      status: "saved",
    });
    assert.equal(saved.search.state, "saved");
    assert.equal(saved.search.last_run_at, null);
    const run = await invoke("run-deal-space-search-v1", {
      idempotency_key: randomUUID(),
      deal_id: deal,
      search_id,
      expected_version: 2,
    });
    assert.equal(run.search.version, 3);
    assert.ok(run.search.last_run_at);
    assert.deepEqual(run.search.result_property_ids, []);
    assert.equal(run.search.has_more, false);
    const reopened = await invoke("read-deal-space-searches-v1", { deal_id: deal, search_id });
    assert.deepEqual(reopened.searches, [run.search]);
    assert.equal((await invoke("read-deal-space-searches-v1", { deal_id: deal, search_id: null })).searches.length, 1);
    for (const [criteria, code] of [
      ...["", "   ", "medical\u0000office"].map((query) => [
        { ...filters, query },
        "tour_input_invalid",
      ] as const),
      [{ ...filters, min_square_feet: 9000, max_square_feet: 8000 }, "tour_search_range_invalid"],
    ] as const)
      await assert.rejects(
        invoke("save-deal-space-search-v1", {
          ...save,
          idempotency_key: randomUUID(),
          search_id: randomUUID(),
          criteria,
        }),
        (error: unknown) => {
          const result = publicError(error);
          assert.equal(result.error, code);
          assert.match(result.correlation_id, /^[0-9a-f-]{36}$/);
          assert.deepEqual(Object.keys(result).sort(), ["correlation_id", "error", "hint"]);
          return true;
        },
      );
    const normalized = await invoke("save-deal-space-search-v1", {
      ...save,
      idempotency_key: randomUUID(),
      search_id: randomUUID(),
      criteria: { ...filters, counties: [], query: "  medical office  " },
    });
    assert.equal(normalized.search.criteria.query, "medical office");
    assert.deepEqual(normalized.search.criteria.counties, ["Escambia", "Santa Rosa", "Okaloosa", "Walton", "Bay"]);
    await assert.rejects(
      invoke("save-deal-space-search-v1", {
        ...save,
        idempotency_key: randomUUID(),
        search_id: randomUUID(),
        client_prefill_source: { ...save.client_prefill_source, client_id: randomUUID() },
      }),
      /space_search_prefill_mismatch/,
    );
    await assert.rejects(
      invoke("save-deal-space-search-v1", { ...save, idempotency_key: randomUUID(), expected_version: 1 }),
      /space_search_version_conflict/,
    );
    const stale = await invoke("save-deal-space-search-v1", {
      ...save,
      idempotency_key: randomUUID(),
      expected_version: 3,
      status: "saved",
      criteria: { ...filters, min_square_feet: 3000 },
    });
    assert.equal(stale.search.state, "stale");
    assert.equal(stale.search.last_run_at, run.search.last_run_at);
    assert.equal(
      (await invoke("read-deal-space-searches-v1", { deal_id: deal, search_id })).searches[0].state,
      "stale",
    );
    await c.query(`create function ops.fail_saved_search_run() returns trigger language plpgsql as $$begin raise exception 'synthetic_run_failure'; end$$;
      create trigger fail_saved_search_run before update on ops.deal_space_search for each row when(new.last_run_at is distinct from old.last_run_at) execute function ops.fail_saved_search_run()`);
    await assert.rejects(
      invoke("run-deal-space-search-v1", {
        idempotency_key: randomUUID(),
        deal_id: deal,
        search_id,
        expected_version: 4,
      }),
      /synthetic_run_failure/,
    );
    assert.deepEqual(
      (await invoke("read-deal-space-searches-v1", { deal_id: deal, search_id })).searches[0],
      stale.search,
    );
    await c.query("drop trigger fail_saved_search_run on ops.deal_space_search");
    const archived = await invoke("save-deal-space-search-v1", {
      ...save,
      idempotency_key: randomUUID(),
      expected_version: 4,
      status: "archived",
    });
    assert.equal(archived.search.state, "archived");
    await assert.rejects(
      invoke("run-deal-space-search-v1", {
        idempotency_key: randomUUID(),
        deal_id: deal,
        search_id,
        expected_version: 5,
      }),
      /space_search_not_runnable/,
    );
    const restored = await invoke("save-deal-space-search-v1", {
      ...save,
      idempotency_key: randomUUID(),
      expected_version: 5,
      status: "saved",
    });
    assert.equal(restored.search.state, "saved");
    await c.query("update ops.deal_space_search set last_run_at=now()-interval '25 hours' where id=$1", [search_id]);
    assert.equal(
      (await invoke("read-deal-space-searches-v1", { deal_id: deal, search_id })).searches[0].state,
      "stale",
    );
    const propertySql = readFileSync(new URL("./tour-delivery-data-plane-postgres.sql", import.meta.url), "utf8")
      .split("insert into ops.tour(id,")
      .at(0)
      ?.replace("\\set ON_ERROR_STOP on", "")
      .replace("begin;", "")
      .replaceAll("tour-delivery-proof", "carr-internal");
    assert.ok(propertySql);
    await c.query(propertySql);
    const positive = await invoke("run-deal-space-search-v1", {
      idempotency_key: randomUUID(),
      deal_id: deal,
      search_id,
      expected_version: 6,
    });
    assert.deepEqual(positive.search.result_property_ids, ["a1000000-0000-4000-8000-000000000001"]);
    assert.equal(positive.search.state, "saved");
    await c.query(
      "create trigger fail_saved_search_run before update on ops.deal_space_search for each row when(new.last_run_at is distinct from old.last_run_at) execute function ops.fail_saved_search_run()",
    );
    await assert.rejects(
      invoke("run-deal-space-search-v1", {
        idempotency_key: randomUUID(),
        deal_id: deal,
        search_id,
        expected_version: 7,
      }),
      /synthetic_run_failure/,
    );
    assert.deepEqual(
      (await invoke("read-deal-space-searches-v1", { deal_id: deal, search_id })).searches[0],
      positive.search,
    );
    await c.query(
      "drop trigger fail_saved_search_run on ops.deal_space_search; drop function ops.fail_saved_search_run()",
    );
    for (const status of ["draft", "saved", "archived"]) {
      const other = { ...save, idempotency_key: randomUUID(), search_id: randomUUID(), status };
      const initial = await invoke("save-deal-space-search-v1", other);
      assert.equal(initial.search.state, status);
      const moved = await invoke("save-deal-space-search-v1", {
        ...other,
        idempotency_key: randomUUID(),
        expected_version: 1,
        status: "archived",
      });
      assert.equal(moved.search.state, "archived");
    }
    const point = { latitude: 30.4, longitude: -87.2, source_ref: "proof:entrance", precision_class: "entrance" };
    const tour = await invoke("create-tour-domain", {
      idempotency_key: randomUUID(),
      tour_name: "Saved search handoff",
      subject_type: "deal",
      subject_id: deal,
      canonical_dataset_version: "synthetic",
      start_point: { ...point, position_role: "start" },
      end_point: { ...point, position_role: "end" },
    });
    const cart = await invoke("append-tour-selection-cart-version", {
      idempotency_key: randomUUID(),
      tour_id: tour.tour_id,
      base_selection_version_id: null,
      property_ids: positive.search.result_property_ids,
      expected_selection_version: 0,
      selection_digest: `sha256:${"a".repeat(64)}`,
    });
    assert.equal(cart.selected_count, 1);
    for (const name of [
      "save-deal-space-search-v1",
      "run-deal-space-search-v1",
      "read-deal-space-searches-v1",
    ] as const) {
      assert.equal(TOOLS[name].authorityOnly, true);
      assert.equal(TOOLS[name].humanOnly, true);
      await assert.rejects(invoke(name, {}, { id: randomUUID(), slug: "unauthorized", human: false }), /human_only/);
    }
    await c.query("update party set deleted_at=now() where id=$1", [party]);
    await assert.rejects(
      invoke("read-deal-space-searches-v1", { deal_id: deal, search_id }),
      /space_search_unavailable/,
    );
    await assert.rejects(invoke("save-deal-space-search-v1", save), /space_search_unavailable/);
    if (process.env.RECORD_SPACE_SEARCH_FIXTURE)
      writeFileSync(
        process.env.RECORD_SPACE_SEARCH_FIXTURE,
        JSON.stringify({ created, saved, run, reopened, stale, archived, restored, positive }, null, 2) + "\n",
      );
  } finally {
    try {
      await c.end();
    } finally {
      await fixture.close();
    }
  }
});
