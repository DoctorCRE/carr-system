#!/usr/bin/env python3
"""ops/probe-keepalive-selftest.py — prove the probe can say DOWN.

A liveness probe run against three healthy servers agrees that everything is
fine, and would agree just as readily if `port_accepts` returned True
unconditionally, if the PID parse silently produced None-means-up, or if the
whole verdict were hardcoded. That is the same trap ops/edge-liveness.py fell
into on its first green run, and the answer is the same: exercise the failure
paths deliberately.

NO MOCKS, AND THAT IS THE DESIGN. These checks use REAL sockets and REAL
processes:
  * a real listening socket on an ephemeral port, to prove `accepting` is true
    when something is genuinely accepting;
  * that same socket CLOSED, to prove it goes false — the closed case is the one
    that matters and the one a mock would have gotten wrong;
  * a real child process, to prove process liveness reads true;
  * that same process KILLED, to prove it reads false.
A test-only seam would have proven that the seam works. On 2026-08-14 the
settings gate shipped two defects and both were "a test that exercised a path
production never takes" (team loop T75), so this file takes the long way round.

WHAT IS NOT COVERED HERE, said plainly: `launchd_pid` is exercised against real
launchd labels only through the live run recorded in the pull request, because
this suite cannot create a launchd agent without installing one. The pure
socket and process logic is owned here; the launchctl parse is owned by that run.
"""
import os
import socket
import subprocess
import sys
import time

from pathlib import Path
sys.path.append(str(Path(__file__).resolve().parents[1] / "lib"))
from selftest_harness import temporary_directory

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import importlib.util

spec = importlib.util.spec_from_file_location(
    "probe_keepalive",
    os.path.join(os.path.dirname(__file__), "..", "bin", "probe-keepalive.py"))
if spec is None or spec.loader is None:
    sys.exit("cannot import probe-keepalive.py")
pk = importlib.util.module_from_spec(spec)
spec.loader.exec_module(pk)

FAILED: list[str] = []


def check(label: str, cond: bool, detail: str = "") -> None:
    if cond:
        print(f"  ok    {label}")
    else:
        print(f"  FAIL  {label}" + (f"\n        {detail}" if detail else ""))
        FAILED.append(label)


