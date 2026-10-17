// The gated rule boot: standing-context detail "boot" (mcp-server/src/rule-boot.js).
// Risk order (Jev, 2026-09-26): pages complete and index coverage first, then
// always-on in full, sponsor scoping, determinism, the verb door.
import { test } from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { executeRegisteredTool } from "../src/tools.js";
import { RULE_BOOT_CLASSES } from "../src/rule-boot-classes.js";
import { paginate, renderRuleBoot, ruleBootPage, RULE_BOOT_PAGE_CHARS } from "../src/rule-boot.js";

const JOE = { id: "11111111-1111-4111-8111-111111111111", slug: "joe", human: true, via: "oauth-google" };

const classified = Object.keys(RULE_BOOT_CLASSES).sort();
const alwaysOnId = classified.find(id => RULE_BOOT_CLASSES[id].on && !RULE_BOOT_CLASSES[id].personal_to);
const actionId = classified.find(id => RULE_BOOT_CLASSES[id].cls === "b" && !RULE_BOOT_CLASSES[id].on && !RULE_BOOT_CLASSES[id].personal_to);
const joePersonalId = classified.find(id => RULE_BOOT_CLASSES[id].personal_to === "joe");

function row(short, statement, personalTo = null) {
  return { id: `${short}-0000-4000-8000-000000000000`, statement, personal_to: personalTo };
}

// Every classified shared rule with a long statement, so the boot spans pages.
function corpus() {
  const rows = classified
    .filter(id => !RULE_BOOT_CLASSES[id].personal_to)
    .map(id => row(id, `Rule ${id} binding text. ` + "x".repeat(900) + ` END-${id}`));
  rows.push(row(joePersonalId, `Joe personal rule ${joePersonalId}. END-${joePersonalId}`, "joe"));
  rows.push(row("dddd0001", "Dell personal rule dddd0001. END-dddd0001", "dell"));
  rows.push(row("ffff0001", "A rule taught after classification. END-ffff0001"));
  return rows;
}

test("pages: every page fits the cap and the pages concatenate to the whole boot", async () => {
  const rows = corpus();
  const whole = renderRuleBoot(rows, "joe").text;
  const first = await ruleBootPage(rows, "joe", 1);
  assert.ok(first.pages_total > 1, "the fixture must span several pages to test paging");
  let joined = "";
  for (let p = 1; p <= first.pages_total; p += 1) {
    const page = await ruleBootPage(rows, "joe", p);
    assert.equal(page.digest, first.digest, "every page carries the same digest");
    assert.ok(page.text.length <= RULE_BOOT_PAGE_CHARS);
    // `./run.sh call` prints JSON; the escaped page must still fit a ~30k Bash result.
    assert.ok(JSON.stringify(page, null, 2).length < 30000, `page ${p} too large once JSON-encoded`);
    joined += page.text;
  }
  assert.equal(joined, whole, "no byte lost or duplicated across pages");
  const last = await ruleBootPage(rows, "joe", first.pages_total);
  assert.match(last.next, /^complete/);
  assert.match(first.next, /"page":2/);
  const beyond = await ruleBootPage(rows, "joe", first.pages_total + 1);
  assert.equal(beyond.error, "page_out_of_range");
});

