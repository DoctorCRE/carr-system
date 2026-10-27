#!/usr/bin/env python3
"""Hermetic regression proof for active LaunchAgent self-install handling."""
from __future__ import annotations

import contextlib
import hashlib
import importlib.util
import io
import json
import os
import plistlib
import re
import signal
import stat
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import patch

REPO = Path(__file__).resolve().parent.parent
spec = importlib.util.spec_from_file_location(
    "config_as_code_launchd", REPO / "ops" / "config-as-code.py"
)
assert spec and spec.loader
mod: Any = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)
# LaunchAgent fixtures must never target administrator-owned client policy.
mod.managed_safety_plan = lambda: []


def plist(label: str, program: str = "/usr/bin/true") -> str:
    return plistlib.dumps({
        "Label": label,
        "ProgramArguments": [program],
        "RunAtLoad": False,
    }).decode("utf-8")


def check(label: str, condition: bool, detail: object = "") -> bool:
    print(f"{'PASS' if condition else 'FAIL'}  {label}"
          + ("" if condition or not detail else f": {detail}"))
    return condition


def _fake_launchctl(root: Path, fail_bootstrap_times: int,
                    rewrite_on_bootout: str = "") -> tuple[Path, Path]:
    """A stub launchctl that records calls and fails its first N bootstraps.

    With ``rewrite_on_bootout`` it also rewrites the installed plist during
    bootout, the way a concurrent install landing in that gap would."""
    log, counter = root / "launchctl.log", root / "bootstrap.count"
    fake = root / "launchctl"
    rewrite = (f"[ \"$1\" = bootout ] && printf '%s' '{rewrite_on_bootout}' > "
               f"'{root / 'agent.plist'}'\n") if rewrite_on_bootout else ""
    fake.write_text(
        "#!/bin/sh\n"
        f"echo \"$*\" >> '{log}'\n" + rewrite +
        "if [ \"$1\" = bootstrap ]; then\n"
        f"  n=$(cat '{counter}' 2>/dev/null || echo 0); n=$((n + 1)); echo $n > '{counter}'\n"
        f"  [ $n -le {fail_bootstrap_times} ] && exit 5\n"
        "fi\nexit 0\n", encoding="utf-8")
    fake.chmod(fake.stat().st_mode | stat.S_IXUSR)
    return fake, log


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


# The helper is started with /bin/bash; it must also stay correct if a
# platform's sh is dash, so every case below runs under each shell present.
SHELLS = [sh for sh in ("/bin/bash", "/bin/dash") if os.path.exists(sh)]


def _start_helper(root: Path, pg: str, fails: int = 0, wait_max: str = "30",
                  shell: str = "/bin/bash", rewrite_on_bootout: str = ""):
    """Stage NEW over an installed OLD and start the real one-shot against it."""
    fake, calls_log = _fake_launchctl(root, fails, rewrite_on_bootout)
    dest, staged, log = root / "agent.plist", root / "agent.plist.staged", root / "x.log"
    dest.write_text("OLD", encoding="utf-8")
    staged.write_text("NEW", encoding="utf-8")
    helper = subprocess.Popen(
        [shell, "-c", mod.SELF_RELOAD_SCRIPT, "carr-self-reload", pg,
         str(fake), "gui/501", "com.carr.fleet-sync", str(staged), str(dest),
         str(log), wait_max, _sha("OLD")])
    return helper, dest, log, calls_log


def _calls(calls_log: Path) -> list[str]:
    return calls_log.read_text().splitlines() if calls_log.exists() else []


def _log(log: Path) -> str:
    return log.read_text() if log.exists() else ""


