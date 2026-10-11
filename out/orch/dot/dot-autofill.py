#!/usr/bin/env python3
"""Refill idle Dot capacity with paid-token-saving research, then routed PR reviews.

Explicit PR reviews use bin/dot-review.py. Idle research rotates oldest eligible
source audits, territory refreshes and vertical studies; reports expire after
14 days. Pending jobs are skipped and there is no daily cap.
"""
import fcntl
import json, re, subprocess
from datetime import datetime, timedelta
import sys
from pathlib import Path

import runpy

REVIEW = runpy.run_path(str(Path(__file__).resolve().parents[3] / "bin/dot-review.py"))
atomic_brief = REVIEW["atomic_brief"]

D = Path(__file__).resolve().parent
REPOS = ["jbookout/carr-system", "jbookout/doctorcre-app"]
HEADER = ("Rules, all required: 1. Read-only, public web and public GitHub only. 2. Never start or "
          "delegate a Codex task. 3. Post your answer only in this Slack thread. 4. Finish with a final "
          "line that is exactly DOT-REPORT-END, one space, and this job's name (the word after JOB "
          "above), outside any code block. Either that or the bare marker is accepted, so it never "
          "conflicts with your saved task.")
# The Dot's saved ChatGPT task asks for "DOT-REPORT-END <job>" while briefs said the bare marker; on
# 2026-10-02 the Dot stopped mid-job (triage-06) to ask which one. dot-thread-age.py accepts both.

def query_prs(repo):
    r = subprocess.run(["gh", "pr", "list", "-R", repo, "--state", "open", "--limit", "30",
                        "--json", "number,title,headRefOid,isDraft"], capture_output=True, text=True, timeout=60)
    return json.loads(r.stdout or "[]") if r.returncode == 0 else []


AUDIT_DIRS = ("hooks", "ops", "lib", "tools", "mcp-server/src")
JOB_NAME = re.compile(r"^\d+-([BMV])-(.+)$")
REFRESH_AGE = timedelta(days=14).total_seconds()


def job_key(path):
    name = path.name[:-8] if path.name.endswith(".partial") else path.name
    match = JOB_NAME.fullmatch(Path(name).stem)
    return f"{match[1]}-{match[2]}" if match else None


def research_task(root, kind, slug):
    # Preserve the sent brief's task verbatim, replacing only its obsolete header/job prefix.
    templates = [p for p in (root / "sent").glob("*.md") if job_key(p) == f"{kind}-{slug}"]
    for path in sorted(templates, key=lambda p: (p.stat().st_mtime, p.name), reverse=True):
        _, separator, task = path.read_text().partition(" Task: ")
        if separator and task.strip():
            return task.strip()
    if kind == "M":
        return ("public market research for medical office and healthcare retail space in the "
                f"{slug.replace('-', ' ')} metro: current vacancy and asking rents for medical office "
                "(with source and date), notable new construction or hospital-system expansions, "
                "population and household-income growth, payer mix and health-system landscape, "
                "healthcare employment trends, and three sourced commentary lines a broker could cite "
                "in a client packet. Cite every figure with source and date; flag anything older than 2025.")
    return (f"deep research for a healthcare tenant brokerage on the {slug.replace('-', ' ')} vertical "
            "in the US Southeast: typical space size range, build-out cost per square foot and the "
            "main cost drivers, plumbing/electrical/imaging/specialty requirements, zoning and "
            "certificate-of-need issues, parking ratios, visibility and co-tenancy preferences, typical "
            "lease terms and TI allowances, buy-versus-lease patterns, consolidation and private-equity "
            "activity, and the five questions a broker should ask a practice owner in this vertical "
            "before touring. Cite sources with dates; mark inferences; flag any figure older than 2024.")


