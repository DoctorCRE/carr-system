// update-lead.test.mjs — coverage for the new update-lead verb (open loop #383).
//
// THE GAP THIS CLOSES. The health audit found two live clients (L-118, L-135)
// stuck on lead_stage 'nurture_drip' mid-deal, still receiving the
// prospecting newsletter, because no writer in the registry could move an
// EXISTING lead's stage. new-lead and promote-pool only ever CREATE a lead;
// log-outreach only ever CLOSES one (not_interested -> closed_lost,
// do_not_contact -> do_not_contact). Nothing could move a lead FORWARD
// through its own funnel. update-lead is that writer, modeled closely on
// update-vendor / update-deal: idempotency_key required, base_version from a
// fresh read required, version_conflict surfaced (never auto-retried), and a
// stage/lane value is checked against the live lead_stage / lead_lane tables
// before the write — the same up-front FK validation new-lead already does,
// so a wrong slug names the field and lists the valid ones instead of
// dying opaquely inside a poisoned transaction.
//
// Identity (party_id) and the lead-to-client conversion pointer (client_id)
// are deliberately absent from the allowed-fields list, the same posture
// update-deal takes on client_id (reassign-deal owns that, and there is no
// reassign-lead here — client_id has no dedicated verb yet, so it stays out
// of reach rather than being half-wired) and the same posture
// update-party-contact takes on identity fields generally (record-finding's
// proposes_correction doctrine, rule 5d44d3f3): a field-level editor edits
// business state, not identity or structural pointers.
//
// Run with: node --test mcp-server/test/update-lead.test.mjs
// (also picked up by `npm test`'s test/*.test.mjs glob).

import { test } from "node:test";
import assert from "node:assert/strict";
import { TOOLS, ToolError } from "../src/tools.js";

const ids = {
  joe: "10000000-0000-0000-0000-000000000002",
  lead118: "30000000-0000-0000-0000-000000000118",
  lead135: "30000000-0000-0000-0000-000000000135",
  vendor: "40000000-0000-0000-0000-000000000001",
};
const joe = { id: ids.joe, slug: "joe", display: "Joe", human: true,
  via: "mcp", client_id: "claude" };

const LEAD_STAGES = ["new", "qualified", "engaged", "outreach_active",
  "nurture_drip", "opportunity", "active_deal", "closed_won",
  "closed_lost", "do_not_contact", "archived"];
const LEAD_LANES = ["renewal", "new_entity", "relocation", "upstream", "associate"];

class UpdateLeadFake {
  constructor({ leadId, ref, stage, lane = null, suppressed = false, version, subjectType = "lead" }) {
    this.leadId = leadId;
    this.ref = ref;
    this.subjectType = subjectType;
    this.row = { stage, lane, suppressed, version };
    this.events = [];
    this.updated = null;
  }

  async query(text, params = []) {
    const sql = text.replace(/\s+/g, " ").trim();

    if (sql.startsWith("select request_hash, response"))
      return { rows: [] };

    if (sql.includes("from v_ref_index where subject_type='lead' and ref ilike"))
      return { rows: (this.subjectType === "lead" && params[0].toLowerCase() === this.ref.toLowerCase())
        ? [{ subject_id: this.leadId }] : [] };
    if (sql.includes("from v_ref_index where subject_type='vendor' and ref ilike"))
      return { rows: (this.subjectType === "vendor" && params[0].toLowerCase() === this.ref.toLowerCase())
        ? [{ subject_id: this.leadId }] : [] };

    if (sql.startsWith("select version from lead where id=$1 for update"))
      return { rows: params[0] === this.leadId ? [{ version: this.row.version }] : [] };
    if (sql.startsWith("select created_at from lead where id=$1"))
      return { rows: params[0] === this.leadId ? [{ created_at: "2026-08-01T00:00:00.000000+00:00" }] : [] };
    if (sql.includes("from event e join actor a on a.id=e.actor_id") && sql.includes("e.recorded_at > $2"))
      return { rows: this.events };

    if (sql.startsWith("select 1 from lead_stage where slug=$1"))
      return { rows: LEAD_STAGES.includes(params[0]) ? [{ "?column?": 1 }] : [] };
    if (sql.startsWith("select slug from lead_stage order by slug"))
      return { rows: LEAD_STAGES.map(slug => ({ slug })) };
    if (sql.startsWith("select 1 from lead_lane where slug=$1"))
      return { rows: LEAD_LANES.includes(params[0]) ? [{ "?column?": 1 }] : [] };
    if (sql.startsWith("select slug from lead_lane order by slug"))
      return { rows: LEAD_LANES.map(slug => ({ slug })) };

    if (sql.startsWith("select") && sql.includes("from lead where id=$1")) {
      // the pre-write "old value" read — column list varies by call
      const cols = sql.slice("select ".length, sql.indexOf(" from lead")).split(",").map(s => s.trim());
      const out = {};
      for (const c of cols) out[c] = this.row[c] ?? null;
      return { rows: params[0] === this.leadId ? [out] : [] };
    }

    if (sql.startsWith("update lead set")) {
      assert.equal(params.at(-1), this.leadId);
      this.updated = { sql, params };
      return { rows: [{ version: ++this.row.version }] };
    }

    if (sql.startsWith("insert into event")) {
      this.events.push({ verb: params[2], field: params[5] || null,
        old_value: params[6] ? JSON.parse(params[6]) : null,
        new_value: params[7] ? JSON.parse(params[7]) : null });
      return { rows: [] };
    }
    if (sql.startsWith("insert into tool_call"))
      return { rows: [] };

    throw new Error(`unhandled fake query: ${sql}`);
  }
}

