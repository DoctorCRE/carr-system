// Tests lead and client merge safeguards and evidence.

import assert from "node:assert/strict";
import { test } from "node:test";

function object(value: unknown): Record<string, unknown> {
  assert.ok(value !== null && typeof value === "object" && !Array.isArray(value));
  return Object.fromEntries(Object.entries(value));
}

// The legacy registry is parsed at its runtime seam until its own conversion.
const registry = object(await import(new URL("../src/tools.js", import.meta.url).href));
const TOOLS = object(registry.TOOLS);

const ids = {
  joe: "10000000-0000-0000-0000-000000000002",
  leadSide: "20000000-0000-0000-0000-00000000000a",
  clientSide: "20000000-0000-0000-0000-00000000000b",
};
const joe = { id: ids.joe, slug: "joe", display: "Joe", human: true, via: "mcp", client_id: "claude" };

type Metric = {
  has_business_ref: boolean;
  verified_identity_fields: number;
  linked_records: number;
  created_at: string;
};
class Fake {
  roles: Record<string, string[]>;
  metrics: Record<string, Metric>;
  updates: string[] = [];
  events: unknown[][] = [];
  assignmentCount = 0;
  constructor(roles: Record<string, string[]>, metrics: Record<string, Metric> | null = null) {
    this.roles = roles;
    this.metrics = metrics || {
      [ids.clientSide]: {
        has_business_ref: true,
        verified_identity_fields: 2,
        linked_records: 3,
        created_at: "2020-01-01",
      },
      [ids.leadSide]: {
        has_business_ref: false,
        verified_identity_fields: 1,
        linked_records: 1,
        created_at: "2021-01-01",
      },
    };
    this.updates = [];
    this.events = [];
  }

  async query(text: string, params: unknown[] = []) {
    const sql = text.replace(/\s+/g, " ").trim();

    if (sql.startsWith("select request_hash, response")) return { rows: [] };

    if (sql.includes("merge_live_endpoints"))
      return { rows: partyIds(params[0]).map((id) => ({ id, merged_into: null })) };

    if (sql.includes("v_ref_index")) return { rows: [] };

    if (sql.includes("role_kinds_for_party")) {
      const held = this.roles[String(params[0])] || [];
      return { rows: held.map((k) => ({ kind: k })) };
    }
    if (sql.includes("merge_survivorship")) {
      return { rows: partyIds(params[0]).map((id) => ({ id, ...this.metrics[id] })) };
    }
    if (sql.includes("merge_orphan_sweep"))
      return {
        rows: [
          { attachment: "party_link", count: 0 },
          { attachment: "vendor_assignment", count: this.assignmentCount || 0 },
        ],
      };

    if (
      sql.startsWith("update lead set party_id") ||
      sql.startsWith("update client set party_id") ||
      sql.startsWith("update vendor set party_id")
    ) {
      const table = sql.split(" ")[1];
      assert.ok(table);
      this.updates.push(table);
      return { rows: [{ id: "row" }] };
    }
    if (sql.startsWith("update party set merged_into")) {
      this.updates.push("party");
      return { rows: [] };
    }
    if (sql.includes("having count(*)>1")) return { rows: [] };

    if (sql.startsWith("insert into event")) {
      this.events.push(params);
      return { rows: [] };
    }
    return { rows: [] };
  }
}

function partyIds(value: unknown): string[] {
  assert.ok(Array.isArray(value));
  return value.map((id) => {
    assert.equal(typeof id, "string");
    return String(id);
  });
}
const detail = (e: unknown) => {
  const error = object(e);
  return JSON.stringify(error.payload ?? error.body ?? error.message ?? e);
};

const verb = object(TOOLS["confirm-merge"]);
const handler = verb.handler;
assert.ok(typeof handler === "function");

async function merge(fake: Fake, extra: Record<string, unknown> = {}) {
  assert.ok(typeof handler === "function");
  return object(
    await handler(fake, joe, {
      idempotency_key: crypto.randomUUID(),
      survivor_party: ids.clientSide,
      merged_party: ids.leadSide,
      match_basis: "exact email domain",
      ...extra,
    }),
  );
}

test("a lead row and a client row for one person are refused as a merge", async () => {
  const fake = new Fake({ [ids.leadSide]: ["lead"], [ids.clientSide]: ["client"] });
  await assert.rejects(
    () => merge(fake),
    (e) => {
      const body = detail(e);
      assert.match(body, /lead_client_pair/, "the refusal names the shape it caught");
      return true;
    },
  );
  assert.deepEqual(
    fake.updates,
    [],
    "NOTHING is written when the gate refuses — a merge that got half-applied is worse than one refused",
  );
});

test("the refusal explains the rule in Joe's terms, not as a code", async () => {
  const fake = new Fake({ [ids.leadSide]: ["lead"], [ids.clientSide]: ["client"] });
  await assert.rejects(
    () => merge(fake),
    (e) => {
      const body = detail(e).toLowerCase();
      assert.match(
        body,
        /everyone starts as a lead|starts as a lead/,
        "it quotes the ruling rather than making the reader look up an id",
      );
      assert.match(
        body,
        /same_person_because/,
        "and it names the way through, so the refusal is a closed route and not a closed problem",
      );
      return true;
    },
  );
});

