// work-request-projection.test.mjs — the crosswalk is only worth what this proves about it.
//
// Doctrine names ONE canonical Work Request machine and calls the Workspace view a
// mapped, non-authoritative projection. The crosswalk between them did not exist;
// this validates the one written for it against BOTH live contracts, so the mapping
// cannot silently drift out of step with either side.
//
// Run: node control-room/test/work-request-projection.test.mjs

import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { dirname, join } from "node:path";

const REPO = join(dirname(fileURLToPath(import.meta.url)), "..", "..");
const read = (p) => JSON.parse(readFileSync(join(REPO, p), "utf8"));

const projection = read("control-room/contracts/work-request-projection.v1.json");
const canonical = read("control-room/contracts/state-machines.v1.json").machines.work_request;

const failures = [];
const check = (ok, msg) => { if (!ok) failures.push(msg); };

const canonicalStates = [...canonical.main, ...canonical.side];
const mapped = new Map(projection.crosswalk.map((r) => [r.canonical, r.projection]));
const granted = new Set(projection.granted_projection_states.states);

// TOTAL — every canonical state maps. An unmapped state is not a rendering choice,
// it is a request the UI cannot describe at all.
for (const s of canonicalStates) {
  check(mapped.has(s), `canonical state '${s}' has no projection mapping`);
}

// NO PHANTOMS — the crosswalk may not map states the canonical machine does not have.
// This is the direction that catches a stale crosswalk after the canonical machine
// drops or renames a state.
for (const s of mapped.keys()) {
  check(canonicalStates.includes(s),
    `crosswalk maps '${s}', which is not a state of the canonical machine`);
}

// CLOSED — every target is a state doctrine actually granted the projection. This is
// the invariant the whole contract exists to hold: a projection cannot invent states.
for (const [from, to] of mapped) {
  check(granted.has(to),
    `'${from}' projects to '${to}', which doctrine does not grant the projection`);
}

// DETERMINISTIC — one target per canonical state, no duplicate rows.
const seen = new Set();
for (const row of projection.crosswalk) {
  check(!seen.has(row.canonical), `duplicate crosswalk row for '${row.canonical}'`);
  seen.add(row.canonical);
}

// TERMINALS STAY TERMINAL — a canonical terminal must not project onto a state the
// requester would read as still moving. Getting this wrong tells someone their
// request is alive when the record has closed it.
const projectionTerminals = new Set(["completed", "declined", "failed"]);
for (const t of canonical.terminal) {
  check(projectionTerminals.has(mapped.get(t)),
    `canonical terminal '${t}' projects to '${mapped.get(t)}', which is not terminal in the projection`);
}

// NOTHING BUT A CONFIRMED CLOSE MAY READ AS COMPLETED. The canonical machine's own
// invariant is that confirmed_closed requires accepted verification evidence, so any
// other state rendering as completed would claim an unevidenced outcome.
for (const [from, to] of mapped) {
  check(to !== "completed" || from === "confirmed_closed",
    `'${from}' projects to 'completed'; only 'confirmed_closed' may, because completion requires accepted verification evidence`);
}

// EVERY JUDGMENT CALL IS DECLARED. A collapse that loses information is allowed; an
// UNDOCUMENTED one is not, because the next reader cannot tell a decision from an
// oversight.
const declared = new Set(projection.judgment_calls.map((j) => j.id));
check(declared.has("released-is-not-completed"), "missing judgment call: released-is-not-completed");
check(declared.has("superseded-projects-as-declined"), "missing judgment call: superseded-projects-as-declined");
for (const j of projection.judgment_calls) {
  check(Boolean(j.why && j.cost_accepted && j.reopens_if),
    `judgment call '${j.id}' must state why, what it costs, and what reopens it`);
}

// REASON SURVIVES COLLAPSE, NAMED AT A PLACE. declined and superseded now share
// one projection target on rows that actually exist (migration 0426 made
// superseded reachable), so the invariant stops being a principle and becomes a
// field somebody has to populate. A judgment call that collapses two canonical
// states must say WHERE the distinguishing reason lives, or the next reader has
// no way to check the cost it accepted is actually being paid.
// Scoped to the one collapse whose reason is a real column on a real row:
// declined and superseded both project to 'declined', and 0426 made the second
// of them enterable. The released collapse is deliberately NOT covered — nothing
// records a distinguishing reason for it, and asserting one would demand a field
// no writer produces.
const supersedeCall = projection.judgment_calls.find((j) => j.id === "superseded-projects-as-declined");
check(Boolean(supersedeCall && supersedeCall.reason_carrier),
  "superseded-projects-as-declined names no reason_carrier; declined and superseded share one " +
  "projection target, so REASON SURVIVES COLLAPSE has no enforcement site without one");
check(Boolean(supersedeCall && /\bexit_reason\b/.test(supersedeCall.reason_carrier) &&
              /\bsuperseded_by\b/.test(supersedeCall.reason_carrier)),
  "the reason_carrier must name the canonical columns that carry the distinction " +
  "(exit_reason and superseded_by), not gesture at one");

// THE KNOWN CONFLICT STAYS VISIBLE until it is actually fixed. The workspace contract
// declares nine states where doctrine grants seven; this test fails loudly once that
// is repaired, so the note cannot outlive the problem it describes.
const wsStates = read("workspace/contracts/state-machines.v1.json").machines.engineering_request.states;
const ungranted = wsStates.filter((s) => !granted.has(s));
if (ungranted.length) {
  check(Boolean(projection.known_conflict_this_contract_exposes),
    `workspace projection declares ungranted states (${ungranted.join(", ")}) and this contract does not record the conflict`);
} else {
  check(!projection.known_conflict_this_contract_exposes,
    "the workspace contract no longer declares ungranted states — delete known_conflict_this_contract_exposes, it is now describing a problem that does not exist");
}

if (failures.length) {
  console.error(`FAIL  ${failures.length} problem(s)`);
  for (const f of failures) console.error("  " + f);
  process.exit(1);
}
console.log(
  `ok  work-request projection: ${canonicalStates.length} canonical states map onto ` +
  `${granted.size} granted projection states, ${projection.judgment_calls.length} judgment calls declared`
);
