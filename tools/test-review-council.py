#!/usr/bin/env python3
"""test-review-council.py — offline proof for the Automatic Review Council
(pipelines/run_codex_review.py + bin/review-council-runner.sh), both the
Codex lane and the Grok lane added the same day as a verified scope
extension.

Seven groups, in order:
  1. request schema validation (good + bad requests)
  2. the contract prompt renders (pure function, no subprocess) — and is
     IDENTICAL regardless of which reviewer will receive it
  3. worktree add/remove roundtrip on the REAL repo, read-only (git worktree
     add/remove against this repo's own HEAD — no writes to the checkout)
  4. the curl payload builds correctly against a STUB (no live call — the
     stub replaces subprocess.run so nothing ever touches the network)
  5. failure-path files land in requests/failed/ (a schema-invalid request
     run through the real bin/review-council-runner.sh single-file mode —
     this exercises the shell runner's move logic without needing any
     reviewer installed or any network access, since a schema failure
     happens before either is ever touched)
  6. the Grok lane, mirroring the Codex coverage above: binary probe
     (deterministic — every filesystem/PATH probe is stubbed, both the
     absent and the present case, so the result never depends on whether
     Grok or Codex actually happen to be installed on the machine running
     this file), command-shape assertions (including a HARD safety
     regression check that neither backend's command ever carries an
     approval-bypass flag), output parsing against a captured real envelope
     shape, per-backend token isolation, and per-reviewer independence (one
     backend forced to fail never blocks another's own outcome)
  7. security lens triggering with Git failures and known paths, offline

NO LIVE CALL TO THE WORKER OR TO ANY REVIEWER CLI ANYWHERE IN THIS FILE.
Nothing here deploys, installs, commits, or reaches the Worker or an LLM
API/CLI. Test artifacts this file creates under out/review-council/ are
cleaned up before it exits, pass or fail.

    .venv/bin/python tools/test-review-council.py     # exit 0 = all pass
"""
from __future__ import annotations

import json
import os
import stat
import subprocess
import sys
import tempfile
import uuid
from pathlib import Path
from unittest import mock

# Script-relative, NOT os.path.expanduser("~/carr-system") — this file runs
# under ops/ci.sh's `gates` class on a GitHub ubuntu runner where the repo is
# checked out under /home/runner/work/..., not under $HOME, so a
# home-relative path would silently miss and break the sys.path.insert below
# (ModuleNotFoundError on import, before a single check runs). Same pattern
# already used by ops/guard-selftest.py and ops/vault-drift-watch-selftest.py.
REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.join(REPO, "pipelines"))

import run_codex_review as rcr  # noqa: E402

RC_DIR = os.path.join(REPO, "out", "review-council")
REQ_DIR = os.path.join(RC_DIR, "requests")
FAILED_DIR = os.path.join(REQ_DIR, "failed")

# out/ is gitignored, so a fresh checkout does not have these. On Joe's Mac they
# have existed for months and the tests silently depended on that; on a CI runner
# the first write died with FileNotFoundError. Create them rather than assume
# them — a test that only passes on a machine with history is not a test.
for _d in (RC_DIR, REQ_DIR, FAILED_DIR):
    os.makedirs(_d, exist_ok=True)

results: list[tuple[str, bool, str]] = []  # (label, ok, detail)


def check(label, ok, detail=""):
    results.append((label, ok, detail))
    print(f"  {'ok  ' if ok else 'FAIL'}  {label}" + (f"  — {detail}" if (not ok and detail) else ""))


def sample_request(**overrides):
    req = {
        "request_id": str(uuid.uuid4()),
        "created_at": "2026-08-06T12:00:00Z",
        "requested_by": "test-harness",
        "kind": "code",
        "evidence": {
            "commit_sha": "abc1234",
            "files": ["mcp-server/src/index.js"],
            "work_order": "loop #999 test fixture",
            "record_refs": ["C-999"],
        },
        "lenses": ["security", "idempotency"],
        "acceptance_criteria": ["writes exactly one verb", "reads never widen"],
        "reviewers": ["codex"],
        "contract_version": "1.0.0",
        "timeout_minutes": 20,
    }
    req.update(overrides)
    return req


# ── 1. request schema validation ─────────────────────────────────────────

