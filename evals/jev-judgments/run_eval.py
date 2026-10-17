#!/usr/bin/env python3
"""Paid-call admission eval for the jev-judgments surface (2026-10-04 audit).

THE FLOW MEASURED: a would-be paid Jev call reaches ops/typesafe_client.py.
Does the client pay for it? The baseline arm is the client at the merge base
(daily cap only); the candidate arm is this tree's client (call-site registry,
attribution, unattended policy, fixture/CI refusal, site budgets). Both arms
run the REAL ask() with the vendor transport replaced by a recorder, so the
observation is simply whether a request would have left the machine.

CASES. `--build` samples production traces from out/jev-calls.jsonl
(2026-09-28..10-04, every non-cache attempt with a caller), stratified by the
call site inferred from the hashed question ids, and adds hand-written hard
cases judged during the audit. Labels encode the audit's per-site decisions,
written here independently of ops/config/jev-call-sites.v1.json so a registry
mistake shows up as a failed case rather than a self-fulfilling label:

  should_not_fire when the call came from a selftest fixture (its prompt was
  seen in three or more sessions) or CI, from a site the audit removed
  (command_precheck: deterministic now; jev_build_advisory: no live caller),
  lacks the attribution its site requires, or ran in an unattended worker at a
  site whose verdict nobody consumes.

`--report` runs both arms over expectations.v2.json and prints the measured comparison. No model is called and nothing is sent anywhere.
"""

import argparse
import collections
import contextlib
import hashlib
import importlib.util
import json
import os
import random
import subprocess
import sys
import tempfile
from datetime import date
from pathlib import Path
from unittest import mock

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]
sys.path.insert(0, str(REPO / "tools" / "room-bridge"))
import evaluation_kernel as kernel

EXPECTATIONS = HERE / "expectations.v2.json"
VERSION = "jev-judgments-expectations/v2"
WINDOW = ("2026-09-28", "2026-10-05")
PER_SITE = 14
SEED = 11
PRICE_PER_CALL_USD = 0.042 / 1_000_000 * 600  # cost guard price x a typical 600-token request

QUESTION_SITES = (  # question id -> site, from the code that asks it
    ({"undeclared_option", "bad_import", "missing_path", "guard_refusal", "wrong_interface",
      "direct_model_work"}, "command_precheck"),
    ({"instructs", "exceeds", "failure_class", "intended_path", "bug_frame", "duplicate",
      "test_asserts_behavior", "stuck_in_loop", "drifted_from_task"}, "jev_session_watch"),
    ({"claim_scope", "claims_supported", "evidence_shows_omitted_failure", "omitted_failure"},
     "jev_done_checks"),
    ({"binds"}, "jev_rule_select"),
    ({"rank"}, "rule_trigger_delivery"),
    ({"hands_off"}, "jev_handoff"),
    ({"pick"}, "jev_defect_class"),
    ({"tier"}, "jev_executor_tier"),
    ({"completion"}, "headless_tasks"),
)
PREFIX_SITES = (("bind_", "rule_trigger_delivery"), ("req_", "jev_requirements"),
                ("risk_", "jev_done_checks"), ("test_", "jev_session_watch"))
DIRECT_CALLERS = {"jev_change_tolls", "jev_code_review", "jev_build_advisory", "jev_deal_read"}

# Independent policy oracle: mechanical callers were disabled by #1529;
# retained callers were reviewed in #1531, #1543, #1545 and #1576. Do not
# derive these labels from the registry being tested. The v1 labels stay frozen.
REMOVED = {"command_precheck", "jev_build_advisory", "rule_trigger_delivery",
           "jev_session_watch", "jev_defect_class", "jev_executor_tier", "jev_code_review"}
JOB_ATTRIBUTED = {"seat_health", "jev_model_route", "jev_deal_read", "post_call_jev"}
UNATTENDED_OK = JOB_ATTRIBUTED | {"adhoc:", "jevlint_review"}
KNOWN = {"jev_fact_boundary", "jev_intake", "jev_scorecard", "jev_best_of", "flash-script",
         "rule_gold_label", "rule_delivery_eval", "judge_paired_eval", "rule_trigger_compile",
         "timebomb-audit", "jevlint_review"} | JOB_ATTRIBUTED

