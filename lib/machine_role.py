"""machine_role — which of a partner's Macs runs the shared (primary-only) jobs.

WHY. Primary used to be decided only by git identity: user.email equal to
OWNER_EMAIL in ops/githooks/pre-push. That ties two different things together.
The owner email is what lets Joe push to main, and he needs it on every Mac he
works from; "primary" is which ONE Mac runs the jobs that write shared state
(nightly record layer, rules refresh, local briefs, partner ping, cutover watch,
video pipeline). When Joe moved to the Mac Studio (2026-09-23) and the MacBook
became a mobile extension, both Macs carried the owner email, so both would have
installed the primary-only jobs and every one of them would have run twice.

THE MARKER. ~/.config/carr/machine-role.json, {"role": "primary"} or
{"role": "secondary"}, written per machine by
`ops/config-as-code.py set-role primary|secondary`. It is deliberately per
machine, but ~/.config/carr is copied wholesale between Macs at migration, so
rewrite it on each Mac after a copy. Mark the old primary secondary BEFORE
marking the new one primary, so the shared jobs never run on both.

PRECEDENCE. A valid marker wins. A missing marker falls back to the old
determinant (git email vs OWNER_EMAIL), so every machine that has never been
marked behaves exactly as before. An unreadable or invalid marker returns
secondary, the same fail-closed direction the old code chose when it could not
prove primary.
"""
import json
import os
import re
import subprocess

ROLES = ("primary", "secondary")


def role_file(home=None):
    home = home or os.path.expanduser("~")
    return os.path.join(home, ".config", "carr", "machine-role.json")


def read_marker(home=None):
    """Return "primary", "secondary", None (no marker), or "invalid"."""
    path = role_file(home)
    try:
        with open(path, encoding="utf-8") as fh:
            role = json.load(fh).get("role")
    except FileNotFoundError:
        return None
    except Exception:
        return "invalid"
    return role if role in ROLES else "invalid"


def write_marker(role, home=None):
    """Atomically write this machine's role; 0600 like the rest of ~/.config/carr."""
    if role not in ROLES:
        raise ValueError(f"role must be one of {ROLES}")
    path = role_file(home)
    os.makedirs(os.path.dirname(path), mode=0o700, exist_ok=True)
    tmp = path + ".tmp"
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        json.dump({"role": role}, fh)
        fh.write("\n")
    os.replace(tmp, path)
    return path


def owner_email(repo):
    """The owner identity from its one home, ops/githooks/pre-push."""
    try:
        with open(os.path.join(repo, "ops", "githooks", "pre-push"),
                  encoding="utf-8") as fh:
            m = re.search(r'^OWNER_EMAIL="([^"]+)"', fh.read(), re.M)
        return m.group(1) if m else ""
    except OSError:
        return ""


def is_primary(repo, git_email=None, home=None, env=None):
    """True only when this machine is proven primary.

    git_email may be passed by callers that already resolve it with their own
    scrubbed git environment; otherwise it is read here.
    """
    marker = read_marker(home)
    if marker == "primary":
        return True
    if marker in ("secondary", "invalid"):
        return False
    owner = owner_email(repo)
    if not owner:
        return False
    if git_email is None:
        git_email = subprocess.run(
            ["git", "-C", repo, "config", "user.email"],
            capture_output=True, text=True, env=env).stdout.strip()
    return git_email == owner