test("the Whitfield case still merges when the human states the basis", async () => {
  const fake = new Fake({ [ids.leadSide]: ["lead"], [ids.clientSide]: ["client"] });
  const out = await merge(fake, {
    same_person_because:
      "Two party rows for one human — same NPI, same practice address, " +
      "confirmed against the intake record. Not a name match.",
  });
  assert.equal(out.ok, true, "the legitimate merge is not blocked");
  assert.ok(fake.updates.includes("party"), "and it actually runs");
});

test("the stated basis is recorded on the event, so the reason survives the merge", async () => {
  const fake = new Fake({ [ids.leadSide]: ["lead"], [ids.clientSide]: ["client"] });
  await merge(fake, { same_person_because: "same NPI and address, verified in the record" });
  const written = JSON.stringify(fake.events);
  assert.match(
    written,
    /same NPI and address/,
    "a merge is permanent; the reason it was allowed has to outlive the session that gave it",
  );
});

test("deterministic precedence refuses a human-selected loser with fewer cited facts", async () => {
  const fake = new Fake({ [ids.leadSide]: ["lead"], [ids.clientSide]: ["client"] });
  await assert.rejects(
    () =>
      handler(fake, joe, {
        idempotency_key: "00000571-0000-4000-8000-000000000066",
        survivor_party: ids.leadSide,
        merged_party: ids.clientSide,
        match_basis: "exact phone and address",
        same_person_because: "same NPI and address in the intake evidence",
      }),
    /wrong_merge_survivor/,
  );
  assert.deepEqual(fake.updates, [], "the wrong survivor is refused before any row moves");
});

test("merge evidence records both basis and the pre-merge orphan sweep", async () => {
  const fake = new Fake({ [ids.leadSide]: ["lead"], [ids.clientSide]: ["client"] });
  await merge(fake, { same_person_because: "same NPI and address, verified in the record" });
  const written = JSON.stringify(fake.events);
  assert.match(written, /exact email domain/);
  assert.match(written, /orphan_sweep/);
});

test("an empty or throwaway basis does not open the gate", async () => {
  for (const excuse of ["", "   ", "ok", "yes", "same person"]) {
    const fake = new Fake({ [ids.leadSide]: ["lead"], [ids.clientSide]: ["client"] });
    await assert.rejects(
      () => merge(fake, { same_person_because: excuse }),
      `"${excuse}" should not be enough to retire a ref permanently`,
    );
  }
});

test("two leads are an ordinary duplicate and are NOT gated", async () => {
  const fake = new Fake({ [ids.leadSide]: ["lead"], [ids.clientSide]: ["lead"] });
  const out = await merge(fake);
  assert.equal(out.ok, true, "this rule is about a lead/client PAIR, not about duplicates generally");
});

test("two clients are an ordinary duplicate and are NOT gated", async () => {
  const fake = new Fake({ [ids.leadSide]: ["client"], [ids.clientSide]: ["client"] });
  const out = await merge(fake);
  assert.equal(out.ok, true);
});

test("a party that already holds BOTH roles is not the shape Joe ruled on", async () => {
  const fake = new Fake({ [ids.leadSide]: ["lead", "client"], [ids.clientSide]: ["client"] });
  const out = await merge(fake);
  assert.equal(out.ok, true, "the conversion has already happened on that row; this is a different question");
});

test("a vendor row on either side takes it out of scope", async () => {
  const fake = new Fake({ [ids.leadSide]: ["lead"], [ids.clientSide]: ["client", "vendor"] });
  const out = await merge(fake);
  assert.equal(
    out.ok,
    true,
    "vendor duplicates have their own verb and their own history; do not widen this gate onto them",
  );
});

test("the gate is symmetric — it does not depend on which side is the survivor", async () => {
  const fake = new Fake({ [ids.leadSide]: ["lead"], [ids.clientSide]: ["client"] });
  await assert.rejects(
    () =>
      handler(fake, joe, {
        idempotency_key: "00000571-0000-4000-8000-000000000067",
        survivor_party: ids.leadSide,
        merged_party: ids.clientSide,
        match_basis: "exact email domain",
      }),
    /lead_client_pair|wrong_merge_survivor/,
    "a caller who swaps the arguments cannot slip past either the pair gate or survivor precedence",
  );
});

test("party merges refuse unresolved deal vendor assignments before moving any records", async () => {
  const fake = new Fake({ [ids.leadSide]: ["vendor"], [ids.clientSide]: ["vendor"] });
  fake.assignmentCount = 1;
  await assert.rejects(
    merge(fake),
    (error) => object(object(error).payload).error === "merge_vendor_assignments_require_resolution",
  );
  assert.deepEqual(fake.updates, []);
});
