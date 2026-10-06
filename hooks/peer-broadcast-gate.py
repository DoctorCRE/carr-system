#!/usr/bin/env python3
"""peer-broadcast-gate.py — one question goes to one session, not to the room.

WHY (Joe, 2026-08-14): "why are all my sessions repeating there reports. this
shit is burning a lot of wasted tokens. fix it now"

WHAT HAPPENED. A session could not tell which peer owned five modified files, so
it asked nine of them. Seven replied, each with a multi-paragraph inventory of
its own session's work, because a session answering "are these yours" defends
itself by reciting everything it wrote. Every reply also landed in the asker's
context. Sixteen long messages.

The answer had been one command away the entire time. `git diff origin/main`
showed all five files byte-identical to the remote — there was nothing to own.
The "238 lines at risk" had been measured against a local HEAD five commits
behind. Nine sessions were interrupted to answer a question the repository had
already answered.

WHY A GATE AND NOT A RULE. The cost is externalised: the asker pays for one
message and every other session on the machine pays for reading and answering
it. A cost you do not feel is one you do not stop paying, which is what makes
this a bad fit for an advisory rule and a good fit for something that denies.

WHAT IT ALLOWS, deliberately. Replies are never blocked — answering a session
that wrote to you first is a conversation, not a broadcast, and a reply address
(uds:/tmp/cc-socks/...) is how those are addressed. Two named peers per window
are also allowed: asking the likely owner, being told no, and asking the next
best candidate is legitimate targeted work. The THIRD distinct named peer in the
same window is the fan-out this exists to stop.

It gates breadth, not content. It cannot tell whether a question was worth
asking; it can tell that the same session is working through a roster.

OWN SUBAGENTS ARE NOT PEERS (2026-08-14, second false positive that day). An
orchestrating session spawned workers with the Agent tool and messaged them by
agentId (`aabf255ee92301b74`); the gate counted each worker as a "session",
filled the budget with them, and refused the session's own next worker. But the
externalised-cost rationale above is the entire justification for denying, and
it does not apply to a subagent: the reply lands in the asker's own context, so
the asker pays the full cost itself. That is orchestration, not interrogation.

The exemption is PROVEN, never pattern-matched. The hook payload carries this
session's transcript_path, and the transcript records every Agent-tool spawn as
a structural toolUseResult.agentId — so "is this recipient my own subagent" is
answerable from ground truth, the same attribution stance as
staging-attribution-gate.py. A recipient merely SHAPED like an agentId, or an
id that appears only as text inside a message, stays a peer: matching on shape
would let a session spend an unlimited budget on anything named like a hex
token, and #70 already showed what keying identity on spelling does. No
transcript, or an unreadable one, means no exemption — the gate keeps its
protection rather than guessing.

SUBAGENTS SHARE THE PARENT'S SESSION_ID (2026-09-23, Joe's order after it
refused him). A subagent's hook payload carries its parent's session_id, so its
sends were charged to the PARENT's budget: a seal helper messaging "main" and
its own nested helper filled the ledger, and the parent's single Joe-directed
message to the Orchestrator was refused as the third name. Two holes, two
proofs, no patterns:

  - "main" is the harness address a subagent uses to reach ITS OWN parent. The
    reply lands in the same session, so it is exempt: neither refused nor
    charged.
  - own_subagent() read only the payload's transcript, which is the PARENT's,
    so a helper spawned by a subagent was never proven. The harness writes
    <transcript without .jsonl>/subagents/agent-<id>.jsonl for EVERY agent
    spawned anywhere in this session's tree, nested helpers included. That file
    is ground truth like the spawn record is, and it now proves ownership too.
    The id is used in a path, so only [A-Za-z0-9_-]+ may use it; any other
    spelling ("/", "..") gets no exemption.
"""
import json
import os
import re
import sys
import time

STATE_DIR = os.path.expanduser("~/.cache/carr/peer-broadcast-gate")
WINDOW_SECONDS = 1800   # 30 minutes: long enough to span one investigation
MAX_NAMED_PEERS = 2     # the third distinct named peer is a broadcast

