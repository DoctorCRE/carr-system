#!/usr/bin/env python3
"""selftest-harness-selftest.py — proves lib/selftest_harness.py prints and
returns exactly what the hand-rolled selftest boilerplate it replaces did.

The harness exists so ~330 selftests can stop carrying their own copy of
check()/failures/footer. That substitution is only safe if a converted suite's
output and exit code are the SAME as before, so this file does not describe the
legacy strings -- it runs the legacy code itself, verbatim, beside the harness,
and compares the captured bytes.

It deliberately does NOT use Checker for its own bookkeeping: a harness that
certifies itself with itself would pass even if both halves drifted together.

    .venv/bin/python ops/selftest-harness-selftest.py     # exit 0 = all pass
"""
import contextlib
import io
import os
import subprocess
import sys
import tempfile

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.append(os.path.join(REPO, "lib"))
import selftest_harness as H  # noqa: E402

results: list[tuple[str, bool, str]] = []


def expect(name, cond, detail=""):
    results.append((name, bool(cond), str(detail)))
    print(("  ok   " if cond else "  FAIL ") + name + ("" if cond else f" {detail}"))


def captured(fn):
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        rv = fn()
    return buf.getvalue(), rv


# ------------------------------------------------ the legacy code, verbatim
# Copied from the common pattern in e.g. ops/unread-artifact-gate-selftest.py
# (check) and its main() footer, and the limit-N footer variant from
# ops/rule-resort-weekly-selftest.py. Only `failures` is made a parameter.

def legacy_run(outcomes: list, limit: int | None = None) -> int:
    failures: list[str] = []

    def check(name, cond, detail=""):
        if cond:
            print(f"  ok   {name}")
        else:
            print(f"  FAIL {name} {detail}")
            failures.append(name)

    for name, cond, detail in outcomes:
        check(name, cond, detail)
    print()
    if failures:
        if limit is None:
            print(f"FAIL {len(failures)} check(s): {', '.join(failures)}")
        else:
            print(f"FAIL {len(failures)} check(s): {', '.join(failures[:limit])}"
                  + (" …" if len(failures) > limit else ""))
        return 1
    print("OK all checks passed")
    return 0


def harness_run(outcomes: list, limit: int | None = None) -> int:
    c = H.Checker()
    for name, cond, detail in outcomes:
        c.check(name, cond, detail)
    return c.summary(limit=limit)


CASES = {
    "all pass": [("a", True, ""), ("b", 1, "unused")],
    "one fail, empty detail": [("a", True, ""), ("b", False, "")],
    "one fail with detail": [("a", False, "exit 3"), ("b", True, "")],
    "three fail": [("x", 0, "d1"), ("y", None, ""), ("z", [], "d3")],
    "no checks at all": [],
}


def test_check_and_summary_are_byte_identical():
    for label, outcomes in CASES.items():
        for limit in (None, 2):
            want_out, want_rc = captured(lambda: legacy_run(outcomes, limit))
            got_out, got_rc = captured(lambda: harness_run(outcomes, limit))
            tag = f"{label} (limit={limit})"
            expect(f"{tag}: printed bytes match the legacy code",
                   got_out == want_out, f"\n    want={want_out!r}\n    got ={got_out!r}")
            expect(f"{tag}: exit code matches the legacy code",
                   got_rc == want_rc, f"want {want_rc}, got {got_rc}")


def test_footer_strings_are_the_literal_legacy_ones():
    c = H.Checker()
    out, rc = captured(c.summary)
    expect("a clean run prints exactly a blank line then 'OK all checks passed'",
           out == "\nOK all checks passed\n", repr(out))
    expect("a clean run returns 0", rc == 0, rc)

    c = H.Checker()
    captured(lambda: (c.check("first", False), c.check("second", False, "why")))
    out, rc = captured(c.summary)
    expect("a failed run prints exactly 'FAIL 2 check(s): first, second'",
           out == "\nFAIL 2 check(s): first, second\n", repr(out))
    expect("a failed run returns 1", rc == 1, rc)

    out, _ = captured(lambda: c.summary(limit=1))
    expect("limit=N lists N names and marks the rest with ' …'",
           out == "\nFAIL 2 check(s): first …\n", repr(out))

    out, rc = captured(lambda: c.summary("suite"))
    expect("a label prefixes the footer line and changes nothing else",
           out == "\nsuite: FAIL 2 check(s): first, second\n" and rc == 1, repr(out))


def test_check_lines_and_state():
    c = H.Checker()
    out, rv = captured(lambda: c.check("passes", True, "ignored"))
    expect("a pass prints '  ok   <name>' (two spaces, ok, three spaces)",
           out == "  ok   passes\n", repr(out))
    expect("check returns True on a pass", rv is True, rv)
    out, rv = captured(lambda: c.check("fails", False))
    expect("a failure with no detail keeps the legacy trailing space",
           out == "  FAIL fails \n", repr(out))
    expect("check returns False on a failure", rv is False, rv)
    out, _ = captured(lambda: c.check("fails2", 0, {"rc": 2}))
    expect("a non-str detail is formatted like the f-string did",
           out == "  FAIL fails2 {'rc': 2}\n", repr(out))
    expect("failures holds the failed names in order",
           c.failures == ["fails", "fails2"], c.failures)
    expect("ok is False once anything failed", c.ok is False)
    expect("ok is True on a fresh checker", H.Checker().ok is True)
    alias = c.failures
    captured(lambda: c.check("fails3", False))
    expect("failures is one list object for the checker's life (aliasable)",
           alias is c.failures and alias[-1] == "fails3")


