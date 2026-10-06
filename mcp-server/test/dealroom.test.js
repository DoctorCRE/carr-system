import test from "node:test";
import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import { pipelineChanges } from "../src/dealroom.js";
import { TOOLS } from "../src/tools.js";
import { docActivityEntry } from "../src/doc-activity.js";
import {
  createFieldWriteState, performFieldWrite, nextCellBase,
} from "../../dealroom/js/field-write-reconciliation.mjs";

const ids = {
  deal: "10000000-0000-0000-0000-000000000001",
  joe: "20000000-0000-0000-0000-000000000001",
  dell: "20000000-0000-0000-0000-000000000002",
};
const actors = {
  joe: { id: ids.joe, slug: "joe", display: "Joe", human: true },
  dell: { id: ids.dell, slug: "dell", display: "Dell", human: true },
};

class FakeClient {
  constructor(now = "2026-08-08T15:00:00.000Z") {
    this.now = new Date(now);
    this.sequence = 1;
    this.toolCalls = new Map();
    this.events = [];
    this.leases = new Map();
    this.notes = [];
    this.nextActions = [];
    this.postCallActions = [];
    this.conflicts = [];
    this.criticalDates = [];
    this.captureSessions = [];
    this.deals = new Map([[ids.deal, {
      id: ids.deal, name: "Deal Alpha", phase: "research", owner: "joe",
      type: "renewal", city: "Mobile", segment: "healthcare",
      attention: false, next_date: null, version: 1, salesforce_id: null,
      outcome: null, closed_on: null,
      operating_state: "active", parking_reason: null, parking_note: null,
      parked_at: null, parked_by: null,
    }]]);
  }

  tick(milliseconds) { this.now = new Date(this.now.getTime() + milliseconds); }
  uuid() { return `90000000-0000-0000-0000-${String(this.sequence++).padStart(12, "0")}`; }
  actorSlug(id) { return Object.values(actors).find(actor => actor.id === id)?.slug || "system"; }
  tupleAfter(row, time, id) {
    return row.recorded_at > time || (row.recorded_at === time && row.id > id);
  }
  addEvent({ actor = actors.joe, verb = "seed", subject_type = "deal", subject_id = ids.deal,
    field = null, old_value = null, new_value = null, recorded_at = this.now.toISOString(),
    id = this.uuid(), idempotency_key = null,
    cause = null, human_quote = null, agent_rationale = null,
    organization_tenant_id = "carr-internal", personal_scope = "none" }) {
    const row = { id, recorded_at, actor: actor.slug, actor_id: actor.id, verb, subject_type,
      subject_id, field, old_value, new_value, idempotency_key,
      cause, human_quote, agent_rationale, organization_tenant_id, personal_scope };
    this.events.push(row);
    return row;
  }