# A PEER IS ITS NAME. The ref is a disambiguator, not part of who it is.
#
# ListAgents prints `name [ref]`, and SendMessage REQUIRES that form whenever a
# bare name is ambiguous or unconfirmed: "'x' is not an agent in this
# conversation. Re-send with the ref to confirm you mean: x [d7e9b2]". This gate
# keyed on the raw string, so `x` and `x [d7e9b2]` counted as two sessions.
#
# That deadlocked on 2026-08-14. A caller who had already reached `x` was told
# `x [d7e9b2]` was "another one" — while the refusal listed `x` as already
# messaged in the same sentence — and neither spelling could be sent: the bare
# name was refused by the tool, the ref-qualified one by this gate. The single
# targeted message this gate exists to encourage was the one thing that could
# not be delivered.
#
# Anchored to the END and deliberately narrow: only a trailing bracketed token of
# ref-shaped characters is stripped, so a name that merely contains a bracket is
# untouched. Reply addresses return before this is ever consulted.
REF_SUFFIX = re.compile(r"\s*\[[0-9A-Za-z._:-]+\]\s*$")
# An agent id is spliced into a path, so it may only be these characters.
AGENT_ID = re.compile(r"[A-Za-z0-9_-]+")
# The harness address a subagent uses to reach its own parent session.
PARENT_ADDRESS = "main"


def peer_identity(to: str) -> str:
    """The stable key for a recipient: its name, with any trailing [ref] removed.

    Falls back to the original string if stripping would leave nothing, so a
    recipient that is ONLY a bracketed token still counts as a peer rather than
    collapsing to empty and silently sharing one slot with every other such name.
    """
    return REF_SUFFIX.sub("", to).strip() or to


def own_subagent(who: str, transcript_path: str) -> bool:
    """True iff THIS session's transcript records spawning agent `who`.

    The Agent tool's spawn lands in the transcript as a toolUseResult carrying
    agentId (verified against a live transcript, 2026-08-14, session db8cb4af).
    Only that structural record counts. The line pre-filter is plain substring —
    an agentId quoted inside a message string is escaped in the JSONL and never
    parses out as toolUseResult.agentId, so text mentions cannot mint an
    exemption. Any failure to read or parse means False: an unproven subagent
    is charged as a peer, never waved through.
    """
    if not transcript_path:
        return False
    # A nested helper's spawn is in its spawner's transcript, not this one; the
    # harness's own per-agent file under this session's subagents/ proves it.
    if AGENT_ID.fullmatch(who or ""):
        base = transcript_path[:-6] if transcript_path.endswith(".jsonl") else transcript_path
        if os.path.isfile(os.path.join(base, "subagents", f"agent-{who}.jsonl")):
            return True
    try:
        with open(transcript_path, errors="ignore") as fh:
            for line in fh:
                if "agentId" not in line or who not in line:
                    continue
                try:
                    rec = json.loads(line)
                except ValueError:
                    continue
                result = rec.get("toolUseResult")
                if isinstance(result, dict) and result.get("agentId") == who:
                    return True
    except OSError:
        return False
    return False


def state_path(session):
    """One state file PER SESSION, keyed by the harness session id.

    WHY PER SESSION. The state used to be one shared file, so eleven parallel
    sessions on this Mac spent ONE combined budget of two named peers: a
    session's first targeted ask could be refused because three OTHER sessions
    had messaged someone in the last half hour. The gate's whole rationale is
    that the asker pays the cost of what it sends — but a shared ledger charges
    a session for asks it never made, which is the exact inversion the
    completion-integrity council called authority drift. Fan-out breadth is a
    property of ONE session's behaviour; the window and budget stay as they
    were.

    An unknown or missing session id falls back to a single shared "unknown"
    file rather than guessing: an unattributed caller keeps the old (stricter)
    behaviour instead of minting a fresh budget per malformed payload.
    """
    safe = re.sub(r"[^A-Za-z0-9_.-]", "_", str(session or "").strip())[:80]
    if not safe:
        safe = "unknown"
    return os.path.join(STATE_DIR, f"{safe}.json")


