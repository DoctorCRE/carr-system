"""Read-only scheduled-job inventory and actionable drift reports."""
from __future__ import annotations

import os
import fcntl
import tempfile
import re
import hashlib
import json
import plistlib
import shlex
import subprocess
import sys
import time
from pathlib import Path

SOURCE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SOURCE / "ops"))
from git_env import scrubbed_env
import launchd_scope
import machine_role
import launchd_hold

SCHEDULE_KEYS = ("StartInterval", "StartCalendarInterval", "KeepAlive", "RunAtLoad",
                 "WatchPaths", "QueueDirectories", "ThrottleInterval")
# Environment fields whose value IS the checkout a job runs against.
CHECKOUT_ENV = ("CARR_REPO", "CARR_REPO_ROOT")
# Every bin/run-scheduled.sh wrapper appends to this one log, so its age says
# nothing about any single job.
SHARED_WRAPPER_LOG = "out/run-scheduled.log"
WEEK_MINUTES = 7 * 24 * 60
# A package manager's own repository is git's answer for every binary it
# installs (Homebrew's cellar links resolve to /opt/homebrew); it is never the
# checkout a job's code lives in.
PACKAGE_MANAGER_ROOTS = ("/opt/homebrew", "/usr/local/Homebrew", "/usr/local")


def schedule(plist):
    return {k: plist[k] for k in SCHEDULE_KEYS if k in plist}


def listed(text):
    result = {}
    for line in text.splitlines():
        fields = line.split()
        if len(fields) == 3 and re.fullmatch(r"-?\d+", fields[1]):
            result[fields[2]] = {"pid": fields[0], "exit": int(fields[1])}
    return result


def disabled(text):
    return {label: value in ("true", "disabled") for label, value in
            re.findall(r'"([^"\n]+)"\s*=>\s*(true|false|disabled|enabled)', text)}


def cron_label(command):
    return "cron." + hashlib.sha256(command.encode()).hexdigest()[:16]


def cron_entries(text):
    """Every crontab firing, in order; one command may legitimately appear once only."""
    result = []
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#") or re.match(r"^[A-Za-z_][\w]*\s*=", line):
            continue
        parts = line.split(None, 1 if line.startswith("@") else 5)
        if len(parts) not in (2, 6):
            raise ValueError("unrecognized crontab entry")
        command = parts[-1]
        result.append({"label": cron_label(command), "command": command, "interval": " ".join(parts[:-1])})
    return result


def cron_words(command):
    try:
        return shlex.split(command)
    except ValueError:
        return command.split()


def cron_directory(command):
    """The directory a cron command's last `cd` selects; None means cron's default home."""
    words = cron_words(command)
    targets = [words[i + 1] for i, w in enumerate(words[:-1])
               if w == "cd" and (i == 0 or words[i - 1] in ("&&", ";", "||"))]
    return targets[-1].rstrip(";") if targets else None


def expand(value):
    if isinstance(value, dict):
        return {k: expand(v) for k, v in value.items()}
    if isinstance(value, list):
        return [expand(v) for v in value]
    if not isinstance(value, str):
        return value
    return os.path.expanduser(value)


def calendar_entries(value):
    entries = [value] if isinstance(value, dict) else list(value or [])
    return sorted(entries, key=lambda e: sorted(e.items()))


def cadence(interval):
    """The part of a schedule launchd reports back for a loaded job."""
    view = {}
    if "StartInterval" in interval:
        view["StartInterval"] = interval["StartInterval"]
    if "StartCalendarInterval" in interval:
        view["StartCalendarInterval"] = calendar_entries(interval["StartCalendarInterval"])
    if interval.get("WatchPaths"):
        view["WatchPaths"] = sorted(interval["WatchPaths"])
    return view