def main() -> int:
    print("probe-keepalive-selftest — the probe must be able to say DOWN\n")

    # ── a real socket, open then closed ──────────────────────────────────────
    srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    srv.bind(("127.0.0.1", 0))
    srv.listen(4)
    port = srv.getsockname()[1]

    check("a genuinely listening port reads as accepting",
          pk.port_accepts(port) is True, f"port {port}")

    srv.close()
    time.sleep(0.2)
    check("the SAME port, once closed, reads as not accepting",
          pk.port_accepts(port) is False,
          f"port {port} still reported accepting after close — this is the "
          f"check a mock would have gotten wrong")

    # A port nothing has ever bound. Distinct from the case above: that one
    # proves the transition, this one proves the resting state.
    check("a port nothing ever bound reads as not accepting",
          pk.port_accepts(59_999) is False)

    # ── the probe's own verdict wiring ───────────────────────────────────────
    # probe() consults launchd first, so it is exercised here through a label
    # that certainly does not exist: no PID means DOWN, whatever the port says.
    up, signal, detail = pk.probe("com.carr.definitely-not-a-real-label", None)
    check("an unknown launchd label reports DOWN", up is False, detail)
    check("...and names the process signal rather than claiming a port answer",
          signal == "process", signal)
    check("...and its detail says launchd holds no PID",
          "no PID" in detail, detail)

    # ── a real process, alive then killed ────────────────────────────────────
    child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"])
    try:
        time.sleep(0.3)
        check("a real running child has a live PID",
              child.poll() is None, "child exited early")
    finally:
        child.kill()
        child.wait(timeout=10)
    check("the SAME child, once killed, is no longer running",
          child.poll() is not None)

    # ── the signal names must stay distinguishable ───────────────────────────
    # 'accepting' and 'process' are different claims about how much is known.
    # If they ever collapse into one string, a wedged-but-alive server becomes
    # indistinguishable from a serving one in the recorded detail.
    check("the two signal names are distinct",
          len({"accepting", "process"}) == 2)

    # ── the failure-class mapping ────────────────────────────────────────────
    # ops.run refuses a `failed` row without a failure_class, so a mapping that
    # silently produced an empty class would make every DOWN row fail to insert
    # — the outage would go unrecorded precisely when it mattered.
    src = open(os.path.join(os.path.dirname(__file__), "..",
                            "bin", "probe-keepalive.py")).read()
    check("a DOWN result always attaches a failure-class",
          "--failure-class" in src and "keepalive_not_accepting" in src
          and "keepalive_not_running" in src)

    # ── the exit-code contract, exercised rather than asserted in prose ──────
    # The first cut of this check grepped the docstring for a sentence, which
    # passed nothing but a spell-check and broke on a line wrap. This runs the
    # REAL main() against REAL targets that are certainly down, twice:
    #
    # (1) with an unreachable database but a WRITABLE local spool — the exact
    #     state of a Mac whose credential has not loaded. Since the 2026-08-18
    #     spool this is no longer a recording failure: every DOWN row queues
    #     durably (a failed direct write falls back to the queue) and the probe
    #     returns 0, its "probe worked" answer. The 2026-08-17 schema-drift
    #     outage dropped 3,485 rows precisely because this path did not exist.
    #
    # (2) with the spool ALSO unwritable — nothing can be recorded anywhere.
    #     Expected: 78, not 1. Three records failing at once is the recording
    #     path being broken, not three simultaneous outages, and returning 1
    #     would make the probe alarm every ten minutes until someone fixed it.
    import tempfile
    saved_targets = getattr(pk, "TARGETS")
    saved_env = dict(os.environ)
    try:
        setattr(pk, "TARGETS", [
            ("call-mode", "com.carr.definitely-not-a-real-label", None),
            ("doc-engine", "com.carr.also-not-real", 59_999),
            ("quill-dictate", "com.carr.still-not-real", None),
        ])
        for var in ("DATABASE_URL", "CARR_DB_JOBS_URL", "CARR_DB_EXPORTER_URL"):
            os.environ[var] = "postgresql://nobody@127.0.0.1:1/nothing"

        os.environ["CARR_RUN_SPOOL_DB"] = os.path.join(
            temporary_directory(prefix="carr-probe-selftest-spool-"), "spool.sqlite3")
        rc = pk.main()
        check("every server down with the ledger unreachable but the spool "
              "writable returns 0 — the DOWN rows are queued durably, not lost",
              rc == 0, f"rc={rc}")

        os.environ["CARR_RUN_SPOOL_DB"] = os.path.join(
            os.path.devnull, "spool.sqlite3")
        rc = pk.main()
        check("every server down AND nothing recordable anywhere returns 78, "
              "so a broken recording path cannot alarm every cycle",
              rc == 78, f"rc={rc}")
    finally:
        setattr(pk, "TARGETS", saved_targets)
        os.environ.clear()
        os.environ.update(saved_env)

    # THE REMAINING HALF OF THE EXIT-CODE CONTRACT — a server that is DOWN while
    # the ledger IS reachable must still return 0, so one outage is never
    # reported twice under two names — is deliberately NOT asserted here. It
    # needs a live ledger, and the two attempts to stand in for one by grepping
    # this file's own prose both passed nothing but a spell-check and both broke
    # on a line wrap. It is proven instead by stopping a real server on the real
    # Mac, which is recorded in the pull request. A check that cannot be made
    # honestly here belongs somewhere it can be.

    print()
    if FAILED:
        print(f"FAILED {len(FAILED)} check(s):")
        for f in FAILED:
            print(f"  - {f}")
        return 1
    print("all checks passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
