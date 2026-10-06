#!/usr/bin/env python3
"""Fixtures for hooks/machine-converge.py — the SessionStart secondary-machine
converge.

THE ONE ASSERTION THIS FILE EXISTS FOR (decision #442): a converge must come
out the other side with the dictation-consent wrapper INTACT. The live plist
is seeded here exactly as the 2026-08-17 incident found it — unwrapped, no
StartInterval — and the test passes only if the converge rewrites it from the
tracked template WITH the four bin/run-scheduled.sh wrapper lines and the
still-recording reminder. This is also the regression trap for the verb
choice: if a future edit swaps the hook's `install --apply` for `pull`, the
seeded unwrapped plist gets captured INTO the fixture repo instead of being
repaired, the live copy stays unwrapped, and this test fails.

Everything runs against throwaway git repos, a throwaway $HOME and a stubbed
launchctl on $PATH, so nothing here can touch real machine config — the same
discipline as ops/fleet-sync-selftest.py.
"""
import json
import os
import plistlib
import re
import shutil
import stat
import subprocess
import sys
import tempfile

SELF_REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))

# Every git hook exports GIT_DIR, and GIT_DIR outranks cwd/-C when git picks
# a repository. sh() and git() below build throwaway repos and origins under
# tempfile.TemporaryDirectory(); without this, an inherited GIT_DIR would
# retarget those "fixture" git calls at whatever checkout invoked this
# selftest — the 2026-08-13 incident ops/git_env.py documents. __file__'s
# directory is ops/, so this reaches the one scrub definition directly.
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from git_env import fixture_env  # noqa: E402
sys.path.insert(0, SELF_REPO)
from lib import launchd_calendar  # noqa: E402


def fixture_git_env():
    """Keep Git's automatic maintenance inside the fixture command lifetime.

    Porcelain commands may start `git maintenance run --auto` or `git gc
    --auto`. Both detach controls default to true, which lets a maintenance
    child keep writing `.git/objects` after subprocess.run() has returned and
    race TemporaryDirectory cleanup. The fixture still exercises automatic
    maintenance; it merely waits for that child like every other subprocess.
    """
    env = fixture_env()
    env.update({
        "GIT_CONFIG_COUNT": "2",
        "GIT_CONFIG_KEY_0": "maintenance.autoDetach",
        "GIT_CONFIG_VALUE_0": "false",
        "GIT_CONFIG_KEY_1": "gc.autoDetach",
        "GIT_CONFIG_VALUE_1": "false",
    })
    return env

# The checkout under test supplies the real hook, the real installer and the
# real tracked template — a hand-copied template here would let the template
# and the test drift apart, which is the two-homes disease.
COPIES = [
    "hooks/machine-converge.py",
    "hooks/grok_invocation.py",
    "lib/claude_continuity_config.py",
    "lib/machine_prerequisites.py",
    "lib/launchd_calendar.py",
    "lib/machine_role.py",
    "lib/launchd_scope.py",
    "lib/launchd_hold.py",
    "mcp-server/continuity-stdio-proxy.mjs",
    "ops/claude-continuity-hook.py",
    "ops/config-as-code.py",
    "ops/config/claude-continuity-hooks.json",
    "ops/git_env.py",
    "ops/githooks/pre-push",
    "ops/config/hooks.json",
    "ops/launchd/com.carr.dictation-consent.plist",
]

# The pre-#442 live state: wrapper stripped, no StartInterval. {repo} is
# filled per fixture.
DRIFTED_PLIST = """<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>com.carr.dictation-consent</string>
    <key>ProgramArguments</key>
    <array>
        <string>/bin/zsh</string>
        <string>{repo}/tools/dictation-rig/bin/consent-watch.sh</string>
    </array>
    <key>RunAtLoad</key>
    <false/>
</dict>
</plist>
"""


def sh(args, cwd=None, env=None):
    # Callers that don't need a special environment (every raw `git` call in
    # this file) get the scrubbed, joined fixture environment by default, so a
    # stray fixture git call can never fall through to ambient identity or
    # leave maintenance behind. Callers needing something specific build on
    # the same environment rather than passing os.environ straight through.
    return subprocess.run(args, cwd=cwd, env=fixture_git_env() if env is None else env,
                          capture_output=True, text=True)


def git(repo, *args, env=None):
    r = sh(["git", "-C", repo, *args], env=env)
    if r.returncode != 0:
        raise RuntimeError(f"fixture git {' '.join(args)} failed: {r.stderr}")
    return r.stdout.strip()


