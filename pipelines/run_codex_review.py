#!/usr/bin/env python3
"""run_codex_review.py — Automatic Review Council (built 2026-08-06, Codex
lane; GROK LANE ADDED same day as a verified scope extension — see below).

FILENAME NOTE. This file kept its original name (rather than a rename or a
"sibling" file per backend) because the shared logic — request validation,
worktree lifecycle, the review-contract prompt, the curl/record-finding
write path, status sidecars — is identical across reviewers and a second
near-duplicate file would only invite drift. It is now a small, parameterized
multi-backend runner (see BACKENDS below), not Codex-specific despite the
name. bin/review-council-runner.sh and tools/test-review-council.py both
reference this path and were not renamed either.

WHAT THIS IS. The per-request worker invoked by bin/review-council-runner.sh,
one call per request file. It does five things, in order, and every one of
them is written to fail VISIBLY rather than silently (house rule: a blocked
or failed action is filed, never dropped):
  1. load + validate one request file against THE REQUEST CONTRACT below
  2. for kind="code": `git worktree add` ONE detached, read-only checkout of
     evidence.commit_sha under out/review-council/worktrees/<request_id>,
     shared by every reviewer this request names
  3. for EACH reviewer the request names (independently — see "Per-reviewer
     independence" below): probe for that reviewer's CLI; if absent, report
     INSTALL NEEDED for that reviewer and record it as a SKIP rather than
     installing anything
  4. invoke that reviewer's CLI, sandboxed read-only, headless, against the
     SAME fixed, versioned review-contract prompt (render_contract_prompt is
     backend-agnostic on purpose — it names no reviewer), and parse its
     STRUCTURED JSON output
  5. POST the result to the Worker as THAT REVIEWER'S OWN bearer (its own
     REVIEW_TOKENS slug — 'codex-reviewer' or 'grok-reviewer'), calling
     record-finding — the reviewers' ONLY write, exactly as the smoke-probe's
     'probe' profile locks its own write set (mcp-server/src/mcp.js)

SCOPE (frozen at build start, EXTENDED same day — both states below are true
and worth keeping straight, because the record of the extension is part of
what makes this file trustworthy to a future reader):

  ORIGINAL FREEZE: Codex-only automation, subscription-covered via
  ChatGPT-token auth (~/.codex/auth.json). Grok manual — Joe's 2026-08-06
  cost override, decision 65468572.

  VERIFIED EXTENSION (same day, before this build finished): the coordinator
  reported Grok Build CLI 0.2.118 installed at /opt/homebrew/bin/grok, Joe
  completed `grok login` (OAuth device flow), and the subscription-covered
  headless path live-verified (no XAI_API_KEY anywhere in the environment;
  ~/.grok/auth.json holds only an OIDC entitlement token; `grok -p "..."`
  answered correctly). This was NOT taken on faith — before wiring the Grok
  lane in, this file's author independently re-verified, live, from this
  machine:
    - `which grok` / `grok --version` — 0.2.118, confirmed
    - `env | grep -i xai` — empty, confirmed no XAI_API_KEY
    - a real read-only prompt (`grok -p "read seed.txt, print exactly its
      contents"` under `--sandbox read-only`) — completed cleanly, no
      approval prompt, no --always-approve needed (default 'ask' mode
      auto-approves read-only tool calls, confirmed live)
    - THE LOAD-BEARING CHECK: a write attempt inside a REAL git worktree
      (not a /tmp path — every profile including read-only allows writing to
      temp dirs, so a temp-dir test would prove nothing) under
      `--sandbox read-only`, with NO --always-approve. The write was
      KERNEL-BLOCKED: `~/.grok/sandbox-events.jsonl` logged
      `{"event_type":"FsViolation","profile":"read-only","operation":"write",
      "target":".../grok-write-probe.txt"}`, and `git status --porcelain` in
      the worktree came back clean. Seatbelt enforcement on macOS, confirmed
      live, not assumed from documentation alone.
  On that evidence, the Grok lane below is real, not speculative — build_grok_
  command()'s flags are LIVE-VERIFIED, unlike build_codex_command()'s, which
  remains UNTESTED (no Codex binary exists on this machine; see
  find_codex_binary()'s docstring). That asymmetry is real and stated where
  each command is built, not smoothed over.

PER-REVIEWER INDEPENDENCE. A request may name more than one reviewer
(`"reviewers": ["codex", "grok"]`). Each one is invoked, and its outcome is
recorded, entirely independently — one reviewer erroring, timing out, or
being uninstalled NEVER stops another reviewer's run or swallows its finding.
See run_one_reviewer() (the per-reviewer unit, catches its own exceptions and
always returns an outcome dict, never raises past its own call site) and
process_request()'s loop over active_reviewers(req). The git worktree is
shared (built once per request, not per reviewer) since it is read-only and
checking out the same commit twice is pure waste; every reviewer gets a
private CLI process against that same tree.

EXIT CODES (bin/nightly.sh's own convention, reused deliberately so the
runner's step() wrapper needs no new logic) are now a ROLL-UP across every
reviewer processed for the request — see process_request()'s final block:
  0  = OK.   No reviewer failed, and at least one reviewer completed (a
             reviewer that found zero issues is still success). Some
             reviewers may have SKIPped (not installed) without failing the
             request overall.
  1  = FAIL. At least one reviewer errored, timed out, or could not get its
             finding posted. A FAILURE record-finding is attempted for THAT
             reviewer's own bearer wherever a subject can be resolved (see
             write_failure_finding) so the failure is visible in the record,
             not only in this process's exit code — and it never blocks a
             different reviewer's own attempt.
  78 = SKIP (EX_CONFIG, nightly.sh's own code for "not configured"). EVERY
             named reviewer was unavailable (CLI not installed, or the
             request names no reviewer this file recognizes). Nothing is
             installed automatically — see find_codex_binary() /
             find_grok_binary(). The request file is left in place by the
             caller for a later retry, not filed to failed/, because SKIP is
             not a verdict on the request.

════════════════════════════════════════════════════════════════════════════
THE REQUEST CONTRACT (documented HERE ONLY — per the build instruction, this
schema is not restated in bin/review-council-runner.sh or anywhere else; that
script points back to this block by reference).

Request files are file-per-drop JSON, one work order per file, written to
  ~/carr-system/out/review-council/requests/<request_id>.json
The filename convention is `<request_id>.json` — request_id is a UUID, so the
filename is collision-free by construction with no locking or listing race
needed to pick a name. RESULTS ARE THE DURABLE RECORD (the record_flag rows
this file writes via record-finding, ONE PER REVIEWER); REQUESTS ARE WORK
ORDERS — once every named reviewer has been attempted, the request is moved
to requests/done/ or requests/failed/ with a status sidecar (same basename,
.status.json) and is not itself the audit trail of what was found.

Top-level fields:
  request_id          string, REQUIRED. A UUID. Also the filename stem.
  created_at           string, REQUIRED. ISO-8601 timestamp of when the
                       request was dropped.
  requested_by         string, REQUIRED. Human or system identifier of
                       whoever opened the request (e.g. "joe", "council").
  kind                 string, REQUIRED. "code" | "design".
  evidence             object, REQUIRED. What the reviewer looks at:
                         commit_sha   string. REQUIRED when kind="code" —
                                      the exact commit the worktree checks
                                      out. Never a branch name: the whole
                                      point of a detached worktree is that
                                      the code under review cannot move out
                                      from under the review mid-run.
                         files        array of strings, optional. Path hints
                                      inside the checkout worth the
                                      reviewer's attention first; the
                                      reviewer is free to read more.
                         work_order   string, optional. A loop/ORDER
                                      reference or free-text description of
                                      the work being reviewed.
                         record_refs  array of strings, optional. Refs the
                                      finding should attach to (C-127,
                                      L-204, a deal name, ...) — see
                                      pick_subject() for how these choose
                                      the record-finding subject.
  lenses               array of strings, REQUIRED (may be empty, but present).
                       What to review FOR — e.g. "security", "correctness",
                       "idempotency", "matches the acceptance criteria".
  acceptance_criteria  array of strings, REQUIRED (may be empty). The
                       standard the work is being held to; each reviewer
                       reports agreement/disagreement with each one by name.
  reviewers            array of strings, REQUIRED. Recognized values today:
                       "codex", "grok" (case-insensitive; unrecognized names
                       are simply not this file's job and are ignored — see
                       active_reviewers()). A request naming reviewers this
                       file does not recognize, and none it does, is a SKIP.
  contract_version     string, REQUIRED. The REQUEST contract's own version
                       (distinct from CONTRACT_VERSION below, which versions
                       the PROMPT sent to every reviewer — the SAME prompt,
                       byte-for-byte, regardless of which reviewer receives
                       it; render_contract_prompt() names no backend).
                       Carried through to each finding's metadata.
  timeout_minutes      number, REQUIRED. Wall-clock budget for EACH
                       reviewer's CLI call (not a combined budget — Codex
                       timing out does not shorten Grok's own budget).

Optional CARRY-FORWARD fields (engineering-workflow-sop section 15, the
continuous loop and the findings ledger). ABSENT = the request behaves exactly
as it did before they existed: same prompt byte-for-byte, same one row per
reviewer. Their full shape and rules live in pipelines/review_findings_ledger.py
(validate_carry_forward), not restated here:
  pr                   object {number, branch} — at least one. Turns
                       carry-forward on; needs kind="code".
  prior_commits        array of shas, REQUIRED with pr. Earlier commits of the
                       same PR; never the commit under review.
  prior_findings       array, REQUIRED with pr (may be empty). One entry per
                       earlier finding, naming the record-layer flag_id of the
                       review row it sits in. Open ones go into every
                       reviewer's prompt; each reviewer classifies each one and
                       the runner records one code_review_disposition row per
                       open finding under that reviewer's own bearer.

A request missing a required field, or with kind/reviewers/evidence shaped
wrong, fails schema validation — see validate_request(). Malformed requests
are not guessable-fixed; they fail loud, in the log, in the exit code, and
(where request_id is at least present) in a failed/ sidecar.
════════════════════════════════════════════════════════════════════════════

USAGE:
  .venv/bin/python pipelines/run_codex_review.py <path-to-request.json>

Talks to the Worker via curl (subprocess), per repo convention (stdlib-only —
see requirements.txt's header comment; no `requests` package in this repo).
Never prints a bearer token to stdout/stderr/log; each reviewer's token is
read from its own env var (or the shared env file) exactly once and passed
to curl via an -H argument, matching mcp-server/smoke-reads.sh's own handling
of CARR_MCP_PROBE_TOKEN.
"""
from __future__ import annotations

