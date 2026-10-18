import test from "node:test";
import assert from "node:assert/strict";
import {
  MAX_PAGE, MAX_QUERY_LENGTH, PAGE_SIZE, hasControlCharacter, isBusinessApiPath, likeTerm, parseBusinessApiPath,
  parseBusinessQuery, parseBusinessRecordQuery, partialSignal, readBusinessList, readBusinessRecord,
} from "../src/workspace-business-read.js";

const JOE = { slug: "joe" };
const DELL = { slug: "dell" };
const ID = "3f1a2b3c-4d5e-6f70-8192-a3b4c5d6e7f8";

const clientRow = (overrides = {}) => ({
  id: ID, ref: "C-101", name: "Ridgeline Dental", party_kind: "org", city: "Ocala", state: "FL",
  recorded_status: "active", recorded_status_label: "Active", recorded_status_active_pipeline: true,
  recorded_etl_status: "signed", recorded_client_type: "practice", recorded_client_type_label: "Practice",
  vertical: "dental", owner_label: "Joe", owned_by_viewer: true, updated_at: "2026-09-01T12:00:00.000Z",
  ...overrides,
});

const vendorRow = (overrides = {}) => ({
  id: ID, ref: "V-CPA-006", name: "Gulfside CPA", party_kind: "org", city: "Tampa", state: "FL",
  recorded_category: "cpa", recorded_category_label: "CPA", recorded_stage: "warm", recorded_stage_label: "Warm",
  recorded_disposition: "active", recorded_disposition_label: "Active", recorded_disposition_workable: true,
  relationship_level: 3, relationship_level_label: "Trusted", referral_active: true, is_target: false,
  out_of_market: false, last_touch: "2026-08-30", owner_label: "Dell", owned_by_viewer: false,
  updated_at: "2026-09-02T09:30:00.000Z", ...overrides,
});

const CLIENT_FACETS = { statuses: [{ slug: "active", label: "Active", is_active_pipeline: true }], types: [{ slug: "practice", label: "Practice" }] };
const VENDOR_FACETS = { categories: [{ slug: "cpa", label: "CPA" }], stages: [{ slug: "warm", label: "Warm" }], dispositions: [{ slug: "active", label: "Active", workable: true }] };

function fakeClient({ head = {}, facets = CLIENT_FACETS, fail = null } = {}) {
  const queries = [];
  return {
    queries,
    async query(text, params) {
      queries.push({ text, params });
      if (fail) throw fail;
      if (text.startsWith("with filtered")) {
        return { rows: [{ total_count: 1, viewer_owner_resolved: true, rows: [clientRow()], record: clientRow(), match_count: 1, ...head }] };
      }
      return { rows: [facets] };
    },
  };
}

const listQuery = (overrides = {}) => parseBusinessQuery("clients", new URLSearchParams(overrides), "joe");
// The fixed clock the time-sensitive tests in this file already use, named once
// so a test that does not care about time still reads deterministically.
const CLOCK = () => new Date("2026-09-10T15:00:00.000Z");

// Relations as the repository's grant audit reads them: whatever follows FROM
// or JOIN. The two CTE names are not relations and are excluded by name.
const CTE_NAMES = new Set(["filtered", "ordered", "t", "lateral", "jsonb_to_recordset"]);
const BASE_RELATIONS = ["client", "client_status", "client_type", "party", "vendor", "vendor_category",
  "vendor_disposition", "vendor_relationship_level", "vendor_stage", "actor", "activity", "party_link", "deal"];
function relationsIn(sql) {
  return [...sql.matchAll(/\b(?:from|join)\s+([A-Za-z_][A-Za-z0-9_.]*)/g)]
    .map((match) => match[1]).filter((name) => !CTE_NAMES.has(name));
}
// The predicate block alone: the owner subquery also appears inside the row
// payload (as the `owned_by_viewer` flag), and only the WHERE decides the set.
const whereOf = (text) => text.split("\n     where ")[1].split(/\n {2}\)|\n {5}limit/)[0];

