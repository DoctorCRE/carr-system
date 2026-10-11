// THE RESEARCH-SITE INDEX (Joe, 2026-10-07): an index of useful sources that
// research checks first, then goes beyond on the open web. Not a permission list.
//
// What this pins:
//   1. Every verb is open to every caller — machine and session identities
//      included. Joe ruled he does not want to be involved in adding sites.
//   2. Hosts and topic tags are normalised and validated before SQL.
//   3. Adding a listed host is a no-op that returns the existing row; removal is
//      soft and records who and why.
//   4. The read filters by topic tag or free text and reminds the caller the
//      index is a starting point.
import test from "node:test";
import assert from "node:assert/strict";
import { TOOLS, ToolError, executeRegisteredTool } from "../src/tools.js";
import { RESEARCH_SITE_WRITE_VERBS, normalizeResearchHost, normalizeTopics, startUrl } from "../src/research-sites.js";

const joe = { id: "10000000-0000-0000-0000-000000000002", slug: "joe", display: "Joe",
  human: true, via: "oauth", client_id: "claude-ai" };
const joeLocal = { id: "10000000-0000-0000-0000-000000000010", slug: "joe-local", human: false,
  via: "local-token", native_agent_verified: true, sponsoring_human_slug: "joe", client_id: "local-verb" };

class Fake {
  constructor(plan = {}) { this.plan = plan; this.calls = []; }
  async query(text, params = []) {
    const sql = String(text).replace(/\s+/g, " ").trim();
    this.calls.push([sql, params]);
    for (const [fragment, value] of Object.entries(this.plan))
      if (sql.includes(fragment)) return { rows: typeof value === "function" ? value(params) : value };
    return { rows: [] };
  }
}

test("the index verbs are registered and none is human-only", () => {
  for (const name of ["add-research-site", "remove-research-site", "list-research-sites"]) {
    assert.ok(TOOLS[name], name);
    assert.notEqual(TOOLS[name].humanOnly, true, name);
    assert.notEqual(TOOLS[name].authorityOnly, true, name);
  }
  assert.equal(TOOLS["add-research-site"].write, true);
  assert.equal(TOOLS["remove-research-site"].write, true);
  assert.notEqual(TOOLS["list-research-sites"].write, true);
  assert.deepEqual([...RESEARCH_SITE_WRITE_VERBS].sort(), ["add-research-site", "remove-research-site"]);
  assert.deepEqual(TOOLS["add-research-site"].inputSchema.required, ["idempotency_key", "host", "topics"]);
  assert.deepEqual(TOOLS["remove-research-site"].inputSchema.required, ["idempotency_key", "host", "reason"]);
  assert.match(TOOLS["list-research-sites"].description, /open internet/);
});

test("a machine identity passes the dispatcher into add-research-site", async () => {
  // Empty args: a refusal must come from argument validation, never from identity.
  const error = await executeRegisteredTool({ query: async () => ({ rows: [] }) }, joeLocal,
    "add-research-site", {}).then(() => null, e => e);
  assert.notEqual(error?.payload?.error, "human_only_verb_requires_verified_partner");
});

test("hosts normalise to exact public hostnames and everything else is refused", () => {
  assert.equal(normalizeResearchHost("WWW.CBRE.com."), "www.cbre.com");
  for (const bad of ["https://www.cbre.com", "www.cbre.com/path", "*.cbre.com", "cbre", "10.0.0.1",
    "www.cbre.com:8443", "user@cbre.com", "cbre..com", "", "printer.local", null]) {
    assert.throws(() => normalizeResearchHost(bad),
      e => e instanceof ToolError && e.payload.error === "research_site_host_invalid", String(bad));
  }
});

