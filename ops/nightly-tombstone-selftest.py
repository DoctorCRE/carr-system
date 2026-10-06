#!/usr/bin/env python3
"""nightly-tombstone-selftest.py — the four reliability contracts of the chain.

WHAT THIS DEFENDS, and why each half is here rather than being obvious.

The 2026-08-23 process-audit council found that roughly a third of bin/nightly.sh
did no work on a typical night — six steps refusing for a missing canonical seam,
four for an admin capability nobody has provisioned on this Mac, one retired
no-op still being executed — and that the chain had three reliability bugs worth
more than the wasted steps:

  1. TOMBSTONES. A step known in advance to be unable to work was executed anyway
     so that it could refuse, printing BLOCKED into out/nightly.log every night
     for a week or more. A word that appears nightly stops being read, and it is
     the same word a real break would use. Gating those steps out is only half
     the fix: the other half is that the duty must still be FILED (rule 1b8e7f43
     — a blocked action is filed, never dropped), so a tombstone names what is
     missing and what would reopen it.
  2. DEAD-MAN PINGS ON A STEP THAT DID NOT RUN. bin/hc-ping.sh used to ping
     NOTHING for exit 69, so a backup that did not happen went LATE instead of
     alarming. A cover path elsewhere is a reason the gap may not bite; it is not
     a reason to stop reporting it.
  3. A LIVE HOLDER THAT HAS STOPPED WORKING. On 2026-08-23 a stalled run held the
     singleton lock while very much alive, so pid liveness said "in progress" and
     two later launches exited 0 as duplicates. The night that completed started
     76 minutes late.
  4. A DEATH THAT LEFT NO LINE. On 2026-08-17, 08-18 and 08-19 an unset variable
     under `set -u` killed the chain where it stood. The shell's complaint went to
     stderr, which nothing was reading; out/nightly.log held nothing at all, and
     the dead-man pings are the last step so the chain never reached them. Three
     nights of no exports, no boards, no backup, every alarm green.

WHY IT RUNS THE REAL CODE AND NOT A COPY. Each case extracts the actual function
or block from bin/nightly.sh / bin/run-lock.sh and executes it. Asserting on
source text is how the previous version of the Drive-projection check came to
keep passing on a COMMENT after the chain stopped calling the script it named. A
test that pins behaviour survives the fix moving; a test that pins shape does not.

Nothing here touches the real vault, the real database, a real credential, the
real lock directory or the real Healthchecks account. The pings are aimed at a
throwaway HTTP server on localhost that records what it was asked for.

Exit 0 if every case passes, 1 otherwise.

    ./.venv/bin/python ops/nightly-tombstone-selftest.py
"""
from __future__ import annotations

import http.server
import os
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
NIGHTLY = (REPO / "bin" / "nightly.sh").read_text(encoding="utf-8")
RUN_LOCK = REPO / "bin" / "run-lock.sh"

failures: list[str] = []
passes = 0


def check(name: str, ok: bool, detail: str = "") -> None:
    global passes
    print(f"  {'ok  ' if ok else 'FAIL'}  {name}" + (f" — {detail}" if detail and not ok else ""))
    if ok:
        passes += 1
    else:
        failures.append(name)


def shell_func(name: str) -> str:
    """The verbatim definition of one zsh function in bin/nightly.sh.

    Both shapes the file actually uses: a single-line body, and a multi-line body
    closed by a `}` in column zero. THE ONE-LINE FORM IS TRIED FIRST AND WINS,
    because the multi-line pattern also matches a one-liner — it just runs on to
    the next `}` in column zero, hundreds of lines later, swallowing whatever sits
    between. That is not a hypothetical: the first cut of this file preferred the
    longer match and dragged the whole exit handler into say()'s definition.

    Raises rather than returning "" on a miss — an extraction that silently
    yielded nothing would make every case below pass against an empty harness,
    which is the worst possible failure mode for this file.
    """
    one = re.search(rf'^{re.escape(name)}\(\) \{{.*\}}$', NIGHTLY, re.M)
    if one:
        return one.group(0)
    many = re.search(rf'^{re.escape(name)}\(\) \{{.*?^\}}', NIGHTLY, re.M | re.S)
    if not many:
        raise SystemExit(f"nightly-tombstone-selftest: cannot find {name}() in bin/nightly.sh")
    return many.group(0)