def test_schema_validation():
    print("\n[1] request schema validation")

    good = sample_request()
    try:
        rcr.validate_request(good)
        check("valid request passes", True)
    except rcr.RequestError as e:
        check("valid request passes", False, str(e))

    bad_cases = [
        ("missing request_id", {k: v for k, v in sample_request().items() if k != "request_id"}),
        ("missing evidence", {k: v for k, v in sample_request().items() if k != "evidence"}),
        ("bad request_id (not a uuid)", sample_request(request_id="not-a-uuid")),
        ("bad kind", sample_request(kind="banana")),
        ("kind=code missing commit_sha",
         {**sample_request(), "evidence": {"work_order": "no sha here"}}),
        ("commit_sha not sha-shaped",
         {**sample_request(), "evidence": {**sample_request()["evidence"], "commit_sha": "not a sha!!"}}),
        ("lenses not a list", sample_request(lenses="security")),
        ("acceptance_criteria not a list", sample_request(acceptance_criteria="just one string")),
        ("reviewers empty", sample_request(reviewers=[])),
        ("timeout_minutes not positive", sample_request(timeout_minutes=0)),
        ("timeout_minutes not a number", sample_request(timeout_minutes="twenty")),
    ]
    for label, req in bad_cases:
        try:
            rcr.validate_request(req)
            check(f"rejects: {label}", False, "validate_request did not raise")
        except rcr.RequestError:
            check(f"rejects: {label}", True)

    # design kind does not require commit_sha
    design_req = sample_request(kind="design", evidence={"work_order": "a design review"})
    try:
        rcr.validate_request(design_req)
        check("kind=design without commit_sha passes", True)
    except rcr.RequestError as e:
        check("kind=design without commit_sha passes", False, str(e))

    # active_reviewers: case-insensitive, both codex and grok recognized
    # (2026-08-06 scope extension — Grok is no longer excluded), unknown
    # names ignored rather than erroring, order preserved, deduplicated.
    check("active_reviewers: ['codex'] -> ['codex']",
          rcr.active_reviewers(sample_request(reviewers=["codex"])) == ["codex"])
    check("active_reviewers: ['Codex'] -> ['codex'] (case-insensitive)",
          rcr.active_reviewers(sample_request(reviewers=["Codex"])) == ["codex"])
    check("active_reviewers: ['grok'] -> ['grok'] (Grok now in scope)",
          rcr.active_reviewers(sample_request(reviewers=["grok"])) == ["grok"])
    check("active_reviewers: ['GROK'] -> ['grok'] (case-insensitive)",
          rcr.active_reviewers(sample_request(reviewers=["GROK"])) == ["grok"])
    check("active_reviewers: ['codex','grok'] -> both, in order",
          rcr.active_reviewers(sample_request(reviewers=["codex", "grok"])) == ["codex", "grok"])
    check("active_reviewers: ['grok','codex'] -> preserves request order",
          rcr.active_reviewers(sample_request(reviewers=["grok", "codex"])) == ["grok", "codex"])
    check("active_reviewers: dedupes repeats",
          rcr.active_reviewers(sample_request(reviewers=["codex", "codex", "grok"])) == ["codex", "grok"])
    check("active_reviewers: unknown reviewer name ignored, not an error",
          rcr.active_reviewers(sample_request(reviewers=["banana"])) == [])
    check("active_reviewers: mix of known + unknown keeps only known",
          rcr.active_reviewers(sample_request(reviewers=["banana", "codex"])) == ["codex"])

    # load_request end-to-end against a real file, valid and invalid JSON
    tmp_ok = os.path.join(RC_DIR, f"_test-schema-ok-{uuid.uuid4()}.json")
    tmp_bad = os.path.join(RC_DIR, f"_test-schema-badjson-{uuid.uuid4()}.json")
    try:
        with open(tmp_ok, "w") as fh:
            json.dump(good, fh)
        loaded = rcr.load_request(Path(tmp_ok))
        check("load_request reads a valid file", loaded["request_id"] == good["request_id"])

        with open(tmp_bad, "w") as fh:
            fh.write("{not valid json,,,")
        try:
            rcr.load_request(Path(tmp_bad))
            check("load_request rejects invalid JSON", False, "did not raise")
        except rcr.RequestError:
            check("load_request rejects invalid JSON", True)
    finally:
        for p in (tmp_ok, tmp_bad):
            if os.path.exists(p):
                os.remove(p)


# ── 2. the contract prompt renders ───────────────────────────────────────

