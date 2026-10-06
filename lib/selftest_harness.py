"""selftest_harness — the boilerplate ops/*-selftest.py files kept writing by hand.

About 330 selftests under ops/ (plus tools/test-*.py) each carry their own copy
of the same four pieces:

  * a check(name, cond, detail) helper that prints "  ok   name" or
    "  FAIL name detail" and appends the name to a module-level failures list;
  * a footer that prints a blank line and then "OK all checks passed" or
    "FAIL N check(s): a, b, ..." and returns 0 or 1;
  * the importlib.util.spec_from_file_location + exec_module incantation,
    because hooks/ and ops/ are not packages and their files are hyphenated;
  * a tempfile fixture directory torn down with shutil.rmtree in a finally,
    and an env_for() that sets CARR_HOOK_FIXTURE=1 and points the hook
    telemetry and guard log at that directory.

This module is those pieces once. THE PRINTED BYTES ARE THE CONTRACT: a suite
converted to Checker prints exactly what its hand-rolled copy printed, so a log
read before and after a conversion is the same log, and ops/ci.sh -- which
decides pass or fail from the exit code and prints the captured log through
fail_tail on a failure -- sees nothing change. ops/selftest-harness-selftest.py
pins those strings byte for byte.

Import it from a selftest the way lib/ is reached elsewhere, without a package:

    sys.path.append(os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                 os.pardir, "lib"))
    from selftest_harness import Checker  # noqa: E402

append, not insert: lib/ goes LAST on the path, so it can never shadow a
module the suite already resolves from its own directory or from the repo.
"""
from __future__ import annotations

import contextlib
import importlib.util
import os
import shutil
import tempfile
from types import ModuleType
from typing import Iterator, List, Optional

__all__ = ["Checker", "load_module", "fixture_dir", "hook_env"]

OK_LINE = "OK all checks passed"


class Checker:
    """Collects named pass/fail results and prints the legacy footer.

    `failures` is a plain list and is the SAME object for the checker's whole
    life, so a converted suite can alias it (`failures = CHECK.failures`) and
    every existing `failures.append(...)` or `if failures:` keeps working.
    """

    def __init__(self) -> None:
        self.failures: List[str] = []

    def check(self, name: str, cond: object, detail: object = "") -> bool:
        """Record one result. Prints the legacy lines exactly:
        "  ok   <name>" on pass, "  FAIL <name> <detail>" on failure (the
        trailing space before an empty detail is part of the legacy bytes)."""
        if cond:
            print(f"  ok   {name}")
            return True
        print(f"  FAIL {name} {detail}")
        self.failures.append(name)
        return False

    @property
    def ok(self) -> bool:
        return not self.failures

    def summary(self, label: Optional[str] = None, *,
                limit: Optional[int] = None) -> int:
        """Print the footer and return the exit code: 0 when every check
        passed, 1 otherwise.

        With no label the output is byte-identical to the hand-rolled footer:

            <blank line>
            OK all checks passed
        or
            <blank line>
            FAIL 2 check(s): first, second

        `limit` reproduces the variant that lists only the first N names and
        appends " …" when more failed. `label`, when given, prefixes the footer
        line as "<label>: " so a suite can name itself; converted suites leave
        it unset, because their legacy output never carried one.
        """
        prefix = f"{label}: " if label else ""
        print()
        if self.failures:
            shown = self.failures if limit is None else self.failures[:limit]
            more = " …" if limit is not None and len(self.failures) > limit else ""
            print(f"{prefix}FAIL {len(self.failures)} check(s): "
                  f"{', '.join(shown)}{more}")
            return 1
        print(f"{prefix}{OK_LINE}")
        return 0


def load_module(path: str | os.PathLike[str],
                name: Optional[str] = None) -> ModuleType:
    """Load a file that `import` cannot spell (hyphenated, not in a package).

    `name` defaults to the file's stem with hyphens turned into underscores.
    Unlike the bare three-line incantation, a missing or unloadable path fails
    with an ImportError naming the path instead of an AttributeError on None.
    The module is NOT registered in sys.modules, matching the legacy pattern.
    """
    path = os.fspath(path)
    if name is None:
        name = os.path.splitext(os.path.basename(path))[0].replace("-", "_")
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise ImportError(f"cannot load {name} from {path} — no importable module there")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@contextlib.contextmanager
def fixture_dir(prefix: str = "selftest-") -> Iterator[str]:
    """A fresh temporary directory (as a str path, like mkdtemp) that is
    removed on exit however the block exits."""
    tmp = tempfile.mkdtemp(prefix=prefix)
    try:
        yield tmp
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def hook_env(tmp: str, **extra: str) -> dict[str, str]:
    """The environment a hook under test runs in: the caller's environment,
    CARR_HOOK_FIXTURE=1 so hook_meter treats the run as a fixture, and the
    hook telemetry and guard log redirected into `tmp` so a selftest never
    writes to the real ones. `extra` overrides or adds variables last."""
    env = dict(os.environ)
    env["CARR_HOOK_FIXTURE"] = "1"
    env["CARR_HOOK_TELEMETRY"] = os.path.join(tmp, "telemetry.jsonl")
    env["CARR_HOOK_GUARD_LOG"] = os.path.join(tmp, "guard.log")
    env.update(extra)
    return env