def slice_between(start: str, end: str) -> str:
    i = NIGHTLY.index(start)
    j = NIGHTLY.index(end, i) + len(end)
    return NIGHTLY[i:j]


class PingRecorder(http.server.BaseHTTPRequestHandler):
    hits: list[str] = []

    def do_GET(self) -> None:                       # noqa: N802 (stdlib callback name)
        PingRecorder.hits.append(self.path)
        self.send_response(200)
        self.send_header("Content-Length", "2")
        self.end_headers()
        self.wfile.write(b"ok")

    def log_message(self, *_args) -> None:          # keep the test output readable
        return


def start_ping_server() -> tuple[http.server.HTTPServer, str]:
    srv = http.server.HTTPServer(("127.0.0.1", 0), PingRecorder)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv, f"http://127.0.0.1:{srv.server_port}"


print("nightly-tombstone: the chain's four reliability contracts")

# ── 1. A TOMBSTONE FILES THE DUTY AND RUNS NOTHING ───────────────────────────
print("\ntombstones")
scratch = Path(tempfile.mkdtemp(prefix="nightly-tombstone-"))
try:
    log = scratch / "nightly.log"
    # The real say(), record_run() and tombstone(), lifted out of the chain.
    # LEDGER_OFF=1 makes record_run return before it can reach the database — the
    # ledger's own behaviour is tools/ops-record.py's to prove, not this file's.
    harness = "\n".join([
        "set -u",
        f'LOG="{log}"',
        "LEDGER_OFF=1",
        "tombstoned=0",
        "seam_blocked=0",
        "timed_out=0",
        "LAST_STEP_RC=0",
        shell_func("say"),
        shell_func("record_run"),
        shell_func("tombstone"),
        'tombstone "probe step" "a capability nobody provisioned" "somebody provisions it"',
        'print -r -- "tombstoned=$tombstoned seam_blocked=$seam_blocked rc=$LAST_STEP_RC"',
    ])
    r = subprocess.run(["/bin/zsh", "-c", harness], capture_output=True, text=True, timeout=60)
    logged = log.read_text() if log.exists() else ""

    check("a tombstoned step exits the harness cleanly", r.returncode == 0,
          f"rc={r.returncode} stderr={r.stderr!r}")
    check("it writes a TOMBSTONE line, a word no failure uses",
          "  TOMBSTONE  " in logged, f"log={logged!r}")
    check("the line names the step, what is missing, and what reopens it",
          "probe step" in logged
          and "missing: a capability nobody provisioned" in logged
          and "reopens when: somebody provisions it" in logged,
          f"log={logged!r}")
    check("it says the step was not run, so the line cannot read as work done",
          "gated out, not run" in logged, f"log={logged!r}")
    check("it counts as tombstoned and NOT as blocked",
          "tombstoned=1 seam_blocked=0" in r.stdout, f"stdout={r.stdout!r}")
    # THE POINT OF THE WHOLE CHANGE. blocked-count reaching zero must mean the
    # steps were gated out, and the receipt has to distinguish that from the
    # seams having been built. Both numbers, always, on one line.
    check("a tombstone leaves LAST_STEP_RC non-zero, so a registered ping still alarms",
          "rc=69" in r.stdout, f"stdout={r.stdout!r}")

    # And it must not have launched anything: the chain's per-step wall clock and
    # credential boundary are both step()'s, and a tombstone calls neither.
    check("a tombstone launches no child (it never reaches carr_routine_exec)",
          "carr_routine_exec" not in shell_func("tombstone")
          and "CARR_STEP_TIMEOUT_ARGV" not in shell_func("tombstone"))

    # ── 2. THE RECEIPT PRINTS ZERO COUNTS TOO ────────────────────────────────
    # A figure that appears only when it is non-zero cannot be read as a
    # measurement: "nothing to report" and "the code that would report it never
    # ran" look identical, and the second is what three silent August nights were.
    _r0 = NIGHTLY.index('say "===== nightly receipt:')
    _r1 = NIGHTLY.index("\n", NIGHTLY.index('print -r -- "nightly receipt:', _r0))
    receipt = NIGHTLY[_r0:_r1]
    clean = subprocess.run(
        ["/bin/zsh", "-c", "\n".join([
            "set -u", f'LOG="{scratch}/receipt.log"',
            shell_func("say"),
            "seam_blocked=0", "tombstoned=7", "timed_out=0",
            receipt,
        ])],
        capture_output=True, text=True, timeout=60)
    check("the receipt is emitted on a night with nothing blocked",
          "nightly receipt: blocked=0 tombstoned=7 timed_out=0" in clean.stdout,
          f"stdout={clean.stdout!r}")
    check("...and blocked=0 is printed beside the tombstone count, never alone",
          re.search(r"blocked=0 tombstoned=[1-9]", clean.stdout) is not None,
          "blocked=0 without a tombstone count reads as 'the seams got built'")

    # ── 3. NO STEP ON THE NORMAL ROUTE STILL EXECUTES A REFUSAL ──────────────
    # Comment lines are blanked first, at their own length so surviving offsets
    # stay honest: this file and the chain both discuss the refusal scripts by
    # name, and a prose mention cannot launch a process.
    code_only = "\n".join(
        " " * len(line) if line.lstrip().startswith("#") else line
        for line in NIGHTLY.split("\n"))
    check("the chain no longer launches a seam-refusal process on any route",
          "routine-canonical-seam-refusal.sh" not in code_only,
          "a step still executes the refusal script to produce its message")
    check("the chain no longer launches an admin-refusal process on any route",
          "routine-admin-refusal.sh" not in code_only,
          "a step still executes the refusal script to produce its message")
    check("and no step is a bare `true` standing in for retired work",
          not re.search(r'^step "[^"]*" true$', code_only, re.M))

    # ── THE RECEIPT THE ACCEPTANCE ASKS FOR, FROM THE REAL CALLSITES ─────────
    # Every `tombstone` callsite in bin/nightly.sh, lifted verbatim (they carry
    # backslash continuations) and replayed against the real function. This is
    # the closest honest thing to running the chain: the arguments executed here
    # are the exact arguments tonight's run will pass. What it cannot cover is
    # the ~19 steps that DO work, which is why it asserts the tombstone count and
    # blocked=0 rather than claiming to be a full chain.
    #
    # RUNNING THE ACTUAL CHAIN IS NOT AN OPTION FOR A TEST. It writes exports to
    # OneDrive, pushes the corpus to ~/.claude/skills, takes the real singleton
    # lock and dumps the production database. A selftest that does all that as a
    # side effect of asking "how many steps were gated out" is a worse bargain
    # than the question is worth.
    calls = re.findall(r'^\s*tombstone "(?:(?!^\s*(?:tombstone|step|drive_projection) ).)*?"\s*$',
                       code_only, re.M | re.S)
    # The call inside drive_projection()'s own body is a definition, not a step:
    # its arguments are that function's parameters and mean nothing at top level.
    # It is replayed a few lines below by calling the wrapper itself, which is the
    # only way to exercise the normal-route branch as the chain reaches it.
    calls = [c for c in calls if '"$label"' not in c]
    projections = len(re.findall(r'^drive_projection "', NIGHTLY, re.M))
    expected = len(calls) + projections
    check("bin/nightly.sh has tombstone callsites to replay", len(calls) >= 5,
          f"found {len(calls)}")

    replay_log = scratch / "replay.log"
    replay = subprocess.run(
        ["/bin/zsh", "-c", "\n".join([
            "set -u", f'LOG="{replay_log}"', "LEDGER_OFF=1",
            "tombstoned=0", "seam_blocked=0", "timed_out=0", "LAST_STEP_RC=0",
            shell_func("say"), shell_func("record_run"), shell_func("tombstone"),
            *calls,
            # The drive_projection steps, through the real wrapper on the normal route.
            "RECOVERY=0", shell_func("step"), shell_func("drive_projection"),
            *[f'drive_projection "replayed projection {i}" "a seam" true'
              for i in range(projections)],
            receipt,
        ])],
        capture_output=True, text=True, timeout=120)
    replayed = replay_log.read_text() if replay_log.exists() else ""

    check("every real tombstone callsite replays cleanly", replay.returncode == 0,
          f"rc={replay.returncode} stderr={replay.stderr!r}")
    check(f"all {expected} gated-out steps write a TOMBSTONE line",
          replayed.count("  TOMBSTONE  ") == expected,
          f"{replayed.count('  TOMBSTONE  ')} lines for {expected} steps")
    # Every one of them must say BOTH things. A tombstone that names what is
    # missing and not what reopens it is a shrug with a timestamp, and the reopen
    # condition is the half that makes it a filed duty rather than a deletion.
    tomb_lines = [ln for ln in replayed.splitlines() if "  TOMBSTONE  " in ln]
    check("...and each names what is missing AND what would reopen it",
          all(re.search(r"· missing: \S.*· reopens when: \S", ln) for ln in tomb_lines),
          "\n".join(ln for ln in tomb_lines
                    if not re.search(r"· missing: \S.*· reopens when: \S", ln)))
    check("THE ACCEPTANCE: the receipt reads blocked=0 with the work accounted for as tombstoned",
          f"nightly receipt: blocked=0 tombstoned={expected} timed_out=0" in replay.stdout,
          f"stdout={replay.stdout!r}")
