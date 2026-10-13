#!/usr/bin/env python3
"""Is THIS machine in step with the repo, and with the other partner's Mac?

WHY THIS EXISTS. On 2026-08-19 Dell's Mac looked migrated and was not: the
checkout was current, every gate matched, config-as-code reported clean — and
the machine still could not run its own checks, because it had Python 3.9.6
and the repo needs 3.10+. Thirty-six gate selftests could not be imported,
mypy could not be installed at all, and every push used CARR_SKIP_CI. None of
the existing checks reported any of that, because each one answered a narrower
question than "am I actually in step".

The audit that found it was a pile of ad-hoc shell typed into one session. That
is the wrong home for it: it cannot be re-run, it cannot be compared between
machines, and it dies with the transcript. Rule a8c55a47 — a manual path and an
automated path that do the same job must be the same code — so it lives here,
and both Macs run this one file.

THE DISTINCTION THIS DRAWS, and the reason a plain diff of two machines is
useless: some differences are correct. A secondary machine deliberately does
not run the six jobs that write shared state, and deliberately has no ~/.codex.
Reporting those as drift trains people to ignore the output. So every row is
one of OK, BY DESIGN, or GAP, and only GAP rows count toward the verdict.

Exit status: 0 always, unless --strict, which exits 1 when any GAP is found.
Read-only. It changes nothing on the machine.
"""
import argparse
import importlib.util
import json
import os
import re
import shutil
import subprocess
import sys

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SERVICES = os.path.join(REPO, "ops", "config", "services.json")
LAUNCH_AGENTS = os.path.expanduser("~/Library/LaunchAgents")
INSTALL = "action: python3 ops/config-as-code.py install --apply"

# The interpreter floor, kept in step with requirements.txt's mypy marker and
# with bin/migrate-dell.sh's PY_MIN_MINOR. Three places name 3.10; if one moves
# and the others do not, ops/ci-selftest.py fails on the mismatch.
PY_MIN = (3, 10)

OK, DESIGN, GAP = "OK", "BY DESIGN", "GAP"

# (section, label, state, detail)
ROWS: list[tuple[str, str, str, str]] = []


def row(section, label, state, detail=""):
    ROWS.append((section, label, state, detail))


def sh(*args, **kw):
    """Run a command, never raise. Returns (rc, stdout+stderr stripped)."""
    try:
        p = subprocess.run(args, capture_output=True, text=True, cwd=REPO,
                           timeout=kw.get("timeout", 120))
        return p.returncode, ((p.stdout or "") + (p.stderr or "")).strip()
    except (OSError, subprocess.SubprocessError) as exc:
        return 127, f"{type(exc).__name__}: {exc}"


def load_config_as_code():
    path = os.path.join(REPO, "ops", "config-as-code.py")
    spec = importlib.util.spec_from_file_location("cac_audit", path)
    if spec is None or spec.loader is None:
        return None
    mod = importlib.util.module_from_spec(spec)
    try:
        spec.loader.exec_module(mod)
    except Exception:
        return None
    return mod


def audit_identity(cac):
    actor_file = os.path.expanduser("~/.config/carr/local-actor.json")
    slug = None
    if os.path.exists(actor_file):
        try:
            slug = json.load(open(actor_file)).get("actor_slug")
        except (OSError, ValueError):
            slug = None
    # An ABSENT actor file is not cosmetic: the fallback classifies the machine
    # by git email, and a wrong answer there silently makes a secondary machine
    # behave as the primary. Dell's Mac had no file at all on 2026-08-19.
    row("identity", "machine actor", OK if slug else GAP,
        slug or "~/.config/carr/local-actor.json missing — run bin/set-local-actor.sh")

    _, email = sh("git", "config", "user.email")
    row("identity", "git identity", OK if email else GAP, email or "unset")

    if cac is not None:
        primary = bool(getattr(cac, "IS_PRIMARY", False))
        row("identity", "role", OK,
            ("primary" if primary else "secondary")
            + f" · {len(getattr(cac, 'PRIMARY_ONLY', ()))} shared-state jobs are primary-only")
        return primary
    row("identity", "role", GAP, "could not import ops/config-as-code.py")
    return None


