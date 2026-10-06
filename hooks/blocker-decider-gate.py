#!/usr/bin/env python3
"""blocker-decider-gate.py — a capability blocker names its decider, or it
does not file (rule 88e9b5eb).

'NOT AUTHORIZED' AND 'NOT POSSIBLE' ARE DIFFERENT FINDINGS, and a loop row
that says blocker='capability' without saying which one it is hides exactly
the fact its reader needs. If someone COULD grant the credential, gate, or
verb, the row must name that person — in this system that is Joe or Dell —
because a grant nobody is asked for never arrives, and the loop rots as
"blocked" while the decider it is waiting on does not know it exists. If
NOBODY can grant it (the plan lacks the API, the surface has no such control),
the row must say so plainly, so it reads as a limit to design around rather
than a request to wait on.

So on add-loop with blocker='capability', the row's own text must carry one
of the two resolutions:

    a named decider    — Joe or Dell appears in the row's text
    a stated limit     — the text says it is impossible/cannot be granted,
                         rather than merely unauthorized

Everything else — other blocker classes, other verbs, malformed payloads —
passes untouched, and any internal error FAILS OPEN: a wedged filing is worse
than a vague one, and the deferral gate server-side still enforces the rest
of the row's shape.

PreToolUse deny (exit 2), same contract as escalation-gate.py beside it.
Fixtures: ops/blocker-decider-gate-selftest.py.
"""
import json
import os
import re
import sys

# Joe and Dell are the only grant-holders in this system. A word-boundary
# match anywhere in the row's text counts — "Joe grants it", "only Dell can",
# "Joe→Dell" all name the person whose queue this actually sits in.
DECIDER = re.compile(r"\b(joe|dell)\b", re.I)

# The other honest answer: nobody can grant it. Stated impossibility turns
# the row from a request into a recorded limit, which is allowed.
IMPOSSIBLE = re.compile(
    r"\b(impossible|cannot be granted|can'?t be granted|does not exist"
    r"|no such (api|control|permission|capability|feature)"
    r"|not (available|supported) on (this|the) plan|nobody can grant)\b", re.I)

TEXT_FIELDS = ("title", "body", "unblocks", "source_note", "blocker_detail")


def row_text(tool_input) -> str:
    if not isinstance(tool_input, dict):
        return ""
    return "\n".join(str(tool_input.get(f) or "") for f in TEXT_FIELDS
                     if tool_input.get(f))


def needs_decider(tool_input) -> bool:
    if not isinstance(tool_input, dict):
        return False
    if tool_input.get("blocker") != "capability":
        return False
    text = row_text(tool_input)
    return not (DECIDER.search(text) or IMPOSSIBLE.search(text))


REASON = (
    "BLOCKER-DECIDER GATE — refused. blocker='capability' without a decider "
    "is 'not authorized' and 'not possible' filed as the same finding "
    "(rule 88e9b5eb), and they are different findings.\n\n"
    "If someone can grant this capability, NAME THE DECIDER in the row — in "
    "this system that is Joe or Dell — so the request sits in a person's "
    "queue instead of rotting as 'blocked':\n\n"
    "    blocker_detail: \"needs the NEON_API_KEY only Joe holds — Joe "
    "grants it\"\n\n"
    "If nobody can grant it, SAY THAT PLAINLY (e.g. 'no such API on this "
    "plan — genuinely impossible'), so the row reads as a limit to design "
    "around, not a request nobody was asked for.\n"
)


def class_advisory(payload):
    """Show a recorder the defect classes that already exist, before it files.

    A SECOND CONCERN IN THIS FILE, hosted the way ops/command_precheck.py is
    hosted in delegation-gate.py and for the same reason: a new file under
    hooks/ carries a shebang, which makes it a sealed script entrypoint and
    costs a mutation-registry successor — a generated registry, a production
    migration, a fixture rebuilt from origin/main and a disposable PostgreSQL
    round trip. This gate is already registered on exactly the record verbs,
    already reads the same payload, and already runs under the repository
    interpreter, so hosting the dispatch here costs a re-digest of one row.

    WHY IT HAS TO BE A HOOK AT ALL, which is the whole point. The record-defect
    verb's own schema text already asks a session to reuse an existing class.
    The ledger holds 304 singletons across 320 classes. Asking works sometimes;
    a door that opens on the way past works every time.

    IT CANNOT AFFECT THIS GATE. It runs before the blocker logic, catches
    everything, and returns on any failure — no credential, no service, no
    database, bad payload, missing library. The refusal path below is untouched
    either way, and a session that files a defect while the judgment is down
    files it exactly as it does today.
    """
    tool = payload.get("tool_name") or payload.get("toolName") or ""
    if not (tool.startswith("mcp__") and tool.endswith("__record-defect")):
        return
    ti = payload.get("tool_input") or payload.get("toolInput") or {}
    if not isinstance(ti, dict):
        return
    # claimed and actual are the verb's own two required fields — a defect row
    # that states no contradiction is a note, not a defect — and they are what
    # the ranking reads. It scores a class by the contradiction it describes,
    # so passing the class name alone would throw away the half that separates
    # a near-duplicate from a genuine reuse.
    claimed, actual = ti.get("claimed"), ti.get("actual")
    # Both must be real, non-empty strings. Not str(): str(None) is "None" and
    # str(["a"]) is "['a']", each of them truthy, so coercing would send junk
    # to a paid service and rank a defect class against the word None.
    if not (isinstance(claimed, str) and claimed.strip()
            and isinstance(actual, str) and actual.strip()):
        return
    proposed = {"claimed": claimed, "actual": actual}
    try:
        import importlib.util
        spec = importlib.util.spec_from_file_location(
            "jev_defect_class",
            os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                         "ops", "jev_defect_class.py"))
        if spec is None or spec.loader is None:
            return
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        note = module.advise(proposed)
        if note:
            print(json.dumps({"hookSpecificOutput": {
                "hookEventName": "PreToolUse", "additionalContext": note}}))
    except Exception:
        return


def main():
    try:
        payload = json.load(sys.stdin)
    except Exception:
        sys.exit(0)

    class_advisory(payload)

    try:
        tool = payload.get("tool_name") or payload.get("toolName") or ""
        if not (tool.startswith("mcp__") and tool.endswith("__add-loop")):
            sys.exit(0)
        ti = payload.get("tool_input") or payload.get("toolInput") or {}
        if not needs_decider(ti):
            sys.exit(0)
        print(REASON, file=sys.stderr)
        sys.exit(2)
    except SystemExit:
        raise
    except Exception:
        sys.exit(0)


if __name__ == "__main__":
    main()
