#!/usr/bin/env python3
"""Acceptance fixtures for the completion-evidence Stop gate.

WRITTEN BEFORE THE WIDENING (rule e65efc68). The CASES block below is the
original narrow gate's regression set and every one of it still has to pass:
a widening that quietly drops an existing fire is a weakening.

CLAUSE_CASES is the new half, and it is the acceptance test for fix A of the
2026-08-23 completion-integrity council. What it has to prove:

  1. THE CASE STUDY FIRES. An order with two clauses ("recategorize the rules"
     AND "make them load into every session"), a session that finishes the
     first and closes, blocks — naming the clause that has no receipt. This is
     the slice-standing-for-an-order shape Grok's chair named as the actual
     defect, and Joe's stated success signal: at least one correct fire on it.

  2. IT IS NOT A BANNED-WORD LIST. The same transcript blocks when the close
     says "Wrapped up \u2014 should be good", and blocks again when the close
     contains no completion vocabulary at all. If a weaker verb or plain
     silence were an escape, the gate would be theater within a week \u2014 which
     is Grok's explicit design constraint on this fix.

  3. NAMING THE RESIDUAL IS THE EXIT. The only way past an unaccounted clause
     is a receipt from its consumer surface, or saying plainly that the clause
     is not done. That is the whole predicate.

  4. THE DUAL BINDS. Where the session's own record holds a landed receipt, it
     may not close by calling the work unbuilt. That is the 24-rebuild failure
     (ops/built_unclosed.py header) in its Stop-gate form.

  5. THE FLOOR HOLDS. An order that yields no clause still gets the original
     predicate. Widening never makes the gate quieter.

RUNNING IT. No database, no network, no production access:

    .venv/bin/python ops/completion-evidence-gate-selftest.py
"""
from __future__ import annotations

import importlib.util
import itertools
import json
import os
import shlex
import subprocess
import sys
import tempfile

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
spec = importlib.util.spec_from_file_location(
    "completion_evidence", os.path.join(REPO, "hooks", "completion-evidence-gate.py")
)
assert spec and spec.loader
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)


def user(text):
    return {"type": "user", "message": {"role": "user", "content": text}}


def assistant(text):
    return {"type": "assistant", "message": {"role": "assistant", "content": text}}


CALL_IDS = itertools.count()


def tool(name, value=None):
    return {"type": "assistant", "message": {"content": [
        {"type": "tool_use", "id": f"fixture-{next(CALL_IDS)}", "name": name, "input": value or {}}
    ]}}


def codex_user(value):
    return {"type": "response_item", "payload": {
        "type": "message", "role": "user", "content": [
            {"type": "input_text", "text": value},
        ],
    }}


def codex_wrapper(value):
    return {"type": "response_item", "payload": {
        "type": "message", "role": "user", "content": [
            {"type": "input_text", "text": "The following is the Codex agent history added since your last approval"},
            {"type": "input_text", "text": value},
        ],
    }}


def codex_tool(name, value):
    return {"type": "response_item", "payload": {
        "type": "custom_tool_call", "call_id": f"fixture-{next(CALL_IDS)}", "name": name, "input": value,
    }}


def codex_assistant(value):
    return {"type": "response_item", "payload": {
        "type": "message", "role": "assistant", "content": [
            {"type": "output_text", "text": value},
        ],
    }}


def completed_fixture(records):
    """Expand legacy sequential fixtures into paired terminal-success events.

    These fixtures exercise completed work, rather than the pending/failed and
    concurrent boundaries covered separately in the Dot regression suite.
    """
    expanded = []
    for record in records:
        expanded.append(record)
        payload = record.get('payload') or {}
        if payload.get('type') == 'custom_tool_call':
            expanded.append({'type':'response_item', 'payload':{
                'type':'custom_tool_call_output', 'call_id':payload['call_id'],
                'output':json.dumps({'exit_code':0, 'output':'fixture completed'})}})
        content = (record.get('message') or {}).get('content')
        for block in content if isinstance(content, list) else []:
            if block.get('type') == 'tool_use' and str(block.get('id','')).startswith('fixture-'):
                expanded.append({'type':'user','message':{'content':[{
                    'type':'tool_result','tool_use_id':block['id'], 'is_error':False,
                    'content':'fixture completed successfully'}]}})
    return expanded


