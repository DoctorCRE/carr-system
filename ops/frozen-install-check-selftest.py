#!/usr/bin/env python3
"""frozen-install-check-selftest.py — the seeded unfrozen install is caught.

This is the acceptance test named by slice R05: "the checker catches a seeded
unfrozen install locally". It seeds real violations into a throwaway tree and
asserts ops/frozen-install-check.py fails on each one — and, just as important,
that it does NOT fail on the near-misses that would make it noise: a quoted
error message telling a human to run npm install, a comment explaining why an
install is not run, a Python hint string, a global CLI install, and the
installer bootstrap.

A checker that only proves the positive case is a checker nobody trusts once it
starts firing, so every negative below is a shape that actually exists in this
repo's tree today.

Run: .venv/bin/python ops/frozen-install-check-selftest.py   # exit 0 = all pass
"""

import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from git_env import fixture_env  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
CHECK = ROOT / "ops" / "frozen-install-check.py"
FIXTURE_ENV = fixture_env()


def git(*args, cwd):
    subprocess.run(("git",) + args, cwd=cwd, check=True,
                   capture_output=True, text=True, env=FIXTURE_ENV)


def check(root):
    proc = subprocess.run(
        [sys.executable, str(CHECK), "--root", str(root), "--json"],
        capture_output=True, text=True, env=FIXTURE_ENV)
    return proc.returncode, json.loads(proc.stdout)


def build(td, files, scope=None, track=True):
    """A throwaway tree containing exactly `files` ({relpath: text})."""
    root = Path(td)
    git("init", "-q", "-b", "main", cwd=root)
    git("config", "user.email", "fixture@example.com", cwd=root)
    git("config", "user.name", "fixture", cwd=root)
    written = []
    if scope is not None:
        files = dict(files)
        files["ops/config/frozen-install-scope.json"] = json.dumps(scope)
    for name, text in files.items():
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)
        written.append(name)
    if track:
        git("add", *written, cwd=root)
        git("commit", "-qm", "fixture", cwd=root)
    return root


def paths(report):
    return {p["path"] for p in report["problems"]}


# ---- 1. THE SEEDED VIOLATIONS. Each one alone must fail the check. ----------
SEEDS = {
    "npm install": "bin/seed.sh|#!/bin/sh\nnpm --prefix mcp-server install\n",
    "npm i": "bin/seed.sh|#!/bin/sh\nnpm i lodash\n",
    "pip loose requirements": "bin/seed.sh|#!/bin/sh\npip install -r requirements.txt\n",
    "pip unpinned package": "bin/seed.sh|#!/bin/sh\npip3 install psycopg\n",
    "python -m pip unpinned": "bin/seed.sh|#!/bin/sh\npython3 -m pip install openpyxl\n",
    "uv pip unpinned": "bin/seed.sh|#!/bin/sh\nuv pip install openpyxl\n",
    "pnpm unfrozen": "bin/seed.sh|#!/bin/sh\npnpm install\n",
    "yarn unfrozen": "bin/seed.sh|#!/bin/sh\nyarn install\n",
    "workflow pip": ".github/workflows/seed.yml|jobs:\n  a:\n    steps:\n      - run: pip install psycopg\n",
    "package.json script": "mcp-server/package.json|"
                           + json.dumps({"name": "x", "scripts": {"setup": "npm install"}}),
    # The continuation case: read line-by-line this looks like a bare
    # `pip install -q`, with no requirement file to judge at all.
    "continued line": "bin/seed.sh|#!/bin/sh\npip install -q \\\n  -r requirements.txt\n",
    # A local project install RESOLVES the project's own dependencies from the
    # index unless resolution is switched off, so it is not frozen. The first
    # cut listed `.` and `-e` as always-frozen and forgave exactly this.
    "local project": "bin/seed.sh|#!/bin/sh\npip install .\n",
    "local project editable": "bin/seed.sh|#!/bin/sh\npip install -e .\n",
    "local project path": "bin/seed.sh|#!/bin/sh\npip install ./vendor-patches\n",
    "local project extra": "bin/seed.sh|#!/bin/sh\npip install .[dev]\n",
}
for label, spec in SEEDS.items():
    name, text = spec.split("|", 1)
    with tempfile.TemporaryDirectory() as td:
        root = build(td, {name: text})
        rc, report = check(root)
        assert rc == 1, f"{label}: seeded violation was NOT caught\n{report}"
        assert name in paths(report), f"{label}: caught, but not in {name}\n{report}"

# ---- 2. THE FROZEN SPELLINGS PASS. ------------------------------------------
FROZEN = {
    "npm ci": "bin/ok.sh|#!/bin/sh\nnpm --prefix mcp-server ci\n",
    "pip lock": "bin/ok.sh|#!/bin/sh\npip install -r requirements.lock\n",
    "pip pinned": "bin/ok.sh|#!/bin/sh\npip install 'psycopg[binary]==3.2.1'\n",
    "pip hashes": "bin/ok.sh|#!/bin/sh\npip install --require-hashes -r reqs.txt\n",
    "pnpm frozen": "bin/ok.sh|#!/bin/sh\npnpm install --frozen-lockfile\n",
    "yarn immutable": "bin/ok.sh|#!/bin/sh\nyarn install --immutable\n",
    # Near-misses that exist in this tree and must never fire.
    "quoted npm message": 'bin/ok.sh|#!/bin/sh\ndie "wrangler missing (run npm install in mcp-server/)"\n',
    "quoted pip message": 'bin/ok.sh|#!/bin/sh\nbad "pip install failed - see /tmp/mig-pip.log"\n',
    "comment": "bin/ok.sh|#!/bin/sh\n# a per-worktree npm install would drift from the lockfile\ntrue\n",
    "python hint string": 'ops/ok.py|raise SystemExit("psycopg missing (pip install psycopg)")\n',
    "python comment": "ops/ok.py|# pip install 15s -> 11s in the timing table\nx = 1\n",
    "global cli": "bin/ok.sh|#!/bin/sh\nnpm i -g @openai/codex\n",
    "installer bootstrap": "bin/ok.sh|#!/bin/sh\npython3 -m pip install --upgrade pip\n",
    "redirections only": "bin/ok.sh|#!/bin/sh\npip install -r requirements.lock >/tmp/a.log 2>&1\n",
    # Resolution switched off makes a local install reproducible again.
    "local project no-deps": "bin/ok.sh|#!/bin/sh\npip install --no-deps .\n",
    "local editable no-deps": "bin/ok.sh|#!/bin/sh\npip install --no-deps -e .\n",
    # Prose is deliberately out of scope; flagging it trains people to ignore
    # the check.
    "prose outside scan roots": "corpus/notes.sh|#!/bin/sh\nnpm install\n",
}
for label, spec in FROZEN.items():
    name, text = spec.split("|", 1)
    with tempfile.TemporaryDirectory() as td:
        root = build(td, {name: text})
        rc, report = check(root)
        assert rc == 0, f"{label}: false positive\n{json.dumps(report, indent=2)}"

