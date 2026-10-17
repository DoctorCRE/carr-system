#!/usr/bin/env python3
"""test-run-lock.py — the mutual-exclusion contract for bin/run-lock.sh.

WHY THIS EXISTS. On 2026-08-14 the nightly chain reported 30 tampered vault
files. Nothing had been tampered with. The scheduled 02:05 run and a manual run
overlapped, and the second run's vault-drift check read the files the first run
had just re-exported — every quarantined diff was one line, the export
timestamp. Two chains against one database and one vault also race on the
export ledger, the backup, and the drift baseline itself. The chain had no
mutual exclusion of any kind; `./bin/nightly.sh` twice meant two chains.

Written before the helper it tests, per rule e65efc68.

THE CONTRACT, one test per clause:
  1. One winner. Two processes racing for the same lock: exactly one proceeds.
  2. The loser is quiet and non-fatal. It returns 1 and says who holds the lock,
     rather than dying — a duplicate run is a no-op, not a failed night.
  3. The lock is released when the holder exits, on success and on failure —
     and is NOT released when the holder is killed outright, because no trap can
     run then. The test asserts both halves, so the limit is recorded rather
     than assumed away.
  4. A stale lock is reclaimed. If the recorded pid is gone (hard kill, panic,
     reboot mid-chain) the next run takes the lock instead of blocking forever.
     This is the clause that decides whether the fix is safe to leave unattended,
     and it is what makes clause 3's limit survivable.
  5. Different lock names do not collide.
  6. bin/nightly.sh actually wires all of it. A helper nobody calls is not
     mutual exclusion.
"""
import os
import shutil
import signal
import subprocess
import sys
import tempfile
import time

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
HELPER = os.path.join(REPO, "bin", "run-lock.sh")

failures: list[str] = []
checked = 0


def check(name, cond, detail=""):
    global checked
    checked += 1
    if cond:
        print(f"  ok    {name}")
    else:
        print(f"  FAIL  {name}  {detail}")
        failures.append(name)


# The caller's full wiring, exactly as bin/nightly.sh uses it. Every test runs
# through this preamble so the thing under test is the pattern shipped, not a
# convenient simplification of it.
PREAMBLE = (
    'source "{helper}"\n'
    'carr_take_lock {name} || exit 1\n'
    "trap 'carr_release_lock; exit 143' INT TERM HUP\n"
    "trap 'carr_release_lock' EXIT\n"
)


def zsh(script, lockdir, timeout=30):
    """Run a zsh snippet with the helper sourced and CARR_LOCK_DIR pointed at a
    scratch directory, so no test ever touches the real lock path."""
    return subprocess.run(
        ["/bin/zsh", "-c", f'source "{HELPER}"\n{script}'],
        capture_output=True, text=True, timeout=timeout,
        env={**os.environ, "CARR_LOCK_DIR": lockdir},
    )


def zsh_wired(name, body, lockdir, timeout=30):
    """Same, but through the caller's full take-then-trap wiring."""
    return subprocess.run(
        ["/bin/zsh", "-c", PREAMBLE.format(helper=HELPER, name=name) + body],
        capture_output=True, text=True, timeout=timeout,
        env={**os.environ, "CARR_LOCK_DIR": lockdir},
    )


