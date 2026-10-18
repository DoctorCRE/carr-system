"""Install the idle watcher and demand launcher without starting either Flash agent."""
import os
import plistlib
import subprocess
from pathlib import Path


def install(repo, home, *, launch=subprocess.run):
    agents = home / "Library/LaunchAgents"
    domain = f"gui/{os.getuid()}"
    for label in ("local.ds4-flash-next", "local.flash-desk"):
        path = agents / f"{label}.plist"
        body = plistlib.loads(path.read_bytes())
        body["RunAtLoad"] = False
        body.pop("KeepAlive", None)
        path.write_bytes(plistlib.dumps(body))
        launch(["/bin/launchctl", "disable", f"{domain}/{label}"], check=True)
    launcher = home / ".local/bin/flash-desk-start"
    launcher.write_text(f'#!/bin/zsh\nexec "{repo}/bin/flash-desk-start" "$@"\n')
    launcher.chmod(0o755)
    label = "com.carr.flash-idle-stop"
    source = repo / f"ops/launchd/{label}.plist"
    target = agents / f"{label}.plist"
    target.write_text(source.read_text().replace("{{REPO}}", str(repo)))
    (repo / "out").mkdir(exist_ok=True)
    # Only the lightweight watcher is loaded. The model and desk stay disabled.
    launch(["/bin/launchctl", "bootout", f"{domain}/{label}"], capture_output=True)
    launch(["/bin/launchctl", "bootstrap", domain, str(target)], check=True)
    launch(["/bin/launchctl", "print", f"{domain}/{label}"], check=True, capture_output=True)


def configure(repo, *, apply=False):
    repo = Path(repo).resolve()
    if not apply:
        print("Would install the 5-minute idle watcher, demand desk launcher, and disable login startup.")
        return 0
    common = Path(subprocess.check_output(["git", "rev-parse", "--git-common-dir"], cwd=repo, text=True).strip())
    common = (repo / common).resolve()
    branch = subprocess.check_output(["git", "branch", "--show-current"], cwd=repo, text=True).strip()
    if common.parent != repo or branch != "main":
        raise SystemExit("Install from canonical main after merge so launchd retains a stable source path.")
    install(repo, Path.home())
    print("Installed Flash on demand; server and desk were not enabled or started.")
    return 0