finally:
    shutil.rmtree(scratch, ignore_errors=True)

# ── 4. A BLOCKED OR SKIPPED STEP WITH A REGISTERED PING STILL ALARMS ─────────
print("\ndead-man pings on a step that did not run")
server, base = start_ping_server()
ping_home = Path(tempfile.mkdtemp(prefix="nightly-hc-home-"))
try:
    cfg = ping_home / ".config" / "carr"
    cfg.mkdir(parents=True)
    (cfg / "healthchecks.env").write_text(
        f'HC_PING_EXPORTS="{base}/exports"\n'
        f'HC_PING_BACKUP="{base}/backup"\n'
        f'HC_PING_MCP="{base}/mcp"\n'
        f'HC_PING_CHAIN="{base}/chain"\n')

    def ping(**rcs: str) -> list[str]:
        PingRecorder.hits = []
        env = {**os.environ, "HOME": str(ping_home), **rcs}
        subprocess.run(["/bin/zsh", str(REPO / "bin" / "hc-ping.sh")],
                       capture_output=True, text=True, timeout=180, env=env)
        return list(PingRecorder.hits)

    # TWO INVOCATIONS COVER THREE OUTCOMES, and the packing is deliberate rather
    # than tidy. hc-ping.sh ends by curling the live Worker's /health, so every
    # extra run costs a real network round trip — up to its 15s timeout on a
    # runner that cannot reach it. Each case below still asserts one outcome per
    # check; they are only being carried by fewer processes.

    # 78 = the step ran and found its credential absent. This already alarmed and
    # must keep alarming; it is the case the local backup is in on this Mac.
    hits = ping(HC_BACKUP_RC="78", HC_EXPORTS_RC="0", HC_CHAIN_RC="0")
    check("a SKIPPED backup (78) pings its check's /fail endpoint",
          "/backup/fail" in hits, f"hits={hits}")
    check("...while the step that did succeed pings OK, so the two stay distinguishable",
          "/exports" in hits and "/exports/fail" not in hits, f"hits={hits}")

    # 69 is the case the old mute swallowed, and it is the code a tombstoned step
    # now carries. THIS IS THE REGRESSION TEST FOR THE MUTE: before 2026-08-23
    # this pinged nothing at all and the check went late instead of alarming.
    # Exports is left unsupplied in the same run to cover the one honest silence
    # that remains — nobody said, so nothing is claimed and the dead-man runs its
    # own clock.
    hits = ping(HC_BACKUP_RC="69", HC_CHAIN_RC="0")
    check("a BLOCKED/TOMBSTONED backup (69) pings /fail rather than going late",
          "/backup/fail" in hits, f"hits={hits}")
    check("an UNSUPPLIED outcome still pings nothing, which is the honest signal",
          "/exports" not in hits and "/exports/fail" not in hits, f"hits={hits}")

    # ── THE CHECK MUST REPORT ON THE STEP IT IS NAMED AFTER ─────────────────
    # Pinging correctly is worthless if the wrong step's outcome arrives. Until
    # 2026-08-23 `BACKUP_RC=$LAST_STEP_RC` sat at the bottom of the PORTABILITY
    # MIRROR, several steps below the encrypted backup, so HC_BACKUP_RC carried
    # the mirror's exit code — a different program doing a different job. A
    # backup that FAILED while the mirror succeeded pinged the backup check OK.
    #
    # It stayed invisible because on this Mac both steps SKIP for the same absent
    # credential, so the wrong answer and the right answer have been identical.
    # This gives them DIFFERENT codes, which is the only way to see which arrived.
    #
    # Replays the real region from the backup step to the ping export with a fake
    # step() that reports a per-label code. Asserting which outcome ARRIVES,
    # rather than where the assignment sits, is what keeps this true the next
    # time somebody moves the line.
    region = slice_between('step "encrypted backup -> R2"',
                           'export HC_BACKUP_RC="$BACKUP_RC"')
    wired = subprocess.run(
        ["/bin/zsh", "-c", "\n".join([
            "set -u",
            f'LOG="{ping_home}/wiring.log"', "LEDGER_OFF=1",
            'REPO="."', "RECOVERY=0", "EXPORTS_RC=0", "LAST_STEP_RC=0",
            # One line per capability the replayed region binds. The portability
            # mirror moved onto the exporter capability on 2026-09-01, so the
            # preamble declares it the same way it already declared the backup
            # one — under `set -u` an undeclared name aborts the replay before
            # the assertion below can run, and the failure then looks like a
            # wiring bug rather than a missing fixture line.
            'CARR_DB_BACKUP_URL=""', 'CARR_DB_EXPORTER_URL=""',
            "rc_total=0", "tombstoned=0",
            "seam_blocked=0", "timed_out=0",
            shell_func("say"), shell_func("record_run"), shell_func("tombstone"),
            "step() {",
            '  case "$1" in',
            '    "encrypted backup -> R2") LAST_STEP_RC=41 ;;',
            '    "portability mirror"*)    LAST_STEP_RC=42 ;;',
            "    *)                        LAST_STEP_RC=43 ;;",
            "  esac",
            "}",
            region,
            'print -r -- "HC_BACKUP_RC=$HC_BACKUP_RC"',
        ])],
        capture_output=True, text=True, timeout=60)
    check("the backup check is pinged with the BACKUP's outcome, not a later step's",
          "HC_BACKUP_RC=41" in wired.stdout,
          f"stdout={wired.stdout!r} (42 = the portability mirror, 43 = some other "
          f"step) stderr={wired.stderr!r}")