def loaded_cadence(text):
    view = {}
    interval = re.search(r"(?m)^\s*run interval = (\d+) seconds\s*$", text)
    if interval:
        view["StartInterval"] = int(interval[1])
    calendar, watched = [], []
    for stream, body in re.findall(r"(?ms)^\s*stream = (\S+)\s*$.*?^\s*descriptor = \{\s*$(.*?)^\s*\}\s*$", text):
        if stream.startswith("com.apple.launchd.calendarinterval"):
            calendar.append({k: int(v) for k, v in re.findall(r'"(\w+)" => (-?\d+)', body)})
        elif stream == "com.apple.fsevents.matching":
            watched.extend(re.findall(r'(?m)^\s*\d+ = "(.*)"\s*$', body))
    if calendar:
        view["StartCalendarInterval"] = calendar_entries(calendar)
    if watched:
        view["WatchPaths"] = sorted(watched)
    return view


def loaded_arguments(text):
    block = re.search(r"(?ms)^\s*arguments = \{\s*$(.*?)^\s*\}\s*$", text)
    return [line.strip() for line in block[1].splitlines() if line.strip()] if block else None


def installed_arguments(plist):
    return plist.get("ProgramArguments") or ([plist["Program"]] if plist.get("Program") else [])


def checkout_environment(plist, runtime):
    """Installed and loaded values of the environment fields that name a checkout."""
    env = plist.get("EnvironmentVariables") or {}
    return ([env[k] for k in CHECKOUT_ENV if env.get(k)] +
            re.findall(r"(?m)^\s*(?:%s)\s*=>\s*(.+?)\s*$" % "|".join(CHECKOUT_ENV), runtime))


def working_directories(plist, runtime):
    return ([plist["WorkingDirectory"]] if plist.get("WorkingDirectory") else []) + \
        re.findall(r"(?m)^\s*working directory\s*=\s*(.+?)\s*$", runtime)


def running(label, live, runtime):
    pid = live.get(label, {}).get("pid", "-")
    return pid.isdigit() or bool(re.search(r"(?m)^\s*state = running\s*$", runtime))


def expectation(job, role):
    """(enabled, installed) on a machine of this role; placement is launchd_scope's alone."""
    if job["scheduler"] == "launchd" and not launchd_scope.allowed_on_machine(job["label"] + ".plist", role == "primary"):
        return False, False
    return job["expected_enabled"], job.get("expected_installed", True)


def activity_path(job):
    return expand(job.get("activity_path") or job["log_path"])


def longest_gap_seconds(interval):
    """Longest wait between two firings of a periodic schedule; None if it never recurs."""
    if "StartInterval" in interval:
        return interval["StartInterval"]
    if "StartCalendarInterval" not in interval:
        return None
    entries = calendar_entries(interval["StartCalendarInterval"])
    if any(set(e) - {"Minute", "Hour", "Weekday"} for e in entries):
        raise ValueError("calendar fields beyond Minute/Hour/Weekday are not modelled")
    firings = sorted({day * 1440 + hour * 60 + minute for e in entries
                      for day in ([e["Weekday"] % 7] if "Weekday" in e else range(7))
                      for hour in ([e["Hour"]] if "Hour" in e else range(24))
                      for minute in ([e["Minute"]] if "Minute" in e else range(60))})
    gaps = [b - a for a, b in zip(firings, firings[1:])] + [firings[0] + WEEK_MINUTES - firings[-1]]
    return max(gaps) * 60


def inside(path, required):
    return path == required or path.startswith(required + "/")


def checkout_drift(paths, required, roots):
    for path in paths:
        checkout = roots.get(path)
        if checkout and os.path.realpath(checkout) != os.path.realpath(required):
            return True
        if "/carr-system" in path and not inside(path, required):
            return True
    return False


def is_checkout(path, required, roots):
    """A field whose whole value is a checkout must name exactly the required one."""
    return os.path.realpath(roots.get(path, path)) == os.path.realpath(required)


