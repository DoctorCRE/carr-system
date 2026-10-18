#!/usr/bin/env python3
"""ops/key-recovery-test-selftest.py — the acceptance test for
bin/key-recovery-test.sh (Program 4's last unproven backup requirement).

THE GAP THIS CLOSES. bin/restore-rehearse.sh proves a backup restores, but it
reads the age private key from ~/.config/carr/age-key.txt — never from the
OFFLINE PAPER copy that is the actual disaster-recovery path if this Mac dies.
That paper copy, written down and stored off the machine on 2026-08-02, had
never been exercised: if a transcription error sits in it, every backup is
unreadable and the way anyone finds out is the day it is needed.
bin/key-recovery-test.sh closes that gap in two phases — DECISIVE: does the
typed paper key's derived public key match backups-public-key.txt, without
ever printing either private key; REAL: on a match, hand the typed identity's
PATH (never its content) to the unmodified bin/restore-rehearse.sh and let its
own four production-write guards and its own teardown do the actual restoring.

NO MOCK, SAME DISCIPLINE AS ops/restore-rehearse-record-selftest.py. This
drives the REAL bin/key-recovery-test.sh as a real subprocess, generates a REAL
throwaway age keypair with the REAL age-keygen binary (never a stand-in), and
lets the script's own shape validation, its own `age-keygen -y` derivation, and
its own comparison all run for real. The ONE thing it cannot afford is a
terminal to type into and a real Neon branch — so it uses TWO test-only env
hooks, chained:

  CARR_KEY_RECOVERY_TEST_SELFTEST(_TYPED_KEY|_PUBKEY_FILE|_PAUSE_AFTER_WRITE)
      Mirrors bin/restore-rehearse.sh's CARR_RESTORE_REHEARSE_SELFTEST
      precedent, but stands in for the interactive `read -s` ALONE — shape
      validation, the real age-keygen -y derivation, the real comparison
      against a FIXTURE public-key file (never the repo's own
      backups-public-key.txt), and — on a match — the real call into
      bin/restore-rehearse.sh, all still execute for real.

  CARR_RESTORE_REHEARSE_SELFTEST(_*)
      bin/restore-rehearse.sh's OWN existing selftest hook (proven by
      ops/restore-rehearse-record-selftest.py already). Environment variables
      are inherited by a child process automatically, so setting BOTH hooks on
      this suite's subprocess env means the match-path scenario genuinely
      exercises "key-recovery-test.sh really shells out to
      ./run.sh restore-rehearse --identity <path>", while the expensive,
      network/Neon-touching middle of THAT script is the one already-tested
      substitution — never a second, parallel stub written for this suite.

NEVER THE REAL KEY, NEVER THE REAL BACKUPS. The derivation scenarios use
throwaway keypairs generated fresh, in a temp dir, by the real age-keygen,
for this run only. The portable signal scenario uses a synthetic shape-valid
value and interrupts before any derivation or comparison. The fixture public-key files it compares
against are written to a temp dir too — backups-public-key.txt in the repo is
never read and never touched.

WHAT record_run() DOES, restated here because this suite exists to prove it:
called from bin/key-recovery-test.sh's own EXIT trap (cleanup(), which fires on
every exit path — a shape-invalid input, a pubkey mismatch, a failed real
restore, an interrupt, or the ordinary PASS at the bottom), it writes ONE
ops.run row through tools/ops-record.py — service restore-rehearse-weekly, key
restore.key-recovery (deliberately distinct from restore-rehearse.sh's own
restore.rehearsal, so the weekly drill and this paper-copy drill are never
confused in the ledger) — state succeeded/failed, a failure_class chosen from
three buckets (pubkey_mismatch, restore_failed, aborted), and a --detail line
naming which dump was used and that the identity came from the OFFLINE PAPER
COPY. --detail NEVER carries key material, by construction: it is built from
state flags and a dump filename, never from the typed value.

THE PROVENANCE LINE IS THE TESTED SURFACE, same discipline as
ops/restore-rehearse-record-selftest.py: record_run() prints its
`evidence: ... / detail: ...` block on stdout BEFORE it ever attempts the
database write, so this suite (DATABASE_URL deliberately unreachable, same
unreachable_env() helper) can assert against that line without a live
database, and a human reading a real transcript sees the same thing.

TIER 1 (always runs; no DB, no network beyond loopback-refused, no terminal).
Requires the real `age-keygen` binary on PATH — checked explicitly and skipped
with a clear NOT RUN note (never a silent pass) if it is absent, per rule
88e9b5eb: "not authorized" and "not possible" must never be reported as the
same finding. Present on Joe's Mac (verified while building this); not
installed by default on the CI runner as of this writing.

Covers: shape validation rejects a malformed typed value (failure_class
pubkey_mismatch, no restore attempted); a real mismatch between the typed
key's derived public key and a fixture public-key file (failure_class
pubkey_mismatch, no restore attempted, neither key ever printed); a real match
that proceeds into a REAL call to bin/restore-rehearse.sh, chained through
THAT script's own selftest hook to a synthetic PASS (exit 0, detail names the
dump); a real match whose restore then fails (failure_class restore_failed);
an interrupt (SIGINT to the whole process group, matching what a terminal's
Ctrl-C actually delivers) sent while the script is deliberately paused right
after writing the identity file, proving the temp file and its containing
directory are BOTH gone afterward and the run is recorded aborted; and, across
every scenario, that the throwaway secret key value never appears verbatim in
the script's stdout or stderr.

TIER 2 (a genuine key-recovery drill, run only by Joe, only with the real
paper copy) is deliberately NOT part of this file — it needs a human typing
from paper and touches production's real ops.run row. That live proof is
`./run.sh key-recovery-test` itself.

RUN IT:
    python3 ops/key-recovery-test-selftest.py
"""
import glob
import os
import shutil
import signal
import subprocess
import sys
import tempfile
from pathlib import Path
import time
from typing import Optional

REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))

# How long bin/key-recovery-test.sh is told to pause after writing the identity
# file, so the interrupt lands while it is paused. Wide on purpose — see the
# note at the interrupted-path case below.
PAUSE_SECONDS = 45
sys.path.insert(0, REPO)

from lib.loadpy import load_module_from_path  # noqa: E402

SCRIPT = os.path.join(REPO, "bin", "key-recovery-test.sh")
OPS_RECORD = load_module_from_path("key_recovery_ops_record",
                                    os.path.join(REPO, "tools", "ops-record.py"))
DEAD_DSN = "postgresql://carr_jobs:probe@127.0.0.1:1/nonexistent"

# The 0600 no-eval credential fixture the chained restore-rehearse.sh reads
# through bin/routine-credential-env.sh. See unreachable_env() for why the
# environment alone could not hold this line.
ROUTINE_ENV_TMP = tempfile.TemporaryDirectory()
ROUTINE_ENV_FILE = Path(ROUTINE_ENV_TMP.name) / "db.env"
ROUTINE_ENV_FILE.write_text(f"CARR_DB_JOBS_URL='{DEAD_DSN}'\n", encoding="utf-8")
ROUTINE_ENV_FILE.chmod(0o600)

# Built from two literal halves, same reason bin/key-recovery-test.sh's own
# comment gives: the CARR unattended guard matches this substring in Bash
# command TEXT (a session typing it directly), not in file content — but
# spelling it whole here would still read oddly out of context, so it is kept
# split for a human skimming this file too.
AGE_KEYGEN = "age-key" "gen"

FAILED: list[str] = []


def check(label: str, cond: bool, detail: str = "") -> bool:
    if cond:
        print(f"  ok    {label}")
    else:
        print(f"  FAIL  {label}" + (f"\n        {detail}" if detail else ""))
        FAILED.append(label)
    return bool(cond)