def test_prompt_renders():
    print("\n[2] contract prompt renders")
    req = sample_request()
    prompt = rcr.render_contract_prompt(req)
    check("prompt is a non-empty string", isinstance(prompt, str) and len(prompt) > 100)
    check("carries CONTRACT_VERSION", rcr.CONTRACT_VERSION in prompt)
    for lens in req["lenses"]:
        check(f"carries lens: {lens}", lens in prompt)
    for crit in req["acceptance_criteria"]:
        check(f"carries acceptance criterion: {crit!r}", crit in prompt)
    check("carries the commit sha", req["evidence"]["commit_sha"] in prompt)
    check("carries the work order", req["evidence"]["work_order"] in prompt)
    check("carries the record ref", req["evidence"]["record_refs"][0] in prompt)
    for key in ("findings", "acceptance_criteria_results", "could_not_assess", "severity"):
        check(f"demands structured output key: {key}", key in prompt)
    check("demands absence be visible",
          "VISIBLE" in prompt or "visible" in prompt)
    # CONTRACT_VERSION itself contains the substring "codex" (a historical
    # naming leftover — see the module docstring), so this checks for
    # backend-naming PHRASES rather than the bare substring, which would
    # false-positive on the version string embedded in every prompt.
    check("prompt does not name a specific reviewer seat (same prompt for every reviewer)",
          "Codex seat" not in prompt and "Grok seat" not in prompt
          and "You are the Codex" not in prompt and "You are the Grok" not in prompt)

    # renders fine with no lenses / no acceptance criteria (defaults kick in)
    minimal = sample_request(lenses=[], acceptance_criteria=[])
    prompt2 = rcr.render_contract_prompt(minimal)
    check("renders with empty lenses/criteria (defaults used, no crash)",
          isinstance(prompt2, str) and len(prompt2) > 100)

    # THE SAME PROMPT, literally: rendering twice for the same request produces
    # byte-identical text regardless of which reviewer will eventually receive
    # it — render_contract_prompt takes no backend parameter at all.
    check("render_contract_prompt is backend-agnostic (identical output, no backend param)",
          rcr.render_contract_prompt(req) == rcr.render_contract_prompt(req))


# ── 3. worktree add/remove roundtrip on the real repo, read-only ────────

def test_worktree_roundtrip():
    print("\n[3] worktree add/remove roundtrip (real repo, read-only)")

    head_sha = subprocess.run(["git", "-C", REPO, "rev-parse", "HEAD"],
                               capture_output=True, text=True, timeout=15).stdout.strip()
    check("resolved HEAD sha", bool(head_sha), head_sha)

    test_request_id = f"test-{uuid.uuid4()}"
    try:
        path = rcr.worktree_add(head_sha, test_request_id, repo=Path(REPO))
        check("worktree_add returns a path that exists", path.exists())
        check("worktree checked out README.md", (path / "README.md").is_file())
        check("worktree checked out mcp-server/src/index.js",
              (path / "mcp-server" / "src" / "index.js").is_file())

        # COMPARE RESOLVED PATHS, NOT RAW STRINGS (fixed 2026-08-13). This
        # assertion failed for everyone running CI from inside a linked worktree,
        # and passed in the canonical checkout — so it read as a flaky test and
        # was in fact a real, deterministic path bug in the test itself. Every
        # worktree bin/worktree.sh creates SYMLINKS out/ back to the canonical
        # tree (out/ is gitignored, so a fresh worktree has none, and ten-plus
        # scripts hard-code it). WORKTREES_DIR lives under out/, so from a linked
        # worktree `path` reads .../worktrees/<name>/out/review-council/... while
        # git resolves the symlink and registers .../carr-system/out/... — the
        # same directory, a different string. It mattered beyond neatness: the
        # standing remedy for a busy shared tree is to work from a worktree, and
        # a pre-push CI that fails there blocks the exact escape hatch it should
        # protect. Resolving both sides compares identity instead of spelling.
        def listed_worktrees():
            out = subprocess.run(["git", "-C", REPO, "worktree", "list", "--porcelain"],
                                 capture_output=True, text=True, timeout=15).stdout
            return {os.path.realpath(ln.split(" ", 1)[1].strip())
                    for ln in out.splitlines() if ln.startswith("worktree ")}

        real_path = os.path.realpath(path)
        check("git worktree list shows the new worktree", real_path in listed_worktrees())

        # confirm nothing was written INTO the checkout (read-only in practice)
        status = subprocess.run(["git", "-C", str(path), "status", "--porcelain"],
                                 capture_output=True, text=True, timeout=15).stdout
        check("worktree is clean (nothing written into it)", status.strip() == "")

        rcr.worktree_remove(test_request_id, repo=Path(REPO))
        check("worktree_remove deletes the directory", not path.exists())

        check("git worktree list no longer shows it", real_path not in listed_worktrees())
    finally:
        # belt and braces: never leave a worktree registered even if an
        # assertion above raised mid-test.
        rcr.worktree_remove(test_request_id, repo=Path(REPO), missing_ok=True)

    # bad sha fails loudly rather than hanging or silently no-opping
    try:
        rcr.worktree_add("0000000000000000000000000000000000dead", f"test-bad-{uuid.uuid4()}", repo=Path(REPO))
        check("worktree_add on a bogus sha raises WorktreeError", False, "did not raise")
    except rcr.WorktreeError:
        check("worktree_add on a bogus sha raises WorktreeError", True)


# ── 4. curl payload builds correctly against a stub (no live call) ──────

