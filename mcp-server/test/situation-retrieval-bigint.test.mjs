import test from "node:test";
import assert from "node:assert/strict";
import { TOOLS, ToolError } from "../src/tools.js";

const actor = { id: "10000000-0000-4000-8000-000000000002", slug: "joe", human: true };
const proposalId = "20000000-0000-4000-8000-000000000001";
const approval = {
  idempotency_key: "retrieval-approval", proposal_ids: [proposalId],
  base_versions: { [proposalId]: 2 }, golden_suite_digest: "synthetic-golden-suite",
};

class Database {
  constructor(plan = {}) { this.plan = plan; this.receipts = new Map(); this.promotions = 0; }
  async query(text, params = []) {
    const sql = text.replace(/\s+/g, " ").trim();
    if (sql.includes("select request_hash, response"))
      return { rows: this.receipts.has(params[0]) ? [this.receipts.get(params[0])] : [] };
    if (sql.includes("insert into tool_call")) {
      this.receipts.set(params[0], { request_hash: params[3], response: JSON.parse(params[4]) });
      return { rows: [] };
    }
    if (sql.includes("select promote_retrieval_proposal")) this.promotions++;
    for (const [fragment, value] of Object.entries(this.plan))
      if (sql.includes(fragment)) return { rows: typeof value === "function" ? value(params) : structuredClone(value) };
    return { rows: [] };
  }
}

test("retrieval approval refuses rounded BIGINT concurrency tokens without promotion", async () => {
  const db = new Database({ "from retrieval_proposal": [
    { id: proposalId, status: "pending", version: "9007199254740993" },
  ] });
  await assert.rejects(() => TOOLS["approve-retrieval-proposals"].handler(db, actor, {
    ...approval, base_versions: { [proposalId]: 9007199254740992 },
  }), error => error instanceof ToolError && error.payload.error === "retrieval_base_version_invalid");
  assert.equal(db.promotions, 0);
});

test("retrieval approval refuses unsafe or nonnumeric input versions before database use", async () => {
  const db = { query: async () => { throw new Error("database must not be queried"); } };
  for (const version of [9007199254740992, "2", 0, -1, 1.5, null, undefined])
    await assert.rejects(() => TOOLS["approve-retrieval-proposals"].handler(db, actor, {
      ...approval, base_versions: { [proposalId]: version },
    }), error => error instanceof ToolError && error.payload.error === "retrieval_base_version_invalid");
});

test("retrieval proposal versions round-trip through insertion and both replay paths into approval", async () => {
  const proposals = [
    ["concept", { concept_key: "synthetic-concept", label: "Synthetic", definition: "Synthetic definition" }],
    ["phrase", { concept_key: "synthetic-concept", phrase: "synthetic phrase", match_mode: "exact",
      weight: 1, source: "manual", source_ref: "synthetic" }],
    ["mapping", { concept_key: "synthetic-concept", section_address: "synthetic#section", role: "supports",
      weight: 1, rationale: "Synthetic rationale" }],
    ["retirement", { target_type: "concept", target_id: proposalId, base_version: 1 }],
  ];
  for (const [version, expected] of [["2", 2], ["9007199254740991", 9007199254740991]]) {
    for (const [type, input] of proposals) {
      let inserts = 0;
      const row = { id: proposalId, proposal_type: type, status: "pending", version };
      const db = new Database({
        "insert into retrieval_proposal": () => ++inserts === 1 ? [row] : [],
        "where idempotency_key=": [row],
        "where id = any": [row],
      });
      const args = { ...input, reason: "Synthetic proposal", idempotency_key: `proposal-${type}` };
      const tool = TOOLS[`propose-retrieval-${type}`];
      const inserted = await tool.handler(db, actor, args);
      assert.equal(inserted.proposal.version, expected);
      db.receipts.get(args.idempotency_key).response.proposal.version = version;
      const replay = await tool.handler(db, actor, args);
      assert.equal(replay.replayed, true);
      assert.equal(replay.proposal.version, expected);
      assert.equal(db.receipts.get(args.idempotency_key).response.proposal.version, version);
      assert.equal(inserts, 1);
      const rowReplay = await tool.handler(db, actor, { ...args, idempotency_key: `row-replay-${type}` });
      assert.equal(rowReplay.replay, true);
      assert.equal(rowReplay.proposal.version, expected);
      const approved = await TOOLS["approve-retrieval-proposals"].handler(db, actor, {
        ...approval, base_versions: { [proposalId]: rowReplay.proposal.version },
      });
      assert.equal(approved.ok, true);
      assert.equal(db.promotions, 1);
    }
  }
});

test("retrieval retirement verbs refuse unsafe or nonnumeric base versions before database use", async () => {
  const db = { query: async () => { throw new Error("database must not be queried"); } };
  for (const verb of ["propose-retrieval-retirement", "retire-retrieval-curation"])
    for (const base_version of [9007199254740992, "2", 0, -1, 1.5, null, undefined])
      await assert.rejects(() => TOOLS[verb].handler(db, actor, {
        idempotency_key: "invalid-retirement", target_type: "concept", target_id: proposalId,
        reason: "Synthetic retirement", golden_suite_digest: "synthetic", base_version,
      }), error => error instanceof ToolError && error.payload.error === "retrieval_base_version_invalid");
});