  async query(text, params = []) {
    const sql = text.replace(/\s+/g, " ").trim();

    if (sql.startsWith("select request_hash, response")) {
      const prior = this.toolCalls.get(params[0]);
      return { rows: prior ? [{ request_hash: prior.request_hash, response: prior.response }] : [] };
    }
    if (sql.startsWith("insert into tool_call")) {
      this.toolCalls.set(params[0], { request_hash: params[3], response: JSON.parse(params[4]),
        actor_id: params[2], organization_tenant_id: params[7] ?? null,
        application_session_id: params[12] ?? null });
      return { rows: [] };
    }
    if (sql.startsWith("select id,subject_id,field,old_value,new_value from event")) {
      assert.match(sql, /organization_tenant_id=\$2/);
      assert.match(sql, /personal_scope='none' or personal_scope=\$3/);
      const event = this.events.find(row => row.id === params[0] && row.subject_type === "deal" &&
        row.organization_tenant_id === params[1] && ["none", params[2]].includes(row.personal_scope));
      return { rows: event ? [{ id:event.id, subject_id:event.subject_id, field:event.field,
        old_value:event.old_value, new_value:event.new_value }] : [] };
    }
    if (sql.startsWith("select id from event where subject_type='deal'")) {
      const rows = this.events.filter(event => event.subject_type === "deal" &&
        event.subject_id === params[0] && event.field === params[1])
        .sort((a, b) => b.recorded_at.localeCompare(a.recorded_at) || b.id.localeCompare(a.id));
      return { rows: rows.slice(0, 1).map(event => ({ id:event.id })) };
    }
    if (sql.includes("from v_ref_index") && sql.includes("where subject_id=$1")) {
      // A raw uuid resolves exactly — the Deal Room board addresses deals by id,
      // and so does every write the board makes.
      const deal = this.deals.get(params[0]);
      return { rows: deal ? [{ subject_type: "deal", subject_id: deal.id }] : [] };
    }
    if (sql.includes("from v_ref_index") && sql.includes("subject_type='deal'")) {
      const needle = String(params[0]).replaceAll("%", "").toLowerCase();
      const rows = [...this.deals.values()].filter(d => d.name.toLowerCase().includes(needle))
        .map(d => ({ subject_id: d.id, display_name: d.name, status: d.phase, client_ref: "C-1" }));
      return { rows };
    }
    if (sql.includes("dealroom:field-lock")) return { rows: [{}] };
    if (sql.includes("dealroom:close-lead") || sql.includes("dealroom:open-lead")) return { rows: [] };
    if (sql === "select id from actor where slug=$1")
      return { rows: [{ id: actors[params[0]].id }] };
    if (sql.startsWith("update deal_participant set to_at=now()"))
      return { rows: [{ actor_id: actors[this.deals.get(params[0]).owner].id }] };
    if (sql.startsWith("insert into deal_participant")) return { rows: [] };
    if (sql.startsWith("update deal set owner=$1, updated_by=$2 where id=$3")) {
      const deal = this.deals.get(params[2]);
      deal.owner = params[0];
      deal.version += 1;
      return { rows: [] };
    }
    if (sql === "select id from actor where slug=$1 and active") {
      const actor = actors[params[0]];
      return { rows: actor ? [{ id: actor.id }] : [] };
    }
    if (sql.includes("dealroom:base-event")) {
      return { rows: this.events.filter(e => e.id === params[0] && e.subject_type === "deal" &&
        e.subject_id === params[1] && e.field === params[2]).map(e => ({ recorded_at: e.recorded_at, id: e.id })) };
    }
    if (sql.includes("dealroom:latest-field-event")) {
      const [dealId, field, baseTime, baseId] = params;
      const rows = this.events.filter(e => e.subject_type === "deal" && e.subject_id === dealId && e.field === field)
        .filter(e => !baseTime || this.tupleAfter(e, String(baseTime), String(baseId)))
        .sort((a, b) => b.recorded_at.localeCompare(a.recorded_at) || b.id.localeCompare(a.id));
      return { rows: rows.slice(0, 1).map(e => ({ event_id: e.id, actor_id: e.actor_id,
        actor: e.actor, value: e.new_value?.[field] ?? null })) };
    }
    if (sql === "select version from deal where id=$1 for update") {
      const deal = this.deals.get(params[0]);
      return { rows: deal ? [{ version: deal.version }] : [] };
    }
    if (sql.startsWith("select a.slug as actor, e.verb, e.field")) {
      return { rows: this.events.filter(e => e.subject_id === params[0]).slice(-5).reverse()
        .map(({ actor, verb, field, old_value, new_value, recorded_at }) =>
          ({ actor, verb, field, old_value, new_value, recorded_at })) };
    }
    if (sql.startsWith("update deal set ") && sql.includes("updated_by=$1")) {
      const deal = this.deals.get(params.at(-1));
      const assignments = sql.slice("update deal set ".length, sql.indexOf(", updated_by=$1"))
        .split(", ");
      assignments.forEach((assignment, index) => { deal[assignment.split("=")[0]] = params[index + 1]; });
      deal.version += 1;
      return { rows: [] };
    }
    if (/^select (phase|owner|attention|next_date) as value from deal/.test(sql)) {
      const field = sql.match(/^select (\w+) as value/)[1];
      const deal = this.deals.get(params[0]);
      return { rows: deal ? [{ value: deal[field] }] : [] };
    }
    if (sql === "select next_date::text as value from deal where id=$1" ||
        sql === "select next_date::text as next_date from deal where id=$1") {
      const deal = this.deals.get(params[0]);
      const key = sql.includes("as value") ? "value" : "next_date";
      return { rows: deal ? [{ [key]: deal.next_date }] : [] };
    }
    if (sql.startsWith("select jsonb_build_object('state',operating_state")) {
      const deal = this.deals.get(params[0]);
      return { rows: deal ? [{ value: { state: deal.operating_state,
        reason: deal.parking_reason, note: deal.parking_note } }] : [] };
    }
    if (sql.includes("dealroom:apply-operating-state")) {
      const deal = this.deals.get(params[0]);
      deal.operating_state = params[1];
      deal.parking_reason = params[1] === "parked" ? params[2] : null;
      deal.parking_note = params[1] === "parked" ? params[3] : null;
      deal.parked_at = params[1] === "parked" ? this.now.toISOString() : null;
      deal.parked_by = params[1] === "parked" ? params[4] : null;
      deal.version += 1;
      return { rows: [] };
    }
    if (sql.startsWith("select ") && sql.endsWith(" from deal where id=$1")) {
      const fields = sql.slice("select ".length, -" from deal where id=$1".length).split(",");
      const deal = this.deals.get(params[0]);
      return { rows: deal ? [Object.fromEntries(fields.map(field => [field, deal[field]]))] : [] };
    }
    if (sql.includes("dealroom:apply-field")) {
      const field = sql.match(/update deal set (\w+)=/)[1];
      const deal = this.deals.get(params[0]);
      deal[field] = params[1];
      deal.version += 1;
      return { rows: [] };
    }
    if (sql.startsWith("insert into event")) {
      this.addEvent({ actor: { id: params[1], slug: this.actorSlug(params[1]) }, verb: params[2],
        subject_type: params[3], subject_id: params[4], field: params[5],
        old_value: params[6] ? JSON.parse(params[6]) : null,
        new_value: params[7] ? JSON.parse(params[7]) : null,
        // $11 in writeEvent's insert: the operation's key, which is how the row
        // it just wrote is found again without a `returning` clause.
        idempotency_key: params[10] ?? null,
        // The provenance columns writeEvent actually writes. `cause` is not a
        // parameter: writeEvent interpolates it into the statement text, so it
        // is read back out of the text, from the one position it can occupy.
        cause: (sql.match(/, '([a-z_]+)', \$9,/) || [])[1] ?? null,
        human_quote: params[8] ?? null,
        agent_rationale: params[9] ?? null,
        recorded_at: this.now.toISOString() });
      return { rows: [] };
    }
    if (sql.includes("dealroom:board-field-base")) {
      // The board list read, with each editable cell's latest committed event —
      // the same (recorded_at desc, id desc) the SQL orders by, and the same
      // single pass, so a value and its base cannot come from different moments.
      const fields = params[2] || [];
      const rows = [...this.deals.values()].map(deal => {
        const field_base = {};
        for (const event of this.events) {
          if (event.subject_type !== "deal" || event.subject_id !== deal.id) continue;
          if (!event.field || !fields.includes(event.field)) continue;
          const held = field_base[event.field];
          if (!held || this.tupleAfter(event, held.recorded_at, held.id))
            field_base[event.field] = { id: event.id, recorded_at: event.recorded_at };
        }
        return { id: deal.id, name: deal.name, type: deal.type, phase: deal.phase,
          owner: deal.owner, attention: deal.attention, next_date: deal.next_date,
          next_step: null, market: deal.city, segment: deal.segment, workspace_kind: "team",
          client_id: null, client_ref: "C-1", client_name: deal.name,
          account_client_id: null, account_client_ref: null, account_name: null,
          account_owner: null, market_agent: null, last_touch: null, last_review_at: null,
          operating_state: deal.operating_state, parking_reason: deal.parking_reason,
          parking_note: deal.parking_note, parked_at: deal.parked_at, parked_by: deal.parked_by,
          field_base };
      });
      return { rows };
    }
    if (sql.includes("from v_deal_room_account")) return { rows: [] };
    if (sql.includes("from v_deal_room_session")) return { rows: [] };
    if (sql.includes("dealroom:written-event")) {
      const rows = this.events.filter(e => e.subject_type === "deal" && e.subject_id === params[0] &&
        e.field === params[1] && e.idempotency_key === params[2])
        .sort((a, b) => b.recorded_at.localeCompare(a.recorded_at) || b.id.localeCompare(a.id));
      return { rows: rows.slice(0, 1).map(e => ({ id: e.id, recorded_at: e.recorded_at })) };
    }
    if (sql.includes("dealroom:create-conflict")) {
      const conflict = { id: this.uuid(), deal_id: params[0], field: params[1],
        value_a: JSON.parse(params[2]), actor_a: params[3], event_a: params[4],
        value_b: JSON.parse(params[5]), actor_b: params[6], status: "open",
        resolved_by: null, winner: null };
      this.conflicts.push(conflict);
      return { rows: [{ id: conflict.id, status: conflict.status }] };
    }
    if (sql.includes("dealroom:get-conflict")) {
      const found = this.conflicts.find(conflict => conflict.id === params[0]);
      return { rows: found ? [{ ...found }] : [] };
    }
    if (sql.includes("dealroom:resolve-conflict")) {
      const found = this.conflicts.find(conflict => conflict.id === params[0]);
      found.status = "resolved";
      found.resolved_by = params[1];
      found.winner = params[2];
      return { rows: [] };
    }
    if (sql.includes("dealroom:presence-upsert")) {
      const expires_at = new Date(this.now.getTime() + 3000).toISOString();
      this.leases.set(params.join(":"), { actor: this.actorSlug(params[0]), actor_id: params[0],
        deal_id: params[1], field: params[2], expires_at });
      return { rows: [{ expires_at }] };
    }
    if (sql.includes("dealroom:current-step")) {
      const rows = this.notes.filter(n => n.deal_id === params[0] && n.kind === "next_step")
        .sort((a, b) => b.created_at.localeCompare(a.created_at) || b.id.localeCompare(a.id));
      return { rows: rows.slice(0, 1).map(({ id, text: noteText }) => ({ id, text: noteText })) };
    }
    if (sql.includes("dealroom:add-note") || sql.includes("dealroom:add-next-step")) {
      const kind = sql.includes("add-next-step") ? "next_step" : "note";
      const note = { id: this.uuid(), deal_id: params[0], kind, text: params[1], actor_id: params[2],
        actor: this.actorSlug(params[2]), created_at: new Date(this.now.getTime() + this.sequence).toISOString() };
      this.notes.push(note);
      return { rows: [{ id: note.id, created_at: note.created_at }] };
    }
    if (sql.includes("dealroom:set-next-date")) {
      const deal = this.deals.get(params[0]);
      deal.next_date = params[1];
      deal.version += 1;
      return { rows: [] };
    }
    if (sql.includes("dealroom:drop-prior-action")) {
      for (const action of this.nextActions) {
        if (action.subject_id === params[1] && action.owner_id === params[0] && action.status === "open")
          action.status = "dropped";
      }
      return { rows: [] };
    }
    if (sql.includes("dealroom:add-next-action")) {
      const action = { id: this.uuid(), subject_id: params[0], owner_id: params[1],
        owner: this.actorSlug(params[1]), due_on: params[2], description: params[3],
        status: "open", updated_at: this.now.toISOString() };
      this.nextActions.push(action);
      return { rows: [{ id: action.id }] };
    }
    if (sql.includes("from v_deal_room_event") && sql.includes("limit $3")) {
      const [cursorTime, cursorId, limit] = params;
      const rows = this.events.filter(e => e.subject_type === "deal")
        .filter(e => !["sf_commission_placeholder", "sf_close_date_placeholder"].includes(e.field))
        .filter(e => !cursorTime || this.tupleAfter(e, String(cursorTime), String(cursorId)))
        .sort((a, b) => a.recorded_at.localeCompare(b.recorded_at) || a.id.localeCompare(b.id))
        .slice(0, limit).map(e => ({ ...e, actor_id: undefined }));
      return { rows };
    }
    if (sql.includes("from v_deal_room_presence")) {
      const rows = [...this.leases.values()].filter(lease => new Date(lease.expires_at) > this.now)
        .map(({ actor, deal_id, field, expires_at }) => ({ actor, deal_id, field, expires_at }))
        .sort((a, b) => a.actor.localeCompare(b.actor) || a.deal_id.localeCompare(b.deal_id) || a.field.localeCompare(b.field));
      return { rows };
    }
    if (sql.includes("from v_capture_session_status")) return { rows: this.captureSessions.map(row => ({ ...row })) };
    if (sql.includes("from v_deal_reconciliation_read") && !sql.includes("v_deal_room_board")) {
      const deal = this.deals.get(params[0]);
      return { rows: deal ? [{ id: deal.id, name: deal.name, salesforce_id: deal.salesforce_id,
        base_version: deal.version, phase: deal.phase, outcome: deal.outcome, closed_on: deal.closed_on }] : [] };
    }
    if (sql.includes("from v_deal_room_board") && sql.includes("where b.id=$1")) {
      const deal = this.deals.get(params[0]);
      return { rows: deal ? [{ id: deal.id, name: deal.name, phase: deal.phase, owner: deal.owner,
        type: deal.type, city: deal.city, segment: deal.segment, attention: deal.attention,
        next_date: deal.next_date, next_step: this.nextActions.find(a => a.subject_id === deal.id && a.status === "open")?.description || null,
        workspace_kind: "team", operating_state: deal.operating_state,
        parking_reason: deal.parking_reason, parking_note: deal.parking_note,
        parked_at: deal.parked_at, parked_by: deal.parked_by,
        salesforce_id: deal.salesforce_id, base_version: deal.version }] : [] };
    }
    if (sql.includes("from v_deal_room_current_lease where deal_id=$1")) return { rows: this.currentLease ? [this.currentLease] : [] };
    if (sql.includes("from v_deal_room_note")) {
      const rows = this.notes.filter(n => n.deal_id === params[0])
        .sort((a, b) => b.created_at.localeCompare(a.created_at) || b.id.localeCompare(a.id))
        .map(({ id, kind, text: noteText, actor, created_at }) => ({ id, kind, text: noteText, actor, created_at }));
      return { rows };
    }
    if (sql.includes("from v_deal_room_critical_date")) return { rows: [...this.criticalDates] };
    if (sql.includes("from v_deal_room_event") && sql.includes("subject_id=$1")) {
      const rows = this.events.filter(e => e.subject_type === "deal" && e.subject_id === params[0])
        .filter(e => !["sf_commission_placeholder", "sf_close_date_placeholder"].includes(e.field))
        .sort((a, b) => b.recorded_at.localeCompare(a.recorded_at) || b.id.localeCompare(a.id))
        .map(({ id, recorded_at, actor, verb, field, old_value, new_value }) =>
          ({ id, recorded_at, actor, verb, field, old_value, new_value }));
      return { rows };
    }
    if (sql.includes("from v_deal_room_action")) {
      return { rows: [...this.nextActions, ...this.postCallActions]
        .filter(a => (a.subject_id || a.deal_id) === params[0]).map(a => ({ ...a })) };
    }
    if (sql.includes("from v_deal_room_activity") || sql.includes("from v_deal_room_participant") ||
        sql.includes("from v_deal_room_premises") || sql.includes("from v_deal_room_negotiation") ||
        sql.includes("from v_deal_room_document")) return { rows: [] };
    throw new Error(`unhandled fake query: ${sql}`);
  }
}