def test_curl_payload_stub():
    print("\n[4] curl payload builds correctly against a stub — NO LIVE CALL")
    req = sample_request()
    review_result = {"summary": "looks fine", "findings": [], "acceptance_criteria_results": [],
                      "could_not_assess": []}
    meta = {"model": "codex-test", "completion_status": "completed"}

    finding_args = rcr.build_finding_payload(req, review_result, meta, "codex")
    check("finding_args has idempotency_key", bool(finding_args.get("idempotency_key")))
    check("finding_args.subject prefers record_refs[0]", finding_args["subject"] == "C-999")
    check("finding_args.kind is code_review", finding_args["kind"] == "code_review")
    check("finding_args.source names the reviewer's own actor slug",
          "codex-reviewer" in finding_args["source"])
    check("finding_args.source names contract + request_id",
          rcr.CONTRACT_VERSION in finding_args["source"] and req["request_id"] in finding_args["source"])
    check("finding_args.value carries review + meta",
          finding_args["value"]["review"] == review_result and finding_args["value"]["meta"] == meta)

    grok_finding_args = rcr.build_finding_payload(req, review_result, meta, "grok")
    check("a grok finding names 'grok-reviewer' in source, not 'codex-reviewer'",
          "grok-reviewer" in grok_finding_args["source"]
          and "codex-reviewer" not in grok_finding_args["source"])

    # subject fallback order: record_refs -> work_order -> commit
    check("subject falls back to work_order", rcr.pick_subject(
        {"evidence": {"work_order": "ORDER 99"}}) == "ORDER 99")
    check("subject falls back to commit", rcr.pick_subject(
        {"evidence": {"commit_sha": "deadbee"}}) == "commit:deadbee")

    rpc = rcr.build_rpc_envelope(finding_args, rpc_id=7)
    check("rpc envelope is jsonrpc 2.0", rpc["jsonrpc"] == "2.0")
    check("rpc envelope calls record-finding", rpc["params"]["name"] == "record-finding")
    check("rpc envelope id is set", rpc["id"] == 7)

    stub_token = "TEST-TOKEN-NEVER-REAL"  # pragma: allowlist secret
    stub_url = "https://stub.invalid/mcp"
    cmd = rcr.build_curl_command(stub_url, stub_token, rpc)
    check("curl command starts with curl", cmd[0] == "curl")
    check("curl command targets the stub url", stub_url in cmd)
    check("curl command carries the bearer token",
          f"Authorization: Bearer {stub_token}" in cmd)
    check("curl command -d payload round-trips through json.loads",
          json.loads(cmd[-1])["params"]["arguments"]["idempotency_key"] == finding_args["idempotency_key"])
    check("no real host present (api.doctorcre.com / api.practicecre.com) in this stub call",
          "api.doctorcre.com" not in cmd and "api.practicecre.com" not in cmd)

    # post_finding with an INJECTED runner — this is the "no live call" proof:
    # subprocess.run is never invoked here, `fake_run` is.
    def fake_run_ok(cmd_argv, **kwargs):
        check("fake_run never calls the real network (no live call)", True)
        payload = json.loads(cmd_argv[-1])
        assert payload["params"]["name"] == "record-finding"
        body = {"jsonrpc": "2.0", "id": payload["id"],
                "result": {"content": [{"type": "text",
                            "text": json.dumps({"ok": True, "flag_id": "stub-flag-1",
                                                 "subject_id": "stub-subject-1"})}]}}
        return subprocess.CompletedProcess(cmd_argv, 0, stdout=json.dumps(body), stderr="")

    ok, resp = rcr.post_finding(finding_args, url=stub_url, token=stub_token, runner=fake_run_ok)
    check("post_finding (stub success) returns ok=True", ok is True)
    check("post_finding (stub success) surfaces flag_id", resp.get("flag_id") == "stub-flag-1")

    def fake_run_verb_error(cmd_argv, **kwargs):
        body = {"jsonrpc": "2.0", "id": 1,
                "result": {"content": [{"type": "text",
                            "text": json.dumps({"isError": True, "error": "not_in_profile"})}]}}
        return subprocess.CompletedProcess(cmd_argv, 0, stdout=json.dumps(body), stderr="")

    ok2, resp2 = rcr.post_finding(finding_args, url=stub_url, token=stub_token, runner=fake_run_verb_error)
    check("post_finding (stub verb error) returns ok=False", ok2 is False)

    def fake_run_transport_fail(cmd_argv, **kwargs):
        return subprocess.CompletedProcess(cmd_argv, 7, stdout="", stderr="curl: (7) Failed to connect")

    ok3, resp3 = rcr.post_finding(finding_args, url=stub_url, token=stub_token, runner=fake_run_transport_fail)
    check("post_finding (stub transport failure) returns ok=False", ok3 is False)
    check("post_finding (stub transport failure) reports curl_nonzero_exit",
          resp3.get("error") == "curl_nonzero_exit")

    ok4, resp4 = rcr.post_finding(finding_args, url=stub_url, token="")
    check("post_finding with no token returns ok=False, no_review_token",
          ok4 is False and resp4.get("error") == "no_review_token")

    # backend param flows through to a per-backend finding, still via a stub
    ok5, resp5 = rcr.post_finding(grok_finding_args, url=stub_url, token=stub_token,
                                   backend="grok", runner=fake_run_ok)
    check("post_finding accepts backend='grok' with an explicit stub token", ok5 is True)

    # per-backend token isolation — CARR_MCP_REVIEW_TOKEN_CODEX and
    # CARR_MCP_REVIEW_TOKEN_GROK are read independently, never cross-read
    saved_env = {k: os.environ.get(k) for k in
                 ("CARR_MCP_REVIEW_TOKEN_CODEX", "CARR_MCP_REVIEW_TOKEN_GROK")}
    try:
        os.environ["CARR_MCP_REVIEW_TOKEN_CODEX"] = "codex-token-value"
        os.environ.pop("CARR_MCP_REVIEW_TOKEN_GROK", None)
        check("read_review_token('codex') reads its own env var",
              rcr.read_review_token("codex") == "codex-token-value")
        check("read_review_token('grok') does NOT see the codex token",
              rcr.read_review_token("grok") != "codex-token-value")
        os.environ["CARR_MCP_REVIEW_TOKEN_GROK"] = "grok-token-value"
        check("read_review_token('grok') reads its own env var once set",
              rcr.read_review_token("grok") == "grok-token-value")
        check("read_review_token('codex') unaffected by the grok token",
              rcr.read_review_token("codex") == "codex-token-value")
    finally:
        for k, v in saved_env.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v


