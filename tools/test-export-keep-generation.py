#!/usr/bin/env python3
"""Focused, synthetic checks for FileProvider-safe export generations.

No vault files or database are touched. The test exercises the real helper
against temporary files and injects only the redacted OneDrive error shape.
"""

import errno
import shutil
import sys
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import exporters.common as common  # noqa: E402

fails: list[str] = []


def check(label, ok, detail=""):
    print(f"  {'PASS' if ok else 'FAIL'}  {label}" + (f"  — {detail}" if detail else ""))
    if not ok:
        fails.append(label)


def gen_dir_for(target: Path) -> Path:
    return target.parent / (target.name + ".generations")


def generation_files(target: Path) -> list[Path]:
    directory = gen_dir_for(target)
    return sorted(path for path in directory.iterdir() if not path.name.startswith(".")) if directory.exists() else []


def check_provider_wait(tmp: Path) -> None:
    """The shared wait in front of the sweep, on the outcomes that matter.

    Its NO must be as trustworthy as its YES, so a provider that never recovers
    and one that fails for a non-transient reason are tested as hard as the
    happy path. The clock is injected, so none of this sleeps.
    """
    target = tmp / "probe.xlsx"
    target.write_bytes(b"rows\n")
    slept: list[float] = []

    check("a ready provider returns nothing cold",
          common.wait_for_provider([target]).cold == [])

    check("a path that does not exist is not waited for",
          common.wait_for_provider([tmp / "absent.xlsx"]).cold == [])

    # A provider that refuses twice and then serves must PASS, because the
    # 02:05 outage is transient and giving up early is the whole bug.
    state = {"calls": 0, "now": 0.0}

    def advance(interval):
        slept.append(interval)
        state["now"] += interval

    def flaky(paths, *, timeout):
        state["calls"] += 1
        if state["calls"] <= 2:
            return [(target, OSError(errno.EDEADLK, "Resource deadlock avoided"))]
        return common.probe_provider_files(paths, timeout=timeout)

    def wait(probe, budget=600):
        return common.wait_for_provider(
            [target], budget_seconds=budget, poll_seconds=0, sleep=advance,
            monotonic=lambda: state["now"], running=lambda **kw: True,
            probe=probe).cold

    cold = wait(flaky)
    check("a transient refusal that clears returns ready", cold == [])
    check("it actually waited rather than passing blind", state["calls"] >= 3,
          f"{state['calls']} probe(s)")

    # A permanently cold file still returns so each target can run. Both
    # sleep and monotonic advance the same fake clock; no wall-time spinning.
    slept.clear()
    cold = wait(lambda paths, **kw: [(target, OSError(errno.EDEADLK, "cold"))], .05)
    check("an exhausted budget reports the cold file", len(cold) == 1)
    check("an exhausted budget does not raise", True)
    check("a zero poll cannot busy-spin",
          len(slept) <= int(.05 / .01) + 2,
          f"{len(slept)} sleep(s): {[round(i, 4) for i in slept]}")

    # A permission failure reads the same on the last attempt as the first.
    slept.clear()
    cold = wait(lambda paths, **kw: [(target, OSError(errno.EACCES, "Permission denied"))])
    check("a hard error is reported at once", len(cold) == 1)
    check("a hard error is not waited out", slept == [], f"{len(slept)} sleep(s)")