HARD_CASES = (  # (tag, input), judged during the audit
    ("adhoc", {"site": "adhoc:review-brief", "session": True, "worker": True}),
    ("stale_claim_judge", {"site": "stale_claim_judge", "session": True, "worker": True}),
    ("stale_claim_judge", {"site": "stale_claim_judge", "session": True}),
    ("jev_deal_read", {"site": "jev_deal_read", "session": False, "job": "com.carr.deal-room"}),
    ("jev_deal_read", {"site": "jev_deal_read", "session": False}),
    ("unregistered", {"site": "brand_new_burner", "session": True}),
    ("jev_requirements", {"site": "jev_requirements", "session": True, "worker": True}),
    ("jev_change_tolls", {"site": "jev_change_tolls", "session": True, "worker": True}),
    ("ci", {"site": "jev_handoff", "session": True, "ci": True}),
    ("slice-done-marker", {"site": "slice-done-marker", "session": False,
                           "carr_job": "release-pipeline.slice-marker"}),
    ("rule_trigger_delivery", {"site": "rule_trigger_delivery", "session": True}),
    ("rule_trigger_delivery", {"site": "rule_trigger_delivery", "session": True, "worker": True}),
    ("jev_build_advisory", {"site": "jev_build_advisory", "session": True}),
    ("jev_executor_tier", {"site": "jev_executor_tier", "session": True}),
) + tuple((site, {"site": site, "session": True, "worker": worker})
          for site in sorted(KNOWN) for worker in (False, True))


def label(case):
    site = case["site"]
    family = "adhoc:" if site.startswith("adhoc:") and len(site) > 6 else site
    if case.get("fixture") or case.get("ci"):
        return True
    if family in REMOVED or (family not in KNOWN and family != "adhoc:"):
        return True
    if not case.get("session") and not (family in JOB_ATTRIBUTED and (case.get("job") or case.get("carr_job"))):
        return True
    return bool(case.get("worker")) and family not in UNATTENDED_OK


def _site_of(row, names):
    if row["caller"] in DIRECT_CALLERS:
        return row["caller"]
    if row["caller"] != "jev_judge":
        return None
    qs = {names.get(h) for h in row.get("question_ids_sha256") or []} - {None}
    for ids, site in QUESTION_SITES:
        if qs & ids:
            return site
    for prefix, site in PREFIX_SITES:
        if any(q.startswith(prefix) for q in qs):
            return site
    return None


def build(log_path):
    names = {}
    for ids, _ in QUESTION_SITES:
        for q in ids:
            names[hashlib.sha256(q.encode()).hexdigest()] = q
    for prefix, _ in PREFIX_SITES:
        for i in range(60):
            names[hashlib.sha256(f"{prefix}{i}".encode()).hexdigest()] = f"{prefix}{i}"
    for q in ("tier", "completion"):
        names[hashlib.sha256(q.encode()).hexdigest()] = q
    rows, sessions = [], collections.defaultdict(set)
    with open(log_path, encoding="utf-8") as fh:
        for line in fh:
            try:
                row = json.loads(line)
            except ValueError:
                continue
            if not (WINDOW[0] <= row.get("ts", "") < WINDOW[1]) or not row.get("caller") or row.get("cache_hit"):
                continue
            rows.append(row)
            sessions[row.get("prompt_sha256")].add(row.get("session"))
    by_site = collections.defaultdict(list)
    for row in rows:
        site = _site_of(row, names)
        if site:
            by_site[site].append(row)
    cases = []
    for site in sorted(by_site):
        ranked = sorted(by_site[site], key=lambda r: hashlib.sha256(
            (r["ts"] + str(r.get("prompt_sha256"))).encode()).hexdigest())
        for row in ranked[:PER_SITE]:
            case = {"site": site, "session": bool(row.get("session")),
                    "fixture": len(sessions[row.get("prompt_sha256")]) >= 3,
                    "trace": {"ts": row["ts"], "prompt_sha256": (row.get("prompt_sha256") or "")[:16]}}
            cases.append(("production_trace", site, case))
    for tag, case in HARD_CASES:
        cases.append(("human_judged_hard_case", tag, dict(case)))
    rng = random.Random(SEED)
    by_tag = collections.defaultdict(list)
    for item in cases:
        by_tag[item[1]].append(item)
    labelled = {}
    for tag in sorted(by_tag):
        group = by_tag[tag]
        rng.shuffle(group)
        for n, (source, _, case) in enumerate(group):
            digest = hashlib.sha256(json.dumps(case, sort_keys=True).encode()).hexdigest()
            labelled[f"{tag}-{digest[:10]}"] = {
                "split": "test" if n % 2 == 0 else "train", "should_not_fire": label(case),
                "input_sha256": digest, "tag": tag, "source": source, "input": case}
    EXPECTATIONS.write_text(json.dumps({"version": VERSION, "cases": dict(sorted(labelled.items()))},
                                       indent=1, sort_keys=True) + "\n")
    print(f"wrote {len(labelled)} cases to {EXPECTATIONS.relative_to(REPO)}")