def unreachable_env(extra: Optional[dict] = None) -> dict:
    """Every credential the recorder reads, SET to a dead port — never deleted.

    Port 1 on loopback refuses instantly, so the suite pays no connect timeout,
    and the list of names comes from ops-record.py's own credential_names() so
    it cannot drift as connection modes are added.

    DELETING WAS THE BUG, and this is the suite where it cost the most. The
    helper used to point DATABASE_URL at a dead port and unset
    CARR_DB_JOBS_URL — airtight while `run` was connect("write"), because
    DATABASE_URL was that mode's first choice. PR #288 made `run`
    connect("routine"), which reads CARR_DB_JOBS_URL and nothing else, so
    ops-record.py's _load_db_env() re-supplied the PRODUCTION jobs DSN by
    setdefault.

    The two tier-1 suites fixed on 2026-08-18 got away with it because every
    service key they name is a carr-selftest-* key production has never
    registered, so the recorder refused EX_CONFIG. THIS suite names
    restore-rehearse-weekly, which is registered, so nothing refused it.
    Measured 2026-08-19: this file and ops/restore-rehearse-record-selftest.py
    had written 206 fabricated rows into production's ops.run against that one
    service — 146 failed, 60 succeeded — and four of them landed within six
    seconds carrying contradictory outcomes (a clean success, a public-key
    mismatch, a failed restore and an abort), which is what a fixture sweep
    looks like when it reaches a real ledger. That service's entire production
    health signal was this exhaust, the same shape open loop #450 already
    records for radar-weekly. Nothing is deleted here; the purge is that
    loop's ruling to make.

    The username stays carr_jobs so routine mode's own credential-shape check
    passes and the suite proves what it claims to: the CONNECTION fails, not
    the credential's spelling.
    """
    env = dict(os.environ)
    for name in OPS_RECORD.credential_names():
        env[name] = DEAD_DSN
    # THE SECOND BELT, and the one this suite was missing entirely. Blinding
    # the environment is not enough on its own: this script chains into
    # bin/restore-rehearse.sh, which sources bin/routine-credential-env.sh and
    # calls carr_clear_routine_db_env — that UNSETS every credential name
    # above, including the dead ones — and then reloads from
    # ${CARR_ROUTINE_DB_ENV_FILE:-$HOME/.config/carr/db.env}. With the variable
    # unset, the fallback is the developer's REAL production credential, so
    # the chained rehearsal recorded to production no matter what this helper
    # put in the environment. Pointing it at a 0600 no-eval fixture carrying a
    # dead DSN closes that path, the same way
    # ops/restore-rehearse-record-selftest.py already does for its own runs.
    env["CARR_ROUTINE_DB_ENV_FILE"] = str(ROUTINE_ENV_FILE)
    env.pop("PGSERVICE", None)
    if extra:
        env.update(extra)
    return env


def have_age_keygen() -> bool:
    return shutil.which(AGE_KEYGEN) is not None


def gen_throwaway_keypair(workdir: str) -> tuple[str, str]:
    """A fresh, real, throwaway age identity — never the real key, never
    written anywhere but this test's own temp dir. Returns (secret_line, pub)."""
    idfile = os.path.join(workdir, "throwaway-identity.txt")
    subprocess.run([AGE_KEYGEN, "-o", idfile], check=True,
                    capture_output=True, text=True, timeout=20)
    pub = subprocess.run([AGE_KEYGEN, "-y", idfile], check=True,
                          capture_output=True, text=True, timeout=20).stdout.strip()
    lines = [ln for ln in open(idfile, encoding="utf-8").read().splitlines()
             if ln and not ln.startswith("#")]
    secret = lines[-1]
    return secret, pub


def drive(env_extra: dict, timeout: int = 60) -> subprocess.CompletedProcess:
    """Run the REAL script with the REAL selftest hook."""
    env = unreachable_env({"CARR_KEY_RECOVERY_TEST_SELFTEST": "1", **env_extra})
    return subprocess.run([SCRIPT], capture_output=True, text=True,
                           timeout=timeout, env=env, cwd=REPO)


def evidence_lines(proc: subprocess.CompletedProcess) -> str:
    lines = [ln for ln in proc.stdout.splitlines()
              if ln.strip().startswith("evidence:") or ln.strip().startswith("detail:")]
    return "\n".join(lines)


def assert_no_leak(proc_or_output, secret: str, label: str) -> None:
    if isinstance(proc_or_output, subprocess.CompletedProcess):
        out, err = proc_or_output.stdout, proc_or_output.stderr
    else:
        out, err = proc_or_output
    check(f"{label}: the throwaway secret never appears in stdout",
          secret not in out)
    check(f"{label}: the throwaway secret never appears in stderr",
          secret not in err)