test("the API path parser admits exactly the two datasets and a uuid record", () => {
  assert.deepEqual(parseBusinessApiPath("/api/v1/business/clients"), { dataset: "clients", id: null });
  assert.deepEqual(parseBusinessApiPath("/api/v1/business/vendors"), { dataset: "vendors", id: null });
  assert.deepEqual(parseBusinessApiPath(`/api/v1/business/clients/${ID}`), { dataset: "clients", id: ID });
  for (const path of ["/api/v1/business/leads", "/api/v1/business/clients/not-a-uuid", "/api/v1/business/clients/a/b",
    "/api/v1/business", "/api/v1/command-center", "/api/v1/workspace/command-center", "/api/v1/business/clients/../vendors"]) {
    assert.equal(parseBusinessApiPath(path), null, path);
    assert.equal(isBusinessApiPath(path), false, path);
  }
});

test("every base relation this module reads is schema-qualified, in every statement it can emit", async () => {
  // The repository's grant audit recognises a relation only by its qualified
  // name, so an unqualified `from client` makes this module look grant-clean
  // while it quietly depends on ten relations nobody checked. This test walks
  // every statement shape the module can produce — both datasets, both scopes,
  // list and record, page and facets — and holds the whole dependency list.
  const clientsTeam = fakeClient();
  await readBusinessList({ client: clientsTeam, actor: JOE, query: listQuery(), correlationId: "corr-q1", now: CLOCK });
  // Page two exists here to exercise the offset, so its fixture has to be a
  // page two that could exist: a single row at offset 25 needs a total of at
  // least 26. Answering with total 1 was the fixture describing an impossible
  // page, and readBusinessList was right to refuse it — that invariant is the
  // one keeping a page from reaching past the total it was counted with, and it
  // stays exactly as it is.
  const clientsMine = fakeClient({ head: { total_count: 30 } });
  await readBusinessList({ client: clientsMine, actor: JOE, query: listQuery({ scope: "mine", q: "ridge", status: "active", type: "practice", pipeline: "active", sort: "recent", page: "2" }), correlationId: "corr-q2", now: CLOCK });
  const vendorsTeam = fakeClient({ facets: VENDOR_FACETS, head: { rows: [vendorRow()], total_count: 1 } });
  await readBusinessList({ client: vendorsTeam, actor: DELL, query: parseBusinessQuery("vendors", new URLSearchParams(), "dell"), correlationId: "corr-q3", now: CLOCK });
  const vendorsMine = fakeClient({ facets: VENDOR_FACETS, head: { rows: [vendorRow()], total_count: 1 } });
  await readBusinessList({ client: vendorsMine, actor: DELL, query: parseBusinessQuery("vendors", new URLSearchParams({ scope: "mine", category: "cpa", stage: "warm", disposition: "active", q: "gulf" }), "dell"), correlationId: "corr-q4", now: CLOCK });
  const clientDetail = fakeClient();
  await readBusinessRecord({ client: clientDetail, actor: JOE, dataset: "clients", id: ID, correlationId: "corr-q5", now: CLOCK });
  // A vendor read answers with a vendor record, as the vendor tests below do.
  const vendorDetail = fakeClient({ facets: VENDOR_FACETS, head: { record: vendorRow() } });
  await readBusinessRecord({ client: vendorDetail, actor: JOE, dataset: "vendors", id: ID, correlationId: "corr-q6", now: CLOCK });

  const statements = [clientsTeam, clientsMine, vendorsTeam, vendorsMine, clientDetail, vendorDetail]
    .flatMap((client) => client.queries.map((query) => query.text));
  assert.equal(statements.length, 10, "four list statements plus four facet statements plus two record statements");

  // The exact dependency list, and nothing else appearing unannounced.
  const relations = [...new Set(statements.flatMap(relationsIn))].sort();
  assert.deepEqual(relations, [
    "public.activity", "public.deal", "public.party_link", "public.party_link_deal", "public.actor", "public.client", "public.client_status", "public.client_type", "public.party",
    "public.vendor", "public.vendor_category", "public.vendor_disposition",
    "public.vendor_relationship_level", "public.vendor_stage",
  ].sort());
  // And not one of them is reachable in its bare form anywhere.
  for (const sql of statements) {
    for (const name of BASE_RELATIONS) {
      assert.doesNotMatch(sql, new RegExp(`(?:from|join)\\s+${name}\\b`), `${name} is unqualified`);
    }
  }
  // Qualifying changed the names, not the shape: parameters and predicates hold.
  assert.deepEqual(clientsTeam.queries[0].params, ["joe", PAGE_SIZE, 0]);
  assert.deepEqual(vendorsMine.queries[0].params, ["dell", "%gulf%", "cpa", "warm", "active", PAGE_SIZE, 0]);
  assert.match(whereOf(clientsMine.queries[0].text), /c\.owner_id = \(select a\.id from public\.actor a where a\.slug = \$1::text\)/);
  assert.match(whereOf(vendorDetail.queries[0].text), /v\.merged_into is null/);
});