ARM_FILES = ("ops/typesafe_client.py", "ops/config/jev-cost-guard.v1.json",
             "ops/config/jev-call-sites.v1.json", "tools/judge/interface.py",
             "mcp-server/src/judge-providers.v1.json", "lib/jev_required_actions.py",
             "mcp-server/src/jev-request-contract.v1.json",
             "lib/transcript_read.py", "lib/__init__.py")


def _tree(ref, root):
    for rel in ARM_FILES:
        shown = subprocess.run(["git", "show", f"{ref}:{rel}"], cwd=REPO, capture_output=True)
        if shown.returncode == 0:
            (root / rel).parent.mkdir(parents=True, exist_ok=True)
            (root / rel).write_bytes(shown.stdout)
    return root


def _load(tree):
    spec = importlib.util.spec_from_file_location(f"eval_client_{abs(hash(str(tree)))}",
                                                  tree / "ops" / "typesafe_client.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class _Response:
    status = 200

    def __init__(self):
        self.body = json.dumps({"model": "jev-eval", "answers": {"q": {"type": "noul", "noul": 0.5}},
                                "usage": {"input_tokens": 600, "output_tokens": 10}}).encode()

    def read(self, *a):
        body, self.body = self.body, b""
        return body

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def observe(client, case, scratch):
    sent = []
    env = {k: v for k, v in os.environ.items() if not k.startswith(("CARR_JEV", "CARR_HOOK"))
           and k not in ("XPC_SERVICE_NAME", "CODEX_THREAD_ID", "CLAUDE_CODE_SESSION_ID",
                         "CLAUDE_CODE_HOST_SESSION_ID")}
    if case.get("fixture"):
        env["CARR_HOOK_FIXTURE"] = "1"
    if case.get("ci"):
        env["CARR_JEV_OFFLINE"] = "1"
    if case.get("worker"):
        env["CARR_JEV_WORKER"] = "off"
    if case.get("job"):
        env["XPC_SERVICE_NAME"] = case["job"]
    if case.get("carr_job"):
        env["CARR_JEV_JOB"] = case["carr_job"]
    log = scratch / "calls.jsonl"
    refusal = None
    with mock.patch.dict(os.environ, env, clear=True), \
            mock.patch.object(client, "JEV_DAILY_CAP_LOG", str(log)), \
            mock.patch.object(client, "_launch_spend_alert_worker", lambda *a: None, create=True), \
            mock.patch.object(client.urllib.request, "urlopen",
                              lambda request, timeout=None: sent.append(1) or _Response()):
        try:
            client.ask("eval state", {"q": client.noul("Eval judgment")}, api_key="eval-fixture",
                       calls_log=str(log), cache_ttl_seconds=0, retries=0, caller=case["site"],
                       session_id="eval-session" if case.get("session") else None)
        except Exception as exc:  # a refusal is the observation, not a crash
            refusal = getattr(exc, "code", None) or type(exc).__name__
    return {"paid": bool(sent), "refusal": refusal}


def _sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def report(base_ref):
    expectations = json.loads(EXPECTATIONS.read_text())
    cases = expectations["cases"]
    cohorts = {}
    with tempfile.TemporaryDirectory() as tmp:
        trees = {"baseline": _tree(base_ref, Path(tmp) / "baseline"), "candidate": REPO}
        for arm, tree in trees.items():
            client = _load(tree)
            rows = []
            for cid in sorted(cases):
                with tempfile.TemporaryDirectory() as scratch:
                    seen = observe(client, cases[cid]["input"], Path(scratch))
                rows.append({"case_id": cid, "split": cases[cid]["split"],
                             "input_sha256": cases[cid]["input_sha256"], **seen})
            cohorts[arm] = rows
            path = HERE / "runs" / f"{arm}.jsonl"
            path.parent.mkdir(exist_ok=True)
            path.write_text("".join(json.dumps(r, sort_keys=True) + "\n" for r in rows))
    spec = importlib.util.spec_from_file_location("jev_judgments_score", HERE / "score.py")
    scorer = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(scorer)
    first = scorer.score(expectations, cohorts["baseline"], cohorts["candidate"])
    print(json.dumps(first, indent=2))
    return 0


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--build", metavar="JEV_CALLS_LOG")
    parser.add_argument("--report", action="store_true")
    parser.add_argument("--base", default=None, help="baseline git ref (default: merge base with origin/main)")
    args = parser.parse_args(argv)
    if args.build:
        build(args.build)
    if args.report:
        base = args.base or subprocess.run(["git", "merge-base", "HEAD", "origin/main"], cwd=REPO,
                                           capture_output=True, text=True, check=True).stdout.strip()
        return report(base)
    return 0


if __name__ == "__main__":
    sys.exit(main())
