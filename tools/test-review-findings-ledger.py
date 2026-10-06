#!/usr/bin/env python3
"""test-review-findings-ledger.py — offline proof for review carry-forward
and the findings ledger (pipelines/review_findings_ledger.py, wired into
pipelines/run_codex_review.py). Sibling of tools/test-review-council.py and
collected the same way (ops/ci.sh's `tools/test-*.py` glob, gates class).

What it proves, in order:
  1. BACKWARD COMPATIBILITY. A request with no PR identity renders the exact
     prompt and the exact record-finding payload the runner produced before
     this build (sha256 pins captured from the unmodified file on
     2026-09-30), validates as before, and posts exactly one row per
     reviewer, with no disposition rows.
  2. Request schema for carry-forward: good shapes pass, and every malformed
     prior-finding shape fails VISIBLY (a RequestError naming the field, and
     through process_request a failed status sidecar), never silently.
  3. Prompt injection: open prior findings reach every reviewer's prompt with
     the classification instructions; closed ones (fixed, dismissed,
     accepted_risk) do not.
  4. The disposition vocabulary is exactly fixed / dismissed / accepted_risk /
     still_present, and the reason rule is enforced (dismissed and
     accepted_risk require a reason).
  5. The runner records one disposition row per open prior finding, maps
     not_applicable to dismissed-with-reason, never re-records a finding
     already fixed, and fails the reviewer visibly when a classification is
     missing, unknown, or has no evidence.

NO LIVE CALL. Every reviewer CLI and every curl to the Worker is replaced by a
stub. Nothing here deploys, installs, or reaches the network.

    python3 tools/test-review-findings-ledger.py     # exit 0 = all pass
"""
from __future__ import annotations

import copy
import hashlib
import json
import os
import subprocess
import sys
import tempfile
import uuid
from pathlib import Path
from unittest import mock

REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.join(REPO, "pipelines"))

import run_codex_review as rcr  # noqa: E402
import review_findings_ledger as ledger  # noqa: E402

os.makedirs(os.path.join(REPO, "out", "review-council"), exist_ok=True)

results: list[tuple[str, bool, str]] = []


def check(label, ok, detail=""):
    results.append((label, ok, detail))
    print(f"  {'ok  ' if ok else 'FAIL'}  {label}" + (f"  — {detail}" if (not ok and detail) else ""))


# Pinned from the UNMODIFIED runner (commit 3d6aa244) for BASE_REQUEST below.
# If either changes, a request without PR identity no longer behaves exactly
# as it did before carry-forward existed.
BASELINE_PROMPT_SHA256 = "eeb607fa45d355c5b83bb6e029bf9c925d07e32ca64f2c798c5e077a200961b3"
BASELINE_PAYLOAD_SHA256 = "c4458d48d76fb89def8aa22a88df7854ee01e0f475898fd9377fd738eb184626"

BASE_REQUEST = {
    "request_id": "11111111-1111-4111-8111-111111111111",
    "created_at": "2026-09-30T00:00:00Z",
    "requested_by": "ledger-test",
    "kind": "code",
    "evidence": {"commit_sha": "abc1234", "files": ["pipelines/run_codex_review.py"],
                 "work_order": "loop 676 fixture", "record_refs": ["C-999"]},
    "lenses": ["correctness"],
    "acceptance_criteria": ["dispositions recorded"],
    "reviewers": ["codex"],
    "contract_version": "1.0.0",
    "timeout_minutes": 20,
}

FLAG_A = "aaaaaaaa-0000-4000-8000-000000000001"
FLAG_B = "bbbbbbbb-0000-4000-8000-000000000002"
DISPOSITION_FLAG = "dddddddd-0000-4000-8000-000000000004"


def prior(flag_id=FLAG_A, index=0, title="Missing null check", **extra):
    p = {"flag_id": flag_id, "commit_sha": "def5678", "index": index,
         "reviewer": "codex", "severity": "major", "title": title,
         "detail": f"{title}: detail", "location": "pipelines/x.py:10"}
    p.update(extra)
    return p


def pr_request(priors=None, **overrides):
    req = copy.deepcopy(BASE_REQUEST)
    req["request_id"] = str(uuid.uuid4())
    req["pr"] = {"number": 1450, "branch": "claude/review-findings-ledger"}
    req["prior_commits"] = ["def5678"]
    req["prior_findings"] = priors if priors is not None else [
        prior(FLAG_A, 0, "Missing null check"),
        prior(FLAG_A, 1, "Unbounded retry loop"),
        prior(FLAG_B, 0, "Token printed to log"),
    ]
    req.update(overrides)
    return req