def render(rows):
    return [f"WARN scheduled jobs {r['label']} {r['code']}: {r['detail']} · on breach: "
            f"tools/job-watchdog.py scan files/updates loop {r['key']} · owner {r['owner']} · "
            f"fix: {r['fix']} · verify: python3 ops/scheduled-jobs-check.py · "
            f"auto-clear: next complete scan without {r['code']} for {r['label']}" for r in rows]


def report(manifest, snapshot, now):
    rows = []
    def emit(label, code, detail, fix, owner="orchestrator"):
        rows.append({"label": label, "code": code, "key": f"scheduled_jobs:{label}:{code}",
                     "owner": owner, "detail": detail, "fix": fix})
    registry = {j["label"]: j for j in manifest["jobs"]}
    live = listed(snapshot["launchctl_list"])
    overrides = disabled(snapshot["launchctl_disabled"])
    prefixes = tuple(manifest.get("scope", {}).get("launchd_label_prefixes", ["com.carr.", "local.carr-"]))
    cron = {}
    for entry in cron_entries(snapshot["cron"]):
        cron.setdefault(entry["label"], []).append(entry)
    roots = snapshot.get("checkout_roots", {})
    role = snapshot["machine_role"]
    # launchd keeps an override entry forever after a job is gone; alone it is not an installed job.
    observed = set(snapshot["plists"]) | {k for k in live if k.startswith(prefixes)} | cron.keys()
    for label in sorted(observed - registry.keys()):
        emit(label, "unknown_job", "job has no declared expectation", "review the job and declare its purpose/state or retire it through its owner")
    for error in snapshot.get("errors", []):
        emit(error, "evidence_unavailable", "live evidence could not be read", "restore read access to the named evidence source and rerun the checker")
    for job in manifest["jobs"]:
        label = job["label"]
        owner = job["owner"]
        required = expand(job["required_checkout"])
        enabled, expected_installed = expectation(job, role)
        plist = snapshot["plists"].get(label)
        is_cron = job["scheduler"] == "cron"
        present = label in cron if is_cron else plist is not None
        if expected_installed and not present:
            emit(label, "missing_job", "declared job definition is absent", "restore the declared job definition from its repository source and verify registration", owner)
        if is_cron:
            actual = cron.get(label, [])
            if actual and not enabled:
                emit(label, "unexpected_enabled", "cron entry is active despite disabled expectation", "retire or pause the entry through its owner", owner)
            if len(actual) > 1:
                emit(label, "duplicate_entry", f"crontab fires this command from {len(actual)} entries", "keep exactly the declared cron entry", owner)
            if any(e["interval"] != job["interval"] for e in actual):
                emit(label, "interval_drift", "cron cadence differs from manifest", "restore the declared cron cadence", owner)
            for entry in actual:
                words = cron_words(entry["command"])
                directory = cron_directory(entry["command"])
                if (expand(job["program_path"]) not in words or
                        checkout_drift([w for w in words if w.startswith("/")], required, roots) or
                        (directory and not (inside(directory, required) or is_checkout(directory, required, roots)))):
                    emit(label, "wrong_checkout", "cron command does not run the declared program in the declared checkout", "restore the declared program and checkout in crontab", owner)
                    break
        else:
            runtime = snapshot["launchctl_print"].get(label, "")
            is_disabled = overrides.get(label, bool((plist or {}).get("Disabled", False)))
            if enabled and is_disabled:
                emit(label, "disabled_but_expected", "launchd disabled override or plist Disabled is set", "restore expected enabled state through the agent owner and verify launchctl print-disabled", owner)
            if enabled and plist and label not in live:
                emit(label, "missing_registration", "plist exists but launchd has no registered job", "bootstrap the declared agent and verify launchctl print", owner)
            if not enabled and label in live and running(label, live, runtime):
                emit(label, "unexpected_running", "job is running despite disabled expectation; a disable override does not stop it", "stop the running job through its owner (launchctl bootout) and verify launchctl list", owner)
            elif not enabled and label in live and (not is_disabled or not expected_installed):
                emit(label, "unexpected_enabled", "job is registered despite disabled expectation", "retire or pause the agent through its owner", owner)
            declared = expand(job["interval"])
            if plist and schedule(plist) != declared:
                emit(label, "interval_drift", "installed cadence differs from manifest", "restore declared cadence and reload the agent", owner)
            if runtime and loaded_cadence(runtime) != cadence(declared):
                emit(label, "loaded_interval_drift", "launchd is firing on a cadence other than the manifest's", "reload the agent from its declared definition and verify launchctl print", owner)
            if plist and job.get("log_paths") and [plist.get(k) for k in ("StandardOutPath", "StandardErrorPath")] != [expand(p) if p else None for p in job["log_paths"]]:
                emit(label, "log_path_drift", "installed log destinations differ from manifest", "restore declared log paths and reload the agent", owner)
            codes = re.findall(r"last exit (?:code|status)\s*=\s*(-?\d+)", runtime)
            code = int(codes[-1]) if codes else live.get(label, {}).get("exit", 0)
            if enabled and code:
                emit(label, "failing_exit", f"last exit status {code}", "inspect the job log and repair the failing command; verify a successful run", owner)
            if plist is not None:
                argv = expand(job["program_arguments"])
                loaded = loaded_arguments(runtime) if runtime else None
                drifted = [name for name, actual in (("installed", installed_arguments(plist)), ("loaded", loaded))
                           if actual is not None and actual != argv]
                if drifted:
                    emit(label, "program_drift", " and ".join(drifted) + " arguments differ from the declared program arguments",
                         "restore the declared program arguments and reload the agent", owner)
                environment = checkout_environment(plist, runtime)
                fields = (installed_arguments(plist) + (loaded or []) +
                          working_directories(plist, runtime) + environment)
                if checkout_drift(fields, required, roots) or not all(
                        is_checkout(v, required, roots) for v in environment):
                    emit(label, "wrong_checkout", "installed or loaded runtime does not use declared checkout",
                         f"restore declared program in {job['required_checkout']} and reload the agent", owner)
        if enabled and present and job.get("log_max_age_seconds") is not None:
            mtime = snapshot["log_mtimes"].get(activity_path(job))
            if mtime is None or now - mtime > job["log_max_age_seconds"]:
                emit(label, "stale_log", "declared activity log is missing or older than its allowed cadence", "inspect the scheduler and job log; restore a run that writes the declared activity signal", owner)
    git = snapshot.get("git", {})
    if git.get("behind", 0):
        emit("canonical", "behind_main", f"canonical checkout is {git['behind']} commits behind origin/main",
             "repair canonical fleet-sync/fast-forward through bin/fleet-sync.sh and verify HEAD equals origin/main")
    if git.get("ahead", 0):
        emit("canonical", "ahead_of_main", f"canonical checkout has {git['ahead']} commits not on origin/main",
             "move the unpublished canonical commits to a branch through the repository hygiene owner, then verify HEAD equals origin/main")
    if git.get("branch") != "main":
        emit("canonical", "wrong_branch", "canonical checkout does not select main", "restore canonical main through the repository hygiene owner without discarding local work")
    if git.get("remote_matches") is False:
        emit("canonical", "remote_ref_stale", "origin/main does not match GitHub main; behind count is only a cached lower bound",
             "restore fleet-sync's fetch and fast-forward, then verify against remote main")
    return rows


