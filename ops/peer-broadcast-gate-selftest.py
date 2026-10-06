#!/usr/bin/env python3
"""peer-broadcast-gate-selftest.py — prove the gate blocks fan-out and nothing else.

The failure it guards is asymmetric: blocking a legitimate reply is worse than
letting one extra ask through, because a session that cannot answer a peer is
broken in a way that is hard to diagnose from the other end. So the allow cases
carry as much weight here as the deny case.
"""
import json
import os
import subprocess
import sys
import tempfile

REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
GATE = os.path.join(REPO, "hooks", "peer-broadcast-gate.py")

sys.path.append(os.path.join(os.path.dirname(os.path.abspath(__file__)), os.pardir, "lib"))
from selftest_harness import Checker  # noqa: E402

CHECKER = Checker()
failures = CHECKER.failures
check = CHECKER.check


def run(to, state_dir, tool="SendMessage", transcript=None, transcript_key="transcript_path", session=None):
    """Invoke the gate with an isolated HOME so the real state file is untouched."""
    env = {**os.environ, "HOME": state_dir}
    body = {"tool_name": tool, "tool_input": {"to": to}}
    if session is not None:
        body["session_id"] = session
    if transcript is not None:
        body[transcript_key] = transcript
    p = subprocess.run([sys.executable, GATE], input=json.dumps(body),
                       capture_output=True, text=True, env=env, timeout=60)
    return p.returncode, (p.stdout + p.stderr)


def spawn_record(agent_id):
    """One transcript line as the harness actually writes it when the Agent tool
    launches a subagent — verified against a real session transcript
    (db8cb4af, 2026-08-14): the spawn is a toolUseResult carrying agentId."""
    return json.dumps({
        "type": "user",
        "toolUseResult": {"isAsync": True, "status": "async_launched",
                          "agentId": agent_id, "description": "worker"},
    })


def write_transcript(d, lines):
    path = os.path.join(d, "transcript.jsonl")
    with open(path, "w") as fh:
        fh.write("\n".join(lines) + "\n")
    return path


print("peer-broadcast-gate")

# 1. The core case: two named peers pass, the third is refused.
with tempfile.TemporaryDirectory() as d:
    rc1, _ = run("carr-ai-01 [aaa]", d)
    rc2, _ = run("carr-ai-02 [bbb]", d)
    rc3, out3 = run("carr-ai-03 [ccc]", d)
    check("first named peer is allowed", rc1 == 0, f"rc={rc1}")
    check("second named peer is allowed", rc2 == 0, f"rc={rc2}")
    check("THIRD named peer is refused", rc3 == 2, f"rc={rc3}")
    check("the refusal names the cheaper route", "git diff origin/main" in out3)
    check("the refusal says replies are not blocked", "Replies are never blocked" in out3)

# 2. Replies must NEVER be blocked, however many are sent.
with tempfile.TemporaryDirectory() as d:
    codes = [run(f"uds:/tmp/cc-socks/{n}.sock", d)[0] for n in range(6)]
    check("six replies in a row are all allowed", codes == [0] * 6, f"got {codes}")

# 3. A reply must not consume the named-peer budget either.
with tempfile.TemporaryDirectory() as d:
    run("uds:/tmp/cc-socks/1.sock", d)
    run("uds:/tmp/cc-socks/2.sock", d)
    rc, _ = run("carr-ai-01 [aaa]", d)
    check("replies do not use up the named-peer allowance", rc == 0, f"rc={rc}")

# 4. Re-messaging the SAME peer is a thread, not fan-out — never blocked.
with tempfile.TemporaryDirectory() as d:
    run("carr-ai-01 [aaa]", d)
    run("carr-ai-02 [bbb]", d)
    codes = [run("carr-ai-01 [aaa]", d)[0] for _ in range(3)]
    check("continuing a thread with a reached peer stays allowed", codes == [0, 0, 0],
          f"got {codes}")