class PostRecorder:
    """Stub for the curl call: records every record-finding argument set and
    answers like the Worker would. `fail_kinds` makes posts of that kind fail."""

    def __init__(self, fail_kinds=()):
        self.calls: list[dict] = []
        self.fail_kinds = set(fail_kinds)

    def __call__(self, argv, **kwargs):
        body = json.loads(argv[argv.index("-d") + 1])
        args = body["params"]["arguments"]
        self.calls.append(args)
        if args.get("kind") in self.fail_kinds:
            inner = {"error": "stub_refusal"}
        else:
            inner = {"ok": True, "flag_id": str(uuid.uuid4()), "subject_id": "stub-subject"}
        out = {"jsonrpc": "2.0", "id": 1,
               "result": {"content": [{"type": "text", "text": json.dumps(inner)}]}}
        return subprocess.CompletedProcess(argv, 0, stdout=json.dumps(out), stderr="")

    def kinds(self):
        return [c.get("kind") for c in self.calls]


def fake_spec(review: dict) -> dict:
    return {
        "find_binary": lambda: ("/fake/reviewer", ["stub"]),
        "build_command": lambda *a: ["true"],
        "parse_output": lambda stdout: (copy.deepcopy(review), {}),
        "install_hint": "n/a", "model_label": "fake-model",
    }


def fake_proc(argv, **kwargs):
    return subprocess.CompletedProcess(argv, 0, stdout="{}", stderr="")


def run_reviewer(req, review, post=None):
    post = post or PostRecorder()
    os.environ["CARR_MCP_REVIEW_TOKEN_CODEX"] = "fake-codex-token-for-test"
    prompt = rcr.render_contract_prompt(req)
    outcome = rcr.run_one_reviewer("codex", req, prompt, Path("/tmp"), spec=fake_spec(review),
                                   subprocess_runner=fake_proc, post_runner=post)
    return outcome, post


def expect_request_error(label, req, needle):
    try:
        rcr.validate_request(req)
        check(label, False, "validated but should have failed")
    except rcr.RequestError as e:
        check(label, needle in str(e), f"message did not name {needle!r}: {e}")


# ── 1. backward compatibility ────────────────────────────────────────────

def test_backward_compat():
    print("\n[1] a request with no PR identity behaves exactly as before")
    req = copy.deepcopy(BASE_REQUEST)
    try:
        rcr.validate_request(req)
        check("no-PR request still validates", True)
    except rcr.RequestError as e:
        check("no-PR request still validates", False, str(e))

    prompt = rcr.render_contract_prompt(req)
    got = hashlib.sha256(prompt.encode()).hexdigest()
    check("no-PR prompt is byte-identical to the pre-change runner", got == BASELINE_PROMPT_SHA256,
          f"sha256 {got}")
    check("no-PR prompt carries no prior-findings block", "PRIOR FINDINGS" not in prompt
          and "prior_finding_dispositions" not in prompt)

    payload = rcr.build_finding_payload(req, {"summary": "s", "findings": []}, {"m": 1}, "codex")
    payload.pop("idempotency_key")
    got = hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()
    check("no-PR record-finding payload is byte-identical to the pre-change runner",
          got == BASELINE_PAYLOAD_SHA256, f"sha256 {got}")

    outcome, post = run_reviewer(req, {"summary": "clean", "findings": []})
    check("no-PR reviewer run: status ok", outcome["status"] == "ok", str(outcome))
    check("no-PR reviewer run: exactly one record-finding post, the review itself",
          post.kinds() == ["code_review"], str(post.kinds()))
    check("no-PR reviewer run: no disposition fields in the outcome",
          "dispositions" not in outcome and "carry_forward_problems" not in outcome)
    check("no-PR reviewer run: meta carries no pr key", "pr" not in outcome["meta"])


# ── 2. schema ────────────────────────────────────────────────────────────