def backlog(root, now):
    # Serialize selection and ledger append across concurrent autofill processes.
    with (root / "autofill-backlog.txt").open("a+") as ledger:
        fcntl.flock(ledger, fcntl.LOCK_EX)
        ledger.seek(0)
        picks = [json.loads(line) for line in ledger if line.strip()]
        last_pick = {}
        for pick in picks:
            stamp = datetime.fromisoformat(pick["at"]).timestamp()
            last_pick[pick["key"]] = max(last_pick.get(pick["key"], 0), stamp)
        reports = {}
        for path in (root / "reports").glob("*.txt"):
            key = job_key(path)
            if key and path.stat().st_size:
                reports[key] = max(reports.get(key, 0), path.stat().st_mtime)
        pending = set()
        for folder in ("queue", "sent", "claim"):
            for path in (root / folder).glob("*.md"):
                report = root / "reports" / (path.stem + ".txt")
                if folder != "sent" or not report.exists() or not report.stat().st_size:
                    pending.add(job_key(path))
        markets = [s.strip() for s in (root / "territory.txt").read_text().splitlines()
                   if s.strip() and not s.lstrip().startswith("#")]
        inventory = list((root / "reports").glob("*.txt"))
        inventory += list((root / "reports").glob("*.txt.partial"))
        inventory += list((root / "sent").glob("*.md"))
        verticals = {key[2:] for p in inventory
                     if (key := job_key(p)) and key.startswith("V-")}
        rotation = [("B", directory.replace("/", "-")) for directory in AUDIT_DIRS]
        rotation += [("M", slug) for slug in dict.fromkeys(markets)]
        rotation += [("V", slug) for slug in sorted(verticals)]
        candidates = []
        for order, (kind, slug) in enumerate(rotation):
            key = f"{kind}-{slug}"
            if key in pending:
                continue
            reported = reports.get(key, 0)
            if kind != "B" and reported and now.timestamp() - reported <= REFRESH_AGE:
                continue
            candidates.append((max(reported, last_pick.get(key, 0)), order, kind, slug))
        if not candidates:
            return 0
        _, _, kind, slug = min(candidates)
        key = f"{kind}-{slug}"
        if kind == "B":
            directory = AUDIT_DIRS[[d.replace("/", "-") for d in AUDIT_DIRS].index(slug)]
            task = ("read-only bug audit of current main of https://github.com/jbookout/carr-system "
                    f"limited to {directory}/. Record the main commit SHA you read and inspect that "
                    "directory at that commit. For each defect, give file and line, the input that "
                    "triggers it, the wrong result, severity (blocks merge, should fix, nice to have), "
                    "and a one-line fix. Cite source links at the inspected SHA; mark inferences and "
                    "say plainly if no defects are found. Public source only; encrypted stores, key "
                    "material, credential paths and production data dumps are out of scope.")
        else:
            task = research_task(root, kind, slug)
        stamp = now
        while True:
            name = f"{stamp:%m%d%H%M}-{key}.md"
            if not any((root / folder / name).exists() for folder in ("queue", "sent", "claim")):
                break
            stamp += timedelta(minutes=1)
        atomic_brief(root / "queue" / name, f"[orch] JOB {key}. {HEADER} Task: {task}\n")
        ledger.write(json.dumps({"at": now.isoformat(), "key": key, "name": name[:-3]}) + "\n")
        return 1


def autofill(root=D, now=None, list_prs=query_prs):
    """Idle reviews use the same routing, evidence and deduplication as explicit jobs."""
    import runpy
    review = runpy.run_path(str(Path(__file__).resolve().parents[3] / "bin/dot-review.py"))
    written = 0
    for repo in REPOS:
        for pr in list_prs(repo):
            if pr["isDraft"]:
                continue
            try:
                receipt = review["request"](repo, pr["number"], orch=root.parent, idle=True)
                written += int(receipt.get("seat") == "dot" and receipt.get("enqueued") is True and Path(receipt["brief"]).exists())
            except (OSError, ValueError, subprocess.SubprocessError) as exc:
                print(f"review routing failed for {repo}#{pr['number']}: {type(exc).__name__}", file=sys.stderr)
    return written