def keep_maintenance_joined(repo):
    """Persist the fixture-only lifetime rule for nested Git subprocesses."""
    git(repo, "config", "maintenance.autoDetach", "false")
    git(repo, "config", "gc.autoDetach", "false")


def owner_email():
    with open(os.path.join(SELF_REPO, "ops", "githooks", "pre-push"),
              encoding="utf-8") as fh:
        return re.search(r'^OWNER_EMAIL="([^"]+)"', fh.read(), re.M).group(1)


def write_exec(path, body):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(body)
    os.chmod(path, os.stat(path).st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)


def build_fixture(root, email, actor_slug=None, behind=True, dirty=False,
                  seed_drifted_plist=True):
    """A canonical-checkout stand-in, its origin, a fake $HOME, a stub PATH."""
    repo = os.path.join(root, "repo")
    home = os.path.join(root, "home")
    stubs = os.path.join(root, "stubs")

    for rel in COPIES:
        dst = os.path.join(repo, rel)
        os.makedirs(os.path.dirname(dst), exist_ok=True)
        shutil.copy2(os.path.join(SELF_REPO, rel), dst)

    # The PR #555 install guard requires this fixture repo to carry the scripts
    # its hooks block names, just as a real checkout does.
    hooks_path = os.path.join(SELF_REPO, "ops", "config", "hooks.json")
    with open(hooks_path, encoding="utf-8") as fh:
        hooks_text = fh.read()
    hook_scripts = set(re.findall(r'\{\{REPO\}\}/([^\s"\']+\.py)', hooks_text))
    for rel in hook_scripts:
        src = os.path.join(SELF_REPO, rel)
        if not os.path.isfile(src):
            raise FileNotFoundError(f"hook script missing from checkout: {src}")
        dst = os.path.join(repo, rel)
        os.makedirs(os.path.dirname(dst), exist_ok=True)
        shutil.copy2(src, dst)
    write_exec(os.path.join(repo, "bin", "run-scheduled.sh"), "#!/bin/zsh\nexit 0\n")
    write_exec(os.path.join(repo, "tools", "dictation-rig", "bin", "consent-watch.sh"),
               "#!/bin/zsh\nexit 0\n")
    with open(os.path.join(repo, "README.md"), "w", encoding="utf-8") as fh:
        fh.write("fixture\n")

    git(repo, "init", "-q", "-b", "main")
    keep_maintenance_joined(repo)
    git(repo, "config", "user.email", email)
    git(repo, "config", "user.name", "Fixture")
    git(repo, "add", "-A")
    git(repo, "commit", "-q", "-m", "seed")

    origin = os.path.join(root, "origin.git")
    sh(["git", "clone", "-q", "--bare", repo, origin])
    keep_maintenance_joined(origin)
    git(repo, "remote", "add", "origin", origin)
    git(repo, "fetch", "-q", "origin")

    if behind:
        work = os.path.join(root, "work")
        sh(["git", "clone", "-q", origin, work])
        keep_maintenance_joined(work)
        git(work, "config", "user.email", email)
        git(work, "config", "user.name", "Fixture")
        with open(os.path.join(work, "README.md"), "a", encoding="utf-8") as fh:
            fh.write("origin moved\n")
        git(work, "commit", "-qam", "origin ahead")
        git(work, "push", "-q", "origin", "main")

    if dirty:
        with open(os.path.join(repo, "README.md"), "a", encoding="utf-8") as fh:
            fh.write("LOCAL EDIT THAT MUST SURVIVE\n")

    if actor_slug:
        actor = os.path.join(home, ".config", "carr", "local-actor.json")
        os.makedirs(os.path.dirname(actor), exist_ok=True)
        with open(actor, "w", encoding="utf-8") as fh:
            json.dump({"actor_slug": actor_slug}, fh)

    agents = os.path.join(home, "Library", "LaunchAgents")
    os.makedirs(agents, exist_ok=True)
    if seed_drifted_plist:
        with open(os.path.join(agents, "com.carr.dictation-consent.plist"),
                  "w", encoding="utf-8") as fh:
            fh.write(DRIFTED_PLIST.format(repo=repo))

    installed = os.path.join(agents, "com.carr.dictation-consent.plist")
    with open(os.path.join(root, "launchctl-state.json"), "w", encoding="utf-8") as fh:
        json.dump({"com.carr.dictation-consent": installed} if seed_drifted_plist else {}, fh)
    write_exec(os.path.join(stubs, "launchctl"), """#!/usr/bin/env python3
import json, os, plistlib, sys
from pathlib import Path
args = sys.argv[1:]
state_path = Path(os.environ['LAUNCHCTL_STATE'])
state = json.loads(state_path.read_text())
with open(os.environ['LAUNCHCTL_LOG'], 'a', encoding='utf-8') as log:
    log.write(' '.join(args) + '\\n')
if args[0] == 'print':
    label = args[1].rsplit('/', 1)[-1]
    if label in state:
        print(f'path = {state[label]}')
        sys.exit(0)
    print(f'Could not find service "{label}" in domain for user gui: 501', file=sys.stderr)
    sys.exit(113)
if args[0] in ('load', 'unload'):
    dest = args[-1]
    label = plistlib.loads(Path(dest).read_bytes())['Label']
    if args[0] == 'load':
        state[label] = dest
    else:
        state.pop(label, None)
    state_path.write_text(json.dumps(state))
    sys.exit(0)
sys.exit(64)
""")
    return repo, home, stubs