CASES = [
    ("ordinary answer", [user("status"), assistant("The board is current.")], False),
    ("one file patch is friction-free", [user("fix it"), tool("apply_patch", {"command": "*** Update File: a.py\n+x"}), assistant("Done.")], False),
    ("unsupported completion after multi-file patch", [user("fix it"), tool("apply_patch", {"command": "*** Update File: a.py\n+x\n*** Update File: b.py\n+y"}), assistant("Done.")], True),
    ("fresh read permits completion", [user("fix it"), tool("apply_patch", {"command": "*** Update File: a.py\n+x\n*** Update File: b.py\n+y"}), tool("Read"), assistant("Done and verified.")], False),
    ("explicit disclosure permits close", [user("fix it"), tool("apply_patch", {"command": "*** Update File: a.py\n+x\n*** Update File: b.py\n+y"}), assistant("Done, but unverified because the server is unavailable.")], False),
    ("no tests failed is not a disclosure", [user("fix it"), tool("apply_patch", {"command": "*** Update File: a.py\n+x\n*** Update File: b.py\n+y"}), assistant("Done; no tests failed.")], True),
    ("skipped none is not a disclosure", [user("fix it"), tool("apply_patch", {"command": "*** Update File: a.py\n+x\n*** Update File: b.py\n+y"}), assistant("Done; skipped none.")], True),
    ("verification failed needs a reason", [user("fix it"), tool("apply_patch", {"command": "*** Update File: a.py\n+x\n*** Update File: b.py\n+y"}), assistant("Done, but verification failed because the test server is unavailable.")], False),
    ("record write requires evidence", [user("reconcile"), tool("mcp__carr__update-deal"), assistant("Done.")], True),
    ("observe-memory write requires evidence", [user("learn preference"), tool("mcp__carr__observe-memory"), assistant("Done.")], True),
    ("correct-memory write requires evidence", [user("correct preference"), tool("mcp__carr__correct-memory"), assistant("Done.")], True),
    ("forget-memory write requires evidence", [user("forget preference"), tool("mcp__carr__forget-memory"), assistant("Done.")], True),
    ("generic call-verb write requires evidence", [user("reconcile"), tool("mcp__carr__call-verb", {"verb": "update-deal", "args": {}}), assistant("Done.")], True),
    ("recipient required for delivery", [user("update it"), tool("mcp__carr__update-deal"), assistant("Delivered after the update.")], True),
    ("named recipient permits delivery", [user("update it"), tool("mcp__carr__update-deal"), tool("Read"), assistant("Delivered to Dell after a fresh read.")], False),
    ("deploy gets checked", [user("release"), tool("Bash", {"command": "npx wrangler deploy"}), assistant("Deployed.")], True),
    ("unrelated historical tool does not matter", [user("status"), tool("Read"), assistant("Done with the explanation.")], False),
    ("Claude continuity direct write requires evidence", [
        user("checkpoint this milestone"),
        tool("mcp__carr-continuity__claude-checkpoint"),
        assistant("Done."),
    ], True),
    ("Claude continuity direct recovery is fresh evidence", [
        user("checkpoint this milestone"),
        tool("mcp__carr-continuity__claude-checkpoint"),
        tool("mcp__carr-continuity__claude-read-recovery"),
        assistant("Done and verified."),
    ], False),
    ("Claude continuity nested write requires evidence", [
        codex_user("checkpoint this milestone"),
        codex_tool("exec", 'await tools["mcp__carr-continuity__claude-record-event"]({});'),
        codex_assistant("Done."),
    ], True),
    ("Codex nested CARR write requires evidence", [
        codex_user("reconcile Cadence Studio"),
        codex_tool("exec", "const row = await tools.mcp__carr__update_deal({ id: 'd1', stage: 'LOI' });"),
        codex_assistant("Completed."),
    ], True),
    ("Codex nested generic call verb requires evidence", [
        codex_user("reconcile Cadence Studio"),
        codex_tool("exec", "await tools.mcp__carr__call_verb({ verb: 'update-deal', args: { id: 'd1' } });"),
        codex_assistant("Done."),
    ], True),
    ("Codex nested CARR read is fresh evidence", [
        codex_user("reconcile Cadence Studio"),
        codex_tool("exec", "await tools.mcp__carr__update_deal({ id: 'd1' });"),
        codex_tool("exec", "const fresh = await tools.mcp__carr__get_deal({ id: 'd1' });"),
        codex_assistant("Done and verified."),
    ], False),
    ("Codex history wrapper cannot reset the mutation window", [
        codex_user("reconcile Cadence Studio"),
        codex_tool("exec", "await tools.mcp__carr__update_deal({ id: 'd1' });"),
        codex_wrapper("Earlier work and environment context."),
        codex_assistant("Done."),
    ], True),
    ("Codex create national market deal requires evidence", [
        codex_user("add market"),
        codex_tool("exec", "await tools.mcp__carr__create_national_market_deal({ account: 'Cadence Studio' });"),
        codex_assistant("Done."),
    ], True),
    ("Codex patch deal field requires evidence", [
        codex_user("update market"),
        codex_tool("exec", "await tools.mcp__carr__patch_deal_field({ id: 'd1', field: 'phase' });"),
        codex_assistant("Done."),
    ], True),
    ("Codex doctrine write requires evidence", [
        codex_user("write doctrine"),
        codex_tool("exec", "await tools.mcp__carr__write_doctrine_section({ doctrine_id: 'x' });"),
        codex_assistant("Completed."),
    ], True),
    ("Codex workflow acceptance requires evidence", [
        codex_user("accept the canary"),
        codex_tool("exec", "await tools.mcp__carr__accept_workflow({ workflow_key: 'fixture' });"),
        codex_assistant("Completed."),
    ], True),
    ("Codex legacy schedule disable requires evidence", [
        codex_user("retire the old schedule"),
        codex_tool("exec", "await tools.mcp__carr__disable_legacy_schedule({ workflow_key: 'fixture' });"),
        codex_assistant("Completed."),
    ], True),
    ("Codex sourced problem capture requires evidence", [
        codex_user("capture this operating problem"),
        codex_tool("exec", "await tools.mcp__carr__report_problem({ idempotency_key: 'fixture' });"),
        codex_assistant("Completed."),
    ], True),
    ("Codex Work Request triage requires evidence", [
        codex_user("review and triage this request"),
        codex_tool("exec", "await tools.mcp__carr__review_and_triage({ human_ref: 'WR-000001' });"),
        codex_assistant("Completed."),
    ], True),
    ("Codex sourced ready-plan proposal requires evidence", [
        codex_user("prepare the bounded plan"),
        codex_tool("exec", "await tools.mcp__carr__propose_ready_plan({ human_ref: 'WR-000001' });"),
        codex_assistant("Completed."),
    ], True),
    ("Codex sourced ready-plan acceptance requires evidence", [
        codex_user("accept the exact plan"),
        codex_tool("exec", "await tools.mcp__carr__accept_ready_plan({ human_ref: 'WR-000001' });"),
        codex_assistant("Completed."),
    ], True),
    ("Codex outcome-feedback proposal requires evidence", [
        codex_user("propose observed outcome feedback"),
        codex_tool("exec", "await tools.mcp__carr__propose_outcome_feedback({ human_ref: 'WR-000001' });"),
        codex_assistant("Completed."),
    ], True),
    ("Codex outcome-feedback acceptance requires evidence", [
        codex_user("accept the exact observed outcome"),
        codex_tool("exec", "await tools.mcp__carr__accept_outcome_feedback({ human_ref: 'WR-000001' });"),
        codex_assistant("Completed."),
    ], True),
    ("CARR read action permits completion", [
        codex_user("reconcile Cadence Studio"),
        codex_tool("exec", "await tools.mcp__carr__patch_deal_field({ id: 'd1' });"),
        codex_tool("exec", "await tools.mcp__carr__review_queue({});"),
        codex_assistant("Done and verified."),
    ], False),
]


# --------------------------------------------------------------- clause half
# Fixtures for fix A: the gate binds on ANY completion claim against an
# originating order, requiring the order's clauses, each clause's named
# consumer surface, and a fresh consumer-side receipt per delivered clause.


def read(path, is_error=False):
    """A Read call plus its result, so a fixture can say whether the file was there."""
    return [
        {"type": "assistant", "message": {"content": [
            {"type": "tool_use", "id": "t1", "name": "Read", "input": {"file_path": path}}]}},
        {"type": "user", "message": {"role": "user", "content": [
            {"type": "tool_result", "tool_use_id": "t1", "is_error": is_error,
             "content": "no such file" if is_error else "1\tcontents"}]}},
    ]


def patch(*paths):
    body = "".join(f"*** Update File: {p}\n+x\n" for p in paths)
    return tool("apply_patch", {"command": body})


def flat(rows):
    out = []
    for row in rows:
        out.extend(row if isinstance(row, list) else [row])
    return out


# The case study, as a transcript. Joe ordered two clauses; the session
# finished the first one and closed against it.
CASE_STUDY_ORDER = ("recategorize the 218 rules into enforcement classes "
                    "and make them load into every session")
CASE_STUDY_WORK = [
    user(CASE_STUDY_ORDER),
    patch("ops/config/rule-enforcement-map.json", "ops/rule-enforcement-map-check.py"),
    tool("Bash", {"command": ".venv/bin/python -m pytest ops/rule-map-selftest.py"}),
]