test("retrieval approval validates every stored version before promoting any proposal", async () => {
  const secondId = "20000000-0000-4000-8000-000000000002";
  for (const rows of [
    [{ id: proposalId, status: "pending", version: "9007199254740993" }],
    [{ id: proposalId, status: "pending", version: "2" },
      { id: secondId, status: "pending", version: "9007199254740993" }],
  ]) {
    const db = new Database({ "from retrieval_proposal": rows });
    await assert.rejects(() => TOOLS["approve-retrieval-proposals"].handler(db, actor, {
      ...approval, proposal_ids: rows.map(row => row.id), base_versions: { [proposalId]: 2, [secondId]: 2 },
    }), error => error instanceof ToolError && error.payload.error === "retrieval_version_invalid");
    assert.equal(db.promotions, 0);
  }
});

test("retrieval approval conflicts report the exact safe numeric current version", async () => {
  const db = new Database({ "from retrieval_proposal": [
    { id: proposalId, status: "pending", version: "9007199254740991" },
  ] });
  await assert.rejects(() => TOOLS["approve-retrieval-proposals"].handler(db, actor, approval),
    error => error instanceof ToolError && error.payload.error === "version_conflict" &&
      error.payload.current_version === 9007199254740991);
  assert.equal(db.promotions, 0);
});

test("unsafe proposal versions refuse insertion, row replay, and legacy envelope replay", async () => {
  const args = { idempotency_key: "unsafe-proposal", reason: "Synthetic proposal", concept_key: "synthetic-concept",
    label: "Synthetic", definition: "Synthetic definition" };
  for (const version of ["9007199254740993", 9007199254740992]) {
    for (const rowReplay of [false, true]) {
      const row = { id: proposalId, status: "pending", version };
      const db = new Database({
        "insert into retrieval_proposal": rowReplay ? [] : [row],
        "where idempotency_key=": [row],
      });
      await assert.rejects(() => TOOLS["propose-retrieval-concept"].handler(db, actor, args),
        error => error instanceof ToolError && error.payload.error === "retrieval_version_invalid");
      assert.equal(db.receipts.size, 0);
    }
    let inserts = 0;
    const db = new Database({ "insert into retrieval_proposal": () => {
      inserts++;
      return [{ id: proposalId, status: "pending", version: "2" }];
    } });
    await TOOLS["propose-retrieval-concept"].handler(db, actor, args);
    db.receipts.get(args.idempotency_key).response.proposal.version = version;
    await assert.rejects(() => TOOLS["propose-retrieval-concept"].handler(db, actor, args),
      error => error instanceof ToolError && error.payload.error === "retrieval_version_invalid");
    assert.equal(inserts, 1);
    assert.equal(db.receipts.get(args.idempotency_key).response.proposal.version, version);
  }
});

test("retirement responses normalize safe BIGINT versions and refuse unsafe legacy replay", async () => {
  const args = { idempotency_key: "retire-curation", target_type: "concept", target_id: proposalId,
    base_version: 1, reason: "Synthetic retirement", golden_suite_digest: "synthetic" };
  for (const [version, expected] of [["2", 2], ["9007199254740991", 9007199254740991]]) {
    let retirements = 0;
    const db = new Database({ "from retire_retrieval_curation": params => {
      assert.equal(params[2], 1);
      retirements++;
      return [{ id: proposalId, version, status: "retired" }];
    } });
    const result = await TOOLS["retire-retrieval-curation"].handler(db, actor, args);
    assert.equal(result.retired.version, expected);
    db.receipts.get(args.idempotency_key).response.retired.version = version;
    const replay = await TOOLS["retire-retrieval-curation"].handler(db, actor, args);
    assert.equal(replay.replayed, true);
    assert.equal(replay.retired.version, expected);
    assert.equal(retirements, 1);
    for (const unsafe of ["9007199254740993", 9007199254740992]) {
      db.receipts.get(args.idempotency_key).response.retired.version = unsafe;
      await assert.rejects(() => TOOLS["retire-retrieval-curation"].handler(db, actor, args),
        error => error instanceof ToolError && error.payload.error === "retrieval_version_invalid");
      assert.equal(retirements, 1);
      assert.equal(db.receipts.get(args.idempotency_key).response.retired.version, unsafe);
    }
  }
});

test("doctrine search normalizes only its declared BIGINT policy version before logging", async () => {
  for (const [policy_version, expected] of [["2", 2], ["9007199254740991", 9007199254740991]]) {
    const row = { section_id: proposalId, final_score: 0.9, provenance: {
      policy_id: "coequal-normalized-v1", policy_version, contributors: [{ version: "opaque" }],
    } };
    const db = new Database({
      "retrieval_visibility_actor_id": [{ id: actor.id }],
      "from search_doctrine_situations": [row],
    });
    let logged;
    db.sideWrite = (_sql, params) => { logged = params; };
    const result = await TOOLS["search-doctrine"].handler(db, actor, { q: "synthetic phrase" });
    assert.equal(result.hits[0].provenance.policy_version, expected);
    assert.equal(logged[5], expected);
    assert.equal(row.provenance.policy_version, policy_version);
    assert.deepEqual(result.hits[0].provenance.contributors, [{ version: "opaque" }]);
  }
});

test("doctrine search refuses unsafe BIGINT policy versions before logging", async () => {
  for (const policy_version of ["9007199254740993", 9007199254740992]) {
    const db = new Database({
      "retrieval_visibility_actor_id": [{ id: actor.id }],
      "from search_doctrine_situations": [{ section_id: proposalId, provenance: { policy_version } }],
    });
    db.sideWrite = () => { throw new Error("unsafe policy version must not be logged"); };
    await assert.rejects(() => TOOLS["search-doctrine"].handler(db, actor, { q: "synthetic phrase" }),
      error => error instanceof ToolError && error.payload.error === "retrieval_version_invalid");
  }
});