def run_hook(repo, home, stubs, root):
    # fixture_git_env() is the base rather than a hand-rolled `for var in
    # list(env): if var.startswith("GIT_") ...` loop, so this can't drift
    # from ops/git_env.py's list the way the sync-enforcement-map copies did
    # (see the git_env.py docstring). CLAUDE_PROJECT_DIR still has to go by
    # hand — it isn't a git variable, but it's another pointer back at the
    # real checkout that the hook must not be able to discover.
    env = dict(fixture_git_env(), HOME=home,
               PATH=stubs + os.pathsep + os.environ.get("PATH", ""),
               LAUNCHCTL_LOG=os.path.join(root, "launchctl.log"),
               LAUNCHCTL_STATE=os.path.join(root, "launchctl-state.json"))
    env.pop("CLAUDE_PROJECT_DIR", None)
    return sh([sys.executable, os.path.join(repo, "hooks", "machine-converge.py")],
              cwd=root, env=env)


def installed_plist(home):
    path = os.path.join(home, "Library", "LaunchAgents",
                        "com.carr.dictation-consent.plist")
    with open(path, "rb") as fh:
        return plistlib.load(fh)


def wrapper_intact(home):
    """The four deliberate wrapper lines of commit 4fb58a8a, in order, plus
    the still-recording five-minute tick PR #328 carried into the template
    (a StartCalendarInterval since macOS 27 stopped firing StartInterval)."""
    try:
        d = installed_plist(home)
    except FileNotFoundError:
        return False
    args = d.get("ProgramArguments") or []
    return (len(args) == 5
            and args[0] == "/bin/zsh"
            and args[1].endswith("/bin/run-scheduled.sh")
            and args[2] == "dictation-consent"
            and args[3] == "launchd.run"
            and args[4].endswith("/tools/dictation-rig/bin/consent-watch.sh")
            and "StartInterval" not in d
            and launchd_calendar.cadence_seconds(d) == 300)


def case_secondary_converges(tmp):
    """Dell's machine: behind origin, drifted plist -> ff + wrapper restored."""
    repo, home, stubs = build_fixture(tmp, "dell@example.com", actor_slug="dell")
    r = run_hook(repo, home, stubs, tmp)
    checks = {
        "hook exits 0": r.returncode == 0,
        "checkout fast-forwarded to origin/main":
            git(repo, "rev-parse", "HEAD") == git(repo, "rev-parse", "origin/main"),
        "dictation-consent wrapper SURVIVES the converge": wrapper_intact(home),
        "launchd job reloaded through launchctl":
            "load" in open(os.path.join(tmp, "launchctl.log")).read(),
        "Claude settings rendered":
            os.path.isfile(os.path.join(home, ".claude", "settings.json")),
        "no ~/.codex created on a Claude-only machine":
            not os.path.exists(os.path.join(home, ".codex")),
        "brief line reports the apply": "re-applied" in r.stdout,
    }
    return checks


def case_primary_noop(tmp):
    """Joe's machine by actor slug: nothing moves, nothing prints."""
    repo, home, stubs = build_fixture(tmp, "dell@example.com", actor_slug="joe")
    before = git(repo, "rev-parse", "HEAD")
    r = run_hook(repo, home, stubs, tmp)
    return {
        "hook exits 0": r.returncode == 0,
        "prints nothing": r.stdout.strip() == "",
        "checkout not moved": git(repo, "rev-parse", "HEAD") == before,
        "no config written":
            not os.path.exists(os.path.join(home, ".claude", "settings.json")),
        "drifted plist deliberately untouched": not wrapper_intact(home),
    }