CLAUSE_CASES = [
    # 1. THE SUCCESS SIGNAL: a slice standing for the order.
    ("case study: finished slice closes against a two-clause order",
     CASE_STUDY_WORK + [assistant("The enforcement map is complete and reviewed.")],
     True, "load into every session"),

    # 2. NOT A BANNED-WORD LIST. A weaker verb is not an escape...
    ("weaker verb is not an escape",
     CASE_STUDY_WORK + [assistant("Wrapped up \u2014 should be good.")],
     True, "load into every session"),

    # ...and neither is saying nothing at all.
    ("silence about the clause fires too",
     CASE_STUDY_WORK + [assistant("The map now carries 218 rows across four classes.")],
     True, "load into every session"),

    # 3. NAMING THE RESIDUAL IS THE EXIT.
    ("disclosed residual closes the clause",
     CASE_STUDY_WORK + [assistant(
         "The enforcement map is complete. Scoped loading into every session is "
         "not built \u2014 no loader exists yet, so nothing loads at boot.")],
     False, ""),

    # ...as is an actual receipt from the clause's consumer surface.
    ("live-boot receipt closes the clause",
     CASE_STUDY_WORK + [
         tool("mcp__carr__standing-context"),
         assistant("The map is complete and standing-context now loads all 218.")],
     False, ""),

    # A generic read is NOT a receipt for a live-surface clause. This is the
    # difference between the old gate and this one: builder-context evidence
    # cannot account for a clause whose consumer is somewhere else.
    ("a plain file read does not account for a live-surface clause",
     CASE_STUDY_WORK + [tool("Read", {"file_path": "ops/config/rule-enforcement-map.json"}),
                        assistant("Complete and verified.")],
     True, "load into every session"),

    # 4. STANDING CLAUSES (Joe's ruling): scope decay across turns.
    ("unaccounted clause survives a later turn",
     CASE_STUDY_WORK + [
         assistant("Map recategorized."),
         user("now fix the failing tests"),
         patch("tests/a.py", "tests/b.py"),
         tool("Bash", {"command": ".venv/bin/python -m pytest"}),
         assistant("Fixed, and the suite passes.")],
     True, "load into every session"),

    ("an explicit retirement drops the standing clause",
     CASE_STUDY_WORK + [
         assistant("Map recategorized."),
         user("skip the loading part for now, just fix the failing tests"),
         patch("tests/a.py", "tests/b.py"),
         tool("Bash", {"command": ".venv/bin/python -m pytest"}),
         assistant("Fixed, and the suite passes.")],
     False, ""),

    ("a receipt on the earlier turn drops the standing clause",
     CASE_STUDY_WORK + [
         tool("mcp__carr__standing-context"),
         assistant("Map recategorized and loading at boot."),
         user("now fix the failing tests"),
         patch("tests/a.py", "tests/b.py"),
         tool("Bash", {"command": ".venv/bin/python -m pytest"}),
         assistant("Fixed, and the suite passes.")],
     False, ""),

    # 5. CONSUMER SURFACE PER CLAUSE KIND.
    ("deploy clause needs a live probe, not wrangler's exit",
     [user("deploy the worker to production"),
      tool("Bash", {"command": "npx wrangler deploy"}),
      assistant("Deployed.")],
     True, "production"),

    ("a live catalog probe closes the deploy clause",
     [user("deploy the worker to production"),
      tool("Bash", {"command": "npx wrangler deploy"}),
      tool("Bash", {"command": "./run.sh health"}),
      assistant("Deployed; the live catalog still carries every verb.")],
     False, ""),

    ("partner-ready clause needs first use or an honest unconfirmed",
     [user("get the deal room page ready for Dell to use"),
      patch("dealroom/page.tsx", "dealroom/api.ts"),
      tool("Bash", {"command": ".venv/bin/python -m pytest dealroom"}),
      assistant("Ready.")],
     True, "human"),

    ("released-unconfirmed is an honest close for a partner clause",
     [user("get the deal room page ready for Dell to use"),
      patch("dealroom/page.tsx", "dealroom/api.ts"),
      tool("Bash", {"command": ".venv/bin/python -m pytest dealroom"}),
      assistant("The page builds and its tests pass. Dell has not used it yet, "
                "so it is released-unconfirmed rather than live.")],
     False, ""),

    # 6. NOISE CONTROL. Ordinary repo work with its own evidence must stay quiet.
    ("multi-clause repo order with evidence stays quiet",
     [user("write the selftest and then implement the gate"),
      patch("ops/x-selftest.py", "hooks/x.py"),
      tool("Bash", {"command": ".venv/bin/python ops/x-selftest.py"}),
      assistant("Both landed and the selftest passes.")],
     False, ""),

    ("a single mechanical edit is still friction-free",
     [user("add a docstring to foo.py"),
      tool("apply_patch", {"command": "*** Update File: foo.py\n+x"}),
      assistant("Done.")],
     False, ""),
]

# 7. THE DUAL. Where consumer receipts exist, a session may not close by
#    calling the work unbuilt because an attestation verb was skipped.
DUAL_CASES = [
    ("landed receipt contradicts an unbuilt close",
     flat([user("capability-program says 0/51 completed \u2014 is the scoped loader built?"),
           read("hooks/scoped-loader.py"),
           assistant("The scoped loader was never built; nothing is on disk. "
                     "I'll start building it.")]),
     True, "unbuilt"),

    ("no landed receipt, so an unbuilt close is honest",
     flat([user("capability-program says 0/51 completed \u2014 is the scoped loader built?"),
           read("hooks/scoped-loader.py", is_error=True),
           assistant("The scoped loader was never built; nothing is on disk. "
                     "I'll start building it.")]),
     False, ""),

    ("reading a file is not by itself an unbuilt contradiction",
     flat([user("what does the loader do?"),
           read("hooks/scoped-loader.py"),
           assistant("It maps applicability tags onto the boot set.")]),
     False, ""),
]


# One scratch ledger for every fixture that spawns the real hook. Created at
# import so all of them share it and none of them touches out/stop-latch.
latch_state = tempfile.mkdtemp(prefix="completion-latch-state-")


def machine_text_boundary():
    """Injected machine text is data, never an order.

    Every string here is from a real 2026-08 transcript and every one produced
    a false fire before order_text() existed.
    """
    checks = [
        ('[hooks/drift-assertion-gate.py]: DRIFT ASSERTION \u2014 you are about to '
         'tell Joe that a present state is WRONG', 0),
        ('<scheduled-task name="nightly-record-layer" file="/Users/booko/x.md">'
         'run the nightly chain every night</scheduled-task>', 0),
        ("<system-reminder>send the summary to Dell when done</system-reminder>", 0),
        ("COMPLETION EVIDENCE GATE \u2014 deploy claim needs a live probe", 0),
        # 47 of 51 recipient fires in one replay pass were this single line:
        # a gate's own deny banner read as an order to tell Joe something. The
        # real attribution format carries a space inside its brackets, which
        # the first version of MACHINE_LINE did not allow for.
        ("[/usr/bin/python3 /Users/booko/carr-system/hooks/drift-assertion-gate.py]: "
         "DRIFT ASSERTION \u2014 you are about to tell Joe that a present state is WRONG", 0),
        # ...and a real order alongside injected text still yields its clause.
        ("deploy the worker to production\n<system-reminder>be careful</system-reminder>", 1),
    ]
    outcomes = []
    for raw, count in checks:
        got = mod.order_clauses(mod.order_text(raw))
        ok = len(got) == count
        outcomes.append(ok)
        print(f"{'PASS' if ok else 'FAIL'}  machine text yields {len(got)} clauses "
              f"(want {count}): {raw[:58]!r}")
    return all(outcomes)