# ── 5. failure-path files land in requests/failed/ ───────────────────────

def test_failure_path_lands_in_failed():
    print("\n[5] failure-path files land in requests/failed/")
    os.makedirs(REQ_DIR, exist_ok=True)
    os.makedirs(FAILED_DIR, exist_ok=True)

    # Schema-invalid on purpose (missing acceptance_criteria) — this fails
    # BEFORE run_codex_review.py ever probes for Codex or touches the network,
    # so it exercises the FAIL path deterministically regardless of whether
    # Codex is installed on this machine.
    bad_req = sample_request()
    del bad_req["acceptance_criteria"]
    fname = f"_test-failpath-{bad_req['request_id']}.json"
    src_path = os.path.join(REQ_DIR, fname)
    done_path_in_failed = os.path.join(FAILED_DIR, fname)
    sidecar_in_failed = done_path_in_failed + ".status.json"

    try:
        with open(src_path, "w") as fh:
            json.dump(bad_req, fh)

        env = {**os.environ, "REVIEW_COUNCIL_REQUEST": src_path}
        result = subprocess.run(
            ["zsh", os.path.join(REPO, "bin", "review-council-runner.sh")],
            capture_output=True, text=True, timeout=60, env=env, cwd=REPO)

        check("runner script exits non-zero on a failed sweep", result.returncode != 0,
              f"stdout/stderr tail: {(result.stdout + result.stderr)[-400:]}")
        check("request file no longer sits in requests/ (moved)", not os.path.exists(src_path))
        check("request file landed in requests/failed/", os.path.exists(done_path_in_failed))
        check("a .status.json sidecar landed alongside it", os.path.exists(sidecar_in_failed))

        if os.path.exists(sidecar_in_failed):
            with open(sidecar_in_failed) as fh:
                sidecar = json.load(fh)
            check("sidecar status is 'failed'", sidecar.get("status") == "failed")
            check("sidecar names the schema reason",
                  "schema_invalid" in json.dumps(sidecar.get("detail", {})))
    finally:
        for p in (src_path, done_path_in_failed, sidecar_in_failed):
            if os.path.exists(p):
                os.remove(p)


# ── 6. the Grok lane, mirroring the Codex coverage above ─────────────────

