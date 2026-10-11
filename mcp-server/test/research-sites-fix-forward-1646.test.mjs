// THE RESEARCH-SITE FINDINGS OF THE #1646 POST-MERGE BLOCK (2026-10-11), each
// pinned by the reviewer's own reproduction run as behaviour, not SQL shape:
//
//   P1 7  a credential-bearing start url was accepted, inserted and written to
//         the audit event (fixed in #1695);
//   P2 5  two adds of the same ABSENT host with different idempotency keys:
//         SELECT ... FOR UPDATE has no row to lock, so the loser's insert
//         raised 23505 instead of returning the winner's row (#1695);
//   P2 6  capture and away, the profiles the weekly research runs use, refused
//         both write verbs (#1702);
//   P2 10 free text went into LIKE unescaped, so "%" and "_" matched every
//         host (#1695).
//
// research-sites.test.mjs and research-sites-profiles.test.mjs carry the wider
// cases. The fake database here models the parts of PostgreSQL the findings
// turn on: a partial unique index on the active host, a concurrent writer's
// uncommitted row that a plain read cannot see, and LIKE with or without an
// escape character.
import test from "node:test";
import assert from "node:assert/strict";
import { TOOLS, ToolError } from "../src/tools.js";
import { allowedIn, callTool } from "../src/mcp.js";

const machine = { id: "10000000-0000-0000-0000-000000000010", slug: "joe-local", human: false,
  via: "local-token", native_agent_verified: true, sponsoring_human_slug: "joe", client_id: "local-verb" };
const joe = { id: "10000000-0000-0000-0000-000000000002", slug: "joe", display: "Joe",
  human: true, via: "oauth", client_id: "claude-ai" };
const WRITES = ["add-research-site", "remove-research-site"];
const WINNER = { id: "30000000-0000-0000-0000-0000000016a1", host: "research.example.org",
  topics: ["npi"], url: null, note: null, removed_at: null };

const flat = text => String(text).replace(/\s+/g, " ").trim();

// The loser's connection while a concurrent add of the same host is in flight.
// The winner's row exists but is uncommitted: a plain read does not see it and
// a FOR UPDATE read finds no row to lock. An insert that meets it on the active
// host index waits for the commit, then does nothing under ON CONFLICT or
// raises 23505 without it. After that wait the row is committed and visible.
class RaceDb {
  constructor() { this.committed = false; this.calls = []; }
  async query(text, params = []) {
    const sql = flat(text);
    this.calls.push([sql, params]);
    if (/^insert into research_site\b/.test(sql)) {
      this.committed = true;
      if (/on conflict/.test(sql)) return { rows: [] };
      throw Object.assign(new Error('duplicate key value violates unique constraint "research_site_active_host"'),
        { code: "23505" });
    }
    if (/^select .* from research_site where host=\$1/.test(sql))
      return { rows: this.committed && params[0] === WINNER.host ? [WINNER] : [] };
    return { rows: [] };
  }
}

// LIKE as PostgreSQL evaluates it: % and _ are wildcards unless the statement
// names an escape character, which then makes the next character literal.
function like(value, pattern, escape) {
  let re = "";
  for (let i = 0; i < pattern.length; i++) {
    const ch = pattern[i];
    if (escape && ch === escape && i + 1 < pattern.length) re += pattern[++i].replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
    else if (ch === "%") re += "[\\s\\S]*";
    else if (ch === "_") re += "[\\s\\S]";
    else re += ch.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
  }
  return new RegExp(`^${re}$`).test(value);
}

class IndexDb {
  constructor(rows) { this.rows = rows; }
  async query(text, params = []) {
    const sql = flat(text);
    if (!/from research_site s/.test(sql)) return { rows: [] };
    const escape = /like \$1 escape '\\'/.test(sql) ? "\\" : null;
    return { rows: this.rows.filter(r => [r.host, (r.note || "").toLowerCase(), r.topics.join(" ")]
      .some(v => like(v, params[0], escape))) };
  }
}

class RecordingDb {
  constructor() { this.calls = []; }
  async query(text, params = []) { this.calls.push([flat(text), params]); return { rows: [] }; }
}