def checkout_candidates(snapshot):
    """Every path naming the checkout a job runs against, for git to resolve."""
    found = set()
    for label, plist in snapshot["plists"].items():
        runtime = snapshot["launchctl_print"].get(label, "")
        values = (installed_arguments(plist) + (loaded_arguments(runtime) or []) +
                  working_directories(plist, runtime) + checkout_environment(plist, runtime))
        found.update(v for v in values if v.startswith("/"))
    for entry in cron_entries(snapshot["cron"]):
        directory = cron_directory(entry["command"])
        found.update(w for w in cron_words(entry["command"]) + [directory or ""] if w.startswith("/"))
    return found


def collect(manifest):
    """Capture machine evidence without modifying git refs, schedules or logs."""
    snapshot = {"plists": {}, "launchctl_print": {}, "log_mtimes": {}, "errors": [], "git": {}, "checkout_roots": {}}
    snapshot["machine_role"] = "primary" if machine_role.is_primary(str(SOURCE), env=scrubbed_env()) else "secondary"
    def command(argv, source, absent_ok=False):
        try:
            result = subprocess.run(argv, capture_output=True, text=True, timeout=10,
                                    stdin=subprocess.DEVNULL, env=scrubbed_env())
            if result.returncode and not absent_ok:
                snapshot["errors"].append(source)
            return result
        except (OSError, subprocess.SubprocessError):
            snapshot["errors"].append(source)
            return subprocess.CompletedProcess(argv, 127, "", "")
    domain = f"gui/{os.getuid()}"
    snapshot["launchctl_list"] = command(["launchctl", "list"], "launchctl_list").stdout
    snapshot["launchctl_disabled"] = command(["launchctl", "print-disabled", domain], "launchctl_disabled").stdout
    prefixes = tuple(manifest.get("scope", {}).get("launchd_label_prefixes", ["com.carr.", "local.carr-"]))
    # A declared job is observed under its own label even when it sits outside the scope
    # prefixes (local.ds4-flash-next); otherwise an installed one reads as absent.
    declared = {j["label"] for j in manifest["jobs"] if j["scheduler"] == "launchd"}
    directories = [Path.home() / "Library/LaunchAgents", Path("/Library/LaunchAgents"), Path("/Library/LaunchDaemons")]
    for directory in directories:
        for path in sorted(directory.glob("*.plist")):
            if not (path.name.startswith(prefixes) or path.stem in declared):
                continue
            try:
                plist = plistlib.loads(path.read_bytes())
                label = plist["Label"]
                if label in snapshot["plists"]:
                    snapshot["errors"].append("duplicate_plist:" + label)
                snapshot["plists"][label] = plist
            except (OSError, ValueError, KeyError, plistlib.InvalidFileException):
                snapshot["errors"].append("plist:" + path.name)
    for label in listed(snapshot["launchctl_list"]):
        if label.startswith(prefixes) or label in declared:
            snapshot["launchctl_print"][label] = command(["launchctl", "print", domain + "/" + label], "launchctl_print:" + label).stdout
    cron = command(["crontab", "-l"], "crontab", absent_ok=True)
    if cron.returncode and "crontab" not in snapshot["errors"] and not (
            cron.returncode == 1 and "no crontab for" in cron.stderr):
        snapshot["errors"].append("crontab")
    snapshot["cron"] = cron.stdout
    directories_seen = {}
    for path in checkout_candidates(snapshot):
        if re.fullmatch(r"python(?:\d+(?:\.\d+)*)?", Path(path).name):
            continue  # The interpreter's dependency checkout is not the job's code checkout.
        directory = Path(path)
        if not directory.is_dir():
            directory = directory.parent
        while not directory.is_dir() and directory != directory.parent:
            directory = directory.parent
        directory = directory.resolve()
        if directory not in directories_seen:
            result = command(["git", "-C", str(directory), "rev-parse", "--show-toplevel"],
                             "runtime_checkout", absent_ok=True)
            directories_seen[directory] = result.stdout.strip() if result.returncode == 0 else None
            if result.returncode not in (0, 128):
                snapshot["errors"].append("runtime_checkout")
        checkout = directories_seen[directory]
        if checkout and os.path.realpath(checkout) not in PACKAGE_MANAGER_ROOTS:
            snapshot["checkout_roots"][path] = checkout
    logs = {activity_path(j) for j in manifest["jobs"] if j.get("log_path")}
    logs.update(p[k] for p in snapshot["plists"].values() for k in ("StandardOutPath", "StandardErrorPath") if p.get(k))
    for log in logs:
        try:
            snapshot["log_mtimes"][log] = Path(log).stat().st_mtime
        except FileNotFoundError:
            snapshot["log_mtimes"][log] = None
        except OSError:
            snapshot["errors"].append("log_stat:" + log)
    canonical = expand(manifest["canonical_checkout"])
    prefix = ["git", "-C", canonical]
    ab = command(prefix + ["rev-list", "--left-right", "--count", "HEAD...origin/main"], "canonical_revision").stdout.split()
    if len(ab) == 2:
        snapshot["git"].update(ahead=int(ab[0]), behind=int(ab[1]))
    snapshot["git"]["branch"] = command(prefix + ["symbolic-ref", "--short", "HEAD"], "canonical_branch").stdout.strip()
    local = command(prefix + ["rev-parse", "origin/main"], "origin_main").stdout.strip()
    remote = command(prefix + ["ls-remote", "origin", "refs/heads/main"], "remote_main").stdout.split()
    if remote:
        snapshot["git"]["remote_matches"] = local == remote[0]
    else:
        snapshot["errors"].append("remote_main")
    return snapshot