import argparse
import glob
import json
import os
import re
import shutil
import subprocess
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Optional

# The sibling helper must import however this file is loaded: as a script
# (pipelines/ is already sys.path[0]), by the test suites (which add it), AND
# by file path through importlib.util.spec_from_file_location, which adds
# nothing — ops/codex-hook-smoke-selftest.py loads it that way.
_PIPELINES_DIR = str(Path(__file__).resolve().parent)
if _PIPELINES_DIR not in sys.path:
    sys.path.insert(0, _PIPELINES_DIR)
import review_findings_ledger as ledger  # noqa: E402

REPO = Path(__file__).resolve().parent.parent
REVIEW_COUNCIL_DIR = REPO / "out" / "review-council"
WORKTREES_DIR = REVIEW_COUNCIL_DIR / "worktrees"
REQUESTS_DIR = REVIEW_COUNCIL_DIR / "requests"
RUNNER_LOG = REVIEW_COUNCIL_DIR / "runner.log"

EX_OK = 0
EX_FAIL = 1
EX_SKIP = 78  # EX_CONFIG — same code bin/nightly.sh treats as "not configured"

# The PROMPT contract's own version — distinct from a request's contract_version
# (which versions the REQUEST shape). Bump this string, not the prose around it,
# whenever the instructions sent to a reviewer change in a way that would make
# an old finding's "what was this reviewer asked" metadata misleading. ONE
# version for every reviewer, because it is the SAME prompt for every reviewer.
CONTRACT_VERSION = "codex-review-contract-1.0.0"

CARR_MCP_URL = os.environ.get("CARR_MCP_URL", "https://api.doctorcre.com/mcp")
MCP_TOKENS_ENV = os.environ.get(
    "CARR_MCP_ENV", os.path.expanduser("~/.config/carr/mcp-tokens.env"))

# Each reviewer authenticates to the Worker under its OWN bearer/slug — never
# a shared token — so a record-finding row's actor always names exactly which
# reviewer wrote it, server-side, independent of anything this process claims.
REVIEW_TOKEN_VAR_BY_BACKEND = {
    "codex": "CARR_MCP_REVIEW_TOKEN_CODEX",
    "grok": "CARR_MCP_REVIEW_TOKEN_GROK",
}
ACTOR_SLUG_BY_BACKEND = {
    "codex": "codex-reviewer",
    "grok": "grok-reviewer",
}


# ---------------------------------------------------------------------------
# logging (house convention: timestamped, self-identifying, append-only —
# same shape as hooks/guard-unattended.py's log() and bin/nightly.sh's say())
# ---------------------------------------------------------------------------

def log(msg: str) -> None:
    RUNNER_LOG.parent.mkdir(parents=True, exist_ok=True)
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    with open(RUNNER_LOG, "a") as fh:
        fh.write(f"{ts}  {msg}\n")
    print(f"{ts}  {msg}", file=sys.stderr)


# ---------------------------------------------------------------------------
# 1. request loading + validation
# ---------------------------------------------------------------------------

class RequestError(Exception):
    """A request file fails schema validation. Carries a human-readable reason."""


REQUIRED_TOP_LEVEL = [
    "request_id", "created_at", "requested_by", "kind", "evidence",
    "lenses", "acceptance_criteria", "reviewers", "contract_version",
    "timeout_minutes",
]


def validate_request(req: dict) -> None:
    """Raise RequestError with a specific reason, or return None. Every check
    names the exact field so a malformed request is fixable from the message
    alone, without opening this file."""
    if not isinstance(req, dict):
        raise RequestError("request body is not a JSON object")

    missing = [f for f in REQUIRED_TOP_LEVEL if f not in req]
    if missing:
        raise RequestError(f"missing required field(s): {', '.join(missing)}")

    try:
        uuid.UUID(str(req["request_id"]))
    except (ValueError, AttributeError, TypeError):
        raise RequestError(f"request_id is not a UUID: {req['request_id']!r}")

    if req["kind"] not in ("code", "design"):
        raise RequestError(f'kind must be "code" or "design", got {req["kind"]!r}')

    evidence = req["evidence"]
    if not isinstance(evidence, dict):
        raise RequestError("evidence must be an object")
    if req["kind"] == "code" and not evidence.get("commit_sha"):
        raise RequestError('evidence.commit_sha is required when kind="code"')
    if evidence.get("commit_sha") and not re.fullmatch(
            r"[0-9a-fA-F]{7,40}", str(evidence["commit_sha"])):
        raise RequestError(f'evidence.commit_sha does not look like a sha: '
                            f'{evidence["commit_sha"]!r}')

    for arr_field in ("lenses", "acceptance_criteria", "reviewers"):
        if not isinstance(req[arr_field], list):
            raise RequestError(f"{arr_field} must be an array")
    if not req["reviewers"]:
        raise RequestError("reviewers must name at least one reviewer")

    if not isinstance(req["timeout_minutes"], (int, float)) or req["timeout_minutes"] <= 0:
        raise RequestError("timeout_minutes must be a positive number")

    # Carry-forward fields are optional; when present, malformed prior data
    # fails the whole request here, visibly, before any reviewer runs.
    try:
        ledger.validate_carry_forward(req)
    except ledger.CarryForwardError as e:
        raise RequestError(str(e))