test("P2 5: the loser of a concurrent add returns the winner's row, not a unique violation", async () => {
  const db = new RaceDb();
  const out = await TOOLS["add-research-site"].handler(db, machine, {
    idempotency_key: "00000000-0000-4000-8000-0000000016a2", host: "research.example.org", topics: ["npi"] });
  assert.equal(out.ok, true);
  assert.equal(out.already_listed, true);
  assert.equal(out.id, WINNER.id);
  // The winner wrote the one audit event; the loser inserted nothing and writes none.
  assert.ok(!db.calls.some(([sql]) => /^insert into event\b/.test(sql)), "the loser wrote an audit event");
});

// Synthetic values only: none of these is a real credential.
const CREDENTIAL_URLS = [
  "https://reviewer:synthetic-pw@research.example.org/tables",
  "https://research.example.org/tables?token=synthetic-value",
  "https://research.example.org/tables?api_key=synthetic-value",
];

test("P1 7: a credential-bearing start url is refused before any SQL and never echoed", async () => {
  for (const url of CREDENTIAL_URLS) {
    const db = new RecordingDb();
    const error = await TOOLS["add-research-site"].handler(db, machine, {
      idempotency_key: "00000000-0000-4000-8000-0000000016a3", host: "research.example.org",
      topics: ["npi"], url }).then(() => null, e => e);
    assert.ok(error instanceof ToolError, `${url} was accepted`);
    assert.equal(db.calls.length, 0, `${url} reached SQL`);
    assert.ok(!JSON.stringify(error.payload).includes("synthetic"), `${url} echoed in the error`);
  }
  // A refusal for another reason does not echo the url either.
  const other = await TOOLS["add-research-site"].handler(new RecordingDb(), machine, {
    idempotency_key: "00000000-0000-4000-8000-0000000016a4", host: "research.example.org", topics: ["npi"],
    url: "https://elsewhere.example/tables?token=synthetic-value" }).then(() => null, e => e);
  assert.ok(other instanceof ToolError);
  assert.ok(!JSON.stringify(other.payload).includes("synthetic"), "the different-host refusal echoed the url");
});

test("P1 7: a public start url still reaches the insert as given", async () => {
  const db = new RecordingDb();
  db.query = async (text, params = []) => {
    const sql = flat(text);
    db.calls.push([sql, params]);
    return { rows: /^insert into research_site\b/.test(sql)
      ? [{ id: WINNER.id, host: WINNER.host, added_at: "2026-10-11T12:00:00Z" }] : [] };
  };
  const url = "https://research.example.org/tables?keyword=office&page=2";
  const out = await TOOLS["add-research-site"].handler(db, machine, {
    idempotency_key: "00000000-0000-4000-8000-0000000016a5", host: "research.example.org", topics: ["npi"], url });
  assert.equal(out.ok, true);
  assert.equal(db.calls.find(([sql]) => /^insert into research_site\b/.test(sql))[1][1], url);
});

test("P2 10: free-text search matches % and _ literally, and ordinary text as a substring", async () => {
  const rows = [{ host: "research.example.org", note: "county tables", topics: ["npi"] },
    { host: "rate_100pct.example.org", note: "50% off", topics: ["cre-market"] }];
  const hosts = async text => (await TOOLS["list-research-sites"].handler(new IndexDb(rows), joe, { text }))
    .sites.map(s => s.host);
  assert.deepEqual(await hosts("%"), ["rate_100pct.example.org"]);
  assert.deepEqual(await hosts("_"), ["rate_100pct.example.org"]);
  assert.deepEqual(await hosts("County"), ["research.example.org"]);
  assert.deepEqual(await hosts("example"), ["research.example.org", "rate_100pct.example.org"]);
});

const DISPATCH_ARGS = {
  "add-research-site": { idempotency_key: "00000000-0000-4000-8000-0000000016a6",
    host: "research.example.org", topics: ["npi"] },
  "remove-research-site": { idempotency_key: "00000000-0000-4000-8000-0000000016a7",
    host: "research.example.org", reason: "dead source" },
};

test("P2 6: capture and away dispatch both write verbs past the profile gate", async () => {
  for (const profile of ["capture", "away"])
    for (const name of WRITES) {
      assert.equal(allowedIn(profile, name, TOOLS[name]), true, `${name} under ${profile}`);
      // No database here, so the call fails later; only not_in_profile is the defect.
      const payload = await callTool({}, joe, name, { ...DISPATCH_ARGS[name] }, profile)
        .then(() => null, e => e instanceof ToolError ? e.payload : { error: "non_tool_error" });
      assert.notEqual(payload?.error, "not_in_profile", `${name} under ${profile}`);
    }
});
