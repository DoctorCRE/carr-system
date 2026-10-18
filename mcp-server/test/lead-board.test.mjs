import test from "node:test";
import assert from "node:assert/strict";
import { TOOLS } from "../src/tools.js";

const SAFE_LEAD = Object.freeze({
  id: "30000000-0000-0000-0000-000000000118",
  registry_ref: "L-118",
  party_id: "20000000-0000-4000-8000-000000000118",
  name: "Example Practice",
  specialty: "Dental",
  city: "Mobile",
  county: "Mobile",
  state: "AL",
  lane: "renewal",
  stage: "nurture_drip",
  stage_label: "Nurture (Drip)",
  stage_sort: 50,
  score: "71.00",
  segment: "renewal-radar",
  suppressed: false,
  est_lease_event: "2027-03-01",
  event_confidence: "medium",
  last_touch: "2026-08-11",
  next_action_date: "2026-08-28",
  owner: "joe",
  owner_label: "Joe",
  base_version: 3,
  created_at: "2026-07-01T12:00:00.000Z",
  updated_at: "2026-08-21T12:00:00.000Z",
  conversion_eligible: true,
  converted: false,
  stage_moves: [{
    move_id: "40000000-0000-0000-0000-000000000118",
    from_stage: "outreach_active",
    to_stage: "nurture_drip",
    reason: "Practice requested a later follow-up",
    evidence_ref: "synthetic-evidence-118",
    status: "undone",
    created_at: "2026-08-20T12:00:00.000Z",
    undone_at: "2026-08-21T12:00:00.000Z",
    undone_by: "Joe",
  }],
});

class LeadBoardFake {
  constructor() { this.queries = []; }

  async query(text) {
    const sql = text.replace(/\s+/g, " ").trim();
    this.queries.push(sql);
    if (/\b(?:from|join)\s+(?:lead|lead_stage_move)\b/i.test(sql)) {
      throw Object.assign(new Error("permission denied for base lead table"), { code: "42501" });
    }
    if (sql.includes("from v_lead_board_stage")) {
      return { rows: [
        { slug: "new", label: "New", sort: 10 },
        { slug: "nurture_drip", label: "Nurture (Drip)", sort: 50 },
        { slug: "do_not_contact", label: "Do Not Contact", sort: 100 },
      ] };
    }
    if (sql.includes("from v_lead_board")) {
      return { rows: [
        SAFE_LEAD,
        { ...SAFE_LEAD, id: "30000000-0000-0000-0000-000000000119",
          registry_ref: "L-119", name: "Do Not Contact Example", stage: "do_not_contact",
          stage_label: "Do Not Contact", stage_sort: 100, suppressed: true, base_version: 8 },
      ] };
    }
    throw new Error(`unhandled fake query: ${sql}`);
  }
}

test("lead-board exposes the full safe, versioned worked-lead board", async () => {
  const tool = TOOLS["lead-board"];
  assert.ok(tool, "lead-board must be registered");
  assert.equal(tool.write, false);
  assert.match(tool.description, /all leads/i);
  assert.match(tool.description, /never pre-filtered|never pre-qualified/i);

  const db = new LeadBoardFake();
  const result = await tool.handler(db, { slug: "joe", human: true }, {});

  assert.deepEqual(result.stages.map((stage) => stage.slug), ["new", "nurture_drip", "do_not_contact"]);
  assert.equal(result.leads.length, 2, "suppressed and terminal leads stay visible");
  assert.equal(result.leads[0].party_id, SAFE_LEAD.party_id);
  assert.deepEqual(result.leads[0], SAFE_LEAD);
  assert.equal(result.leads[0].converted, false);
  assert.deepEqual(result.leads[0].stage_moves, SAFE_LEAD.stage_moves);
  assert.deepEqual(result.metrics, { nurture_count: 1, conversion_denominator: 2, converted_count: 0 });
  assert.equal(result.leads[0].score, SAFE_LEAD.score);
  assert.equal(result.leads[0].base_version, 3, "safe writes receive the authoritative row version");
  assert.equal(result.leads[1].suppressed, true);
  assert.match(result.generated_at, /^\d{4}-\d{2}-\d{2}T/);
  assert.equal(db.queries.length, 2);
  assert.ok(db.queries.some((sql) => sql.includes("from v_lead_board_stage")));
  assert.ok(db.queries.some((sql) => sql.includes("from v_lead_board")));
  assert.equal(db.queries.some(sql => /\b(?:from|join)\s+(?:lead|lead_stage_move)\b/i.test(sql)), false);
  assert.equal(db.queries.some((sql) => /\blimit\b/i.test(sql)), false,
    "the default board must not silently pre-qualify by truncating the lead set");
});

test("lead-board contract does not expose contact, notes, or raw-source fields", async () => {
  const result = await TOOLS["lead-board"].handler(new LeadBoardFake(), { slug: "joe", human: true }, {});
  const forbidden = ["phone", "cell", "email", "address", "notes", "notes_path", "source_detail"];
  for (const lead of result.leads) {
    for (const field of forbidden) assert.equal(Object.hasOwn(lead, field), false, field);
  }
});

test("archived leads stay visible but leave nurture and conversion metrics",async()=>{
  const db=new LeadBoardFake(),query=db.query.bind(db);
  db.query=async sql=>{
    const r=await query(sql);
    if(/\bfrom v_lead_board\b/.test(sql))return {rows:[{...SAFE_LEAD,converted:true},{...SAFE_LEAD,id:"archived-example",stage:"archived",conversion_eligible:false,converted:true}]};
    return r;
  };
  const result=await TOOLS["lead-board"].handler(db);
  assert.deepEqual(result.metrics,{nurture_count:1,conversion_denominator:1,converted_count:1});
  assert.equal(result.leads.length,2);
  assert.deepEqual(result.leads[0].stage_moves, SAFE_LEAD.stage_moves);
  assert.deepEqual(result.leads[1].stage_moves, SAFE_LEAD.stage_moves);
  assert.equal(result.leads[1].conversion_eligible, false);
  assert.equal(db.queries.some(sql => /\b(?:from|join)\s+(?:lead|lead_stage_move)\b/i.test(sql)), false);
});
