#!/usr/bin/env python3
"""Every launchd job declaration in the repository must parse with plistlib.

WHY THIS IS ITS OWN FILE, which is the whole point of it. This assertion
already existed, inside ops/config-as-code-selftest.py, and it did not stop the
bug it was written for. On 2026-08-22 the room-bridge job (#488) merged with a
double hyphen inside an XML comment — XML forbids that sequence there — and its
hosted run passed. The check was never asked.

THE CAUSE IS A FENCE THAT COVERS TOO MUCH. ops/config/ci-check-scope.json marks
config-as-code-selftest.py local_only, and its reason is true and evidenced: the
suite compares the LIVE ~/.claude/settings.json hooks block against the repo's
baseline, a hosted runner has no installed Claude Code, and it failed on the
ubuntu runner on 2026-08-13. That reason justifies the MACHINE-STATE assertions
in that file. It never justified this one, which reads nothing but files in this
repository — PLIST_DIR below is derived from this file's own path. A portable
assertion sat behind a machine-shaped fence, so on every hosted run it was
skipped along with everything else in the file, silently and by construction.

That is the shape worth naming, because it generalises: an exemption is declared
per FILE and earned per ASSERTION, so the moment a portable check joins a file
with a local-only reason, it stops running where it matters most and nothing
reports a gap. The coverage looks intact — the suite is announced as "not run"
with an honest reason — while the check that could have caught the defect is
quietly inside it.

SECOND OCCURRENCE, NOT FIRST. The comment on the original assertion records the
same bug on 2026-08-18 in com.carr.partner-ping.plist. So this class of failure
has now shipped twice, and the check written in response to the first one could
not catch the second, because of where it lived rather than what it said.

WHAT A LENIENT PARSER COSTS. launchd and plutil use CoreFoundation's forgiving
reader and accept these files, so the job runs and nothing looks wrong. Every
REPOSITORY tool that inspects a job uses plistlib. When plistlib refuses,
_missing_paths in ops/config-as-code.py catches the error and returns [] — "no
missing paths" — so the guard that stops a job installing when its program was
never built silently does nothing for that job. A file only launchd can read is
a file the tooling skips without saying so.

Run: python3 ops/launchd-plist-portable-selftest.py
"""
from __future__ import annotations

import json
import plistlib
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
PLIST_DIR = REPO / "ops" / "launchd"
SCOPE = REPO / "ops" / "config" / "ci-check-scope.json"
SELF = Path(__file__).name
HOMEBREW_BIN = "/opt/homebrew/bin"

failures: list[str] = []
checked = 0


def check(label: str, ok: bool, detail: str = "") -> None:
    global checked
    checked += 1
    if ok:
        print(f"  ok    {label}")
    else:
        print(f"  FAIL  {label}" + (f"  {detail}" if detail else ""))
        failures.append(label)


def main() -> int:
    plists = sorted(PLIST_DIR.glob("*.plist"))
    # An empty directory would make every assertion below vacuously true, which
    # is the failure mode this whole file exists to stop happening elsewhere.
    check("there are launchd declarations to check at all",
          bool(plists),
          f"{PLIST_DIR} holds no .plist files")

    for path in plists:
        try:
            with open(path, "rb") as handle:
                plistlib.load(handle)
            parsed, detail = True, ""
        except Exception as exc:
            parsed = False
            detail = f"{type(exc).__name__}: {exc}"
        check(f"{path.name} parses with plistlib, not just plutil", parsed, detail)
        if not parsed:
            continue
        # launchd's default PATH is /usr/bin:/bin:/usr/sbin:/sbin. A job that
        # reaches gh, node, npm, jq or any other Homebrew tool without its own
        # PATH fails "No such file or directory" only under launchd, never in
        # the terminal that tested it: com.carr.job-watchdog shipped that way
        # (#1431) and flooded every prompt with false evidence errors. Position is
        # deliberately unchecked: job-watchdog puts Homebrew first, the older jobs
        # append it so every binary they already resolved keeps resolving the same.
        with open(path, "rb") as handle:
            env = plistlib.load(handle).get("EnvironmentVariables") or {}
        check(f"{path.name} declares a PATH that reaches Homebrew",
              HOMEBREW_BIN in str(env.get("PATH", "")).split(":"),
              f"EnvironmentVariables.PATH is {env.get('PATH')!r}")

    # THE FENCE GUARD, and the reason this file protects itself rather than
    # trusting a convention. The defect above was not a wrong assertion; it was
    # a correct assertion in a file somebody had a good reason to exempt. If
    # this suite is ever added to a scope exemption, the portable coverage
    # disappears again in exactly the same silent way — so it fails here
    # instead, naming the list it was added to.
    if SCOPE.is_file():
        scope = json.loads(SCOPE.read_text(encoding="utf-8"))
        for family in ("local_only", "quarantined"):
            listed = any(entry.get("check") == SELF
                         for entry in scope.get(family, []))
            check(f"this suite is not exempted as {family}",
                  not listed,
                  f"{SELF} is listed under {family} in {SCOPE.name}; the portable "
                  "plist check would stop running on hosted CI, which is the "
                  "exact defect this file was split out to fix")

    print(f"\nlaunchd plist portable selftest: {checked - len(failures)}/{checked} passed")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