def test_load_module():
    with H.fixture_dir("harness-selftest-") as tmp:
        path = os.path.join(tmp, "some-hyphenated-thing.py")
        with open(path, "w") as fh:
            fh.write("VALUE = 41 + 1\n")
        mod = H.load_module(path)
        expect("load_module executes a hyphenated file", mod.VALUE == 42)
        expect("the default name is the stem with hyphens as underscores",
               mod.__name__ == "some_hyphenated_thing", mod.__name__)
        expect("an explicit name is honoured",
               H.load_module(path, "custom").__name__ == "custom")
        expect("the module is not registered in sys.modules",
               "some_hyphenated_thing" not in sys.modules)
        missing = os.path.join(tmp, "nope.txt")
        try:
            H.load_module(missing)
            expect("an unloadable path raises ImportError", False, "no exception")
        except ImportError as e:
            expect("an unloadable path raises ImportError naming the path",
                   missing in str(e), str(e))


def test_fixture_dir():
    with H.fixture_dir("harness-selftest-") as tmp:
        kept = tmp
        expect("fixture_dir yields an existing directory as a str",
               isinstance(tmp, str) and os.path.isdir(tmp))
        expect("the prefix is used",
               os.path.basename(tmp).startswith("harness-selftest-"), tmp)
        with open(os.path.join(tmp, "f"), "w") as fh:
            fh.write("x")
    expect("fixture_dir removes the directory on a normal exit",
           not os.path.exists(kept))
    try:
        with H.fixture_dir("harness-selftest-") as tmp:
            kept = tmp
            raise RuntimeError("boom")
    except RuntimeError:
        pass
    expect("fixture_dir removes the directory when the block raises",
           not os.path.exists(kept))


def test_hook_env():
    before = dict(os.environ)
    env = H.hook_env("/tmp/fixture-x", CARR_HOOK_GUARD_LOG="/elsewhere", EXTRA="1")
    expect("hook_env sets CARR_HOOK_FIXTURE=1", env.get("CARR_HOOK_FIXTURE") == "1")
    expect("hook_env points telemetry into the fixture dir",
           env.get("CARR_HOOK_TELEMETRY") == "/tmp/fixture-x/telemetry.jsonl")
    expect("an extra overrides a default and adds new keys",
           env.get("CARR_HOOK_GUARD_LOG") == "/elsewhere" and env.get("EXTRA") == "1")
    expect("the default guard log is inside the fixture dir",
           H.hook_env("/t")["CARR_HOOK_GUARD_LOG"] == "/t/guard.log")
    expect("the caller's environment is inherited",
           all(env.get(k) == v for k, v in before.items()
               if k not in ("CARR_HOOK_FIXTURE", "CARR_HOOK_TELEMETRY",
                            "CARR_HOOK_GUARD_LOG", "EXTRA")))
    expect("os.environ itself is not modified", dict(os.environ) == before)


def test_documented_import_line_works_from_a_suite():
    """The import block the module docstring tells converters to paste, run
    as a real subprocess from a file one directory below the repo root -- the
    exact position of ops/*-selftest.py and tools/test-*.py."""
    body = (
        "import os, sys\n"
        "sys.path.append(os.path.join(os.path.dirname(os.path.abspath(__file__)),\n"
        "                             os.pardir, 'lib'))\n"
        "from selftest_harness import Checker  # noqa: E402\n"
        "c = Checker()\n"
        "c.check('always', True)\n"
        "c.check('maybe', os.environ.get('WANT_FAIL') != '1', 'asked to fail')\n"
        "raise SystemExit(c.summary())\n"
    )
    with tempfile.TemporaryDirectory(prefix="harness-selftest-") as td:
        sub = os.path.join(td, "ops")
        os.mkdir(sub)
        os.symlink(os.path.join(REPO, "lib"), os.path.join(td, "lib"))
        suite = os.path.join(sub, "demo-selftest.py")
        with open(suite, "w") as fh:
            fh.write(body)
        for want_fail, rc_want, tail in (("0", 0, "OK all checks passed"),
                                         ("1", 1, "FAIL 1 check(s): maybe")):
            p = subprocess.run([sys.executable, suite], capture_output=True, text=True,
                               env={**os.environ, "WANT_FAIL": want_fail}, timeout=60)
            expect(f"a suite using the documented import exits {rc_want}",
                   p.returncode == rc_want, f"rc={p.returncode} {p.stderr[-300:]}")
            expect(f"and its last line is {tail!r}",
                   p.stdout.rstrip("\n").splitlines()[-1:] == [tail], repr(p.stdout))


def main() -> int:
    print("selftest-harness-selftest: lib/selftest_harness.py against the legacy boilerplate")
    for t in (test_check_and_summary_are_byte_identical,
              test_footer_strings_are_the_literal_legacy_ones,
              test_check_lines_and_state,
              test_load_module,
              test_fixture_dir,
              test_hook_env,
              test_documented_import_line_works_from_a_suite):
        print(f"\n[{t.__name__}]")
        t()
    failed = [name for name, ok, _ in results if not ok]
    print()
    if failed:
        print(f"FAIL {len(failed)} check(s): {', '.join(failed)}")
        return 1
    print("OK all checks passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