def load(now, session):
    path = state_path(session)
    try:
        with open(path) as fh:
            data = json.load(fh)
    except (OSError, ValueError):
        return {}
    # Drop anything older than the window so a long session is not punished for
    # a burst it sent hours ago.
    #
    # Keys are normalised ON READ, not only on write, so state written before the
    # ref fix heals itself instead of keeping a session deadlocked until the
    # window expires. Where both spellings are present the NEWER timestamp wins,
    # which is what the live path would have recorded had it always normalised.
    fresh = {}
    for who, ts in data.items():
        if not isinstance(ts, (int, float)) or now - ts >= WINDOW_SECONDS:
            continue
        key = peer_identity(str(who))
        if ts > fresh.get(key, 0):
            fresh[key] = ts
    return fresh


def save(data, session):
    os.makedirs(STATE_DIR, exist_ok=True)
    tmp = state_path(session) + ".tmp"
    with open(tmp, "w") as fh:
        json.dump(data, fh)
    os.replace(tmp, state_path(session))


def main():
    try:
        payload = json.load(sys.stdin)
    except (ValueError, OSError):
        return 0  # never block on a malformed hook payload

    tool = payload.get("tool_name") or ""
    if not tool.endswith("SendMessage"):
        return 0

    to = str((payload.get("tool_input") or {}).get("to") or "").strip()
    if not to:
        return 0

    # A REPLY IS NOT A BROADCAST. Reply addresses are sockets; a named peer is
    # an outbound ask. Blocking replies would make the gate punish the sessions
    # behaving correctly, which is the opposite of the point.
    if to.startswith("uds:") or to.startswith("/"):
        return 0

    # Identity is the name; `x` and `x [d7e9b2]` are one peer. See REF_SUFFIX.
    who = peer_identity(to)

    # A subagent reaching its own parent: same session, same context, nobody
    # external pays. Exempt before the ledger is even read, so it is never charged.
    if who == PARENT_ADDRESS:
        return 0

    # The budget belongs to THIS session, keyed off the harness payload — see
    # state_path() for why a shared ledger was the wrong shape.
    session = (payload.get("session_id") or payload.get("sessionId") or "")
    now = time.time()
    seen = load(now, session)
    if who in seen:           # re-messaging the same peer is a thread, not fan-out
        seen[who] = now
        save(seen, session)
        return 0

    # MESSAGING YOUR OWN SUBAGENT IS ORCHESTRATION, NOT BROADCAST — the asker
    # pays the whole reply cost itself, so there is nobody this gate would be
    # protecting. Exempt entirely: neither refused nor charged to the budget.
    # Proven against the transcript's spawn records, never inferred from shape.
    transcript = str(payload.get("transcript_path")
                     or payload.get("transcriptPath") or "")
    if own_subagent(who, transcript):
        return 0

    if len(seen) >= MAX_NAMED_PEERS:
        already = ", ".join(sorted(seen))
        print(
            "PEER BROADCAST GATE — refused.\n\n"
            f"You have already messaged {len(seen)} different sessions in the last "
            f"{WINDOW_SECONDS // 60} minutes ({already}), and this is another one. "
            "That is a roster, not a question.\n\n"
            "WHAT THIS COSTS, which you do not pay: every recipient reads it, composes "
            "a reply, and sends it back into your context — and a session asked 'is this "
            "yours' answers by reciting its entire session inventory. On 2026-08-14 nine "
            "asks produced seven multi-paragraph replies, all correct, all wasted.\n\n"
            "DO THIS INSTEAD:\n"
            "  1. ANSWER IT FROM THE REPO. Most peer questions are really questions about "
            "state. `git diff origin/main -- <paths>` settles ownership; `git log`, "
            "`git ls-tree` (which shows MODES, unlike diff) and the record verbs settle "
            "most of the rest. The nine-session broadcast above was answering a question "
            "one diff had already answered.\n"
            "  2. IF A PEER MUST BE ASKED, PICK ONE — the likely owner, from evidence: "
            "markers in the diff itself, the branch the content sits on, the lane the "
            "files belong to.\n"
            "  3. ONLY ASK WHAT A PEER ALONE KNOWS — what it intends, what it is mid-way "
            "through, whether it is about to write. Anything about the state of the tree "
            "is a query, not a conversation.\n\n"
            "Replies are never blocked, and neither is continuing a thread with a session "
            "you have already reached.",
            file=sys.stderr)
        return 2   # deny

    seen[who] = now
    save(seen, session)
    return 0


if __name__ == "__main__":
    sys.exit(main())
