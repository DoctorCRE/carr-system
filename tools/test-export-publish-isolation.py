#!/usr/bin/env python3
"""One unreadable cloud file must cost ONE target, not the whole sweep.

WHY THIS EXISTS. On 2026-09-01 the nightly chain's `exports (6 targets ->
OneDrive)` step died on a bare traceback: keep_generation spent its EDEADLK
retry budget reading the PREVIOUS OneDrive copy of the first target, and the
OSError escaped run_export entirely. Three things were wrong at once and only
one of them had been fixed. The sweep-level catch (loop #535) stops the other
targets being cancelled; it does NOT give the failing target an `export_run`
row, and it does NOT remove the staged .tmp left behind in the vault. A target
that fails without a receipt is a target `run.sh health` cannot see failing.

No vault file and no database is touched: this drives the real helper against
temporary files and injects only the redacted OneDrive error shape.
"""

import errno
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import exporters.common as common  # noqa: E402

fails: list[str] = []


def check(label, ok, detail=""):
    print(f"  {'PASS' if ok else 'FAIL'}  {label}" + (f"  — {detail}" if detail else ""))
    if not ok:
        fails.append(label)


def deadlock(*_args, **_kwargs):
    raise OSError(errno.EDEADLK, "Resource deadlock avoided")


def main():
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)

        print("1. a generation copy that cannot be taken refuses to publish")
        final = tmp / "vendors.xlsx"
        final.write_bytes(b"the previous good file\n")
        staged = final.with_name(final.name + ".tmp")
        staged.write_bytes(b"the new build\n")

        real_keep = common.keep_generation
        common.keep_generation = deadlock
        try:
            published, file_sha, error = common.publish_export(staged, final, live=True)
        finally:
            common.keep_generation = real_keep

        check("the target is reported unpublished", published is False)
        check("the error is handed back for the receipt", isinstance(error, OSError)
              and error.errno == errno.EDEADLK, repr(error))
        check("no tamper hash is invented for a file that never moved", file_sha is None)
        check("THE PREVIOUS GOOD FILE IS UNTOUCHED",
              final.read_bytes() == b"the previous good file\n", final.read_bytes())
        check("the staged .tmp is removed rather than left in the vault",
              not staged.exists())

        print("2. an ordinary publication replaces the file and hashes it")
        final2 = tmp / "lead-registry.xlsx"
        final2.write_bytes(b"old\n")
        staged2 = final2.with_name(final2.name + ".tmp")
        staged2.write_bytes(b"new bytes\n")
        published, file_sha, error = common.publish_export(staged2, final2, live=True)
        check("published", published is True)
        check("no error", error is None)
        check("the new bytes landed", final2.read_bytes() == b"new bytes\n")
        check("the tamper hash is of the WRITTEN file", file_sha == __import__("hashlib")
              .sha256(b"new bytes\n").hexdigest())
        check("the previous file was kept as a dated generation",
              any(p.read_bytes() == b"old\n"
                  for p in (final2.parent / (final2.name + ".generations")).iterdir()))
        check("the staged .tmp is consumed", not staged2.exists())

        print("3. a landed file whose tamper hash cannot be read is still a SUCCESS")
        final3 = tmp / "client-roster.xlsx"
        final3.write_bytes(b"old\n")
        staged3 = final3.with_name(final3.name + ".tmp")
        staged3.write_bytes(b"newer\n")
        real_read = Path.read_bytes
        reads = {"count": 0}

        def flaky_read(self, *a, **k):
            # keep_generation reads this same path FIRST, to keep the dated copy.
            # Only the SECOND read — the post-publication tamper hash — fails, or
            # the test would be proving the case above over again.
            if self == final3:
                reads["count"] += 1
                if reads["count"] >= 2:
                    raise OSError(errno.EDEADLK, "Resource deadlock avoided")
            return real_read(self, *a, **k)

        Path.read_bytes = flaky_read
        try:
            published, file_sha, error = common.publish_export(staged3, final3, live=True)
        finally:
            Path.read_bytes = real_read
        check("the generation copy was taken before the read-back failed",
              reads["count"] >= 2, f"reads={reads['count']}")

        check("the export still counts as published", published is True)
        check("but the tamper hash is recorded as absent, never guessed", file_sha is None)
        check("and the reason is handed back to be printed", isinstance(error, OSError))
        check("the new bytes are on disk", final3.read_bytes() == b"newer\n")

        print("4. a draft run records no tamper hash, exactly as before")
        final4 = tmp / "draft.md"
        final4.write_bytes(b"old\n")
        staged4 = final4.with_name(final4.name + ".tmp")
        staged4.write_bytes(b"draft body\n")
        published, file_sha, error = common.publish_export(staged4, final4, live=False)
        check("published", published is True)
        check("draft runs never write a live-file hash", file_sha is None)
        check("no error", error is None)

    print()
    if fails:
        print(f"FAILED: {len(fails)} check(s): " + "; ".join(fails))
        return 1
    print("export publish isolation: all checks pass")
    return 0


if __name__ == "__main__":
    sys.exit(main())