def interruptible_child(command: list[str], env: dict) -> subprocess.Popen:
    # CI's background shell can pass SIG_IGN through Python and exec into zsh.
    # A new session changes the process group, not that inherited disposition;
    # zsh then cannot install the Ctrl-C trap this fixture is meant to exercise.
    # Spawn with the terminal's default disposition and restore this single-
    # threaded harness immediately, including when process creation fails.
    previous = signal.signal(signal.SIGINT, signal.SIG_DFL)
    try:
        return subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                text=True, env=env, cwd=REPO, start_new_session=True)
    finally:
        signal.signal(signal.SIGINT, previous)


def tier1_portable() -> None:
    print("\nTIER 1a — portable checks that need no external tool")
    check("the script exists and is executable",
          os.access(SCRIPT, os.X_OK), SCRIPT)
    previous = signal.signal(signal.SIGINT, signal.SIG_IGN)
    try:
        probe = interruptible_child([
            sys.executable, "-c",
            "import signal; print(signal.getsignal(signal.SIGINT) == signal.SIG_IGN)",
        ], os.environ.copy())
        out, err = probe.communicate(timeout=10)
        check("interrupt child handles SIGINT even when the CI parent ignores it",
              probe.returncode == 0 and out.strip() == "False", out + err)
        check("interrupt child launch preserves the parent's signal disposition",
              signal.getsignal(signal.SIGINT) == signal.SIG_IGN)
    finally:
        signal.signal(signal.SIGINT, previous)

    # No key derivation is reached: a shape-valid synthetic value is enough
    # to exercise readiness and teardown even on runners without age-keygen.
    with tempfile.TemporaryDirectory(prefix="carr-ready-boundary.") as workdir:
        secret = "AGE-SECRET-KEY-1" + "A" * 58
        ready = Path(workdir) / "ready"
        env = unreachable_env({
            "TMPDIR": workdir,
            "CARR_KEY_RECOVERY_TEST_SELFTEST": "1",
            "CARR_KEY_RECOVERY_TEST_SELFTEST_TYPED_KEY": secret,
            "CARR_KEY_RECOVERY_TEST_SELFTEST_PAUSE_AFTER_WRITE": str(PAUSE_SECONDS),
            "CARR_KEY_RECOVERY_TEST_SELFTEST_READY_FILE": str(ready),
        })
        proc = interruptible_child([SCRIPT], env)
        deadline = time.monotonic() + 90
        while not ready.exists() and proc.poll() is None and time.monotonic() < deadline:
            time.sleep(0.0001)
        pause_pid = ready.read_text().strip() if ready.exists() else ""
        # Readiness must name an already-running member of the process group,
        # so it cannot announce a foreground command that is still in future.
        try:
            pause_running = (pause_pid.isdigit() and int(pause_pid) != proc.pid
                             and os.getpgid(int(pause_pid)) == proc.pid)
        except ProcessLookupError:
            pause_running = False
        signalled = time.monotonic()
        if proc.poll() is None:
            os.killpg(proc.pid, signal.SIGINT)
        try:
            out, err = proc.communicate(timeout=PAUSE_SECONDS - 5)
        except subprocess.TimeoutExpired:
            os.killpg(proc.pid, signal.SIGKILL)
            out, err = proc.communicate()
        check("ready boundary: pause is already running in the script's process group",
              pause_running, f"readiness={pause_pid!r}")
        check("ready boundary: SIGINT exits 130 promptly",
              proc.returncode == 130 and time.monotonic() - signalled < 20,
              f"got {proc.returncode}")
        check("ready boundary: exactly one aborted receipt",
              out.count("evidence: state=") == 1
              and "state=failed exit_code=130 failure_class=aborted" in out, out)
        check("ready boundary: no identity directory survives",
              not glob.glob(os.path.join(workdir, "carr-key-recovery.*")))
        assert_no_leak((out, err), secret, "ready boundary")