test("the query parser bounds every filter and refuses anything it does not own", () => {
  const defaults = listQuery();
  assert.deepEqual(defaults, { dataset: "clients", scope: "team", q: null, owner: "all", territory: null, sort: "name", page: 1, page_size: PAGE_SIZE, status: null, type: null, pipeline: "any" });
  assert.equal(parseBusinessQuery("vendors", new URLSearchParams({ scope: "mine", stage: "warm" }), "joe").stage, "warm");
  // An owner selector does not exist on this wire at all; "mine" is the only
  // way to ask for a personal list and it resolves server-side.
  for (const params of [{ owner: "other" }, { owner_id: ID }, { limit: "500" }, { offset: "10" }, { tenant: "other" }]) {
    assert.throws(() => listQuery(params), /QUERY_INVALID/, JSON.stringify(params));
  }
  assert.throws(() => listQuery({ viewer: "dell" }), /AUTHORIZATION_REFUSED/);
  assert.equal(listQuery({ viewer: "joe" }).scope, "team");
  assert.throws(() => listQuery({ scope: "everyone" }), /QUERY_INVALID/);
  assert.throws(() => listQuery({ sort: "owner" }), /QUERY_INVALID/);
  assert.throws(() => listQuery({ pipeline: "warm-ish" }), /QUERY_INVALID/);
  assert.throws(() => listQuery({ page: "0" }), /QUERY_INVALID/);
  assert.throws(() => listQuery({ page: "-1" }), /QUERY_INVALID/);
  assert.throws(() => listQuery({ page: String(MAX_PAGE + 1) }), /QUERY_INVALID/);
  assert.throws(() => listQuery({ status: "act ive" }), /QUERY_INVALID/);
  assert.throws(() => listQuery({ status: "'; drop table client; --" }), /QUERY_INVALID/);
  assert.throws(() => listQuery({ q: "x".repeat(MAX_QUERY_LENGTH + 1) }), /QUERY_INVALID/);
  assert.equal(listQuery({ q: "  Ridgeline  " }).q, "Ridgeline");
  assert.equal(listQuery({ q: "   " }).q, null);
  assert.equal(listQuery({ status: "any" }).status, null);
  assert.equal(listQuery({ page: String(MAX_PAGE) }).page, MAX_PAGE);
  // A vendor filter is not a client filter, and neither leaks into the other.
  assert.throws(() => listQuery({ category: "cpa" }), /QUERY_INVALID/);
  assert.throws(() => parseBusinessQuery("vendors", new URLSearchParams({ pipeline: "active" }), "joe"), /QUERY_INVALID/);
  assert.throws(() => parseBusinessRecordQuery(new URLSearchParams({ scope: "mine" }), "joe"), /QUERY_INVALID/);
  assert.deepEqual(parseBusinessRecordQuery(new URLSearchParams({ viewer: "joe" }), "joe"), {});
});

test("control characters and like wildcards are data, never syntax", () => {
  assert.equal(hasControlCharacter("Ridgeline"), false);
  assert.equal(hasControlCharacter(`Ridge${String.fromCharCode(0)}line`), true);
  assert.equal(hasControlCharacter(`Ridge${String.fromCharCode(9)}line`), true);
  assert.throws(() => listQuery({ q: `Ridge${String.fromCharCode(27)}line` }), /QUERY_INVALID/);
  assert.equal(likeTerm("100%"), "%100\\%%");
  assert.equal(likeTerm("a_b"), "%a\\_b%");
  assert.equal(likeTerm("back\\slash"), "%back\\\\slash%");
});