def handoff_script_cases() -> list[bool]:
    """Run the real detached one-shot script against a stub launchctl."""
    out: list[bool] = []
    out.append(check("the helper runs under /bin/bash and at least one shell is tested",
                     mod.SELF_RELOAD_SHELL == "/bin/bash" and "/bin/bash" in SHELLS, SHELLS))
    finished = subprocess.Popen(["/usr/bin/true"])
    finished.wait()
    dead_pid = str(finished.pid)          # a process group that has already exited
    for shell in SHELLS:
        for fails, expect_body, expect_log in (
            (0, "NEW", "loaded the new definition"),
            (1, "OLD", "restored the previous definition"),
            (2, "OLD", "RESTORE FAILED, com.carr.fleet-sync is unloaded"),
        ):
            with tempfile.TemporaryDirectory(prefix="carr-self-reload-") as tmp:
                helper, dest, log, calls_log = _start_helper(Path(tmp), dead_pid, fails, "5", shell=shell)
                rc = helper.wait(timeout=60)
                calls, text = _calls(calls_log), _log(log)
                out.append(check(
                    f"[{shell}] self-reload one-shot with {fails} failed bootstrap(s): body {expect_body}, "
                    f"'{expect_log}'",
                    dest.read_text() == expect_body and expect_log in text
                    and (rc == 0) == (fails == 0)
                    and calls[:2] == ["bootout gui/501/com.carr.fleet-sync",
                                      f"bootstrap gui/501 {dest}"]
                    and not any(c.startswith("kickstart") for c in calls)
                    and (fails < 2 or "launchctl bootstrap gui/501" in text),
                    (rc, dest.read_text(), text, calls),
                ))

        # THE WAIT. A live leader holds the one-shot back; its exit releases it.
        with tempfile.TemporaryDirectory(prefix="carr-self-reload-") as tmp:
            leader = subprocess.Popen(["/bin/sleep", "60"], start_new_session=True)
            try:
                helper, dest, log, calls_log = _start_helper(Path(tmp), str(leader.pid), shell=shell)
                time.sleep(2.5)
                held = (helper.poll() is None and _calls(calls_log) == []
                        and dest.read_text() == "OLD")
                leader.kill()
                leader.wait()
                rc = helper.wait(timeout=30)
            finally:
                if leader.poll() is None:
                    leader.kill()
            out.append(check(
                f"[{shell}] the one-shot does nothing while the job's leader lives, and reloads after it exits",
                held and rc == 0 and dest.read_text() == "NEW"
                and "loaded the new definition" in _log(log),
                (held, _calls(calls_log), _log(log)),
            ))

        # THE GROUP. The leader is gone but a member of its process group is not.
        with tempfile.TemporaryDirectory(prefix="carr-self-reload-") as tmp:
            group = subprocess.Popen(["/bin/sh", "-c", "/bin/sleep 60 & exit 0"],
                                     start_new_session=True)
            group.wait()                       # leader exited; its sleep child remains
            pgid = group.pid
            try:
                member_alive = subprocess.run(["/bin/kill", "-0", "--", f"-{pgid}"],
                                              check=False).returncode == 0
                helper, dest, log, calls_log = _start_helper(Path(tmp), str(pgid), shell=shell)
                time.sleep(2.5)
                held = (helper.poll() is None and _calls(calls_log) == []
                        and dest.read_text() == "OLD")
                os.killpg(pgid, signal.SIGKILL)
                rc = helper.wait(timeout=30)
            finally:
                try:
                    os.killpg(pgid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
            out.append(check(
                f"[{shell}] with the leader gone, a surviving group member still holds the one-shot back",
                member_alive and held and rc == 0 and dest.read_text() == "NEW",
                (member_alive, held, _calls(calls_log), _log(log)),
            ))

        # A NEWER INSTALL WINS. The plist changes while the one-shot waits.
        with tempfile.TemporaryDirectory(prefix="carr-self-reload-") as tmp:
            leader = subprocess.Popen(["/bin/sleep", "60"], start_new_session=True)
            try:
                helper, dest, log, calls_log = _start_helper(Path(tmp), str(leader.pid), shell=shell)
                time.sleep(1)
                dest.write_text("NEWER", encoding="utf-8")
                leader.kill()
                leader.wait()
                rc = helper.wait(timeout=30)
            finally:
                if leader.poll() is None:
                    leader.kill()
            out.append(check(
                f"[{shell}] a plist changed after staging is never overwritten by the older staged body",
                rc == 1 and dest.read_text() == "NEWER" and _calls(calls_log) == []
                and "changed since staging" in _log(log)
                and "loaded the new definition" not in _log(log),
                (rc, dest.read_text(), _calls(calls_log), _log(log)),
            ))

        # THE SECOND CHECK: an install that lands during bootout also wins.
        with tempfile.TemporaryDirectory(prefix="carr-self-reload-") as tmp:
            helper, dest, log, calls_log = _start_helper(
                Path(tmp), dead_pid, shell=shell, rewrite_on_bootout="NEWEST")
            rc = helper.wait(timeout=30)
            calls = _calls(calls_log)
            out.append(check(
                f"[{shell}] a plist rewritten during bootout is loaded as installed, "
                "not replaced by the staged body",
                rc == 1 and dest.read_text() == "NEWEST"
                and calls == ["bootout gui/501/com.carr.fleet-sync",
                              f"bootstrap gui/501 {dest}"]
                and "changed during bootout" in _log(log)
                and "loaded the new definition" not in _log(log),
                (rc, dest.read_text(), calls, _log(log)),
            ))
    return out


def check_and_install_cases() -> list[bool]:
    """Drive the refusal through cmd_check and cmd_install, not only the helpers."""
    out: list[bool] = []
    saved = {name: getattr(mod, name) for name in (
        "HOME", "REPO", "REPO_HERE", "SETTINGS",
        "TASKS_SRC", "TASKS_REPO", "TASKS_QUARANTINE", "LAUNCHD_SRC", "LAUNCHD_REPO",
        "LAUNCHD_ALT_REPO", "HOOKS_REPO", "CODEX_HOOKS_SRC", "CODEX_CONFIG",
        "PREREQUISITE_CHECK")}
    real_run = mod.subprocess.run
    launchctl_calls: list[list[str]] = []
    loaded_paths: dict[str, str] = {}
    disabled: set[str] = set()

    def stub_run(args, *a, **k):
        if args and os.path.basename(str(args[0])) == "launchctl":
            launchctl_calls.append(list(args))
            if args[1] == "disable":
                disabled.add(args[2].rsplit("/", 1)[-1])
            if args[1] == "print-disabled":
                return SimpleNamespace(returncode=0, stderr="", stdout="".join(
                    f'\t"{label}" => disabled\n' for label in sorted(disabled)))
            if args[1] == "print":
                label = args[2].rsplit("/", 1)[-1]
                if label in loaded_paths:
                    return SimpleNamespace(returncode=0,
                                           stdout=f"path = {loaded_paths[label]}\n", stderr="")
                return SimpleNamespace(returncode=113, stdout="",
                                       stderr=f'Could not find service "{label}" in domain for user gui: 501')
            if args[1] == "unload":
                loaded_paths.pop(Path(args[-1]).stem, None)
            if args[1] == "load":
                loaded_paths[Path(args[-1]).stem] = args[-1]
            return SimpleNamespace(returncode=0, stdout="", stderr="")
        return real_run(args, *a, **k)

    try:
        with tempfile.TemporaryDirectory(prefix="carr-cac-refusal-") as tmp:
            home = Path(tmp)
            repo = home / "carr-system"
            launchd = repo / "ops" / "launchd"
            launchd.mkdir(parents=True)
            (repo / "ops" / "scheduled-tasks").mkdir(parents=True)
            (repo / "ops" / "config").mkdir(parents=True, exist_ok=True)
            (repo / "ops" / "config" / "hooks.json").write_text(
                '{\n  "PreToolUse": []\n}\n', encoding="utf-8")
            planted = plistlib.dumps({"Label": "com.carr.planted",
                                      "ProgramArguments": ["/usr/bin/true"],
                                      "StartInterval": 300}).decode()
            good, _ = mod.launchd_calendar.rewrite_template(plistlib.dumps({
                "Label": "com.carr.good", "ProgramArguments": ["/usr/bin/true"],
                "StartInterval": 600}).decode())
            (launchd / "com.carr.planted.plist").write_text(planted, encoding="utf-8")
            (launchd / "com.carr.good.plist").write_text(good, encoding="utf-8")
            mod.HOME = str(home)
            mod.REPO = mod.REPO_HERE = str(repo)
            mod.SETTINGS = str(home / ".claude" / "settings.json")
            mod.TASKS_SRC = str(home / ".claude" / "scheduled-tasks")
            mod.TASKS_REPO = str(repo / "ops" / "scheduled-tasks")
            mod.TASKS_QUARANTINE = str(home / ".claude" / "scheduled-tasks-quarantine")
            mod.LAUNCHD_SRC = str(home / "Library" / "LaunchAgents")
            mod.LAUNCHD_REPO = str(launchd)
            mod.LAUNCHD_ALT_REPO = {}
            mod.HOOKS_REPO = str(repo / "ops" / "config" / "hooks.json")
            mod.CODEX_HOOKS_SRC = str(home / ".codex" / "hooks.json")
            mod.CODEX_CONFIG = str(home / ".codex" / "config.toml")
            mod.PREREQUISITE_CHECK = lambda _repo: []
            mod.subprocess.run = stub_run
            agents = Path(mod.LAUNCHD_SRC)
            agents.mkdir(parents=True, exist_ok=True)
            orphan = agents / "com.carr.removed-template.plist"
            orphan.write_text(plist("com.carr.removed-template"))
            with contextlib.redirect_stdout(io.StringIO()) as retired_dry:
                mod.cmd_install(False)
            out.append(check("install dry run discovers a removed launchd template",
                             "would retire  com.carr.removed-template.plist" in retired_dry.getvalue()
                             and orphan.exists(), retired_dry.getvalue()[-500:]))
            with contextlib.redirect_stdout(io.StringIO()) as install_out:
                install_rc = mod.cmd_install(True)
            agents = Path(mod.LAUNCHD_SRC)
            out.append(check(
                "cmd_install refuses the planted StartInterval template, installs the good one, "
                "and exits nonzero",
                install_rc != 0 and not (agents / "com.carr.planted.plist").exists()
                and (agents / "com.carr.good.plist").exists()
                and "REFUSED  com.carr.planted.plist" in install_out.getvalue()
                and not any(str(agents / "com.carr.planted.plist") in " ".join(c)
                            for c in launchctl_calls),
                (install_rc, install_out.getvalue()[-800:]),
            ))
            out.append(check("install retires removed templates in its apply path",
                             not orphan.exists()
                             and len(list((home / "Library/LaunchAgents-quarantine").rglob(orphan.name))) == 1))
            # Outlook jobs ship only through bin/install-outlook-*.sh, which
            # also builds and pins the helper they run; a fleet install must
            # leave even a stale Outlook plist exactly as that installer left it.
            from outlook_test_support import write_settings
            write_settings(home, home / "outlook-runtime")
            outlook = "com.carr.outlook-triage.plist"
            (launchd / outlook).write_text(plist("com.carr.outlook-triage", "{{REPO}}/new"), encoding="utf-8")
            stale = plist("com.carr.outlook-triage", str(home / "outlook-runtime/old"))
            (agents / outlook).write_text(stale, encoding="utf-8")
            launchctl_calls.clear()
            with contextlib.redirect_stdout(io.StringIO()) as outlook_out:
                mod.cmd_install(True)
            out.append(check(
                "install leaves an installer-owned Outlook plist untouched and names its installer",
                (agents / outlook).read_text(encoding="utf-8") == stale
                and f"SKIP  {outlook} (installer-owned: bin/install-outlook-triage.sh)" in outlook_out.getvalue()
                and not any(outlook in " ".join(c) for c in launchctl_calls),
                outlook_out.getvalue()[-800:]))
            (launchd / outlook).unlink()
            (agents / outlook).unlink()
            # A definition-only agent found installed is switched off AND moved
            # aside, so the convergence leaves `check` green rather than red.
            held = "com.carr.defonly.plist"
            (launchd / held).write_text(plist("com.carr.defonly"), encoding="utf-8")
            (agents / held).write_text(plist("com.carr.defonly"), encoding="utf-8")
            with patch.dict(mod.launchd_hold.DEFINITION_ONLY, {held: "fixture awaiting activation"}):
                with contextlib.redirect_stdout(io.StringIO()):
                    mod.cmd_install(False)
                dry_kept = (agents / held).exists()
                with contextlib.redirect_stdout(io.StringIO()) as held_out:
                    mod.cmd_install(True)
                left_installed = mod.definition_only_installed_plists()
            out.append(check(
                "install --apply quarantines an installed definition-only plist; dry run keeps it",
                dry_kept and not (agents / held).exists() and held not in left_installed
                and len(list((home / "Library/LaunchAgents-quarantine").rglob(held))) == 1,
                held_out.getvalue()[-800:]))
            (launchd / held).unlink()
            # A routine fleet refresh must not opt a machine into Flash.
            from tools import flash_install
            (launchd / "com.carr.planted.plist").unlink()
            for label in (*flash_install.LABELS, flash_install.WATCHER):
                filename = label + ".plist"
                (launchd / filename).write_bytes((REPO / "ops/launchd" / filename).read_bytes())
            with patch.object(flash_install, "install") as flash_setup, \
                    patch.dict(os.environ, {mod.ACTIVE_LAUNCHD_LABEL_ENV: "com.carr.good"}), \
                    contextlib.redirect_stdout(io.StringIO()):
                first_rc = mod.cmd_install(True)
                before = {p.name: (p.read_bytes(), p.stat().st_mtime_ns)
                          for p in agents.iterdir()}
                launchctl_calls.clear()
                def read_only(file, mode="r", *args, **kwargs):
                    if any(flag in mode for flag in "wax+"):
                        raise AssertionError(f"unchanged install opened {file} for writing")
                    return real_open(file, mode, *args, **kwargs)
                real_open = open
                with patch("builtins.open", side_effect=read_only), \
                        patch("io.open", side_effect=read_only):
                    second_rc = mod.cmd_install(True)
                after = {p.name: (p.read_bytes(), p.stat().st_mtime_ns)
                         for p in agents.iterdir()}
            out.append(check(
                "two fleet installs at the same revision leave plists untouched and never install Flash",
                first_rc == second_rc == 0 and not flash_setup.called
                and before == after and launchctl_calls == []
                and not any((agents / (label + ".plist")).exists()
                            for label in (*flash_install.LABELS, flash_install.WATCHER)),
                (first_rc, second_rc, flash_setup.call_count, launchctl_calls),
            ))
            for label in (*flash_install.LABELS, flash_install.WATCHER):
                (launchd / (label + ".plist")).unlink()
            (launchd / "com.carr.planted.plist").write_text(planted, encoding="utf-8")
            with contextlib.redirect_stdout(io.StringIO()) as check_out:
                check_rc = mod.cmd_check()
            text = check_out.getvalue()
            out.append(check(
                "cmd_check reports the planted template as SCHEDULE REFUSED and exits 1",
                check_rc == 1 and text.startswith("config-as-code: DRIFT")
                and "launchd template ops/launchd/com.carr.planted.plist (SCHEDULE REFUSED)" in text
                and "com.carr.good.plist (SCHEDULE REFUSED)" not in text,
                (check_rc, text[:800]),
            ))
            pending = agents / "com.carr.good.plist.pending-reload"
            pending.write_text("synthetic interrupted reload\n", encoding="utf-8")
            with contextlib.redirect_stdout(io.StringIO()) as pending_out:
                pending_rc = mod.cmd_check()
            out.append(check(
                "cmd_check reports a pending launchd reload even when plist bytes match",
                pending_rc == 1
                and "com.carr.good.plist (PENDING RELOAD)" in pending_out.getvalue(),
                (pending_rc, pending_out.getvalue()[:800]),
            ))
            pending.unlink()
            # install refuses everything while the administrator-owned policy
            # cannot be written, so its absence must show in check, not only
            # as an install failure nobody runs.
            policy = home / "managed-settings.json"
            body = json.dumps({"permissions": {"deny": ["Read(~/.ssh/id_*)"]}}, indent=2) + "\n"
            with patch.object(mod, "managed_safety_plan", return_value=[(str(policy), body)]):
                with contextlib.redirect_stdout(io.StringIO()) as absent_out:
                    absent_rc = mod.cmd_check()
                policy.write_text(json.dumps(json.loads(body)), encoding="utf-8")
                with contextlib.redirect_stdout(io.StringIO()) as present_out:
                    mod.cmd_check()
            out.append(check(
                "cmd_check reports a missing managed safety policy and accepts an equal one",
                absent_rc == 1 and f"Claude managed safety policy {policy}" in absent_out.getvalue()
                and "managed safety policy" not in present_out.getvalue()
                and len(re.findall(r"of (\d+) items", absent_out.getvalue())) == 1
                and re.findall(r"of (\d+) items", absent_out.getvalue())
                == re.findall(r"of (\d+) items", present_out.getvalue()),
                (absent_out.getvalue()[:600], present_out.getvalue()[:600])))
    finally:
        mod.subprocess.run = real_run
        for name, value in saved.items():
            setattr(mod, name, value)
    return out


def retired_launchd_cases() -> list[bool]:
    """Retired definitions reconcile against a temp HOME and fake launchctl."""
    out = []
    saved = {name: getattr(mod, name) for name in
             ("HOME", "LAUNCHD_SRC", "LAUNCHD_REPO", "LAUNCHD_ALT_REPO")}
    real_run = mod.subprocess.run
    with tempfile.TemporaryDirectory(prefix="carr-retired-launchd-") as tmp:
        home = Path(tmp)
        agents = home / "Library/LaunchAgents"
        templates = home / "repo/ops/launchd"
        agents.mkdir(parents=True)
        templates.mkdir(parents=True)
        calls = []
        loaded: dict[str, str] = {}
        mode = "normal"

        def fake_launchctl(args, **kwargs):
            calls.append(list(args))
            label = args[-1].rsplit("/", 1)[-1]
            if args[1] == "bootout":
                if mode == "bootout-failed":
                    return SimpleNamespace(returncode=5, stdout="", stderr="busy")
                if mode != "still-loaded":
                    loaded.pop(label, None)
                return SimpleNamespace(returncode=0, stdout="", stderr="")
            assert args[1] == "print", args
            if mode == "inspect-failed":
                return SimpleNamespace(returncode=1, stdout="", stderr="permission denied")
            if label in loaded:
                return SimpleNamespace(returncode=0, stdout=f"path = {loaded[label]}\n", stderr="")
            return SimpleNamespace(returncode=113, stdout="",
                                   stderr=f'Could not find service "{label}"')

        try:
            mod.HOME = str(home)
            mod.LAUNCHD_SRC = str(agents)
            mod.LAUNCHD_REPO = str(templates)
            mod.LAUNCHD_ALT_REPO = {"com.carr.alternate.plist": str(home / "alternate.plist")}
            mod.subprocess.run = fake_launchctl
            names = ("com.carr.retired", "com.carr.current", "com.carr.alternate",
                     "com.carr.declared-link", "com.vendor.personal", "local.carr-progress-board")
            for label in names:
                (agents / f"{label}.plist").write_text(plist(label))
            (templates / "com.carr.current.plist").write_text(plist("com.carr.current"))
            (home / "alternate.plist").write_text(plist("com.carr.alternate"))
            (templates / "com.carr.declared-link.plist").symlink_to(home / "missing-template.plist")
            retired = ("com.carr.retired", "com.carr.fix-train", "com.carr.cliproxyapi", "com.carr.held")
            for label in retired:
                target = agents / f"{label}.plist"
                target.write_text(plist(label))
                loaded[label] = str(target)
            hold = home / ".config/carr/launchd-hold"
            hold.parent.mkdir(parents=True)
            hold.write_text("com.carr.held deliberate machine-local hold\n")
            live = agents / "com.carr.retired.plist"
            with contextlib.redirect_stdout(io.StringIO()) as dry:
                failures = mod.reconcile_retired_launchd(False)
            out.append(check("retirement dry run lists only owned missing templates without effects",
                             failures == [] and "would retire  com.carr.retired.plist" in dry.getvalue()
                             and dry.getvalue().count("would retire") == len(retired) and not calls
                             and live.exists() and not (home / "Library/LaunchAgents-quarantine").exists(),
                             dry.getvalue()))
            with contextlib.redirect_stdout(io.StringIO()):
                failures = mod.reconcile_retired_launchd(True)
            quarantined = list((home / "Library/LaunchAgents-quarantine").rglob(live.name))
            out.append(check("removed template boots out, verifies absent, and preserves bytes in quarantine",
                             failures == [] and not live.exists() and len(quarantined) == 1
                             and quarantined[0].read_text() == plist("com.carr.retired")
                             and [c[1] for c in calls] == ["print", "bootout", "print"] * len(retired)
                             and not loaded, (failures, calls)))
            quarantine = home / "Library/LaunchAgents-quarantine/carr-retired"
            out.append(check("every undeclared CARR label retires despite former exceptions and holds",
                             all(not (agents / f"{label}.plist").exists()
                                 and len(copies := list(quarantine.rglob(f"{label}.plist"))) == 1
                                 and copies[0].read_text() == plist(label) for label in retired)))
            out.append(check("current, alternate and non-carr plists stay untouched",
                             all((agents / f"{label}.plist").read_text() == plist(label)
                                 for label in names[1:])))
            calls.clear()
            with contextlib.redirect_stdout(io.StringIO()):
                failures = mod.reconcile_retired_launchd(True)
            out.append(check("retirement rerun has no effects", failures == [] and calls == []))
            live.write_text(plist("com.carr.retired", "/usr/bin/false"))
            with contextlib.redirect_stdout(io.StringIO()):
                failures = mod.reconcile_retired_launchd(True)
            copies = list((home / "Library/LaunchAgents-quarantine").rglob(live.name))
            out.append(check("later retirement preserves both quarantined copies without overwrite",
                             failures == [] and len(copies) == 2
                             and {p.read_text() for p in copies} ==
                             {plist("com.carr.retired"), plist("com.carr.retired", "/usr/bin/false")}))
            for mode in ("inspect-failed", "bootout-failed", "still-loaded", "foreign", "self"):
                live.write_text(plist("com.carr.retired"))
                loaded["com.carr.retired"] = str(home / "foreign.plist") if mode == "foreign" else str(live)
                calls.clear()
                prior = os.environ.get(mod.ACTIVE_LAUNCHD_LABEL_ENV)
                if mode == "self":
                    os.environ[mod.ACTIVE_LAUNCHD_LABEL_ENV] = "com.carr.retired"
                try:
                    with contextlib.redirect_stdout(io.StringIO()):
                        failures = mod.reconcile_retired_launchd(True)
                finally:
                    if prior is None:
                        os.environ.pop(mod.ACTIVE_LAUNCHD_LABEL_ENV, None)
                    else:
                        os.environ[mod.ACTIVE_LAUNCHD_LABEL_ENV] = prior
                out.append(check(f"retirement fails closed for {mode}",
                                 failures == [live.name] and live.exists()
                                 and len(list((home / "Library/LaunchAgents-quarantine").rglob(live.name))) == 2,
                                 (failures, calls)))
            loaded.clear()
            mode = "normal"
            for description, body in (("non-carr internal label", plist("com.vendor.personal")),
                                      ("malformed plist", "not a plist"),
                                      ("non-dictionary plist", plistlib.dumps(["bad"]).decode())):
                live.write_text(body)
                calls.clear()
                with contextlib.redirect_stdout(io.StringIO()):
                    failures = mod.reconcile_retired_launchd(True)
                out.append(check(f"retirement rejects {description} before launchctl",
                                 failures == [live.name] and live.read_text() == body and not calls))
            live.unlink()
            live.symlink_to(agents / "com.vendor.personal.plist")
            with contextlib.redirect_stdout(io.StringIO()):
                failures = mod.reconcile_retired_launchd(True)
            out.append(check("retirement refuses symlink definitions", failures == [live.name]
                             and live.is_symlink() and not calls))
            mod.LAUNCHD_REPO = str(home / "unavailable")
            with contextlib.redirect_stdout(io.StringIO()):
                failures = mod.reconcile_retired_launchd(True)
            out.append(check("missing template directory cannot retire the entire fleet",
                             failures == [mod.LAUNCHD_REPO] and live.is_symlink() and not calls))
        finally:
            mod.subprocess.run = real_run
            for name, value in saved.items():
                setattr(mod, name, value)
    return out


def smoke_job_refusal_cases() -> list[bool]:
    """The smoke job acts only on its own throwaway label and directory."""
    out: list[bool] = []
    root = "/tmp/carr-handoff-root"
    label = "com.carr.handoff-smoke-0a1b2c3d"
    own = f"{root}/{label}"
    good = (label, f"{own}/{label}.plist", f"{own}/v2.plist", own)
    out.append(check("the smoke job accepts its own throwaway label and directory",
                     mod.smoke_job_refusal(*good, handoff_root=root) is None,
                     mod.smoke_job_refusal(*good, handoff_root=root)))
    for why, args in (
        # Paths below are self-consistent with the bad label, so ONLY the
        # label check can refuse them.
        ("a real CARR label", ("com.carr.fleet-sync",
                               f"{root}/com.carr.fleet-sync/com.carr.fleet-sync.plist",
                               f"{root}/com.carr.fleet-sync/v2.plist",
                               f"{root}/com.carr.fleet-sync")),
        ("the bare prefix", ("com.carr.handoff-smoke-",
                             f"{root}/com.carr.handoff-smoke-/com.carr.handoff-smoke-.plist",
                             f"{root}/com.carr.handoff-smoke-/v2.plist",
                             f"{root}/com.carr.handoff-smoke-")),
        ("a label with a space", ("com.carr.handoff-smoke-a b",
                                  f"{root}/com.carr.handoff-smoke-a b/com.carr.handoff-smoke-a b.plist",
                                  f"{root}/com.carr.handoff-smoke-a b/v2.plist",
                                  f"{root}/com.carr.handoff-smoke-a b")),
        ("a label with a path in it", (label + "/../x", f"{own}/{label}.plist", f"{own}/v2.plist", own)),
        ("a plist in LaunchAgents", (label, f"{os.path.expanduser('~')}/Library/LaunchAgents/{label}.plist",
                                     f"{own}/v2.plist", own)),
        ("a work dir outside the hand-off root", (label, f"{own}/{label}.plist", f"{own}/v2.plist", "/tmp")),
        ("a traversal out of its directory", (label, f"{own}/../{label}.plist", f"{own}/v2.plist", own)),
        ("a replacement body elsewhere", (label, f"{own}/{label}.plist", "/tmp/v2.plist", own)),
    ):
        out.append(check(f"the smoke job refuses {why}",
                         mod.smoke_job_refusal(*args, handoff_root=root) is not None))
    with contextlib.redirect_stdout(io.StringIO()) as refused_out:
        rc = mod.cmd_launchd_handoff_smoke_job(
            ["com.carr.fleet-sync", "/tmp/x.plist", "/tmp/v2.plist", "/tmp"])
    out.append(check("cmd_launchd_handoff_smoke_job exits 64 on a refused label, before any hand-off",
                     rc == 64 and "REFUSED" in refused_out.getvalue(), refused_out.getvalue()))
    return out


def failed_reload_retry_cases() -> list[bool]:
    """Two installer processes share disk and launchd state across a failed load."""
    with tempfile.TemporaryDirectory(prefix="carr-launchd-retry-") as tmp:
        root = Path(tmp)
        label = "com.carr.retry-proof"
        dest = root / f"{label}.plist"
        desired = root / "desired.plist"
        state_path = root / "launchd-state.json"
        dest.write_text(plist(label, "/usr/bin/false"), encoding="utf-8")
        desired.write_text(plist(label, "/usr/bin/true"), encoding="utf-8")
        state_path.write_text(json.dumps({
            "loaded": True, "program": "/usr/bin/false",
            "fail_unload_once": True, "last_unload_failed": False,
        }), encoding="utf-8")
        fake = root / "launchctl"
        fake.write_text("""#!/usr/bin/env python3
import json, os, plistlib, sys
from pathlib import Path
state_path = Path(os.environ['CARR_FAKE_LAUNCHD_STATE'])
state = json.loads(state_path.read_text())
args = sys.argv[1:]
label = os.environ['CARR_FAKE_LAUNCHD_LABEL']
dest = os.environ['CARR_FAKE_LAUNCHD_DEST']
if args[0] == 'print':
    if not state['loaded']:
        print(f'Could not find service "{label}" in domain for user gui: 501', file=sys.stderr)
        sys.exit(113)
    print(f'path = {dest}\\nprogram = {state["program"]}')
    sys.exit(0)
if args[0] == 'unload':
    if state['fail_unload_once']:
        state['fail_unload_once'] = False
        state['last_unload_failed'] = True
        state_path.write_text(json.dumps(state))
        sys.exit(5)
    state['loaded'] = False
    state['last_unload_failed'] = False
elif args[0] == 'load':
    if state['last_unload_failed']:
        state['last_unload_failed'] = False
        state_path.write_text(json.dumps(state))
        sys.exit(5)
    if state.get('suppress_register_once'):
        state['suppress_register_once'] = False
        state_path.write_text(json.dumps(state))
        sys.exit(0)
    state['loaded'] = True
    state['program'] = plistlib.loads(Path(dest).read_bytes())['ProgramArguments'][0]
else:
    sys.exit(64)
state_path.write_text(json.dumps(state))
""", encoding="utf-8")
        fake.chmod(fake.stat().st_mode | stat.S_IXUSR)
        install = """import importlib.util, pathlib, sys
spec = importlib.util.spec_from_file_location('config_as_code_retry', sys.argv[1])
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)
dest = pathlib.Path(sys.argv[2])
body = pathlib.Path(sys.argv[3]).read_text()
print('OUTCOME:', mod.install_launchd_plist(dest.name, str(dest), body,
                                          dest.read_text() == body))
"""
        env = dict(os.environ, PATH=f"{root}{os.pathsep}{os.environ['PATH']}",
                   CARR_FAKE_LAUNCHD_STATE=str(state_path),
                   CARR_FAKE_LAUNCHD_LABEL=label,
                   CARR_FAKE_LAUNCHD_DEST=str(dest),
                   PYTHONDONTWRITEBYTECODE="1")
        argv = [sys.executable, "-c", install, str(REPO / "ops" / "config-as-code.py"),
                str(dest), str(desired)]
        first = subprocess.run(argv, cwd=REPO, env=env, capture_output=True, text=True)
        after_first = json.loads(state_path.read_text())
        first_ok = (first.returncode == 0 and "OUTCOME: failed" in first.stdout
                    and after_first["loaded"] and after_first["program"] == "/usr/bin/false"
                    and dest.read_text() == desired.read_text()
                    and (root / f"{label}.plist.pending-reload").exists())
        second = subprocess.run(argv, cwd=REPO, env=env, capture_output=True, text=True)
        after_second = json.loads(state_path.read_text())
        second_ok = (second.returncode == 0 and "OUTCOME: loaded" in second.stdout
                     and "OUTCOME: kept" not in second.stdout
                     and after_second["loaded"]
                     and after_second["program"] == "/usr/bin/true"
                     and not (root / f"{label}.plist.pending-reload").exists())
        dest.write_text(plist(label, "/usr/bin/false"), encoding="utf-8")
        state_path.write_text(json.dumps({
            "loaded": True, "program": "/usr/bin/false",
            "fail_unload_once": False, "last_unload_failed": False,
            "suppress_register_once": True,
        }), encoding="utf-8")
        false_success = subprocess.run(argv, cwd=REPO, env=env,
                                       capture_output=True, text=True)
        after_false_success = json.loads(state_path.read_text())
        unverified_ok = (false_success.returncode == 0
                         and "OUTCOME: failed" in false_success.stdout
                         and not after_false_success["loaded"]
                         and (root / f"{label}.plist.pending-reload").exists())
        verified_retry = subprocess.run(argv, cwd=REPO, env=env,
                                        capture_output=True, text=True)
        after_verified_retry = json.loads(state_path.read_text())
        verified_ok = (verified_retry.returncode == 0
                       and "OUTCOME: loaded" in verified_retry.stdout
                       and after_verified_retry["loaded"]
                       and after_verified_retry["program"] == "/usr/bin/true"
                       and not (root / f"{label}.plist.pending-reload").exists())
        return [
            check("failed changed-plist reload leaves old loaded definition and new disk bytes",
                  first_ok, (first.stdout, first.stderr, after_first)),
            check("fresh installer process retries the pending reload instead of keeping old definition",
                  second_ok, (second.stdout, second.stderr, after_second)),
            check("load success without launchd registration retains pending state",
                  unverified_ok, (false_success.stdout, false_success.stderr,
                                  after_false_success)),
            check("fresh installer process clears pending state only after verified registration",
                  verified_ok, (verified_retry.stdout, verified_retry.stderr,
                                after_verified_retry)),
        ]


def outlook_render_cases() -> list[bool]:
    from lib.outlook_settings import SettingsError, load_settings
    from outlook_test_support import write_settings
    outcomes: list[bool] = []
    with tempfile.TemporaryDirectory(prefix="outlook-config-render-") as temp:
        root = Path(temp).resolve()
        home, repo = root / "home & local", root / "repo & local"
        config = write_settings(home, repo, copy_helper=str(home / "custom & bin/granted-copy"),
                                executable_path=f"{home}/custom & bin:/usr/bin:/bin")
        settings = load_settings(home)
        with patch.object(mod, "HOME", str(home)), patch.object(mod, "REPO", str(repo)), \
                patch.object(mod, "TOKENS", [("{{REPO}}", str(repo)), ("{{HOME}}", str(home))]), \
                patch("pathlib.Path.home", return_value=home):
            for name in ("intake", "refresh", "triage"):
                template = (REPO / f"ops/launchd/com.carr.outlook-{name}.plist").read_text()
                rendered = mod.concrete(template)
                definition = plistlib.loads(rendered.encode())
                outcomes.append(check(
                    f"generic renderer binds configured Outlook {name} paths",
                    definition["WorkingDirectory"] == str(repo)
                    and definition["EnvironmentVariables"]["PATH"] == settings.executable_path
                    and (name != "intake" or definition["ProgramArguments"][0] == str(settings.copy_helper)),
                    definition))
                captured = mod.portable(rendered)
                outcomes.append(check(
                    f"generic capture keeps Outlook {name} machine settings portable",
                    str(home) not in captured and str(repo) not in captured
                    and "{{EXECUTABLE_PATH}}" in captured
                    and (name != "intake" or "{{COPY_HELPER}}" in captured)
                    and plistlib.loads(mod.concrete(captured).encode()) == definition
                    and mod.launchd_texts_match(rendered, template),
                    captured))
            config.unlink()
            try:
                mod.concrete(template)
            except SettingsError as error:
                outcomes.append(check("generic Outlook render refuses missing settings with file diagnostic",
                                      str(config) in str(error) and "file" in str(error), error))
            else:
                outcomes.append(check("generic Outlook render refuses missing settings with file diagnostic", False))
            outcomes.append(check("generic non-Outlook render needs no Outlook settings",
                                  mod.concrete(plist("com.carr.synthetic")) == plist("com.carr.synthetic")))
        # The Studio arrangement: the checker runs from the canonical checkout
        # while bin/install-outlook-intake.sh runs the jobs from the separate
        # checkout named in settings.repo.
        canonical, runtime = root / "canonical", root / "outlook-runtime"
        write_settings(home, runtime)
        settings = load_settings(home)
        with patch.object(mod, "HOME", str(home)), patch.object(mod, "REPO", str(canonical)), \
                patch.object(mod, "TOKENS", [("{{REPO}}", str(canonical)), ("{{HOME}}", str(home))]), \
                patch("pathlib.Path.home", return_value=home):
            for name in ("intake", "refresh", "triage"):
                template = (REPO / f"ops/launchd/com.carr.outlook-{name}.plist").read_text()
                installed = settings.render_plist(template.encode()).decode()
                try:
                    matches = mod.launchd_texts_match(installed, template)
                except SettingsError as error:
                    matches = error
                outcomes.append(check(
                    f"installer-rendered Outlook {name} plist matches when its runtime checkout is separate",
                    matches is True, matches))
            # The research-copy installer writes with plistlib (sorted keys);
            # the tracked template is hand-formatted. Same job, so no drift.
            source = REPO / "spec/outlook/com.carr.outlook-research-copy.plist"
            definition = {key: value.replace("{{HOME}}", str(home)) if isinstance(value, str)
                          else [item.replace("{{HOME}}", str(home)) for item in value] if key == "ProgramArguments"
                          else value
                          for key, value in plistlib.loads(source.read_bytes()).items()}
            installed = plistlib.dumps(definition).decode()
            outcomes.append(check("plistlib re-serialized research-copy plist matches its hand-formatted template",
                                  mod.launchd_texts_match(installed, source.read_text()) is True))
            changed = plistlib.dumps({**definition, "Umask": 18}).decode()
            outcomes.append(check("a changed research-copy value is still drift",
                                  mod.launchd_texts_match(changed, source.read_text()) is False))
    return outcomes


def main() -> int:
    original_run = mod.subprocess.run
    original_active = os.environ.get(mod.ACTIVE_LAUNCHD_LABEL_ENV)
    calls: list[list[str]] = []
    loaded_paths: dict[str, str] = {}
    print_error = ""

    def fake_run(args, *unused_args, **unused_kwargs):
        calls.append(list(args))
        if args[1] == "unload":
            loaded_paths.pop(Path(args[-1]).stem, None)
        if args[1] == "load":
            loaded_paths[Path(args[-1]).stem] = args[-1]
        if args[1] == "print":
            if print_error:
                return SimpleNamespace(returncode=1, stdout="", stderr=print_error)
            label = args[2].rsplit("/", 1)[-1]
            if label not in loaded_paths:
                return SimpleNamespace(
                    returncode=113, stdout="",
                    stderr=f'Could not find service "{label}" in domain for user gui: 501',
                )
            return SimpleNamespace(returncode=0,
                                   stdout=f"path = {loaded_paths[label]}\n", stderr="")
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    cases: list[bool] = []
    try:
        mod.subprocess.run = fake_run
        with tempfile.TemporaryDirectory(prefix="carr-active-launchd-") as tmp:
            root = Path(tmp)
            fleet_label = "com.carr.fleet-sync"
            other_label = "com.carr.other"
            fleet_dest = root / f"{fleet_label}.plist"
            other_dest = root / f"{other_label}.plist"
            old_fleet = plist(fleet_label, "/usr/bin/false")
            desired_fleet = plist(fleet_label)
            desired_other = plist(other_label)

            fleet_dest.write_text(desired_fleet, encoding="utf-8")
            os.environ[mod.ACTIVE_LAUNCHD_LABEL_ENV] = fleet_label
            calls.clear()
            with contextlib.redirect_stdout(io.StringIO()) as unchanged_out:
                unchanged = mod.install_launchd_plist(
                    fleet_dest.name, str(fleet_dest), desired_fleet, True
                )
            cases.append(check(
                "unchanged active fleet job stays loaded",
                unchanged == "kept" and calls == []
                and fleet_dest.read_text(encoding="utf-8") == desired_fleet,
                (unchanged, calls, unchanged_out.getvalue()),
            ))

            fleet_dest.write_text(old_fleet, encoding="utf-8")
            calls.clear()
            spawned: list[tuple[list[str], dict]] = []
            original_popen = mod.subprocess.Popen
            original_handoff = mod.SELF_RELOAD_HANDOFF_DIR
            mod.SELF_RELOAD_HANDOFF_DIR = str(root / "handoff")
            mod.subprocess.Popen = lambda args, **kw: spawned.append((list(args), kw))
            try:
                with contextlib.redirect_stdout(io.StringIO()) as changed_out:
                    changed = mod.install_launchd_plist(
                        fleet_dest.name, str(fleet_dest), desired_fleet, False
                    )
            finally:
                mod.subprocess.Popen = original_popen
            staged = root / "handoff" / f"{fleet_label}.plist.staged"
            argv = spawned[0][0] if spawned else []
            cases.append(check(
                "changed active fleet plist is left untouched and its reload handed "
                "to a detached one-shot (no hourly exit 1)",
                changed == "deferred" and calls == []
                and fleet_dest.read_text(encoding="utf-8") == old_fleet
                and staged.read_text(encoding="utf-8") == desired_fleet
                and len(spawned) == 1 and spawned[0][1].get("start_new_session") is True
                and argv[0] == "/bin/bash" and argv[5] == "/bin/launchctl"
                and argv[4] == str(os.getpgrp()) and argv[7] == fleet_label
                and argv[8] == str(staged) and argv[9] == str(fleet_dest)
                and argv[12] == _sha(old_fleet)
                and argv[13] == sys.executable
                and argv[14] == str(REPO / "lib/launchd_hold.py")
                and argv[15] == mod.launchd_repo_path(fleet_dest.name)
                and "self-reload deferred" in changed_out.getvalue(),
                (changed, calls, spawned, changed_out.getvalue()),
            ))

            def broken_popen(*_a, **_k):
                raise OSError("synthetic spawn failure")
            mod.subprocess.Popen = broken_popen
            try:
                with contextlib.redirect_stdout(io.StringIO()) as broken_out:
                    broken = mod.install_launchd_plist(
                        fleet_dest.name, str(fleet_dest), desired_fleet, False
                    )
            finally:
                mod.subprocess.Popen = original_popen
                mod.SELF_RELOAD_HANDOFF_DIR = original_handoff
            cases.append(check(
                "a hand-off that cannot start fails closed with the external remedy",
                broken == "failed" and calls == []
                and fleet_dest.read_text(encoding="utf-8") == old_fleet
                and "config-as-code.py install --apply" in broken_out.getvalue(),
                (broken, broken_out.getvalue()),
            ))

            other_dest.write_text(desired_other, encoding="utf-8")
            loaded_paths[other_label] = str(other_dest)
            calls.clear()
            with contextlib.redirect_stdout(io.StringIO()) as kept_out:
                other = mod.install_launchd_plist(
                    other_dest.name, str(other_dest), desired_other, True
                )
            cases.append(check(
                "unchanged loaded other job remains loaded without a reload",
                other == "kept" and calls == [
                    ["launchctl", "print", f"gui/{os.getuid()}/{other_label}"]
                ] and other_dest.read_text(encoding="utf-8") == desired_other
                and "kept loaded" in kept_out.getvalue(),
                (other, calls, kept_out.getvalue()),
            ))

            before = other_dest.stat().st_mtime_ns
            calls.clear()
            with contextlib.redirect_stdout(io.StringIO()), \
                    patch("builtins.open", wraps=open) as writes:
                repaired = mod.install_launchd_plist(
                    other_dest.name, str(other_dest), desired_other, False
                )
            cases.append(check(
                "repairing stale loaded arguments never rewrites identical plist bytes",
                repaired == "loaded" and other_dest.stat().st_mtime_ns == before
                and not any(call.args[0] == str(other_dest) and "w" in str(call.args[1:])
                            for call in writes.call_args_list)
                and ["launchctl", "load", "-w", str(other_dest)] in calls,
                (repaired, calls),
            ))

            loaded_paths[other_label] = str(root / "foreign.plist")
            calls.clear()
            with contextlib.redirect_stdout(io.StringIO()) as foreign_out:
                foreign = mod.install_launchd_plist(
                    other_dest.name, str(other_dest), desired_other, True
                )
            cases.append(check(
                "same label loaded from a foreign path is not silently accepted",
                foreign == "failed" and calls == [
                    ["launchctl", "print", f"gui/{os.getuid()}/{other_label}"]
                ] and other_dest.read_text(encoding="utf-8") == desired_other
                and "INSPECT FAILED" in foreign_out.getvalue(),
                (foreign, calls, foreign_out.getvalue()),
            ))

            pending = Path(str(other_dest) + ".pending-reload")
            other_dest.write_text(plist(other_label, "/usr/bin/false"), encoding="utf-8")
            calls.clear()
            with contextlib.redirect_stdout(io.StringIO()):
                changed_foreign = mod.install_launchd_plist(
                    other_dest.name, str(other_dest), desired_other, False
                )
            cases.append(check(
                "changed plist refuses foreign registration before touching disk",
                changed_foreign == "failed" and calls == [
                    ["launchctl", "print", f"gui/{os.getuid()}/{other_label}"]
                ] and other_dest.read_text(encoding="utf-8")
                == plist(other_label, "/usr/bin/false") and not pending.exists(),
                (changed_foreign, calls),
            ))

            other_dest.write_text(desired_other, encoding="utf-8")
            pending.write_text("synthetic pending reload\n", encoding="utf-8")
            calls.clear()
            with contextlib.redirect_stdout(io.StringIO()):
                pending_foreign = mod.install_launchd_plist(
                    other_dest.name, str(other_dest), desired_other, True
                )
            cases.append(check(
                "pending retry refuses foreign registration before touching marker or job",
                pending_foreign == "failed" and calls == [
                    ["launchctl", "print", f"gui/{os.getuid()}/{other_label}"]
                ] and other_dest.read_text(encoding="utf-8") == desired_other
                and pending.read_text(encoding="utf-8") == "synthetic pending reload\n",
                (pending_foreign, calls),
            ))

            pending.write_text("synthetic pending reload\n", encoding="utf-8")
            print_error = "Operation not permitted"
            calls.clear()
            with contextlib.redirect_stdout(io.StringIO()):
                pending_ambiguous = mod.install_launchd_plist(
                    other_dest.name, str(other_dest), desired_other, True
                )
            cases.append(check(
                "pending retry refuses ambiguous inspection before touching marker or job",
                pending_ambiguous == "failed" and calls == [
                    ["launchctl", "print", f"gui/{os.getuid()}/{other_label}"]
                ] and other_dest.read_text(encoding="utf-8") == desired_other
                and pending.read_text(encoding="utf-8") == "synthetic pending reload\n",
                (pending_ambiguous, calls),
            ))
            print_error = ""
            pending.unlink()

            loaded_paths.pop(other_label, None)
            calls.clear()
            with contextlib.redirect_stdout(io.StringIO()):
                absent = mod.install_launchd_plist(
                    other_dest.name, str(other_dest), desired_other, True
                )
            cases.append(check(
                "unchanged absent job is loaded",
                absent == "loaded" and [call[:2] for call in calls]
                == [["launchctl", "print"], ["launchctl", "unload"],
                    ["launchctl", "print"], ["launchctl", "load"],
                    ["launchctl", "print"]]
                and other_dest.read_text(encoding="utf-8") == desired_other,
                (absent, calls),
            ))

            print_error = "Operation not permitted"
            calls.clear()
            with contextlib.redirect_stdout(io.StringIO()) as error_out:
                ambiguous = mod.install_launchd_plist(
                    other_dest.name, str(other_dest), desired_other, True
                )
            cases.append(check(
                "ambiguous launchctl inspection refuses without altering the job",
                ambiguous == "failed" and calls == [
                    ["launchctl", "print", f"gui/{os.getuid()}/{other_label}"]
                ] and other_dest.read_text(encoding="utf-8") == desired_other
                and "INSPECT FAILED" in error_out.getvalue(),
                (ambiguous, calls, error_out.getvalue()),
            ))
            print_error = ""

            os.environ.pop(mod.ACTIVE_LAUNCHD_LABEL_ENV, None)
            calls.clear()
            with contextlib.redirect_stdout(io.StringIO()):
                external = mod.install_launchd_plist(
                    fleet_dest.name, str(fleet_dest), desired_fleet, False
                )
            cases.append(check(
                "external install renders and reloads the changed fleet plist",
                external == "loaded"
                and [call[:2] for call in calls]
                == [["launchctl", "print"], ["launchctl", "unload"],
                    ["launchctl", "print"],
                    ["launchctl", "load"], ["launchctl", "print"]]
                and fleet_dest.read_text(encoding="utf-8") == desired_fleet,
                (external, calls),
            ))

            # A Mac demoted to secondary: its primary-only job is unloaded and
            # moved to quarantine, never deleted, and a second retire refuses
            # to overwrite the first quarantined copy.
            original_quarantine = getattr(mod, "LAUNCHD_QUARANTINE")
            setattr(mod, "LAUNCHD_QUARANTINE", str(root / "quarantine"))
            try:
                name = "com.carr.nightly-record-layer.plist"
                live = root / name
                live.write_text(plist("com.carr.nightly-record-layer"), encoding="utf-8")
                calls.clear()
                with contextlib.redirect_stdout(io.StringIO()):
                    planned = mod.retire_primary_only_plist(name, str(live), False)
                cases.append(check(
                    "dry-run retire touches nothing",
                    planned == "planned" and calls == [] and live.exists(),
                    (planned, calls),
                ))
                with contextlib.redirect_stdout(io.StringIO()):
                    retired = mod.retire_primary_only_plist(name, str(live), True)
                moved = root / "quarantine" / name
                cases.append(check(
                    "secondary retires a primary-only job: unloaded and moved aside",
                    retired == "retired" and not live.exists() and moved.exists()
                    and [call[:2] for call in calls] == [["launchctl", "unload"]],
                    (retired, calls),
                ))
                live.write_text(plist("com.carr.nightly-record-layer"), encoding="utf-8")
                with contextlib.redirect_stdout(io.StringIO()):
                    again = mod.retire_primary_only_plist(name, str(live), True)
                cases.append(check(
                    "retire refuses to overwrite an earlier quarantined copy",
                    again == "failed" and live.exists(),
                    again,
                ))
            finally:
                setattr(mod, "LAUNCHD_QUARANTINE", original_quarantine)
    finally:
        mod.subprocess.run = original_run
        if original_active is None:
            os.environ.pop(mod.ACTIVE_LAUNCHD_LABEL_ENV, None)
        else:
            os.environ[mod.ACTIVE_LAUNCHD_LABEL_ENV] = original_active

    # STARTINTERVAL IS REFUSED (macOS 27 never fires it). A planted template in
    # a throwaway repo is reported by check and refused by install's gate, a
    # converted one is not, and the real tree carries no refusal at all.
    with tempfile.TemporaryDirectory(prefix="carr-launchd-refuse-") as tmp:
        repo = Path(tmp)
        (repo / "ops" / "launchd").mkdir(parents=True)
        planted = plistlib.dumps({
            "Label": "com.carr.planted",
            "ProgramArguments": ["/usr/bin/true"],
            "StartInterval": 300,
            "RunAtLoad": True,
        }).decode("utf-8")
        (repo / "ops" / "launchd" / "com.carr.planted.plist").write_text(
            planted, encoding="utf-8")
        found = mod.refused_launchd_templates(str(repo))
        cases.append(check(
            "check reports a planted StartInterval template as refused",
            len(found) == 1 and found[0][0] == "ops/launchd/com.carr.planted.plist"
            and "StartInterval" in found[0][1],
            found,
        ))
        cases.append(check(
            "install refuses to render a planted StartInterval template",
            bool(mod.launchd_template_refusal(planted)),
        ))
        converted, _ = mod.launchd_calendar.rewrite_template(planted)
        (repo / "ops" / "launchd" / "com.carr.planted.plist").write_text(
            converted, encoding="utf-8")
        cases.append(check(
            "the converted template is accepted by both",
            mod.refused_launchd_templates(str(repo)) == []
            and mod.launchd_template_refusal(converted) is None
            and plistlib.loads(converted.encode())["RunAtLoad"] is True,
            mod.refused_launchd_templates(str(repo)),
        ))
    cases.extend(outlook_render_cases())
    cases.extend(retired_launchd_cases())
    cases.extend(smoke_job_refusal_cases())
    cases.extend(failed_reload_retry_cases())
    cases.extend(handoff_script_cases())
    cases.extend(check_and_install_cases())
    cases.append(check(
        "no tracked CARR template is refused",
        mod.refused_launchd_templates(str(REPO)) == [],
        mod.refused_launchd_templates(str(REPO)),
    ))

    print(f"config-as-code-launchd-selftest: {sum(cases)}/{len(cases)} passed")
    return 0 if all(cases) else 1


if __name__ == "__main__":
    raise SystemExit(main())