def main():
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)

        print("1. keeps an atomic byte-identical copy without shutil's fcopyfile path")
        target = tmp / "lead-board.md"
        target.write_bytes(b"first generation\n")
        exploded = []

        def boom(*args, **kwargs):
            exploded.append(args)
            raise OSError(errno.EDEADLK, "Resource deadlock avoided")

        real_copy2, real_copyfile = shutil.copy2, shutil.copyfile
        shutil.copy2, shutil.copyfile = boom, boom
        try:
            common.keep_generation(target)
        finally:
            shutil.copy2, shutil.copyfile = real_copy2, real_copyfile
        kept = generation_files(target)
        check("shutil copy helpers are never called", not exploded, f"called {len(exploded)}x")
        check("one generation is kept", len(kept) == 1, f"found {len(kept)}")
        check("copy is byte-identical", kept and kept[0].read_bytes() == b"first generation\n")
        check("copy uses the stamped source name", kept and kept[0].name.endswith("-lead-board.md"))

        print("2. EDEADLK retries twice, then publishes one complete generation")
        deadlock_target = tmp / "deal-room.md"
        deadlock_target.write_bytes(b"cloud path payload\n")
        real_read_bytes, real_sleep = Path.read_bytes, common.time.sleep
        deadlock_reads, sleeps = [], []

        def deadlock_then_read(path):
            if path == deadlock_target and len(deadlock_reads) < 2:
                deadlock_reads.append(path)
                raise OSError(errno.EDEADLK, "Resource deadlock avoided")
            return real_read_bytes(path)

        Path.read_bytes = deadlock_then_read
        common.time.sleep = sleeps.append
        try:
            common.keep_generation(deadlock_target)
        finally:
            Path.read_bytes, common.time.sleep = real_read_bytes, real_sleep
        kept = generation_files(deadlock_target)
        check("EDEADLK used all required attempts", len(deadlock_reads) == 2,
              f"failed reads {len(deadlock_reads)}")
        # A fixture that stalls TWICE sleeps twice, which is the leading slice of
        # the declared backoff and not the whole of it. Those were the same list
        # only while ATTEMPTS was 3, and asserting equality silently welded this
        # partial-retry case to the size of the budget: widening the budget on
        # 2026-08-26 failed here for no reason but the coincidence.
        check("EDEADLK backoff is the ordered leading slice of the declared budget",
              sleeps == list(common.GENERATION_COPY_BACKOFF_SECONDS[:len(sleeps)]),
              f"{sleeps!r} vs budget {list(common.GENERATION_COPY_BACKOFF_SECONDS)!r}")
        check("EDEADLK slept once between each attempt", len(sleeps) == len(deadlock_reads),
              f"{len(sleeps)} sleep(s) for {len(deadlock_reads)} failed read(s)")
        check("retry leaves one complete generation",
              len(kept) == 1 and kept[0].read_bytes() == b"cloud path payload\n")

        print("2b. the budget is sized against a real hydration stall")
        # The retry shipped on 2026-08-25 as 3 attempts over 0.05s + 0.10s, and
        # the curriculum export died the same EDEADLK death on 08-26 anyway: a
        # sixth of a second is indistinguishable from no retry while OneDrive's
        # FileProvider materialises a dehydrated file. The step began 07:05:13Z
        # and failed 07:05:15Z; the same file read by hand two minutes later
        # succeeded first try. So the reviewable number is total wall-clock, and
        # nothing was asserting it.
        budget = sum(common.GENERATION_COPY_BACKOFF_SECONDS)
        check("total backoff outlasts a hydration stall", budget >= 20.0,
              f"{budget:.2f}s of backoff")
        # Both retry loops index BACKOFF_SECONDS[attempt] on every attempt but
        # the last, so a mismatch turns the retry into an IndexError raised from
        # inside the error handler.
        check("attempts and backoff steps agree",
              len(common.GENERATION_COPY_BACKOFF_SECONDS) == common.GENERATION_COPY_ATTEMPTS - 1,
              f"{common.GENERATION_COPY_ATTEMPTS} attempts, "
              f"{len(common.GENERATION_COPY_BACKOFF_SECONDS)} backoff step(s)")
        check("backoff never shrinks",
              all(a <= b for a, b in zip(common.GENERATION_COPY_BACKOFF_SECONDS,
                                         common.GENERATION_COPY_BACKOFF_SECONDS[1:])),
              repr(common.GENERATION_COPY_BACKOFF_SECONDS))
        # Named constants, not integers: EDEADLK is 11 on macOS where EAGAIN is
        # 35, and on Linux those numbers swap. Exact in both directions, so a
        # permission or storage failure still escapes instead of burning the
        # budget and surfacing late.
        # ETIMEDOUT joined on 2026-09-15 and is the one that was actually
        # firing nightly: a dehydrated OneDrive file's first read times out
        # rather than returning either of the other two. Exact set, still, so a
        # permission or storage failure escapes instead of burning the budget.
        check("exactly the three FileProvider transients are retryable",
              common.GENERATION_COPY_RETRY_ERRNOS ==
              frozenset({errno.EAGAIN, errno.EDEADLK, errno.ETIMEDOUT}),
              repr(sorted(common.GENERATION_COPY_RETRY_ERRNOS)))

        print("3. EAGAIN is transient, but exhaustion remains an error")
        again_target = tmp / "vendors.md"
        again_target.write_bytes(b"retry me\n")
        again_reads, sleeps = [], []

        def always_again(path):
            if path == again_target:
                again_reads.append(path)
                raise OSError(errno.EAGAIN, "Resource temporarily unavailable")
            return real_read_bytes(path)

        Path.read_bytes = always_again
        common.time.sleep = sleeps.append
        exhausted = None
        try:
            common.keep_generation(again_target)
        except OSError as error:
            exhausted = error
        finally:
            Path.read_bytes, common.time.sleep = real_read_bytes, real_sleep
        check("exhausted EAGAIN escapes", exhausted is not None and exhausted.errno == errno.EAGAIN,
              repr(exhausted))
        check("exhausted EAGAIN attempts are capped", len(again_reads) == common.GENERATION_COPY_ATTEMPTS,
              f"attempts {len(again_reads)}")
        check("exhausted EAGAIN sleeps only between attempts",
              sleeps == list(common.GENERATION_COPY_BACKOFF_SECONDS), repr(sleeps))
        check("exhausted retry publishes no partial generation", not generation_files(again_target))
        check("exhausted retry removes staged files", not list(gen_dir_for(again_target).glob(".*")))

        print("4. permanent copy failures escape without a retry")
        permanent_target = tmp / "renewal-feed.md"
        permanent_target.write_bytes(b"do not hide this\n")
        permanent_reads, sleeps = [], []

        def permission_denied(path):
            if path == permanent_target:
                permanent_reads.append(path)
                raise OSError(errno.EACCES, "Permission denied")
            return real_read_bytes(path)

        Path.read_bytes = permission_denied
        common.time.sleep = sleeps.append
        permanent = None
        try:
            common.keep_generation(permanent_target)
        except OSError as error:
            permanent = error
        finally:
            Path.read_bytes, common.time.sleep = real_read_bytes, real_sleep
        check("permanent failure escapes", permanent is not None and permanent.errno == errno.EACCES,
              repr(permanent))
        check("permanent failure does not retry", len(permanent_reads) == 1 and not sleeps,
              f"reads={len(permanent_reads)} sleeps={sleeps}")
        check("permanent failure publishes no generation", not generation_files(permanent_target))
        check("permanent failure removes its staged file",
              not list(gen_dir_for(permanent_target).glob(".*")))

        print("4b. a cold-cloud read timeout retries and then publishes")
        # THE REAL NIGHTLY FAILURE, pinned. A dehydrated OneDrive file's first
        # read returns ETIMEDOUT, and that read is what triggers hydration, so
        # the retry succeeds. Before 2026-09-15 this errno was not in the retry
        # set and escaped on attempt one, which cost six consecutive nights of
        # exports. The case asserts BOTH halves: it retries, and it publishes.
        timeout_target = tmp / "vendors-timeout.md"
        timeout_target.write_bytes(b"hydrates on the second read\n")
        timeout_reads, timeout_sleeps = [], []

        def timed_out_once(path):
            if path == timeout_target and not timeout_reads:
                timeout_reads.append(path)
                raise OSError(errno.ETIMEDOUT, "Operation timed out")
            if path == timeout_target:
                timeout_reads.append(path)
            return real_read_bytes(path)

        Path.read_bytes = timed_out_once
        common.time.sleep = timeout_sleeps.append
        try:
            common.keep_generation(timeout_target)
        finally:
            Path.read_bytes, common.time.sleep = real_read_bytes, real_sleep
        check("cold-read timeout is retried rather than escaping",
              len(timeout_reads) == 2 and timeout_sleeps == [common.GENERATION_COPY_BACKOFF_SECONDS[0]],
              f"reads={len(timeout_reads)} sleeps={timeout_sleeps}")
        check("cold-read timeout still publishes one complete generation",
              len(generation_files(timeout_target)) == 1,
              repr(generation_files(timeout_target)))
        check("ETIMEDOUT is declared retryable",
              errno.ETIMEDOUT in common.GENERATION_COPY_RETRY_ERRNOS)

        print("5. an existing same-second generation is never overwritten")
        collision_target = tmp / "client-roster.md"
        collision_target.write_bytes(b"new rollback point\n")
        collision_dir = gen_dir_for(collision_target)
        collision_dir.mkdir()
        stamp = "20260825T123456Z"
        original = collision_dir / f"{stamp}-{collision_target.name}"
        original.write_bytes(b"existing rollback point\n")
        real_datetime = common.datetime

        class FrozenDatetime:
            @classmethod
            def now(cls, tz):
                return datetime(2026, 8, 25, 12, 34, 56, tzinfo=timezone.utc)

        common.datetime = FrozenDatetime
        try:
            common.keep_generation(collision_target)
        finally:
            common.datetime = real_datetime
        kept = generation_files(collision_target)
        check("existing generation remains unchanged", original.read_bytes() == b"existing rollback point\n")
        check("collision receives a distinct generation", len(kept) == 2 and
              any(path.read_bytes() == b"new rollback point\n" for path in kept),
              repr([path.name for path in kept]))
        check("temporary staging files are removed", not list(collision_dir.glob(".*")))

        print("6. a concurrent same-second publisher cannot be overwritten")
        race_target = tmp / "concurrent.md"
        race_target.write_bytes(b"this run's rollback point\n")
        race_dir = gen_dir_for(race_target)
        race_dir.mkdir()
        race_base = race_dir / f"{stamp}-{race_target.name}"
        real_link, real_replace = common.os.link, common.os.replace
        interleaved = []

        def claim_then_link(source, destination):
            if not interleaved:
                Path(destination).write_bytes(b"concurrent rollback point\n")
                interleaved.append(Path(destination))
            return real_link(source, destination)

        def claim_then_replace(source, destination):
            # This branch is what the former check-then-replace implementation
            # called: it creates the competing file, then proves replace clobbers it.
            if not interleaved:
                Path(destination).write_bytes(b"concurrent rollback point\n")
                interleaved.append(Path(destination))
            return real_replace(source, destination)

        common.datetime = FrozenDatetime
        common.os.link, common.os.replace = claim_then_link, claim_then_replace
        try:
            common.keep_generation(race_target)
        finally:
            common.datetime = real_datetime
            common.os.link, common.os.replace = real_link, real_replace
        kept = generation_files(race_target)
        check("interleaving claimed the primary generation name", interleaved == [race_base],
              repr(interleaved))
        check("concurrent generation remains unchanged",
              race_base.read_bytes() == b"concurrent rollback point\n")
        check("this run publishes under the next atomic name", len(kept) == 2 and
              any(path != race_base and path.read_bytes() == b"this run's rollback point\n"
                  for path in kept), repr([path.name for path in kept]))
        check("race leaves no temporary staging file", not list(race_dir.glob(".*")))

        print("7. missing sources remain a no-op and pruning stays at the cap")
        common.keep_generation(tmp / "never-existed.md")
        check("missing source creates nothing",
              not (tmp / "never-existed.md.generations").exists())

        prune_target = tmp / "prune.md"
        prune_dir = gen_dir_for(prune_target)
        prune_dir.mkdir()
        for index in range(common.KEEP_GENERATIONS + 3):
            (prune_dir / f"202608{10 + index:02d}T000000Z-prune.md").write_bytes(b"old\n")
        prune_target.write_bytes(b"newest\n")
        common.keep_generation(prune_target)
        kept = generation_files(prune_target)
        check("prunes to configured cap", len(kept) == common.KEEP_GENERATIONS, f"found {len(kept)}")
        check("newest generation survives pruning", any(path.read_bytes() == b"newest\n" for path in kept))

        check_provider_wait(tmp)

    print()
    if fails:
        print(f"{len(fails)} FAILED: {', '.join(fails)}")
        return 1
    print("generation copy retries only transient FileProvider failures without overwriting rollback points")
    return 0


if __name__ == "__main__":
    sys.exit(main())
