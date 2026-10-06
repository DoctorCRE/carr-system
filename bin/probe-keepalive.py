#!/usr/bin/env python3
"""probe-keepalive.py — ask the four long-running servers whether they are up,
and write the answer to the ledger.

THE GAP THIS CLOSES. call-mode, doc-engine, canary-ingest-sink and quill-dictate
are launchd KeepAlive agents. Everything else on this Mac answers "did it fire recently";
these four need "is it up", and nothing could ask it. They are deliberately not
wrapped by bin/run-scheduled.sh — the wrapper records when its child EXITS, and
a KeepAlive child exiting means launchd restarted it, so wrapping would file a
row per restart and read as repeated failures of a service working exactly as
designed. They were therefore registered with no cadence, which made them
honest and invisible at the same time: `ops-record health` had nothing to say
about them, and ops/edge-liveness.py could not help either, because a service
with no cadence never goes stale.

THE MOVE IS TO TURN "IS IT UP" INTO A CADENCE. This probe runs on a schedule and
records one run row per server per pass. That single change makes both existing
mechanisms work on them for free:
  * a server that is DOWN records state=failed, and `ops-record assess` turns
    that into an incident on the same path every other failure takes;
  * if the probe itself stops — the Mac is off, asleep, or logged out — the
    four services go stale on their newly-registered cadence and
    ops/edge-liveness.py alarms from GitHub.
Nothing new had to be invented for either. The probe only had to make them
speak.

── THE FOUR ARE NOT PROBED EQUALLY, AND THAT IS STATED RATHER THAN SMOOTHED ──

    call-mode          TCP 127.0.0.1:4682   a connect proves it is ACCEPTING
    doc-engine         TCP 127.0.0.1:4680   a connect proves it is ACCEPTING
    canary-ingest-sink TCP 127.0.0.1:4684   a connect proves it is ACCEPTING
    quill-dictate      (no socket)          process liveness ONLY

A TCP connect is a real answer: something is listening and completed a
handshake. Process liveness is a weaker claim and must never be reported as if
it were the same one — a wedged process, a deadlocked one, and a healthy one all
have a PID. quill-dictate is a desk dictation agent with no port to knock on, so
process liveness is the strongest signal available, and the detail line on its
run row says which signal was used so nobody reads the row as more than it is.

WHAT THIS PROBE STILL CANNOT SEE, for either kind: a server that accepts a
connection and then answers nonsense. Proving that needs a protocol-level
request, which needs each server's request shape, which is a bigger and
separate piece of work. An honest partial signal beats a confident wrong one,
so the limit is written here rather than discovered later.

── EXIT CODE ──────────────────────────────────────────────────────────────────
0 when the PROBE worked, whatever it found. A down server is carried by the row
it recorded, not by this process's exit code — this script is itself wrapped by
bin/run-scheduled.sh, so exiting non-zero for a down server would file a second
failure against the probe's own service and report one outage twice under two
names. Non-zero here means the probe could not do its job.
"""
import os
import socket
import subprocess
import sys
from datetime import datetime, timezone

REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, REPO)
from lib import machine_role
from lib.launchd_scope import allowed_on_machine

# service key, launchd label, TCP port (None = process liveness only)
TARGETS = [
    ("call-mode", "com.carr.call-mode", 4682),
    ("doc-engine", "com.carr.doc-engine", 4680),
    ("canary-ingest-sink", "com.carr.canary-ingest-sink", 4684),
    ("quill-dictate", "com.carr.quill-dictate", None),
]

CONNECT_TIMEOUT = 3.0


def launchd_pid(label: str):
    """The PID launchd holds for a label, or None when it is not running.

    `launchctl list <label>` prints a plist; a KeepAlive agent that is up has a
    "PID" key. A crashed one that launchd has not yet restarted, or one that is
    throttled, has no PID and is DOWN for this probe's purposes — which is the
    correct reading: for the seconds it is absent, it is not serving anyone.
    """
    try:
        out = subprocess.run(["launchctl", "list", label], capture_output=True,
                             text=True, timeout=15)
    except Exception:
        return None
    if out.returncode != 0:
        return None
    for line in out.stdout.splitlines():
        if '"PID"' in line:
            digits = "".join(c for c in line.split("=", 1)[-1] if c.isdigit())
            return int(digits) if digits else None
    return None


def port_accepts(port: int) -> bool:
    """Loopback only, on purpose. These servers bind 127.0.0.1 and nothing here
    should ever reach off this machine."""
    try:
        with socket.create_connection(("127.0.0.1", port), CONNECT_TIMEOUT):
            return True
    except OSError:
        return False