async function call(name, client, actor, args) {
  return TOOLS[name].handler(client, actor, args);
}

async function body(response) { return response.json(); }

test("cursor pages are stable, strictly ordered, gap-free, and placeholder-free", async () => {
  const db = new FakeClient();
  const t = "2026-08-08T15:00:00.000Z";
  db.addEvent({ id: "30000000-0000-0000-0000-000000000002", recorded_at: t, field: "phase", new_value: { phase: "research" } });
  db.addEvent({ id: "30000000-0000-0000-0000-000000000001", recorded_at: t, field: "owner", new_value: { owner: "joe" } });
  db.addEvent({ field: "sf_commission_placeholder", new_value: { sf_commission_placeholder: 1 } });
  db.addEvent({ subject_type: "client", field: "phase", new_value: { phase: "closed" } });
  db.tick(1000);
  db.addEvent({ field: null, new_value: { phase: "negotiation", sf_close_date_placeholder: "hidden" } });
  db.tick(1000);
  db.addEvent({ field: "attention", new_value: { attention: true } });

  const firstRequest = new Request("https://example.test/pipeline/changes");
  const first = await body(await pipelineChanges(firstRequest, db, actors.joe, { limit: 2 }));
  const firstRepeat = await body(await pipelineChanges(firstRequest, db, actors.joe, { limit: 2 }));
  assert.deepEqual(firstRepeat, first);
  assert.deepEqual(first.events.map(e => e.id), [
    "30000000-0000-0000-0000-000000000001",
    "30000000-0000-0000-0000-000000000002",
  ]);

  const pageRequest = new Request(`https://example.test/pipeline/changes?cursor=${first.cursor}`);
  const second = await body(await pipelineChanges(pageRequest, db, actors.dell, { limit: 2 }));
  const secondRepeat = await body(await pipelineChanges(pageRequest, db, actors.dell, { limit: 2 }));
  assert.deepEqual(secondRepeat, second);
  assert.equal([...first.events, ...second.events].length, 4);
  assert.equal(JSON.stringify({ first, second }).includes("sf_commission_placeholder"), false);
  assert.equal(JSON.stringify({ first, second }).includes("sf_close_date_placeholder"), false);
  assert.equal((await pipelineChanges(
    new Request("https://example.test/pipeline/changes?cursor=broken"), db, actors.joe)).status, 400);
});

test("presence lease upserts and expires at read time with a fake clock", async () => {
  const db = new FakeClient();
  await call("presence-lease", db, actors.joe,
    { idempotency_key: "lease-1", deal: "Deal Alpha", field: "phase" });
  assert.equal(db.leases.size, 1);
  assert.equal(db.events.length, 0);

  db.tick(1000);
  let polled = await body(await pipelineChanges(new Request("https://example.test/pipeline/changes"), db, actors.dell));
  assert.deepEqual(polled.presence.map(p => [p.actor, p.field]), [["joe", "phase"]]);

  await call("presence-lease", db, actors.joe,
    { idempotency_key: "lease-2", deal: "Deal Alpha", field: "phase" });
  assert.equal(db.leases.size, 1);
  db.tick(2999);
  polled = await body(await pipelineChanges(new Request("https://example.test/pipeline/changes"), db, actors.dell));
  assert.equal(polled.presence.length, 1);
  db.tick(2);
  polled = await body(await pipelineChanges(new Request("https://example.test/pipeline/changes"), db, actors.dell));
  assert.equal(polled.presence.length, 0);
});

test("concurrent edits to different fields both land without conflict", async () => {
  const db = new FakeClient();
  const phase = await call("patch-deal-field", db, actors.joe,
    { idempotency_key: "patch-phase", deal: "Deal Alpha", field: "phase", value: "negotiation", base_event_id: null });
  const attention = await call("patch-deal-field", db, actors.dell,
    { idempotency_key: "patch-attention", deal: "Deal Alpha", field: "attention", value: true, base_event_id: null });
  assert.equal(phase.ok, true);
  assert.equal(attention.ok, true);
  assert.equal(db.deals.get(ids.deal).phase, "negotiation");
  assert.equal(db.deals.get(ids.deal).attention, true);
  assert.equal(db.conflicts.length, 0);
  assert.deepEqual(db.events.map(e => [e.field, e.actor]), [["phase", "joe"], ["attention", "dell"]]);
});

test("same-field conflict retains both actors and values; resolve uses normal attributed path", async () => {
  const db = new FakeClient();
  await call("patch-deal-field", db, actors.joe,
    { idempotency_key: "owner-a", deal: "Deal Alpha", field: "owner", value: "joe", base_event_id: null });
  const collided = await call("patch-deal-field", db, actors.dell,
    { idempotency_key: "owner-b", deal: "Deal Alpha", field: "owner", value: "dell", base_event_id: null });
  assert.equal(collided.ok, false);
  assert.deepEqual({ value_a: collided.conflict.value_a, actor_a: collided.conflict.actor_a,
    value_b: collided.conflict.value_b, actor_b: collided.conflict.actor_b },
  { value_a: "joe", actor_a: "joe", value_b: "dell", actor_b: "dell" });
  assert.equal(db.deals.get(ids.deal).owner, "joe");

  const resolved = await call("resolve-conflict", db, actors.joe,
    { idempotency_key: "resolve-owner", conflict_id: collided.conflict.id, winner: "b" });
  assert.equal(resolved.ok, true);
  assert.equal(db.deals.get(ids.deal).owner, "dell");
  assert.equal(db.conflicts[0].status, "resolved");
  assert.equal(db.conflicts[0].resolved_by, ids.joe);
  assert.deepEqual(db.events.map(e => [e.verb, e.actor]),
    [["patch-deal-field", "joe"], ["resolve-conflict", "joe"]]);
  assert.ok(db.events.every(e => e.actor_id));
});

test("a conflict cannot overwrite a third edit to the same field", async () => {
  const db = new FakeClient();
  const first = await call("patch-deal-field", db, actors.joe,
    { idempotency_key: "third-a", deal: "Deal Alpha", field: "owner", value: "joe", base_event_id: null });
  const crossed = await call("patch-deal-field", db, actors.dell,
    { idempotency_key: "third-b", deal: "Deal Alpha", field: "owner", value: "dell", base_event_id: null });
  assert.equal(crossed.ok, false);
  const third = await call("patch-deal-field", db, actors.joe,
    { idempotency_key: "third-c", deal: "Deal Alpha", field: "owner", value: "dell", base_event_id: first.event_id });
  assert.equal(third.ok, true);
  await assert.rejects(
    call("resolve-conflict", db, actors.dell,
      { idempotency_key: "third-resolve", conflict_id: crossed.conflict.id, winner: "a" }),
    (error) => error.payload?.error === "newer_change_exists" || error.error === "newer_change_exists",
  );
  assert.equal(db.deals.get(ids.deal).owner, "dell");
  assert.equal(db.conflicts[0].status, "open");
  assert.equal(db.events.length, 2);
});

test("a retry under the same key replays; a fresh key over the same intent conflicts with itself", async () => {
  // The property the board's write path depends on, pinned here so a later
  // server change cannot quietly remove it.
  const db = new FakeClient();
  const args = { deal: "Deal Alpha", field: "attention", value: true, base_event_id: null };
  const first = await call("patch-deal-field", db, actors.joe, { idempotency_key: "flag-1", ...args });
  assert.equal(first.ok, true);
  assert.equal(db.events.length, 1);

  db.tick(1000);
  const replay = await call("patch-deal-field", db, actors.joe, { idempotency_key: "flag-1", ...args });
  assert.equal(replay.replayed, true);
  assert.deepEqual({ ...replay, replayed: undefined }, { ...first, replayed: undefined },
    "the recorded answer comes back, not a new one");
  assert.equal(db.events.length, 1, "no second write");
  assert.equal(db.conflicts.length, 0);

  // The same intent, from the same stale base, under a NEW key is a different
  // operation — and the event the first one wrote is now an intervening change.
  const fresh = await call("patch-deal-field", db, actors.joe, { idempotency_key: "flag-2", ...args });
  assert.equal(fresh.ok, false);
  assert.equal(db.conflicts.length, 1);
  assert.deepEqual([fresh.conflict.actor_a, fresh.conflict.actor_b], ["joe", "joe"]);
  assert.deepEqual([fresh.conflict.value_a, fresh.conflict.value_b], [true, true]);

  // And a key already spent on different arguments is refused rather than replayed.
  await assert.rejects(call("patch-deal-field", db, actors.joe,
    { idempotency_key: "flag-1", ...args, value: false }), /key_reuse/);
});

