"""Install the idle watcher without starting the Flash server."""
import os
import plistlib
import re
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import flashlib


LABELS = ("local.ds4-flash-next",)
WATCHER = "com.carr.flash-idle-stop"


def write_changed(path, body):
    if not path.exists() or path.read_bytes() != body:
        path.write_bytes(body)


def install(repo, home, *, launch=subprocess.run):
    repo, home = Path(repo), Path(home)
    agents = home / "Library/LaunchAgents"
    agents.mkdir(parents=True, exist_ok=True)
    domain = f"gui/{os.getuid()}"
    with flashlib.lifecycle_lock(home=home):
        granted = flashlib.active_grant(home=home) is not None
        for label in LABELS:
            path = agents / f"{label}.plist"
            if path.exists():
                installed = plistlib.loads(path.read_bytes())
                if installed.get("RunAtLoad") or installed.get("KeepAlive"):
                    granted = False
        if not granted:
            flashlib._revoke(home=home)
        for label in LABELS:
            source = repo / f"ops/launchd/{label}.plist"
            body = source.read_text().replace("{{HOME}}", str(home)).replace("{{REPO}}", str(repo))
            definition = plistlib.loads(body.encode())
            definition["RunAtLoad"] = False
            definition.pop("KeepAlive", None)
            write_changed(agents / f"{label}.plist", plistlib.dumps(definition))
            if not granted:
                launch(["launchctl", "disable", f"{domain}/{label}"], check=True, timeout=15)
                result = launch(["launchctl", "bootout", f"{domain}/{label}"], capture_output=True, timeout=15)
                if result.returncode not in (0, 3, 113):
                    raise RuntimeError(f"could not stop {label}")
    source = repo / f"ops/launchd/{WATCHER}.plist"
    target = agents / f"{WATCHER}.plist"
    body = source.read_text().replace("{{REPO}}", str(repo))
    unchanged = target.exists() and target.read_bytes() == body.encode()
    loaded = launch(["launchctl", "print", f"{domain}/{WATCHER}"], capture_output=True, timeout=15)
    if not unchanged or loaded.returncode:
        write_changed(target, body.encode())
        (repo / "out").mkdir(exist_ok=True)
        launch(["launchctl", "bootout", f"{domain}/{WATCHER}"], capture_output=True, timeout=15)
        launch(["launchctl", "bootstrap", domain, str(target)], check=True, timeout=15)
        launch(["launchctl", "print", f"{domain}/{WATCHER}"], check=True, capture_output=True, timeout=15)


def check(home, *, launch=subprocess.run):
    agents = Path(home) / "Library/LaunchAgents"
    if (not any((agents / f"{label}.plist").exists() for label in (*LABELS, WATCHER))
            and flashlib.is_switched_off(home=home)):
        return []
    drift = []
    result = launch(["launchctl", "print-disabled", f"gui/{os.getuid()}"],
                    capture_output=True, text=True, timeout=15)
    if result.returncode:
        return ["Flash disabled state unavailable"]
    overrides = dict(re.findall(r'"([^"\n]+)"\s*=>\s*(true|false|disabled|enabled)', result.stdout))
    for label in LABELS:
        try:
            body = plistlib.loads((Path(home) / f"Library/LaunchAgents/{label}.plist").read_bytes())
        except (OSError, ValueError, plistlib.InvalidFileException):
            drift.append(f"{label} missing or invalid")
            continue
        if body.get("RunAtLoad") or body.get("KeepAlive"):
            drift.append(f"{label} automatic startup configured")
        if flashlib.is_switched_off(home=home) and overrides.get(label) not in {"true", "disabled"}:
            drift.append(f"{label} enabled without a human grant")
    return drift


def configure(repo, *, apply=False):
    repo = Path(repo).resolve()
    if not apply:
        print("Would install the 5-minute idle watcher and disable server login startup.")
        return 0
    common = Path(subprocess.check_output(["git", "rev-parse", "--git-common-dir"], cwd=repo, text=True).strip())
    common = (repo / common).resolve()
    branch = subprocess.check_output(["git", "branch", "--show-current"], cwd=repo, text=True).strip()
    if common.parent != repo or branch != "main":
        raise SystemExit("Install from canonical main after merge so launchd retains a stable source path.")
    install(repo, Path.home())
    print("Installed Flash on demand; server was not enabled or started.")
    return 0