def audit_repo():
    _, branch = sh("git", "rev-parse", "--abbrev-ref", "HEAD")
    sh("git", "fetch", "-q", "origin", "main")
    _, behind = sh("git", "rev-list", "--count", "HEAD..origin/main")
    _, dirty = sh("git", "status", "--porcelain", "--untracked-files=no")
    _, stranded = sh("git", "rev-list", "--count", "HEAD", "--not", "--remotes")

    row("repo", "branch", OK, branch)
    n = behind if behind.isdigit() else "?"
    row("repo", "behind trunk", OK if n == "0" else GAP, f"{n} commits")
    row("repo", "tracked tree", OK if not dirty else GAP,
        "clean" if not dirty else f"{len(dirty.splitlines())} modified")
    # Unpushed work is the failure class where a fix exists only on one laptop.
    row("repo", "unpushed commits", OK if stranded == "0" else GAP,
        f"{stranded} reachable from no remote")


def audit_config_and_gates():
    py = os.path.join(REPO, ".venv", "bin", "python")
    py = py if os.path.exists(py) else sys.executable
    rc, out = sh(py, os.path.join(REPO, "ops", "config-as-code.py"), "check")
    last = out.strip().splitlines()[-1] if out.strip() else "no output"
    row("config", "machine matches repo", OK if rc == 0 else GAP, last[:90])

    rc, out = sh(sys.executable, os.path.join(REPO, "hooks", "gate-integrity.py"))
    first = out.strip().splitlines()[0] if out.strip() else "no output"
    m = re.search(r"(\d+) gates match baseline", first)
    row("config", "gates", OK if (rc == 0 and m) else GAP,
        f"{m.group(1)} matching baseline" if m else first[:90])


def audit_toolchain():
    py = os.path.join(REPO, ".venv", "bin", "python")
    if os.path.exists(py):
        _, ver = sh(py, "-c", "import sys;print('%d.%d.%d' % sys.version_info[:3])")
        parts = ver.split(".")
        try:
            ok = (int(parts[0]), int(parts[1])) >= PY_MIN
        except (ValueError, IndexError):
            ok = False
        # THE ONE THAT WAS MISSED. Every other check passed on a machine whose
        # interpreter could not import a third of its own test suite.
        row("toolchain", "venv python", OK if ok else GAP,
            f"{ver} (floor {PY_MIN[0]}.{PY_MIN[1]})")
        rc, mv = sh(py, "-m", "mypy", "--version")
        row("toolchain", "mypy", OK if rc == 0 else GAP,
            mv.splitlines()[0] if rc == 0 else "absent — the types class cannot bind")
        rc, _ = sh(py, "-c", "import pytest")
        row("toolchain", "pytest", OK if rc == 0 else GAP,
            "present" if rc == 0 else "absent — some gate suites cannot run")
    else:
        row("toolchain", "venv python", GAP, ".venv missing")

    node = shutil.which("node")
    if node:
        _, nv = sh(node, "-v")
        row("toolchain", "node", OK, nv)
    else:
        row("toolchain", "node", GAP, "absent")

    if shutil.which("gh"):
        rc, _ = sh("gh", "auth", "status")
        # Unattended runs have no browser to borrow, so this is not cosmetic.
        row("toolchain", "github cli", OK if rc == 0 else GAP,
            "logged in" if rc == 0 else "not logged in — no unattended pull requests")
    else:
        row("toolchain", "github cli", GAP, "not installed")


def audit_optional():
    db = os.path.expanduser("~/.config/carr/db.env")
    row("optional", "database credential", OK if os.path.exists(db) else GAP,
        "present" if os.path.exists(db)
        else "absent — checks needing it decline with exit 78 (a skip, not a failure)")

    quill = os.path.join(REPO, "tools", "dictation-rig", "vendor", "quill")
    has_quill = os.path.isdir(quill) and bool(os.listdir(quill))
    row("optional", "dictation rig", OK if has_quill else GAP,
        "submodule present" if has_quill else "submodule not initialised")

    # Codex absent is the SUPPORTED state on a Claude-only machine, so calling
    # it a gap would be wrong. Its presence is equally fine where chosen.
    codex = os.path.isdir(os.path.expanduser("~/.codex"))
    row("optional", "codex adapter", DESIGN,
        "present" if codex else "absent (supported on a Claude-only machine)")