test("topic tags normalise and are validated", () => {
  assert.deepEqual(normalizeTopics(["CRE Market", "npi", "npi"]), ["cre-market", "npi"]);
  for (const bad of [[], ["bad/tag"], [""], Array(13).fill(0).map((_, i) => `t${i}`)])
    assert.throws(() => normalizeTopics(bad), e => e.payload.error === "research_site_topics_invalid");
  assert.throws(() => normalizeTopics(undefined), e => e.payload.error === "research_site_topics_required");
});

test("a machine identity adds a site: one insert, one audit event, the actor recorded", async () => {
  const fake = new Fake({
    "insert into research_site": [{ id: "30000000-0000-0000-0000-000000000001", host: "data.example.org",
      added_at: "2026-10-07T12:00:00Z" }],
  });
  const out = await TOOLS["add-research-site"].handler(fake, joeLocal, {
    idempotency_key: "site-add-1", host: "Data.Example.org", topics: ["Demographics"],
    url: "https://data.example.org/tables", note: "county tables",
  });
  assert.equal(out.ok, true);
  assert.deepEqual(out.topics, ["demographics"]);
  const insert = fake.calls.find(([sql]) => sql.includes("insert into research_site"));
  assert.deepEqual(insert[1], ["data.example.org", "https://data.example.org/tables", ["demographics"],
    "county tables", joeLocal.id]);
  assert.ok(fake.calls.some(([sql]) => sql.includes("insert into event")), "no audit event");
});

test("a start url on a different host is refused before SQL", async () => {
  const fake = new Fake();
  await assert.rejects(() => TOOLS["add-research-site"].handler(fake, joe, {
    idempotency_key: "site-add-url", host: "www.cbre.com", topics: ["cre-market"], url: "https://evil.example/x",
  }), e => e.payload.error === "research_site_url_invalid");
  assert.equal(fake.calls.length, 0);
});

test("adding a host already in the index returns the row without writing", async () => {
  const fake = new Fake({ "from research_site where host=$1 and removed_at is null":
    [{ id: "30000000-0000-0000-0000-000000000001", host: "www.cbre.com", topics: ["cre-market"] }] });
  const out = await TOOLS["add-research-site"].handler(fake, joe, {
    idempotency_key: "site-add-dup", host: "www.cbre.com", topics: ["cre-market"] });
  assert.equal(out.already_listed, true);
  assert.ok(!fake.calls.some(([sql]) => sql.includes("insert into event")), "a no-op add wrote an audit event");
});

// ── Fix-forward of the #1646 post-merge review (P1 #7, P2 #5, P2 #10) ─────────
// Synthetic values only: none of these is a real credential.
const CREDENTIAL_URLS = [
  "https://reviewer:synthetic-pw@data.example.org/tables",
  "https://reviewer@data.example.org/tables",
  "https://data.example.org/tables?token=synthetic-value",
  "https://data.example.org/tables?api_key=synthetic-value",
  "https://data.example.org/tables?page=2&X-Amz-Signature=synthetic-value",
  "https://data.example.org/tables?client_secret=synthetic-value",
  "https://data.example.org/tables?password=synthetic-value",
  "https://data.example.org/tables?sig=synthetic-value",
  "https://data.example.org/tables#access_token=synthetic-value",
];

test("a credential-bearing start url is refused before SQL and never echoed", async () => {
  for (const url of CREDENTIAL_URLS) {
    const fake = new Fake();
    const error = await TOOLS["add-research-site"].handler(fake, joeLocal, {
      idempotency_key: "site-add-cred", host: "data.example.org", topics: ["demographics"], url,
    }).then(() => null, e => e);
    assert.equal(error?.payload?.error, "research_site_url_credential", url);
    assert.equal(fake.calls.length, 0, `${url} reached SQL`);
    assert.ok(!JSON.stringify(error.payload).includes("synthetic"), `${url} echoed in the error`);
  }
  // The same-host check refuses without echoing the url either.
  const other = await TOOLS["add-research-site"].handler(new Fake(), joe, {
    idempotency_key: "site-add-other", host: "www.cbre.com", topics: ["cre-market"],
    url: "https://evil.example/x?token=synthetic-value" }).then(() => null, e => e);
  assert.ok(!JSON.stringify(other.payload).includes("synthetic"));
  // A credential pasted as the host is not echoed.
  assert.throws(() => normalizeResearchHost("reviewer:synthetic-pw@data.example.org"),
    e => e.payload.error === "research_site_host_invalid" && e.payload.host === null);
});