def main():
    if not os.path.exists(HELPER):
        print(f"FAIL: {HELPER} not present")
        return 1
    scratch = tempfile.mkdtemp(prefix="carr-lock-test-")
    try:
        # 1 + 2. Two racers, one winner. The first holds the lock for a beat so
        # the second is guaranteed to arrive while it is held.
        marker = os.path.join(scratch, "winners")
        a = subprocess.Popen(
            ["/bin/zsh", "-c",
             PREAMBLE.format(helper=HELPER, name="race")
             + f'print -r -- A >> {marker}; sleep 2'],
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
            env={**os.environ, "CARR_LOCK_DIR": scratch})
        time.sleep(0.6)
        b = zsh(f'if carr_take_lock race; then print -r -- B >> {marker}; '
                f'else print -r -- "loser rc=$?"; fi', scratch)
        a.wait(timeout=30)
        winners = open(marker).read().split() if os.path.exists(marker) else []
        check("exactly one racer takes the lock", winners == ["A"], f"winners={winners}")
        check("the loser exits non-fatally and names the holder",
              "loser rc=1" in b.stdout and "held by pid" in (b.stdout + b.stderr).lower(),
              f"stdout={b.stdout!r} stderr={b.stderr!r}")

        # 3a. Released on normal exit — the same lock is takeable again.
        r = zsh('carr_take_lock race && print -r -- "took it again"', scratch)
        check("the lock is free once the holder exits",
              "took it again" in r.stdout, f"stdout={r.stdout!r} stderr={r.stderr!r}")

        # 3b. Released when the holder FAILS. A chain that exits non-zero at any
        # step must not leave the lock behind; this is the common case, since the
        # chain reports the worst step outcome as its own exit code.
        zsh_wired("fail-case", "exit 7", scratch)
        r = zsh('carr_take_lock fail-case && print -r -- "free after failure"', scratch)
        check("the lock is released when the holder exits non-zero",
              "free after failure" in r.stdout, f"stdout={r.stdout!r}")

        # 3c. KILLED OUTRIGHT. This is the case the traps CANNOT cover and the
        # reason correctness rests on pid liveness instead: SIGKILL runs no trap,
        # and a SIGTERM arriving while a long step is in flight is not handled
        # until that step returns — a chain whose steps take minutes can be told
        # to die and still hold the lock when the machine goes down. Either way
        # the lock outlives its holder, and the next run must not be blocked by
        # it. Anything less means one crash quietly ends every future night.
        p = subprocess.Popen(
            ["/bin/zsh", "-c", PREAMBLE.format(helper=HELPER, name="killed-case") + "sleep 30"],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            env={**os.environ, "CARR_LOCK_DIR": scratch})
        time.sleep(0.6)
        p.send_signal(signal.SIGKILL)
        p.wait(timeout=15)
        check("a killed chain does leave its lock behind (the trap cannot fire)",
              os.path.isdir(os.path.join(scratch, "carr-killed-case.lock")))
        r = zsh('carr_take_lock killed-case && print -r -- "reclaimed after kill"', scratch)
        check("...and the next run reclaims it rather than blocking",
              "reclaimed after kill" in r.stdout, f"stdout={r.stdout!r} stderr={r.stderr!r}")

        # 4. THE CLAUSE THAT MATTERS UNATTENDED. Forge a lock held by a pid that
        # cannot exist, the way a hard kill or a reboot mid-chain leaves one, and
        # confirm the next run reclaims it rather than blocking every night after.
        stale = os.path.join(scratch, "carr-stale-case.lock")
        os.makedirs(stale, exist_ok=True)
        with open(os.path.join(stale, "pid"), "w") as fh:
            fh.write("999999\n")          # far above the pid ceiling; nothing owns it
        r = zsh('carr_take_lock stale-case && print -r -- "reclaimed"', scratch)
        check("a lock whose holder is dead is reclaimed",
              "reclaimed" in r.stdout, f"stdout={r.stdout!r} stderr={r.stderr!r}")
        check("reclaiming a stale lock says so out loud",
              "stale" in (r.stdout + r.stderr).lower(), f"stdout={r.stdout!r}")

        # 5. Two different names are two different locks.
        p = subprocess.Popen(
            ["/bin/zsh", "-c", f'source "{HELPER}"\ncarr_take_lock name-one && sleep 2'],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            env={**os.environ, "CARR_LOCK_DIR": scratch})
        time.sleep(0.6)
        r = zsh('carr_take_lock name-two && print -r -- "independent"', scratch)
        p.wait(timeout=15)
        check("different lock names do not collide",
              "independent" in r.stdout, f"stdout={r.stdout!r}")

        # 6. A helper nobody wires is not mutual exclusion. The failure this was
        # built for happens in bin/nightly.sh or it does not stop happening, so
        # the wiring is part of the contract and is asserted here rather than
        # left to survive on the memory of whoever edits the chain next.
        chain = open(os.path.join(REPO, "bin", "nightly.sh")).read()
        check("bin/nightly.sh sources the helper",
              "run-lock.sh" in chain)
        check("bin/nightly.sh takes the lock and no-ops when it cannot",
              'carr_take_lock "$SERVICE" || exit 0' in chain)
        check("bin/nightly.sh releases on signals and on exit",
              "trap 'exit 143' INT TERM HUP" in chain
              and "trap 'carr_release_lock' EXIT" in chain)
        check("bin/nightly.sh only releases a lock it actually took",
              chain.index('carr_take_lock "$SERVICE" || exit 0') < chain.index("trap 'carr_release_lock' EXIT"))
    finally:
        shutil.rmtree(scratch, ignore_errors=True)

    print(f"\npassed {checked - len(failures)} · failed {len(failures)}")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
