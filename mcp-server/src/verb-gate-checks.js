// verb-gate-checks.js — server-side port of the DETERMINISTIC deny logic from
// two of the four client verb gates (bypass audit C33/C34, 2026-09-24, Joe's
// decision 0b11c89b: every place a rule can be overridden or ignored must be
// enforced).
//
// WHY THIS EXISTS. hooks/escalation-gate.py and hooks/blocker-decider-gate.py
// are Claude Code PreToolUse hooks, matched by tool_name regex on the DIRECT
// mcp__*__add-loop / mcp__*__record-defect call. Every other door to the same
// verb -- ./run.sh call, tools/call-verb.py, mcp-server/local-verb.mjs
// (including its BREAK-GLASS direct-DATABASE_URL mode), and the
// mcp__*__call-verb passthrough -- ultimately calls tools.js's
// executeRegisteredTool(), which is where the checks exported here are
// applied (see the comment at that call site). That is the one function
// every door funnels through, including break-glass, which bypasses mcp.js's
// callTool() entirely and calls executeRegisteredTool() directly -- a second
// Opus re-review (2026-09-24) found the first redesign's placement inside
// callTool() missed exactly that door, so the check moved one layer deeper
// to the true single choke point. That is also the redesign an Opus review
// originally asked for after a client-side regex re-check
// (hooks/verb_gate_recheck.py, now deleted) proved leaky against real
// transcript data: 62/12,145 replayed commands would have been falsely
// denied, and several trivial bypasses (shell variables, `$(cat f)`, calling
// tools/call-verb.py directly) were never going to be closable from the
// Bash-command-text side at all.
//
// WHAT IS AND IS NOT PORTED. Read the two Python files' classify()/
// needs_decider() before touching this file.
//   - blocker-decider-gate.py's needsDecider(): fully deterministic, reads
//     only the tool_input row's own text. Ported in full below as
//     needsDecider().
//   - escalation-gate.py's classify(): four of its five branches
//     (BOUNDARY, FACT_CAPTURE, PROTECTED, INTERNAL) are plain regex over the
//     row's own text and are ported in full below as classifyLoopText().
//     The FIFTH branch, HUMAN_WANTS_CHOICE against the partner's own last
//     transcript turn ("what are my options", "ask me", …), is NOT ported:
//     the server never receives the session transcript, so there is no data
//     to check it against. This is a real, reported behavior change — see
//     the PR description's Jev architecture_or_design receipt for the
//     alternatives considered and why fail-closed (a few more calls refused
//     with a clear message, rather than silently admitting one a transcript
//     might have exempted) was the one Jev picked.
//   - drift-claim-gate.py and rule-shape-gate.py are NOT ported: neither one
//     ever denies (drift-claim-gate only injects additionalContext; rule-shape-gate
//     is warn-only by design, per the bypass audit's C32 finding). There is
//     no verdict to move server-side.
//
// Refusals are named the same way the Python hooks name them
// (internal_decision / capability_no_decider), so a caller who has seen one
// refusal recognises the other.

const BOUNDARY = new RegExp(
  "\\b(disable|weaken|loosen|relax|bypass|turn off|switch off|remove|widen|expand" +
  "|opt out of|make .{0,20}optional)\\b[^.?]{0,60}" +
  "\\b(gate|guard|hook|rule|check|constraint|permission|allowlist|deny list|denylist" +
  "|boundary|approval|escalation|firewall|limit|cap|restriction)\\b" +
  "|\\b(hook|gate|guard|allowlist|permission|settings\\.json|denylist|deny list)\\b" +
  "[^.?]{0,50}\\b(edit|change|modify|update|add to|grant|escalate|elevate|root|sudo)\\b" +
  "|\\bgrant (myself|itself|the system|sessions?)\\b" +
  "|\\bunattended\\b[^.?]{0,40}\\b(authority|permission|allow|expand)\\b", "i");

const FACT_CAPTURE = new RegExp(
  "\\b(what happened|how did it go|how'?d it go|what did (he|she|they|it) say" +
  "|did (he|she|they) (say|mention|agree|commit|respond|show|come|call|reply)" +
  "|who (was|were|did) (there|you|attend)|were you|did you (meet|call|visit|tour|talk|speak|see)" +
  "|how many people|when did (he|she|they|you)" +
  "|pursue or table|worth a follow[- ]?up|any good|what'?s your read|your read on" +
  "|grade|rating|rate (him|her|them|the vendor)|deliver(y|ed)" +
  "|stage (change|now|for)|still (active|live|warm|interested)|is (he|she|they) still" +
  "|did (it|that|the deal) (close|sign|die|stall)" +
  "|which of these did you|have you (met|spoken|talked|heard))\\b", "i");

