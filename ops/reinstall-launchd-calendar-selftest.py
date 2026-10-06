#!/usr/bin/env python3
"""Hermetic proof for `ops/config-as-code.py reinstall-launchd-calendar`.

It runs the real command against a throwaway templates directory, a throwaway
LaunchAgents directory and a stub launchctl that only records its arguments,
so nothing here can reach the machine's own agents. What it proves:

  * a dry run writes nothing and calls launchctl not at all;
  * --apply rewrites and re-bootstraps ONLY the installed agent whose body
    differs from its converted template;
  * an agent whose installed body already matches, one that is not installed,
    and one whose template is not a converted interval are all left alone;
  * nothing is kickstarted unless --kickstart is given;
  * a failed bootstrap restores the previous body and exits 1;
  * the label of the job running the command is refused, not reloaded.
"""
from __future__ import annotations

import importlib.util
import os
import plistlib
import stat
import subprocess
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
SCRIPT = REPO / "ops" / "config-as-code.py"
sys.path.insert(0, str(REPO))
from lib import launchd_calendar  # noqa: E402

spec = importlib.util.spec_from_file_location("cac_for_reinstall_test",
                                              REPO / "ops" / "config-as-code.py")
assert spec and spec.loader
cac = importlib.util.module_from_spec(spec)
spec.loader.exec_module(cac)

RESULTS: list[bool] = []


def check(label: str, condition: bool, detail: object = "") -> None:
    RESULTS.append(bool(condition))
    print(f"{'PASS' if condition else 'FAIL'}  {label}"
          + ("" if condition or detail == "" else f": {detail!r}"))


def interval_plist(label: str, seconds: int) -> str:
    return plistlib.dumps({"Label": label, "ProgramArguments": ["/usr/bin/true"],
                           "StartInterval": seconds, "RunAtLoad": True}).decode()


def build(root: Path) -> tuple[Path, Path, Path, Path]:
    templates, agents, log = root / "templates", root / "agents", root / "launchctl.log"
    templates.mkdir()
    agents.mkdir()
    fake = root / "launchctl"
    fake.write_text(
        "#!/bin/sh\n"
        f"echo \"$*\" >> '{log}'\n"
        "if [ \"$1\" = bootstrap ] && [ -n \"$FAKE_FAIL_BOOTSTRAP\" ]; then exit 5; fi\n"
        "if [ \"$1\" = bootstrap ] && [ -n \"$FAKE_FAIL_BOOTSTRAP_TIMES\" ]; then\n"
        f"  n=$(cat '{root}/bootstrap.count' 2>/dev/null || echo 0); n=$((n + 1))\n"
        f"  echo $n > '{root}/bootstrap.count'\n"
        "  [ $n -le \"$FAKE_FAIL_BOOTSTRAP_TIMES\" ] && exit 5\n"
        "fi\n"
        "if [ \"$1\" = print ] && [ -n \"$FAKE_FAIL_PRINT\" ]; then exit 3; fi\n"
        "exit 0\n", encoding="utf-8")
    fake.chmod(fake.stat().st_mode | stat.S_IXUSR)
    for name, seconds in (("a", 300), ("b", 1800), ("c", 60)):
        converted, _ = launchd_calendar.rewrite_template(
            interval_plist(f"com.carr.fixture-{name}", seconds))
        (templates / f"com.carr.fixture-{name}.plist").write_text(converted, encoding="utf-8")
    daily = plistlib.dumps({"Label": "com.carr.fixture-d", "ProgramArguments": ["/usr/bin/true"],
                            "StartCalendarInterval": {"Hour": 3, "Minute": 0}}).decode()
    (templates / "com.carr.fixture-d.plist").write_text(daily, encoding="utf-8")
    # a: installed with the dead StartInterval body -> must be reinstalled.
    (agents / "com.carr.fixture-a.plist").write_text(
        interval_plist("com.carr.fixture-a", 300), encoding="utf-8")
    # b: installed and already matching -> untouched.
    (agents / "com.carr.fixture-b.plist").write_text(
        cac.concrete((templates / "com.carr.fixture-b.plist").read_text()), encoding="utf-8")
    # c: not installed -> untouched. d: not a converted interval -> untouched.
    (agents / "com.carr.fixture-d.plist").write_text("stale daily body\n", encoding="utf-8")
    return templates, agents, log, fake