def prose_is_not_an_order():
    """Description is not instruction.

    Every fragment here is verbatim from a 2026-08 transcript and every one of
    them produced a false clause before is_directive() existed. They are the
    reason this gate is not simply a verb matcher: Joe writes long messages
    full of analysis and quoted output, and a sentence inside one of them is
    not an order just because it contains "deploy".
    """
    prose = [
        "The write-door version of this check only fires when a record gets filed",
        "I am not asking you to deploy anything or to take my word for any of it",
        "tested behind a feature gate, but not promoted to the production default",
        "- Invokes the underlying tool the way this project runs it",
        "Seed prompt must never contain the nonce",
        "tool not installed",
        "The user can send it another message",
        # Second replay pass \u2014 each still fired after the first round of fixes.
        "is Joe's call to schedule",
        "Do not merge, deploy, push, or open a PR",
        "still no rehearsal receipt, so nothing here is closer to production",
        "Tell the user to open /hooks once (reloads config) or restart",
        # Third replay pass.
        "Promotion counted violations only when the rule base table was readable",
        "nightly: it branches STAGING, guarded on the pinned production project id",
        "Loop 420, the first release approval, with Joe's signature",
        "Every handler among the thirty-eight ungated write verbs was read for its SQL",
        '"description": "Name of an already-configured MCP server to invoke",',
    ]
    orders = [
        "deploy the worker to production",
        "wire eventkit to run unattended",
        "make them load into every session",
        "please send the brief to Dell",
        "you should schedule the audit every night",
    ]
    outcomes = []
    for line in prose:
        got = mod.order_clauses(line)
        outcomes.append(not got)
        print(f"{'PASS' if not got else 'FAIL'}  prose yields no clause: {line[:56]!r}")
    for line in orders:
        got = mod.order_clauses(line)
        outcomes.append(bool(got))
        print(f"{'PASS' if got else 'FAIL'}  order still yields a clause: {line[:56]!r}")
    return all(outcomes)


def dual_precision():
    """The dual needs the artifact's whole name, not one shared word."""
    near_miss = flat([
        user("is the loader built?"),
        read("hooks/guard-unattended.py"),
        assistant("The scoped loader does not exist; nothing was ever built."),
    ])
    # "unbuilt now 38" is a COUNT from ops/built_unclosed.py, and a screenshot
    # is not the work. Both fired on real logs.
    metric = flat([
        user("what does the map check say?"),
        read("/tmp/scratch/iso-render.png"),
        assistant("Enforcement map check: OK, active render parity exact, unbuilt now 38."),
    ])
    cases = [("one shared word is not a contradiction", near_miss),
             ("an unbuilt COUNT is not an absence claim", metric)]
    outcomes = []
    for name, recs in cases:
        got, reason = mod.evaluate(completed_fixture(recs))
        outcomes.append(not got)
        print(f"{'PASS' if not got else 'FAIL'}  {name} ({reason})")
    return all(outcomes)


def clause_extraction_coverage():
    """The order must decompose into the clauses a human would name."""
    checks = [
        (CASE_STUDY_ORDER, 2, {"repo", "runtime"}),
        ("deploy the worker to production", 1, {"production"}),
        ("send the weekly brief to Dell", 1, {"recipient"}),
        ("schedule the audit to run every night", 1, {"scheduler"}),
        ("admit the 218 rules into the production store", 1, {"store"}),
        ("get the deal room page ready for Dell to use", 1, {"human"}),
        ("what is the status of the map?", 0, set()),
        # Noun forms are not orders. Both of these fired as live-surface
        # clauses before the verb-position check existed, and the first one
        # is from a real 2026-08 transcript.
        ("fix my media encoder install for me", 1, {"repo"}),
        ("fix the deploy script", 1, {"repo"}),
    ]
    outcomes = []
    for text_in, count, classes in checks:
        clauses = mod.order_clauses(text_in)
        got = {c.consumer for c in clauses}
        ok = len(clauses) == count and got == classes
        outcomes.append(ok)
        print(f"{'PASS' if ok else 'FAIL'}  clauses of {text_in!r}: "
              f"{len(clauses)} {sorted(got)} (want {count} {sorted(classes)})")
    return all(outcomes)


def floor_preserved():
    """Every original fire must still fire after the widening."""
    outcomes = []
    for name, recs, expected in CASES:
        if not expected:
            continue
        got, _ = mod.evaluate(completed_fixture(recs))
        outcomes.append(got)
        if not got:
            print(f"FAIL  widening silenced an original fire: {name}")
    ok = all(outcomes)
    print(f"{'PASS' if ok else 'FAIL'}  floor preserved: "
          f"{sum(outcomes)}/{len(outcomes)} original fires still fire")
    return ok



def cancel_capability_session_is_a_write():
    """A cancel is a lifecycle write, and "cancel" is not a generic write prefix.

    Paired with the WRITE_ACTION_EXACT entry of the same name. The live-registry
    coverage check below would also catch a missing classification, but only on a
    machine where mcp-server/src/tools.js loads; this pins the intent directly so
    the pair still means something in a portable runner.
    """
    ok = mod.is_write_action("cancel-capability-session")
    # And the negative half: adding it must not have turned "cancel" into a
    # prefix that swallows anything named cancel-*.
    not_a_prefix = not mod.is_write_action("cancel-something-that-does-not-exist")
    passed = ok and not_a_prefix
    print(f"{'PASS' if passed else 'FAIL'}  cancel-capability-session classifies as a write "
          f"without making 'cancel' a blanket prefix")
    return passed


def review_portfolio_revision_is_a_write():
    """The portfolio's independent review is a write; other review-* stay reads.

    Same shape as the cancel-capability-session pair above. "review" is
    deliberately NOT a write prefix, because review-memory and review-queue are
    genuine reads, so the portfolio verb has to earn its classification through
    an exact WRITE_ACTION_EXACT entry. The negative half is what keeps that
    honest: if someone ever "fixes" a future review-* write by adding the
    prefix instead, these reads start reporting as mutations and the gate
    begins demanding completion evidence for looking something up.
    """
    write = mod.is_write_action("review-portfolio-revision")
    reads = {name: mod.is_write_action(name) for name in
             ("review-memory", "review-queue", "read-portfolio",
              "review-something-that-does-not-exist")}
    passed = write and not any(reads.values())
    print(f"{'PASS' if passed else 'FAIL'}  review-portfolio-revision classifies as a write "
          f"without making 'review' a blanket prefix"
          + ("" if passed else f"; write={write} reads={reads}"))
    return passed