def probe(label: str, port):
    """Returns (up, signal, detail). `signal` names WHICH question was answered,
    because 'accepting connections' and 'has a PID' are different claims and the
    row must not blur them."""
    pid = launchd_pid(label)
    if pid is None:
        return False, "process", "launchd holds no PID — the agent is not running"
    if port is None:
        return True, "process", f"process alive (pid {pid}); no port to probe"
    if port_accepts(port):
        return True, "accepting", f"accepting connections on 127.0.0.1:{port} (pid {pid})"
    return False, "accepting", (f"pid {pid} is alive but 127.0.0.1:{port} refused a "
                                f"connection — running and not serving")


def record(service: str, up: bool, signal: str, detail: str) -> int:
    py = os.path.join(REPO, ".venv", "bin", "python")
    if not os.access(py, os.X_OK):
        py = sys.executable
    # Through tools/ops-spool.py since 2026-08-18, not ops-record directly: a
    # succeeded probe result is a heartbeat and queues locally for the batch
    # flusher, so six probe passes an hour stop holding Neon awake; a DOWN
    # result still goes to the ledger immediately (the spool tries failed
    # states direct first) because `assess` turns it into an incident. The
    # explicit timestamps exist because a spooled row is INSERTED up to a
    # flush interval after it was observed, and without them ops-record would
    # stamp the row with the flush clock instead of the probe clock.
    observed = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    argv = [py, os.path.join(REPO, "tools", "ops-spool.py"), "run",
            "--kind", "check", "--service", service, "--key", "liveness.probe",
            "--state", "succeeded" if up else "failed",
            "--exit-code", "0" if up else "1",
            "--started-at", observed, "--ended-at", observed,
            "--source-kind", "collector", "--source-ref", "bin/probe-keepalive.py",
            "--detail", f"[{signal}] {detail}"]
    if not up:
        # ops.run's own constraint requires a failure_class for `failed`, and the
        # class names WHICH probe failed so an incident title is readable without
        # opening the row.
        argv += ["--failure-class",
                 "keepalive_not_accepting" if signal == "accepting"
                 else "keepalive_not_running"]
    try:
        r = subprocess.run(argv, capture_output=True, text=True, timeout=120)
        return r.returncode
    except Exception:
        return 1


def main() -> int:
    unrecorded = 0
    primary = machine_role.is_primary(REPO)
    targets = [target for target in TARGETS
               if allowed_on_machine(target[1] + ".plist", primary)]
    for service, label, port in targets:
        up, signal, detail = probe(label, port)
        rc = record(service, up, signal, detail)
        mark = "up  " if up else "DOWN"
        note = "" if rc == 0 else f"  (recording failed rc={rc})"
        print(f"  {mark}  {service:<16} [{signal}] {detail}{note}")
        if rc != 0:
            unrecorded += 1

    if unrecorded == 0:
        return 0

    # ALL OF THEM FAILING IS A DIFFERENT FACT FROM ONE OF THEM FAILING, and the
    # difference decides whether this alarms.
    #
    # Since the 2026-08-18 spool, a record "fails" here only when the row could
    # not even be QUEUED locally (tools/ops-spool.py returns nonzero only when
    # both the direct write and the SQLite spool are out of reach). Three
    # independent records failing on the same pass is not three coincidences,
    # it is the recording path itself being broken on this machine. That is
    # EX_CONFIG, the same reading bin/nightly.sh and bin/smoke-and-record.sh
    # give a step that ran, found a setting it needs absent, wrote nothing and
    # said so.
    #
    # Returning 1 here instead would make this probe report FAILED every ten
    # minutes on any Mac whose credential has not loaded, and an alarm that
    # fires every cycle until someone pastes a token is exactly how the smoke
    # suite was lost the first time. The honest consequence of an unreachable
    # ledger is that these four services go stale on their cadence and read
    # `unknown` — Program 3's load-bearing decision, that health derives from
    # the latest observation and its freshness, already handles this correctly
    # without any help from an alarm.
    if unrecorded == len(targets):
        print("probe-keepalive: nothing could be recorded at all — treating this "
              "as an unreachable ledger (EX_CONFIG), not as three simultaneous "
              "outages. These services will read stale, which is the honest "
              "answer when nobody could write down what was found.")
        return 78

    print(f"probe-keepalive: {unrecorded} of {len(targets)} results could not be "
          f"recorded while others could — that is a real partial failure, not a "
          f"missing credential.")
    return 1


if __name__ == "__main__":
    sys.exit(main())
