#!/usr/bin/env python3
"""Bounded R06 proof for two-tree hook routing and redacted overwrite evidence."""

import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
HOOKS = os.path.join(REPO, "hooks")
failures: list[str] = []


def check(label, condition, detail=""):
    print(f"  {'ok' if condition else 'FAIL'}  {label}")
    if not condition:
        failures.append(f"{label}: {detail}")


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def git(*args):
    return subprocess.run(["git", "-C", REPO, *args], check=True,
                          capture_output=True, text=True).stdout.strip()


def main():
    meter = load("r06_hook_meter", os.path.join(HOOKS, "hook_meter.py"))
    runner = load("r06_hook_meter_run", os.path.join(HOOKS, "hook-meter-run.py"))
    common = git("rev-parse", "--path-format=absolute", "--git-common-dir")
    canonical = os.path.dirname(common)

    check("duplicate top-level cwd is ambiguous",
          runner._top_level_cwd(b'{"cwd":"/first","cwd":"/second"}') is None)
    malformed = [
        b'{"cwd":"/first"} trailing',
        b'{"cwd":"/first"}{"tool_name":"Bash"}',
        b'{"tool_name":"Bash"}{"cwd":"/second"}',
    ]
    check("content after the root object is malformed",
          all(runner._top_level_cwd(payload) is None for payload in malformed))
    check("trailing whitespace after the root object remains valid",
          runner._top_level_cwd(b'{"cwd":"/first"} \t\r\n') == "/first")

    # Build the Git metadata the router reads. The invoking checkout may be a
    # linked tree outside .claude/worktrees, which must fall back to canonical.
    with tempfile.TemporaryDirectory(prefix="r06-routing-") as tmp:
        fixture_canonical = Path(tmp).resolve() / "canonical"
        fixture_common = fixture_canonical / ".git"
        helper = fixture_canonical / ".claude" / "worktrees" / "helper"
        outside = fixture_canonical / "out" / "helper"
        for name, tree in (("helper", helper), ("outside", outside)):
            (tree / "ops").mkdir(parents=True)
            admin = fixture_common / "worktrees" / name
            admin.mkdir(parents=True)
            (tree / ".git").write_text(f"gitdir: {admin}\n", encoding="utf-8")
            (admin / "commondir").write_text("../..\n", encoding="utf-8")
            (admin / "gitdir").write_text(str(tree / ".git") + "\n", encoding="utf-8")
        fixture_root = str(fixture_canonical)
        prior = os.environ.pop(meter.INVOCATION_REPO_ENV, None)
        try:
            check("Git hook resolves the registered helper tree",
                  meter.routing_repo(str(helper)) == str(helper))
            check("Claude hook resolves payload cwd to the registered helper tree",
                  meter.routing_repo(fixture_root, str(helper / "ops")) == str(helper))
            unregistered = str(helper.parent / "not-registered")
            check("unregistered helper nomination falls back canonical",
                  meter.routing_repo(fixture_root, unregistered) == fixture_root)
            check("registered tree outside helper root falls back canonical",
                  meter.routing_repo(str(outside)) == fixture_root
                  and meter.routing_repo(fixture_root, str(outside / "ops")) == fixture_root)
            (fixture_common / "worktrees" / "helper" / "gitdir").write_text(
                str(outside / ".git") + "\n", encoding="utf-8")
            check("mismatched registration backlink falls back canonical",
                  meter.routing_repo(fixture_root, str(helper)) == fixture_root)
        finally:
            if prior is not None:
                os.environ[meter.INVOCATION_REPO_ENV] = prior

    with tempfile.TemporaryDirectory(prefix="r06-hook-") as tmp:
        gate = os.path.join(tmp, "show-context.py")
        with open(gate, "w", encoding="utf-8") as fh:
            fh.write("import os\nprint(os.environ.get('CARR_HOOK_INVOCATION_REPO', ''))\n")
        telemetry = os.path.join(tmp, "telemetry.jsonl")
        guard = os.path.join(tmp, "guard.log")
        payload = {
            "tool_input": {"seed": {"cwd": canonical}},
            "cwd": os.path.join(REPO, "ops"),
            "hook_event_name": "PreToolUse",
            "tool_name": "Bash",
        }
        env = dict(os.environ)
        env.update({
            "CARR_HOOK_FIXTURE": "1",
            "CARR_HOOK_TELEMETRY": telemetry,
            "CARR_HOOK_GUARD_LOG": guard,
            meter.INVOCATION_REPO_ENV: canonical,
        })
        result = subprocess.run(
            [sys.executable, os.path.join(HOOKS, "hook-meter-run.py"), gate],
            input=json.dumps(payload), capture_output=True, text=True, env=env,
        )
        check("runner reads only the top-level existing cwd",
              result.returncode == 0 and result.stdout.strip() == os.path.join(REPO, "ops"),
              (result.returncode, result.stdout, result.stderr))

        config = load("r06_config_as_code", os.path.join(REPO, "ops", "config-as-code.py"))
        settings = os.path.join(tmp, "settings.json")
        seed = {
            "hooks": {"old": ["fixture"]},
            "permissions": {"allow": ["kept"]},
            "theme": "kept",
        }
        with open(settings, "w", encoding="utf-8") as fh:
            json.dump(seed, fh, indent=2)
            fh.write("\n")
        before = open(settings, encoding="utf-8").read()
        desired = dict(seed)
        desired["hooks"] = {"PreToolUse": ["replacement"]}
        events = []

        def fake_sink(event):
            check("fake sink fires before overwrite",
                  open(settings, encoding="utf-8").read() == before)
            events.append(event)

        config.write_claude_settings(settings, desired, before, fake_sink)
        after = json.load(open(settings, encoding="utf-8"))
        check("fake sink receives exactly one event", len(events) == 1, events)
        event = events[0] if events else {}
        check("overwrite witness is redacted and marks no notification",
              set(event) == {
                  "schema_version", "writer", "target_class", "before_sha256",
                  "after_sha256", "preserved_top_level_key_count",
                  "preserved_permission_entry_count", "actual_notification",
              } and event.get("actual_notification") is False
              and event.get("before_sha256") != event.get("after_sha256"), event)
        check("unrelated settings survive canonical writer",
              after.get("theme") == "kept"
              and after.get("permissions") == {"allow": ["kept"]}
              and after.get("hooks") == desired["hooks"], after)

    if failures:
        print("r06-hook-correctness-selftest: FAIL")
        for failure in failures:
            print(f"  {failure}")
        return 1
    print("r06-hook-correctness-selftest: PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