test("the board's retry after a lost answer lands once, under the key it first used", async () => {
  // The client model (dealroom/js/field-write-reconciliation.mjs) driven against
  // the real verb, over a transport that loses the answer AFTER the write
  // commits — the failure the per-cell key exists for.
  const db = new FakeClient();
  const sent = [];
  let dropAnswer = true;
  const patch = async (request) => {
    sent.push(request);
    const res = await call("patch-deal-field", db, actors.joe, {
      idempotency_key: request.idempotency_key, deal: request.deal, field: request.field,
      value: request.value, base_event_id: request.base_event_id,
    });
    if (dropAnswer) { dropAnswer = false; throw new Error("connection lost before the answer arrived"); }
    return res.ok === false && res.conflict
      ? { status: "conflict", conflict: res.conflict }
      : { status: "ok", ...res };
  };
  let writes = createFieldWriteState();
  const io = { getState: () => writes, setState: (next) => { writes = next; },
    newKey: () => "board-attention-1", patch };

  const lost = await performFieldWrite({ deal: "Deal Alpha", field: "attention", value: true, base: null, ...io });
  assert.equal(lost.status, "unknown", "the board says it does not know, and shows nothing as saved");
  assert.equal(db.events.length, 1, "while the write did in fact land");
  assert.equal(db.deals.get(ids.deal).attention, true);

  // The feed delivers that event and moves this cell's base before the person
  // presses Retry. The retained request is not rewritten by it.
  const base = db.events[0].id;
  const retry = await performFieldWrite({ deal: "Deal Alpha", field: "attention", value: true, base, ...io });
  assert.equal(retry.status, "ok");
  assert.equal(retry.replayed, true);
  assert.deepEqual(sent[1], sent[0], "same key, same base, same value");
  assert.equal(db.events.length, 1, "one event for one intended change");
  assert.equal(db.conflicts.length, 0, "and no conflict between a partner and themselves");
  assert.deepEqual(writes, {}, "the cell is settled and open to the next intent");
  // The event that moved the base here is this operation's OWN, and the answer
  // says so: the replay names the event it committed, and that is the id the feed
  // moved the base to. So this is not a newer change to reconcile with — it is
  // this change, arriving twice, and the board applies it and says nothing.
  assert.equal(retry.event_id, db.events[0].id);
  assert.equal(retry.superseded, false,
    "an operation is never superseded by the event it itself committed");
  assert.equal(retry.message, null, "and no sentence about a partner's change is shown");

  // What the same two clicks used to do, kept here so the difference is visible.
  const secondKey = await call("patch-deal-field", db, actors.joe, { idempotency_key: "a-second-key",
    deal: "Deal Alpha", field: "attention", value: true, base_event_id: null });
  assert.equal(secondKey.ok, false);
  assert.equal(db.conflicts.length, 1);
  assert.equal(secondKey.conflict.actor_a, secondKey.conflict.actor_b);
});

/**
 * The board's own write path against the real verb, as app.js runs it: one write
 * state, one base per cell, and the same two rules app.js uses — performFieldWrite
 * for the operation and nextCellBase for the base — imported here rather than
 * restated, so a change to either shows up in these sequences.
 *
 * `dropAnswers` loses the answer to that many attempts AFTER the write has
 * committed, which is the failure the per-cell key exists for. `feedSaw` is the
 * changes feed delivering an event. Nothing else moves a base.
 */
function boardCells(db, actor, { dropAnswers = 0 } = {}) {
  const sent = [];
  let losses = dropAnswers;
  let minted = 0;
  let writes = createFieldWriteState();
  const fieldBase = new Map();
  // Normalized exactly as app.js's noteCellBase normalizes: a base is an identity
  // and a time. A feed event carries values too, and none of them belong in this
  // map — which is what the board's own rule says, so the harness must say it too.
  const noteBase = (cell, event) => {
    if (!event?.id) return;
    const seen = { id: event.id, recorded_at: event.recorded_at ?? null };
    fieldBase.set(cell, nextCellBase(fieldBase.get(cell) || null, seen));
  };
  // What applyBoardSnapshot does with an authoritative read: seed every editable
  // cell's base from the snapshot, through the same forward-only rule.
  const readBoard = async () => {
    const board = await TOOLS["deal-room-board"].handler(db, actor, { workspace: "all" });
    for (const deal of board.deals) {
      for (const [field, seen] of Object.entries(deal.field_base || {})) {
        noteBase(`${deal.id}|${field}`, seen);
      }
    }
    return board;
  };
  const write = async (field, value, deal = ids.deal) => {
    const cell = `${deal}|${field}`;
    const result = await performFieldWrite({
      deal, field, value,
      base: fieldBase.get(cell)?.id || null,
      baseNow: () => fieldBase.get(cell)?.id || null,
      getState: () => writes, setState: (next) => { writes = next; },
      newKey: () => `board-${field}-${++minted}`,
      patch: async (request) => {
        sent.push(request);
        const res = await call("patch-deal-field", db, actor, {
          idempotency_key: request.idempotency_key, deal: request.deal, field: request.field,
          value: request.value, base_event_id: request.base_event_id,
        });
        if (losses > 0) { losses -= 1; throw new Error("connection lost before the answer arrived"); }
        return res.ok === false && res.conflict
          ? { status: "conflict", conflict: res.conflict }
          : { status: "ok", ...res };
      },
    });
    if (result.status === "ok" && !result.superseded && result.event_id) {
      noteBase(cell, { id: result.event_id, recorded_at: result.event_recorded_at });
    }
    return result;
  };
  return {
    sent, write, readBoard,
    keys: () => minted,
    base: (field, deal = ids.deal) => fieldBase.get(`${deal}|${field}`) || null,
    feedSaw: (field, event, deal = ids.deal) => noteBase(`${deal}|${field}`, event),
    state: () => writes,
  };
}

test("the write's answer names the event it committed, and two edits before any poll both land", async () => {
  // The fast-second-edit route: no feed at all between them, which is what a
  // second click a second later looks like. The first edit's answer names its
  // event, so the second edit sends THAT as its base instead of the stale one
  // the board started with — and stops conflicting with itself.
  const db = new FakeClient();
  const joe = boardCells(db, actors.joe);

  const first = await joe.write("attention", true);
  assert.equal(first.status, "ok");
  assert.equal(first.event_id, db.events[0].id, "read back from the record, not constructed");
  assert.equal(first.event_recorded_at, db.events[0].recorded_at);
  assert.deepEqual(joe.base("attention"), { id: db.events[0].id, recorded_at: db.events[0].recorded_at });

  db.tick(1000);
  const second = await joe.write("attention", false);
  assert.equal(second.status, "ok", "a genuinely different second edit lands");
  assert.equal(joe.sent[1].base_event_id, db.events[0].id, "on the base its own first edit created");
  assert.notEqual(joe.sent[1].idempotency_key, joe.sent[0].idempotency_key, "and under its own key");

  assert.equal(db.events.length, 2, "two intended changes, two events");
  assert.equal(db.conflicts.length, 0, "and no conflict between a partner and themselves");
  assert.equal(db.deals.get(ids.deal).attention, false);
  assert.equal(second.event_id, db.events[1].id);
  assert.deepEqual(joe.base("attention"), { id: db.events[1].id, recorded_at: db.events[1].recorded_at });
});

test("the two base statements hold together as written, since no test can run them", async () => {
  // NOT execution, and it does not pretend to be: every suite that touches these
  // verbs answers a fake by matching the comment marker, so a scoping or
  // parameter fault in either statement would reach production untested — and
  // deal-room-board is the Deal Room's only home read, so its failure mode is a
  // blank board. This is the cheapest guard against silent drift in the parts a
  // reader cannot check by eye: the correlation, the parameter count, and the
  // distinct-on/order-by agreement Postgres requires.
  const tools = await readFile(new URL("../src/tools.js", import.meta.url), "utf8");
  const dealRoomApi = await readFile(
    new URL("../../migrations/0079_deal_room_api.sql", import.meta.url), "utf8");

  const board = tools.slice(tools.indexOf('"deal-room-board": {'),
    tools.indexOf("dealroom:board-field-base") + 200);
  assert.match(board, /from v_deal_room_board b\b/, "the outer relation is aliased, so b.id resolves");
  assert.match(board, /from v_deal_room_event e\b/,
    "the reader must use the granted deal-scoped event view");
  assert.doesNotMatch(board, /from (?:public\.)?event e\b/,
    "carr_reader is views-only and cannot read the event base table");
  assert.match(dealRoomApi,
    /grant select on v_deal_room_event,[\s\S]*?to carr_reader;/,
    "the selected event view must be part of carr_reader's declared surface");
  assert.doesNotMatch(dealRoomApi, /grant select on (?:table )?(?:public\.)?event\b/i,
    "the fix must not widen carr_reader to the event base table");
  assert.match(board, /where e\.subject_type='deal' and e\.subject_id=b\.id/,
    "the subquery is correlated to the row it decorates, not to the whole table");
  assert.match(board, /distinct on \(e\.field\) e\.field, e\.id, e\.recorded_at[\s\S]*?order by e\.field, e\.recorded_at desc, e\.id desc/,
    "distinct on must lead the order by, and the rest is the record layer's own ordering");
  assert.match(board, /coalesce\(\([\s\S]*?\), '\{\}'::jsonb\) as field_base/,
    "an aggregate over no rows is NULL, and the client must get an object");
  assert.match(board, /e\.field = any\(\$3::text\[\]\)/);
  assert.match(board, /\[workspace, args\.account_client_id \|\| null, \[\.\.\.DEAL_ROOM_FIELDS\]\]/,
    "three placeholders, three arguments, and the field list is the registered one");
  for (const column of ["b.workspace_kind", "b.account_client_id", "b.attention", "b.next_date", "b.name"]) {
    assert.ok(board.includes(column), `${column} must stay qualified once the relation is aliased`);
  }

  const written = tools.slice(tools.indexOf("async function applyDealRoomField"),
    tools.indexOf("dealroom:written-event") + 200);
  assert.match(written, /where subject_type='deal' and subject_id=\$1 and field=\$2 and idempotency_key=\$3/,
    "the readback names the row this operation wrote, and cannot name a partner's");
  assert.match(written, /\[dealId, field, idempotencyKey\]/);
  assert.match(written, /to_jsonb\(recorded_at\)#>>'\{\}' as recorded_at/,
    "serialized as the changes feed serializes it, so the client compares like with like");
});

