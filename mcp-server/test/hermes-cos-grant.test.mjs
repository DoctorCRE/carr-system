// Loop #459: bounded Hermes chief-of-staff door.
// This suite proves the server-controlled separation without a Worker, secret
// value, database, migration, deployment, or live record.
import { test } from "node:test";
import assert from "node:assert/strict";
import {
  hermesActorForToken, hermesActorForTokenMaps, hermesCosActorForToken,
  agentActorForToken, permittedActionOwnerSlugs, authenticatedIdentity, propsForSlug,
} from "../src/identity.js";

// `actorFromProps` is module-private under amendment 8 (PR 1013). The exported
// grant door is `authenticatedIdentity.connectionForGrant`; called without the
// server's witness it returns exactly the same actor, unbranded, which is what
// every case in this file is about.
const actorFromProps = (props, bindings = null) =>
  authenticatedIdentity.connectionForGrant(props, bindings);

import {
  PROFILES, allowedIn, profileForActor, hermesCosDealFieldRefusal,
  hermesCosPremisesRefusal, HERMES_COS_DEAL_FIELDS,
} from "../src/mcp.js";
import { TOOLS, ToolError } from "../src/tools.js";

const PLAIN = JSON.stringify({ "hermes-pilot": "plain-secret" });
const COS = JSON.stringify({ "hermes-pilot": "cos-secret" });
const PROJECTOR = JSON.stringify({ "hermes-pilot": "projector-secret" });
const WRITE = { write: true };
const HALBERD = "Halberd Tire — Pensacola distribution warehouse";
const IDS = Object.freeze({ joe: "10000000-0000-0000-0000-000000000002",
  hermes: "10000000-0000-0000-0000-000000000009",
  deal: "20000000-0000-0000-0000-00000000f459", joeBall: "30000000-0000-0000-0000-000000000001",
  hermesBall: "30000000-0000-0000-0000-000000000002", newBall: "30000000-0000-0000-0000-000000000003" });

const cos = () => ({ ...hermesCosActorForToken("Bearer cos-secret", COS), id: IDS.hermes });
const plain = () => ({ ...hermesActorForToken("Bearer plain-secret", PLAIN), id: IDS.hermes });
const joe = () => ({ ...actorFromProps(propsForSlug("joe", { via: "oauth-google" })), id: IDS.joe });

test("the CoS map is checked separately and before the plain/projector door", () => {
  const a = hermesCosActorForToken("Bearer cos-secret", COS);
  assert.equal(a.slug, "hermes-pilot");
  assert.equal(a.hermesCos, true);
  assert.equal(a.hermes, true);
  assert.equal(a.human, false);
  assert.equal(a.via, "hermes-cos-token");
  assert.equal(a.sponsoring_human_slug, "joe");
  assert.equal(hermesActorForToken("Bearer cos-secret", PLAIN), null);
  assert.equal(hermesCosActorForToken("Bearer plain-secret", COS), null);
  assert.equal(agentActorForToken("Bearer cos-secret", COS), null);
});

test("the CoS door rejects every map key except the registered hermes-pilot runtime", () => {
  for (const slug of ["joe", "dell", "codex", "unknown"]) {
    const actor = hermesCosActorForToken("Bearer adversarial-secret",
      JSON.stringify({ [slug]: "adversarial-secret" }));
    assert.equal(actor, null, `${slug} must not become a CoS actor`);
    assert.equal(Boolean(actor && profileForActor(actor, new Request("https://x/mcp")) === "hermes-cos"), false);
    assert.equal(Boolean(actor && allowedIn(profileForActor(actor, new Request("https://x/mcp")), "update-deal", WRITE)), false);
    assert.deepEqual(permittedActionOwnerSlugs(actor), []);
  }
  assert.equal(hermesCosActorForToken("Bearer cos-secret",
    JSON.stringify({ "hermes-pilot": "cos-secret", joe: "other-secret" })), null,
  "an otherwise valid map with an unregistered key fails closed");
  assert.equal(hermesCosActorForToken("Bearer cos-secret", JSON.stringify(["cos-secret"])), null);
});

test("ordinary Hermes and projector credentials retain their exact existing door", () => {
  const a = hermesActorForTokenMaps("Bearer projector-secret", PLAIN, PROJECTOR);
  assert.equal(a.via, "hermes-token");
  assert.equal(a.hermesCos, undefined);
  assert.equal(profileForActor(a, new Request("https://api.doctorcre.com/mcp?profile=full")), "hermes");
  assert.equal(PROFILES.hermes.has("update-deal"), false);
  assert.equal(PROFILES.hermes.has("add-premises"), false);
  assert.equal(PROFILES.hermes.has("project-room-queue"), true);
  assert.equal(PROFILES.hermes.has("record-dispatch-link"), true);
});