test("the list total and the list rows come from one filtered set, with a stable order", async () => {
  const client = fakeClient();
  const result = await readBusinessList({ client, actor: JOE, query: listQuery(), correlationId: "corr-list", now: () => new Date("2026-09-10T15:00:00.000Z") });
  const [page, facets] = client.queries;
  // One predicate, read twice: once whole for the count, once paged for the rows.
  assert.match(page.text, /with filtered as \(/);
  assert.match(page.text, /select \(select count\(\*\) from filtered\)::int as total_count/);
  assert.match(page.text, /from ordered o/);
  // Stable ordering: the same expression drives the window and the page, and id
  // breaks every tie so two equal names never swap between pages.
  assert.match(page.text, /row_number\(\) over \(order by lower\(f\.name\) asc, f\.id asc\)/);
  assert.match(page.text, /order by lower\(f\.name\) asc, f\.id asc\n\s+limit \$2 offset \$3/);
  assert.deepEqual(page.params, ["joe", PAGE_SIZE, 0]);
  // Merged and deleted records are excluded from the counted set itself.
  assert.match(page.text, /c\.merged_into is null/);
  assert.match(page.text, /p\.merged_into is null/);
  assert.match(page.text, /p\.deleted_at is null/);
  assert.doesNotMatch(page.text, /insert |update |delete |truncate /i);
  assert.match(facets.text, /from public\.client_status/);
  assert.match(facets.text, /from public\.client_type/);
  assert.equal(result.total, 1);
  assert.equal(result.page, 1);
  assert.equal(result.page_size, PAGE_SIZE);
  assert.equal(result.page_count, 1);
  assert.equal(result.out_of_range, false);
  assert.equal(result.viewer, "joe");
  assert.equal(result.source.source, "client");
  assert.equal(result.source.valid_until, "2026-09-10T15:01:00.000Z");
  assert.deepEqual(result.facets, CLIENT_FACETS);
  // The status-provenance sentence stays plain and still refuses the claim.
  assert.match(result.recorded_field_note, /what someone entered on this record/);
  assert.match(result.recorded_field_note, /not proof that an agreement was signed/);
  assert.deepEqual(result.query, listQuery());
});

test("every search branch binds the search term, and never the authenticated slug", async () => {
  // A reference-only match is the case that catches a branch bound to $1: the
  // name misses, so the row can only come back if the ref branch carries the term.
  const clients = fakeClient();
  await readBusinessList({ client: clients, actor: JOE, query: listQuery({ q: "C-101" }), correlationId: "corr-ref" });
  const clientBranches = whereOf(clients.queries[0].text).match(/ilike \$\d+/g);
  assert.deepEqual(clientBranches, ["ilike $2", "ilike $2", "ilike $2"]);
  assert.deepEqual(clients.queries[0].params, ["joe", "%C-101%", PAGE_SIZE, 0]);

  const vendors = fakeClient({ facets: VENDOR_FACETS, head: { rows: [vendorRow()], total_count: 1 } });
  await readBusinessList({ client: vendors, actor: DELL, query: parseBusinessQuery("vendors", new URLSearchParams({ q: "V-CPA-006" }), "dell"), correlationId: "corr-ref-v" });
  const vendorBranches = whereOf(vendors.queries[0].text).match(/ilike \$\d+/g);
  assert.deepEqual(vendorBranches, ["ilike $2", "ilike $2", "ilike $2"]);
  assert.deepEqual(vendors.queries[0].params, ["dell", "%V-CPA-006%", PAGE_SIZE, 0]);
  // $1 stays the actor slug in both, which is what the owner subquery reads.
  assert.doesNotMatch(whereOf(vendors.queries[0].text), /ilike \$1/);
});

test("recent sort orders by the recorded update time and still tie-breaks on id", async () => {
  const client = fakeClient();
  await readBusinessList({ client, actor: JOE, query: listQuery({ sort: "recent" }), correlationId: "corr-sort" });
  assert.match(client.queries[0].text, /order by f\.updated_at desc, f\.id asc/);
});

test("team is owner-independent and My work binds to the authenticated owner uuid only", async () => {
  const team = fakeClient();
  await readBusinessList({ client: team, actor: DELL, query: listQuery(), correlationId: "corr-team" });
  assert.doesNotMatch(whereOf(team.queries[0].text), /owner_id/);
  assert.equal(team.queries[0].params[0], "dell");

  const mine = fakeClient();
  await readBusinessList({ client: mine, actor: DELL, query: listQuery({ scope: "mine" }), correlationId: "corr-mine" });
  const predicate = whereOf(mine.queries[0].text);
  assert.match(predicate, /c\.owner_id = \(select a\.id from public\.actor a where a\.slug = \$1::text\)/);
  // The owner never comes from anywhere but $1, and $1 is the session slug.
  assert.equal(mine.queries[0].params[0], "dell");
  assert.doesNotMatch(predicate, /owner_label\s*=/);
});

test("an unresolvable viewer owner refuses My work instead of rendering an empty personal list", async () => {
  const query = listQuery({ scope: "mine" });
  const client = fakeClient({ head: { viewer_owner_resolved: false, rows: [], total_count: 0 } });
  await assert.rejects(() => readBusinessList({ client, actor: JOE, query, correlationId: "corr-owner" }), /VIEWER_OWNER_UNKNOWN/);
  // The same unresolved owner does not disturb the team view, which never uses it.
  const team = fakeClient({ head: { viewer_owner_resolved: false } });
  const result = await readBusinessList({ client: team, actor: JOE, query: listQuery(), correlationId: "corr-team-owner" });
  assert.equal(result.total, 1);
});

test("a page past the end reports the true total rather than collapsing to zero", async () => {
  const client = fakeClient({ head: { total_count: 30, rows: [] } });
  const result = await readBusinessList({ client, actor: JOE, query: listQuery({ page: "3" }), correlationId: "corr-page" });
  assert.equal(result.total, 30);
  assert.equal(result.page, 3);
  assert.equal(result.page_count, 2);
  assert.equal(result.out_of_range, true);
  assert.deepEqual(result.rows, []);
  assert.deepEqual(client.queries[0].params, ["joe", PAGE_SIZE, 50]);
});

test("an empty book and an empty filter both report zero without inventing a page", async () => {
  const client = fakeClient({ head: { total_count: 0, rows: [] } });
  const result = await readBusinessList({ client, actor: JOE, query: listQuery({ q: "nobody" }), correlationId: "corr-empty" });
  assert.equal(result.total, 0);
  assert.equal(result.page_count, 1);
  assert.equal(result.out_of_range, false);
  assert.equal(client.queries[0].params[1], "%nobody%");
  assert.match(client.queries[0].text, /p\.name ilike \$2 escape/);
  assert.match(client.queries[0].text, /c\.roster_ref ilike \$2 escape/);
});

test("counts that disagree with their own rows are refused, never published", async () => {
  const impossible = [
    { total_count: null }, { total_count: "many" }, { total_count: -1 },
    { rows: null }, { rows: undefined }, { rows: { not: "an array" } },
    { total_count: 1, rows: Array.from({ length: PAGE_SIZE + 1 }, () => clientRow()) },
    { total_count: 1, rows: [clientRow(), clientRow()] },
  ];
  for (const head of impossible) {
    const client = fakeClient({ head });
    await assert.rejects(() => readBusinessList({ client, actor: JOE, query: listQuery(), correlationId: "corr-bad" }), /FRESHNESS_UNKNOWN/, JSON.stringify(Object.keys(head)));
  }
  // Rows on page two cannot reach past a total that only covers page one.
  const short = fakeClient({ head: { total_count: 10, rows: [clientRow()] } });
  await assert.rejects(() => readBusinessList({ client: short, actor: JOE, query: listQuery({ page: "2" }), correlationId: "corr-short" }), /FRESHNESS_UNKNOWN/);
});

test("pipeline filters state the three recorded possibilities and never fold unknown into false", async () => {
  const cases = { active: /cs\.is_active_pipeline = true/, other: /cs\.is_active_pipeline = false/, unknown: /cs\.is_active_pipeline is null/ };
  for (const [pipeline, expected] of Object.entries(cases)) {
    const client = fakeClient();
    await readBusinessList({ client, actor: JOE, query: listQuery({ pipeline }), correlationId: `corr-${pipeline}` });
    assert.match(client.queries[0].text, expected);
  }
  const any = fakeClient();
  await readBusinessList({ client: any, actor: JOE, query: listQuery(), correlationId: "corr-any" });
  assert.doesNotMatch(any.queries[0].text, /is_active_pipeline (=|is)/);
});

test("vendor reads carry their own lookups, filters and live-record predicate", async () => {
  const client = fakeClient({ facets: VENDOR_FACETS, head: { rows: [vendorRow()], total_count: 1, record: vendorRow() } });
  const query = parseBusinessQuery("vendors", new URLSearchParams({ category: "cpa", stage: "warm", disposition: "active", q: "gulf" }), "joe");
  const result = await readBusinessList({ client, actor: JOE, query, correlationId: "corr-vendor" });
  const [page, facets] = client.queries;
  assert.match(page.text, /v\.merged_into is null/);
  assert.match(page.text, /p\.deleted_at is null/);
  assert.match(page.text, /coalesce\(v\.category_slug, v\.category\) = \$3::text/);
  assert.match(page.text, /v\.stage = \$4::text/);
  assert.match(page.text, /v\.disposition = \$5::text/);
  assert.deepEqual(page.params, ["joe", "%gulf%", "cpa", "warm", "active", PAGE_SIZE, 0]);
  assert.match(facets.text, /from public\.vendor_category/);
  assert.match(facets.text, /from public\.vendor_stage/);
  assert.match(facets.text, /from public\.vendor_disposition/);
  assert.equal(result.source.source, "vendor");
  assert.deepEqual(result.facets, VENDOR_FACETS);
  assert.match(result.recorded_field_note, /not proof of a commitment/);
});

test("a recorded code with no lookup label is reported as partial, not relabelled or blanked", () => {
  assert.equal(partialSignal("clients", [clientRow()]), null);
  const partial = partialSignal("clients", [clientRow({ recorded_status_label: null }), clientRow({ recorded_client_type_label: null })]);
  assert.equal(partial.kind, "unlabelled_recorded_codes");
  assert.equal(partial.count, 2);
  assert.deepEqual(partial.fields, ["recorded_client_type", "recorded_status"]);
  // A recorded value that is simply absent is not a partial label; it is unknown.
  assert.equal(partialSignal("clients", [clientRow({ recorded_status: null, recorded_status_label: null })]), null);
  assert.equal(partialSignal("vendors", [vendorRow({ recorded_stage_label: null })]).fields[0], "recorded_stage");
});

test("the partial count is records, not cells, and covers every coded field", () => {
  // REGRESSION: one record with two unnamed codes was counted as two, under a
  // heading that says "records".
  const both = partialSignal("clients", [clientRow({ recorded_status_label: null, recorded_client_type_label: null })]);
  assert.equal(both.count, 1, "one record is one record however many of its codes are unnamed");
  assert.deepEqual(both.fields, ["recorded_client_type", "recorded_status"]);
  const mixed = partialSignal("clients", [
    clientRow({ recorded_status_label: null, recorded_client_type_label: null }),
    clientRow(),
    clientRow({ recorded_status_label: null }),
  ]);
  assert.equal(mixed.count, 2);
  // REGRESSION: relationship_level was the one coded vendor field with no
  // unresolved path, so an unmatched level rendered as a bare number.
  const level = partialSignal("vendors", [vendorRow({ relationship_level_label: null })]);
  assert.equal(level.count, 1);
  assert.deepEqual(level.fields, ["relationship_level"]);
  assert.equal(partialSignal("vendors", [vendorRow({ relationship_level: null, relationship_level_label: null })]), null,
    "a level nobody recorded is unknown, not an unnamed code");
  const everything = partialSignal("vendors", [vendorRow({
    recorded_category_label: null, recorded_stage_label: null, recorded_disposition_label: null, relationship_level_label: null,
  })]);
  assert.equal(everything.count, 1);
  assert.deepEqual(everything.fields, ["recorded_category", "recorded_disposition", "recorded_stage", "relationship_level"]);
});

test("a missing grant or missing source is named as a provisioning gap, and leaks no SQL", async () => {
  // Deployed against a role that cannot see these tables, the first read must
  // not read as a bug in this code — and must not narrate the database.
  const cases = [["42501", "read_access"], ["42P01", "read_source"], ["42703", "read_source"],
    ["3F000", "read_source"], ["3D000", "read_source"], ["28000", "read_credential"], ["28P01", "read_credential"]];
  for (const [pgCode, dependency] of cases) {
    const failure = Object.assign(new Error(`permission denied for table client; select c.id from client c where ...`), { code: pgCode });
    const client = fakeClient({ fail: failure });
    await assert.rejects(
      () => readBusinessList({ client, actor: JOE, query: listQuery(), correlationId: "corr-grant" }),
      (error) => {
        assert.equal(error.code, "DEPENDENCY_NOT_PROVISIONED", pgCode);
        assert.deepEqual(error.detail, { dependency });
        // The class travels; the statement and the driver's words do not.
        assert.doesNotMatch(JSON.stringify(error.detail), /select|from |table|client|permission/i);
        return true;
      });
  }
  // Connection trouble is still ordinary unavailability, and a surprise is
  // still an internal error — the new class does not swallow either.
  await assert.rejects(() => readBusinessList({ client: fakeClient({ fail: Object.assign(new Error("x"), { code: "ECONNRESET" }) }), actor: JOE, query: listQuery(), correlationId: "c" }), /DEPENDENCY_UNAVAILABLE/);
  await assert.rejects(() => readBusinessList({ client: fakeClient({ fail: Object.assign(new Error("x"), { code: "42601" }) }), actor: JOE, query: listQuery(), correlationId: "c" }), /INTERNAL_ERROR/);
  await assert.rejects(() => readBusinessRecord({ client: fakeClient({ fail: Object.assign(new Error("x"), { code: "42501" }) }), actor: JOE, dataset: "vendors", id: ID, correlationId: "c" }), /DEPENDENCY_NOT_PROVISIONED/);
});

test("nullable source fields survive the read as null and are never filled in", async () => {
  const sparse = clientRow({ ref: null, city: null, state: null, recorded_status: null, recorded_status_label: null,
    recorded_status_active_pipeline: null, recorded_etl_status: null, owner_label: null, owned_by_viewer: false });
  const client = fakeClient({ head: { rows: [sparse], total_count: 1 } });
  const result = await readBusinessList({ client, actor: JOE, query: listQuery(), correlationId: "corr-null" });
  assert.equal(result.rows[0].ref, null);
  assert.equal(result.rows[0].recorded_status, null);
  assert.equal(result.rows[0].recorded_etl_status, null);
  assert.equal(result.rows[0].owner_label, null);
  assert.equal(result.partial, null);
});

test("the record read uses the same live-record predicate and refuses a tombstone as not found", async () => {
  const client = fakeClient();
  const result = await readBusinessRecord({ client, actor: JOE, dataset: "clients", id: ID, correlationId: "corr-record", now: () => new Date("2026-09-10T15:00:00.000Z") });
  const [record] = client.queries;
  assert.match(record.text, /c\.merged_into is null/);
  assert.match(record.text, /p\.merged_into is null/);
  assert.match(record.text, /p\.deleted_at is null/);
  assert.match(record.text, /c\.id = \$2::uuid/);
  assert.deepEqual(record.params, ["joe", ID]);
  assert.equal(result.record.id, ID);
  assert.equal(result.source.valid_until, "2026-09-10T15:01:00.000Z");
  assert.deepEqual(result.not_in_this_read, ["assignments", "negotiations", "deals", "email", "documents", "history"]);

  const merged = fakeClient({ head: { record: null } });
  await assert.rejects(() => readBusinessRecord({ client: merged, actor: JOE, dataset: "clients", id: ID, correlationId: "corr-merged" }), /RECORD_NOT_FOUND/);
  await assert.rejects(() => readBusinessRecord({ client, actor: JOE, dataset: "clients", id: "nope", correlationId: "corr-id" }), /QUERY_INVALID/);
  await assert.rejects(() => readBusinessRecord({ client, actor: JOE, dataset: "leads", id: ID, correlationId: "corr-set" }), /QUERY_INVALID/);
});

test("both reads fail closed outside the bound tenant and the two partner actors", async () => {
  const query = listQuery();
  await assert.rejects(() => readBusinessList({ client: fakeClient(), actor: JOE, tenant: "other", query, correlationId: "c" }), /TENANT_SCOPE_REFUSED/);
  for (const actor of [{ slug: "codex" }, { slug: "stranger" }, { slug: "" }, {}, null]) {
    await assert.rejects(() => readBusinessList({ client: fakeClient(), actor, query, correlationId: "c" }), /AUTHORIZATION_REFUSED|TENANT_SCOPE_REFUSED/);
    await assert.rejects(() => readBusinessRecord({ client: fakeClient(), actor, dataset: "clients", id: ID, correlationId: "c" }), /AUTHORIZATION_REFUSED|TENANT_SCOPE_REFUSED/);
  }
  await assert.rejects(() => readBusinessList({ client: fakeClient(), actor: JOE, query }), /INTERNAL_ERROR/);
});

test("database failures stay typed and never become an empty list", async () => {
  const unavailable = fakeClient({ fail: Object.assign(new Error("down"), { code: "ECONNREFUSED" }) });
  await assert.rejects(() => readBusinessList({ client: unavailable, actor: JOE, query: listQuery(), correlationId: "c" }), /DEPENDENCY_UNAVAILABLE/);
  const unexpected = fakeClient({ fail: new Error("boom") });
  await assert.rejects(() => readBusinessRecord({ client: unexpected, actor: JOE, dataset: "clients", id: ID, correlationId: "c" }), /INTERNAL_ERROR/);
});
