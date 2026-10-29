#!/usr/bin/env python3
"""ops/sync-skills-drift-selftest.py — the acceptance test for bin/sync-skills.sh's
drift report, and specifically for the half of it that used to be invisible.

THE DEFECT THIS EXISTS FOR (2026-08-21, defect class
`warning-surface-blind-to-the-direction-that-destroys-data`). The report built
its list from `rsync -rcn --delete --out-format='%n'`, which emits the paths the
apply would ADD and says nothing about the paths it would DELETE. The apply is
`rsync -rc --delete`, an exact mirror, so a file that existed only on the Drive
side was removed without ever appearing in the report that exists to warn you
first. It happened for real: the `surface-review` skill, hand-authored on Drive
2026-08-13, sat unmentioned in every drift listing for eight days and was one
`--apply` away from deletion. The script's closing advice — "if the repo side is
right (it should be, it is canon): ./bin/sync-skills.sh --apply" — read as safe
while it was not.

A drift report that is silent about deletion is worse than no report, because it
is consulted and believed.

NO MOCK. This drives the REAL bin/sync-skills.sh as a subprocess against a pair
of throwaway trees, using the CARR_SKILLS_DST seam the fix adds for exactly this
purpose. Nothing here touches the live Drive projection: every case builds its
own source and destination under a temporary directory and the destination path
is asserted to be inside it before the script is ever invoked.
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCRIPT = os.path.join(REPO, "bin", "sync-skills.sh")

failures: list[str] = []


import pathlib, sys  # noqa: E401
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))  # repo root, for lib/
from lib.gate_check import Check  # noqa: E402

check = Check(detail="   {detail}", failures=failures)


def build(tmp: str) -> tuple[str, str]:
    """A source tree and a destination tree, both with the two dirs the script mirrors."""
    src = os.path.join(tmp, "canon")
    dst = os.path.join(tmp, "projection")
    for root in (src, dst):
        os.makedirs(os.path.join(root, "skills"), exist_ok=True)
        os.makedirs(os.path.join(root, "agents"), exist_ok=True)
    # one file present and identical on both sides, so "identical" is a real state
    for root in (src, dst):
        os.makedirs(os.path.join(root, "skills", "shared-skill"), exist_ok=True)
        with open(os.path.join(root, "skills", "shared-skill", "SKILL.md"), "w") as fh:
            fh.write("same on both sides\n")
        with open(os.path.join(root, "agents", "shared-agent.md"), "w") as fh:
            fh.write("same on both sides\n")
    return src, dst


def run(src: str, dst: str, *args: str) -> subprocess.CompletedProcess:
    assert dst.startswith(tempfile.gettempdir()) or "/tmp" in dst or "/var/folders" in dst, \
        f"refusing to run against a destination outside a temp dir: {dst}"
    env = dict(os.environ, CARR_SKILLS_SRC=src, CARR_SKILLS_DST=dst)
    return subprocess.run(["/bin/zsh", SCRIPT, *args], capture_output=True, text=True, env=env)


if not os.path.exists(SCRIPT):
    sys.exit(f"sync-skills-drift-selftest: missing {SCRIPT}")

print("sync-skills-drift-selftest")

# ── 1. identical trees report identical and exit 0 ───────────────────────────
with tempfile.TemporaryDirectory() as tmp:
    src, dst = build(tmp)
    r = run(src, dst)
    check("identical trees report no drift", "identical" in r.stdout, r.stdout.strip()[:200])
    check("identical trees exit 0", r.returncode == 0, f"exit {r.returncode}")

# ── 2. a repo-only file is reported as an addition ───────────────────────────
with tempfile.TemporaryDirectory() as tmp:
    src, dst = build(tmp)
    os.makedirs(os.path.join(src, "skills", "repo-only-skill"))
    with open(os.path.join(src, "skills", "repo-only-skill", "SKILL.md"), "w") as fh:
        fh.write("canon has this, the projection does not\n")
    r = run(src, dst)
    check("a repo-only file appears in the report", "repo-only-skill" in r.stdout,
          r.stdout.strip()[:300])
    check("drift without --apply exits 1", r.returncode == 1, f"exit {r.returncode}")

# ── 3. THE REGRESSION: a Drive-only file is reported as a DELETION ───────────
#    This is the case that shipped broken. A file the apply would destroy must
#    be named, and named as a deletion rather than buried among the additions.
with tempfile.TemporaryDirectory() as tmp:
    src, dst = build(tmp)
    os.makedirs(os.path.join(dst, "skills", "drive-only-skill"))
    with open(os.path.join(dst, "skills", "drive-only-skill", "SKILL.md"), "w") as fh:
        fh.write("hand-authored on the projection side, in no commit anywhere\n")
    r = run(src, dst)
    out = r.stdout
    check("a Drive-only file appears in the report at all", "drive-only-skill" in out,
          out.strip()[:400])
    check("it is labelled as a deletion, not an addition",
          "DELETE" in out.upper() and "drive-only-skill" in out, out.strip()[:400])
    check("the report says the apply would destroy it",
          any(w in out.lower() for w in ("delete", "destroy", "remove")), out.strip()[:400])
    check("a deletion-only difference still exits 1", r.returncode == 1, f"exit {r.returncode}")

# ── 4. the report told the truth: --apply really does delete it ──────────────
#    Ties the warning to the behaviour. If this ever stops deleting, the report
#    above becomes a false alarm and this test says so.
with tempfile.TemporaryDirectory() as tmp:
    src, dst = build(tmp)
    doomed_dir = os.path.join(dst, "skills", "drive-only-skill")
    os.makedirs(doomed_dir)
    doomed = os.path.join(doomed_dir, "SKILL.md")
    with open(doomed, "w") as fh:
        fh.write("about to be mirrored away\n")
    r = run(src, dst, "--apply")
    check("--apply reports that it applied", "applied" in r.stdout.lower(), r.stdout.strip()[:200])
    check("--apply exits 0", r.returncode == 0, f"exit {r.returncode}")
    check("the Drive-only file is gone after --apply, as the report warned",
          not os.path.exists(doomed))
    check("the shared file survived --apply",
          os.path.exists(os.path.join(dst, "agents", "shared-agent.md")))

# ── 5. the live projection is never the target of a test run ─────────────────
with open(SCRIPT) as fh:
    body = fh.read()
check("the destination is overridable rather than hardcoded",
      "CARR_SKILLS_DST" in body,
      "sync-skills.sh must read CARR_SKILLS_DST so this suite can never touch the real Drive tree")

# ── 6. _to_delete staging is OUTSIDE the mirror entirely (Joe's ruling, 2026-08-21) ──
#    _to_delete is where a candidate deletion is PARKED for a human ruling rather
#    than removed, so the mirror has no business touching it in either direction:
#    it must not push canon's staging onto Drive, and it must not destroy Drive's.
#    The real case is nested — write-content/graphics/_to_delete/ — so the
#    exclusion is tested at depth rather than only at the top level.
with tempfile.TemporaryDirectory() as tmp:
    src, dst = build(tmp)

    src_staged = os.path.join(src, "skills", "shared-skill", "graphics", "_to_delete")
    os.makedirs(src_staged)
    with open(os.path.join(src_staged, "parked-in-canon.b64"), "w") as fh:
        fh.write("staged on the repo side, awaiting a human ruling\n")

    dst_staged = os.path.join(dst, "skills", "shared-skill", "graphics", "_to_delete")
    os.makedirs(dst_staged)
    dst_parked = os.path.join(dst_staged, "parked-on-drive.b64")
    with open(dst_parked, "w") as fh:
        fh.write("staged on the projection side, awaiting a human ruling\n")

    r = run(src, dst)
    check("staging folders are absent from the drift report entirely",
          "_to_delete" not in r.stdout, r.stdout.strip()[:400])
    check("two trees differing ONLY inside staging read as identical",
          "identical" in r.stdout and r.returncode == 0,
          f"exit {r.returncode}: {r.stdout.strip()[:300]}")

    a = run(src, dst, "--apply")
    check("--apply leaves a staged file on Drive alone",
          os.path.exists(dst_parked),
          "a parked candidate deletion must survive the mirror")
    check("--apply does not push canon's staging onto Drive",
          not os.path.exists(os.path.join(dst_staged, "parked-in-canon.b64")),
          "staging is local to the side it was staged on")
    check("--apply still mirrors everything outside staging",
          os.path.exists(os.path.join(dst, "agents", "shared-agent.md")),
          f"exit {a.returncode}")

check("the exclusion is declared in the script rather than implied",
      "_to_delete" in body and "--exclude" in body,
      "sync-skills.sh must pass --exclude for _to_delete on every rsync it runs")

print()
if failures:
    print(f"sync-skills-drift-selftest: {len(failures)} FAILED — " + "; ".join(failures))
    sys.exit(1)
print("sync-skills-drift-selftest: all checks passed")