test("the CoS profile is server-locked and differs from Hermes by exactly two verbs", () => {
  const req = new Request("https://api.doctorcre.com/mcp?profile=full");
  assert.equal(profileForActor(cos(), req), "hermes-cos");
  assert.deepEqual([...PROFILES.hermes].sort(), [
    "add-critical-date", "add-loop", "complete-action", "log-activity", "project-room-queue",
    "record-defect", "record-dispatch-link", "record-finding", "set-next-action", "stamp-touch", "update-loop",
  ]);
  assert.deepEqual([...PROFILES["hermes-cos"]].filter(v => !PROFILES.hermes.has(v)).sort(),
    ["add-premises", "update-deal"]);
  assert.equal(allowedIn("hermes-cos", "update-deal", WRITE), true);
  assert.equal(allowedIn("hermes-cos", "add-premises", WRITE), true);
  for (const asked of ["full", "away", "capture", "read", "nonsense"]) {
    assert.equal(profileForActor(cos(), new Request(`https://api.doctorcre.com/mcp?profile=${asked}`)), "hermes-cos");
  }
});

test("the internal CoS marker cannot be caller-set and human/authority invariants remain", () => {
  assert.equal(plain().hermesCos, undefined);
  assert.equal(profileForActor({ ...plain(), hermesCos: false }, new Request("https://x/mcp?profile=full")), "hermes");
  assert.equal(profileForActor({ ...plain(), hermesCos: true }, new Request("https://x/mcp?profile=full")), "hermes",
    "the exact server via is part of the internal CoS selector");
  assert.equal(cos().human, false);
  for (const verb of ["teach", "activate-rule", "retire-rule", "confirm-merge", "merge-vendor-rows",
    "add-party", "update-party-contact", "link-parties", "reassign-deal", "set-lead", "close-loop",
    "new-client", "new-lead", "new-vendor", "prepare-document", "send-message"]) {
    assert.equal(allowedIn("hermes-cos", verb, WRITE), false, `${verb} must stay refused`);
  }
  // humanOnly LABEL RETIRED (WR-000019 slice S1, 2026-08-27): dead since
  // executeRegisteredTool stopped reading it 2026-08-26 (decision dc57f62d);
  // this slice drops the stale declaration from tools.js. The refusals above
  // are the real gate (profile allowlist), unaffected by the label's removal.
  assert.equal(TOOLS["teach"].humanOnly, undefined);
});

test("only CoS may select Joe; plain Hermes and Joe remain own-ball-only", () => {
  assert.deepEqual(permittedActionOwnerSlugs(cos()), ["hermes-pilot", "joe"]);
  assert.deepEqual(permittedActionOwnerSlugs(plain()), ["hermes-pilot"]);
  assert.deepEqual(permittedActionOwnerSlugs(joe()), ["joe"]);
  assert.ok(!permittedActionOwnerSlugs(cos()).includes("dell"));
});

class FakeDB {
  constructor() {
    this.calls = new Map();
    this.balls = [
      { id: IDS.joeBall, owner_id: IDS.joe, status: "open" },
      { id: IDS.hermesBall, owner_id: IDS.hermes, status: "open" },
    ];
    this.events = [];
  }
  async query(text, params = []) {
    const sql = text.replace(/\s+/g, " ").trim();
    if (sql.startsWith("select request_hash, response")) {
      const prior = this.calls.get(params[0]);
      return { rows: prior ? [prior] : [] };
    }
    if (sql.includes("from v_ref_index where subject_type='deal'"))
      return { rows: [{ subject_id: IDS.deal, display_name: HALBERD, status: "site_selection", client_ref: "C-127" }] };
    if (sql.startsWith("select id from actor where slug=$1")) {
      const id = { joe: IDS.joe, "hermes-pilot": IDS.hermes }[params[0]];
      return { rows: id ? [{ id }] : [] };
    }
    if (sql.startsWith("update next_action set status='dropped'")) {
      const [ownerId] = params;
      this.dropped = this.balls.filter(b => b.owner_id === ownerId && b.status === "open");
      for (const row of this.dropped) row.status = "dropped";
      return { rows: [] };
    }
    if (sql.startsWith("update capture_post_call_action")) return { rows: [] };
    if (sql.startsWith("insert into next_action")) {
      this.inserted = { owner_id: params[2], due_on: params[3], description: params[4], created_by: params[5] };
      return { rows: [{ id: IDS.newBall }] };
    }
    if (sql.startsWith("insert into event")) {
      this.events.push({ actor_id: params[1], verb: params[2], new_value: JSON.parse(params[7]) });
      return { rows: [] };
    }
    if (sql.startsWith("insert into tool_call")) {
      this.calls.set(params[0], { request_hash: params[3], response: JSON.parse(params[4]),
        actor_id: params[2], organization_tenant_id: params[7] ?? null,
        application_session_id: params[12] ?? null });
      return { rows: [] };
    }
    throw new Error(`unhandled fake query: ${sql}`);
  }
}