test("a write that names no base still fails closed, which is why the board read carries one", async () => {
  // Unchanged server behaviour, kept executable: a request with no base makes no
  // claim about what was seen, so any prior event on that cell conflicts. It
  // refuses and never overwrites — and it is exactly what a cold board used to
  // send on a person's first edit of the session.
  const db = new FakeClient();
  db.addEvent({ field: "phase", new_value: { phase: "research" }, actor: actors.dell });
  db.tick(60000);

  const blind = await call("patch-deal-field", db, actors.joe, { idempotency_key: "blind-1",
    deal: "Deal Alpha", field: "phase", value: "closing", base_event_id: null });
  assert.equal(blind.ok, false);
  assert.equal(db.deals.get(ids.deal).phase, "research", "the record is untouched");
  assert.equal(db.conflicts.length, 1);
});

test("the board read carries each cell's base, so a first edit on a record with history lands", async () => {
  // The cold-first-write route, end to end: history on two cells, one
  // authoritative read, then a first edit — with no changes feed at all.
  const db = new FakeClient();
  db.addEvent({ field: "phase", new_value: { phase: "research" }, actor: actors.dell });
  db.tick(1000);
  db.addEvent({ field: "attention", new_value: { attention: true }, actor: actors.dell });
  db.tick(1000);
  // An older event for a cell that also has a newer one: the base must be the
  // newer of the two, not whichever the scan met first.
  const superseded = db.events[0];
  db.addEvent({ field: "phase", new_value: { phase: "negotiation" }, actor: actors.dell });
  db.tick(60000);
  const newestPhase = db.events.at(-1);

  const joe = boardCells(db, actors.joe);
  const board = await joe.readBoard();
  const row = board.deals.find((deal) => deal.id === ids.deal);
  assert.deepEqual(Object.keys(row.field_base).sort(), ["attention", "phase"],
    "one entry per cell that has history, and none for the cells that have none");
  assert.deepEqual(row.field_base.phase, { id: newestPhase.id, recorded_at: newestPhase.recorded_at });
  assert.notEqual(row.field_base.phase.id, superseded.id);
  assert.equal(row.field_base.owner, undefined, "a cell with no history has no base to send");
  assert.equal(joe.base("owner"), null);

  const first = await joe.write("phase", "closing");
  assert.equal(first.status, "ok", "the first edit of the session lands on a record with history");
  assert.equal(joe.sent[0].base_event_id, newestPhase.id, "on the base the read handed it");
  assert.equal(db.conflicts.length, 0, "no conflict naming an old change as concurrent");
  assert.equal(db.deals.get(ids.deal).phase, "closing");

  // A cell with no history is still written with no base, and still lands.
  const owner = await joe.write("owner", "dell");
  assert.equal(owner.status, "ok");
  assert.equal(joe.sent[1].base_event_id, null, "null is preserved for a genuinely empty cell");
});

test("a partner's write after the snapshot still conflicts, and rapid own edits still land", async () => {
  const db = new FakeClient();
  db.addEvent({ field: "phase", new_value: { phase: "research" }, actor: actors.dell });
  db.tick(1000);
  const joe = boardCells(db, actors.joe);
  await joe.readBoard();

  // Dell writes after Joe's snapshot was taken. Joe's base is now behind, and the
  // read having handed him one changes nothing about that.
  db.tick(1000);
  const dell = await call("patch-deal-field", db, actors.dell, { idempotency_key: "dell-after-snapshot",
    deal: "Deal Alpha", field: "phase", value: "legal", base_event_id: db.events[0].id });
  assert.equal(dell.ok, true);

  db.tick(1000);
  const crossed = await joe.write("phase", "closing");
  assert.equal(crossed.status, "conflict", "a snapshot base is a claim about a moment, not a licence");
  assert.deepEqual([crossed.conflict.actor_a, crossed.conflict.actor_b], ["dell", "joe"]);
  assert.equal(db.deals.get(ids.deal).phase, "legal", "Dell's value stands until it is resolved");

  // Rapid own edits on a cell the snapshot did give him: two in a row, no feed.
  db.tick(1000);
  const one = await joe.write("attention", true);
  const two = await joe.write("attention", false);
  assert.deepEqual([one.status, two.status], ["ok", "ok"]);
  assert.notEqual(joe.sent.at(-1).idempotency_key, joe.sent.at(-2).idempotency_key);
  assert.equal(joe.sent.at(-1).base_event_id, one.event_id, "the second stands on the first's event");
  assert.equal(db.conflicts.length, 1, "and neither of them conflicted with the other");
});

test("a park that crosses a partner answers with a conflict and no sentence of its own", async () => {
  // The park form's conflict branch, in the half that is not the DOM: the answer
  // is the server's conflict, carrying no message, so the form falls back to its
  // own line ("This record was not parked.") while the chooser replaces the
  // dialog's contents. That the chooser is a CONTENT SWAP rather than a second
  // showModal() — which threw a DOMException onto that same line — is asserted in
  // dealroom-board-sync.test.mjs, where app.js's dialog handling is placed.
  const db = new FakeClient();
  db.addEvent({ field: "operating_state", actor: actors.dell,
    new_value: { operating_state: { state: "active", reason: null, note: null } } });
  db.tick(1000);
  const joe = boardCells(db, actors.joe);
  await joe.readBoard();

  db.tick(1000);
  const dell = await call("patch-deal-field", db, actors.dell, { idempotency_key: "dell-parks",
    deal: "Deal Alpha", field: "operating_state",
    value: { state: "parked", reason: "client_paused", note: null },
    base_event_id: db.events[0].id });
  assert.equal(dell.ok, true);

  db.tick(1000);
  const crossed = await joe.write("operating_state", { state: "parked", reason: "other", note: null });
  assert.equal(crossed.status, "conflict");
  assert.deepEqual([crossed.conflict.actor_a, crossed.conflict.actor_b], ["dell", "joe"]);
  assert.equal(crossed.message, null,
    "a conflict is the server's own answer; nothing invents a sentence over it");
  assert.equal(crossed.message || "This record was not parked.", "This record was not parked.",
    "which is the expression the park form throws onto its error line");
  assert.equal(db.deals.get(ids.deal).parking_reason, "client_paused", "Dell's park stands");
  assert.equal(db.events.length, 2, "the refused write is not an event");
});

test("a snapshot taken before a write cannot walk that cell's base backwards", async () => {
  // The board read and a confirmed write can be in flight together: the snapshot
  // carries the OLDER base for a cell this page has already written, which is why
  // seeding is forward-only. Taking it would make the next edit collide with our
  // own committed one.
  const db = new FakeClient();
  db.addEvent({ field: "phase", new_value: { phase: "research" }, actor: actors.dell });
  db.tick(1000);
  const joe = boardCells(db, actors.joe);
  // A read that answers, and whose payload is still in hand when a write lands.
  const opened = await joe.readBoard();
  const staleBase = opened.deals.find((deal) => deal.id === ids.deal).field_base.phase;

  db.tick(1000);
  const written = await joe.write("phase", "negotiation");
  assert.equal(written.status, "ok");
  assert.equal(joe.base("phase").id, written.event_id);

  // The read that was already open now lands, older base and all.
  joe.feedSaw("phase", staleBase);
  assert.equal(joe.base("phase").id, written.event_id, "the newer event stands");

  db.tick(1000);
  const next = await joe.write("phase", "legal");
  assert.equal(next.status, "ok", "so the next edit does not collide with our own committed one");
  assert.equal(db.conflicts.length, 0);
});

test("advancing the base does not weaken the partner check: an intervening edit still conflicts", async () => {
  const db = new FakeClient();
  const joe = boardCells(db, actors.joe);

  const first = await joe.write("phase", "negotiation");
  assert.equal(first.status, "ok");

  // Dell edits the same cell from the base Joe's write created — Dell's own board
  // saw it. Joe's next write is now behind, and must be refused, not merged.
  db.tick(1000);
  const dell = await call("patch-deal-field", db, actors.dell, { idempotency_key: "dell-phase-1",
    deal: "Deal Alpha", field: "phase", value: "legal", base_event_id: db.events[0].id });
  assert.equal(dell.ok, true);

  db.tick(1000);
  const crossed = await joe.write("phase", "closing");
  assert.equal(crossed.status, "conflict", "the server still refuses a write from a superseded base");
  assert.equal(db.conflicts.length, 1);
  assert.deepEqual([crossed.conflict.actor_a, crossed.conflict.actor_b], ["dell", "joe"],
    "a real partner conflict, with both sides named");
  assert.equal(db.deals.get(ids.deal).phase, "legal", "and Dell's value stands until it is resolved");
  assert.equal(db.events.length, 2, "the refused write is not an event");
  assert.deepEqual(joe.base("phase"), { id: db.events[0].id, recorded_at: db.events[0].recorded_at },
    "a conflict names no committed event, so Joe's base does not move");
});