def evaluate_artifact_deletion_is_a_write():
    """V5-F01's deletion evaluation persists a receipt, so it is a write; the
    word "evaluate" is not thereby promoted to a prefix (same shape as the
    cancel-capability-session and review-portfolio-revision pairs above)."""
    write = mod.is_write_action("evaluate-artifact-deletion")
    not_a_prefix = not mod.is_write_action("evaluate-something-that-does-not-exist")
    passed = write and not_a_prefix
    print(f"{'PASS' if passed else 'FAIL'}  evaluate-artifact-deletion classifies as a write "
          f"without making 'evaluate' a blanket prefix")
    return passed


def cre_lifecycle_writes_are_writes():
    """V5-J102's lifecycle writers whose first word is not a write prefix are
    exact entries; none of those first words is thereby promoted to a prefix."""
    names = ("initialize-prospect-relationship", "initialize-assignment",
             "initialize-property-negotiation", "open-cre-assignment",
             "commit-winning-property", "cancel-pending-deal", "run-migration-shadow")
    writes = {name: mod.is_write_action(name) for name in names}
    reads = {name: mod.is_write_action(name) for name in
             ("initialize-something-that-does-not-exist", "open-something-else",
              "commit-something-else", "cancel-something-else", "run-something-else",
              "read-cre-lifecycle")}
    passed = all(writes.values()) and not any(reads.values())
    print(f"{'PASS' if passed else 'FAIL'}  V5-J102 lifecycle writers classify as writes "
          f"without making initialize/open/commit/cancel/run blanket prefixes"
          + ("" if passed else f"; writes={writes} reads={reads}"))
    return passed


def f05_rule_contract_binder_is_a_write():
    """V5-F05's binder is exact; its context read and future bind reads stay reads."""
    write = mod.is_write_action("bind-rule-context-contract")
    negatives = {
        name: mod.is_write_action(name)
        for name in ("read-action-context", "bind-rule-context-preview")
    }
    passed = write and not any(negatives.values())
    print(f"{'PASS' if passed else 'FAIL'}  V5-F05 rule contract binder classifies as a write "
          "without making bind a blanket prefix"
          + ("" if passed else f"; write={write} negatives={negatives}"))
    return passed


def registry_prefix_coverage(*, required=False):
    """Keep the family classifier honest against the local live registry when present."""
    registry = os.path.join(REPO, "mcp-server", "src", "tools.js")
    if not os.path.exists(registry):
        print("FAIL  live registry unavailable" if required else "SKIP  live registry unavailable")
        return not required
    script = (
        'import { TOOLS } from "./src/tools.js"; '
        'console.log(JSON.stringify(Object.keys(TOOLS).filter((n) => TOOLS[n].write).sort()))'
    )
    result = subprocess.run(["node", "--input-type=module", "-e", script],
                            cwd=os.path.join(REPO, "mcp-server"), text=True,
                            capture_output=True, timeout=30)
    if result.returncode:
        print("FAIL  live registry could not load; run npm --prefix mcp-server ci" if required else "SKIP  live registry could not load")
        return not required
    writes = json.loads(result.stdout)
    missing = [name for name in writes if not mod.is_write_action(name)]
    # notification-feed and read-doc-conversation are WR-000113/112 READS and must
    # stay False: a prefix that captured either would make every future read named
    # the same way a write.
    reads = ["review-queue", "get-deal", "list-verbs", "catch-me-up", "deal-board", "find",
             "notification-feed", "read-doc-conversation", "read-journey-one-clock"]
    false_writes = [name for name in reads if mod.is_write_action(name)]
    ok = not missing and not false_writes
    print(f"{'PASS' if ok else 'FAIL'}  live registry write coverage: "
          f"{len(writes) - len(missing)}/{len(writes)} writes classified"
          + (f"; missing={','.join(missing)}" if missing else "")
          + (f"; read false positives={','.join(false_writes)}" if false_writes else ""))
    return ok


def r03_notification_classification():
    """acknowledge-notification is a WRITE_ACTION_EXACT entry; its siblings are reads.

    Positive and negative in one case, because the pair is the point: the entry
    is EXACT so it covers exactly the one verb that writes a durable receipt,
    and the two reads named next to it stay unclassified.
    """
    positives = ["acknowledge-notification", "add-doc-conversation-turn"]
    negatives = ["notification-feed", "read-doc-conversation"]
    missing = [action for action in positives if not mod.is_write_action(action)]
    false_writes = [action for action in negatives if mod.is_write_action(action)]
    ok = not missing and not false_writes
    print(f"{'PASS' if ok else 'FAIL'}  R03 notification and Doc conversation classification"
          + (f"; missing={','.join(missing)}" if missing else "")
          + (f"; read false positives={','.join(false_writes)}" if false_writes else ""))
    return ok


def doc_conversation_write_door_classification():
    """WR-000114: the three write doors classify as writes; the read still does not.

    Positive and negative in ONE case, added together, because the pair is the
    point (a policy flip that only moves the positives leaves the negative
    silently asserting the old world). create-doc-conversation is covered by the
    EXISTING "create" prefix and is asserted here anyway, so a future narrowing
    of that prefix fails a case that names this verb. share- and rename- are
    WRITE_ACTION_EXACT entries rather than new prefixes, so the negatives below
    include the same two words in READ positions: a "share" or "rename" prefix
    would turn both of them into writes and fail this case.
    """
    positives = ["create-doc-conversation", "share-doc-conversation",
                 "rename-doc-conversation", "whats-new"]
    negatives = ["read-doc-conversation", "share-preview", "rename-preview", "whats-new-preview"]
    missing = [action for action in positives if not mod.is_write_action(action)]
    false_writes = [action for action in negatives if mod.is_write_action(action)]
    ok = not missing and not false_writes
    print(f"{'PASS' if ok else 'FAIL'}  Doc conversation write-door classification"
          + (f"; missing={','.join(missing)}" if missing else "")
          + (f"; read false positives={','.join(false_writes)}" if false_writes else ""))
    return ok


def authority_family_coverage():
    """Human-only acceptance/retirement and future proposal/approval writes stay gated."""
    actions = [
        "accept-workflow", "disable-legacy-schedule", "approve-work-request",
        "accept-outcome-feedback",
        "issue-execution-envelope", "transition-evaluation-case",
        "transition-execution-environment-provider",
        "propose-cognition-job", "decide-guidance-import-batch",
        "deactivate-guidance-registry",
    ]
    missing = [action for action in actions if not mod.is_write_action(action)]
    ok = not missing
    print(f"{'PASS' if ok else 'FAIL'}  authority workflow family coverage"
          + (f"; missing={','.join(missing)}" if missing else ""))
    return ok