def test_schema():
    print("\n[2] carry-forward request schema — malformed data fails visibly")
    try:
        rcr.validate_request(pr_request())
        check("valid PR request with prior findings passes", True)
    except rcr.RequestError as e:
        check("valid PR request with prior findings passes", False, str(e))
    try:
        rcr.validate_request(pr_request(priors=[]))
        check("PR request with an empty prior_findings list passes (clean prior pass)", True)
    except rcr.RequestError as e:
        check("PR request with an empty prior_findings list passes (clean prior pass)", False, str(e))
    try:
        rcr.validate_request(pr_request(pr={"branch": "claude/x"}))
        check("PR identity by branch alone passes", True)
    except rcr.RequestError as e:
        check("PR identity by branch alone passes", False, str(e))

    expect_request_error("pr with neither number nor branch", pr_request(pr={}), "pr")
    expect_request_error("pr.number not a positive integer", pr_request(pr={"number": 0}), "pr.number")
    expect_request_error("pr.number a bool", pr_request(pr={"number": True}), "pr.number")
    expect_request_error("pr on a design review", pr_request(kind="design"), "kind")
    r = pr_request(); r.pop("prior_commits")
    expect_request_error("pr without prior_commits", r, "prior_commits")
    r = pr_request(); r.pop("prior_findings")
    expect_request_error("pr without prior_findings", r, "prior_findings")
    r = copy.deepcopy(BASE_REQUEST); r["prior_findings"] = [prior()]
    expect_request_error("prior_findings without pr", r, "pr")
    expect_request_error("prior_commits includes the commit under review",
                         pr_request(prior_commits=["abc1234"]), "prior_commits")
    expect_request_error("prior_findings not a list", pr_request(priors={"flag_id": FLAG_A}), "prior_findings")
    expect_request_error("prior finding not an object", pr_request(priors=["just a string"]), "prior_findings[0]")
    expect_request_error("prior finding flag_id not a uuid",
                         pr_request(priors=[prior(flag_id="42")]), "flag_id")
    expect_request_error("prior finding from a commit not in prior_commits",
                         pr_request(priors=[prior(commit_sha="fff0000")]), "commit_sha")
    expect_request_error("prior finding with a negative index",
                         pr_request(priors=[prior(index=-1)]), "index")
    expect_request_error("prior finding with no title",
                         pr_request(priors=[prior(title="  ")]), "title")
    expect_request_error("prior finding with an unknown severity",
                         pr_request(priors=[prior(severity="urgent")]), "severity")
    expect_request_error("duplicate prior finding (same flag_id and index)",
                         pr_request(priors=[prior(), prior()]), "duplicate")
    expect_request_error("ledger_disposition outside the vocabulary",
                         pr_request(priors=[prior(ledger_disposition={"disposition": "wontfix"})]),
                         "ledger_disposition")
    expect_request_error("ledger_disposition dismissed without a reason",
                         pr_request(priors=[prior(ledger_disposition={"disposition": "dismissed"})]),
                         "reason")
    expect_request_error("ledger_disposition accepted_risk with a blank reason",
                         pr_request(priors=[prior(ledger_disposition={"disposition": "accepted_risk",
                                                                      "reason": " "})]),
                         "reason")

    # Through the real entry point: a malformed prior finding lands a failed
    # sidecar and a failing exit code, not a silent pass.
    with tempfile.TemporaryDirectory() as d:
        path = Path(d) / f"{uuid.uuid4()}.json"
        path.write_text(json.dumps(pr_request(priors=[prior(flag_id="not-a-uuid")])))
        rc = rcr.process_request(path)
        sidecar = json.loads(Path(str(path) + ".status.json").read_text())
        check("process_request on malformed prior data exits FAIL", rc == rcr.EX_FAIL, f"rc={rc}")
        check("process_request on malformed prior data writes a failed sidecar naming the field",
              sidecar["status"] == "failed" and "flag_id" in sidecar["detail"]["reason"],
              json.dumps(sidecar))


# ── 3. prompt ────────────────────────────────────────────────────────────

def test_prompt():
    print("\n[3] prior findings reach the prompt; closed ones do not")
    req = pr_request(priors=[
        prior(FLAG_A, 0, "Missing null check"),
        prior(FLAG_A, 1, "Unbounded retry loop",
              ledger_disposition={"disposition": "fixed"}),
        prior(FLAG_B, 0, "Token printed to log",
              ledger_disposition={"disposition": "accepted_risk", "reason": "local-only log"}),
        prior(FLAG_B, 1, "Stale docstring",
              ledger_disposition={"disposition": "dismissed", "reason": "docstring is correct"}),
        prior(FLAG_B, 2, "Race on sidecar write",
              ledger_disposition={"disposition": "still_present"}),
    ])
    rcr.validate_request(req)
    prompt = rcr.render_contract_prompt(req)
    check("prompt carries a PRIOR FINDINGS block", "PRIOR FINDINGS" in prompt)
    check("open prior finding is in the prompt", "Missing null check" in prompt)
    check("still_present prior finding is carried forward into the prompt",
          "Race on sidecar write" in prompt)
    check("fixed prior finding is NOT re-raised in the prompt", "Unbounded retry loop" not in prompt)
    check("accepted_risk prior finding is NOT re-raised", "Token printed to log" not in prompt)
    check("dismissed prior finding is NOT re-raised", "Stale docstring" not in prompt)
    check("prompt names each open finding's record-layer flag id", FLAG_A in prompt and FLAG_B in prompt)
    check("prompt asks for fixed / still_present / not_applicable with evidence",
          all(w in prompt for w in ('"fixed"', '"still_present"', '"not_applicable"', '"evidence"')))
    check("output contract names prior_finding_dispositions", "prior_finding_dispositions" in prompt)
    check("prompt tells the reviewer not to repeat a prior finding as a new one",
          "do not repeat" in prompt.lower())
    check("prompt names the PR", "1450" in prompt)
    # The fixture's evidence.files names run_codex_review.py, so check only
    # the text carry-forward adds: it must name no backend, even though each
    # prior finding carries the reviewer that raised it.
    added = (ledger.prompt_prior_block(req) + ledger.prompt_contract_field(req)
             + ledger.prompt_rules(req)).lower()
    check("carry-forward prompt text names no backend (identical for every reviewer)",
          added and "codex" not in added and "grok" not in added)
    opens = ledger.open_prior_findings(req)
    check("open_prior_findings: two open, ids P1 and P2 in order",
          [o["id"] for o in opens] == ["P1", "P2"]
          and [o["prior"]["title"] for o in opens] == ["Missing null check", "Race on sidecar write"],
          str(opens))