# 4b. THE SAME PEER UNDER BOTH SPELLINGS IS STILL ONE PEER.
#
# The deadlock this closes, hit 2026-08-14. SendMessage refuses a bare name it
# cannot resolve unambiguously — "'x' is not an agent in this conversation.
# Re-send with the ref to confirm you mean: x [d7e9b2]" — so the caller must use
# the ref form. The gate keyed on the raw string, so `x` and `x [d7e9b2]` counted
# as two different sessions. A caller who had already reached `x` was then told
# `x [d7e9b2]` was "another one", while the refusal text listed `x` as already
# messaged in the same sentence. Neither spelling could be sent: bare was refused
# by the tool, ref-qualified by the gate.
#
# Identity is the NAME. The ref is a disambiguator the tool asks for, not part of
# who the peer is.
# Asserted with the budget ALREADY FULL, which is the only form that proves
# anything. With one peer reached there is spare allowance, so a second spelling
# passes whether or not the gate understands it is the same session — the first
# draft of these two cases passed against the unfixed gate for exactly that
# reason. Filling the budget first makes the assertion about identity.
with tempfile.TemporaryDirectory() as d:
    run("carr-ai-01", d)                       # reached bare
    run("carr-ai-02 [bbb]", d)                 # budget now full
    rc, out = run("carr-ai-01 [d7e9b2]", d)    # same peer as the first, ref-qualified
    check("the ref-qualified form of a peer already reached by bare name is allowed",
          rc == 0, f"rc={rc}: {out[:200]}")

with tempfile.TemporaryDirectory() as d:
    run("carr-ai-01 [aaa]", d)                 # reached ref-qualified
    run("carr-ai-02 [bbb]", d)                 # budget now full
    rc, out = run("carr-ai-01", d)             # same peer, bare
    check("and the reverse: bare name after the ref-qualified form is allowed",
          rc == 0, f"rc={rc}: {out[:200]}")

# The budget must not be spent twice by one peer under two spellings, or two
# genuine peers become unreachable after one.
with tempfile.TemporaryDirectory() as d:
    run("carr-ai-01", d)
    run("carr-ai-01 [aaa]", d)      # same peer, must not consume a second slot
    rc, out = run("carr-ai-02 [bbb]", d)
    check("two spellings of one peer consume ONE slot, not two", rc == 0,
          f"rc={rc}: {out[:160]}")

# And the gate must still count genuinely different peers, ref or not — the fix
# must not become a way to spend an unlimited budget by varying the suffix.
with tempfile.TemporaryDirectory() as d:
    run("carr-ai-01 [aaa]", d)
    run("carr-ai-02 [bbb]", d)
    rc, _ = run("carr-ai-03", d)
    check("a genuinely third peer is still refused, bare or not", rc == 2, f"rc={rc}")

with tempfile.TemporaryDirectory() as d:
    run("carr-ai-01 [aaa]", d)
    run("carr-ai-02 [bbb]", d)
    rc, out = run("carr-ai-03 [ccc]", d)
    check("the refusal lists peers by name, without the ref noise",
          rc == 2 and "carr-ai-01" in out and "[aaa]" not in out,
          f"rc={rc}: {out[:200]}")

# 4c. A SESSION'S OWN SUBAGENTS ARE ORCHESTRATION, NOT BROADCAST.
#
# The false positive, hit 2026-08-14 (session db8cb4af): an orchestrating
# session spawned workers via the Agent tool and messaged them by agentId. The
# gate counted those workers as "sessions", filled the budget with them, and
# then refused the session's OWN next worker — listing one of its own subagents
# as an already-messaged peer in the refusal.
#
# The gate's rationale does not apply here. Its whole justification is the
# EXTERNALISED cost — the asker pays one message, every other session on the
# machine pays for reading and answering. A subagent's reply lands in the
# asker's own context; the asker pays the full cost itself. There is nobody to
# protect.
#
# Identity must be PROVEN, not pattern-matched: a recipient is exempt only when
# THIS session's transcript records spawning that agentId. A recipient merely
# SHAPED like an agentId is still a peer — otherwise naming a session
# 'a0123456789abcdef' would spend an unlimited budget.

# Own subagent, budget already full — the incident, replayed. With spare budget
# this passes against the unfixed gate, so it is asserted at the point the old
# gate refused.
with tempfile.TemporaryDirectory() as d:
    t = write_transcript(d, [spawn_record("a4da4a7e30149a307"),
                             spawn_record("aabf255ee92301b74")])
    run("carr-ai-01 [aaa]", d, transcript=t)
    run("carr-ai-02 [bbb]", d, transcript=t)   # budget now full
    rc, out = run("aabf255ee92301b74", d, transcript=t)
    check("an own subagent is allowed even with the peer budget full",
          rc == 0, f"rc={rc}: {out[:200]}")