def real_hook_case(kind, non_carr=False):
    if kind == "codex":
        records = [
            codex_user("reconcile"),
            codex_tool("exec", "await tools.mcp__carr__update_deal({ id: 'd1' });"),
            codex_assistant("Done."),
        ]
    else:
        records = [user("reconcile"), tool("mcp__carr__update-deal"), assistant("Done.")]
    with tempfile.NamedTemporaryFile(mode="w", suffix=".jsonl", delete=False) as fh:
        for row in completed_fixture(records):
            fh.write(json.dumps(row) + "\n")
        path = fh.name
    try:
        # A SESSION ID PER CASE, AND A SCRATCH LEDGER. As of 2026-08-23 this gate
        # latches one intervention per claim-set per session, so a fixed
        # "selftest" id makes these two cases silence each other — and makes the
        # suite pass once and fail on every later run, because the ledger under
        # out/ survives it. out/ is a symlink back to the canonical checkout from
        # every worktree on this Mac, so that ledger is shared machine-wide: a
        # fixture writing it would silence the running gate in a real session.
        # Caught exactly this way, by both cases going red the second time.
        session = f"selftest-{kind}-{os.getpid()}"
        payload = {"transcript_path": path, "session_id": session, "stop_hook_active": False}
        if kind == "codex":
            payload = {"transcriptPath": path, "sessionId": session, "stop_hook_active": False,
                       "hook_event_name": "Stop"}
        if non_carr:
            payload["cwd"] = "/private/tmp/non-carr-app"
        hook = os.path.join(REPO, "hooks", "completion-evidence-gate.py")
        result = subprocess.run([os.sys.executable, hook], input=json.dumps(payload), text=True,
                                capture_output=True, timeout=20,
                                env={**os.environ, "CARR_STOP_LATCH_STATE": latch_state})
        body = json.loads(result.stdout or "{}")
        return body.get("decision") == "block"
    finally:
        os.unlink(path)


def checkout_scope_is_clone_name_independent():
    """The running hook owns its checkout even when its clone has an opaque name."""
    original_repo = mod.REPO
    opaque_repo = "/private/tmp/carr-verifier-opaque"
    try:
        mod.REPO = opaque_repo
        root = mod.payload_is_carr({"cwd": opaque_repo}, [])
        nested = mod.payload_is_carr({"cwd": opaque_repo + "/hooks"}, [])
        sibling = not mod.payload_is_carr({"cwd": opaque_repo + "-other"}, [])
    finally:
        mod.REPO = original_repo
    ok = root and nested and sibling
    print(f"{'PASS' if ok else 'FAIL'}  checkout scope is independent of clone basename")
    return ok