// PR 1695 round 2: camelCase and run-together credential names (S3 v2 pre-signed
// AWSAccessKeyId among them) were accepted because the name was split only on
// - _ and . — they are matched on the lowercased, separator-stripped name now.
const CAMEL_CREDENTIAL_NAMES = ["accessToken", "apiToken", "authToken", "privateToken",
  "clientSecret", "AWSAccessKeyId", "accesstoken", "ApiKey", "x-apiKey"];

test("a camelCase credential field in a start url is refused", async () => {
  for (const name of CAMEL_CREDENTIAL_NAMES) {
    for (const url of [`https://data.example.org/tables?page=2&${name}=synthetic-value`,
      `https://data.example.org/tables#${name}=synthetic-value`]) {
      const fake = new Fake();
      const error = await TOOLS["add-research-site"].handler(fake, joeLocal, {
        idempotency_key: "site-add-camel", host: "data.example.org", topics: ["demographics"], url,
      }).then(() => null, e => e);
      assert.equal(error?.payload?.error, "research_site_url_credential", url);
      assert.equal(fake.calls.length, 0, `${url} reached SQL`);
      assert.ok(!JSON.stringify(error.payload).includes("synthetic"), `${url} echoed in the error`);
    }
  }
});

// PR 1695 round 3: a key name ending in a digit, PHP session ids, passcodes,
// short pw fields and one-time / SSO values (OAuth code, otp, CAS ticket,
// SAMLResponse) were accepted.
const ROUND3_CREDENTIAL_NAMES = ["apiKey1", "key2", "api_key2", "X-Api-Key2", "keys3",
  "PHPSESSID", "sessid", "passcode", "pw", "code", "otp", "ticket", "SAMLResponse"];

test("a digit-suffixed key, session id, passcode or one-time value in a start url is refused", () => {
  for (const name of ROUND3_CREDENTIAL_NAMES) {
    for (const url of [`https://data.example.org/tables?page=2&${name}=synthetic-value`,
      `https://data.example.org/tables#${name}=synthetic-value`]) {
      const error = (() => { try { startUrl(url, "data.example.org"); return null; } catch (e) { return e; } })();
      assert.equal(error?.payload?.error, "research_site_url_credential", url);
      assert.ok(!JSON.stringify(error.payload).includes("synthetic"), `${url} echoed in the error`);
    }
  }
});

test("ordinary words that contain a credential word stay acceptable start url fields", () => {
  for (const name of ["tokenizer", "tokenize", "tokenizers", "turnkey", "hotkey",
    "keyword", "keywords", "monkey", "author", "authors", "zipcode"]) {
    const url = `https://data.example.org/tables?${name}=office&page=2`;
    assert.equal(startUrl(url, "data.example.org"), url, name);
  }
});

test("a public start url with ordinary query fields is stored as given", async () => {
  for (const url of ["https://data.example.org/tables?keyword=office&page=2",
    "https://data.example.org/monkey-report?zipcode=36601", "http://data.example.org/",
    // Words that merely contain a credential word stay acceptable field names.
    "https://data.example.org/tables?monkey=1&sortOrder=asc",
    "https://data.example.org/tables?searchKeywords=office&author=cbre"]) {
    const fake = new Fake({ "insert into research_site": [{ id: "30000000-0000-0000-0000-000000000002",
      host: "data.example.org", added_at: "2026-10-11T12:00:00Z" }] });
    const out = await TOOLS["add-research-site"].handler(fake, joeLocal, {
      idempotency_key: "site-add-public", host: "data.example.org", topics: ["demographics"], url });
    assert.equal(out.ok, true, url);
    assert.equal(fake.calls.find(([sql]) => sql.includes("insert into research_site"))[1][1], url);
  }
});