test("Halberd CoS handoff selects Joe, replaces Joe's ball, and keeps runtime provenance", async () => {
  const db = new FakeDB();
  const result = await TOOLS["set-next-action"].handler(db, cos(), {
    idempotency_key: "cos-459-halberd", ref: HALBERD, owner: "joe",
    description: "Confirm lease-vs-purchase preference before the tour", due_on: "2026-08-27",
  });
  assert.equal(result.owner, "joe");
  assert.equal(db.inserted.owner_id, IDS.joe);
  assert.equal(db.inserted.created_by, IDS.hermes);
  assert.deepEqual(db.dropped.map(x => x.id), [IDS.joeBall]);
  assert.equal(db.balls.find(x => x.id === IDS.hermesBall).status, "open");
  assert.equal(db.events[0].new_value.summary, "ball → joe: Confirm lease-vs-purchase preference before the tour");
  assert.equal(db.events[0].new_value.owner, "joe");
  assert.equal(db.events[0].new_value.set_by, "hermes-pilot");
});

test("CoS handoff refuses Dell before any write", async () => {
  const db = new FakeDB();
  await assert.rejects(() => TOOLS["set-next-action"].handler(db, cos(), {
    idempotency_key: "cos-459-dell", ref: HALBERD, owner: "dell", description: "forbidden",
  }), (e) => e instanceof ToolError && e.payload.error === "owner_not_permitted");
  assert.equal(db.inserted, undefined);
  assert.equal(db.events.length, 0);
});

test("CoS defaults to its own ball when owner is omitted", async () => {
  const db = new FakeDB();
  const result = await TOOLS["set-next-action"].handler(db, cos(), {
    idempotency_key: "cos-459-default", ref: HALBERD, description: "Doc keeps this one",
  });
  assert.equal(result.owner, "hermes-pilot");
  assert.equal(db.inserted.owner_id, IDS.hermes);
  assert.deepEqual(db.dropped.map(x => x.id), [IDS.hermesBall]);
});

test("update-deal CoS field lock is exact and whole-call", () => {
  assert.deepEqual([...HERMES_COS_DEAL_FIELDS], ["deal_type", "segment", "city", "lane"]);
  assert.equal(hermesCosDealFieldRefusal("hermes-cos", "update-deal",
    { fields: { deal_type: "lease", segment: "industrial", city: "Pensacola", lane: "territory" } }), null);
  assert.deepEqual(hermesCosDealFieldRefusal("hermes-cos", "update-deal",
    { fields: { deal_type: "lease", phase: "closing", won_value: 1 } }), ["phase", "won_value"]);
  assert.equal(hermesCosDealFieldRefusal("hermes", "update-deal", { fields: { phase: "closing" } }), null);
  assert.equal(hermesCosDealFieldRefusal("full", "update-deal", { fields: { won_value: 1 } }), null);
});

test("add-premises CoS payload lock refuses new_party but allows party_ref", () => {
  assert.equal(hermesCosPremisesRefusal("hermes-cos", "add-premises",
    { ownership: [{ kind: "owner", new_party: { name: "Halberd Holdings" } }] }), true);
  assert.equal(hermesCosPremisesRefusal("hermes-cos", "add-premises",
    { ownership: [{ kind: "owner", party_ref: "P-0948" }] }), false);
  assert.equal(hermesCosPremisesRefusal("hermes", "add-premises",
    { ownership: [{ kind: "owner", new_party: { name: "not reachable by name" } }] }), false);
});

test("bot-brief admission remains read-only and caller authority fields remain absent", () => {
  const brief = TOOLS["bot-brief"];
  assert.notEqual(brief.write, true);
  assert.equal(brief.inputSchema.additionalProperties, false);
  for (const key of ["capability_profile", "read_only", "grants_authority", "envelope_digest", "sponsoring_human_slug"])
    assert.equal(Object.hasOwn(brief.inputSchema.properties, key), false, key);
});