test("a replayed answer never puts a partner's newer value back — ordinary cell", async () => {
  const db = new FakeClient();
  const joe = boardCells(db, actors.joe, { dropAnswers: 1 });

  const lost = await joe.write("phase", "negotiation");
  assert.equal(lost.status, "unknown", "Joe's board never heard the answer");
  assert.equal(db.events.length, 1, "though the write landed");
  assert.equal(joe.base("phase"), null, "an unanswered write names no base to advance to");

  // Dell edits the same cell from the base Joe's write created. No conflict:
  // Dell saw it.
  db.tick(1000);
  const dell = await call("patch-deal-field", db, actors.dell, { idempotency_key: "dell-phase-1",
    deal: "Deal Alpha", field: "phase", value: "legal", base_event_id: db.events[0].id });
  assert.equal(dell.ok, true);
  assert.equal(db.deals.get(ids.deal).phase, "legal");

  // Joe's feed delivers Dell's event, and only then does Joe press Retry.
  joe.feedSaw("phase", db.events[1]);
  const retry = await joe.write("phase", "negotiation");
  assert.equal(retry.status, "ok");
  assert.equal(retry.replayed, true, "the server truthfully replays Joe's recorded answer");
  assert.equal(retry.response.new_value, "negotiation", "which is about Joe's older operation");
  assert.equal(retry.event_id, db.events[0].id, "and names Joe's older event");
  assert.equal(joe.sent[1].base_event_id, null, "sent under the base it was built on");
  assert.equal(retry.superseded, true, "so the board is told not to paint it");
  assert.match(retry.message, /showing the current value rather than applying this one/);

  assert.deepEqual(joe.base("phase"), { id: db.events[1].id, recorded_at: db.events[1].recorded_at },
    "and the replay does not reset the base onto Joe's older event");
  assert.deepEqual(Object.keys(joe.base("phase")).sort(), ["id", "recorded_at"],
    "a base is an identity and a time: no value from an event is kept in it");
  assert.equal(db.deals.get(ids.deal).phase, "legal", "the record still holds Dell's newer value");
  assert.equal(db.events.length, 2, "and the retry wrote nothing");
  assert.equal(db.conflicts.length, 0);
});

test("a replayed answer never un-restores a record a partner has restored — operating state", async () => {
  const db = new FakeClient();
  const joe = boardCells(db, actors.joe, { dropAnswers: 1 });
  const parked = { state: "parked", reason: "prospect_never_active", note: "Intake did not advance" };

  const lost = await joe.write("operating_state", parked);
  assert.equal(lost.status, "unknown");
  assert.equal(db.deals.get(ids.deal).operating_state, "parked");

  db.tick(1000);
  const restored = await call("patch-deal-field", db, actors.dell, { idempotency_key: "dell-restore-1",
    deal: "Deal Alpha", field: "operating_state", value: { state: "active" },
    base_event_id: db.events[0].id });
  assert.equal(restored.ok, true);
  assert.equal(db.deals.get(ids.deal).operating_state, "active");

  joe.feedSaw("operating_state", db.events[1]);
  const retry = await joe.write("operating_state", parked);
  assert.equal(retry.status, "ok");
  assert.equal(retry.replayed, true);
  assert.deepEqual(retry.response.new_value, parked, "the recorded answer still says parked");
  assert.equal(retry.superseded, true);
  assert.match(retry.message, /was recorded\. The board has since seen a newer change/);

  assert.deepEqual(joe.base("operating_state"), { id: db.events[1].id, recorded_at: db.events[1].recorded_at },
    "the base stays on the partner's newer event");
  assert.equal(db.deals.get(ids.deal).operating_state, "active", "the record is still active work");
  assert.equal(db.deals.get(ids.deal).parking_reason, null);
  assert.equal(db.events.length, 2);
  assert.equal(db.conflicts.length, 0);
});

test("parking is a reversible operating state and never changes phase or outcome", async () => {
  const db = new FakeClient();
  const parked = await call("patch-deal-field", db, actors.joe, {
    idempotency_key: "park-alpha", deal: "Deal Alpha", field: "operating_state",
    value: { state: "parked", reason: "prospect_never_active", note: "Intake did not advance" },
    base_event_id: null,
  });
  assert.equal(parked.ok, true);
  assert.deepEqual(parked.old_value, { state: "active", reason: null, note: null });
  assert.equal(db.deals.get(ids.deal).operating_state, "parked");
  assert.equal(db.deals.get(ids.deal).parking_reason, "prospect_never_active");
  assert.equal(db.deals.get(ids.deal).phase, "research");
  assert.equal(db.deals.get(ids.deal).outcome, null);
  assert.deepEqual(db.events[0].new_value.operating_state,
    { state: "parked", reason: "prospect_never_active", note: "Intake did not advance" });

  const restored = await call("revert-deal-field", db, actors.dell, {
    idempotency_key: "restore-alpha", event_id: db.events[0].id,
  });
  assert.equal(restored.ok, true);
  assert.equal(db.deals.get(ids.deal).operating_state, "active");
  assert.equal(db.deals.get(ids.deal).parking_reason, null);
  assert.equal(db.deals.get(ids.deal).phase, "research");

  await assert.rejects(call("patch-deal-field", new FakeClient(), actors.joe, {
    idempotency_key: "park-without-reason", deal: "Deal Alpha", field: "operating_state",
    value: { state: "parked" }, base_event_id: null,
  }), /parking_reason_required/);
});

test("undo refuses foreign tenant, other personal scope, unknown scope and missing inverse value", async () => {
  for (const eventScope of [
    { organization_tenant_id: "synthetic-other-tenant" },
    { personal_scope: "dell-personal" },
    { personal_scope: null },
    { old_value: {} },
  ]) {
    const db = new FakeClient();
    const event = db.addEvent({ field: "phase", old_value: { phase: "research" },
      new_value: { phase: "legal" }, ...eventScope });
    await assert.rejects(call("revert-deal-field", db, actors.joe,
      { event_id: event.id, idempotency_key: "scoped-undo" }),
    error => error.payload?.error === "event_not_revertible");
    assert.equal(db.events.length, 1);
    assert.equal(db.toolCalls.size, 0);
  }
  const db = new FakeClient();
  const event = db.addEvent({ field: "phase", old_value: { phase: "research" },
    new_value: { phase: "legal" }, personal_scope: "dell-personal" });
  const result = await call("revert-deal-field", db, actors.dell,
    { event_id: event.id, idempotency_key: "own-scoped-undo" });
  assert.equal(result.reverted_event_id, event.id);
});

test("undo waits for a same-field writer and refuses its newer committed edit", async () => {
  const db = new FakeClient();
  const first = await call("patch-deal-field", db, actors.joe, {
    idempotency_key: "undo-race-first", deal: "Deal Alpha", field: "phase",
    value: "negotiation", base_event_id: null,
  });
  assert.equal(first.ok, true);
  const firstEvent = db.events[0].id;

  let resumePatch;
  let patchAtUpdate;
  const patchPaused = new Promise(resolve => { patchAtUpdate = resolve; });
  const originalQuery = db.query.bind(db);
  let fieldLocked = false;
  let nextLock;
  let signalLockQueued;
  const lockQueued = new Promise(resolve => { signalLockQueued = resolve; });
  db.query = async (sql, params = []) => {
    if (sql.includes("dealroom:field-lock")) {
      if (fieldLocked) {
        signalLockQueued();
        await new Promise(resolve => { nextLock = resolve; });
      }
      else fieldLocked = true;
      return { rows: [{}] };
    }
    if (sql.includes("dealroom:apply-field") && params[1] === "legal") {
      patchAtUpdate();
      await new Promise(resolve => { resumePatch = resolve; });
    }
    return originalQuery(sql, params);
  };

  const patch = call("patch-deal-field", db, actors.dell, {
    idempotency_key: "undo-race-second", deal: "Deal Alpha", field: "phase",
    value: "legal", base_event_id: firstEvent,
  });
  await patchPaused;
  const undo = call("revert-deal-field", db, actors.joe, {
    idempotency_key: "undo-race-undo", event_id: firstEvent,
  });
  await lockQueued;
  resumePatch();
  assert.equal((await patch).ok, true);
  nextLock?.();
  await assert.rejects(undo, error => error.payload?.error === "newer_change_exists");
  assert.equal(db.deals.get(ids.deal).phase, "legal");
  assert.equal(db.events.length, 2);
});