test("a concurrent add of the same absent host returns the winner's row, not a unique violation", async () => {
  // The loser's insert hits the active-host index and does nothing; it must
  // then read the committed winner and write no audit event of its own.
  const fake = new Fake({ "select id, host, topics, url, note from research_site":
    [{ id: "30000000-0000-0000-0000-000000000003", host: "data.example.org", topics: ["demographics"] }] });
  const out = await TOOLS["add-research-site"].handler(fake, joeLocal, {
    idempotency_key: "site-add-race-2", host: "data.example.org", topics: ["demographics"] });
  assert.equal(out.already_listed, true);
  assert.equal(out.id, "30000000-0000-0000-0000-000000000003");
  const insert = fake.calls.find(([sql]) => sql.includes("insert into research_site"));
  assert.match(insert[0], /on conflict \(host\) where removed_at is null do nothing/);
  assert.ok(!fake.calls.some(([sql]) => sql.includes("insert into event")));
  assert.ok(!fake.calls.some(([sql]) => /for update/.test(sql)));
});

test("free-text search treats % and _ literally", async () => {
  for (const [input, pattern] of [["%", "%\\%%"], ["_", "%\\_%"], ["a\\b", "%a\\\\b%"], ["Office", "%office%"]]) {
    const fake = new Fake();
    await TOOLS["list-research-sites"].handler(fake, joe, { text: input });
    const [sql, params] = fake.calls[0];
    assert.deepEqual(params, [pattern], input);
    assert.equal((sql.match(/like \$1 escape '\\'/g) || []).length, 3, sql);
  }
});

test("any caller removes a site softly, recording who and why", async () => {
  const fake = new Fake({ "update research_site set removed_at": [{ id: "30000000-0000-0000-0000-000000000001",
    host: "www.cbre.com", removed_at: "2026-10-07T13:00:00Z" }] });
  const out = await TOOLS["remove-research-site"].handler(fake, joeLocal, {
    idempotency_key: "site-remove-1", host: "www.cbre.com", reason: "moved" });
  assert.equal(out.ok, true);
  const update = fake.calls.find(([sql]) => sql.includes("update research_site set removed_at"));
  assert.ok(update[0].includes("removed_at is null"));
  assert.deepEqual(update[1], ["www.cbre.com", joeLocal.id, "moved"]);
  assert.ok(!fake.calls.some(([sql]) => /delete from research_site/.test(sql)));
});

test("removing an unlisted host says so", async () => {
  await assert.rejects(() => TOOLS["remove-research-site"].handler(new Fake(), joe, {
    idempotency_key: "site-remove-missing", host: "not-listed.example", reason: "cleanup",
  }), e => e.payload.error === "research_site_not_listed");
});

test("the read filters by topic and text and reminds the caller to search beyond it", async () => {
  const fake = new Fake({ "from research_site s": [{ host: "www.cbre.com", topics: ["cre-market"] }],
    "unnest(topics)": [{ topic: "cre-market" }, { topic: "npi" }] });
  const out = await TOOLS["list-research-sites"].handler(fake, joe, { topic: "CRE Market", text: "Office" });
  const [sql, params] = fake.calls[0];
  assert.ok(sql.includes("removed_at is null"));
  assert.ok(sql.includes("s.topics @> array[$1]::text[]"));
  assert.deepEqual(params, ["cre-market", "%office%"]);
  assert.deepEqual(out.topics_in_use, ["cre-market", "npi"]);
  assert.match(out.reminder, /open internet/);
  const all = new Fake();
  await TOOLS["list-research-sites"].handler(all, joe, { include_removed: true });
  assert.ok(!all.calls[0][0].includes("removed_at is null"));
});