finally:
    server.shutdown()
    shutil.rmtree(ping_home, ignore_errors=True)

# ── 5. A WEDGED HOLDER IS BROKEN WITHIN THE BOUND ───────────────────────────
print("\nstale singleton lock")
lockdir = Path(tempfile.mkdtemp(prefix="nightly-lock-"))
holder = None
try:
    holder = subprocess.Popen(
        ["/bin/zsh", "-c", f'source "{RUN_LOCK}"\ncarr_take_lock wedged || exit 1\nsleep 300'],
        env={**os.environ, "CARR_LOCK_DIR": str(lockdir)},
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    lock = lockdir / "carr-wedged.lock"
    # WAIT FOR CONTENT, NOT FOR THE FILE. `date ... > "$lock/since"` creates the
    # file the instant the redirect is set up and writes into it a moment later,
    # so an exists() poll returns while the file is still empty — and this test
    # then backdates a claim the holder promptly overwrites with the current
    # time. That made the age read 0 and the staleness check fail for a reason
    # nowhere near the code under test. It would also have been intermittent:
    # whether it bites depends on losing a sub-millisecond race.
    for _ in range(200):
        try:
            if (lock / "since").read_text().strip():
                break
        except OSError:
            pass
        time.sleep(0.05)
    check("the holder took the lock and is alive", (lock / "since").exists()
          and holder.poll() is None)

    # THE AGE FUNCTION IS TESTED DIRECTLY, ahead of anything that depends on it.
    # It parses a timestamp with `date`, whose flags differ between the BSD one
    # on Joe's Mac and the GNU one on ubuntu-latest, where strict CI runs. A
    # dialect it cannot parse returns NOTHING, and returning nothing is
    # indistinguishable from "not stale" — so the bound would go quietly inert on
    # one platform while every other check in this section still passed there.
    # Asserting a real number means that failure names itself instead of
    # surfacing three checks later as a wedge that would not break.
    (lock / "since").write_text("2020-01-01T00:00:00Z\n")
    aged = subprocess.run(
        ["/bin/zsh", "-c",
         f'source "{RUN_LOCK}"\ncarr_lock_age_seconds "{lock}"'],
        env={**os.environ, "CARR_LOCK_DIR": str(lockdir)},
        capture_output=True, text=True, timeout=60)
    check("the lock's age parses to a real number on this platform's `date`",
          aged.stdout.strip().isdigit() and int(aged.stdout) > 60 * 60 * 24 * 365,
          f"stdout={aged.stdout!r} — neither date dialect parsed the timestamp")
    # And an unreadable claim yields no age at all, which the caller reads as
    # "not shown to be stale" rather than as "infinitely old".
    unknown = subprocess.run(
        ["/bin/zsh", "-c",
         f'source "{RUN_LOCK}"\ncarr_lock_age_seconds "{lockdir}/carr-nonexistent.lock"'],
        env={**os.environ, "CARR_LOCK_DIR": str(lockdir)},
        capture_output=True, text=True, timeout=60)
    check("an unreadable claim yields no age, so absence of evidence cannot break a lock",
          unknown.stdout.strip() == "" and unknown.returncode == 0,
          f"stdout={unknown.stdout!r} rc={unknown.returncode}")
    # Put the claim back so the live-holder case below reads a fresh lock.
    (lock / "since").write_text(
        subprocess.run(["date", "-u", "+%Y-%m-%dT%H:%M:%SZ"],
                       capture_output=True, text=True).stdout)

    # A LIVE, RECENT HOLDER IS STILL RESPECTED. Without this case the bound would
    # be satisfied by a version that breaks every lock it meets, which is worse
    # than the wedge: two chains against one database is what run-lock.sh exists
    # to prevent, and 2026-08-14 measured what it costs.
    fresh = subprocess.run(
        ["/bin/zsh", "-c", f'source "{RUN_LOCK}"\ncarr_take_lock wedged && print -r -- TOOK'],
        env={**os.environ, "CARR_LOCK_DIR": str(lockdir),
             "CARR_LOCK_STALE_AFTER_SECONDS": "5400"},
        capture_output=True, text=True, timeout=60)
    check("a live holder inside the bound is left alone",
          "TOOK" not in fresh.stdout and "LOCKED" in fresh.stdout
          and holder.poll() is None, f"stdout={fresh.stdout!r}")

    # Now age the claim past a deliberately tiny bound. Backdating `since` is the
    # whole simulation: the bound is a clock and nothing else, precisely so it
    # needs no cooperation from the component that has stopped cooperating.
    (lock / "since").write_text("2020-01-01T00:00:00Z\n")
    t0 = time.monotonic()
    broke = subprocess.run(
        ["/bin/zsh", "-c", f'source "{RUN_LOCK}"\ncarr_take_lock wedged && print -r -- TOOK'],
        env={**os.environ, "CARR_LOCK_DIR": str(lockdir),
             "CARR_LOCK_STALE_AFTER_SECONDS": "60",
             # The holder here is `zsh -c '... sleep 300'`, which does NOT honour
             # TERM while it waits on that child — so this exercises the real
             # KILL escalation, not the polite path. The grace is shortened only
             # so the escalation is proven in two seconds instead of ten.
             "CARR_LOCK_TERM_GRACE_SECONDS": "2"},
        capture_output=True, text=True, timeout=120)
    elapsed = time.monotonic() - t0
    check("a wedged holder past the bound is broken and the lock is taken",
          "TOOK" in broke.stdout, f"stdout={broke.stdout!r}")
    check("...it says WEDGED out loud, a word no healthy run prints",
          "WEDGED" in broke.stdout, f"stdout={broke.stdout!r}")
    check("...within minutes, not the 76 that provoked this",
          elapsed < 30, f"took {elapsed:.1f}s")
    for _ in range(60):
        if holder.poll() is not None:
            break
        time.sleep(0.1)
    check("...and the stalled run itself is stopped, not merely stepped around",
          holder.poll() is not None, "the wedged holder is still running")

    check("the breaker's lock is intact after the break",
          (lock / "pid").exists(), "the lock directory was removed")

    # THE RACE THE BOUND CREATED, asserted DIRECTLY on carr_release_lock rather
    # than through the break above. Breaking a wedge introduces a second way to
    # lose a lock: the TERMed holder's own exit trap runs while the breaker may
    # already own a new directory at the same path, and an unconditional remove
    # there deletes the breaker's claim and admits a third run — two chains
    # against one database, which is the failure this whole helper exists to
    # prevent, reintroduced by the fix for a different one.
    #
    # Driving it through a real break does NOT test this: the breaker waits for
    # the holder to be gone before it recreates anything, so the window never
    # opens on that path and a version with the guard removed passes. Measured —
    # this check was written that way first and the mutation survived it. The
    # property is "release only what still names us", so it is tested as one.
    stolen = subprocess.run(
        ["/bin/zsh", "-c",
         f'source "{RUN_LOCK}"\n'
         'carr_take_lock stolen-case || exit 9\n'
         'print -r -- 999999 > "$CARR_LOCK_PATH/pid"\n'   # another run took it over
         'carr_release_lock\n'
         '[ -d "$CARR_LOCK_DIR/carr-stolen-case.lock" ] && print -r -- "SURVIVED"'],
        env={**os.environ, "CARR_LOCK_DIR": str(lockdir)},
        capture_output=True, text=True, timeout=60)
    check("a holder does not delete a lock that now names a different run",
          "SURVIVED" in stolen.stdout, f"stdout={stolen.stdout!r}")
finally:
    if holder and holder.poll() is None:
        holder.kill()
    shutil.rmtree(lockdir, ignore_errors=True)

# ── 6. A `set -u` DEATH LEAVES A NAMED LINE AND FIRES THE CHAIN PING ────────
print("\nset -u death")
server, base = start_ping_server()
death = Path(tempfile.mkdtemp(prefix="nightly-death-"))
try:
    cfg = death / ".config" / "carr"
    cfg.mkdir(parents=True)
    (cfg / "healthchecks.env").write_text(
        f'HC_PING_EXPORTS="{base}/exports"\n'
        f'HC_PING_BACKUP="{base}/backup"\n'
        f'HC_PING_MCP="{base}/mcp"\n'
        f'HC_PING_CHAIN="{base}/chain"\n')
    log = death / "nightly.log"
    # The chain's real guard block, verbatim: everything from the first flag to
    # the EXIT trap, which is the definition of carr_chain_exit and its wiring.
    guard = slice_between("CHAIN_OUTCOME=incomplete", "trap 'carr_chain_exit $?' EXIT")
    script = death / "dies.sh"
    script.write_text("\n".join([
        "#!/bin/zsh",
        "set -u",
        f'REPO="{REPO}"',
        f'LOG="{log}"',
        shell_func("say"),
        guard,
        # The 2026-08-17 shape exactly: a variable the credential loader never
        # exported, dereferenced by a step that assumed it had.
        'env X="$CARR_DB_NOBODY_PROVISIONED_THIS" true',
        'say "this line must never be reached"',
    ]))
    script.chmod(0o755)

    PingRecorder.hits = []
    r = subprocess.run(["/bin/zsh", str(script)], capture_output=True, text=True,
                       timeout=180, env={**os.environ, "HOME": str(death)})
    logged = log.read_text() if log.exists() else ""

    check("the induced unset variable does kill the shell",
          r.returncode != 0 and "must never be reached" not in logged,
          f"rc={r.returncode}")
    # THE VARIABLE MUST BE ON THE FATAL LINE, not merely somewhere in the log.
    # The first cut of this check searched the whole file and passed on a
    # mutation that reduced the FATAL line to "the chain died" — because the
    # captured stderr dump a few lines above happens to contain the name too.
    # Both halves matter and they are different guarantees: capturing the shell's
    # stderr is what puts the evidence in the log at all, and PARSING it is what
    # puts the answer on the line a reader finds first. Asserting only the union
    # of the two lets either one rot silently.
    fatal_lines = [ln for ln in logged.splitlines() if "FATAL" in ln]
    check("the death leaves a FATAL line in the log at all",
          bool(fatal_lines), f"log={logged!r}")
    check("...and that line NAMES the variable that killed it",
          any("CARR_DB_NOBODY_PROVISIONED_THIS" in ln for ln in fatal_lines),
          f"fatal={fatal_lines!r}")
    check("...and says the steps below it did not run, not merely that something failed",
          any("did not run tonight" in ln for ln in fatal_lines), f"fatal={fatal_lines!r}")
    check("...and the raw shell stderr is in the log too, so the evidence outlives the parse",
          "CARR_DB_NOBODY_PROVISIONED_THIS: parameter not set" in logged, f"log={logged!r}")
    check("the log carries a completion line, so a reader is not left guessing",
          "FINISHED WITH FAILURES" in logged, f"log={logged!r}")
    check("the whole-chain dead-man ping fires /fail on a chain that ran nothing",
          "/chain/fail" in PingRecorder.hits, f"hits={PingRecorder.hits}")
    # The named steps' outcomes are genuinely unknown after a mid-run death, and
    # hc-ping's unsupplied-outcome path is the honest answer for them.
    check("...while exports and backup, whose outcomes nobody knows, claim nothing",
          "/exports" not in PingRecorder.hits and "/backup" not in PingRecorder.hits,
          f"hits={PingRecorder.hits}")
    check("the receipt says the chain died rather than reporting counts it never took",
          "died_before_completion=1" in r.stdout, f"stdout={r.stdout!r}")
    check("and the dispatcher's evidence line reads failed, not ok",
          "nightly result: chain_failed" in r.stdout, f"stdout={r.stdout!r}")

    # A CLEAN RUN MUST NOT TRIP ANY OF THAT. Without this case the guard is
    # satisfied by a version that reports every night as a death, which would
    # retire the whole alarm inside a week.
    PingRecorder.hits = []
    ok_log = death / "ok.log"
    ok_script = death / "lives.sh"
    ok_script.write_text("\n".join([
        "#!/bin/zsh", "set -u", f'REPO="{REPO}"', f'LOG="{ok_log}"',
        shell_func("say"), guard,
        "CHAIN_OUTCOME=complete",
        'print -r -- "nightly result: chain_ok"',
    ]))
    ok_script.chmod(0o755)
    ok = subprocess.run(["/bin/zsh", str(ok_script)], capture_output=True, text=True,
                        timeout=120, env={**os.environ, "HOME": str(death)})
    check("a run that reaches its completion line reports no death and pings nothing",
          "FATAL" not in (ok_log.read_text() if ok_log.exists() else "")
          and not PingRecorder.hits,
          f"log={ok_log.read_text() if ok_log.exists() else ''!r} hits={PingRecorder.hits}")
finally:
    server.shutdown()
    shutil.rmtree(death, ignore_errors=True)

print()
if failures:
    print(f"nightly-tombstone: FAIL — {len(failures)} of {passes + len(failures)} checks")
    for f in failures:
        print(f"  - {f}")
    sys.exit(1)
print(f"nightly-tombstone: OK — {passes} checks")