# ── 4. disposition vocabulary and reason rule ───────────────────────────

def test_disposition_payload():
    print("\n[4] disposition vocabulary and reason rule")
    req = pr_request()
    rcr.validate_request(req)
    p = req["prior_findings"][0]
    check("vocabulary is exactly the four dispositions",
          tuple(ledger.DISPOSITIONS) == ("fixed", "dismissed", "accepted_risk", "still_present"))

    for disp in ("fixed", "still_present"):
        try:
            args = ledger.build_disposition_payload(req, p, disp, None, "diff removes it", "codex")
            v = args["value"]
            check(f"{disp}: recorded without a reason",
                  v["disposition"] == disp and args["kind"] == ledger.DISPOSITION_KIND)
        except ledger.DispositionError as e:
            check(f"{disp}: recorded without a reason", False, str(e))

    for disp in ("dismissed", "accepted_risk"):
        for blank in (None, "", "   "):
            try:
                ledger.build_disposition_payload(req, p, disp, blank, "x", "codex")
                check(f"{disp} with reason {blank!r} is refused", False, "accepted")
            except ledger.DispositionError as e:
                check(f"{disp} with reason {blank!r} is refused", "reason" in str(e), str(e))
        try:
            args = ledger.build_disposition_payload(req, p, disp, "owner accepts it", "x", "codex")
            check(f"{disp} with a reason is recorded, reason kept",
                  args["value"]["reason"] == "owner accepts it")
        except ledger.DispositionError as e:
            check(f"{disp} with a reason is recorded, reason kept", False, str(e))

    try:
        ledger.build_disposition_payload(req, p, "wontfix", "because", "x", "codex")
        check("an unknown disposition is refused", False, "accepted")
    except ledger.DispositionError:
        check("an unknown disposition is refused", True)

    args = ledger.build_disposition_payload(req, p, "fixed", None, "the guard now exists", "codex")
    v = args["value"]
    check("disposition row is filed against the commit under review",
          args["subject"] == "commit:abc1234", args["subject"])
    check("disposition row links the prior finding (flag id, commit, index, title)",
          v["prior"]["flag_id"] == FLAG_A and v["prior"]["commit_sha"] == "def5678"
          and v["prior"]["index"] == 0 and v["prior"]["title"] == "Missing null check", str(v))
    check("disposition row carries the PR identity", v["pr"] == req["pr"])
    check("disposition row carries the evidence", v["evidence"] == "the guard now exists")
    check("epistemic_status maps fixed -> superseded", args["epistemic_status"] == "superseded")
    check("epistemic_status maps each disposition inside record-finding's enum",
          {ledger.EPISTEMIC_BY_DISPOSITION[d] for d in ledger.DISPOSITIONS}
          <= {"proposed", "observed", "reproduced", "accepted", "disputed", "superseded",
              "inferred", "source_backed", "speculative"})
    src = args["source"]
    check("source names the reviewer slug, carry-forward version and request",
          "codex-reviewer" in src and ledger.CARRY_FORWARD_VERSION in src and req["request_id"] in src)
    check("source passes the Worker's external-locator shape (has '/' and >= 12 chars)",
          "/" in src and len(src.strip()) >= 12)


# ── 5. runner wiring ─────────────────────────────────────────────────────

def disp_calls(post):
    return [c for c in post.calls if c.get("kind") == ledger.DISPOSITION_KIND]