def load_request(path: Path) -> dict:
    with open(path) as fh:
        raw = fh.read()
    try:
        req = json.loads(raw)
    except json.JSONDecodeError as e:
        raise RequestError(f"invalid JSON: {e}")
    validate_request(req)
    return req


def active_reviewers(req: dict) -> list[str]:
    """The reviewers named on this request that THIS FILE knows how to run,
    in the order named, deduplicated, case-insensitive. A request naming a
    reviewer this file does not recognize (a typo, or a future reviewer not
    yet wired up) simply does not include that name here — it is not a
    validation error, because the request itself is well-formed; this file
    just is not that reviewer's runner. If the result is empty, the caller
    treats the whole request as a SKIP (nothing here could act on it)."""
    seen: list[str] = []
    for r in req.get("reviewers", []):
        name = str(r).strip().lower()
        if name in BACKENDS and name not in seen:
            seen.append(name)
    return seen


# ---------------------------------------------------------------------------
# 2. worktree lifecycle (shared across every reviewer a request names)
# ---------------------------------------------------------------------------

class WorktreeError(Exception):
    pass


def worktree_path_for(request_id: str) -> Path:
    return WORKTREES_DIR / request_id


def worktree_add(commit_sha: str, request_id: str, repo: Path = REPO) -> Path:
    """`git worktree add` a DETACHED checkout at exactly commit_sha — never a
    branch, so the tree under review cannot move mid-review. ONE worktree per
    REQUEST, not per reviewer — every reviewer the request names reads the
    same tree. "Read-only" is enforced two ways, deliberately, rather than by
    chmod-ing the tree (which would fight git's own internals and complicate
    cleanup): (1) nothing in this file ever writes into the worktree — it is
    only ever read by a reviewer CLI and by this process; (2) every reviewer
    is invoked with its own kernel-enforced read-only sandbox flag (see
    build_codex_command / build_grok_command), so even a misbehaving model
    process cannot write there — LIVE-CONFIRMED for Grok (see the module
    docstring's FsViolation evidence), UNVERIFIED for Codex (no binary on
    this machine to test against). Raises WorktreeError with git's own
    stderr on failure."""
    path = worktree_path_for(request_id)
    WORKTREES_DIR.mkdir(parents=True, exist_ok=True)
    if path.exists():
        # A stale worktree from a prior crashed run. Clean it up first rather
        # than fail — `git worktree add` refuses a non-empty target directory.
        worktree_remove(request_id, repo=repo, missing_ok=True)
    result = subprocess.run(
        ["git", "-C", str(repo), "worktree", "add", "--detach", str(path), commit_sha],
        capture_output=True, text=True, timeout=120)
    if result.returncode != 0:
        raise WorktreeError(
            f"git worktree add failed for {commit_sha}: {result.stderr.strip()}")
    return path


def worktree_remove(request_id: str, repo: Path = REPO, missing_ok: bool = False) -> None:
    """Always called in a finally: block by the caller — cleanup happens once
    per request, after every reviewer has been attempted, whether they
    succeeded, failed, or crashed."""
    path = worktree_path_for(request_id)
    if not path.exists():
        return
    result = subprocess.run(
        ["git", "-C", str(repo), "worktree", "remove", "--force", str(path)],
        capture_output=True, text=True, timeout=60)
    if result.returncode != 0 and not missing_ok:
        log(f"WARN worktree remove failed for {request_id}: {result.stderr.strip()} "
            f"— falling back to rm -rf under the sanctioned out/ scratch zone")
        shutil.rmtree(path, ignore_errors=True)
        # Detached the leftover admin dir git leaves behind on a forced rm.
        subprocess.run(["git", "-C", str(repo), "worktree", "prune"],
                        capture_output=True, text=True, timeout=30)


# ---------------------------------------------------------------------------
# 3. reviewer binary probes — NEVER install anything themselves
# ---------------------------------------------------------------------------

def find_codex_binary() -> tuple[Optional[str], list[str]]:
    """Probe for the Codex CLI. Returns (path_or_None, checked_paths) so a
    SKIP report can say exactly where it looked.

    The binary is NOT expected on PATH (confirmed: `which codex` finds
    nothing on this machine, and ~/.codex/auth.json — the ChatGPT-token
    credential this lane is scoped to — belongs to an app, not a PATH
    install). So this checks, in order: PATH itself (cheap, and correct if a
    future install does put it there); the Codex app bundle under
    /Applications and ~/Applications, by name; and the common CLI install
    spots (homebrew, npm global -g, a user-local ~/.codex/bin) in case a
    future `npm i -g @openai/codex` lands there instead of as an app bundle.

    NEVER installs anything. On a miss, the caller reports INSTALL NEEDED
    naming `npm i -g @openai/codex` as Joe's own step, and records a SKIP for
    this reviewer only — it never blocks another reviewer on the same
    request (see run_one_reviewer)."""
    checked = []

    on_path = shutil.which("codex")
    checked.append("PATH (which codex)")
    if on_path:
        return on_path, checked

    app_dirs = ["/Applications", os.path.expanduser("~/Applications")]
    for app_dir in app_dirs:
        checked.append(f"{app_dir}/*.app (any bundle with 'codex' in its name)")
        if not os.path.isdir(app_dir):
            continue
        for bundle in glob.glob(os.path.join(app_dir, "*.app")):
            if "codex" not in os.path.basename(bundle).lower():
                continue
            for candidate in glob.glob(os.path.join(bundle, "Contents", "MacOS", "*")):
                if os.access(candidate, os.X_OK) and not os.path.isdir(candidate):
                    return candidate, checked
            for candidate in glob.glob(
                    os.path.join(bundle, "Contents", "Resources", "**", "codex"),
                    recursive=True):
                if os.access(candidate, os.X_OK) and not os.path.isdir(candidate):
                    return candidate, checked

    common_paths = [
        "/opt/homebrew/bin/codex",
        "/usr/local/bin/codex",
        os.path.expanduser("~/.codex/bin/codex"),
        os.path.expanduser("~/.local/bin/codex"),
    ]
    for p in common_paths:
        checked.append(p)
        if os.path.isfile(p) and os.access(p, os.X_OK):
            return p, checked

    # npm global bin, if npm itself is present — a plausible landing spot for
    # `npm i -g @openai/codex` that predates a PATH refresh in this shell.
    npm = shutil.which("npm")
    if npm:
        try:
            npm_root = subprocess.run([npm, "root", "-g"], capture_output=True,
                                       text=True, timeout=15).stdout.strip()
            if npm_root:
                candidate = os.path.join(os.path.dirname(npm_root), ".bin", "codex")
                checked.append(candidate)
                if os.path.isfile(candidate) and os.access(candidate, os.X_OK):
                    return candidate, checked
        except Exception:
            pass

    return None, checked