def audit_launchd(primary, services, agents, holds=None,
                  repo_launchd=os.path.join(REPO, "ops", "launchd")):
    """Join the catalog's runs_on with the com.carr agents launchd has installed.

    WR-000284 C6: on 2026-10-13 eighteen declared jobs were absent from the
    MacBook and one installed job was declared nowhere, and both were found by
    hand. Each declared plist is OK when installed where it runs, BY DESIGN
    when absent from a machine it does not run on (or held there), and a GAP
    otherwise; every GAP names the command that closes it.
    """
    sys.path.insert(0, REPO)
    try:
        from lib import launchd_hold, launchd_scope
        placed = launchd_scope.placements(services)
        holds = launchd_hold.read_holds() if holds is None else holds
    except (ImportError, OSError, ValueError, KeyError) as exc:
        row("launchd", "catalog", GAP, f"{type(exc).__name__}: {exc} — action: fix "
            "ops/config/services.json, lib/launchd_scope.py or ~/.config/carr/launchd-hold")
        return
    if primary is None:
        row("launchd", "placement", GAP,
            "machine role unknown — action: fix the identity rows above, then re-run")
        return
    if not os.path.isdir(agents):
        row("launchd", "LaunchAgents", GAP,
            f"{agents} absent — action: run this audit on a Mac; launchd exists only there")
        return
    installed = {f for f in os.listdir(agents)
                 if f.startswith("com.carr.") and f.endswith(".plist")}
    in_repo = set(os.listdir(repo_launchd)) if os.path.isdir(repo_launchd) else set()
    here = "primary" if primary else "secondary"

    def remove(label, name):
        return (f"action: launchctl bootout gui/$(id -u)/{label}, then delete "
                f"~/Library/LaunchAgents/{name}")

    for name in sorted(set(placed) | installed):
        label = name.removesuffix(".plist")
        where = placed.get(name)
        if where is None and name in launchd_hold.DEFINITION_ONLY:
            row("launchd", label, GAP, "definition only, awaiting its activation packet, "
                "yet installed — " + remove(label, name))
        elif where is None:
            row("launchd", label, GAP, "installed but declared nowhere — " + (
                "action: register its ops/launchd plist in ops/config/services.json, or "
                "list it in lib/launchd_hold.DEFINITION_ONLY so install stops placing it"
                if name in in_repo else
                "action: uninstall it or add its plist to ops/launchd and register it "
                "in ops/config/services.json"))
        elif where in ("every", here):
            if name in installed:
                row("launchd", label, OK, f"runs on {where} · installed")
            elif label in holds:
                row("launchd", label, DESIGN, f"runs on {where} · HELD: {holds[label].reason}")
            else:
                row("launchd", label, GAP, f"runs on {where} · not installed — {INSTALL}")
        elif name in installed:
            # install --apply retires a primary-only plist from a secondary; it
            # only skips a secondary-only plist on the primary, never removes it.
            row("launchd", label, GAP, f"runs on {where} only but is installed here — " + (
                INSTALL if where == "primary" else remove(label, name)))
        else:
            row("launchd", label, DESIGN, f"runs on {where} · not installed here")


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--strict", action="store_true",
                    help="exit 1 when any GAP is found")
    ap.add_argument("--json", action="store_true", help="machine-readable output")
    ap.add_argument("--services", default=SERVICES, help="the service catalog to place jobs by")
    ap.add_argument("--launch-agents", default=LAUNCH_AGENTS,
                    help="the LaunchAgents directory to read installed jobs from")
    args = ap.parse_args(argv)

    cac = load_config_as_code()
    primary = audit_identity(cac)
    audit_repo()
    audit_config_and_gates()
    audit_toolchain()
    audit_optional()
    audit_launchd(primary, args.services, args.launch_agents)

    gaps = [r for r in ROWS if r[2] == GAP]

    if args.json:
        print(json.dumps({
            "rows": [{"section": s, "label": l, "state": st, "detail": d}
                     for s, l, st, d in ROWS],
            "gaps": len(gaps),
        }, indent=2))
        return 1 if (args.strict and gaps) else 0

    _, host = sh("hostname", "-s")
    print(f"\nMACHINE SYNC AUDIT — {host or 'this machine'}\n")
    section = None
    for sec, label, state, detail in ROWS:
        if sec != section:
            print(f"  {sec.upper()}")
            section = sec
        mark = {OK: "ok  ", DESIGN: "----", GAP: "GAP "}[state]
        print(f"    {mark}  {label:<22} {detail}")
    print()
    if gaps:
        print(f"  {len(gaps)} gap(s) — everything else is in step or correct by design:")
        for _, label, _, detail in gaps:
            print(f"    · {label}: {detail}")
    else:
        print("  No gaps. This machine is in step with the repo.")
    print()
    return 1 if (args.strict and gaps) else 0


if __name__ == "__main__":
    sys.exit(main())