# ---- 3. TRACKED FILES ONLY, like every other scanner in this repo. ----------
with tempfile.TemporaryDirectory() as td:
    root = build(td, {"bin/keep.sh": "#!/bin/sh\nnpm ci\n"})
    (root / "bin" / "untracked.sh").write_text("#!/bin/sh\nnpm install\n")
    rc, report = check(root)
    assert rc == 0, f"untracked file was scanned\n{report}"
    git("add", "bin/untracked.sh", cwd=root)
    rc, report = check(root)
    assert rc == 1, "once added, the violation must be caught"
    assert "bin/untracked.sh" in paths(report), report

# ---- 4. EXCEPTIONS ARE ANNOUNCED, NOT SILENT. ------------------------------
seed = {"bin/seed.sh": "#!/bin/sh\npip install -r requirements.txt\n"}
with tempfile.TemporaryDirectory() as td:
    root = build(td, seed, scope={
        "exempt": [{"path": "bin/seed.sh", "match": "requirements.txt",
                    "reason": "fixture reason"}]})
    rc, report = check(root)
    assert rc == 0, f"an exempt entry must not fail the check\n{report}"
    assert report["exempt"] and report["exempt"][0]["reason"] == "fixture reason", \
        f"an applied exception must be REPORTED, not swallowed\n{report}"
    assert not report["problems"], report

with tempfile.TemporaryDirectory() as td:
    root = build(td, seed, scope={
        "handoff": [{"path": "bin/seed.sh", "match": "requirements.txt",
                     "reason": "owned by the CI owner"}]})
    rc, report = check(root)
    assert rc == 0, f"a handoff entry must not fail the check\n{report}"
    assert report["handoff"] and report["handoff"][0]["reason"] == "owned by the CI owner", \
        f"a handoff must be REPORTED\n{report}"

# An exception is scoped to its own path and its own line. A scope entry must
# never turn into a blanket amnesty for a file.
with tempfile.TemporaryDirectory() as td:
    root = build(td, {"bin/seed.sh": "#!/bin/sh\npip install -r requirements.txt\nnpm install\n"},
                 scope={"exempt": [{"path": "bin/seed.sh", "match": "requirements.txt",
                                    "reason": "only the pip line"}]})
    rc, report = check(root)
    assert rc == 1, "a match-scoped exception must not forgive the whole file"
    assert any("npm install" in p["text"] for p in report["problems"]), report

with tempfile.TemporaryDirectory() as td:
    root = build(td, {"bin/seed.sh": "#!/bin/sh\npip install -r requirements.txt\n",
                      "bin/other.sh": "#!/bin/sh\npip install -r requirements.txt\n"},
                 scope={"exempt": [{"path": "bin/seed.sh", "reason": "one file only"}]})
    rc, report = check(root)
    assert rc == 1, "an exception must not reach a different path"
    assert paths(report) == {"bin/other.sh"}, report

# ---- 5. THE REAL TREE. It must RUN here — a checker that crashes on the repo
# it ships in is worse than none — and every exception it applies must carry a
# reason.
#
# WHAT THIS DELIBERATELY DOES NOT ASSERT is that the real tree has zero
# findings. ops/ci.sh runs this checker in SHADOW: it reports and does not
# block, because `ops/ci.sh --strict` is the required status check on main and
# slice R05 is not authorised to activate a required hosted gate. Asserting
# tree-green HERE would activate it through the back door — the gates class
# would go red the day someone lands an unfrozen install — which is exactly the
# hand-off boundary this slice was told to honour. The current findings are
# reported below and in the shadow line on every CI run.
proc = subprocess.run([sys.executable, str(CHECK), "--root", str(ROOT), "--json"],
                      capture_output=True, text=True, env=FIXTURE_ENV)
assert proc.returncode in (0, 1), \
    f"frozen-install-check CRASHED on this tree (rc={proc.returncode}):\n{proc.stderr}"
live = json.loads(proc.stdout)
assert live["scanned"] > 100, f"scanned only {live['scanned']} files — the roots look wrong"
for entry in live["handoff"] + live["exempt"]:
    assert entry["reason"].strip(), f"an exception with no reason: {entry}"

print("frozen-install-check-selftest: "
      f"{len(SEEDS)} seeded violations caught, {len(FROZEN)} frozen/near-miss shapes clean, "
      f"exceptions announced ({len(live['handoff'])} handoff, {len(live['exempt'])} exempt); "
      f"real tree today: {len(live['problems'])} unfrozen install(s) [shadow, not blocking]")