test("undo follows a stale patch that records a conflict against its target event", async () => {
  const db = new FakeClient();
  await call("patch-deal-field", db, actors.joe, {
    idempotency_key: "stale-race-first", deal: "Deal Alpha", field: "phase",
    value: "negotiation", base_event_id: null,
  });
  const firstEvent = db.events[0].id;
  const originalQuery = db.query.bind(db);
  let resumeConflict;
  let signalConflict;
  const conflictPaused = new Promise(resolve => { signalConflict = resolve; });
  let releaseLock;
  let signalLockQueued;
  const lockQueued = new Promise(resolve => { signalLockQueued = resolve; });
  let fieldLocked = false;
  db.query = async (sql, params = []) => {
    if (sql.includes("dealroom:field-lock")) {
      if (fieldLocked) {
        signalLockQueued();
        await new Promise(resolve => { releaseLock = resolve; });
      } else fieldLocked = true;
      return { rows: [{}] };
    }
    if (sql.includes("dealroom:create-conflict")) {
      signalConflict();
      await new Promise(resolve => { resumeConflict = resolve; });
    }
    return originalQuery(sql, params);
  };

  const stalePatch = call("patch-deal-field", db, actors.dell, {
    idempotency_key: "stale-race-patch", deal: "Deal Alpha", field: "phase",
    value: "legal", base_event_id: null,
  });
  await conflictPaused;
  const undo = call("revert-deal-field", db, actors.joe, {
    idempotency_key: "stale-race-undo", event_id: firstEvent,
  });
  await lockQueued;
  resumeConflict();
  assert.equal((await stalePatch).ok, false);
  releaseLock();
  assert.equal((await undo).ok, true);
  assert.equal(db.deals.get(ids.deal).phase, "research");
  assert.equal(db.conflicts[0].event_a, firstEvent);
});

test("next-step supersede leaves old rows intact and deal thread is stably newest-first", async () => {
  const db = new FakeClient();
  const oldStep = await call("set-next-step", db, actors.joe,
    { idempotency_key: "step-1", deal: "Deal Alpha", text: "Call landlord", next_date: "2026-08-10" });
  db.tick(1000);
  const newStep = await call("set-next-step", db, actors.dell,
    { idempotency_key: "step-2", deal: "Deal Alpha", text: "Review counter", next_date: "2026-08-11" });
  assert.equal(db.notes.length, 2);
  assert.equal(newStep.supersedes, oldStep.next_step_id);
  assert.deepEqual(db.notes.map(n => n.text), ["Call landlord", "Review counter"]);

  const page = await call("get-deal-room", db, actors.joe, { deal: "Deal Alpha" });
  assert.deepEqual(page.thread.map(n => [n.text, n.actor]),
    [["Review counter", "dell"], ["Call landlord", "joe"]]);
  assert.equal(page.next_date, "2026-08-11");
  assert.deepEqual(page.events.map(e => [e.actor, e.field]),
    [["dell", "next_date"], ["dell", "next_step"],
     ["joe", "next_date"], ["joe", "next_step"]]);
  const dateEvents = db.events.filter(event => event.field === "next_date");
  assert.deepEqual(dateEvents.map(event => [event.old_value.next_date, event.new_value.next_date]),
    [[null, "2026-08-10"], ["2026-08-10", "2026-08-11"]]);
  assert.equal(JSON.stringify(page).includes("sf_commission_placeholder"), false);
  assert.equal(JSON.stringify(page).includes("sf_close_date_placeholder"), false);
});

test("undo of a date edit cannot erase a later date set with the next step", async () => {
  const db = new FakeClient();
  await call("patch-deal-field", db, actors.joe, {
    idempotency_key: "date-before-step", deal: "Deal Alpha", field: "next_date",
    value: "2026-08-10", base_event_id: null,
  });
  const firstDateEvent = db.events[0].id;
  db.tick(1000);
  await call("set-next-step", db, actors.dell, {
    idempotency_key: "step-after-date", deal: "Deal Alpha",
    text: "Review counter", next_date: "2026-08-11",
  });
  await assert.rejects(call("revert-deal-field", db, actors.joe, {
    idempotency_key: "undo-old-date", event_id: firstDateEvent,
  }), error => error.payload?.error === "newer_change_exists");
  assert.equal(db.deals.get(ids.deal).next_date, "2026-08-11");
  assert.equal(db.events.at(-1).field, "next_date");
  assert.deepEqual(db.events.at(-1).old_value, { next_date: "2026-08-10" });
});

test("date undo reads PostgreSQL dates as calendar text, including after set-next-step", async () => {
  for (const verb of ["patch-deal-field", "set-next-step"]) {
    const db = new FakeClient();
    db.deals.get(ids.deal).next_date = "2026-08-10";
    const originalQuery = db.query.bind(db);
    db.query = async (sql, params) => {
      const result = await originalQuery(sql, params);
      if (/^select next_date(?: as value)? from deal where id=\$1$/.test(sql) && result.rows[0]) {
        const key = sql.includes("as value") ? "value" : "next_date";
        result.rows[0][key] = new Date("2026-08-10T00:00:00.000Z");
      }
      return result;
    };
    if (verb === "patch-deal-field") {
      await call(verb, db, actors.joe, { idempotency_key: "date-patch",
        deal: "Deal Alpha", field: "next_date", value: "2026-08-11", base_event_id: null });
    } else {
      await call(verb, db, actors.joe, { idempotency_key: "date-step",
        deal: "Deal Alpha", text: "Call landlord", next_date: "2026-08-11" });
    }
    const event = db.events.find(row => row.field === "next_date");
    assert.deepEqual(event.old_value, { next_date: "2026-08-10" });
    const result = await call("revert-deal-field", db, actors.joe,
      { idempotency_key: `undo-${verb}`, event_id: event.id });
    assert.equal(result.ok, true);
    assert.equal(db.deals.get(ids.deal).next_date, "2026-08-10");
  }
});

test("undo of an owner edit cannot erase a later set-lead handoff", async () => {
  const db = new FakeClient();
  await call("patch-deal-field", db, actors.joe, {
    idempotency_key: "owner-before-lead", deal: "Deal Alpha", field: "owner",
    value: "dell", base_event_id: null,
  });
  const firstOwnerEvent = db.events[0].id;
  db.tick(1000);
  await call("set-lead", db, actors.dell, {
    idempotency_key: "lead-after-owner", deal: "Deal Alpha",
    new_lead: "joe", base_version: db.deals.get(ids.deal).version,
  });
  await assert.rejects(call("revert-deal-field", db, actors.joe, {
    idempotency_key: "undo-old-owner", event_id: firstOwnerEvent,
  }), error => error.payload?.error === "newer_change_exists");
  assert.equal(db.deals.get(ids.deal).owner, "joe");
  assert.equal(db.events.at(-1).field, "owner");
  assert.deepEqual(db.events.at(-1).old_value.owner, "dell");
});

test("accepted Call Mode actions appear in the normal Deal Room action payload without replacing another task", async () => {
  const db = new FakeClient();
  db.nextActions.push({ id: "existing-action", subject_id: ids.deal, owner: "dell",
    description: "Review survey", due_on: "2026-08-11", status: "open", updated_at: db.now.toISOString() });
  db.postCallActions.push({ id: "post-call-action", deal_id: ids.deal, owner: "joe",
    description: "Call Dr. Alpha", due_on: "2026-08-12", status: "open", updated_at: db.now.toISOString() });
  const page = await call("get-deal-room", db, actors.joe, { deal: "Deal Alpha" });
  assert.deepEqual(page.next_actions.map(action => action.description).sort(), ["Call Dr. Alpha", "Review survey"]);
  const migration = await import("node:fs/promises").then(fs =>
    fs.readFile(new URL("../../migrations/0094_call_mode_post_call.sql", import.meta.url), "utf8"));
  assert.match(migration, /create or replace view v_deal_room_action[\s\S]*capture_post_call_action/);
  assert.match(migration, /create or replace view v_today_triage[\s\S]*post_call_action/);
});

test("reconciliation reads provide a guarded Salesforce key and verify a closed deal", async () => {
  const db = new FakeClient();
  db.deals.get(ids.deal).salesforce_id = "006PQ00000hXMNaYAO";

  const before = await call("get-deal-room", db, actors.joe, { deal: "Deal Alpha" });
  assert.equal(before.salesforce_id, "006PQ00000hXMNaYAO");
  assert.equal(before.base_version, 1);
  assert.equal(JSON.stringify(before).includes("sf_commission_placeholder"), false);
  assert.equal(JSON.stringify(before).includes("sf_close_date_placeholder"), false);

  const updated = await call("update-deal", db, actors.joe, {
    idempotency_key: "close-from-sf", deal: "Deal Alpha", base_version: before.base_version,
    fields: { phase: "closed", outcome: "won", closed_on: "2026-07-30" },
  });
  assert.deepEqual(updated, { ok: true, updated: ["phase", "outcome", "closed_on"] });

  const after = await call("read-deal-reconciliation", db, actors.joe, { deal: "Deal Alpha" });
  assert.deepEqual(after, { id: ids.deal, name: "Deal Alpha", salesforce_id: "006PQ00000hXMNaYAO",
    base_version: 2, phase: "closed", outcome: "won", closed_on: "2026-07-30" });

  await assert.rejects(
    call("update-deal", db, actors.dell, {
      idempotency_key: "stale-close", deal: "Deal Alpha", base_version: before.base_version,
      fields: { outcome: "lost" },
    }),
    error => error?.payload?.error === "version_conflict",
  );
});