def test_runner_carry_forward():
    print("\n[5] runner records one disposition per open prior finding")
    req = pr_request()
    rcr.validate_request(req)
    review = {
        "summary": "two resolved, one remains",
        "findings": [{"severity": "minor", "title": "New nit", "detail": "d", "location": None}],
        "acceptance_criteria_results": [], "could_not_assess": [],
        "prior_finding_dispositions": [
            {"id": "P1", "classification": "fixed", "evidence": "null check added at x.py:12"},
            {"id": "P2", "classification": "still_present", "evidence": "loop at x.py:40 still unbounded"},
            {"id": "P3", "classification": "not_applicable", "evidence": "the log is never written"},
        ],
    }
    outcome, post = run_reviewer(req, review)
    check("carry-forward run: status ok", outcome["status"] == "ok", json.dumps(outcome, default=str)[:400])
    check("new findings are recorded as today: first post is the review row with its findings",
          post.calls[0]["kind"] == "code_review"
          and post.calls[0]["value"]["review"]["findings"][0]["title"] == "New nit")
    check("the review row's meta carries the PR identity",
          post.calls[0]["value"]["meta"].get("pr") == req["pr"])
    dc = disp_calls(post)
    check("one disposition row per open prior finding (3)", len(dc) == 3, str(post.kinds()))
    by_title = {c["value"]["prior"]["title"]: c["value"] for c in dc}
    check("P1 recorded fixed", by_title.get("Missing null check", {}).get("disposition") == "fixed")
    check("P2 recorded still_present",
          by_title.get("Unbounded retry loop", {}).get("disposition") == "still_present")
    p3 = by_title.get("Token printed to log", {})
    check("P3 not_applicable is recorded as dismissed with the evidence as its reason",
          p3.get("disposition") == "dismissed" and "never written" in (p3.get("reason") or ""), str(p3))
    check("outcome lists the three dispositions", len(outcome.get("dispositions", [])) == 3)

    # A finding already fixed on the ledger is neither re-raised nor re-recorded.
    req2 = pr_request(priors=[
        prior(FLAG_A, 0, "Missing null check", ledger_disposition={"disposition": "fixed"}),
        prior(FLAG_B, 0, "Token printed to log"),
    ])
    rcr.validate_request(req2)
    review2 = {"summary": "s", "findings": [], "prior_finding_dispositions": [
        {"id": "P1", "classification": "still_present", "evidence": "still there"}]}
    outcome2, post2 = run_reviewer(req2, review2)
    dc2 = disp_calls(post2)
    check("fixed finding: not re-recorded (only the open one gets a row)",
          len(dc2) == 1 and dc2[0]["value"]["prior"]["flag_id"] == FLAG_B, str([c["value"]["prior"] for c in dc2]))
    check("fixed finding: run still ok", outcome2["status"] == "ok", str(outcome2)[:300])

    # Every prior finding closed: nothing to classify, no disposition rows, ok.
    req3 = pr_request(priors=[prior(ledger_disposition={"disposition": "fixed"})])
    rcr.validate_request(req3)
    outcome3, post3 = run_reviewer(req3, {"summary": "s", "findings": []})
    check("all prior findings closed: no disposition rows, status ok",
          disp_calls(post3) == [] and outcome3["status"] == "ok", str(post3.kinds()))

    # Missing classification fails visibly; the review row still lands.
    review4 = copy.deepcopy(review)
    review4["prior_finding_dispositions"] = review4["prior_finding_dispositions"][:2]
    outcome4, post4 = run_reviewer(req, review4)
    check("a prior finding left unclassified fails the reviewer visibly",
          outcome4["status"] == "failed"
          and any("P3" in p for p in outcome4.get("carry_forward_problems", [])), str(outcome4)[:400])
    check("unclassified: the review row itself still posted", post4.calls[0]["kind"] == "code_review")
    check("unclassified: the classified ones are still recorded", len(disp_calls(post4)) == 2)

    # Unknown classification and empty evidence both fail visibly.
    review5 = copy.deepcopy(review)
    review5["prior_finding_dispositions"][0]["classification"] = "wontfix"
    outcome5, _ = run_reviewer(req, review5)
    check("an unknown classification fails the reviewer visibly", outcome5["status"] == "failed")
    review6 = copy.deepcopy(review)
    review6["prior_finding_dispositions"][1]["evidence"] = ""
    outcome6, post6 = run_reviewer(req, review6)
    check("a classification with no evidence fails the reviewer visibly",
          outcome6["status"] == "failed" and len(disp_calls(post6)) == 2)
    review7 = copy.deepcopy(review)
    review7["prior_finding_dispositions"].append(
        {"id": "P9", "classification": "fixed", "evidence": "n/a"})
    outcome7, _ = run_reviewer(req, review7)
    check("a classification for an id that was never sent fails visibly", outcome7["status"] == "failed")

    # Ten or more open findings: a bad entry for P10 must not hide that P1
    # was never classified (ids are matched exactly, never by substring).
    many = [prior(FLAG_A, i, f"Finding {i}") for i in range(10)]
    opens = ledger.open_prior_findings(pr_request(priors=many))
    entries = [{"id": f"P{n}", "classification": "fixed", "evidence": "e"} for n in range(2, 10)]
    entries.append({"id": "P10", "classification": "wontfix", "evidence": "e"})
    _acc, probs = ledger.reconcile_dispositions(opens, {"prior_finding_dispositions": entries})
    check("P1 left unclassified is reported even when P10 has its own problem",
          any(p.startswith("P1 unclassified") for p in probs), str(probs))

    # Unparseable reviewer output: nothing classified, visible failure.
    outcome8, post8 = run_reviewer(req, {"parse_error": True, "raw_output": "garbage"})
    check("unparseable review with open prior findings fails visibly, no dispositions",
          outcome8["status"] == "failed" and disp_calls(post8) == [])

    # A disposition post the Worker refuses is a failure, not a silent drop.
    outcome9, _ = run_reviewer(req, review, post=PostRecorder(fail_kinds={ledger.DISPOSITION_KIND}))
    check("a refused disposition write fails the reviewer visibly", outcome9["status"] == "failed")