def latch_cases():
    """One intervention per claim-set per turn, with a stable finding identity.

    THE DEFECT, measured twice by two sessions independently. The 2026-08-23
    gates-audit council's labeled ledger caught this gate firing a SECOND time
    on a summary message whose claims already carried receipts one message
    earlier. Replaying seven days, 127 transcripts and 916 Stop points found the
    rate behind that anecdote: one session was hit at FIVE consecutive stops on
    the same claim and the same reason class.

    THE PRECEDENT, and it is why the fix is a memory rather than a narrower
    matcher. Joe, 2026-08-15, third of four rulings on how to build a gate:
    "WHEN A REFUSAL CAN BE ROUTED AROUND, REMEMBER WHAT WAS REFUSED RATHER THAN
    WIDENING THE BAN." The first of those four is why the duplicate had to go at
    all — a gate that punishes the honest interim state gets deleted, and a
    session that verified its work, reported it, and then summarised it is in
    the honest state.

    IDENTITY IS PER LAYER, and getting this wrong would be the whole bug again:

      · CLAUSE layer — the identity is (consumer, clause text). Files are
        deliberately NOT in it. Two turns can touch identical paths while a
        different clause goes unaccounted, and those must not share an id.
      · FLOOR layer — the identity is the changed paths plus the write-verb
        names, under the reason class, because the floor really is a claim
        about the artifact set.
      · THE DUAL IS NOT LATCHED AT ALL. dual_block() fires on a session that has
        mutated nothing, and a close that calls landed work unbuilt is worth
        refusing every time it is uttered.

    Spawns the REAL hook, because the latch lives in main() around evaluate()
    and a direct evaluate() call cannot see it. State is pinned per case: out/
    is a symlink back to the canonical checkout from every worktree on this
    Mac, so a fixture writing the live ledger would silence the running gate.
    """
    hook = os.path.join(REPO, "hooks", "completion-evidence-gate.py")
    results = []

    def fires(records, session, state, name):
        with tempfile.NamedTemporaryFile(mode="w", suffix=".jsonl", delete=False) as fh:
            for row in completed_fixture(records):
                fh.write(json.dumps(row) + "\n")
            path = fh.name
        try:
            proc = subprocess.run(
                [os.sys.executable, hook], text=True, capture_output=True, timeout=30,
                input=json.dumps({"transcript_path": path, "session_id": session,
                                  "stop_hook_active": False, "cwd": REPO}),
                env={**os.environ, "CARR_STOP_LATCH_STATE": state})
            body = json.loads(proc.stdout or "{}")
            return body.get("decision") == "block", body.get("reason", "")
        finally:
            os.unlink(path)

    def expect(name, got, want):
        ok = got == want
        results.append(ok)
        print(f"{'PASS' if ok else 'FAIL'}  latch: {name}: fired={got} want={want}")

    with tempfile.TemporaryDirectory(prefix="completion-latch-") as state:
        # ── the floor duplicate, which is the ledger's own case ────────────
        floor = [user("reconcile the deal"), tool("mcp__carr__update-deal"),
                 assistant("Done.")]
        first, _ = fires(floor, "latch-floor", state, "first")
        expect("an unverified completion claim fires once", first, True)
        second, _ = fires(floor, "latch-floor", state, "second")
        expect("...and the same claim-set restated does not fire again", second, False)

        # Rewording is not a new finding. This is the half a message-text hash
        # gets wrong, and the half that let a council chair be held twice.
        reworded = [user("reconcile the deal"), tool("mcp__carr__update-deal"),
                    assistant("That is complete now — the reconciliation is finished.")]
        expect("...nor does the same claim-set reworded",
               fires(reworded, "latch-floor", state, "reworded")[0], False)

        # But NEW work is a new claim-set. Narrowing must never become muting.
        grew = [user("reconcile the deal"), tool("mcp__carr__update-deal"),
                tool("mcp__carr__update-lead"), assistant("Done.")]
        expect("a new write in the same session still fires",
               fires(grew, "latch-floor", state, "grew")[0], True)

        # And another session hears it. A ledger keyed on anything shared would
        # let one session silence another's gate.
        expect("a second session is not silenced by the first",
               fires(floor, "latch-other-session", state, "other")[0], True)

        # ── the clause layer ──────────────────────────────────────────────
        # Clause A ("recategorize the rules") is receipted by the work itself;
        # clause B ("load into every session") has no receipt from its live
        # surface. The gate must fire on B, once.
        b_open = CASE_STUDY_WORK + [assistant("The enforcement map is complete and reviewed.")]
        fired, reason = fires(b_open, "latch-clause", state, "clause-first")
        results.append(fired and "load into every session" in reason)
        print(f"{'PASS' if fired and 'load into every session' in reason else 'FAIL'}  "
              f"latch: an unaccounted clause fires, naming that clause")
        expect("...and the same unaccounted clause does not fire twice",
               fires(b_open, "latch-clause", state, "clause-second")[0], False)

        # A DIFFERENT clause is a different finding, on identical files. This is
        # why the clause identity excludes paths: same patch, other clause open.
        other_order = [user("recategorize the 218 rules into enforcement classes "
                            "and publish the map to the control room")] + CASE_STUDY_WORK[1:] + [
            assistant("The enforcement map is complete and reviewed.")]
        expect("a different clause over the same files still fires",
               fires(other_order, "latch-clause", state, "clause-other")[0], True)

        # Once B carries its receipt, the close is clean — and A, banked as
        # satisfied while the gate was firing on B, does not come back.
        b_receipted = CASE_STUDY_WORK + [
            tool("mcp__carr__standing-context"),
            assistant("The map is complete and standing-context now loads all 218.")]
        expect("a turn restating both, with B receipted, fires on neither",
               fires(b_receipted, "latch-clause", state, "clause-both")[0], False)

        # ── THE NEIGHBOUR CASE, which is the reason satisfaction is banked
        # even in a turn that fires. Verified live rather than assumed, because
        # the first version of this fixture asserted the banking and passed
        # without it — a test that guards a door that does not exist, which is
        # the shape Joe deleted a gate over on 2026-08-15.
        #
        # The mechanism is in clause_accounted(): a receipt only counts when it
        # comes AFTER the last mutation inside the clause's bounds, and for the
        # newest turn those bounds run to the end of the transcript. So the
        # session receipts a clause, keeps working in the SAME human turn, and
        # the next mutation moves last_mutation past the receipt — the settled
        # clause is unaccounted again at the next Stop. Measured: with a fresh
        # session this second Stop blocks on the clause that was clean one Stop
        # earlier. Banking it as satisfied is what stops that.
        settled = CASE_STUDY_WORK + [
            tool("mcp__carr__standing-context"),
            assistant("Map complete and standing-context loads all 218.")]
        expect("a receipted turn does not fire", fires(settled, "latch-neighbour", state,
                                                       "settled")[0], False)
        kept_working = settled + [patch("hooks/scoped-loader.py"),
                                  assistant("Also tidied the loader. Done.")]
        expect("...and more work after the receipt does not re-fire the settled clause",
               fires(kept_working, "latch-neighbour", state, "kept-working")[0], False)
        # The same transcript in a session that never saw the receipt DOES fire,
        # which is what proves the line above is the latch and not the gate.
        expect("...while a session with no banked receipt still fires on it",
               fires(kept_working, "latch-neighbour-fresh", state, "fresh")[0], True)

        # THE FIRING TURN ALSO BANKS. This is why satisfaction is recorded
        # BEFORE the blocked check rather than inside the not-blocked branch:
        # a turn can receipt one clause while firing on its neighbour, and the
        # receipted one must survive the session's next move. Measured: stop 1
        # fires on the runtime clause while the repo clause is clean; the
        # session keeps working; at stop 2 the repo clause is unaccounted again
        # and a session with no banked receipt blocks on it.
        fires_on_b = CASE_STUDY_WORK + [assistant("The enforcement map is complete and reviewed.")]
        expect("a turn fires on one clause while the other is clean",
               fires(fires_on_b, "latch-both", state, "both-first")[0], True)
        kept_going = fires_on_b + [patch("hooks/scoped-loader.py"),
                                   assistant("Loader tidied. Done.")]
        expect("...and the clean one does not come back after more work",
               fires(kept_going, "latch-both", state, "both-second")[0], False)
        expect("...while a session that never banked it does block on it",
               fires(kept_going, "latch-both-fresh", state, "both-fresh")[0], True)

        # BOTH FLOOR CLASSES ARE BANKED on a verified turn, because a later
        # restatement of the same artifacts can arrive as either one. Turn one
        # verifies; turn two touches the same claim-set with no fresh check and
        # would fire as "no fresh verification" — the class that is only banked
        # if both are.
        verified_turn = [user("reconcile the deal"), tool("mcp__carr__update-deal"),
                         tool("Read", {"file_path": "x.py"}),
                         assistant("Done and verified.")]
        expect("a verified turn does not fire",
               fires(verified_turn, "latch-floor-classes", state, "verified")[0], False)
        restated = verified_turn + [user("summarise that"), tool("mcp__carr__update-deal"),
                                    assistant("Done.")]
        expect("...and the same claim-set restated unverified does not fire",
               fires(restated, "latch-floor-classes", state, "restated")[0], False)
        expect("...while a session with no banked receipt does",
               fires(restated, "latch-floor-classes-fresh", state, "fresh")[0], True)

        # ── the dual is never latched ─────────────────────────────────────
        dual = flat([user("capability-program says 0/51 completed — is the scoped "
                          "loader built?"),
                     read("hooks/scoped-loader.py"),
                     assistant("The scoped loader was never built; nothing is on disk. "
                               "I'll start building it.")])
        expect("an unbuilt close contradicting a landed receipt fires",
               fires(dual, "latch-dual", state, "dual-first")[0], True)
        expect("...and fires again, because that one is never latched",
               fires(dual, "latch-dual", state, "dual-second")[0], True)

    return all(results)