def run(templates, agents, fake, *extra, env_extra=None):
    env = {k: v for k, v in os.environ.items() if k != cac.ACTIVE_LAUNCHD_LABEL_ENV}
    env.update(env_extra or {})
    return subprocess.run(
        [sys.executable, str(SCRIPT), "reinstall-launchd-calendar", "--templates", str(templates),
         "--launch-agents", str(agents), "--launchctl", str(fake), *extra],
        capture_output=True, text=True, env=env, stdin=subprocess.DEVNULL, timeout=60)


def snapshot(agents: Path) -> dict:
    return {p.name: p.read_text() for p in sorted(agents.iterdir())}


def calls(log: Path) -> list[str]:
    return log.read_text().splitlines() if log.exists() else []


with tempfile.TemporaryDirectory(prefix="carr-reinstall-cal-") as tmp:
    templates, agents, log, fake = build(Path(tmp))
    before = snapshot(agents)
    dry = run(templates, agents, fake)
    check("dry run exits 0", dry.returncode == 0, dry.stdout + dry.stderr)
    check("dry run changes no file", snapshot(agents) == before)
    check("dry run never calls launchctl", calls(log) == [], calls(log))
    check("dry run names exactly the one agent it would reinstall",
          "would reinstall  com.carr.fixture-a.plist" in dry.stdout
          and "would reinstall  com.carr.fixture-b" not in dry.stdout, dry.stdout)

    applied = run(templates, agents, fake, "--apply")
    after = snapshot(agents)
    check("apply exits 0", applied.returncode == 0, applied.stdout + applied.stderr)
    body_a = plistlib.loads(after["com.carr.fixture-a.plist"].encode())
    check("the differing agent is rewritten to the calendar form",
          "StartInterval" not in body_a
          and launchd_calendar.cadence_seconds(body_a) == 300
          and body_a.get("RunAtLoad") is True, body_a)
    check("the matching, the uninstalled and the non-interval agents are untouched",
          after["com.carr.fixture-b.plist"] == before["com.carr.fixture-b.plist"]
          and after["com.carr.fixture-d.plist"] == before["com.carr.fixture-d.plist"]
          and "com.carr.fixture-c.plist" not in after, sorted(after))
    uid = os.getuid()
    check("launchctl is asked to bootout, bootstrap and print only the differing agent",
          calls(log) == [f"bootout gui/{uid}/com.carr.fixture-a",
                         f"bootstrap gui/{uid} {agents / 'com.carr.fixture-a.plist'}",
                         f"print gui/{uid}/com.carr.fixture-a"], calls(log))
    check("nothing is kickstarted by default",
          not any(c.startswith("kickstart") for c in calls(log)))

    log.unlink()
    again = run(templates, agents, fake, "--apply")
    check("a second apply is a no-op (everything now matches)",
          again.returncode == 0 and calls(log) == [] and snapshot(agents) == after,
          (again.stdout, calls(log)))

with tempfile.TemporaryDirectory(prefix="carr-reinstall-cal-") as tmp:
    templates, agents, log, fake = build(Path(tmp))
    kicked = run(templates, agents, fake, "--apply", "--kickstart")
    check("--kickstart kickstarts only the reinstalled agent",
          kicked.returncode == 0
          and [c for c in calls(log) if c.startswith("kickstart")]
          == [f"kickstart gui/{os.getuid()}/com.carr.fixture-a"], calls(log))

with tempfile.TemporaryDirectory(prefix="carr-reinstall-cal-") as tmp:
    templates, agents, log, fake = build(Path(tmp))
    before = snapshot(agents)
    failed = run(templates, agents, fake, "--apply", env_extra={"FAKE_FAIL_BOOTSTRAP": "1"})
    check("a failed bootstrap exits 1 and restores the previous body",
          failed.returncode == 1 and snapshot(agents) == before
          and "BOOTSTRAP FAILED" in failed.stdout, (failed.returncode, failed.stdout))

with tempfile.TemporaryDirectory(prefix="carr-reinstall-cal-") as tmp:
    templates, agents, log, fake = build(Path(tmp))
    before = snapshot(agents)
    self_run = run(templates, agents, fake, "--apply",
                   env_extra={cac.ACTIVE_LAUNCHD_LABEL_ENV: "com.carr.fixture-a"})
    check("the job running the command is refused, not reloaded",
          self_run.returncode == 1 and snapshot(agents) == before and calls(log) == [],
          (self_run.stdout, calls(log)))

# --------------------------------------------------------------------------
# In-process runs of the same command, so machine-scope sets can be patched and
# the failure paths driven one at a time.
# --------------------------------------------------------------------------
import contextlib  # noqa: E402
import io  # noqa: E402