def test_flatten_round_trip():
    print("\n[6] a recorded review flattens into valid prior findings for the next round")
    review = {"summary": "s", "findings": [
        {"severity": "blocker", "title": "A", "detail": "a", "location": "x:1"},
        {"severity": "nit", "title": "B", "detail": "b", "location": None}]}
    flat = ledger.flatten_review_findings(FLAG_A, "def5678", "codex", review)
    check("flatten: one prior finding per review finding, indexed in order",
          [f["index"] for f in flat] == [0, 1] and all(f["flag_id"] == FLAG_A for f in flat))
    req = pr_request(priors=flat)
    try:
        rcr.validate_request(req)
        check("flatten output validates as prior_findings", True)
    except rcr.RequestError as e:
        check("flatten output validates as prior_findings", False, str(e))
    try:
        ledger.flatten_review_findings(FLAG_A, "def5678", "codex", {"findings": "not a list"})
        check("flatten refuses a review whose findings is not a list", False, "accepted")
    except ledger.CarryForwardError:
        check("flatten refuses a review whose findings is not a list", True)


# ── 7. PR 1449 cross-family review fixes ────────────────────────────────

class RawPost(PostRecorder):
    """Like PostRecorder, but disposition posts answer with a raw MCP body
    chosen by the test, to exercise error, empty and partial receipts."""

    def __init__(self, disposition_body):
        super().__init__()
        self.disposition_body = disposition_body

    def __call__(self, argv, **kwargs):
        body = json.loads(argv[argv.index("-d") + 1])
        args = body["params"]["arguments"]
        if args.get("kind") != ledger.DISPOSITION_KIND:
            return super().__call__(argv, **kwargs)
        self.calls.append(args)
        return subprocess.CompletedProcess(argv, 0, stdout=json.dumps(self.disposition_body), stderr="")


def run_process(req, reviews_by_backend):
    """process_request end to end with every reviewer and every Worker call
    stubbed. reviews_by_backend maps backend name -> the review it returns."""
    post = PostRecorder()
    specs = {b: fake_spec(r) for b, r in reviews_by_backend.items()}
    orig = rcr.run_one_reviewer

    def wrapped(backend, rq, prompt, cwd, **_kw):
        return orig(backend, rq, prompt, cwd, subprocess_runner=fake_proc, post_runner=post)

    os.environ["CARR_MCP_REVIEW_TOKEN_CODEX"] = "fake-codex-token-for-test"
    os.environ["CARR_MCP_REVIEW_TOKEN_GROK"] = "fake-grok-token-for-test"
    with tempfile.TemporaryDirectory() as d, \
            mock.patch.dict(rcr.BACKENDS, specs), \
            mock.patch.object(rcr, "worktree_add", lambda sha, rid: Path(d)), \
            mock.patch.object(rcr, "worktree_remove", lambda rid: None), \
            mock.patch.object(rcr, "run_one_reviewer", wrapped):
        path = Path(d) / f"{req['request_id']}.json"
        path.write_text(json.dumps(req))
        try:
            rc = rcr.process_request(path)
        except Exception as e:  # noqa: BLE001 — the finding under test is exactly an escape
            return f"raised {type(e).__name__}: {e}", None, post
        sc = Path(str(path) + ".status.json")
        sidecar = json.loads(sc.read_text()) if sc.exists() else None
    return rc, sidecar, post


def three_way_review(**overrides):
    review = {
        "summary": "s", "findings": [],
        "prior_finding_dispositions": [
            {"id": "P1", "classification": "fixed", "evidence": "guard added at x.py:12"},
            {"id": "P2", "classification": "still_present", "evidence": "loop at x.py:40"},
            {"id": "P3", "classification": "not_applicable", "evidence": "log never written"},
        ],
    }
    review.update(overrides)
    return review