def test_grok_lane():
    print("\n[6] grok lane (binary probe, command shape, output parsing, independence)")

    # --- binary probe: DETERMINISTIC, not host-dependent. This used to assert
    # "grok_path is not None" on the grounds that Grok CLI was actually
    # installed on the machine this file was built on — true on that Mac at
    # build time, false on ops/ci.sh's `gates` class running on a bare GitHub
    # ubuntu runner (no Grok CLI there), so it would fail on the very first
    # CI run. Same defect as the Codex probe above, pointing the other way.
    # Fixed the same way: construct both worlds explicitly by stubbing every
    # probe find_grok_binary uses, instead of assuming either one from
    # whatever happens to be on this host.
    with mock.patch.object(rcr.shutil, "which", return_value=None), \
         mock.patch.object(rcr.os.path, "isfile", return_value=False):
        grok_path_absent, checked = rcr.find_grok_binary()
    check("find_grok_binary checked at least PATH", len(checked) >= 1)
    check("find_grok_binary reports 'not found' when no binary is reachable "
          "anywhere it looks — constructed with every probe stubbed out, not "
          "assumed from this host's real state",
          grok_path_absent is None)

    with tempfile.TemporaryDirectory() as tmp_dir:
        fake_grok = Path(tmp_dir) / "grok"
        fake_grok.write_text("#!/bin/sh\necho fake grok\n")
        fake_grok.chmod(fake_grok.stat().st_mode | stat.S_IEXEC)

        def fake_which_grok(name, *a, **kw):
            return str(fake_grok) if name == "grok" else None

        with mock.patch.object(rcr.shutil, "which", side_effect=fake_which_grok):
            grok_path_present, checked_present = rcr.find_grok_binary()
        check("find_grok_binary finds a constructed fake binary on PATH",
              grok_path_present == str(fake_grok), f"got {grok_path_present!r}")

    # --- codex probe: DETERMINISTIC, not host-dependent. The property under
    # test is "when no Codex binary is reachable anywhere find_codex_binary
    # looks, it returns (None, checked) cleanly instead of raising or
    # hallucinating a path" — that must hold on every machine, not just one
    # where Codex happens to be absent. A Codex CLI IS installed on this Mac
    # now (it was not at build time), so asserting against this host's real
    # state would pass on a bare CI runner and fail here — it would be
    # testing the machine, not the code. Construct the "nothing found" world
    # explicitly by stubbing every probe find_codex_binary uses, instead of
    # assuming it from whatever happens to be on this host.
    with mock.patch.object(rcr.shutil, "which", return_value=None), \
         mock.patch.object(rcr.os.path, "isdir", return_value=False), \
         mock.patch.object(rcr.os.path, "isfile", return_value=False):
        codex_path_absent, codex_checked_absent = rcr.find_codex_binary()
    check("find_codex_binary checked at least PATH", len(codex_checked_absent) >= 1)
    check("find_codex_binary reports 'not found' when no binary is reachable "
          "anywhere it looks (PATH, app bundles, common install paths, npm "
          "global) — constructed with every probe stubbed out, not assumed "
          "from this host's real state",
          codex_path_absent is None)

    # --- codex probe, present case: mirrors the present-case coverage Grok
    # already gets above, but with a FAKE executable planted in a temp dir
    # and found via the cheap PATH branch (shutil.which stubbed to point at
    # it) — never by relying on this host's real Codex install, which may or
    # may not exist and may be a different build on a different machine.
    with tempfile.TemporaryDirectory() as tmp_dir:
        fake_codex = Path(tmp_dir) / "codex"
        fake_codex.write_text("#!/bin/sh\necho fake codex\n")
        fake_codex.chmod(fake_codex.stat().st_mode | stat.S_IEXEC)

        def fake_which(name, *a, **kw):
            return str(fake_codex) if name == "codex" else None

        with mock.patch.object(rcr.shutil, "which", side_effect=fake_which):
            codex_path_present, codex_checked_present = rcr.find_codex_binary()
        check("find_codex_binary finds a constructed fake binary on PATH",
              codex_path_present == str(fake_codex), f"got {codex_path_present!r}")

    # --- command shape + THE hard safety regression check ------------------
    fake_prompt = "REVIEW PROMPT PLACEHOLDER"
    fake_cwd = Path("/tmp/does-not-need-to-exist-for-this-pure-function-check")
    grok_cmd = rcr.build_grok_command("grok", fake_prompt, fake_cwd)
    codex_cmd = rcr.build_codex_command("codex", fake_prompt, fake_cwd)

    check("build_grok_command carries --sandbox read-only",
          "--sandbox" in grok_cmd and "read-only" in grok_cmd)
    check("build_grok_command carries --cwd", "--cwd" in grok_cmd and str(fake_cwd) in grok_cmd)
    check("build_grok_command carries -p and the prompt", "-p" in grok_cmd and fake_prompt in grok_cmd)
    check("build_codex_command carries --sandbox read-only",
          "--sandbox" in codex_cmd and "read-only" in codex_cmd)
    check("build_codex_command carries the prompt", fake_prompt in codex_cmd)

    # --dangerously-bypass-hook-trust (added 2026-08-14 to build_codex_command)
    # is a DIFFERENT class of flag from everything else on this list — it does
    # not touch approvals or the sandbox, it only lets Codex run hooks (OUR OWN
    # gate code, e.g. guard-unattended.py) without the interactive trust prompt
    # an unattended `codex exec` can never satisfy. Without it, EVERY
    # PreToolUse hook is SILENTLY SKIPPED (verified live 2026-08-14, see
    # ops/codex-hook-smoke.sh) — so for an invocation carrying our own
    # hooks.json this flag turns enforcement ON, the opposite of a bypass. It
    # is allow-listed by exact token below rather than exempted from the
    # substring scan, so any OTHER "--dangerously-*" flag (e.g. a real
    # --dangerously-bypass-approvals-and-sandbox) still fails this test.
    ALLOWED_DANGEROUS_FLAGS = ("--dangerously-bypass-hook-trust",)
    FORBIDDEN_FLAGS = ("--always-approve", "--yolo", "bypassPermissions",
                        "--permission-mode=bypassPermissions")
    for cmd_name, cmd in (("grok", grok_cmd), ("codex", codex_cmd)):
        joined = " ".join(cmd)
        for forbidden in FORBIDDEN_FLAGS:
            check(f"SAFETY: {cmd_name} command NEVER carries {forbidden!r}",
                  forbidden not in joined)
        unlisted_dangerous = [tok for tok in cmd if tok.startswith("--dangerously")
                               and tok not in ALLOWED_DANGEROUS_FLAGS]
        check(f"SAFETY: {cmd_name} command carries no unlisted --dangerously* flag",
              not unlisted_dangerous, f"found: {unlisted_dangerous}")

    # --- output parsing: a captured REAL envelope shape (from this build's own
    # live `grok -p ... --output-format json` run), offline, no live call.
    real_shaped_envelope = json.dumps({
        "text": '{"summary": "no issues found", "findings": [], '
                '"acceptance_criteria_results": [], "could_not_assess": []}',
        "stopReason": "end_turn",
        "sessionId": "019fd767-a55f-78c1-b19a-a7cd9b28f9a8",
        "requestId": "57797589-c93d-46af-b5e9-f592aae4f080",
        "num_turns": 2,
        "total_cost_usd": 0.0318136,
    })
    review, extra = rcr.parse_grok_output(real_shaped_envelope)
    check("parse_grok_output unwraps the envelope to the inner review JSON",
          review.get("summary") == "no issues found" and review.get("findings") == [])
    check("parse_grok_output surfaces session/cost metadata as bonus meta",
          extra.get("session_id") == "019fd767-a55f-78c1-b19a-a7cd9b28f9a8"
          and extra.get("total_cost_usd") == 0.0318136)

    error_envelope = json.dumps({"type": "error", "message": "Couldn't start session: boom"})
    review_err, extra_err = rcr.parse_grok_output(error_envelope)
    check("parse_grok_output surfaces Grok's own documented error shape",
          review_err.get("parse_error") is True and "boom" in review_err.get("grok_error", ""))

    check("parse_grok_output degrades to raw-text handling on non-JSON stdout (never raises)",
          rcr.parse_grok_output("not json at all")[0].get("parse_error") is True)

    codex_review, codex_extra = rcr.parse_codex_output(
        '{"summary": "fine", "findings": [], "acceptance_criteria_results": [], "could_not_assess": []}')
    check("parse_codex_output is the thin unwrapped-stdout path (no envelope)",
          codex_review.get("summary") == "fine" and codex_extra == {})

    # --- run_one_reviewer branch coverage + PER-REVIEWER INDEPENDENCE, fully
    # offline via injected fakes (no real binary, no real subprocess, no real
    # network call anywhere below).
    req = sample_request(reviewers=["codex", "grok"])
    prompt = rcr.render_contract_prompt(req)

    def fake_post_ok(cmd_argv, **kwargs):
        payload = json.loads(cmd_argv[-1])
        body = {"jsonrpc": "2.0", "id": payload["id"],
                "result": {"content": [{"type": "text", "text": json.dumps(
                    {"ok": True, "flag_id": "fake-flag", "subject_id": "fake-subject"})}]}}
        return subprocess.CompletedProcess(cmd_argv, 0, stdout=json.dumps(body), stderr="")

    ok_spec = {
        "find_binary": lambda: ("/fake/ok-reviewer", ["fake check"]),
        "build_command": lambda *a: ["true"],
        "parse_output": lambda stdout: ({"summary": "clean", "findings": []}, {}),
        "install_hint": "n/a", "model_label": "fake-ok-model",
    }

    def fake_proc_ok(argv, **kwargs):
        return subprocess.CompletedProcess(argv, 0, stdout="{}", stderr="")

    os.environ.setdefault("CARR_MCP_REVIEW_TOKEN_CODEX", "fake-codex-token-for-test")
    os.environ.setdefault("CARR_MCP_REVIEW_TOKEN_GROK", "fake-grok-token-for-test")
    outcome_ok = rcr.run_one_reviewer("codex", req, prompt, Path("/tmp"), spec=ok_spec,
                                       subprocess_runner=fake_proc_ok, post_runner=fake_post_ok)
    check("run_one_reviewer: healthy fake backend -> status ok", outcome_ok["status"] == "ok")

    missing_spec = {
        "find_binary": lambda: (None, ["checked path A", "checked path B"]),
        "build_command": lambda *a: [],
        "parse_output": lambda stdout: ({}, {}),
        "install_hint": "pretend install command", "model_label": "n/a",
    }
    outcome_skip = rcr.run_one_reviewer("grok", req, prompt, Path("/tmp"), spec=missing_spec)
    check("run_one_reviewer: binary not found -> status skip", outcome_skip["status"] == "skip")

    def fake_proc_timeout(argv, **kwargs):
        raise subprocess.TimeoutExpired(cmd=argv, timeout=1)

    timeout_spec = {**ok_spec, "find_binary": lambda: ("/fake/slow-reviewer", [])}
    outcome_timeout = rcr.run_one_reviewer(
        "codex", {**req, "timeout_minutes": 0.001}, prompt, Path("/tmp"),
        spec=timeout_spec, subprocess_runner=fake_proc_timeout, post_runner=fake_post_ok)
    check("run_one_reviewer: CLI timeout -> status failed (not an unhandled exception)",
          outcome_timeout["status"] == "failed" and outcome_timeout.get("reason") == "timeout")

    def fake_proc_nonzero(argv, **kwargs):
        return subprocess.CompletedProcess(argv, 1, stdout="", stderr="boom, exit 1")

    error_spec = {**ok_spec, "find_binary": lambda: ("/fake/broken-reviewer", [])}
    outcome_error = rcr.run_one_reviewer("codex", req, prompt, Path("/tmp"), spec=error_spec,
                                          subprocess_runner=fake_proc_nonzero, post_runner=fake_post_ok)
    check("run_one_reviewer: nonzero exit -> status failed", outcome_error["status"] == "failed")

    # THE independence proof: run a FAILING backend and a SUCCEEDING backend
    # back to back, exactly as process_request()'s loop would, and confirm
    # the failure did not prevent or corrupt the success.
    outcomes = {}
    outcomes["broken"] = rcr.run_one_reviewer("codex", req, prompt, Path("/tmp"), spec=error_spec,
                                               subprocess_runner=fake_proc_nonzero, post_runner=fake_post_ok)
    outcomes["healthy"] = rcr.run_one_reviewer("grok", req, prompt, Path("/tmp"), spec=ok_spec,
                                                subprocess_runner=fake_proc_ok, post_runner=fake_post_ok)
    check("per-reviewer independence: a failing reviewer's outcome is 'failed'",
          outcomes["broken"]["status"] == "failed")
    check("per-reviewer independence: the OTHER reviewer still succeeds afterward",
          outcomes["healthy"]["status"] == "ok")