def find_grok_binary() -> tuple[Optional[str], list[str]]:
    """Probe for the Grok Build CLI. LIVE-CONFIRMED on this machine at build
    time: `which grok` -> /opt/homebrew/bin/grok (a symlink into
    ../lib/node_modules/@xai-official/grok/bin/grok), `grok --version` ->
    "grok 0.2.118 (1e1687c1cf6a)". Still probes rather than hard-coding that
    path, exactly like find_codex_binary(), so a machine without it reports
    INSTALL NEEDED instead of crashing, and a machine with a different
    install layout is still found."""
    checked = []

    on_path = shutil.which("grok")
    checked.append("PATH (which grok)")
    if on_path:
        return on_path, checked

    common_paths = [
        "/opt/homebrew/bin/grok",
        "/usr/local/bin/grok",
        os.path.expanduser("~/.grok/bin/grok"),
        os.path.expanduser("~/.local/bin/grok"),
    ]
    for p in common_paths:
        checked.append(p)
        if os.path.isfile(p) and os.access(p, os.X_OK):
            return p, checked

    npm = shutil.which("npm")
    if npm:
        try:
            npm_root = subprocess.run([npm, "root", "-g"], capture_output=True,
                                       text=True, timeout=15).stdout.strip()
            if npm_root:
                candidate = os.path.join(os.path.dirname(npm_root), ".bin", "grok")
                checked.append(candidate)
                if os.path.isfile(candidate) and os.access(candidate, os.X_OK):
                    return candidate, checked
        except Exception:
            pass

    return None, checked


# ---------------------------------------------------------------------------
# 4. the review-contract prompt (ONE versioned string, every reviewer)
# ---------------------------------------------------------------------------