test("every active rule in scope appears exactly once: full text in Part 1 or one index line", () => {
  const rows = corpus();
  const { text, index_ids: ids, always_on_ids: on } = renderRuleBoot(rows, "joe");
  const [part1, index] = text.split("## PART 2");
  const expected = [...new Set(rows.filter(r => r.personal_to !== "dell")
    .map(r => r.id.slice(0, 8)))].sort();
  assert.deepEqual([...ids, ...on].sort(), expected, "index and full text together cover every rule");
  assert.equal(ids.filter(id => on.includes(id)).length, 0, "no rule is both indexed and in full");
  for (const id of expected) {
    const lines = index.split("\n").filter(l => l.startsWith(`${id} | `));
    const headings = part1.split("\n").filter(l => l.startsWith(`### ${id}`));
    assert.equal(lines.length + headings.length, 1, `${id} must appear exactly once`);
  }
  assert.match(index, new RegExp(`^${actionId} \\| ${RULE_BOOT_CLASSES[actionId].cls.toUpperCase()} \\| `, "m"));
  assert.match(part1, /^### ffff0001$/m, "an unclassified rule carries its full text");
});

test("full text for class A, retained B/C route gaps and unclassified rules", () => {
  // Missed B/C route rules stay in full until their route is fixed.
  const rows = corpus();
  const { text, always_on_ids: on, counts } = renderRuleBoot(rows, "joe");
  const [part1, index] = text.split("## PART 2");
  const shared = rows.filter(r => !r.personal_to).map(r => r.id.slice(0, 8));
  for (const id of shared) {
    const cls = RULE_BOOT_CLASSES[id]?.cls;
    const full = part1.includes(`END-${id}`);
    if (!cls || RULE_BOOT_CLASSES[id].on) {
      assert.ok(full, `${id} (${cls || "U"}) must carry its full text`);
      assert.ok(on.includes(id), `${id} is always on`);
    } else {
      assert.ok(!full, `${id} (class ${cls}) must not carry full text at boot`);
      assert.ok(!on.includes(id), `${id} (class ${cls}) is not always on`);
      assert.match(index, new RegExp(`^${id} \\| ${cls.toUpperCase()} \\| `, "m"), `${id} stays indexed`);
      assert.ok(index.includes(RULE_BOOT_CLASSES[id].summary), `${id} keeps its index summary`);
    }
  }
  for (const cls of ["b", "c", "d", "e"]) {
    assert.ok(shared.some(id => RULE_BOOT_CLASSES[id]?.cls === cls), `fixture covers class ${cls}`);
  }
  assert.ok(part1.includes(rows.find(r => r.id.startsWith(alwaysOnId)).statement), "full text, not a summary");
  assert.ok(part1.includes("END-ffff0001"), "unclassified rules are recall-safe: full text");
  assert.ok(!part1.includes(`END-${actionId}`), "an action rule is delivered at its action, not at boot");
  assert.equal(counts.always_on, on.length);
  assert.match(text, /standing-context with rule_ids/, "the boot names the door to any rule's full text");
});

test("full text selection follows class A plus the declared route-gap list", () => {
  const keep = JSON.parse(readFileSync(new URL("../../ops/config/rule-classes.v1.json", import.meta.url))).keep_full_text;
  for (const [id, c] of Object.entries(RULE_BOOT_CLASSES)) {
    assert.equal(c.on, c.cls === "a" || Object.hasOwn(keep, id), `${id}: on must follow class A or keep_full_text (class ${c.cls})`);
  }
});

test("standing team ownership reaches Joe, Dell and unsponsored boots without task keywords", async () => {
  // The committed selection corpus preserves the live binding statement. Class
  // metadata remains separate, so a summary cannot silently replace this fact.
  const fixture = JSON.parse(readFileSync(new URL("../../ops/config/rule-selection-corpus.v1.json", import.meta.url), "utf8"))
    .rules.find(r => r.id === "725dff46");
  assert.ok(fixture, "vendor-network ownership fixture is required");
  assert.match(fixture.statement, /vendor network is the TEAM's/);
  for (const sponsor of ["joe", "dell", null]) {
    const rows = [row(fixture.id, fixture.statement)];
    const { text, always_on_ids: on } = renderRuleBoot(rows, sponsor);
    assert.ok(on.includes(fixture.id), `${sponsor || "unsponsored"}: ownership is always on`);
    assert.ok(text.split("## PART 2")[0].includes(fixture.statement), "Part 1 contains every byte of the fact");
    assert.doesNotMatch(text.split("## PART 2")[1], /^725dff46 \|/m, "a class-A rule is not repeated in the index");
    const page = await ruleBootPage(rows, sponsor, 1);
    const amended = await ruleBootPage([row(fixture.id, fixture.statement + " amended")], sponsor, 1);
    assert.notEqual(page.digest, amended.digest, "standing text participates in the boot digest");
  }
});

test("sponsor scoping: another sponsor's personal rule never renders", () => {
  const rows = corpus();
  const joe = renderRuleBoot(rows, "joe").text;
  assert.ok(!joe.includes("dddd0001"), "Dell's personal rule must not reach Joe");
  assert.ok(joe.includes(`END-${joePersonalId}`) || joe.includes(`${joePersonalId} |`));
  const dell = renderRuleBoot(rows, "dell").text;
  assert.ok(!dell.includes(joePersonalId), "Joe's personal rule must not reach Dell");
  assert.ok(dell.includes("dddd0001"));
  const none = renderRuleBoot(rows, null).text;
  assert.ok(!none.includes(joePersonalId) && !none.includes("dddd0001"), "unsponsored: shared only");
  // A row mis-scoped by the query still cannot render when the class data says it is someone else's.
  const leaked = [row(joePersonalId, "should not render", null)];
  assert.ok(!renderRuleBoot(leaked, "dell").text.includes(joePersonalId));
});

test("deterministic: same corpus in any order renders the same bytes and digest", async () => {
  const rows = corpus();
  const a = await ruleBootPage(rows, "joe", 1);
  const b = await ruleBootPage([...rows].reverse(), "joe", 1);
  assert.equal(a.digest, b.digest);
  assert.equal(a.text, b.text);
  const changed = rows.map(r => r.id.startsWith(alwaysOnId) ? { ...r, statement: r.statement + " amended" } : r);
  assert.notEqual((await ruleBootPage(changed, "joe", 1)).digest, a.digest, "an amended rule moves the digest");
});

test("paginate never splits a line when it fits", () => {
  const text = Array.from({ length: 50 }, (_, i) => `line ${i} ` + "y".repeat(30)).join("\n") + "\n";
  const pages = paginate(text, 200);
  assert.equal(pages.join(""), text);
  for (const p of pages) assert.ok(p.endsWith("\n"));
});

function client(rows) {
  return { query: async (sql) => {
    if (/standing-context:rule-boot/.test(sql)) return { rows };
    return { rows: [] };
  } };
}

test("verb door: standing-context detail=boot serves pages for the authenticated sponsor", async () => {
  const rows = corpus();
  const out = await executeRegisteredTool(client(rows), JOE, "standing-context", { detail: "boot", page: 1 });
  assert.equal(out.ok, true);
  assert.equal(out.rule_boot.page, 1);
  assert.ok(out.rule_boot.pages_total > 1);
  assert.equal(out.identity.personal_brain_scope, "joe-personal");
  assert.ok(!out.rule_boot.text.includes("dddd0001"));
  await assert.rejects(
    executeRegisteredTool(client(rows), JOE, "standing-context", { detail: "boot", page: 999 }),
    e => JSON.stringify(e.payload || e.message || e).includes("page_out_of_range"));
});

test("the retained classification fits its explicit token budget with every shared rule present", async () => {
  // Lengths only: statements are synthetic, sized from nothing committed. This
  // pins the renderer's overhead, not the corpus; ops/sync-rule-boot-classes.py
  // --check guards the corpus-sized budget in CI.
  const rows = classified.filter(id => !RULE_BOOT_CLASSES[id].personal_to).map(id => row(id, "s"));
  const page = await ruleBootPage(rows, null, 1);
  assert.equal(page.counts.index + page.counts.always_on, rows.length);
  assert.equal(page.over_budget, false);
});