def run_inproc(templates, agents, fake, *extra, env=None, patch=None):
    saved = {k: getattr(cac, k) for k in ("PRIMARY_ONLY",
                                          "SECONDARY_ONLY", "IS_PRIMARY")}
    saved_registry = dict(cac.launchd_hold.DEFINITION_ONLY)
    saved_env = {k: os.environ.get(k) for k in (env or {})}
    try:
        for key, value in (patch or {}).items():
            if key == "DEFINITION_ONLY":
                cac.launchd_hold.DEFINITION_ONLY.clear()
                cac.launchd_hold.DEFINITION_ONLY.update(value)
            else:
                setattr(cac, key, value)
        os.environ.pop(cac.ACTIVE_LAUNCHD_LABEL_ENV, None)
        os.environ.update(env or {})
        with contextlib.redirect_stdout(io.StringIO()) as out:
            rc = cac.cmd_reinstall_launchd_calendar(
                ["--apply", "--templates", str(templates), "--launch-agents", str(agents),
                 "--launchctl", str(fake), *extra])
        return rc, out.getvalue()
    finally:
        for key, value in saved.items():
            setattr(cac, key, value)
        cac.launchd_hold.DEFINITION_ONLY.clear()
        cac.launchd_hold.DEFINITION_ONLY.update(saved_registry)
        for key, value in saved_env.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value


A = "com.carr.fixture-a.plist"
for label, patch in (
    ("definition-only", {"DEFINITION_ONLY": {A: "held"}}),
    ("primary-only on a secondary", {"PRIMARY_ONLY": {A}, "IS_PRIMARY": False}),
    ("secondary-only on the primary", {"SECONDARY_ONLY": {A}, "IS_PRIMARY": True}),
):
    with tempfile.TemporaryDirectory(prefix="carr-reinstall-cal-") as tmp:
        templates, agents, log, fake = build(Path(tmp))
        before = snapshot(agents)
        if label == "definition-only":
            fake.write_text(
                "#!/bin/sh\n"
                f"echo \"$*\" >> '{log}'\n"
                "if [ \"$1\" = print ]; then\n"
                "  echo 'Could not find service \"com.carr.fixture-a\"' >&2; exit 113\n"
                "fi\n"
                "if [ \"$1\" = print-disabled ]; then echo '\"com.carr.fixture-a\" => disabled'; fi\n"
                "exit 0\n")
        rc, out = run_inproc(templates, agents, fake, patch=patch)
        if label == "definition-only":
            check("a definition-only agent stays disabled without a write or bootstrap",
                  rc == 0 and snapshot(agents) == before
                  and any(c.startswith("disable ") for c in calls(log))
                  and not any(c.startswith(("bootstrap ", "kickstart ", "enable ")) for c in calls(log))
                  and "DEFINITION ONLY com.carr.fixture-a:" in out, (rc, out, calls(log)))
        else:
            check(f"a {label} agent is skipped: no write, no launchctl",
                  rc == 0 and snapshot(agents) == before and calls(log) == []
                  and f"ok    {A}" in out, (rc, out, calls(log)))

with tempfile.TemporaryDirectory(prefix="carr-reinstall-cal-") as tmp:
    templates, agents, log, fake = build(Path(tmp))
    unbuilt, _ = launchd_calendar.rewrite_template(plistlib.dumps({
        "Label": "com.carr.fixture-e", "ProgramArguments": [str(Path(tmp) / "never-built")],
        "StartInterval": 300}).decode())
    (templates / "com.carr.fixture-e.plist").write_text(unbuilt, encoding="utf-8")
    (agents / "com.carr.fixture-e.plist").write_text("old e\n", encoding="utf-8")
    rc, out = run_inproc(templates, agents, fake)
    check("an agent whose program is not built here is skipped, the rest proceed",
          rc == 0 and (agents / "com.carr.fixture-e.plist").read_text() == "old e\n"
          and "not built on this machine" in out
          and not any("fixture-e" in c for c in calls(log))
          and any("fixture-a" in c for c in calls(log)), (out, calls(log)))

uid = os.getuid()
with tempfile.TemporaryDirectory(prefix="carr-reinstall-cal-") as tmp:
    templates, agents, log, fake = build(Path(tmp))
    before = snapshot(agents)
    rc, out = run_inproc(templates, agents, fake, env={"FAKE_FAIL_BOOTSTRAP_TIMES": "1"})
    dest = agents / A
    check("a failed bootstrap restores the previous body AND bootstraps it again",
          rc == 1 and snapshot(agents) == before
          and calls(log) == [f"bootout gui/{uid}/com.carr.fixture-a",
                             f"bootstrap gui/{uid} {dest}",
                             f"bootstrap gui/{uid} {dest}"]
          and "BOOTSTRAP FAILED" in out and "restored the previous body" in out,
          (rc, out, calls(log)))