# Which paths arm the lens is decided by the one review-tier map
# (ops/config/review-tiers.v1.json, engineering-workflow-sop section 15), read
# through lib/review_tiers.py: a commit whose highest tier reaches
# SECURITY_LENS_TIER (tier 2) touches the trust surface. The map holds every
# prefix this file used to list (mcp-server/src/, mcp-server/wrangler.toml,
# hooks/, migrations/, bin/) at tier 2 or above.
def _review_tiers():
    import importlib.util  # local: this edit stays inside the lens trigger
    spec = importlib.util.spec_from_file_location(
        "review_tiers", str(REPO / "lib" / "review_tiers.py"))
    if spec is None or spec.loader is None:
        raise ImportError("lib/review_tiers.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


SECURITY_LENS = (
    "SECURITY (auto-armed: this commit touches auth, grants, hooks, or "
    "migrations) — evaluate as an attacker and as an auditor: does any change "
    "widen a token door, a capability profile, or a role grant beyond its "
    "stated intent; can any new code path be reached without the auth it "
    "assumes; do hooks or guards still cover every tool/path they claim to "
    "(name what escapes them); does any migration grant more than its header "
    "argues for, or grant DELETE where a log claims to never shrink; could a "
    "secret, token, or credential path leak into logs, renders, or error "
    "messages; and does anything here weaken an existing enforcement the rest "
    "of the system relies on."
)


def security_lens_if_triggered(commit_sha: str) -> Optional[str]:
    """Return the security lens when the commit touches sensitive paths, else
    None. Fails OPEN (None) on any git error, with the error logged — a broken
    trigger must degrade to a normal review, never block one."""
    try:
        out = subprocess.run(
            ["git", "-C", str(REPO), "diff-tree", "--no-commit-id",
             "--name-only", "-r", commit_sha],
            capture_output=True, text=True, timeout=30)
        if out.returncode != 0:
            log(f"security-lens trigger check failed (git rc={out.returncode}); lens not armed")
            return None
        changed = [l.strip() for l in out.stdout.splitlines() if l.strip()]
        if not changed:
            return None
        # A map that cannot be imported, read or validated ARMS the lens: an
        # unreadable map must never lower review. The fault is logged and the
        # review keeps running.
        try:
            tiers = _review_tiers()
            armed = tiers.tier_for_paths(changed) >= tiers.SECURITY_LENS_TIER
        except Exception as e:  # noqa: BLE001 — fail toward more review
            log(f"review-tier map unreadable ({type(e).__name__}: {str(e)[:200]}); "
                "security lens ARMED conservatively")
            return SECURITY_LENS
        return SECURITY_LENS if armed else None
    except Exception as e:  # noqa: BLE001 — fail open by design, but say so
        log(f"security-lens trigger check errored ({type(e).__name__}); lens not armed")
        return None


def render_contract_prompt(req: dict) -> str:
    """Pure function: request dict -> the fixed prompt sent to EVERY reviewer
    named on the request, byte-for-byte identical regardless of which one —
    this function names no backend on purpose, so "codex" and "grok" (and any
    future reviewer) are reviewing against the exact same contract. Carries
    the lenses, acceptance criteria and evidence pointers, and demands
    STRUCTURED JSON output shaped so a finding is never silently incomplete:
    ranked findings, an explicit agreement/disagreement per acceptance
    criterion, and an explicit "could not assess" list — absence has to be
    VISIBLE in the output, never inferred from a field just not being
    there."""
    evidence = req.get("evidence", {})
    lenses = req.get("lenses") or ["general correctness and safety"]
    criteria = req.get("acceptance_criteria") or []

    evidence_lines = []
    if evidence.get("commit_sha"):
        evidence_lines.append(f"- commit: {evidence['commit_sha']}")
    if evidence.get("files"):
        evidence_lines.append("- files to prioritize: " + ", ".join(evidence["files"]))
    if evidence.get("work_order"):
        evidence_lines.append(f"- work order: {evidence['work_order']}")
    if evidence.get("record_refs"):
        evidence_lines.append("- record refs: " + ", ".join(evidence["record_refs"]))
    evidence_block = "\n".join(evidence_lines) or "- (no evidence pointers given — review the checkout as found)"

    criteria_block = ("\n".join(f"- {c}" for c in criteria)
                       if criteria else "- (none given — assess general quality only)")
    lenses_block = "\n".join(f"- {l}" for l in lenses)

    return f"""You are a reviewer seat of CARR's Automatic Review Council ({CONTRACT_VERSION}).
You are reviewing a {req.get('kind', 'code')} change. You have READ-ONLY access to a
detached git checkout of the exact commit under review. Do not attempt to write,
commit, or push anything — you are sandboxed read-only and any such attempt will
fail; that is expected and correct.

REVIEW LENSES — evaluate specifically through each of these:
{lenses_block}

ACCEPTANCE CRITERIA — the standard this work is being held to. For EACH one,
report whether the change meets it, partially meets it, does not meet it, or
you could not determine it from what you can see:
{criteria_block}

EVIDENCE POINTERS:
{evidence_block}

{ledger.prompt_prior_block(req)}OUTPUT CONTRACT — respond with EXACTLY ONE JSON object and nothing else
(no markdown fences, no prose before or after it). Shape:
{{
  "summary": "one paragraph, plain language",
  "findings": [
    {{
      "severity": "blocker" | "major" | "minor" | "nit",
      "title": "short label",
      "detail": "what you found and why it matters",
      "location": "file:line, or a record ref, or null if not applicable"
    }}
  ],
  "acceptance_criteria_results": [
    {{
      "criterion": "the exact criterion text",
      "verdict": "met" | "partial" | "not_met" | "could_not_assess",
      "note": "why"
    }}
  ],
  "could_not_assess": [
    "anything you were asked to review but could not reach or evaluate, stated plainly"
  ]{ledger.prompt_contract_field(req)}
}}

ABSENCE MUST BE VISIBLE. If findings is empty, that means you looked and found
nothing — say so in summary. If something in scope could not be assessed (a
file outside the checkout, a runtime behavior you cannot execute, a record you
cannot query), it MUST appear in could_not_assess rather than being silently
omitted. An empty could_not_assess list is itself a claim — that you assessed
everything you were asked to — so only leave it empty if that is true.
{ledger.prompt_rules(req)}"""


# ---- per-backend command builders --------------------------------------
#
# HARD SAFETY RULE, enforced here and re-checked in tools/test-review-council.py's
# "no approval-bypass flag" regression test: NEITHER builder may EVER emit
# --always-approve, --yolo, --permission-mode bypassPermissions, or any
# equivalent. The read-only sandbox plus the reviewer's own default 'ask'
# permission mode (which auto-approves only read-only tool calls — confirmed
# live for Grok) is the entire safety story for an unattended reviewer
# process; always-approve would remove the one thing standing between a
# reviewer and a real write if the sandbox ever failed soft on some future
# machine (Grok's own docs: "If the sandbox cannot be applied ... Grok logs a
# warning and continues without enforcement" for BUILT-IN profiles like
# read-only — soft-fail is real, so the permission layer underneath it must
# never be waived too).

def build_codex_command(codex_bin: str, prompt: str, cwd: Path) -> list[str]:
    """The current best-known headless invocation per the original build
    instruction: `codex exec` with a read-only sandbox. UNVERIFIED AGAINST A
    LIVE BINARY — no Codex CLI is installed on this build machine (see
    find_codex_binary's docstring), so this exact flag set has not been
    exercised end-to-end, unlike build_grok_command below. Kept in its own
    function, one line each, specifically so the flags are the single easy
    thing to correct against `codex exec --help` the first time this
    actually runs against a real binary, without touching any surrounding
    logic. `cwd` is accepted for signature symmetry with build_grok_command
    but unused here — the working directory is set by the subprocess launch
    (see run_one_reviewer), same as the original single-backend build."""
    # LIVE-CORRECTED 2026-08-06, first real run: `--json` emits a JSONL EVENT
    # stream (thread.started, ...) — extract_json grabbed the first event and
    # the actual review was lost. Plain `codex exec` prints the final agent
    # message on stdout (smoke-verified same day: prompt in, answer out), and
    # the shared extract_json finds the review object inside it. The
    # `-o/--output-last-message FILE` flag exists as a stronger capture if
    # stdout ever proves noisy; not adopted yet to keep the parser signature
    # untouched.
    #
    # --dangerously-bypass-hook-trust: the hooks in ~/.codex/hooks.json are
    # OUR OWN gate code (ops/config/codex-hooks.json is the tracked
    # contract), and Codex requires persisted hook trust granted
    # interactively before it will run a hook — trust an unattended `codex
    # exec` never has. Without this flag every PreToolUse hook, including
    # guard-unattended.py, is SILENTLY SKIPPED: no denial, no warning. This
    # flag runs already-enabled hooks without requiring that interactive
    # trust, which is what turns enforcement ON for this unattended path.
    # Verified live 2026-08-14 by a negative smoke against the same shell
    # invocation shape in bin/council-lib.sh (see ops/codex-hook-smoke.sh):
    # the identical probe ran clean without the flag and was correctly
    # blocked with it.
    return [codex_bin, "exec", "--sandbox", "read-only",
            "--dangerously-bypass-hook-trust", prompt]


def build_grok_command(grok_bin: str, prompt: str, cwd: Path) -> list[str]:
    """LIVE-VERIFIED 2026-08-06 against grok 0.2.118 (see the module
    docstring's verification log). `--sandbox read-only` is genuinely
    kernel-enforced (macOS Seatbelt): a write attempt inside a real git
    worktree produced an FsViolation event and left the worktree git-clean,
    with NO --always-approve present. `--cwd` is passed explicitly (in
    addition to the subprocess launch dir set by run_one_reviewer) because
    that is exactly the invocation shape tested live. `--output-format json`
    is used (not the plain default) so the CLI's own envelope carries real
    cost/usage/session metadata for the finding's meta block — see
    parse_grok_output(), which unwraps that envelope before handing the
    inner text to the shared extract_json(). `--max-turns 20` bounds a
    runaway agentic loop independent of the wall-clock timeout_minutes
    budget; `--no-auto-update` keeps an automated run from being interrupted
    by an update check (update messages go to stderr per Grok's own docs, so
    this is a minor determinism improvement, not a correctness fix)."""
    return [grok_bin, "-p", prompt, "--sandbox", "read-only", "--cwd", str(cwd),
            "--output-format", "json", "--max-turns", "20", "--no-auto-update"]


def extract_json(text: str) -> dict:
    """A reviewer is asked for exactly one JSON object and nothing else, but
    this parses defensively: try the whole trimmed string first, then fall
    back to the first balanced {...} span, so a stray line of reasoning
    wrapped around the real payload does not turn a good review into a hard
    failure. If nothing parses, the raw text is preserved rather than
    discarded — never destroy output just because it didn't parse."""
    text = text.strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass
    start = text.find("{")
    if start != -1:
        depth = 0
        for i in range(start, len(text)):
            if text[i] == "{":
                depth += 1
            elif text[i] == "}":
                depth -= 1
                if depth == 0:
                    candidate = text[start:i + 1]
                    try:
                        return json.loads(candidate)
                    except json.JSONDecodeError:
                        break
    return {"parse_error": True, "raw_output": text}


def parse_codex_output(stdout: str) -> tuple[dict, dict]:
    """codex exec --json is assumed (UNVERIFIED — see build_codex_command) to
    emit the review JSON directly on stdout, so this is a thin wrapper around
    the shared extract_json with no reviewer-specific envelope to unwrap."""
    return extract_json(stdout), {}


def parse_grok_output(stdout: str) -> tuple[dict, dict]:
    """grok -p ... --output-format json wraps the model's final response in
    the CLI's OWN envelope — LIVE-CONFIRMED shape:
      {"text": "...", "stopReason": "end_turn", "sessionId": "...",
       "total_cost_usd": 0.03, "usage": {...}, "num_turns": 2, ...}
    (see 14-headless-mode.md's documented `json` format, matched exactly by
    this build's own live test runs). The REVIEW CONTRACT'S json object is
    the CONTENT of the "text" field, not the envelope itself — so this
    unwraps one layer, then hands the inner text to the same extract_json()
    codex uses, and returns whatever cost/usage/session fields the envelope
    carried as bonus metadata for the finding's meta block. A malformed or
    non-JSON envelope (e.g. Grok's own documented `{"type":"error",...}`
    failure shape) degrades to treating the raw stdout as the payload via
    extract_json, rather than raising — consistent with this file's rule that
    unparseable output is preserved, not discarded."""
    try:
        envelope = json.loads(stdout)
    except json.JSONDecodeError:
        return extract_json(stdout), {}
    if not isinstance(envelope, dict):
        return extract_json(stdout), {}
    if envelope.get("type") == "error":
        return {"parse_error": True, "grok_error": envelope.get("message")}, {}
    text = envelope.get("text", "")
    review = extract_json(text) if text else {"parse_error": True, "raw_output": stdout[:2000]}
    extra_meta = {
        "session_id": envelope.get("sessionId"),
        "stop_reason": envelope.get("stopReason"),
        "total_cost_usd": envelope.get("total_cost_usd"),
        "num_turns": envelope.get("num_turns"),
    }
    return review, extra_meta


# The per-backend registry. Adding a THIRD reviewer later is: write its
# find_*_binary / build_*_command / parse_*_output trio, add one entry here,
# add its REVIEW_TOKEN_VAR_BY_BACKEND / ACTOR_SLUG_BY_BACKEND lines, add its
# actor row to pipelines/provision-review-council.sql. Nothing in
# process_request() or run_one_reviewer() names a backend by hand.
BACKENDS: dict[str, dict] = {
    "codex": {
        "find_binary": find_codex_binary,
        "build_command": build_codex_command,
        "parse_output": parse_codex_output,
        "install_hint": "npm i -g @openai/codex (or confirm the Codex app install location)",
        "model_label": "codex (ChatGPT-token auth, per ~/.codex/auth.json — model string "
                        "unknown at build time; no binary was installed to query it)",
    },
    "grok": {
        "find_binary": find_grok_binary,
        "build_command": build_grok_command,
        "parse_output": parse_grok_output,
        "install_hint": "npm i -g @xai-official/grok, then `grok login` (OAuth device flow)",
        "model_label": "grok-4.5-build (Grok Build CLI, OIDC entitlement token per "
                        "~/.grok/auth.json — no XAI_API_KEY; live-confirmed 2026-08-06)",
    },
}


# ---------------------------------------------------------------------------
# 5. record-finding write (the reviewers' ONLY write — one call per reviewer)
# ---------------------------------------------------------------------------

def pick_subject(req: dict) -> str:
    """subject = the work order/record ref or the repo commit, in that
    preference order: a record_ref is the most specific and most likely to
    resolve through the Worker's resolveSubject(); work_order is free text
    that MAY resolve as a deal name; the commit sha is the last resort and is
    NOT expected to resolve against v_ref_index on its own (a bare sha is not
    a party/deal ref) — that failure, if it happens, surfaces through the
    normal record-finding error path and is handled by write_failure_finding
    / the failed/ status sidecar, not hidden here. Shared across every
    reviewer on a request — they all point at the same subject, which is
    correct: they are reviewing the same evidence, just as different seats."""
    evidence = req.get("evidence", {})
    if evidence.get("record_refs"):
        return str(evidence["record_refs"][0])
    if evidence.get("work_order"):
        return str(evidence["work_order"])
    return f"commit:{evidence.get('commit_sha', 'unknown')}"


def read_review_token(backend: str) -> str:
    """Each backend reads its OWN env var (CARR_MCP_REVIEW_TOKEN_CODEX /
    CARR_MCP_REVIEW_TOKEN_GROK) — never a shared token — so which reviewer
    posted a finding is determined server-side by which bearer authenticated
    it, not by anything this process asserts in the payload."""
    var = REVIEW_TOKEN_VAR_BY_BACKEND[backend]
    token = os.environ.get(var)
    if token:
        return token
    if os.path.isfile(MCP_TOKENS_ENV):
        for line in open(MCP_TOKENS_ENV):
            line = line.strip()
            if line.startswith(f"{var}="):
                return line.split("=", 1)[1].strip().strip('"').strip("'")
    return ""


def build_finding_payload(req: dict, review_result: dict, meta: dict, backend: str) -> dict:
    """Pure function: builds the record-finding arguments object for ONE
    reviewer's outcome. Kept separate from the curl call so the test harness
    can assert its shape without any network access. `source` names the
    specific reviewer (its actor slug), the shared contract version, and the
    request id — so even reading record_flag rows directly, without ever
    touching this process's logs, tells a human which reviewer said what."""
    kind = f"{req.get('kind', 'code')}_review"
    value = {
        "review": review_result,
        "meta": meta,
    }
    slug = ACTOR_SLUG_BY_BACKEND.get(backend, f"{backend}-reviewer")
    return {
        "idempotency_key": str(uuid.uuid4()),
        "subject": pick_subject(req),
        "kind": kind,
        "value": value,
        "found": True,
        "source": f"{slug} / contract {CONTRACT_VERSION} / request {req.get('request_id')}",
    }


def build_rpc_envelope(finding_args: dict, rpc_id: int = 1) -> dict:
    return {
        "jsonrpc": "2.0",
        "id": rpc_id,
        "method": "tools/call",
        "params": {"name": "record-finding", "arguments": finding_args},
    }


def build_curl_command(url: str, token: str, rpc_payload: dict) -> list[str]:
    """Pure function: the exact argv curl is invoked with. Split out so the
    test harness can build this against a stub token/url and assert its
    shape with no live call — per the build instructions, the harness proves
    the payload builds correctly without ever hitting the network."""
    return [
        "curl", "-sS", "--max-time", "30", "-X", "POST", url,
        "-H", f"Authorization: Bearer {token}",
        "-H", "content-type: application/json",
        "-d", json.dumps(rpc_payload),
    ]


def post_finding(finding_args: dict, url: str = CARR_MCP_URL,
                  token: Optional[str] = None, backend: str = "codex",
                  runner: Callable = subprocess.run) -> tuple[bool, dict]:
    """POSTs via curl (subprocess), never a Python HTTP client — this repo is
    stdlib-only by convention (requirements.txt) and the house pattern for
    talking to the Worker from a script is curl (mcp-server/smoke-reads.sh).
    `runner` is injectable so the test harness can stub the actual call.
    `token`, when omitted, is read for `backend` (defaults to "codex" for
    call-site compatibility with the original single-backend build; every
    internal caller in this file now passes both explicitly). Returns
    (ok, parsed_response_or_error)."""
    if token is None:
        token = read_review_token(backend)
    if not token:
        return False, {"error": "no_review_token",
                        "hint": f"set {REVIEW_TOKEN_VAR_BY_BACKEND.get(backend, backend)} "
                                f"or add it to {MCP_TOKENS_ENV}"}
    rpc_payload = build_rpc_envelope(finding_args)
    cmd = build_curl_command(url, token, rpc_payload)
    try:
        result = runner(cmd, capture_output=True, text=True, timeout=45)
    except Exception as e:
        return False, {"error": "curl_exec_failed", "detail": str(e)}
    if result.returncode != 0:
        return False, {"error": "curl_nonzero_exit", "returncode": result.returncode,
                        "stderr": (result.stderr or "")[:500]}
    try:
        body = json.loads(result.stdout)
    except json.JSONDecodeError:
        return False, {"error": "non_json_response", "raw": (result.stdout or "")[:500]}
    if not isinstance(body, dict):
        return False, {"error": "non_object_response", "raw": (result.stdout or "")[:500]}
    if "error" in body:
        return False, {"error": "rpc_error", "detail": body["error"]}
    # An MCP tool error arrives as result.isError with a plain-text message
    # (e.g. "Denied"), not as a JSON-RPC error. It is a refusal, never a
    # success, whatever the text parses to.
    if isinstance(body.get("result"), dict) and body["result"].get("isError"):
        return False, {"error": "verb_error", "detail": body["result"]}
    content = body.get("result", {}).get("content", [])
    text = content[0]["text"] if content else "{}"
    try:
        inner = json.loads(text)
    except json.JSONDecodeError:
        inner = {"raw": text}
    if inner.get("isError") or (isinstance(inner, dict) and inner.get("error")):
        return False, {"error": "verb_error", "detail": inner}
    return True, inner


def write_failure_finding(req: dict, backend: str, reason: str, meta: dict,
                           runner: Callable = subprocess.run) -> tuple[bool, dict]:
    """Partial failure must be VISIBLE, never silent — so even a failed
    review attempts to land a record-finding saying so, under THAT
    reviewer's own bearer, with found:true and the failure itself as the
    value. This can still fail (e.g. the subject does not resolve, or the
    Worker is unreachable) — that outcome is returned to the caller, which
    falls back to the local failed/ status sidecar as the last-resort record
    of what happened. Never touches or blocks any other reviewer's attempt.
    `runner` MUST be threaded through from the caller (run_one_reviewer's own
    `post_runner`) rather than left at its subprocess.run default whenever the
    caller is a test or any other context that must not make a real network
    call — a failure path is exactly as capable of a live call as a success
    path, and forgetting to thread this through was caught live in this
    file's own test suite (an earlier version of run_one_reviewer's
    timeout/error branches called this without passing post_runner, so the
    offline test's forced-failure cases were quietly reaching the real
    Worker URL with a fake token instead of staying stubbed)."""
    review_result = {"failed": True, "reason": reason}
    finding_args = build_finding_payload(req, review_result, meta, backend)
    return post_finding(finding_args, token=read_review_token(backend), backend=backend, runner=runner)


# ---------------------------------------------------------------------------
# orchestration
# ---------------------------------------------------------------------------

def write_status_sidecar(request_path: Path, status: str, detail: dict) -> None:
    sidecar = request_path.with_suffix(request_path.suffix + ".status.json")
    payload = {
        "status": status,
        "recorded_at": datetime.now(timezone.utc).isoformat(),
        "detail": detail,
    }
    with open(sidecar, "w") as fh:
        json.dump(payload, fh, indent=2, default=str)


def _meta(req: dict, backend: str, binary_path: str, started_at: datetime,
          finished_at: datetime, completion_status: str) -> dict:
    meta = _base_meta(req, backend, binary_path, started_at, finished_at, completion_status)
    if ledger.is_active(req):
        # Only on a carry-forward request, so a no-PR row is unchanged. This is
        # what lets a later round find this row by PR identity.
        meta["pr"] = req["pr"]
        meta["prior_commits"] = req["prior_commits"]
    return meta


def _base_meta(req: dict, backend: str, binary_path: str, started_at: datetime,
               finished_at: datetime, completion_status: str) -> dict:
    return {
        "reviewer": backend,
        "actor_slug": ACTOR_SLUG_BY_BACKEND.get(backend, f"{backend}-reviewer"),
        "model": BACKENDS[backend]["model_label"],
        "runtime_seconds": round((finished_at - started_at).total_seconds(), 1),
        "contract_version": CONTRACT_VERSION,
        "request_contract_version": req.get("contract_version"),
        "evidence": req.get("evidence"),
        "started_at": started_at.isoformat(),
        "finished_at": finished_at.isoformat(),
        "completion_status": completion_status,
        "binary_path": binary_path,
    }


def run_one_reviewer(backend: str, req: dict, prompt: str, cwd: Path,
                      spec: Optional[dict] = None,
                      subprocess_runner: Callable = subprocess.run,
                      post_runner: Callable = subprocess.run) -> dict:
    """Runs exactly ONE reviewer against the shared prompt/worktree and
    returns an outcome dict — {"status": "ok"|"failed"|"skip", ...} — and
    NEVER raises past this call site, so a loop over several reviewers (see
    process_request) cannot have one reviewer's exception take down another's
    attempt. `spec` defaults to BACKENDS[backend]; the test harness overrides
    it with fakes to exercise every branch offline. `subprocess_runner` and
    `post_runner` are separately injectable (the reviewer CLI call vs. the
    curl call to the Worker) for the same reason."""
    spec = spec or BACKENDS[backend]
    started_at = datetime.now(timezone.utc)
    request_id = req["request_id"]

    binary_path, checked = spec["find_binary"]()
    if not binary_path:
        log(f"SKIP  request={request_id} reviewer={backend} — INSTALL NEEDED: "
            f"not found. Checked: {'; '.join(checked)}. Joe's step: "
            f"{spec['install_hint']}. This runner never installs anything itself.")
        return {"status": "skip", "reason": "binary_not_found", "checked": checked}

    argv = spec["build_command"](binary_path, prompt, cwd)
    try:
        proc = subprocess_runner(argv, cwd=str(cwd), capture_output=True, text=True,
                                  timeout=req["timeout_minutes"] * 60)
    except subprocess.TimeoutExpired:
        finished_at = datetime.now(timezone.utc)
        meta = _meta(req, backend, binary_path, started_at, finished_at, "timeout")
        ok, resp = write_failure_finding(req, backend,
            f"{backend} timed out after {req['timeout_minutes']}min", meta, runner=post_runner)
        log(f"FAIL  request={request_id} reviewer={backend} — timed out "
            f"(failure finding {'written' if ok else 'NOT written: ' + str(resp)})")
        return {"status": "failed", "reason": "timeout", "finding_posted": ok,
                "finding_response": resp}
    except Exception as e:
        finished_at = datetime.now(timezone.utc)
        meta = _meta(req, backend, binary_path, started_at, finished_at, f"{backend}_exec_error")
        ok, resp = write_failure_finding(req, backend, f"{backend} exec raised: {e}", meta,
                                          runner=post_runner)
        log(f"FAIL  request={request_id} reviewer={backend} — exec raised {e!r} "
            f"(failure finding {'written' if ok else 'NOT written: ' + str(resp)})")
        return {"status": "failed", "reason": f"exec_raised: {e}", "finding_posted": ok,
                "finding_response": resp}

    finished_at = datetime.now(timezone.utc)
    if proc.returncode != 0:
        detail = f"{backend} exited {proc.returncode}: {(proc.stderr or proc.stdout or '')[:500]}"
        meta = _meta(req, backend, binary_path, started_at, finished_at, f"{backend}_error")
        ok, resp = write_failure_finding(req, backend, detail, meta, runner=post_runner)
        log(f"FAIL  request={request_id} reviewer={backend} — {detail} "
            f"(failure finding {'written' if ok else 'NOT written: ' + str(resp)})")
        return {"status": "failed", "reason": detail, "finding_posted": ok,
                "finding_response": resp}

    review_result, extra_meta = spec["parse_output"](proc.stdout)
    meta = _meta(req, backend, binary_path, started_at, finished_at, "completed")
    meta.update({k: v for k, v in extra_meta.items() if v is not None})
    finding_args = build_finding_payload(req, review_result, meta, backend)
    ok, resp = post_finding(finding_args, token=read_review_token(backend),
                             backend=backend, runner=post_runner)
    if not ok:
        log(f"FAIL  request={request_id} reviewer={backend} — completed but "
            f"record-finding failed: {resp}")
        return {"status": "failed", "reason": "record-finding post failed",
                "post_response": resp, "review_result": review_result, "meta": meta}

    log(f"OK    request={request_id} reviewer={backend} — finding recorded "
        f"(flag_id={resp.get('flag_id')}, subject={resp.get('subject_id')})")
    if not ledger.is_active(req):
        return {"status": "ok", "finding_response": resp, "meta": meta}
    # Contained like every other per-reviewer failure: malformed reviewer
    # output, or any bug in reconciliation, fails THIS reviewer and never
    # escapes to stop the next reviewer or the status sidecar.
    try:
        return _record_dispositions(req, backend, review_result, resp, meta, post_runner)
    except Exception as e:  # noqa: BLE001 — per-reviewer boundary, reported, never swallowed
        log(f"FAIL  request={request_id} reviewer={backend} — carry-forward raised "
            f"{type(e).__name__}: {e}")
        return {"status": "failed", "finding_response": resp, "meta": meta,
                "reason": f"carry-forward raised {type(e).__name__}: {e}",
                "dispositions": [], "carry_forward_problems": [f"raised {type(e).__name__}: {e}"]}


def _valid_finding_receipt(resp) -> bool:
    """A disposition counts as recorded only on a structurally valid
    record-finding receipt: ok is literally true and flag_id is a uuid. An
    error envelope, an empty object, or a partial answer is not a receipt."""
    if not isinstance(resp, dict) or resp.get("ok") is not True:
        return False
    fid = resp.get("flag_id")
    if not isinstance(fid, str):
        return False
    try:
        uuid.UUID(fid)
    except ValueError:
        return False
    return True


def _record_dispositions(req: dict, backend: str, review_result: dict, resp: dict,
                         meta: dict, post_runner: Callable) -> dict:
    """CARRY-FORWARD RECORD WRITE. One record-finding row per open prior
    finding, kind code_review_disposition, under THIS reviewer's own bearer
    (append-only; see review_findings_ledger's module docstring for why a new
    row rather than an update). Closed prior findings were never sent and are
    never re-recorded. Any prior finding this reviewer failed to classify
    validly, and any refused write, makes the reviewer's outcome "failed" so
    the request lands in failed/ — the finding stays open for the next round
    rather than silently vanishing.

    INCIDENT BACK-LINK: no link verb joins an incident to a finding
    (open-incident's related_kind has no 'finding'), and this runner never
    files one — it knows nothing about incidents. The link is a MANUAL
    two-step recipe over existing verbs, with a worked example in
    build_disposition_payload's docstring: open-incident whose `observed`
    names "record flag <flag_id> #<index>", then a code_review_disposition
    row built with incident_ref=..., which stores value.incident_ref."""
    request_id = req["request_id"]
    opens = ledger.open_prior_findings(req)
    accepted, problems = ledger.reconcile_dispositions(opens, review_result)
    slug = ACTOR_SLUG_BY_BACKEND.get(backend, f"{backend}-reviewer")
    token = read_review_token(backend)
    written = []
    for a in accepted:
        args = ledger.build_disposition_payload(req, a["prior"], a["disposition"], a["reason"],
                                                a["evidence"], backend, actor_slug=slug)
        ok, dresp = post_finding(args, token=token, backend=backend, runner=post_runner)
        if ok and not _valid_finding_receipt(dresp):
            ok, dresp = False, {"error": "invalid_receipt", "detail": dresp}
        written.append({"id": a["id"], "prior_flag_id": a["prior"]["flag_id"],
                        "prior_index": a["prior"]["index"], "disposition": a["disposition"],
                        "reason": a["reason"], "commit_sha": req["evidence"]["commit_sha"],
                        "posted": ok, "flag_id": dresp.get("flag_id") if ok else None,
                        **({} if ok else {"post_response": dresp})})
        if not ok:
            problems.append(f"{a['id']} {a['disposition']} disposition not recorded: {dresp}")
    for p in problems:
        log(f"FAIL  request={request_id} reviewer={backend} — carry-forward: {p}")
    log(f"{'FAIL' if problems else 'OK  '}  request={request_id} reviewer={backend} — "
        f"{sum(1 for w in written if w['posted'])}/{len(opens)} prior finding dispositions recorded")
    outcome = {"status": "failed" if problems else "ok", "finding_response": resp, "meta": meta,
               "dispositions": written, "carry_forward_problems": problems}
    if problems:
        outcome["reason"] = "carry-forward incomplete: " + "; ".join(problems)
    return outcome


def process_request(request_path: Path) -> int:
    try:
        req = load_request(request_path)
    except RequestError as e:
        log(f"FAIL  request={request_path.name} — schema invalid: {e}")
        write_status_sidecar(request_path, "failed", {"reason": f"schema_invalid: {e}"})
        return EX_FAIL

    request_id = req["request_id"]
    reviewers = active_reviewers(req)
    if not reviewers:
        log(f"SKIP  request={request_id} — no reviewer this file recognizes in "
            f"reviewers={req.get('reviewers')}; leaving untouched")
        return EX_SKIP

    worktree = None
    outcomes: dict[str, dict] = {}
    try:
        if req["kind"] == "code":
            worktree = worktree_add(req["evidence"]["commit_sha"], request_id)
            cwd = worktree
        else:
            # design review: no code checkout — run in the repo root, read-only,
            # exactly like the code path's sandboxing (no write access assumed).
            cwd = REPO

        # THE SECURITY LENS (Joe, 2026-08-06: "Also make the security lens").
        # Auto-appended — never at the request author's discretion — whenever the
        # commit touches the system's enforcement or trust surface. The trigger
        # exists because loop #163 (the egress guard covering only Bash) was found
        # BY ACCIDENT; a lens finds that class by assignment. Fails open with a
        # log line: a git error must never block a review that would otherwise run.
        if req["kind"] == "code":
            lens = security_lens_if_triggered(req["evidence"]["commit_sha"])
            if lens:
                req["lenses"] = list(req.get("lenses") or []) + [lens]
                log(f"security lens armed for request={request_id} (sensitive paths touched)")

        prompt = render_contract_prompt(req)

        # PER-REVIEWER INDEPENDENCE: each call is isolated by run_one_reviewer's
        # own try/except, so one reviewer's crash, timeout, or missing binary
        # never prevents the next reviewer in this loop from being attempted.
        for backend in reviewers:
            outcomes[backend] = run_one_reviewer(backend, req, prompt, cwd)
    except WorktreeError as e:
        # A worktree failure is request-wide (no reviewer could even start),
        # so every named reviewer gets its own failure finding rather than
        # silently having no outcome at all.
        finished_at = datetime.now(timezone.utc)
        for backend in reviewers:
            meta = _meta(req, backend, "n/a", finished_at, finished_at, "worktree_error")
            ok, resp = write_failure_finding(req, backend, str(e), meta)
            log(f"FAIL  request={request_id} reviewer={backend} — {e} "
                f"(failure finding {'written' if ok else 'NOT written: ' + str(resp)})")
            outcomes[backend] = {"status": "failed", "reason": str(e),
                                  "finding_posted": ok, "finding_response": resp}
    finally:
        if worktree is not None:
            worktree_remove(request_id)

    statuses = {b: o["status"] for b, o in outcomes.items()}
    log(f"SUMMARY request={request_id}  " +
        ", ".join(f"{b}={s}" for b, s in statuses.items()))

    if all(s == "skip" for s in statuses.values()):
        overall_status, exit_code = "skipped", EX_SKIP
    elif any(s == "failed" for s in statuses.values()):
        overall_status, exit_code = "failed", EX_FAIL
    else:
        overall_status, exit_code = "done", EX_OK

    detail: dict = {"reviewers": outcomes}
    if ledger.is_active(req):
        detail["carry_forward_resolution"] = _carry_forward_resolution(req, outcomes)
    write_status_sidecar(request_path, overall_status, detail)
    return exit_code


def _carry_forward_resolution(req: dict, outcomes: dict) -> list:
    """The effective disposition of each prior finding after this request,
    from the rows every reviewer ACTUALLY recorded, resolved by the ledger's
    order-independent policy (newest head wins; at one head the most
    conservative disposition wins). Reviewer order in the request cannot
    change it. Sorted so the sidecar is byte-stable."""
    rows = []
    for outcome in outcomes.values():
        for d in outcome.get("dispositions") or []:
            if d.get("posted"):
                rows.append({"disposition": d["disposition"], "reason": d.get("reason"),
                             "commit_sha": d["commit_sha"],
                             "prior": {"flag_id": d["prior_flag_id"], "index": d["prior_index"]}})
    order = list(req["prior_commits"]) + [req["evidence"]["commit_sha"]]
    try:
        resolved = ledger.resolve_ledger_dispositions(rows, order)
    except ledger.CarryForwardError as e:
        log(f"FAIL  request={req['request_id']} — carry-forward resolution: {e}")
        return [{"error": str(e)}]
    return [{"flag_id": k[0], "index": k[1], **v} for k, v in sorted(resolved.items())]


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("request_file", help="path to one request JSON file")
    args = parser.parse_args(argv)

    path = Path(args.request_file)
    if not path.is_file():
        print(f"no such file: {path}", file=sys.stderr)
        return EX_FAIL
    return process_request(path)


if __name__ == "__main__":
    sys.exit(main())