// CONFLICT TIERING (WR-000019 slice S6). A pure version-number race — the
// intervening write touched a DIFFERENT field than this call's own patch — is
// rebased transparently instead of surfaced to a human; a same-field race
// still refuses exactly as before (proven above).
test("update-deal auto-rebases a disjoint-field version race and still surfaces a same-field one", async () => {
  const db = new FakeClient();

  const first = await call("update-deal", db, actors.joe, {
    idempotency_key: "rebase-city", deal: "Deal Alpha", base_version: 1,
    fields: { city: "Daphne" },
  });
  assert.deepEqual(first, { ok: true, updated: ["city"] });
  assert.equal(db.deals.get(ids.deal).version, 2);

  // Dell read the deal at version 1, before Joe's city edit landed, and now
  // patches a COMPLETELY DIFFERENT field. This is the trivial race: nothing
  // Dell wrote conflicts with what Joe wrote.
  const rebased = await call("update-deal", db, actors.dell, {
    idempotency_key: "rebase-notes", deal: "Deal Alpha", base_version: 1,
    fields: { notes_path: "notes/deal-alpha.md" },
  });
  assert.equal(rebased.ok, true);
  assert.deepEqual(rebased.updated, ["notes_path"]);
  assert.equal(rebased.rebased, true);
  assert.equal(rebased.rebase_receipt.from_base_version, 1);
  assert.equal(rebased.rebase_receipt.rebased_to_version, 2);
  assert.equal(rebased.rebase_receipt.disjoint_intervening_events.length, 1);
  assert.equal(rebased.rebase_receipt.disjoint_intervening_events[0].field, "city");
  // BOTH edits actually landed — the rebase re-applied Dell's patch against
  // the current row rather than dropping it or overwriting Joe's.
  assert.equal(db.deals.get(ids.deal).city, "Daphne");
  assert.equal(db.deals.get(ids.deal).notes_path, "notes/deal-alpha.md");
  assert.equal(db.deals.get(ids.deal).version, 3);

  // A THIRD stale caller touching the SAME field city changed (city again)
  // must still surface a real version_conflict — the tiering never widens to
  // cover an actual same-field collision.
  await assert.rejects(
    call("update-deal", db, actors.joe, {
      idempotency_key: "same-field-conflict", deal: "Deal Alpha", base_version: 1,
      fields: { city: "Mobile" },
    }),
    error => error?.payload?.error === "version_conflict",
  );
});

test("cursor round-trips the pg wire timestamp format (microseconds + offset) without JS Date", async () => {
  const db = new FakeClient();
  // Exactly what @neondatabase/serverless hands back for a timestamptz —
  // NOT JS-Date-parseable; the first live poll threw "Invalid time value".
  const pgWire = "2026-08-07 21:44:19.123456+00";
  db.addEvent({ id: "40000000-0000-0000-0000-000000000001", recorded_at: pgWire, field: "phase", new_value: { phase: "legal" } });

  const first = await (await pipelineChanges(new Request("https://example.test/pipeline/changes"), db, actors.joe, { limit: 5 })).json();
  assert.equal(first.events.length, 1);
  assert.ok(first.cursor, "cursor must be produced from a pg-format timestamp");

  // and the produced cursor must be accepted back on the next poll
  const again = await (await pipelineChanges(new Request(`https://example.test/pipeline/changes?cursor=${first.cursor}`), db, actors.joe, { limit: 5 })).json();
  assert.deepEqual(again.events, [], "no new events after the cursor");
  assert.equal(again.cursor, first.cursor);
});

test("pipeline polling includes capture status snapshots with string timestamps", async () => {
  const db = new FakeClient();
  db.captureSessions.push({ session_id: "50000000-0000-0000-0000-000000000001",
    device_id: "mac-studio", state: "distilling",
    started_at: "2026-08-08 14:00:00+00", state_at: "2026-08-08 14:12:00+00" });
  const result = await (await pipelineChanges(
    new Request("https://example.test/pipeline/changes"), db, actors.joe)).json();
  assert.equal(result.capture_sessions[0].state, "distilling");
  assert.equal(typeof result.capture_sessions[0].started_at, "string");
  assert.equal(typeof result.capture_sessions[0].state_at, "string");
});

// AC-EVENT-QUOTE (WR-000109). The partner's verbatim words reach the event
// column that stores them, byte for byte, and the reason reaches the column
// that stores a rationale. The cause is NOT passed by the verb: writeEvent
// derives it from the presence of the quote, so asserting human_stated here
// asserts that the quote actually arrived, not that a literal was copied.
test("patch-deal-field carries the partner's quote and reason onto the event", async () => {
  const db = new FakeClient();
  const quote = "Move Alpha to negotiation — they signed the LOI this morning.";
  const reason = "Partner directed the phase change on the 8:40 call.";
  const patched = await call("patch-deal-field", db, actors.joe, {
    idempotency_key: "quote-1", deal: "Deal Alpha", field: "phase",
    value: "negotiation", base_event_id: null,
    change_reason: reason, human_quote: quote,
  });
  assert.equal(patched.ok, true);
  assert.equal(db.events.length, 1);
  const [event] = db.events;
  assert.equal(event.human_quote, quote);
  assert.equal(event.agent_rationale, reason);
  assert.equal(event.cause, "human_stated");
});

// AC-NO-QUOTE-UNCHANGED. Without a quote nothing about the existing behaviour
// moves: the write is an automation job, which is what it is, and a reason on
// its own does not promote it. The bare call is the pre-WR-000109 shape and
// must still record exactly what it recorded before.
test("patch-deal-field without a quote stays an automation job", async () => {
  const db = new FakeClient();
  const reason = "Swept forward by the nightly stage reconciliation.";
  const reasonOnly = await call("patch-deal-field", db, actors.joe, {
    idempotency_key: "quote-2", deal: "Deal Alpha", field: "phase",
    value: "negotiation", base_event_id: null, change_reason: reason,
  });
  assert.equal(reasonOnly.ok, true);
  assert.equal(db.events[0].cause, "automation_job");
  assert.equal(db.events[0].human_quote, null);
  assert.equal(db.events[0].agent_rationale, reason);

  const bare = await call("patch-deal-field", db, actors.joe, {
    idempotency_key: "quote-3", deal: "Deal Alpha", field: "attention",
    value: true, base_event_id: null,
  });
  assert.equal(bare.ok, true);
  assert.equal(db.events[1].cause, "automation_job");
  assert.equal(db.events[1].human_quote, null);
  assert.equal(db.events[1].agent_rationale, null);
});

test('feed eligibility matches writer-produced events for every reversible field', async () => {
  const values = { phase: 'legal', owner: 'dell', attention: true, next_date: '2026-10-12',
    operating_state: { state: 'parked', reason: 'client_paused', note: 'Synthetic pause' } };
  for (const [field, value] of Object.entries(values)) {
    const db = new FakeClient();
    await call('patch-deal-field', db, actors.joe, {
      idempotency_key: `feed-${field}`, deal: 'Deal Alpha', field, value, base_event_id: null,
    });
    const e = db.events[0];
    const feed = docActivityEntry({ ...e, has_old_value: true, is_latest: true });
    assert.equal(feed.undo.state, 'available', field);
    assert.deepEqual(feed.before, e.old_value[field]);
    assert.deepEqual(feed.after, e.new_value[field]);
    await call('revert-deal-field', db, actors.joe, { event_id: e.id, idempotency_key: `undo-feed-${field}` });
  }
  for (const [field, value] of Object.entries({ attention: 'true', owner: 'other', phase: '',
    next_date: 'tomorrow', operating_state: { state: 'parked', reason: 'unknown' } })) {
    const db = new FakeClient();
    const e = db.addEvent({ field, old_value: { [field]: value }, new_value: { [field]: value } });
    assert.equal(docActivityEntry({ ...e, has_old_value: true, is_latest: true }).undo.state, 'unavailable', field);
    await assert.rejects(call('revert-deal-field', db, actors.joe, { event_id: e.id, idempotency_key: `bad-${field}` }));
  }
});

test('direct phase control retains authenticated browser provenance without composing a human quote', async () => {
  const c = new FakeClient(); const inserts = []; const query = c.query.bind(c);
  c.query = async (sql,params) => { if(sql.trim().startsWith('insert into event')) inserts.push(params); return query(sql,params); };
  const result = await TOOLS['patch-deal-field'].handler(c, {...actors.joe,via:'dealroom-cookie',client_id:'dealroom-pwa'}, {
    deal: ids.deal, field: 'phase', value: 'legal', base_event_id: null,
    idempotency_key: 'demo-manual-phase',
  });
  assert.equal(result.ok, true);
  assert.equal(inserts[0][11], 'dealroom-cookie'); assert.equal(inserts[0][12], 'dealroom-pwa');
  const event = c.events.find(e => e.idempotency_key === 'demo-manual-phase');
  assert.equal(event.human_quote, null);
});


test("deal timeline returns exact current lease dates and explicit absence without deriving obligations", async () => {
  const db = new FakeClient();
  const empty = await call("get-deal-room", db, actors.joe, { deal: "Deal Alpha" });
  assert.equal(empty.schema_version, "deal-timeline.v1");
  assert.equal(empty.lease, null);
  db.currentLease = { id: "demo-lease", version: 2, status: "current", executed_on: "2026-10-01",
    commencement_on: "2026-11-01", expiration_on: "2031-10-31", options_note: "Notice period requires review",
    evidence_kind: "lease_abstract", evidence_ref: "synthetic clause 3", source: "synthetic abstract" };
  const page = await call("get-deal-room", db, actors.joe, { deal: "Deal Alpha" });
  assert.deepEqual(page.lease, db.currentLease);
  assert.equal(Object.hasOwn(page.lease, "rent_start_on"), false);
  assert.equal(Object.hasOwn(page.lease, "option_on"), false);
  const source = await readFile(new URL("../src/tools.js", import.meta.url), "utf8");
  assert.match(source, /from v_deal_room_current_lease where deal_id=\$1/);
});