# Own subagents must not consume the budget either — the other direction of the
# same incident: two worker messages first, then two genuine peers.
with tempfile.TemporaryDirectory() as d:
    t = write_transcript(d, [spawn_record("a4da4a7e30149a307"),
                             spawn_record("aabf255ee92301b74")])
    run("a4da4a7e30149a307", d, transcript=t)
    run("aabf255ee92301b74", d, transcript=t)
    rc1, out1 = run("carr-ai-01 [aaa]", d, transcript=t)
    rc2, out2 = run("carr-ai-02 [bbb]", d, transcript=t)
    rc3, _ = run("carr-ai-03 [ccc]", d, transcript=t)
    check("own subagents do not consume the named-peer allowance",
          rc1 == 0 and rc2 == 0, f"rc1={rc1} rc2={rc2}: {(out1 + out2)[:160]}")
    check("and a genuinely third PEER is still refused alongside subagent traffic",
          rc3 == 2, f"rc={rc3}")

# The exemption is earned by the transcript, not by the shape of the name. A
# recipient that merely looks like an agentId but was never spawned by this
# session is a peer and spends the budget like one.
with tempfile.TemporaryDirectory() as d:
    t = write_transcript(d, [spawn_record("a4da4a7e30149a307")])
    run("carr-ai-01 [aaa]", d, transcript=t)
    run("carr-ai-02 [bbb]", d, transcript=t)
    rc, _ = run("a0123456789abcdef", d, transcript=t)
    check("an agentId-shaped recipient NOT spawned by this session is still refused",
          rc == 2, f"rc={rc}")

# An agentId that appears in the transcript only as TEXT — quoted in a message,
# pasted in a tool result — is not a spawn record. Only a structural
# toolUseResult.agentId proves ownership; anything less is spoofable by content.
with tempfile.TemporaryDirectory() as d:
    mention = json.dumps({"type": "assistant", "message": {
        "content": 'peer said its id is "agentId": "adecoy0123456789a"'}})
    t = write_transcript(d, [spawn_record("a4da4a7e30149a307"), mention])
    run("carr-ai-01 [aaa]", d, transcript=t)
    run("carr-ai-02 [bbb]", d, transcript=t)
    rc, _ = run("adecoy0123456789a", d, transcript=t)
    check("an agentId mentioned only in message text earns no exemption",
          rc == 2, f"rc={rc}")

# No transcript in the payload, or a transcript that cannot be read, means no
# exemption can be proven — the recipient counts as a peer, the gate keeps
# working, and nothing crashes.
with tempfile.TemporaryDirectory() as d:
    run("carr-ai-01 [aaa]", d)
    run("carr-ai-02 [bbb]", d)
    rc, _ = run("aabf255ee92301b74", d)
    check("without a transcript_path the recipient counts as a peer",
          rc == 2, f"rc={rc}")

with tempfile.TemporaryDirectory() as d:
    gone = os.path.join(d, "no-such-transcript.jsonl")
    run("carr-ai-01 [aaa]", d, transcript=gone)
    run("carr-ai-02 [bbb]", d, transcript=gone)
    rc, _ = run("aabf255ee92301b74", d, transcript=gone)
    check("an unreadable transcript denies the exemption without crashing",
          rc == 2, f"rc={rc}")

# The camelCase payload spelling some hook events use must work too.
with tempfile.TemporaryDirectory() as d:
    t = write_transcript(d, [spawn_record("aabf255ee92301b74")])
    run("carr-ai-01 [aaa]", d, transcript=t, transcript_key="transcriptPath")
    run("carr-ai-02 [bbb]", d, transcript=t, transcript_key="transcriptPath")
    rc, out = run("aabf255ee92301b74", d, transcript=t,
                  transcript_key="transcriptPath")
    check("the camelCase transcriptPath key is honoured as well",
          rc == 0, f"rc={rc}: {out[:160]}")

# 5. It must not touch any other tool.
with tempfile.TemporaryDirectory() as d:
    run("carr-ai-01 [aaa]", d)
    run("carr-ai-02 [bbb]", d)
    rc, _ = run("carr-ai-03 [ccc]", d, tool="Bash")
    check("a non-SendMessage tool is never gated", rc == 0, f"rc={rc}")

# 6. A malformed payload must fail OPEN. A gate that crashes on bad input
#    becomes an outage in every session that sends a message.
p = subprocess.run([sys.executable, GATE], input="not json at all",
                   capture_output=True, text=True, timeout=60)
check("a malformed payload fails open, never blocks", p.returncode == 0,
      f"rc={p.returncode}")