def check(snapshot=None, now=None):
    manifest = native_manifest()
    now = time.time() if now is None else now
    if snapshot is not None:
        return report(manifest, snapshot, now)
    path = status_path(Path(expand(manifest['canonical_checkout'])))
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.with_suffix('.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        try:
            rows = report(manifest, collect(manifest), now)
        except (OSError, ValueError, KeyError, TypeError) as exc:
            rows = unavailable(exc)
        with tempfile.NamedTemporaryFile(mode='w', dir=path.parent, delete=False) as handle:
            json.dump(rows, handle)
        os.replace(handle.name, path)
    return rows


def unavailable(exc):
    return [{"label": "checker", "code": "evidence_unavailable",
             "key": "scheduled_jobs:checker:evidence_unavailable", "owner": "orchestrator",
             "detail": "native definition or evidence could not be interpreted (" + type(exc).__name__ + ")",
             "fix": "restore the native definition or named evidence source and rerun"}]


def status_path(root):
    """One machine observation for the checker, scanner, and prompt banner."""
    return root / 'out/watchdog/scheduled-jobs.json'


def native_manifest(source=SOURCE):
    """Derive expectations from the same native definitions the installer uses."""
    source = Path(source)
    manifest = {"schema_version": 1, "canonical_checkout": "~/carr-system", "jobs": []}
    holds = launchd_hold.read_holds()
    for path in sorted((source / "ops/launchd").glob("*.plist")):
        if path.name == "com.carr.call-mode.plist":
            path = source / "tools/dictation-rig/launchd" / path.name
        text = path.read_text().replace("{{REPO}}", "~/carr-system").replace("{{HOME}}", "~")
        definition = plistlib.loads(text.encode())
        interval = schedule(definition)
        if "StartInterval" in interval:
            raise ValueError(path.name + " uses StartInterval; use the macOS calendar schedule")
        enabled = not (definition.get("Disabled") or launchd_hold.off_reason(definition["Label"], holds=holds)
                       or not any(interval.get(k) for k in SCHEDULE_KEYS if k != "ThrottleInterval"))
        manifest["jobs"].append({
            "label": definition["Label"], "scheduler": "launchd",
            "program_arguments": installed_arguments(definition), "interval": interval,
            "required_checkout": "~/carr-system", "expected_enabled": bool(enabled),
            "expected_installed": bool(enabled), "owner": "orchestrator",
            "log_path": definition.get("StandardOutPath") or definition.get("StandardErrorPath"),
            "log_paths": [definition.get(k) for k in ("StandardOutPath", "StandardErrorPath")],
            "log_max_age_seconds": None,
        })
    return manifest