def selftest():
    """No network, feeder, or live queue writes: all fixtures stay in a temp directory."""
    import os
    import tempfile
    import unittest
    from datetime import timedelta

    class RefillTests(unittest.TestCase):
        def setUp(self):
            self.temp = tempfile.TemporaryDirectory(prefix="autofill-test-", dir=D)
            self.addCleanup(self.temp.cleanup)
            self.root = Path(self.temp.name)
            self.now = datetime(2026, 10, 3, 13, 45)
            for name in ("queue", "sent", "claim", "reports"):
                (self.root / name).mkdir()
            (self.root / "territory.txt").write_text("# territory\nMobile-AL\nPensacola-FL\n")

        def run_fill(self, prs=None, now=None):
            return backlog(self.root, now or self.now)

        def report(self, name, age):
            path = self.root / "reports" / (name + ".txt")
            path.write_text("completed report\n")
            stamp = (self.now - timedelta(days=age)).timestamp()
            os.utime(path, (stamp, stamp))

        def complete(self):
            brief, = (self.root / "queue").glob("*.md")
            brief.rename(self.root / "sent" / brief.name)
            self.report(brief.stem, 0)
            return brief.name

        def picked(self):
            brief, = (self.root / "queue").glob("*.md")
            return brief

        def age_audits(self):
            with (self.root / "autofill-backlog.txt").open("a") as out:
                for slug in ("hooks", "ops", "lib", "tools", "mcp-server-src"):
                    out.write(json.dumps({"at": (self.now - timedelta(days=1)).isoformat(),
                                          "key": "B-" + slug,
                                          "name": "10020000-B-" + slug}) + "\n")

        def test_backlog_only_on_zero_and_one_pick(self):
            self.assertEqual(self.run_fill(), 1)
            self.assertEqual(self.picked().name, "10031345-B-hooks.md")
            rows = (self.root / "autofill-backlog.txt").read_text().splitlines()
            self.assertEqual(len(rows), 1)
            self.assertEqual(json.loads(rows[0])["key"], "B-hooks")
            self.assertTrue(self.picked().read_text().startswith("[orch] JOB B-hooks. " + HEADER))

        def test_skip_if_pending_in_every_folder(self):
            for folder in ("queue", "sent", "claim"):
                with self.subTest(folder=folder):
                    pending = self.root / folder / "09010000-B-hooks.md"
                    pending.write_text("pending")
                    self.assertEqual(backlog(self.root, self.now), 1)
                    self.assertIn("-B-ops", [p.name for p in (self.root / "queue").glob("*.md") if p != pending][0])
                    # Reset only these disposable fixtures.
                    for p in (self.root / "queue").glob("*.md"):
                        p.unlink()
                    if pending.exists():
                        pending.unlink()
                    (self.root / "autofill-backlog.txt").unlink()

        def test_no_daily_cap(self):
            # Joe 2026-10-08: no daily cap; a busy day must not idle the Dot.
            with (self.root / "autofill-backlog.txt").open("a") as out:
                for i in range(100):
                    out.write(json.dumps({"at": self.now.isoformat(), "key": f"B-old{i}", "name": f"x-B-old{i}"}) + "\n")
            self.assertEqual(self.run_fill(), 1)

        def test_oldest_first_and_newest_report(self):
            self.age_audits()
            self.report("020-M-Mobile-AL", 60)
            self.report("09010000-M-Mobile-AL", 16)
            self.report("020-M-Pensacola-FL", 20)
            self.report("010-V-optometry", 25)
            self.assertEqual(self.run_fill(), 1)
            self.assertIn("-V-optometry", self.picked().name)
            self.complete()
            self.assertEqual(self.run_fill(), 1)
            self.assertIn("-M-Pensacola-FL", self.picked().name)
            self.complete()
            self.assertEqual(self.run_fill(), 1)
            self.assertIn("-M-Mobile-AL", self.picked().name)

        def test_missing_report_and_pending_refresh(self):
            self.age_audits()
            # Missing reports win over dated reports; unfinished sent work remains pending.
            (self.root / "sent" / "020-M-Mobile-AL.md").write_text("pending")
            (self.root / "sent" / "010-V-optometry.md").write_text("pending")
            self.report("010-V-optometry", 30)
            (self.root / "claim" / "10010000-V-optometry.md").write_text("pending")
            self.assertEqual(self.run_fill(), 1)
            self.assertIn("-M-Pensacola-FL", self.picked().name)

        def test_fourteen_day_boundary_and_partial_report(self):
            self.age_audits()
            self.report("020-M-Mobile-AL", 14)
            self.report("020-M-Pensacola-FL", 13)
            partial = self.root / "reports" / "010-V-optometry.txt.partial"
            partial.write_text("unfinished")
            self.assertEqual(self.run_fill(), 1)
            self.assertIn("-V-optometry", self.picked().name)
            self.complete()
            # A partial report supplies the study name, but never marks a refresh complete.
            self.assertEqual(self.run_fill(), 1)
            self.assertIn("-B-hooks", self.picked().name)

        def test_rotation_and_same_minute_no_overwrite(self):
            self.report("020-M-Mobile-AL", 0)
            self.report("020-M-Pensacola-FL", 0)
            for slug in ("hooks", "ops", "lib", "tools", "mcp-server-src", "hooks"):
                self.assertEqual(self.run_fill(), 1)
                self.assertIn("-B-" + slug, self.picked().name)
                self.complete()
            self.assertEqual(len(list((self.root / "sent").glob("*.md"))), 6)

        def test_research_brief_style_and_territory(self):
            self.age_audits()
            self.report("020-M-Mobile-AL", 30)
            self.report("020-M-Pensacola-FL", 1)
            template = "public market research for medical office in the Mobile AL metro: cite every figure."
            (self.root / "sent" / "020-M-Mobile-AL.md").write_text("[orch] JOB M-Mobile-AL. old header Task: " + template)
            self.assertEqual(self.run_fill(), 1)
            self.assertEqual(self.picked().name, "10031345-M-Mobile-AL.md")
            self.assertEqual(self.picked().read_text(), "[orch] JOB M-Mobile-AL. " + HEADER + " Task: " + template + "\n")
            self.complete()
            vertical = "deep research on the optometry vertical: cite sources with dates."
            (self.root / "sent" / "010-V-optometry.md").write_text("[orch] JOB V-optometry. old Task: " + vertical)
            self.report("010-V-optometry", 31)
            self.assertEqual(self.run_fill(), 1)
            self.assertEqual(self.picked().read_text(), "[orch] JOB V-optometry. " + HEADER + " Task: " + vertical + "\n")

    suite = unittest.defaultTestLoader.loadTestsFromTestCase(RefillTests)
    result = unittest.TextTestRunner(stream=sys.stdout, verbosity=2).run(suite)
    if not result.wasSuccessful():
        raise SystemExit(1)
    print("Sample generated brief: 10031345-B-hooks.md (temp fixture)")


if __name__ == "__main__":
    if sys.argv[1:] == ["--selftest"]:
        selftest()
    else:
        # Research/build support remains idle priority; explicit reviews use the shared router.
        print(backlog(D, datetime.now()) or autofill())