# 7. An empty recipient is not a broadcast.
with tempfile.TemporaryDirectory() as d:
    rc, _ = run("", d)
    check("an empty recipient is ignored", rc == 0, f"rc={rc}")

# 8. THE BUDGET IS PER SESSION (2026-08-23, this task's phase-2 scoping).
# The state used to be one shared file, so parallel sessions spent ONE combined
# budget: session B's first targeted ask was refused because sessions A and C
# had messaged someone. Two distinct session ids must now hold two independent
# budgets, and the same peer reached from two sessions must cost each of them
# one slot — not be deduplicated into a shared ledger entry.
with tempfile.TemporaryDirectory() as d:
    run("carr-ai-01 [aaa]", d, session="sess-a")
    run("carr-ai-02 [bbb]", d, session="sess-a")     # A's budget full
    rc_a, _ = run("carr-ai-03 [ccc]", d, session="sess-a")
    rc_b1, _ = run("carr-ai-01 [aaa]", d, session="sess-b")
    rc_b2, _ = run("carr-ai-02 [bbb]", d, session="sess-b")
    check("a full budget in session A does not refuse session B's first ask",
          rc_b1 == 0, f"rc={rc_b1}")
    check("session B holds its OWN two-peer budget", rc_b2 == 0 and rc_a == 2,
          f"a3={rc_a} b1={rc_b1} b2={rc_b2}")

# 9. SUBAGENTS SHARE THE PARENT'S SESSION_ID (2026-09-23). A helper's sends
# were charged to its parent's budget, and a nested helper (spawned by a
# subagent, so absent from the parent transcript) was never proven own.
def nested_agent_file(transcript, agent_id):
    folder = os.path.join(transcript[:-6], "subagents")
    os.makedirs(folder, exist_ok=True)
    with open(os.path.join(folder, f"agent-{agent_id}.jsonl"), "w") as fh:
        fh.write("{}\n")

with tempfile.TemporaryDirectory() as d:
    t = write_transcript(d, [])
    run("carr-ai-01 [aaa]", d, transcript=t, session="s9")
    run("carr-ai-02 [bbb]", d, transcript=t, session="s9")   # budget full
    rc, _ = run("main", d, transcript=t, session="s9")
    check("'main' (a subagent's own parent) is exempt even on a full budget", rc == 0, f"rc={rc}")

with tempfile.TemporaryDirectory() as d:
    t = write_transcript(d, [])
    run("main", d, transcript=t, session="s9b")
    run("carr-ai-01 [aaa]", d, transcript=t, session="s9b")
    rc, _ = run("carr-ai-02 [bbb]", d, transcript=t, session="s9b")
    check("'main' is never charged to the budget", rc == 0, f"rc={rc}")

with tempfile.TemporaryDirectory() as d:
    t = write_transcript(d, [])
    nested_agent_file(t, "a9533e8dd38b052db")
    run("carr-ai-01 [aaa]", d, transcript=t, session="s9c")
    run("carr-ai-02 [bbb]", d, transcript=t, session="s9c")
    rc, _ = run("a9533e8dd38b052db [92e6b3]", d, transcript=t, session="s9c")
    check("a nested agent proven by the harness's subagents/ file is exempt", rc == 0, f"rc={rc}")

with tempfile.TemporaryDirectory() as d:
    t = write_transcript(d, [])
    nested_agent_file(t, "x")
    run("carr-ai-01 [aaa]", d, transcript=t, session="s9d")
    run("carr-ai-02 [bbb]", d, transcript=t, session="s9d")
    rc1, _ = run("../subagents/agent-x", d, transcript=t, session="s9d")
    rc2, _ = run("sub/x", d, transcript=t, session="s9d")
    check("a name with '..' or '/' earns no path exemption", rc1 == 2 and rc2 == 2,
          f"rc1={rc1} rc2={rc2}")

with tempfile.TemporaryDirectory() as d:
    t = write_transcript(d, [])
    nested_agent_file(t, "a9533e8dd38b052db")
    run("carr-ai-01 [aaa]", d, transcript=t, session="s9e")
    run("carr-ai-02 [bbb]", d, transcript=t, session="s9e")
    rc, _ = run("carr-ai-03 [ccc]", d, transcript=t, session="s9e")
    check("a real peer is still charged and refused past the budget", rc == 2, f"rc={rc}")

sys.exit(CHECKER.summary())