with tempfile.TemporaryDirectory(prefix="carr-reinstall-cal-") as tmp:
    templates, agents, log, fake = build(Path(tmp))
    before = snapshot(agents)
    rc, out = run_inproc(templates, agents, fake, env={"FAKE_FAIL_BOOTSTRAP_TIMES": "2"})
    dest = agents / A
    check("a double bootstrap failure says RESTORE FAILED, unloaded, with the manual command",
          rc == 1 and snapshot(agents) == before
          and "RESTORE FAILED, com.carr.fixture-a is unloaded" in out
          and f"launchctl bootstrap gui/{uid} {dest}" in out, (rc, out))

with tempfile.TemporaryDirectory(prefix="carr-reinstall-cal-") as tmp:
    templates, agents, log, fake = build(Path(tmp))
    before = snapshot(agents)
    rc, out = run_inproc(templates, agents, fake, env={"FAKE_FAIL_PRINT": "1"})
    dest = agents / A
    check("print failing after a good bootstrap boots the new job out before restoring, "
          "and is reported as a PRINT failure",
          rc == 1 and snapshot(agents) == before
          and calls(log) == [f"bootout gui/{uid}/com.carr.fixture-a",
                             f"bootstrap gui/{uid} {dest}",
                             f"print gui/{uid}/com.carr.fixture-a",
                             f"bootout gui/{uid}/com.carr.fixture-a",
                             f"bootstrap gui/{uid} {dest}"]
          and "PRINT FAILED after a successful bootstrap" in out
          and "BOOTSTRAP FAILED" not in out, (rc, out, calls(log)))

with tempfile.TemporaryDirectory(prefix="carr-reinstall-cal-") as tmp:
    templates, agents, log, fake = build(Path(tmp))
    other, _ = launchd_calendar.rewrite_template(interval_plist("com.carr.fixture-f", 600))
    (templates / "com.carr.fixture-f.plist").write_text(other, encoding="utf-8")
    (agents / "com.carr.fixture-f.plist").write_text(
        interval_plist("com.carr.fixture-f", 600), encoding="utf-8")
    (agents / A).chmod(0o444)
    before_a = (agents / A).read_text()
    rc, out = run_inproc(templates, agents, fake)
    f_body = plistlib.loads((agents / "com.carr.fixture-f.plist").read_bytes())
    check("a read-only plist fails before launchd is touched, stays loaded, and the next "
          "agent is still reinstalled; exit 1 with a summary",
          rc == 1 and (agents / A).read_text() == before_a
          and not any("fixture-a" in c for c in calls(log))
          and "StartInterval" not in f_body
          and any("bootstrap" in c and "fixture-f" in c for c in calls(log))
          and "FAILED: com.carr.fixture-a.plist" in out
          and "1 reinstalled" in out, (rc, out, calls(log)))
    (agents / A).chmod(0o644)

with tempfile.TemporaryDirectory(prefix="carr-reinstall-cal-") as tmp:
    templates, agents, log, fake = build(Path(tmp))
    for extra_name in ("g", "h"):
        converted, _ = launchd_calendar.rewrite_template(
            interval_plist(f"com.carr.fixture-{extra_name}", 900))
        (templates / f"com.carr.fixture-{extra_name}.plist").write_text(converted, encoding="utf-8")
        (agents / f"com.carr.fixture-{extra_name}.plist").write_text(
            interval_plist(f"com.carr.fixture-{extra_name}", 900), encoding="utf-8")
    before = snapshot(agents)
    rc, out = run_inproc(templates, agents, fake, env={"FAKE_FAIL_BOOTSTRAP": "1"})
    touched = {c.split()[-1].split("/")[-1].replace(".plist", "") for c in calls(log)}
    check("a failed restore stops the run: later agents are not booted out, and are listed "
          "as not attempted",
          rc == 1 and snapshot(agents) == before
          and touched == {"com.carr.fixture-a"}
          and "STOPPING: com.carr.fixture-a.plist is unloaded" in out
          and "NOT ATTEMPTED: com.carr.fixture-g.plist, com.carr.fixture-h.plist" in out,
          (rc, out, calls(log)))

print(f"reinstall-launchd-calendar-selftest: {sum(RESULTS)}/{len(RESULTS)} passed")
sys.exit(0 if all(RESULTS) else 1)