def case_owner_fallback_noop(tmp):
    """No local-actor.json: the owner's git identity means primary -> no-op."""
    repo, home, stubs = build_fixture(tmp, owner_email(), actor_slug=None)
    before = git(repo, "rev-parse", "HEAD")
    r = run_hook(repo, home, stubs, tmp)
    return {
        "hook exits 0": r.returncode == 0,
        "prints nothing": r.stdout.strip() == "",
        "checkout not moved": git(repo, "rev-parse", "HEAD") == before,
        "no config written":
            not os.path.exists(os.path.join(home, ".claude", "settings.json")),
    }


def case_dirty_tree_refuses_ff_but_still_applies(tmp):
    """Local tracked edits: the pull refuses (fleet-sync's rule), the edit
    survives, and the config STILL converges from the local checkout."""
    repo, home, stubs = build_fixture(tmp, "dell@example.com", actor_slug="dell",
                                      dirty=True)
    before = git(repo, "rev-parse", "HEAD")
    r = run_hook(repo, home, stubs, tmp)
    readme = open(os.path.join(repo, "README.md"), encoding="utf-8").read()
    return {
        "hook exits 0": r.returncode == 0,
        "pull refused and says so": "SKIP pull" in r.stdout,
        "checkout not moved": git(repo, "rev-parse", "HEAD") == before,
        "local edit survives": "LOCAL EDIT THAT MUST SURVIVE" in readme,
        "wrapper still restored from the LOCAL template": wrapper_intact(home),
    }


def write_role(home, role):
    d = os.path.join(home, ".config", "carr")
    os.makedirs(d, exist_ok=True)
    with open(os.path.join(d, "machine-role.json"), "w", encoding="utf-8") as fh:
        fh.write('{"role": "%s"}' % role)


def case_marker_secondary_converges(tmp):
    """Joe's second Mac: owner email AND joe slug, but marked secondary ->
    it converges like any secondary (the 2026-09-23 Studio move)."""
    repo, home, stubs = build_fixture(tmp, owner_email(), actor_slug="joe")
    write_role(home, "secondary")
    r = run_hook(repo, home, stubs, tmp)
    return {
        "hook exits 0": r.returncode == 0,
        "checkout fast-forwarded to origin/main":
            git(repo, "rev-parse", "HEAD") == git(repo, "rev-parse", "origin/main"),
        "brief line reports the apply": "re-applied" in r.stdout,
    }


def case_marker_primary_noop(tmp):
    """Marked primary outranks a non-owner slug: nothing moves."""
    repo, home, stubs = build_fixture(tmp, "dell@example.com", actor_slug="dell")
    write_role(home, "primary")
    before = git(repo, "rev-parse", "HEAD")
    r = run_hook(repo, home, stubs, tmp)
    return {
        "hook exits 0": r.returncode == 0,
        "prints nothing": r.stdout.strip() == "",
        "checkout not moved": git(repo, "rev-parse", "HEAD") == before,
    }


def main():
    cases = [
        ("secondary machine converges", case_secondary_converges),
        ("primary machine (actor slug) is a no-op", case_primary_noop),
        ("primary machine (owner-email fallback) is a no-op", case_owner_fallback_noop),
        ("dirty tree refuses ff, keeps the edit, still applies", case_dirty_tree_refuses_ff_but_still_applies),
        ("marked secondary converges despite owner identity", case_marker_secondary_converges),
        ("marked primary is a no-op despite non-owner slug", case_marker_primary_noop),
    ]
    passed = failed = 0
    for name, fn in cases:
        with tempfile.TemporaryDirectory(prefix="machine-converge-") as tmp:
            try:
                checks = fn(tmp)
            except Exception as exc:
                print(f"FAIL  {name}: raised {exc!r}")
                failed += 1
                continue
        bad = [label for label, ok in checks.items() if not ok]
        if bad:
            print(f"FAIL  {name}: " + "; ".join(bad))
            failed += 1
        else:
            print(f"PASS  {name} ({len(checks)} checks)")
            passed += 1
    print(f"machine-converge-selftest: {passed}/{passed + failed} cases passed")
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