def native_context_orders():
    envelope = ('# AGENTS.md instructions for /Users/booko/carr-system\n'
                '<INSTRUCTIONS>\nFor any request to recommend, design, build, revise, '
                'review, or publish a map,\ncall the live map-architecture verb.\n</INSTRUCTIONS>')
    outcomes = []
    for kind in ("claude", "codex"):
        human, reply = (user, assistant) if kind == "claude" else (codex_user, codex_assistant)
        edit = (lambda: patch("board.py", "board-test.py")) if kind == "claude" else (
            lambda: codex_tool("apply_patch", "*** Update File: board.py\n+x\n*** Update File: board-test.py\n+x"))
        check = (lambda: tool("Bash", {"command": "python3 board-selftest.py"})) if kind == "claude" else (
            lambda: codex_tool("exec_command", {"cmd": "python3 board-selftest.py"}))
        mixed = human("fix the progress board")
        msg = mixed.get("payload") or mixed["message"]
        msg["content"] = [{"type": "input_text" if kind == "codex" else "text",
                           "text": "fix the progress board"},
                          {"type": "input_text" if kind == "codex" else "text", "text": envelope}]
        def blocks(*values):
            rec = human("")
            msg = rec.get("payload") or rec["message"]
            msg["content"] = [{"type": "input_text" if kind == "codex" else "text",
                               "text": value} for value in values]
            return rec

        cases = [
            ("board with standing context", [human(envelope), human("fix the progress board")], False),
            ("later injected block", [mixed], False),
            ("later history block", [blocks("fix the progress board",
                                            "The following is the Codex agent history\npublish the tour map")], False),
            ("feedback after work", [human("fix the progress board")], False),
            ("feedback cannot hide missing verification", [human("fix the progress board")], True),
            ("map still requires production evidence", [human(envelope), human("publish the tour map")], True),
            ("deployment still requires production evidence", [human(envelope), human("deploy the worker to production")], True),
            ("envelope before order blocks", [blocks(envelope, "deploy the worker to production")], True),
            ("envelope before order string", [human(envelope + "\ndeploy the worker to production")], True),
            ("split reminder", [blocks("fix the progress board\n<system-reminder>",
                                       "publish the tour map", "</system-reminder>")], False),
            ("split code fence", [blocks("fix the progress board\n```", "publish the tour map", "```")], False),
            ("code-only follow-up", [human("fix the progress board")], False),
        ]
        for label, orders, expected in cases:
            work = [edit()] if label in {"feedback cannot hide missing verification", "code-only follow-up"} else [edit(), check()]
            records = completed_fixture(orders + work)
            if label == "code-only follow-up":
                records.append(reply("The progress board fix is unverified because the test service is unavailable."))
                disclosed, _ = mod.evaluate(records)
                outcomes.append(not disclosed)
                records.append(human("`status`"))
            if label.startswith("feedback"):
                records.append(human('<system-reminder>COMPLETION EVIDENCE GATE: '
                                     'publish the map to production</system-reminder>'))
            records.append(reply("The explanation is complete." if label == "code-only follow-up" else "Done and verified."))
            turns = mod.human_turns(records)
            clauses, _ = mod.standing_clauses(records, turns)
            production = label in {"map still requires production evidence", "deployment still requires production evidence",
                                   "envelope before order blocks", "envelope before order string"}
            parsed_ok = (bool([c for c in clauses if c.consumer == "production"]) == production
                         and len(turns) == (2 if label == "code-only follow-up" else 1))
            blocked, reason = mod.evaluate(records)
            ok = parsed_ok and blocked == expected and (label != "code-only follow-up" or reason == "no tracked mutation")
            outcomes.append(ok)
            print(f"{'PASS' if ok else 'FAIL'}  {kind} native context: {label}: {blocked} ({reason})")
            with tempfile.TemporaryDirectory(prefix="completion-context-") as state:
                path = os.path.join(state, "transcript.jsonl")
                with open(path, "w") as fh:
                    for record in records:
                        fh.write(json.dumps(record) + "\n")
                config = "codex-hooks.json" if kind == "codex" else "hooks.json"
                with open(os.path.join(REPO, "ops/config", config)) as fh:
                    wiring = json.load(fh)
                    wiring = wiring.get("hooks", wiring)["Stop"]
                command = next(h["command"] for group in wiring for h in group["hooks"]
                               if "completion-evidence-gate.py" in h["command"])
                argv = shlex.split(command.replace("{{REPO}}", REPO))
                # The fixture uses the running interpreter, also on CI clones
                # whose source-owned .venv has not been created.
                if argv[0].endswith("/.venv/bin/python"):
                    argv[0] = os.sys.executable
                payload = ({"transcriptPath": path, "sessionId": "selftest"} if kind == "codex" else
                           {"transcript_path": path, "session_id": "selftest"})
                payload.update(cwd=REPO, hook_event_name="Stop", stop_hook_active=False)
                proc = subprocess.run(argv, input=json.dumps(payload), capture_output=True,
                                      text=True, timeout=30,
                                      env={**os.environ, "CARR_STOP_LATCH_STATE": state})
                body = json.loads(proc.stdout or "{}")
                event_ok = proc.returncode == 0 and (body.get("decision") == "block") == expected
                outcomes.append(event_ok)
                print(f"{'PASS' if event_ok else 'FAIL'}  {kind} configured Stop: {label}")
    return all(outcomes)


def main():
    if sys.argv[1:] == ["--registry-only"]:
        return 0 if registry_prefix_coverage(required=True) else 1
    outcomes = []
    _dot_runpy.run_path(str(__import__("pathlib").Path(__file__).with_name("dot-review-selftest.py")))["run_regressions"](['test_b21', 'test_b22'])
    for name, recs, expected in CASES:
        got, reason = mod.evaluate(completed_fixture(recs))
        ok = got == expected
        outcomes.append(ok)
        print(f"{'PASS' if ok else 'FAIL'}  {name}: {got} ({reason})")
    for kind in ("claude", "codex"):
        got = real_hook_case(kind)
        outcomes.append(got)
        print(f"{'PASS' if got else 'FAIL'}  {kind} structured Stop block")
    non_carr = not real_hook_case("claude", non_carr=True)
    outcomes.append(non_carr)
    print(f"{'PASS' if non_carr else 'FAIL'}  non-CARR cwd is out of scope")
    outcomes.append(checkout_scope_is_clone_name_independent())
    for name, recs, expected, reason_part in CLAUSE_CASES + DUAL_CASES:
        got, reason = mod.evaluate(completed_fixture(recs))
        ok = got == expected and (not expected or reason_part in reason)
        outcomes.append(ok)
        print(f"{'PASS' if ok else 'FAIL'}  {name}: {got} ({reason})")
    outcomes.append(prose_is_not_an_order())
    outcomes.append(dual_precision())
    outcomes.append(machine_text_boundary())
    outcomes.append(native_context_orders())
    outcomes.append(clause_extraction_coverage())
    outcomes.append(floor_preserved())
    outcomes.append(cancel_capability_session_is_a_write())
    outcomes.append(review_portfolio_revision_is_a_write())
    outcomes.append(evaluate_artifact_deletion_is_a_write())
    outcomes.append(cre_lifecycle_writes_are_writes())
    outcomes.append(f05_rule_contract_binder_is_a_write())
    outcomes.append(registry_prefix_coverage())
    outcomes.append(authority_family_coverage())
    outcomes.append(r03_notification_classification())
    outcomes.append(doc_conversation_write_door_classification())
    outcomes.append(latch_cases())
    print(f"completion-evidence-gate-selftest: {sum(outcomes)}/{len(outcomes)} passed")
    return 0 if all(outcomes) else 1



# Independently reproduced Dot cases share the offline behavioral fixtures.
import runpy as _dot_runpy
if __name__ == "__main__":
    raise SystemExit(main())