def test_review_fixes():
    print("\n[7] PR 1449 review findings")

    # F1 — the runner loads by file path with pipelines/ NOT on sys.path,
    # exactly as ops/codex-hook-smoke-selftest.py does.
    code = ("import importlib.util, sys; "
            "spec = importlib.util.spec_from_file_location('rcr_by_path', "
            f"{os.path.join(REPO, 'pipelines', 'run_codex_review.py')!r}); "
            "m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m); "
            "print('LOADED', hasattr(m, 'render_contract_prompt'))")
    r = subprocess.run([sys.executable, "-I", "-c", code], cwd="/", capture_output=True, text=True)
    check("F1 runner imports when loaded by file path (no pipelines/ on sys.path)",
          r.returncode == 0 and "LOADED True" in r.stdout, (r.stderr or r.stdout)[-300:])

    req = pr_request()
    rcr.validate_request(req)

    # F2 — a malformed id fails ONE reviewer; later reviewers still run and
    # the sidecar records both.
    for bad in ([], {}):
        review = three_way_review()
        review["prior_finding_dispositions"][0]["id"] = bad
        try:
            outcome, _ = run_reviewer(req, review)
            check(f"F2 id {bad!r} fails the reviewer instead of raising",
                  outcome["status"] == "failed", str(outcome)[:300])
        except Exception as e:  # noqa: BLE001
            check(f"F2 id {bad!r} fails the reviewer instead of raising", False, repr(e))
    bad_review = three_way_review()
    bad_review["prior_finding_dispositions"][0]["id"] = []
    rc, sidecar, _ = run_process(pr_request(reviewers=["codex", "grok"]),
                                 {"codex": bad_review, "grok": three_way_review()})
    check("F2 malformed id in the first reviewer: request still completes with a sidecar",
          sidecar is not None and rc == rcr.EX_FAIL, str(rc))
    if sidecar:
        revs = sidecar["detail"]["reviewers"]
        check("F2 the second reviewer still ran and succeeded",
              revs.get("grok", {}).get("status") == "ok" and revs.get("codex", {}).get("status") == "failed",
              json.dumps({k: v.get("status") for k, v in revs.items()}))
    with mock.patch.object(ledger, "reconcile_dispositions", side_effect=RuntimeError("boom")):
        try:
            outcome, _ = run_reviewer(req, three_way_review())
            check("F2 an unexpected reconciliation error is contained as a failed outcome",
                  outcome["status"] == "failed" and "boom" in outcome.get("reason", ""), str(outcome)[:300])
        except Exception as e:  # noqa: BLE001
            check("F2 an unexpected reconciliation error is contained as a failed outcome", False, repr(e))

    # F3 — contradictory duplicates for P1 must not close P1.
    dup = three_way_review()
    dup["prior_finding_dispositions"].append(
        {"id": "P1", "classification": "still_present", "evidence": "guard missing at x.py:12"})
    outcome, post = run_reviewer(req, dup)
    p1_rows = [c for c in disp_calls(post) if c["value"]["prior"]["title"] == "Missing null check"]
    check("F3 contradictory duplicate classifications record NO disposition for that finding",
          p1_rows == [], str([c["value"]["disposition"] for c in p1_rows]))
    check("F3 the other findings' valid classifications are still recorded",
          len(disp_calls(post)) == 2, str(post.kinds()))
    check("F3 the reviewer is failed and the problem names P1", outcome["status"] == "failed"
          and any(p.startswith("P1") for p in outcome.get("carry_forward_problems", [])))

    # F4 — resolution is order-independent, the unresolved outcome wins at the
    # same head, and a newer head beats an older round whatever the append order.
    def row(disp, commit, reason=None):
        v = {"disposition": disp, "commit_sha": commit,
             "prior": {"flag_id": FLAG_A, "index": 0}}
        if reason:
            v["reason"] = reason
        return v

    order = ["def5678", "abc1234"]
    key = (FLAG_A, 0)
    same_head = [row("still_present", "abc1234"), row("fixed", "abc1234"),
                 row("dismissed", "abc1234", "not applicable: x")]
    import itertools
    results_same = {ledger.resolve_ledger_dispositions(list(p), order)[key]["disposition"]
                    for p in itertools.permutations(same_head)}
    check("F4 reviewers disagreeing at the same head: still_present wins in every order",
          results_same == {"still_present"}, str(results_same))
    closed_only = [row("fixed", "abc1234"), row("dismissed", "abc1234", "not applicable: y"),
                   row("accepted_risk", "abc1234", "owner accepts")]
    results_closed = {ledger.resolve_ledger_dispositions(list(p), order)[key]["disposition"]
                      for p in itertools.permutations(closed_only)}
    check("F4 closing dispositions disagreeing: one deterministic, most conservative winner",
          results_closed == {"accepted_risk"}, str(results_closed))
    stale = [row("fixed", "abc1234"), row("still_present", "def5678")]
    results_stale = {ledger.resolve_ledger_dispositions(list(p), order)[key]["disposition"]
                     for p in itertools.permutations(stale)}
    check("F4 a delayed row from an older round never overrides the newer head",
          results_stale == {"fixed"}, str(results_stale))
    try:
        ledger.resolve_ledger_dispositions([row("fixed", "fff0000")], order)
        check("F4 a row from a commit outside the PR's order fails visibly", False, "accepted")
    except ledger.CarryForwardError:
        check("F4 a row from a commit outside the PR's order fails visibly", True)

    fixed_r = three_way_review()
    still_r = three_way_review()
    still_r["prior_finding_dispositions"][0]["classification"] = "still_present"
    resolutions = []
    for reviewers in (["codex", "grok"], ["grok", "codex"]):
        rq = pr_request(reviewers=reviewers)
        rc, sidecar, _ = run_process(rq, {"codex": fixed_r, "grok": still_r})
        resolutions.append((sidecar or {}).get("detail", {}).get("carry_forward_resolution"))
    check("F4 reviewer order permuted: the request's resolution is identical",
          resolutions[0] is not None and resolutions[0] == resolutions[1], str(resolutions))
    p1 = [x for x in (resolutions[0] or []) if x.get("flag_id") == FLAG_A and x.get("index") == 0]
    check("F4 P1 resolves still_present when one reviewer says fixed and one says still_present",
          p1 and p1[0]["disposition"] == "still_present", str(p1))

    # F5 — each prior finding's commit_sha is shape-checked.
    for bad_sha in ("", "d", "def5678-not-a-sha"):
        expect_request_error(f"F5 prior finding commit_sha {bad_sha!r} is refused",
                             pr_request(priors=[prior(commit_sha=bad_sha)]), "commit_sha")

    # F6 — only canonical UUID spellings are accepted.
    expect_request_error("F6 hyphenless flag_id is refused",
                         pr_request(priors=[prior(), prior(flag_id=FLAG_A.replace("-", ""), index=0)]),
                         "flag_id")
    expect_request_error("F6 uppercase flag_id is refused",
                         pr_request(priors=[prior(flag_id=FLAG_A.upper())]), "flag_id")

    # F7 — zero open findings still rejects unsolicited classifications.
    closed_req = pr_request(priors=[prior(ledger_disposition={"disposition": "fixed"})])
    rcr.validate_request(closed_req)
    outcome, post = run_reviewer(closed_req, {"summary": "s", "findings": [],
                                               "prior_finding_dispositions": [
                                                   {"id": "P1", "classification": "fixed", "evidence": "e"}]})
    check("F7 unsolicited classification with zero open findings fails visibly",
          outcome["status"] == "failed" and disp_calls(post) == [], str(outcome)[:300])
    outcome, _ = run_reviewer(closed_req, {"summary": "s", "findings": [],
                                            "prior_finding_dispositions": []})
    check("F7 the clean zero-open case (empty array) stays ok", outcome["status"] == "ok")

    # F8 — error, empty and partial MCP receipts are not persisted dispositions.
    def envelope(inner_text=None, is_error=False, content=True):
        res = {"isError": is_error} if is_error else {}
        if content:
            res["content"] = [{"type": "text", "text": inner_text}]
        return {"jsonrpc": "2.0", "id": 1, "result": res}

    bad_bodies = {
        "result.isError with text Denied": envelope("Denied", is_error=True),
        "inner text {}": envelope("{}"),
        "no content at all": envelope(content=False),
        "flag_id not a uuid": envelope(json.dumps({"ok": True, "flag_id": "nope"})),
        "ok false": envelope(json.dumps({"ok": False, "flag_id": str(uuid.uuid4())})),
    }
    for label, body in bad_bodies.items():
        outcome, _ = run_reviewer(req, three_way_review(), post=RawPost(body))
        posted = [d for d in outcome.get("dispositions", []) if d.get("posted")]
        check(f"F8 {label}: disposition not counted as recorded, reviewer failed",
              outcome["status"] == "failed" and posted == [], str(outcome.get("dispositions"))[:300])

    # Non-blocking — incident_ref is carried when given, absent otherwise.
    p = req["prior_findings"][0]
    with_ref = ledger.build_disposition_payload(req, p, "still_present", None, "e", "codex",
                                                incident_ref="INC-0042")
    without = ledger.build_disposition_payload(req, p, "still_present", None, "e", "codex")
    check("incident_ref is preserved in value when given",
          with_ref["value"].get("incident_ref") == "INC-0042")
    check("incident_ref is absent when not given", "incident_ref" not in without["value"])


def main():
    print("test-review-findings-ledger.py — offline suite (no live calls)")
    test_backward_compat()
    test_schema()
    test_prompt()
    test_disposition_payload()
    test_runner_carry_forward()
    test_flatten_round_trip()
    test_review_fixes()
    failed =[r for r in results if not r[1]]
    print(f"\npassed {len(results) - len(failed)} · failed {len(failed)} · total {len(results)}")
    if failed:
        print("\nFAILURES:")
        for label, _ok, detail in failed:
            print(f"  - {label}: {detail}")
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