const PROTECTED = new RegExp(
  "\\b(client|prospect|landlord|listing agent|tenant|vendor|broker|doctor|practice owner" +
  "|LOI|letter of intent|PSA|lease|proposal|counter|RFP" +
  "|send|email|publish|post|tweet|linkedin|facebook|instagram" +
  "|spend|pay|paid|invoice|budget|purchase|fee|commission|pricing" +
  "|subscription|subscribe|renews?|renewal|billing" +
  "|delete|destroy|drop table|force[- ]push|revoke)\\b" +
  "|[$£€]\\s?\\d" +
  "|\\b\\d+\\s?(usd|dollars?)\\b" +
  "|\\b(per|a)\\s(month|year|seat|user)\\b", "i");

const INTERNAL = new RegExp(
  "\\b(schemas?|migrations?|tables?|columns?|indexe?s?|constraints?|triggers?" +
  "|views?|queries|query|sql" +
  "|verbs?|endpoints?|workers?|connectors?|mcp|apis?" +
  "|hooks?|scripts?|modules?|functions?|refactor\\w*|renam\\w+|repos?|branch\\w*" +
  "|commits?|merges?" +
  "|renders?|exporters?|exports?|pipelines?|jobs?|crons?|launchd" +
  "|scheduled tasks?|nightly" +
  "|folders?|director(y|ies)|file (names?|structure|layout)|naming|structure" +
  "|architecture" +
  "|rule stores?|doctrine|loops?|record layer|detectors?|selftests?|fixtures?" +
  "|tests?|migrat\\w+" +
  "|configs?|settings?|flags?|env|variables?|caches?|logs?|formats?|layouts?" +
  "|sort order|sort by|ordering|sorting)\\b", "i");

const DECIDER = /\b(joe|dell)\b/i;
const IMPOSSIBLE = new RegExp(
  "\\b(impossible|cannot be granted|can'?t be granted|does not exist" +
  "|no such (api|control|permission|capability|feature)" +
  "|not (available|supported) on (this|the) plan|nobody can grant)\\b", "i");

const LOOP_TEXT_FIELDS = ["title", "body", "unblocks", "source_note", "blocker_detail"];

/** Same whole-call flattening as loop_text()/row_text() in the Python gates:
 * the subject cannot hide in blocker_detail while the title stays neutral. */
function loopRowText(args) {
  if (!args || typeof args !== "object") return "";
  return LOOP_TEXT_FIELDS.map(f => (args[f] == null ? "" : String(args[f])))
    .filter(Boolean).join("\n");
}

/** Port of escalation-gate.py's classify(), minus the HUMAN_WANTS_CHOICE
 * transcript exemption (see file header). Returns {allow, why}. */
function classifyLoopText(blob) {
  if (!blob || !blob.trim()) return { allow: true, why: "empty" };
  if (BOUNDARY.test(blob)) return { allow: true, why: "boundary_change_is_constitutional" };
  if (FACT_CAPTURE.test(blob)) return { allow: true, why: "fact_capture_only_joe_knows" };
  if (PROTECTED.test(blob)) return { allow: true, why: "protected_class_is_joes" };
  if (INTERNAL.test(blob)) return { allow: false, why: "internal_decision" };
  return { allow: true, why: "unclassified_allowed" };
}

/** Port of escalation-gate.py's parks_a_decision(): only the two spellings
 * that literally await Joe's ruling. */
function parksADecision(args) {
  if (!args || typeof args !== "object") return false;
  return args.marker === "decision" || args.blocker === "ruling";
}

/** Port of blocker-decider-gate.py's needs_decider(). */
function needsDecider(args) {
  if (!args || typeof args !== "object") return false;
  if (args.blocker !== "capability") return false;
  const text = loopRowText(args);
  return !(DECIDER.test(text) || IMPOSSIBLE.test(text));
}

const ESCALATION_REASON =
  "ESCALATION GATE (server-side) — refused. This loop parks an INTERNAL decision " +
  "on Joe (rule e065aa82), and internal decisions are yours to make and record. " +
  "marker='decision' and blocker='ruling' both mean the ❓ waits in the Monday " +
  "brief for Joe to rule; everything internal — schema, records, renders, jobs, " +
  "config, rules, refactors, procedure — you decide and report. Research until " +
  "confident, pick the smallest reversible option, execute it, record it with " +
  "log-decision, and tell Joe in one line what you did and why.";

const BLOCKER_DECIDER_REASON =
  "BLOCKER-DECIDER GATE (server-side) — refused. blocker='capability' without a " +
  "decider is 'not authorized' and 'not possible' filed as the same finding " +
  "(rule 88e9b5eb). If someone can grant this capability, NAME THE DECIDER in the " +
  "row (Joe or Dell). If nobody can grant it, SAY SO PLAINLY (e.g. 'no such API on " +
  "this plan — genuinely impossible').";

export {
  BOUNDARY, FACT_CAPTURE, PROTECTED, INTERNAL, DECIDER, IMPOSSIBLE,
  loopRowText, classifyLoopText, parksADecision, needsDecider,
  ESCALATION_REASON, BLOCKER_DECIDER_REASON,
};