def tier1_age(workdir: str) -> None:
    print("\nTIER 1b — real age-keygen, real shape validation, real "
          "comparison, real chained restore-rehearse selftest")

    if not have_age_keygen():
        print(f"  not run  age-keygen is not on PATH — the checks that need "
              f"a real identity are skipped here (not authorized vs not "
              f"possible, rule 88e9b5eb). They run for real on Joe's Mac.")
        return

    secret, pub = gen_throwaway_keypair(workdir)
    match_pubkey_file = os.path.join(workdir, "fixture-pubkey-match.txt")
    with open(match_pubkey_file, "w", encoding="utf-8") as f:
        f.write(pub + "\n")
    mismatch_pubkey_file = os.path.join(workdir, "fixture-pubkey-mismatch.txt")
    with open(mismatch_pubkey_file, "w", encoding="utf-8") as f:
        # A syntactically plausible but DIFFERENT bech32-looking public key —
        # never derived from any real identity, just a fixed decoy string.
        f.write("age1qqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqq\n")

    # ── shape validation rejects a malformed typed value before anything is
    #    derived or compared, and never touches restore-rehearse.sh ──────────
    proc = drive({
        "CARR_KEY_RECOVERY_TEST_SELFTEST_TYPED_KEY": "not-an-age-key",
        "CARR_KEY_RECOVERY_TEST_SELFTEST_PUBKEY_FILE": match_pubkey_file,
    })
    ev = evidence_lines(proc)
    check("shape-invalid input exits non-zero",
          proc.returncode != 0, f"got {proc.returncode}")
    check("shape-invalid input: failure_class=pubkey_mismatch",
          "failure_class=pubkey_mismatch" in ev, ev)
    check("shape-invalid input: restore-rehearse.sh was never invoked "
          "(no 'phase 2' heading)", "phase 2:" not in proc.stdout, proc.stdout)
    assert_no_leak(proc, "not-an-age-key", "shape-invalid")

    # ── a real mismatch: the typed key is a genuine, valid age identity, but
    #    its derived public key does not match the fixture file ─────────────
    proc = drive({
        "CARR_KEY_RECOVERY_TEST_SELFTEST_TYPED_KEY": secret,
        "CARR_KEY_RECOVERY_TEST_SELFTEST_PUBKEY_FILE": mismatch_pubkey_file,
    })
    ev = evidence_lines(proc)
    check("real mismatch: exits non-zero", proc.returncode != 0,
          f"got {proc.returncode}")
    check("real mismatch: failure_class=pubkey_mismatch",
          "failure_class=pubkey_mismatch" in ev, ev)
    check("real mismatch: state=failed", "state=failed" in ev, ev)
    check("real mismatch: MISMATCH is named on stderr",
          "MISMATCH" in proc.stderr, proc.stderr[:600])
    check("real mismatch: restore-rehearse.sh was never invoked",
          "phase 2:" not in proc.stdout, proc.stdout)
    check("real mismatch: the derived (public, safe) key IS shown",
          pub in proc.stdout, proc.stdout)
    assert_no_leak(proc, secret, "real mismatch")

    # ── a real match, chained through restore-rehearse.sh's OWN, already
    #    proven selftest hook: a synthetic PASS ──────────────────────────────
    proc = drive({
        "CARR_KEY_RECOVERY_TEST_SELFTEST_TYPED_KEY": secret,
        "CARR_KEY_RECOVERY_TEST_SELFTEST_PUBKEY_FILE": match_pubkey_file,
        "CARR_RESTORE_REHEARSE_SELFTEST": "1",
        "CARR_RESTORE_REHEARSE_SELFTEST_EXIT": "0",
        "CARR_RESTORE_REHEARSE_SELFTEST_DUMP": "/tmp/carr-20260101.sql.age",
        "CARR_RESTORE_REHEARSE_SELFTEST_BYTES": "123456",
        "CARR_RESTORE_REHEARSE_SELFTEST_STAMP": "20260101",
        "CARR_RESTORE_REHEARSE_SELFTEST_PROD_TABLES": "67",
        "CARR_RESTORE_REHEARSE_SELFTEST_PROD_ROWS": "50000",
        "CARR_RESTORE_REHEARSE_SELFTEST_SUMMARY":
            "pct=99.8 rest_rows=49900 rest_tables=67 prod_rows=50000 prod_tables=67",
    })
    ev = evidence_lines(proc)
    check("real match + real restore chain (via restore-rehearse's own "
          "selftest hook): exits 0", proc.returncode == 0,
          f"got {proc.returncode}\n{proc.stdout}\n{proc.stderr}")
    check("real match: MATCH is named on stdout", "MATCH" in proc.stdout,
          proc.stdout[:600])
    check("real match: reached phase 2 (restore-rehearse.sh was really "
          "invoked)", "phase 2:" in proc.stdout, proc.stdout)
    check("real match + pass: state=succeeded (this script's own row)",
          "state=succeeded exit_code=0" in ev, ev)
    check("real match + pass: no failure_class is invented on a success",
          "failure_class=" not in ev.split("\n")[0] if ev else False, ev)
    check("real match + pass: detail names the dump restore-rehearse.sh used",
          "dump=carr-20260101.sql.age" in ev, ev)
    check("real match + pass: detail says the identity came from paper",
          "OFFLINE PAPER COPY" in ev, ev)
    check("real match + pass: KEY RECOVERY TEST: PASS is printed",
          "KEY RECOVERY TEST: PASS" in proc.stdout, proc.stdout[-400:])
    assert_no_leak(proc, secret, "real match + pass")

    # ── a real match whose restore then fails ────────────────────────────────
    proc = drive({
        "CARR_KEY_RECOVERY_TEST_SELFTEST_TYPED_KEY": secret,
        "CARR_KEY_RECOVERY_TEST_SELFTEST_PUBKEY_FILE": match_pubkey_file,
        "CARR_RESTORE_REHEARSE_SELFTEST": "1",
        "CARR_RESTORE_REHEARSE_SELFTEST_EXIT": "1",
        "CARR_RESTORE_REHEARSE_SELFTEST_DIE_REASON": "could not create the rehearsal branch",
    })
    ev = evidence_lines(proc)
    check("real match + failed restore: exits non-zero",
          proc.returncode != 0, f"got {proc.returncode}")
    check("real match + failed restore: failure_class=restore_failed",
          "failure_class=restore_failed" in ev, ev)
    check("real match + failed restore: detail points at "
          "restore.rehearsal's own row for the restore's own detail",
          "restore.rehearsal's own ops.run row" in ev, ev)
    assert_no_leak(proc, secret, "real match + failed restore")

    # ── the interrupted path: SIGINT to the whole process group (what a
    #    terminal's Ctrl-C actually delivers — a plain PID-only signal was
    #    tried first while building this and did NOT interrupt the paused
    #    child; a process-group signal does), sent while the script is
    #    deliberately paused right after writing the identity file ──────────
    # A PRIVATE TMPDIR, BECAUSE THE SHARED ONE IS NOT THIS RUN'S TO WATCH.
    # This globbed the whole of TMPDIR for carr-key-recovery.* and compared a
    # before-set against an after-set. That is only sound while nothing else on
    # the machine runs this script — and worktree-per-session is the default
    # here, so two trees running the suite at once is ordinary. A directory
    # created by the OTHER run appears in `after`, is absent from `before`, and
    # this check reports a leaked identity file that never existed. Seen
    # 2026-08-21: it failed once, then passed clean on a re-run with nothing
    # changed, which is a race rather than a leak.
    #
    # Giving the child its own TMPDIR makes the glob describe exactly one run.
    # Keep it under this invocation's already-private workdir so every return
    # and exception path is covered by the outer TemporaryDirectory cleanup.
    private_tmp = os.path.join(workdir, "interrupt-tmp")
    os.mkdir(private_tmp, mode=0o700)
    tmp_glob = os.path.join(private_tmp, "carr-key-recovery.*")
    before = set(glob.glob(tmp_glob))
    ready_file = os.path.join(workdir, "pause-ready")
    env = unreachable_env({
        "TMPDIR": private_tmp,
        "CARR_KEY_RECOVERY_TEST_SELFTEST": "1",
        "CARR_KEY_RECOVERY_TEST_SELFTEST_TYPED_KEY": secret,
        "CARR_KEY_RECOVERY_TEST_SELFTEST_PUBKEY_FILE": match_pubkey_file,
        "CARR_KEY_RECOVERY_TEST_SELFTEST_PAUSE_AFTER_WRITE": str(PAUSE_SECONDS),
        # The paused process atomically publishes this file when interruptible, so the
        # signal below lands on the state under test no matter how loaded the
        # machine is. A fixed post-spawn sleep was the old guess and it flaked
        # under load: it could fire before the script reached the pause.
        "CARR_KEY_RECOVERY_TEST_SELFTEST_READY_FILE": ready_file,
    })
    proc2 = interruptible_child([SCRIPT], env)
    # WAIT FOR THE READY MARKER, NOT FOR THE CLOCK. Poll up to 90s for the
    # script to arrive at its pause; under heavy load startup can take far
    # longer than any fixed sleep, and signalling early makes THIS suite red
    # for a scheduling reason that has nothing to do with the behaviour under
    # test. If the marker never appears, the script died on the way — fail
    # loudly with what it printed rather than signalling a corpse.
    deadline = time.monotonic() + 90
    while time.monotonic() < deadline:
        if proc2.poll() is not None:
            out, err = proc2.communicate()
            check("interrupted path: script reached its pause", False,
                  f"script exited rc={proc2.returncode} before reaching the "
                  f"pause; stdout={out[-400:]!r} stderr={err[-400:]!r}")
            assert_no_leak((out, err), secret, "interrupted path (early exit)")
            return
        if os.path.exists(ready_file):
            break
        time.sleep(0.05)
    else:
        proc2.kill()
        out, err = proc2.communicate()
        check("interrupted path: script reached its pause", False,
              f"ready marker never appeared within 90s; stdout={out[-400:]!r}")
        assert_no_leak((out, err), secret, "interrupted path (timeout)")
        return
    # THE CLOCK STARTS AT THE SIGNAL, NOT AT THE SPAWN, and the pause is wide.
    #
    # This was `t0 = time.time()` before the Popen, a pause of 8 and a bound of
    # `elapsed < 6` — 4.5 seconds of headroom covering a subprocess spawn, a
    # 1.5s sleep, signal delivery and teardown. On 2026-08-23 that failed on
    # main, with no branch changes, at load average ~380: the whole suite spent
    # 4s of CPU against 45s of wall clock (9% CPU) and this one check went red,
    # refusing every push from the machine for as long as the load lasted.
    #
    # A wall-clock proxy is the right idea and was simply cut too fine. Two
    # changes, and neither weakens what is being proved: the window now excludes
    # the spawn, which was never part of the question, and the pause is wide
    # enough that thrash cannot close the gap between "died on the signal" and
    # "sat out the pause". The distinction under test is 20s vs 45s instead of
    # 6s vs 8s.
    #
    # THE BEHAVIOURAL PROOF WAS NEVER THE TIMER. The exit-code check directly
    # below already establishes death by signal (130); this one exists only to
    # rule out the script sleeping through its pause and dying afterwards for
    # some other reason. Widening it costs nothing that was being caught.
    signalled = time.time()
    os.killpg(os.getpgid(proc2.pid), signal.SIGINT)
    try:
        out, err = proc2.communicate(timeout=PAUSE_SECONDS - 5)
    except subprocess.TimeoutExpired:
        proc2.kill()
        out, err = proc2.communicate()
    elapsed = time.time() - signalled
    check(f"interrupted path: the script actually terminated promptly on "
          f"SIGINT (well under the {PAUSE_SECONDS}s pause, not after it)",
          elapsed < 20, f"took {elapsed:.1f}s after the signal")
    check("interrupted path: exit code is a signal-death code (130 for "
          "SIGINT), not a clean 0 or 1",
          proc2.returncode == 130, f"got {proc2.returncode}")
    ev2 = "\n".join(ln for ln in out.splitlines()
                     if ln.strip().startswith("evidence:") or ln.strip().startswith("detail:"))
    check("interrupted path: recorded failure_class=aborted",
          "failure_class=aborted" in ev2, ev2)
    check("interrupted path: cleanup ran exactly once (one evidence block, "
          "not the double-fire a trap with no explicit exit produces)",
          out.count("evidence: state=") == 1, out)
    after = set(glob.glob(tmp_glob))
    leftover = after - before
    check("interrupted path: no carr-key-recovery temp dir survives — the "
          "identity file and its directory were both shredded and removed",
          not leftover, f"leftover: {leftover}")
    assert_no_leak((out, err), secret, "interrupted path")


def main() -> int:
    print("key-recovery-test-selftest — bin/key-recovery-test.sh must prove "
          "the paper key BEFORE running the real restore, must never leak "
          "key material, and must clean up on every exit path including "
          "Ctrl-C")
    tier1_portable()
    with tempfile.TemporaryDirectory(prefix="carr-key-recovery-selftest.") as workdir:
        os.chmod(workdir, 0o700)
        tier1_age(workdir)
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