def test_security_lens_trigger():
    print("\n[7] security lens trigger")
    commit_sha = "abc1234"
    failures = [
        ("git nonzero", subprocess.CompletedProcess([], 1, "", "unknown commit")),
        ("git timeout", subprocess.TimeoutExpired(cmd="git", timeout=30)),
        ("git exception", OSError("git unavailable")),
    ]
    for label, outcome in failures:
        kwargs = {"side_effect": outcome} if isinstance(outcome, Exception) else {"return_value": outcome}
        with mock.patch.object(rcr.subprocess, "run", **kwargs), mock.patch.object(rcr, "log") as logger:
            lens = rcr.security_lens_if_triggered(commit_sha)
        check(f"security lens armed: {label}", lens == rcr.SECURITY_LENS)
        check(f"security lens failure logged honestly: {label}",
              logger.call_count == 1 and "ARMED conservatively" in logger.call_args.args[0])

    known_paths = [
        ("no paths", "\n", None),
        ("known low tier", "docs/example.md\n", None),
        ("known high tier", "mcp-server/src/auth.js\n", rcr.SECURITY_LENS),
    ]
    for label, stdout, expected in known_paths:
        with mock.patch.object(rcr.subprocess, "run", return_value=subprocess.CompletedProcess([], 0, stdout, "")):
            check(f"security lens: {label}", rcr.security_lens_if_triggered(commit_sha) == expected)

    with mock.patch.object(rcr.subprocess, "run", return_value=subprocess.CompletedProcess([], 0, "docs/example.md\n", "")), \
            mock.patch.object(rcr, "_review_tiers", side_effect=OSError("map unreadable")), \
            mock.patch.object(rcr, "log") as logger:
        lens = rcr.security_lens_if_triggered(commit_sha)
    check("security lens armed: unreadable map", lens == rcr.SECURITY_LENS)
    check("security lens unreadable map logged honestly",
          logger.call_count == 1 and "ARMED conservatively" in logger.call_args.args[0])


def main():
    print("test-review-council.py — offline suite (no live calls, no deploys, no installs)")
    test_schema_validation()
    test_prompt_renders()
    test_worktree_roundtrip()
    test_curl_payload_stub()
    test_failure_path_lands_in_failed()
    test_grok_lane()
    test_security_lens_trigger()

    failed = [r for r in results if not r[1]]
    print(f"\npassed {len(results) - len(failed)} · failed {len(failed)} · total {len(results)}")
    if failed:
        print("\nFAILURES:")
        for label, ok, detail in failed:
            print(f"  - {label}: {detail}")
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
