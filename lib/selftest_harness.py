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
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
from types import ModuleType
from typing import TYPE_CHECKING, Iterator, List, Optional

if TYPE_CHECKING:
    from lib.hook_runtime import Verdict
elif __package__:
    from .hook_runtime import Verdict
else:
    from hook_runtime import Verdict

__all__ = ["Checker", "load_module", "load_hook", "fixture_dir", "hook_env",
           "HookSandbox", "HookResult"]

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


REPO = Path(__file__).resolve().parent.parent


def load_hook(name: str, *, repo=REPO) -> ModuleType:
    return load_module(Path(repo) / "hooks" / (name.removesuffix(".py") + ".py"))


class HookResult(Verdict):
    """The hook's exit code, original streams and parsed verdict."""

    @property
    def envelopes(self):
        envelopes = []
        for line in self.stdout.splitlines():
            try:
                envelope = json.loads(line)
            except ValueError:
                continue
            if isinstance(envelope, dict):
                envelopes.append(envelope)
        return envelopes


class HookSandbox:
    """Own the source tree, home, command replies and state used by a hook."""

    def __init__(self, *, repo=REPO, prefix="gate-selftest-"):
        self.source = Path(repo).resolve()
        self._temporary = tempfile.TemporaryDirectory(prefix=prefix)
        self.root = Path(self._temporary.name).resolve()
        self.repo = self.root / "carr-system"
        git_env = load_module(self.source / "ops" / "git_env.py")
        tracked = subprocess.run(
            ["git", "ls-files", "-z", "--cached", "--stage", "--",
             "hooks", "lib", "ops", "tools"],
            cwd=self.source, env=git_env.scrubbed_env(),
            capture_output=True, check=True).stdout.decode().split("\0")
        for entry in sorted(set(tracked) - {""}):
            metadata, name = entry.split("\t", 1)
            path = Path(name)
            if any(part in {"__pycache__", "out", "runtime"} for part in path.parts):
                raise ValueError(f"runtime state is not fixture source: {name}")
            source = self.source / path
            if source.is_symlink():
                raise ValueError(f"fixture source must be a regular file: {name}")
            target = self.repo / path
            if metadata.split()[0] == "160000":
                # A gitlink records a separate checkout, not public source bytes
                # in this repository. Keep its location without following it.
                target.mkdir(parents=True, exist_ok=True)
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, target)
        self.home = self.root / "home"
        self.carr = self.root / "carr"
        self.home.mkdir()
        self.carr.mkdir()
        self.run_sh = self.carr / "run.sh"
        self.run_sh.write_text('#!/bin/sh\necho "$@" >> "$CARR_ROOT/calls"\n'
                               'cat "$CARR_ROOT/reply"\n')
        self.run_sh.chmod(0o755)
        self.reply("")
        self.guard_log = self.root / "hook-guard.log"
        self.env = git_env.fixture_env(hook_env(str(self.root),
            HOME=str(self.home), CARR_ROOT=str(self.carr),
            CARR_REPO_ROOT=str(self.repo),
            CARR_HOOK_GUARD_LOG=str(self.guard_log),
            CARR_JEV_WORKER="off",
            CARR_STOP_LATCH_STATE=str(self.root / "stop-latch")))
        configured = {key: value for key, value in self.env.items()
                      if key.startswith("CARR_") and key in {
                          "CARR_HOOK_FIXTURE", "CARR_HOOK_TELEMETRY", "CARR_ROOT",
                          "CARR_REPO_ROOT", "CARR_HOOK_GUARD_LOG", "CARR_JEV_WORKER",
                          "CARR_STOP_LATCH_STATE"}}
        self.env = {key: value for key, value in self.env.items()
                    if not key.startswith("CARR_")}
        self.env.update(configured)
        self.env["CARR_JEV_OFFLINE"] = "1"

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        self._temporary.cleanup()

    def reply(self, text):
        (self.carr / "reply").write_text(text)

    def calls(self):
        path = self.carr / "calls"
        return path.read_text().splitlines() if path.exists() else []

    def transcript(self, turns):
        records = []
        for turn in turns:
            if isinstance(turn, dict):
                records.append(turn)
            else:
                role, content = turn
                records.append({"type": role,
                                "message": {"role": role, "content": content}})
        path = self.root / "transcript.jsonl"
        path.write_text("".join(json.dumps(record) + "\n" for record in records))
        return path

    def fire(self, name, event, *, turns=None, env=None, timeout=30, argv=None):
        command = argv or [sys.executable, str(self.repo / "hooks" /
                                              (name.removesuffix(".py") + ".py"))]
        command = [str(self.repo / Path(arg).relative_to(self.source))
                   if arg != sys.executable and Path(arg).is_absolute() and Path(arg).is_relative_to(self.source)
                   else arg for arg in command]
        if turns is not None:
            event = {**event, "transcript_path": str(self.transcript(turns))}
        stdin = event if isinstance(event, str) else json.dumps(event)
        process = subprocess.run(command, input=stdin, text=True,
                                 capture_output=True, timeout=timeout,
                                 cwd=self.repo,
                                 env={**self.env, **(env or {})})
        return HookResult(process.returncode, process.stdout, process.stderr)
