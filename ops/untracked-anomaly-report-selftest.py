#!/usr/bin/env python3
"""untracked-anomaly-report-selftest.py — fixtures for
ops/untracked-anomaly-report.py.

R02 PROPOSED. Two things get proven here, and the second matters more than the
first.

  1. IT CLASSIFIES CORRECTLY. Built against a real git repo in a private
     mkdtemp root with real untracked files, some under approved roots and some
     not, so the approved/anomaly split is measured rather than asserted.

  2. IT DOES NOT PAGE. "Non-paging" is the whole design contract of this
     report and it is exactly the property that erodes quietly — somebody adds
     "just one" alert for the case that finally bit them, and a review artifact
     becomes another alarm nobody trusts. So it is tested from two directions:
     BEHAVIOURAL (exit 0 with anomalies present; clean stderr) and STRUCTURAL
     (the source names no alarm channel at all). The structural half is
     deliberately crude — a substring scan — because it fails loudly the moment
     someone adds the import, which is when the conversation should happen.
"""
import json
import os
import shutil
import subprocess
import subprocess as sp
import sys
import tempfile

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
# ops/git-isolation-check: confine git in the fixture builder (see the
# canonical-edit-gate selftest for the same guard and why).
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from git_env import fixture_env  # noqa: E402
FIXTURE_ENV = fixture_env()
TOOL = os.path.join(REPO, "ops", "untracked-anomaly-report.py")

# Every way this repo raises an alarm. None of them belongs in a review report.
ALARM_CHANNELS = [
    "report-problem", "open-incident", "record-defect", "add-loop",
    "osascript", "terminal-notifier", "notify-send",
    "run.sh call", "sys.exit(1)", "sys.exit(2)", "exit 1",
]

RESULTS: list[dict] = []


def check(name, want, got, detail=""):
    ok = want == got
    RESULTS.append({"case": name, "want": want, "got": got, "ok": ok})
    print(f"  {'ok  ' if ok else 'FAIL'} {name:44} want={want!r:<8} got={got!r}"
          + (f"   [{detail}]" if detail else ""))
    return ok


def run(cmd, cwd):
    sp.run(cmd, cwd=cwd, check=True, env=FIXTURE_ENV, stdout=sp.DEVNULL, stderr=sp.DEVNULL)


def build_fixture():
    root = os.path.realpath(tempfile.mkdtemp(prefix="untracked-anomaly-fixture-"))
    os.makedirs(os.path.join(root, "ops", "config"))
    with open(os.path.join(root, "tracked.txt"), "w") as fh:
        fh.write("tracked\n")
    with open(os.path.join(root, ".gitignore"), "w") as fh:
        fh.write("ignored-entirely/\n")
    # The approved-roots config is TRACKED in the fixture, as it is in the
    # repo. An untracked config would show up as an anomaly under ops/ and the
    # counts below would be measuring the fixture instead of the tool.
    cfg = {"structural": [{"root": "out/", "why": "receipts, load-bearing"}],
           "ruled": []}
    cfg_path = os.path.join(root, "ops", "config",
                            "untracked-approved-roots.json")
    with open(cfg_path, "w") as fh:
        json.dump(cfg, fh)

    run(["git", "init", "-q", "-b", "main"], root)
    run(["git", "config", "user.email", "selftest@example.invalid"], root)
    run(["git", "config", "user.name", "selftest"], root)
    run(["git", "add", "tracked.txt", ".gitignore",
         "ops/config/untracked-approved-roots.json"], root)
    run(["git", "commit", "-qm", "fixture"], root)

    # approved root, present and untracked
    os.makedirs(os.path.join(root, "out"))
    with open(os.path.join(root, "out", "receipt.json"), "w") as fh:
        fh.write("{}\n")
    # gitignored entirely — must not appear anywhere in the report
    os.makedirs(os.path.join(root, "ignored-entirely"))
    with open(os.path.join(root, "ignored-entirely", "x"), "w") as fh:
        fh.write("x\n")
    # three anomalies: two temp roots and a loose file
    for d in ("tmpaaaa", "tmpbbbb"):
        os.makedirs(os.path.join(root, d))
        with open(os.path.join(root, d, "junk"), "w") as fh:
            fh.write("junk\n")
    with open(os.path.join(root, "loose-proposal.txt"), "w") as fh:
        fh.write("loose\n")

    return root, cfg_path


def main():
    if not os.path.exists(TOOL):
        print(f"FAIL: tool not found at {TOOL}")
        return 1

    root, cfg = build_fixture()
    try:
        out_json = os.path.join(root, "out", "report.json")
        out_md = os.path.join(root, "out", "report.md")
        proc = subprocess.run(
            [sys.executable, TOOL, "--repo", root, "--config", cfg,
             "--out-json", out_json, "--out-md", out_md, "--json"],
            capture_output=True, text=True, timeout=180)

        # --- it does not page ------------------------------------------------
        check("exit-0-even-with-anomalies", 0, proc.returncode,
              "a non-zero exit is how a scheduled job becomes an alarm")
        check("stderr-is-clean", "", proc.stderr.strip())

        doc = json.loads(proc.stdout[proc.stdout.index("{"):])

        # --- it classifies ---------------------------------------------------
        anomaly_paths = sorted(a["path"] for a in doc["anomalies"])
        check("anomaly-count", 3, doc["counts"]["anomalies"],
              str(anomaly_paths))
        check("loose-file-is-an-anomaly", True,
              "loose-proposal.txt" in anomaly_paths)
        check("temp-roots-are-anomalies", 2,
              len([p for p in anomaly_paths if p.startswith("tmp")]))
        check("approved-root-is-not-an-anomaly", True,
              not any(p.startswith("out/") for p in anomaly_paths))
        check("approved-root-is-counted-as-approved", 1,
              doc["counts"]["approved"])
        check("gitignored-path-never-appears", True,
              "ignored-entirely" not in json.dumps(doc))
        check("tracked-file-never-appears", True,
              "tracked.txt" not in json.dumps(doc))

        # --- artifacts -------------------------------------------------------
        check("writes-markdown-artifact", True, os.path.exists(out_md))
        check("writes-json-artifact", True, os.path.exists(out_json))
        md = open(out_md).read()
        check("markdown-states-it-pages-nobody", True, "pages nobody" in md)

        # --- a broken config reports MORE, never silently less ----------------
        proc2 = subprocess.run(
            [sys.executable, TOOL, "--repo", root,
             "--config", os.path.join(root, "does-not-exist.json"),
             "--out-json", out_json, "--out-md", out_md, "--json"],
            capture_output=True, text=True, timeout=180)
        check("missing-config-still-exits-0", 0, proc2.returncode)
        doc2 = json.loads(proc2.stdout[proc2.stdout.index("{"):])
        check("missing-config-approves-nothing", 4, doc2["counts"]["anomalies"],
              "the out/ receipt joins the list rather than the list emptying")

        # --- structural: no alarm channel exists in the source ---------------
        src = open(TOOL).read()
        # the docstring names these channels in order to disclaim them; scan the
        # code only.
        code = src.split('"""', 2)[-1]
        present = [c for c in ALARM_CHANNELS if c in code]
        check("source-names-no-alarm-channel", [], present,
              "non-paging is a contract, not a default")
    finally:
        shutil.rmtree(root, ignore_errors=True)

    passed = sum(1 for r in RESULTS if r["ok"])
    print()
    print(f"untracked-anomaly-report-selftest: {passed}/{len(RESULTS)} passed")
    bad = [r["case"] for r in RESULTS if not r["ok"]]
    if bad:
        print("FAILURES: " + ", ".join(bad))
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