test("update-lead: moves an existing lead OFF nurture_drip — the exact loop #383 shape (L-118)", async () => {
  const db = new UpdateLeadFake({ leadId: ids.lead118, ref: "L-118", stage: "nurture_drip", version: 3 });
  const result = await TOOLS["update-lead"].handler(db, joe, {
    idempotency_key: "update-lead-118-stage",
    lead: "L-118",
    base_version: 3,
    fields: { stage: "active_deal" },
  });
  assert.deepEqual(result, { ok: true, updated: ["stage"] });
  assert.ok(db.updated, "the update statement must have run");
  const verbs = db.events.map(e => e.verb);
  assert.ok(verbs.includes("update-lead"));
  assert.deepEqual(db.events[0].new_value, { stage: "active_deal",
    transition_proof: { before_version: 3, after_version: 4, actor_slug: "joe" } });
  assert.deepEqual(db.events[0].old_value, { stage: "nurture_drip" });
});

test("update-lead: L-135, same shape, confirms this isn't a one-off fixture fluke", async () => {
  const db = new UpdateLeadFake({ leadId: ids.lead135, ref: "L-135", stage: "nurture_drip", version: 1 });
  const result = await TOOLS["update-lead"].handler(db, joe, {
    idempotency_key: "update-lead-135-stage",
    lead: "L-135",
    base_version: 1,
    fields: { stage: "active_deal" },
  });
  assert.equal(result.ok, true);
  assert.deepEqual(result.updated, ["stage"]);
});

test("update-lead: an unknown stage names the field and lists the valid slugs, does not touch the row", async () => {
  const db = new UpdateLeadFake({ leadId: ids.lead118, ref: "L-118", stage: "nurture_drip", version: 3 });
  await assert.rejects(
    () => TOOLS["update-lead"].handler(db, joe, {
      idempotency_key: "update-lead-118-bad-stage",
      lead: "L-118",
      base_version: 3,
      fields: { stage: "won" }, // plausible, not real — the real slug is closed_won
    }),
    (err) => {
      assert.ok(err instanceof ToolError);
      assert.equal(err.payload.error, "unknown_stage");
      assert.equal(err.payload.got, "won");
      assert.ok(err.payload.valid.includes("closed_won"));
      return true;
    });
  assert.equal(db.updated, null, "no write may happen once an invalid slug is caught up front");
});

test("update-lead: a stale base_version surfaces version_conflict and is never auto-retried", async () => {
  const db = new UpdateLeadFake({ leadId: ids.lead118, ref: "L-118", stage: "nurture_drip", version: 5 });
  db.events.push({ verb: "update-lead", field: "stage",
    old_value: { stage: "outreach_active" }, new_value: { stage: "nurture_drip" } });
  await assert.rejects(
    () => TOOLS["update-lead"].handler(db, joe, {
      idempotency_key: "update-lead-118-stale",
      lead: "L-118",
      base_version: 3, // stale — current is 5
      fields: { stage: "active_deal" },
    }),
    (err) => {
      assert.ok(err instanceof ToolError);
      assert.equal(err.payload.error, "version_conflict");
      assert.equal(err.payload.current_version, 5);
      return true;
    });
  assert.equal(db.updated, null, "a version conflict must never fall through to a write");
});

test("update-lead: identity and conversion fields are silently out of reach — no_updatable_fields, not a partial write", async () => {
  const db = new UpdateLeadFake({ leadId: ids.lead118, ref: "L-118", stage: "nurture_drip", version: 3 });
  await assert.rejects(
    () => TOOLS["update-lead"].handler(db, joe, {
      idempotency_key: "update-lead-118-identity",
      lead: "L-118",
      base_version: 3,
      fields: { party_id: "99999999-0000-0000-0000-000000000000", client_id: "88888888-0000-0000-0000-000000000000" },
    }),
    (err) => {
      assert.ok(err instanceof ToolError);
      assert.equal(err.payload.error, "no_updatable_fields");
      assert.ok(!err.payload.allowed.includes("party_id"));
      assert.ok(!err.payload.allowed.includes("client_id"));
      assert.ok(!err.payload.allowed.includes("registry_ref"));
      return true;
    });
  assert.equal(db.updated, null);
});

test("update-lead: a ref that resolves to a vendor, not a lead, is refused by name", async () => {
  const db = new UpdateLeadFake({ leadId: ids.vendor, ref: "V-CPA-006", stage: null, version: 1, subjectType: "vendor" });
  await assert.rejects(
    () => TOOLS["update-lead"].handler(db, joe, {
      idempotency_key: "update-lead-wrong-type",
      lead: "V-CPA-006",
      base_version: 1,
      fields: { stage: "active_deal" },
    }),
    (err) => {
      assert.ok(err instanceof ToolError);
      assert.equal(err.payload.error, "not_a_lead");
      return true;
    });
});

test("update-lead: multiple business-state fields land together, each with its own event", async () => {
  const db = new UpdateLeadFake({ leadId: ids.lead118, ref: "L-118", stage: "nurture_drip", version: 3 });
  const result = await TOOLS["update-lead"].handler(db, joe, {
    idempotency_key: "update-lead-118-multi",
    lead: "L-118",
    base_version: 3,
    fields: { stage: "active_deal", segment: "dental", notes: "moved to active deal per loop #383" },
  });
  assert.deepEqual(new Set(result.updated), new Set(["stage", "segment", "notes"]));
  assert.equal(db.events.length, 3);
});

test("update-lead: do_not_contact is inseparable from suppression", async () => {
  const db = new UpdateLeadFake({ leadId: ids.lead118, ref: "L-118", stage: "outreach_active", version: 3 });
  await assert.rejects(
    () => TOOLS["update-lead"].handler(db, joe, {
      idempotency_key: "update-lead-118-dnc-without-suppression",
      lead: "L-118",
      base_version: 3,
      fields: { stage: "do_not_contact" },
    }),
    (err) => {
      assert.ok(err instanceof ToolError);
      assert.equal(err.payload.error, "do_not_contact_requires_suppression");
      return true;
    });
  assert.equal(db.updated, null);

  const accepted = new UpdateLeadFake({ leadId: ids.lead118, ref: "L-118", stage: "outreach_active", version: 3 });
  const result = await TOOLS["update-lead"].handler(accepted, joe, {
    idempotency_key: "update-lead-118-dnc-with-suppression",
    lead: "L-118",
    base_version: 3,
    fields: { stage: "do_not_contact", suppressed: true },
  });
  assert.deepEqual(new Set(result.updated), new Set(["stage", "suppressed"]));
});

test("update-lead: clearing a standing suppression instruction is an explicit human correction", async () => {
  const machine = { ...joe, human: false, slug: "codex", display: "Codex" };
  const refused = new UpdateLeadFake({ leadId: ids.lead118, ref: "L-118", stage: "do_not_contact",
    suppressed: true, version: 3 });
  await assert.rejects(
    () => TOOLS["update-lead"].handler(refused, machine, {
      idempotency_key: "update-lead-118-machine-clear-dnc",
      lead: "L-118",
      base_version: 3,
      fields: { stage: "engaged", suppressed: false },
    }),
    (err) => {
      assert.ok(err instanceof ToolError);
      assert.equal(err.payload.error, "suppression_clear_requires_human");
      return true;
    });
  assert.equal(refused.updated, null);

  const human = new UpdateLeadFake({ leadId: ids.lead118, ref: "L-118", stage: "do_not_contact",
    suppressed: true, version: 3 });
  const result = await TOOLS["update-lead"].handler(human, joe, {
    idempotency_key: "update-lead-118-human-clear-dnc",
    lead: "L-118",
    base_version: 3,
    fields: { stage: "engaged", suppressed: false },
  });
  assert.deepEqual(new Set(result.updated), new Set(["stage", "suppressed"]));
});

test("archive decisions require a partner, while notes on an archived lead remain editable", async () => {
  const machine = { ...joe, slug: "synthetic-agent", human: false };
  for (const [stage, next] of [["new", "archived"], ["archived", "new"]]) {
    const db = new UpdateLeadFake({ leadId: ids.lead118, ref: "L-999999", stage, version: 1 });
    await assert.rejects(() => TOOLS["update-lead"].handler(db, machine, {
      idempotency_key: `synthetic-stage-${stage}`, lead: "L-999999", base_version: 1, fields: { stage: next },
    }), /archive_requires_partner/);
    assert.equal(db.updated, null);
  }
  const db = new UpdateLeadFake({ leadId: ids.lead118, ref: "L-999999", stage: "archived", version: 1 });
  assert.deepEqual(await TOOLS["update-lead"].handler(db, machine, {
    idempotency_key: "synthetic-archive-note", lead: "L-999999", base_version: 1,
    fields: { notes: "Synthetic reviewed note" },
  }), { ok: true, updated: ["notes"] });
});
