"""typesafe_client.py — the one way CARR calls TypeSafe's Jev decision model.

WHAT JEV IS, because the shape decides how this file is written. Jev takes text
plus typed questions and returns calibrated probabilities. It does not write
text, produce code, or explain itself. Three question types exist and nothing
else: a yes/no probability (noul), a pick-one over a defined set (choice), and
a position on ordered levels (score). Code owns the workflow; the model supplies
the semantic judgment where ordinary code has none.

THIS FILE IS A LIBRARY ON PURPOSE. It carries no shebang and no main guard, and
it must never gain either. A .py file with either becomes a registered script
entrypoint in the sealed source inventory — see isScriptEntrypoint() in
ops/scac-mutation-inventory.mjs — which moves the frontier and owes a
forward-only registry successor. Callers are entrypoints that already exist.
Adding a shebang here turns a one-line import into a multi-file sealing cascade.

That detector is a REGEX OVER THE WHOLE FILE and does not know what a docstring
is, so writing the guard construct out even as an example — in prose, in a
comment, inside backticks — is enough to seal this file. The first draft of
this paragraph did exactly that and ops/typesafe-client-selftest.py caught it.
Describe the construct; never spell it.

FOUR THINGS THE VENDOR'S OWN DOCS SAY THAT THIS FILE ENFORCES RATHER THAN
TRUSTS A CALLER TO REMEMBER:

  1. ASK TOGETHER. Jev ingests the state once and evaluates every question
     against it in parallel. The published parallel-questions measurement put
     one batched request at 12.2x cheaper and 10.0x faster than the same
     questions sent separately, with no change in the answers. So `ask` takes a
     MAP of questions, never one, and there is deliberately no single-question
     helper to reach for by accident.

  2. KEEP ARITHMETIC IN CODE. jev-1.13 reads dates as text rather than as
     ordered quantities, and does not count reliably. Asking it which of two
     dates is earlier, how far apart they are, or how many items match is
     documented as unreliable. Extraction is a judgment and belongs here;
     comparison, duration, and totals are not and belong in the caller.

  3. TYPED IS NOT TRUE. The response schema guarantees the SHAPE of an answer,
     never its correctness — "cannot hallucinate" means it cannot emit an
     illegal type, and it can still pick the wrong option. Every caller needs a
     threshold and a fallback, which is why `decide` exists beside `ask` and
     returns an explicit escalate outcome rather than a bare value.

  4. FILTER FIRST. Accuracy falls as the state grows with content unrelated to
     the decision, and there is a hard ceiling besides. Retrieve and narrow in
     code, then send only the fields the question needs.

CREDENTIAL. Read from ~/.config/carr/typesafe.env, mode 600, outside the repo,
recorded by name in secrets-inventory.md; only the direct transport reads it.
The Worker route signs its admission with JEV_ADMISSION_SECRET from
~/.config/carr/jev-admission.env instead. The value is never logged, never
echoed into an exception, and never written to a receipt. On an HTTP error this
module reports the status and a truncated response body, and deliberately does
not report the request it sent, because the request carries the bearer token.

AUTHORITY. Joe ruled on 2026-09-17 that CARR records, client and deal material
included, may be sent to third-party model APIs and admitted this vendor on that
basis. Network reachability is separate and already granted: both hosts sit in
KNOWN_HOSTS in hooks/guard-unattended.py.
"""

import base64
import glob
import hashlib
import hmac
import importlib.util
import json
import math
import os
import re
import sqlite3
import shutil
import uuid
import subprocess
import sys
from contextlib import closing, contextmanager
from contextvars import ContextVar
import time
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone
from collections import Counter
from pathlib import Path
from functools import partial

ENDPOINT = "https://api.typesafe.ai/v1/systemone"
KEY_PATH = os.path.expanduser("~/.config/carr/typesafe.env")
# The Worker route's admission secret: shared by the Worker and the local
# reservation owner only, so a Worker caller never needs the vendor key.
ADMISSION_KEY_PATH = os.path.expanduser("~/.config/carr/jev-admission.env")
ADMISSION_KEY_NAME = "JEV_ADMISSION_SECRET"
REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
# Entry points may import with only ops/ on sys.path.
if REPO not in sys.path:
    sys.path.insert(0, REPO)
_judge_spec = importlib.util.spec_from_file_location(
    "carr_judge_interface", os.path.join(REPO, "tools", "judge", "interface.py"))
assert _judge_spec and _judge_spec.loader
JUDGE = importlib.util.module_from_spec(_judge_spec)
_judge_spec.loader.exec_module(JUDGE)


def _canonical_repo_root(fallback):
    """The ONE repo root every worktree of this repository shares, so a call
    made from any worktree's copy of this file and a hook reading from the
    canonical checkout resolve to the SAME physical out/jev-calls.jsonl.

    Round-2 fix (2026-09-24): `ask()` used to write next to whichever copy of
    this file was executing (`REPO`, worktree-relative) while
    hooks/completion-evidence-gate.py read from the canonical checkout's
    out/ — two different physical files, so a real call from a worktree was
    invisible to the gate. `git rev-parse --path-format=absolute
    --git-common-dir` resolves to the shared `.git` directory across every
    worktree of one repository (verified: an absolute path, git 2.54.0);
    its parent is the canonical repo root regardless of which worktree is
    running. Falls back to `fallback` (this file's own on-disk REPO) on any
    failure — never raises, matching this module's fail-open posture.
    """
    try:
        out = subprocess.run(
            ["git", "rev-parse", "--path-format=absolute", "--git-common-dir"],
            cwd=fallback, capture_output=True, text=True, timeout=5, check=True,
        ).stdout.strip()
        if out:
            return os.path.dirname(out)
    except Exception:
        pass
    return fallback


CANONICAL_REPO = _canonical_repo_root(REPO)
# WHERE A CALL BECOMES OBSERVABLE (decision 0b11c89b, 2026-09-24). A missing
# Jev call used to be checkable only by grepping a session's Bash history for
# the string "typesafe_client", which a bare `echo typesafe_client` also
# satisfied. Every successful ask() now appends one best-effort row here —
# never on failure, never the request or the answers, just enough for a
# reader (lib/jev_required_actions.py) to bind a call to a session, a time
# window, and the facets it named. Failed attempts and cache hits carry ok=false
# and cannot count as evidence that the vendor answered. Writing this must never turn a working
# Jev call into a failure, so every step here is wrapped and swallowed.
# Uses CANONICAL_REPO (not the possibly-worktree-local REPO) so every
# worktree's ask() and the canonical checkout's Stop-hook reader agree on one
# physical file — see _canonical_repo_root above.
JEV_CALLS_LOG = os.path.join(CANONICAL_REPO, "out", "jev-calls.jsonl")
# Receipt destinations may vary for evals; the spend budget never does.
JEV_DAILY_CAP_LOG = JEV_CALLS_LOG
JUDGE_CACHE_PATH = os.path.join(CANONICAL_REPO, "out", "jev-judge-cache.sqlite3")
with open(os.path.join(REPO, "ops", "config", "jev-cost-guard.v1.json"), encoding="utf-8") as _config_file:
    JEV_COST_CONFIG = json.load(_config_file)
JUDGE_CACHE_TTL_SECONDS = JEV_COST_CONFIG["judge_cache_ttl_seconds"]
# The env vars a caller's own session id is found under, same set
# ops/settlement-run-token.py's NATIVE_SESSION_KEYS already uses.
SESSION_ID_ENV_KEYS = ("CODEX_THREAD_ID", "CLAUDE_CODE_SESSION_ID",
                       "CLAUDE_CODE_HOST_SESSION_ID")
KEY_NAME = "TYPESAFE_API_KEY"

# jev-latest is an alias and MOVES when a release ships, so answers can change
# with no change on our side. A caller that has tuned thresholds against a
# specific version should pass that version's exact id instead and step forward
# deliberately. The response reports which version actually answered; `ask`
# returns it untouched under "model" so a receipt can record it.
DEFAULT_MODEL = "jev-latest"

# The documented ceiling is 64k tokens per request for state plus every
# question, and 32k for state plus the single longest question. This is a
# CHARACTER guard against the tighter of those, at a deliberately pessimistic
# 3 characters per token, and it is an ESTIMATE rather than a token count: it
# exists so an oversized state fails here, named and local, instead of as an
# opaque 422 from the service. Narrow the state in code rather than raising it.
STATE_BUDGET_CHARS = 32_000 * 3

TIMEOUT_SECONDS = 60.0
RATE_LIMIT_RETRIES = 3


class TypeSafeError(RuntimeError):
    """Any failure reaching or being understood by the service."""


class JevCallRefused(TypeSafeError):
    """A paid call this client declined before any transport ran.

    `code` is one of REFUSAL_CODES. vendor_spend_unknown declines only the
    retry: the Worker attempt it follows is receipted separately and keeps
    its reservation. A refusal is never billable, so it is
    excluded from the daily-cap seed. `resets_at` is the UTC instant a budget
    refusal lifts (None for policy refusals, which do not lift on a clock).
    """

    def __init__(self, message, *, code, site=None, scope=None, resets_at=None):
        super().__init__(message)
        self.code = code
        self.site = site
        self.scope = scope
        self.resets_at = resets_at


# THE CALL-SITE REGISTRY (2026-10-04 system-wide audit). Four point-fixes in
# three days each caught one burner after the money was spent, because any
# code path could reach the vendor and the only bound was one shared daily
# counter. Now a paid call needs a registered site, the attribution that site
# declares, and room in the site's own hourly and daily budget, beneath a
# global hourly cap. All four are checked before any transport and before
# the daily reservation, fail closed, and are logged without being paid.
JEV_CALL_SITES_PATH = os.path.join(REPO, "ops", "config", "jev-call-sites.v1.json")
BUDGET_REFUSALS = ("hourly_paid_call_cap", "site_hourly_budget", "site_daily_budget")
POLICY_REFUSALS = ("fixture_offline", "unregistered_caller", "unattributed_call", "unattended_worker_off",
                   "call_site_registry_invalid")
# Declined because of what the vendor already said: its account is out of
# credit (HTTP 402), or a Worker attempt may already have been billed.
VENDOR_REFUSALS = ("vendor_credit_exhausted", "vendor_spend_unknown")
REFUSAL_CODES = ("daily_paid_call_cap",) + BUDGET_REFUSALS + POLICY_REFUSALS + VENDOR_REFUSALS
ATTRIBUTIONS = ("session", "session_or_job")
UNATTENDED_POLICIES = ("off", "allowed")
_SITE_FIELDS = {"caller", "trigger", "runs_in", "attribution", "unattended",
                "hourly_budget", "daily_budget", "owner", "value", "sources"}


def load_call_sites(path=None):
    """The validated registry: {"hourly_paid_call_cap": int, "sites": {caller: entry}}.

    Raises TypeSafeError on any malformed entry: an unreadable registry admits
    nothing, which is the fail-closed direction.
    """
    try:
        with open(path or JEV_CALL_SITES_PATH, encoding="utf-8") as fh:
            raw = json.load(fh)
    except (OSError, ValueError) as exc:
        raise TypeSafeError(f"Jev call-site registry unreadable ({type(exc).__name__})") from None
    if not isinstance(raw, dict) or raw.get("schema") != "carr-jev-call-sites/v1":
        raise TypeSafeError("Jev call-site registry has the wrong schema")
    cap = raw.get("hourly_paid_call_cap")
    if type(cap) is not int or cap < 0:
        raise TypeSafeError("Jev call-site registry needs an integer hourly_paid_call_cap")
    sites = {}
    for entry in raw.get("sites") or []:
        if not isinstance(entry, dict) or set(entry) != _SITE_FIELDS:
            raise TypeSafeError(f"Jev call-site entry has the wrong fields: {entry!r:.120}")
        caller = entry["caller"]
        if not isinstance(caller, str) or not re.fullmatch(r"[a-z0-9_.:-]+\*?", caller):
            raise TypeSafeError(f"Jev call-site caller is not a plain name: {caller!r}")
        if caller in sites:
            raise TypeSafeError(f"Jev call-site caller registered twice: {caller}")
        if entry["attribution"] not in ATTRIBUTIONS or entry["unattended"] not in UNATTENDED_POLICIES:
            raise TypeSafeError(f"Jev call-site {caller} has an unknown attribution or unattended policy")
        for budget in ("hourly_budget", "daily_budget"):
            if type(entry[budget]) is not int or entry[budget] < 0:
                raise TypeSafeError(f"Jev call-site {caller} needs an integer {budget}")
        if entry["hourly_budget"] > entry["daily_budget"]:
            raise TypeSafeError(f"Jev call-site {caller} hourly budget exceeds its daily budget")
        for text in ("trigger", "runs_in", "owner", "value"):
            if not isinstance(entry[text], str) or not entry[text].strip():
                raise TypeSafeError(f"Jev call-site {caller} needs a {text}")
        if not isinstance(entry["sources"], list) or not entry["sources"] or not all(
                isinstance(s, str) and s for s in entry["sources"]):
            raise TypeSafeError(f"Jev call-site {caller} needs its source files")
        sites[caller] = entry
    return {"hourly_paid_call_cap": cap, "sites": sites}


def call_site(caller, registry):
    """The registry entry for `caller`: an exact name, else a `prefix:*` entry."""
    sites = registry["sites"]
    if caller in sites:
        return sites[caller]
    for name, entry in sites.items():
        if name.endswith("*") and isinstance(caller, str) and caller.startswith(name[:-1]) \
                and len(caller) > len(name) - 1:
            return entry
    return None


def _job_label():
    """A job label, including the existing Quill recording daemon's launch path."""
    label = (os.environ.get("CARR_JEV_JOB") or "").strip()
    if label:
        return label
    service = (os.environ.get("XPC_SERVICE_NAME") or "").strip()
    return service if service.startswith("com.carr.") or service == "com.digimata.quill" else None


def _unattended():
    """The orchestrator's explicit marker for an unattended worker's environment."""
    return os.environ.get("CARR_JEV_WORKER", "").strip().lower() == "off"


def _truthy_env(name):
    return os.environ.get(name, "").strip().lower() in ("1", "true", "yes", "on")


def _fixture_offline():
    """Selftests and CI fixtures never pay. ops/ci.sh exports CARR_JEV_OFFLINE and
    its gates class exports CARR_HOOK_FIXTURE; a hook a selftest spawns inherits
    both. Measured 2026-10-04: a fixture prompt seen in three or more sessions
    accounted for 20-45% of each day's paid attempts, because a selftest that
    drops TYPESAFE_API_KEY from its environment still reaches the key FILE."""
    return _truthy_env("CARR_JEV_OFFLINE") or _truthy_env("CARR_HOOK_FIXTURE")


def _read_secret(path, name):
    """One named value from a 600-mode env file. Never logged."""
    try:
        with open(path, "r", encoding="utf-8") as handle:
            lines = handle.readlines()
    except OSError as err:
        raise TypeSafeError(
            f"cannot read the {name} credential at {path}: {err.strerror}. "
            "Joe creates it by hand; no agent holds the value."
        ) from None
    for line in lines:
        line = line.strip()
        if line.startswith(f"{name}="):
            value = line.split("=", 1)[1].strip()
            if not value:
                raise TypeSafeError(f"{path}: {name} is present but empty")
            return value
    raise TypeSafeError(f"{path}: no {name} line")


def read_api_key(path=KEY_PATH):
    """The vendor bearer token, used only by the direct transport."""
    return _read_secret(path, KEY_NAME)


def read_admission_secret():
    """The secret that signs a Worker admission; never the vendor key."""
    return _read_secret(ADMISSION_KEY_PATH, ADMISSION_KEY_NAME)


def noul(instructions, true=None, false=None):
    """A yes/no question. Returns the probability that the answer is yes.

    There is NO separate confidence on a noul: the probability is the whole
    answer, and 0.5 means the model finds yes and no about equally likely
    rather than meaning medium intensity. Use one noul per label when several
    labels could apply at once.

    Write `true` and `false` so they agree with `instructions`. A noul whose
    true side describes a no reads as contradictory and measurably degrades.
    """
    question = {"type": "noul", "instructions": instructions}
    criteria = {}
    if true is not None:
        criteria["true"] = true
    if false is not None:
        criteria["false"] = false
    if criteria:
        question["criteria"] = criteria
    problem = malformed_request("", {"built": question})
    if problem:
        raise TypeSafeError(problem)
    return question


def choice(instructions, options):
    """Pick one option from a defined set. `options` maps option -> rubric.

    Include an explicit no-match option wherever nothing may fit. The model
    cannot choose a value that was never offered, so for source-value selection
    check that the candidates actually cover the answer before blaming the
    judgment.
    """
    return _built_question("choice", instructions, options)


def score(instructions, levels):
    """Rate against ordered levels, lowest first.

    Each level must describe a concrete situation and stand on its own. The
    returned value is probability-weighted across the levels, so it is useful
    for comparing against a threshold and NOT for reconstructing an exact
    number by interpolating between levels — the docs call that out directly.
    """
    return _built_question("score", instructions, list(levels))


# THE FULL DISTRIBUTION, NOT JUST THE PICK (Joe's ruling: code decides, Jev
# only judges, and thresholds are calibrated per action). A pick and a
# confidence cannot be calibrated after the fact; the distribution it came from
# can. These readers turn one typed answer into the distribution over every
# option, its entropy in bits, and the top option, identically to the Worker's
# answerDistribution() in mcp-server/src/jev-call-receipt.js — both suites run
# ops/fixtures/jev-calibration/distribution-vectors.v1.json. Nothing here is a
# threshold: entropy is recorded so a threshold can later be MEASURED per
# question family (ops/jev_calibration.py), never assumed.
#
# The tolerance is numeric sanity for rounded vendor probabilities (three
# options at two decimals can miss one by 0.015), not a decision boundary.
PROBABILITY_SUM_TOLERANCE = 0.02
MOVING_MODEL_ALIAS = re.compile(r"(?:^|-)latest$")


def model_is_pinned(model):
    """True for an exact model version; false for a moving alias or nothing."""
    return isinstance(model, str) and bool(model.strip()) and not MOVING_MODEL_ALIAS.search(model.strip())


def state_sha256(state):
    """sha256 of the canonical JSON of the state; equals the Worker's state_sha256."""
    return _prompt_sha256(state)


def entropy_bits(probabilities):
    """Shannon entropy in bits of an already-normalized probability list."""
    return -sum(p * math.log2(p) for p in probabilities if p > 0) + 0.0


def _probability(value):
    return (not isinstance(value, bool) and isinstance(value, (int, float))
            and math.isfinite(value) and 0 <= value <= 1)


def answer_distribution(question, answer):
    """One answer as {type, distribution, distribution_complete,
    probability_sum, entropy_bits, top, top_probability}.

    `question` may be None (a log row that kept the answers but not the
    questions); options the answer names are then the whole option set.
    distribution is None when the vendor returned no probabilities; entropy is
    None unless every probability is valid and they sum to one within
    PROBABILITY_SUM_TOLERANCE. Missing offered options count as zero.
    """
    question = question if isinstance(question, dict) else {}
    answer = answer if isinstance(answer, dict) else {}
    kind = question.get("type") or answer.get("type")
    distribution = None
    top = None
    if kind == "noul":
        p = answer.get("noul")
        if _probability(p):
            distribution = {"true": p, "false": 1 - p}
    elif kind in ("choice", "score"):
        raw = answer.get("probabilities")
        criteria = question.get("criteria")
        if kind == "choice":
            offered = (list(criteria) if isinstance(criteria, dict) else
                       list(question.get("choices") or []))
            top = answer.get("choice") if isinstance(answer.get("choice"), str) else None
            if isinstance(raw, dict):
                keys = offered + [k for k in raw if k not in offered]
                distribution = {k: raw.get(k, 0) for k in keys}
        else:
            levels = criteria if isinstance(criteria, list) else []
            if isinstance(raw, dict):
                count = max([len(levels)] + [int(k) + 1 for k in raw
                                             if isinstance(k, str) and k.isascii() and k.isdigit()])
                distribution = {}
                for index in range(count):
                    level = levels[index] if index < len(levels) else None
                    value = raw.get(level) if level is not None and level in raw else raw.get(str(index), 0)
                    distribution[str(index)] = value
    complete = False
    total = None
    entropy = None
    top_probability = None
    if distribution is not None:
        values = list(distribution.values())
        if values and all(_probability(v) for v in values):
            total = sum(values)
            complete = abs(total - 1) <= PROBABILITY_SUM_TOLERANCE
            if complete and total > 0:
                entropy = entropy_bits([v / total for v in values])
            best = max(values)
            top = next(k for k, v in distribution.items() if v == best)
            top_probability = best
    return {"type": kind, "distribution": distribution, "distribution_complete": complete,
            "probability_sum": total, "entropy_bits": entropy, "top": top,
            "top_probability": top_probability}


def calibration_block(state, questions, result, model_requested):
    """What a later calibration needs about one call, attached to ask()'s result."""
    result = result if isinstance(result, dict) else {}
    answers = result.get("answers") if isinstance(result.get("answers"), dict) else {}
    questions = questions if isinstance(questions, dict) else {}
    return {
        "schema": "carr.jev-calibration.v1",
        "model_requested": model_requested,
        "model_answered": result.get("model"),
        "model_pinned": model_is_pinned(model_requested),
        "state_sha256": state_sha256(state),
        "questions": {key: answer_distribution(questions.get(key), answers.get(key))
                      for key in sorted(answers)},
    }


def _safe_calibration_block(state, questions, result, model_requested):
    """Recording must never turn a usable judgment into a failed call."""
    try:
        return calibration_block(state, questions, result, model_requested)
    except Exception:
        return None


def _session_id():
    for key in SESSION_ID_ENV_KEYS:
        value = os.environ.get(key)
        if value and value.strip():
            return value.strip()
    return None


def _dispatch_binding(transcript_path=None, session=None):
    """Snapshot the human owner before sending, never when the answer arrives.

    Hook callers can pass their transcript path; shell callers discover the
    exact native session file. An absent, unreadable or ambiguous transcript
    leaves the receipt unbound and therefore unable to pay required actions.
    Native Codex identity takes precedence over inherited Claude environment.
    """
    session = session or _session_id()
    try:
        from lib.jev_required_actions import human_turn_scope
        from lib.transcript_read import load_transcript

        if not session:
            return session, None
        path = transcript_path or os.environ.get("CARR_JEV_TRANSCRIPT_PATH")
        if not path:
            # Session IDs are path components, never glob patterns or paths.
            if not re.fullmatch(r"[A-Za-z0-9_-]+", session):
                return session, None
            if os.environ.get("CODEX_THREAD_ID") == session:
                home = os.environ.get("CODEX_HOME") or os.path.expanduser("~/.codex")
                paths = glob.glob(os.path.join(home, "sessions", "**",
                                               f"rollout-*-{session}.jsonl"), recursive=True)
            else:
                paths = glob.glob(os.path.expanduser(f"~/.claude/projects/*/{session}.jsonl"))
            if len(paths) != 1:
                return session, None
            path = paths[0]
        records = load_transcript(path, hook="jev-dispatch", session=session)
        for record in records:
            if record.get("sessionId") and record["sessionId"] != session:
                return session, None
            if record.get("type") == "session_meta":
                if (record.get("payload") or {}).get("id") != session:
                    return session, None
        return session, human_turn_scope(records, session).identity
    except Exception:
        return session, None


# System-work calls use the Worker's append-only call log. The authenticated
# local-verb transport holds the MCP bearer; this client never reads it.
# Receipts are diagnostic evidence, not per-turn obligations: PR 1407 retired
# prompt-facet enforcement in favor of judgment-boundary checks.
# Runtime consumers keep their pinned direct Jev route. The external Worker
# ingress derives system_work, so it cannot reinterpret a runtime request.
# On Worker failure the direct fallback uses the remaining caller budget and
# records a fixed error category with no server receipt or raw error text.
SERVER_VERB = "ask-jev"
# A signed admission is single-use (the Worker's attempt ledger); this only
# bounds how long an unused one stays valid.
ADMISSION_TTL_SECONDS = 30
# Hooks retain the direct route to stay within their timeout. An explicitly
# requested build advisory can still use the server log; prompt intake defers it.
IN_HOOK_ENV = "CARR_JEV_IN_HOOK"
# The Worker caps its own vendor call at 10s; node's start and the round trip
# get the rest. The whole server attempt never takes more than this share of
# the caller's timeout, so a failed attempt still leaves time to go direct.
SERVER_SHARE_OF_TIMEOUT = 0.7
MIN_DIRECT_SECONDS = 2.0


def _local_verb_script():
    """mcp-server/local-verb.mjs, preferring this checkout's own copy and
    falling back to the canonical checkout's (every worktree shares one
    Worker, so either reaches the same verb)."""
    for root in (REPO, CANONICAL_REPO):
        candidate = os.path.join(root, "mcp-server", "local-verb.mjs")
        if os.path.isfile(candidate):
            return candidate
    return None


def _node_binary():
    for candidate in (shutil.which("node"), "/opt/homebrew/bin/node", "/usr/local/bin/node"):
        if candidate and os.path.isfile(candidate) and os.access(candidate, os.X_OK):
            return candidate
    return None


def _server_error_category(stderr):
    """A fixed category for a failed verb call. Never the raw text: local-verb
    stderr is the Worker's own error JSON, which carries no credential, but a
    category is all a receipt or a gate needs."""
    text = stderr or ""
    for marker, category in (
            ('"jev_cache_miss"', "cache_miss"),
            ('"unknown_tool"', "verb_not_deployed"),
            ('"jev_proxy_unconfigured"', "worker_key_unbound"),
            ('"jev_upstream_failed"', "vendor_failed_at_worker"),
            ('"jev_admission_required"', "admission_refused_at_worker"),
            ("could not reach the deployed Worker", "worker_unreachable"),
            ("no MCP token", "local_token_missing"),
            ("refusing MCP token file", "local_token_insecure")):
        if marker in text:
            return category
    return "server_call_failed"


def _worker_upstream(stderr):
    """(vendor HTTP status, reason) from local-verb's jev_upstream_failed
    refusal, else (None, None). Only an integer status and a short snake_case
    reason are kept; the vendor body the Worker quotes is never read."""
    text = stderr or ""
    start = text.find("TOOL ERROR ")
    if start < 0:
        return None, None
    try:
        payload, _ = json.JSONDecoder().raw_decode(text, start + len("TOOL ERROR "))
    except ValueError:
        return None, None
    if not isinstance(payload, dict) or payload.get("error") != "jev_upstream_failed":
        return None, None
    status, reason = payload.get("status"), payload.get("reason")
    return (status if type(status) is int and 100 <= status <= 599 else None,
            reason if isinstance(reason, str) and re.fullmatch(r"[a-z_]{1,40}", reason) else None)


def _admission_token(secret, idempotency_key, caller, session_id):
    """The existing idempotency key, carrying one shared reservation. The
    Worker verifies it and its attempt ledger consumes it once."""
    payload = base64.urlsafe_b64encode(json.dumps(
        [1, int(time.time()) + ADMISSION_TTL_SECONDS, idempotency_key, caller, session_id],
        ensure_ascii=False, separators=(",", ":")).encode()).decode().rstrip("=")
    signature = hmac.new(secret.encode(), payload.encode(), hashlib.sha256).hexdigest()
    return f"jev1.{payload}.{signature}"


def server_ask(state, questions, *, model, facets, purpose, session_id, timeout,
               transport_mode, runner=None, upstream=None, caller=None, admission=None):
    """Ask the Worker's ask-jev verb. Returns (result, None) on success, where
    result is {"model", "answers", "usage", "server_receipt": {...}}, or
    (None, <category>) on transport failure. A paid call is admitted (or uses
    the caller's `admission`, the (registry, site) pair from _admit_paid_call)
    and reserves its shared slot before transport; admission failures raise.
    A passed `upstream` dict gets the Worker's vendor status and reason, and
    `refusal` when no direct attempt may follow this one."""
    script = _local_verb_script()
    node = _node_binary()
    if runner is None and (script is None or node is None):
        return None, "node_or_local_verb_missing"
    idempotency_key = str(uuid.uuid4())
    probe = None
    if transport_mode != "cache_only":
        caller = caller or _caller_name()
        kind = _question_kind(questions)
        prompt = _prompt_sha256({"state": state, "model": model, "questions": questions})
        registry, site = admission or _admit_paid_call(caller, session_id, questions, facets, kind, prompt)
        try:
            secret = read_admission_secret()
        except TypeSafeError:
            # Before the reservation: a machine without the secret takes the
            # direct route's own reservation instead.
            return None, "admission_secret_missing"
        probe = _reserve_paid_call(questions, facets, caller, kind, prompt, registry, site,
                                   session=session_id)
        idempotency_key = _admission_token(secret, idempotency_key, caller, session_id)
    args = {
        "idempotency_key": idempotency_key,
        "session_id": session_id,
        "purpose": purpose,
        "state": state,
        "questions": questions,
        "facets": sorted({str(f) for f in facets}) if facets else [],
        "model": model,
        "transport_mode": transport_mode,
    }
    try:
        run = runner or subprocess.run
        proc = run([node or "node", script or "local-verb.mjs", SERVER_VERB,
                    json.dumps(args, ensure_ascii=False)],
                   capture_output=True, text=True,
                   timeout=float(timeout),
                   stdin=subprocess.DEVNULL)
    except subprocess.TimeoutExpired:
        return None, "server_timeout"
    except Exception:
        return None, "server_call_failed"
    if proc.returncode != 0:
        category = _server_error_category(proc.stderr)
        refusal = None
        if category == "vendor_failed_at_worker":
            status, reason = _worker_upstream(proc.stderr)
            if upstream is not None:
                upstream["status"], upstream["reason"] = status, reason
            try:
                _after_worker_vendor_failure(status, reason, caller)
            except TypeSafeError as error:
                refusal = error
        elif category == "admission_refused_at_worker":
            # The reservation is spent; a direct retry would pay a second slot.
            refusal = TypeSafeError("Jev unavailable: the Worker refused the admission "
                                    "(jev_admission_required); not retried direct")
        if upstream is not None and refusal is not None:
            upstream["refusal"] = refusal
        return None, category
    try:
        out = json.loads(proc.stdout)
    except ValueError:
        return None, "server_response_unparseable"
    if (not isinstance(out, dict) or out.get("ok") is not True
            or not isinstance(out.get("answers"), dict)
            or not isinstance(out.get("receipt_id"), str)):
        return None, "server_response_malformed"
    result = {
        "model": out.get("model"),
        "answers": out["answers"],
        "usage": out.get("usage") if isinstance(out.get("usage"), dict) else None,
        "cache_hit": out.get("cache_hit") is True,
        "server_receipt": {k: out.get(k) for k in (
            "receipt_id", "recorded_at", "purpose", "session_id",
            "state_sha256", "prompt_sha256")},
    }
    if transport_mode != "cache_only" and not result["cache_hit"] and usable_judgment(result, questions):
        _finish_credit_probe(probe)
    return result, None


# Both transports consume the same maintained request policy. Adapter code
# only maps JSON shapes to each language's object/array predicates.
REQUEST_CONTRACT = json.loads((Path(__file__).resolve().parent.parent /
    "mcp-server/src/jev-request-contract.v1.json").read_text())


def malformed_request(state, questions):
    """A fixed diagnostic with a positional reference; never caller data."""
    if not isinstance(state, (str, dict)):
        return "state_invalid"
    if not isinstance(questions, dict) or not (
            REQUEST_CONTRACT["min_questions"] <= len(questions) <= REQUEST_CONTRACT["max_questions"]):
        return "question_count_invalid"
    for index, (key, question) in enumerate(questions.items()):
        ref = f"question[{index}]"
        if not isinstance(key, str) or not key or not isinstance(question, dict):
            return f"{ref}: question_invalid"
        kind = question.get("type")
        rule = REQUEST_CONTRACT["question_types"].get(kind) if isinstance(kind, str) else None
        if rule is None:
            return f"{ref}: type_invalid"
        instructions = question.get("instructions")
        if not isinstance(instructions, str) or not instructions.strip():
            return f"{ref}: instructions_invalid"
        criteria = question.get("criteria")
        if criteria is None and rule["optional"]:
            continue
        shape = dict if rule["criteria"] == "object" else list
        if not isinstance(criteria, shape) or len(criteria) < rule["min_items"]:
            return f"{ref}: criteria_invalid"
    return None


def _built_question(kind, instructions, criteria):
    question = {"type": kind, "instructions": instructions, "criteria": criteria}
    problem = malformed_request("", {"built": question})
    if problem:
        raise TypeSafeError(problem)
    question["criteria"] = criteria.copy()
    return question


def usable_judgment(result, questions):
    """Require one typed answer per requested question and measured usage."""
    if not isinstance(result, dict) or not isinstance(result.get("model"), str) or not result["model"].strip():
        return False
    usage = result.get("usage")
    if not isinstance(usage, dict) or any(
            type(usage.get(key)) is not int or usage[key] < 0
            for key in ("input_tokens", "output_tokens")):
        return False
    answers = result.get("answers")
    if not isinstance(answers, dict) or set(answers) != set(questions):
        return False
    for key, question in questions.items():
        answer = answers.get(key)
        if not isinstance(question, dict) or not isinstance(answer, dict):
            return False
        kind = question.get("type")
        if answer.get("type") != kind:
            return False
        if kind == "noul":
            value = answer.get("noul")
            if isinstance(value, bool) or not isinstance(value, (int, float)) or not 0 <= value <= 1:
                return False
        elif kind == "choice":
            if answer.get("choice") not in question.get("criteria", {}):
                return False
        elif kind == "score":
            value = answer.get("score")
            levels = question.get("criteria")
            if (isinstance(value, bool) or not isinstance(value, (int, float)) or
                    not isinstance(levels, list) or not 0 <= value <= len(levels) - 1):
                return False
        else:
            return False
        if kind in ("choice", "score"):
            confidence = answer.get("confidence")
            if (isinstance(confidence, bool) or not isinstance(confidence, (int, float)) or
                    not 0 <= confidence <= 1):
                return False
    return True


def _caller_name():
    """The immediate caller of ask(), with no source text or stack arguments."""
    try:
        return os.path.splitext(os.path.basename(sys._getframe(2).f_code.co_filename))[0]
    except (AttributeError, ValueError):
        return "unknown"


def _prompt_sha256(payload):
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"),
                           ensure_ascii=False).encode("utf-8")
    return hashlib.sha256(canonical).hexdigest()


def _question_kind(questions):
    kinds = {q.get("type") for q in questions.values() if isinstance(q, dict)
             and q.get("type") in ("noul", "choice", "score")}
    return ",".join(sorted(kinds)) or "unknown"


# Keep these names aligned with lib/jev_required_actions.py's FACET_NAMES.
# The receipt reader previously inferred them from raw question IDs; infer
# them before logging so no caller-chosen ID text needs to reach the ledger.
RECEIPT_FACETS = (
    "architecture_or_design", "semantic_creation", "diagnosis",
    "verification_selection", "evidence_matching", "next_action_priority",
)


def _receipt_facets(questions, facets):
    names = {str(f) for f in facets} if facets else set()
    for facet in RECEIPT_FACETS:
        pattern = re.compile(r"[_\s-]+".join(map(re.escape, facet.split("_"))), re.I)
        if any(isinstance(qid, str) and pattern.search(qid) for qid in questions):
            names.add(facet)
    return sorted(names)


def _cache_key(endpoint, account, credential, model, caller, question_kind, prompt_sha256):
    # The credential hash scopes unnamed accounts without persisting a secret.
    return _prompt_sha256({
        "endpoint": endpoint, "account": account,
        "credential_sha256": hashlib.sha256(credential.encode("utf-8")).hexdigest(),
        "model": model, "caller": caller, "question_kind": question_kind,
        "prompt_sha256": prompt_sha256,
    })


def _cache_connection(path):
    os.makedirs(os.path.dirname(path), mode=0o700, exist_ok=True)
    db = sqlite3.connect(path, timeout=2)
    os.chmod(path, 0o600)
    db.execute("CREATE TABLE IF NOT EXISTS results_v2 ("
               "cache_key TEXT PRIMARY KEY, expires_at REAL NOT NULL, "
               "result_json TEXT NOT NULL)")
    return db


def _cached_result(path, cache_key):
    try:
        db = _cache_connection(path)
        try:
            row = db.execute(
                "SELECT result_json, expires_at FROM results_v2 WHERE cache_key=?",
                (cache_key,)).fetchone()
        finally:
            db.close()
        if row and row[1] > time.time():
            answer = json.loads(row[0])
            # A cache hit made no vendor call. Do not repeat the old billable usage.
            return {**answer, "usage": None, "cache_hit": True}
    except (OSError, sqlite3.Error, ValueError, TypeError):
        pass
    return None


def _store_cached_result(path, cache_key, result, ttl):
    try:
        db = _cache_connection(path)
        try:
            db.execute("DELETE FROM results_v2 WHERE expires_at <= ?", (time.time(),))
            db.execute("INSERT OR REPLACE INTO results_v2 VALUES (?,?,?)",
                       (cache_key, time.time() + ttl,
                        json.dumps(result, separators=(",", ":"))))
            db.commit()
        finally:
            db.close()
    except (OSError, sqlite3.Error, TypeError, ValueError):
        pass  # Cache failure changes neither the answer nor a real call receipt.


def _append_call_receipt(questions, facets, result, log_path, *, caller=None,
                         question_kind=None, prompt_sha256=None, ok=True,
                         cache_hit=False, error=None, calibration=None,
                         server_error=None, session=None, dispatch_binding=None):
    """Best-effort, APPEND-ONLY JSONL row, never storing the request or the
    answers, never able to turn a successful ask() into a failure. See
    JEV_CALLS_LOG above.

    The row carries the response's "model" and "usage" token counts. It
    deliberately carries NO response id (round 3, 2026-09-24): the vendor
    returns none (null in all 63 real receipts at the time of writing) and
    exposes no usage/audit endpoint, so an id field could never be reconciled
    and was a claim this file cannot back. Forgery of a row is DETECTED, not
    prevented (decision d47931da): hooks/bash-write-gate.py warns on any
    shell write naming this file, and hooks/completion-evidence-gate.py
    records a detection event for any turn whose tool calls name it — this
    function, reached only through ask(), is the one legitimate writer.

    `calibration` is ask()'s calibration_block(). The row keeps only its
    numbers — entropy and completeness per question, aligned with
    question_ids_sha256 — plus the state digest and the requested model. The
    distributions themselves name options, which are caller text, so they stay
    in the returned result and in jev_judge.record()'s log, never here.
    """
    try:
        answered = result if isinstance(result, dict) else {}
        usage = answered.get("usage") if isinstance(answered.get("usage"), dict) else None
        usable = (answered.get("usable") is True if "usable" in answered
                  else bool(ok and usage))
        row = {
            "ts": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "session": dispatch_binding[0] if dispatch_binding else session or _session_id(),
            "human_turn_id": dispatch_binding[1] if dispatch_binding else None,
            "job": _job_label(),
            "question_ids_sha256": [hashlib.sha256(qid.encode("utf-8")).hexdigest()
                                    for qid in sorted(questions)],
            "caller": caller,
            "question_kind": question_kind,
            "prompt_sha256": prompt_sha256,
            "facets": _receipt_facets(questions, facets),
            "model": answered.get("model"),
            "usage": usage,
            "input_tokens": usage.get("input_tokens") if usage else None,
            "output_tokens": usage.get("output_tokens") if usage else None,
            "http_status": answered.get("http_status"),
            # None when no answer came back to validate (a refusal, a 402, a
            # network fault): those are transport failures, not bad answers.
            "schema_valid": (answered["schema_valid"] if type(answered.get("schema_valid")) is bool
                             else None),
            "upstream_reason": answered.get("upstream_reason"),
            "usable": usable,
            "ok": bool(ok and usable and not cache_hit),
            "cache_hit": cache_hit,
            "server_receipt_id": ((answered.get("server_receipt") or {}).get("receipt_id")
                                  if not cache_hit else None),
            "server_error": server_error,
        }
        per_question = (calibration or {}).get("questions") if isinstance(calibration, dict) else None
        ordered = [per_question.get(qid) or {} for qid in sorted(questions)] if isinstance(per_question, dict) else None
        row.update({
            "state_sha256": calibration.get("state_sha256") if per_question is not None else None,
            "model_requested": calibration.get("model_requested") if per_question is not None else None,
            "model_pinned": calibration.get("model_pinned") if per_question is not None else None,
            "entropy_bits": [q.get("entropy_bits") for q in ordered] if ordered is not None else None,
            "distribution_complete": ([q.get("distribution_complete") is True for q in ordered]
                                      if ordered is not None else None),
        })
        if error:
            row["error"] = error
        os.makedirs(os.path.dirname(log_path), exist_ok=True)
        with open(log_path, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(row) + "\n")
    except Exception:
        pass


def _logged_attempt_rows(log_path, day):
    """Seed a new UTC day from pre-cap receipts, excluding free cache hits.

    Count attempts conservatively: failures may still have consumed credits.
    A missing log means no prior calls; unreadable evidence makes Jev unavailable.
    """
    try:
        with open(log_path, encoding="utf-8") as fh:
            for line in fh:
                try:
                    row = json.loads(line)
                except ValueError:
                    raise TypeSafeError("Jev unavailable: daily cap accounting has malformed seed evidence") from None
                if not isinstance(row, dict) or not isinstance(row.get("ts"), str):
                    raise TypeSafeError("Jev unavailable: daily cap accounting has invalid seed evidence")
                try:
                    stamp = datetime.fromisoformat(row["ts"].replace("Z", "+00:00"))
                except ValueError:
                    raise TypeSafeError("Jev unavailable: daily cap accounting has invalid seed timestamp") from None
                if stamp.tzinfo is None or ("cache_hit" in row and type(row["cache_hit"]) is not bool):
                    raise TypeSafeError("Jev unavailable: daily cap accounting has invalid seed fields")
                if (stamp.astimezone(timezone.utc).strftime("%Y-%m-%d") == day
                        and not row.get("cache_hit")
                        and row.get("error") not in REFUSAL_CODES):
                    yield row
    except FileNotFoundError:
        pass
    except UnicodeError:
        raise TypeSafeError("Jev unavailable: daily cap accounting has invalid seed encoding") from None


def _logged_attempts(log_path, day):
    return sum(1 for _ in _logged_attempt_rows(log_path, day))


def _daily_cap_limit():
    cap = JEV_COST_CONFIG.get("daily_paid_call_cap")
    if type(cap) is not int or cap < 0:
        raise TypeSafeError("Jev unavailable: invalid daily paid call cap")
    return cap


def _claim_spend_alerts(db, log_path, day, previous, allowed, cap, caller):
    """Queue alarms and attribution in the cap transaction; alarm errors fail open.

    Seed once from existing receipts, including upgrades of an AP counter. A
    reservation with no receipt is retained as unknown, never guessed away.
    Tie-break names lexically so attribution is repeatable across workers.
    """
    try:
        db.execute("SAVEPOINT spend_alarm")
    except Exception:
        return []
    try:
        db.execute("CREATE TABLE IF NOT EXISTS daily_cap_attribution "
                   "(day TEXT PRIMARY KEY)")
        db.execute("CREATE TABLE IF NOT EXISTS daily_cap_counts "
                   "(day TEXT, dimension TEXT, name TEXT, count INTEGER NOT NULL, "
                   "PRIMARY KEY(day,dimension,name))")
        db.execute("CREATE TABLE IF NOT EXISTS daily_cap_delivery "
                   "(day TEXT, threshold INTEGER, alert_json TEXT NOT NULL, "
                   "state TEXT NOT NULL, attempts INTEGER NOT NULL, lease_until REAL NOT NULL, "
                   "error TEXT, PRIMARY KEY(day,threshold))")
        db.execute("CREATE TABLE IF NOT EXISTS daily_cap_mail_delivery "
                   "(day TEXT, threshold INTEGER, state TEXT NOT NULL, error TEXT, "
                   "PRIMARY KEY(day,threshold))")
        row = db.execute("SELECT day FROM daily_cap_attribution WHERE day=?", (day,)).fetchone()
        if row is None:
            receipts = list(_logged_attempt_rows(log_path, day))
            # Rebuild attribution once for existing counters as well. The
            # old alarm mask held no delivery evidence and is no longer read.
            db.execute("DELETE FROM daily_cap_counts WHERE day=?", (day,))
            for dimension, field in (("caller", "caller"), ("session", "session")):
                counts = Counter(str(r.get(field) or "unknown") for r in receipts)
                counts["unknown"] += max(0, previous - len(receipts))
                db.executemany("INSERT INTO daily_cap_counts VALUES (?,?,?,?)",
                               [(day, dimension, name, count) for name, count in counts.items() if count])
            db.execute("DELETE FROM daily_cap_attribution WHERE day < ?", (day,))
            db.execute("DELETE FROM daily_cap_counts WHERE day < ?", (day,))
            db.execute("DELETE FROM daily_cap_delivery WHERE day < ?", (day,))
            # Keep mail tombstones: a caller paused before its cap transaction
            # can resume with an old UTC day and recreate that day's alarm.
            # Deleting its claim would permit a second external send.
            db.execute("INSERT INTO daily_cap_attribution VALUES (?)", (day,))
        if allowed:
            for dimension, name in (("caller", caller), ("session", _session_id())):
                db.execute("INSERT INTO daily_cap_counts VALUES (?,?,?,1) "
                           "ON CONFLICT(day,dimension,name) DO UPDATE SET count=count+1",
                           (day, dimension, str(name or "unknown")))
        used = previous + int(allowed)
        for threshold in (50, 80, 100):
            if used * 100 < cap * threshold:
                continue
            top = {}
            for dimension in ("caller", "session"):
                leader = db.execute("SELECT name,count FROM daily_cap_counts "
                                    "WHERE day=? AND dimension=? ORDER BY count DESC,name LIMIT 1",
                                    (day, dimension)).fetchone()
                top[dimension] = list(leader or ("unknown", 0))
            message = (f"Jev daily cap {threshold}% · {used}/{cap} paid calls on {day} UTC. "
                       f"Top caller: {top['caller'][0]} ({top['caller'][1]}); "
                       f"top session: {top['session'][0]} ({top['session'][1]}). "
                       "Jev goes unavailable at the cap; resets at 00:00 UTC.")
            alert = {"day": day, "threshold": threshold, "calls": used, "cap": cap,
                     "top_caller": top["caller"], "top_session": top["session"], "message": message}
            # A threshold is queued once; only successful OS submission
            # changes its delivery row to acknowledged.
            db.execute("INSERT OR IGNORE INTO daily_cap_delivery VALUES (?,?,?,'pending',0,0,NULL)",
                       (day, threshold, json.dumps(alert)))

        alerts = [json.loads(r[0]) for r in db.execute(
            "SELECT alert_json FROM daily_cap_delivery WHERE day=? AND state != 'delivered' "
            "AND attempts < 3 AND lease_until <= ? ORDER BY threshold", (day, time.time()))]
        db.execute("RELEASE spend_alarm")
        return alerts
    except Exception:
        try:
            db.execute("ROLLBACK TO spend_alarm")
            db.execute("RELEASE spend_alarm")
        except sqlite3.Error:
            pass  # The mandatory counter commit still checks DB integrity.
        return []


class AlarmSinkError(RuntimeError):
    """A named alarm-delivery failure. str() is a fixed category, never raw output."""


# bin/gmail-handover.py's own credential file. Read for existence only: the
# alarm worker never opens it, so no credential crosses into this process.
GMAIL_ENV_PATH = os.path.expanduser("~/.config/carr/gmail.env")
# DRY-RUN SEAM. CARR_JEV_ALERT_SINK=dry-run:<path> appends each alarm, tagged
# with the sink it would have used, to <path> and sends nothing. Tests use it;
# so can an operator rehearsing the alarm.
ALERT_SINK_ENV = "CARR_JEV_ALERT_SINK"


def _dry_run_sink(alert, sink):
    target = os.environ.get(ALERT_SINK_ENV, "")
    if not target.startswith("dry-run:"):
        return False
    with open(target[len("dry-run:"):], "a", encoding="utf-8") as fh:
        fh.write(json.dumps({"sink": sink, "threshold": alert.get("threshold"),
                             "message": alert.get("message")}) + "\n")
    return True


def _emit_spend_alert(alert):
    """Reuse cutover-watch/version-sentinel's local macOS notification path.

    Runs in the detached delivery process, with a bounded wait.
    No prompt, answer or credential crosses into the notification.
    """
    if _dry_run_sink(alert, "notification"):
        return
    message = alert["message"].replace("\\", "\\\\").replace('"', '\\"')
    message = message.replace("\n", " ").replace("\r", " ")
    subprocess.run(["/usr/bin/osascript", "-e",
                    f'display notification "{message}" with title "Jev spend alarm"'],
                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                   timeout=5, check=True)


def _email_spend_alert(alert):
    """Use the existing self-mail command; it owns recipient and credentials.

    2026-10-04: every threshold mail failed as a bare CalledProcessError because
    this machine has no ~/.config/carr/gmail.env, so the command exited with
    "no credential" into a discarded stderr. The precondition is now checked
    first and every failure carries a fixed category the health line can name.
    """
    if _dry_run_sink(alert, "mail"):
        return
    if not (os.environ.get("CARR_GMAIL_USER") and os.environ.get("CARR_GMAIL_APP_PASSWORD")) \
            and not os.path.isfile(GMAIL_ENV_PATH):
        raise AlarmSinkError("mail_unconfigured")
    try:
        subprocess.run([sys.executable, os.path.join(REPO, "bin", "gmail-handover.py"),
                        "--to", "joe", "--subject", "Jev spend alarm",
                        "--body", alert["message"]],
                       stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                       stderr=subprocess.PIPE, text=True, timeout=35, check=True)
    except subprocess.CalledProcessError as exc:
        text = exc.stderr or ""
        for marker, category in (("no credential", "mail_unconfigured"),
                                 ("missing CARR_GMAIL_USER", "mail_unconfigured"),
                                 ("login refused", "mail_auth_refused"),
                                 ("REFUSED", "mail_recipient_refused")):
            if marker in text:
                raise AlarmSinkError(category) from None
        raise AlarmSinkError(f"mail_exit_{exc.returncode}") from None
    except subprocess.TimeoutExpired:
        raise AlarmSinkError("mail_timeout") from None


def _deliver_pending_spend_alerts(path, day):
    """One bounded delivery pass, in a detached process, with durable leases.

    A later reservation (even one refused at the cap) recovers failed/stale
    leases, up to three attempts per threshold. OS submission is acknowledged
    only after the sink returns successfully. Death after submission but before
    acknowledgement can cause a repeat; exactly-once OS delivery is unavailable.
    Mail is attempted once per threshold/day, claimed before any external send.
    An ambiguous timeout or process death cannot safely be retried by SMTP.
    """
    for threshold in (50, 80, 100):
        with closing(sqlite3.connect(path, timeout=1)) as db, db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT alert_json,attempts FROM daily_cap_delivery "
                             "WHERE day=? AND threshold=? AND state != 'delivered' "
                             "AND attempts < 3 AND lease_until <= ?",
                             (day, threshold, time.time())).fetchone()
            if row is None:
                continue
            attempt = row[1] + 1
            db.execute("UPDATE daily_cap_delivery SET state='sending',attempts=?,lease_until=? "
                       "WHERE day=? AND threshold=?", (attempt, time.time() + 30, day, threshold))
            mail_claimed = db.execute(
                "INSERT OR IGNORE INTO daily_cap_mail_delivery VALUES (?,?,'attempted',NULL)",
                (day, threshold)).rowcount == 1
        error = None
        try:
            _emit_spend_alert(json.loads(row[0]))
        except Exception as exc:
            error = type(exc).__name__
        with closing(sqlite3.connect(path, timeout=1)) as db, db:
            db.execute("UPDATE daily_cap_delivery SET state=?,lease_until=0,error=? "
                       "WHERE day=? AND threshold=? AND attempts=? AND state='sending'",
                       ("failed" if error else "delivered", error, day, threshold, attempt))
        # Use the same worker, even when the local notification failed. Its
        # retries never resend mail; the durable claim precedes both sinks.
        if mail_claimed:
            mail_error = None
            try:
                _email_spend_alert(json.loads(row[0]))
            except AlarmSinkError as exc:
                mail_error = str(exc)
            except Exception as exc:
                mail_error = type(exc).__name__
            with closing(sqlite3.connect(path, timeout=1)) as db, db:
                db.execute("UPDATE daily_cap_mail_delivery SET state=?,error=? "
                           "WHERE day=? AND threshold=?",
                           ("failed" if mail_error else "sent", mail_error, day, threshold))


def _launch_spend_alert_worker(path, day):
    # This runs fixed repository code, with accounting identifiers only. All
    # descriptors are detached so neither interpreter exit nor captured hook
    # output waits for notification delivery. It performs no model work.
    code = ("import importlib.util,sys; "
            "s=importlib.util.spec_from_file_location('jev_alert_client',sys.argv[1]); "
            "m=importlib.util.module_from_spec(s); s.loader.exec_module(m); "
            "m._deliver_pending_spend_alerts(sys.argv[2],sys.argv[3])")
    subprocess.Popen([sys.executable, "-c", code, os.path.abspath(__file__), path, day],
                     stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                     stderr=subprocess.DEVNULL, start_new_session=True, close_fds=True)


def _dispatch_spend_alerts(alerts):
    if not alerts:
        return
    path = os.fspath(JEV_DAILY_CAP_LOG) + ".daily-cap.sqlite3"
    day = alerts[0]["day"]
    try:
        _launch_spend_alert_worker(path, day)
    except Exception as exc:
        # Retain failed-launch evidence and capacity; the next reservation
        # retries the pending warning, with the same three-attempt ceiling.
        try:
            with closing(sqlite3.connect(path, timeout=0.1)) as db, db:
                db.execute("UPDATE daily_cap_delivery SET state='failed',attempts=attempts+1,error=? "
                           "WHERE day=? AND state IN ('pending','failed') AND attempts < 3",
                           (type(exc).__name__, day))
        except (OSError, sqlite3.Error):
            pass  # Durable pending rows remain visible to the read-only health path.


PAID_CAP_ACTION = ("on breach: notify Joe at 50%/80%/100% via macOS notification and "
                   "email to his own CARR address (one mail attempt per threshold/day); "
                   "cap refuses further paid calls · owner orchestrator · remediation "
                   "reduce top caller/session demand; pending/failed macOS alarms recover on next "
                   "reservation, at most three attempts (stale lease after 30s); inspect "
                   "notification sink if exhausted; inspect self-mail sink and confirm inbox "
                   "on mail failure or unconfirmed submission; errors=mail_unconfigured means "
                   "Joe creates ~/.config/carr/gmail.env on this machine (setup in "
                   "bin/gmail-handover.py) "
                   "· verify next UTC day below 50% "
                   "· auto-clear at UTC rollover")


def paid_cap_health(*, now=None):
    """Read the same reservation counter as ask(), without writes or credentials."""
    day = (now or datetime.now(timezone.utc)).astimezone(timezone.utc).strftime("%Y-%m-%d")
    try:
        cap = _daily_cap_limit()
        deliveries = []
        mail_deliveries = []
        path = Path(os.fspath(JEV_DAILY_CAP_LOG) + ".daily-cap.sqlite3")
        if path.exists():
            db = sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True, timeout=1)
            try:
                row = db.execute("SELECT attempts FROM daily_cap WHERE day=?", (day,)).fetchone()
                deliveries = db.execute("SELECT state,attempts,lease_until,error FROM daily_cap_delivery "
                                        "WHERE day=?", (day,)).fetchall()
                if db.execute("SELECT 1 FROM sqlite_master WHERE name='daily_cap_mail_delivery'").fetchone():
                    mail_deliveries = db.execute("SELECT state,error FROM daily_cap_mail_delivery WHERE day=?",
                                                 (day,)).fetchall()
            finally:
                db.close()
            used = row[0] if row else _logged_attempts(JEV_DAILY_CAP_LOG, day)
        else:
            used = _logged_attempts(JEV_DAILY_CAP_LOG, day)
        status = "HIT" if used >= cap else "WARN" if used * 100 >= cap * 50 else "OK"
        failed = sum(state == "failed" or (state == "sending" and lease <= time.time())
                     for state, attempts, lease, error in deliveries)
        pending = sum(state in ("pending", "sending") for state, _, _, _ in deliveries)
        delivered = sum(state == "delivered" for state, _, _, _ in deliveries)
        mail_failed = sum(state == "failed" for state, _ in mail_deliveries)
        mail_unconfirmed = sum(state == "attempted" for state, _ in mail_deliveries)
        mail_sent = sum(state == "sent" for state, _ in mail_deliveries)
        if status == "OK" and (failed or pending or mail_failed or mail_unconfirmed):
            status = "WARN"
        errors = sorted({error for _, _, _, error in deliveries if error})
        errors = sorted(set(errors) | {error for _, error in mail_deliveries if error})
        return (f"{status} jev paid cap — {used}/{cap} paid calls · UTC {day} · "
                f"alarms pending={pending} failed={failed} delivered={delivered} "
                f"mail_failed={mail_failed} mail_sent={mail_sent} mail_unconfirmed={mail_unconfirmed} "
                f"errors={','.join(errors) or 'none'} · {PAID_CAP_ACTION}")
    except (OSError, sqlite3.Error, TypeSafeError) as exc:
        return f"UNKNOWN jev paid cap — {type(exc).__name__} · {PAID_CAP_ACTION}"


def _cap_db_path():
    return os.fspath(JEV_DAILY_CAP_LOG) + ".daily-cap.sqlite3"


def _windows(now):
    """(day, hour key, next hour ISO, next UTC midnight ISO) for one instant."""
    now = now.astimezone(timezone.utc)
    hour = now.replace(minute=0, second=0, microsecond=0)
    midnight = hour.replace(hour=0)
    fmt = "%Y-%m-%dT%H:%M:%SZ"
    return (now.strftime("%Y-%m-%d"), hour.strftime("%Y-%m-%dT%H"),
            (hour + timedelta(hours=1)).strftime(fmt), (midnight + timedelta(days=1)).strftime(fmt))


_BUDGET_TABLES = (
    "CREATE TABLE IF NOT EXISTS site_usage (day TEXT, hour TEXT, site TEXT, "
    "count INTEGER NOT NULL, PRIMARY KEY(day,hour,site))",
    "CREATE TABLE IF NOT EXISTS budget_seed (day TEXT PRIMARY KEY)",
    # One refusal row per (code, site, session, window) reaches the call log;
    # the rest are counted here, so a refused burner cannot flood the log.
    "CREATE TABLE IF NOT EXISTS refusal_log (window TEXT PRIMARY KEY, count INTEGER NOT NULL)",
    # Active budget pauses, read by hooks to say so once instead of per call.
    "CREATE TABLE IF NOT EXISTS budget_pause (scope TEXT, site TEXT, resets_at TEXT, "
    "PRIMARY KEY(scope,site,resets_at))",
    "CREATE TABLE IF NOT EXISTS pause_notice (session TEXT, scope TEXT, resets_at TEXT, "
    "PRIMARY KEY(session,scope,resets_at))",
)


def _budget_tables(db):
    for statement in _BUDGET_TABLES:
        db.execute(statement)


UNATTRIBUTED_SITE = "__unattributed__"


def _seed_site_usage(db, log_path, day, hour, registry):
    """Upgrade/rebuild once under the reservation lock, retaining paid evidence.

    Receipts use the existing validated seed parser. Existing reservations may
    exceed receipts (a process can die before appending one), so neither the
    daily counter nor a site bucket is reduced. Unattributed usage consumes
    every site's allowance; an unknown time consumes the current hour too.
    """
    if db.execute("SELECT 1 FROM budget_seed WHERE day=?", (day,)).fetchone():
        return
    buckets = {}
    for receipt in _logged_attempt_rows(log_path, day):
        stamp = datetime.fromisoformat(receipt["ts"].replace("Z", "+00:00"))
        name = receipt.get("caller")
        entry = call_site(name, registry) if registry else None
        identity = entry["caller"] if entry else UNATTRIBUTED_SITE
        key = (_windows(stamp)[1], identity)
        buckets[key] = buckets.get(key, 0) + 1
    # Normalize concrete wildcard buckets before comparing them with receipts.
    for name, bucket_hour, count in db.execute(
            "SELECT site,hour,count FROM site_usage WHERE day=?", (day,)).fetchall():
        entry = call_site(name, registry) if registry else None
        identity = entry["caller"] if entry else UNATTRIBUTED_SITE
        if identity != name:
            db.execute("DELETE FROM site_usage WHERE day=? AND hour=? AND site=?", (day, bucket_hour, name))
            db.execute("INSERT INTO site_usage VALUES (?,?,?,?) ON CONFLICT(day,hour,site) "
                       "DO UPDATE SET count=count+excluded.count", (day, bucket_hour, identity, count))
    for (bucket_hour, identity), count in buckets.items():
        db.execute("INSERT INTO site_usage VALUES (?,?,?,?) ON CONFLICT(day,hour,site) "
                   "DO UPDATE SET count=MAX(count,excluded.count)", (day, bucket_hour, identity, count))
    recorded = db.execute("SELECT COALESCE(SUM(count),0) FROM site_usage WHERE day=?", (day,)).fetchone()[0]
    row = db.execute("SELECT attempts FROM daily_cap WHERE day=?", (day,)).fetchone()
    total = max(row[0] if row else 0, recorded)
    if total > recorded:
        db.execute("INSERT INTO site_usage VALUES (?,?,?,?) ON CONFLICT(day,hour,site) "
                   "DO UPDATE SET count=count+excluded.count", (day, hour, UNATTRIBUTED_SITE, total - recorded))
    db.execute("INSERT INTO daily_cap VALUES (?,?,0) ON CONFLICT(day) "
               "DO UPDATE SET attempts=MAX(attempts,excluded.attempts)", (day, total))
    db.execute("INSERT INTO budget_seed VALUES (?)", (day,))


def _record_refusal(questions, facets, caller, question_kind, prompt_sha256, code, session, now=None):
    """Log a refusal once per code/site/session/hour; count every one."""
    _, hour, _, _ = _windows(now or datetime.now(timezone.utc))
    window = json.dumps([code, caller, session or "", hour])
    first = True
    try:
        with closing(sqlite3.connect(_cap_db_path(), timeout=1.0)) as db, db:
            _budget_tables(db)
            first = db.execute("INSERT OR IGNORE INTO refusal_log VALUES (?,1)", (window,)).rowcount == 1
            if not first:
                db.execute("UPDATE refusal_log SET count=count+1 WHERE window=?", (window,))
    except (OSError, sqlite3.Error):
        pass  # A refusal still refuses when its bookkeeping cannot be written.
    if first:
        _append_call_receipt(questions, facets, None, JEV_DAILY_CAP_LOG, caller=caller,
                             question_kind=question_kind, prompt_sha256=prompt_sha256,
                             ok=False, error=code, session=session)


# WORKER BREAKER. On 2026-10-04 the Worker's own vendor call failed
# (vendor_failed_at_worker) on every attempt, and each call then reserved a
# SECOND slot for the direct fallback: 1,185 of that day's 2,775 counted
# attempts were doomed Worker tries. One failure opens the breaker for this
# long; while open, a cache miss goes straight to the direct route.
WORKER_BREAKER_SECONDS = 15 * 60

# CREDIT HOLD. 2026-09-28..10-03: TypeSafe answered HTTP 402 (no API credit)
# to 16,927 calls across two windows of 22h and 18h, because nothing stopped
# the next call after the first refusal. Every one was logged as an unusable
# answer and reserved a daily-cap slot. One 402 now holds every paid route
# for this long; cache hits are still served, and when it lapses the next
# call is the probe that finds out whether credit is back.
CREDIT_HOLD_SECONDS = 5 * 60
CREDIT_PROBE_SECONDS = 60
_CREDIT_STATE_UNSAFE: set[str] = set()


def _hold_open(table):
    """Advisory Worker breaker read; credit admission uses its transaction."""
    try:
        with closing(sqlite3.connect(_cap_db_path(), timeout=1.0)) as db:
            db.execute(f"CREATE TABLE IF NOT EXISTS {table} (id INTEGER PRIMARY KEY, until REAL NOT NULL)")
            row = db.execute(f"SELECT until FROM {table} WHERE id=1").fetchone()
    except (OSError, sqlite3.Error):
        if table == "credit_hold":
            raise TypeSafeError("Jev unavailable: vendor_credit_state_untrusted") from None
        return False
    return bool(row) and row[0] > time.time()


def _credit_tables(db):
    db.execute("CREATE TABLE IF NOT EXISTS credit_hold (id INTEGER PRIMARY KEY, until REAL NOT NULL)")
    db.execute("CREATE TABLE IF NOT EXISTS credit_probe "
               "(id INTEGER PRIMARY KEY, token TEXT NOT NULL, until REAL NOT NULL)")


def _credit_marker():
    return Path(_cap_db_path() + ".credit-unsafe")


def _open_hold(table, seconds):
    credit = table == "credit_hold"
    path = _cap_db_path()
    if credit:
        # Persist a latch before SQLite. A failed write survives process exit;
        # restored storage repairs the latch into a hold, never paid traffic.
        _CREDIT_STATE_UNSAFE.add(path)
    try:
        if credit:
            marker = _credit_marker()
            marker.parent.mkdir(parents=True, exist_ok=True)
            marker.touch(mode=0o600)
        with closing(sqlite3.connect(path, timeout=1.0)) as db, db:
            if credit:
                _credit_tables(db)
                db.execute("BEGIN IMMEDIATE")
                db.execute("DELETE FROM credit_probe WHERE id=1")
            else:
                db.execute(f"CREATE TABLE IF NOT EXISTS {table} (id INTEGER PRIMARY KEY, until REAL NOT NULL)")
            db.execute(f"INSERT OR REPLACE INTO {table} VALUES (1, ?)", (time.time() + seconds,))
        if credit:
            _credit_marker().unlink(missing_ok=True)
            _CREDIT_STATE_UNSAFE.discard(path)
    except (OSError, sqlite3.Error):
        if credit:
            raise TypeSafeError("Jev unavailable: vendor_credit_state_untrusted; paid calls held") from None
        # The Worker breaker is advisory; credit protection is mandatory.


def _finish_credit_probe(token):
    """Only a verified successful lease owner can clear the recovered hold."""
    if token is None:
        return
    try:
        with closing(sqlite3.connect(_cap_db_path(), timeout=1.0)) as db, db:
            db.execute("BEGIN IMMEDIATE")
            if db.execute("DELETE FROM credit_probe WHERE id=1 AND token=?", (token,)).rowcount:
                db.execute("DELETE FROM credit_hold WHERE id=1")
    except (OSError, sqlite3.Error):
        pass  # Retain the bounded lease; an unverified clear never enables traffic.


REJECTED_REQUEST_STATUSES = (400, 413, 422)


def _after_worker_vendor_failure(status, reason, caller):
    """Decide the direct retry from the vendor status the Worker saw.

    1,405 vendor_failed_at_worker rows (2026-10-03..04) were each followed by
    a second paid direct attempt, whatever the Worker had seen. Now:
      402               -> hold for credit, refuse; the direct key is the same account
      400/413/422       -> refuse; the request itself was rejected and would be again
      timeout, or a 2xx
      with a bad body   -> refuse vendor_spend_unknown; that attempt may be billed
      429/5xx/network/
      unknown           -> trip the breaker and allow the one direct retry
    """
    if status == 402:
        _open_hold("credit_hold", CREDIT_HOLD_SECONDS)
        raise TypeSafeError("TypeSafe returned HTTP 402 at the Worker: no API credit; "
                            "paid calls are held") from None
    if status in REJECTED_REQUEST_STATUSES:
        raise TypeSafeError(f"TypeSafe returned HTTP {status} at the Worker: the request was "
                            "rejected and is not resent") from None
    _open_hold("worker_breaker", WORKER_BREAKER_SECONDS)
    if reason == "timeout" or (status is not None and 200 <= status < 300):
        raise JevCallRefused("Jev unavailable: vendor_spend_unknown (the Worker's vendor attempt "
                             f"may have been billed: {reason or status}); not retried direct",
                             code="vendor_spend_unknown", site=caller)


def _site_enabled(entry):
    return not (_unattended() and entry["unattended"] == "off")


def call_site_enabled(caller):
    """Whether this site's registry policy permits dispatch in this environment.

    Admission still owns attribution and accounting. Advisory dispatchers use
    this projection to avoid running checks that the same policy will refuse.
    """
    if not _unattended():
        return True
    try:
        entry = call_site(caller, load_call_sites())
        return entry is not None and _site_enabled(entry)
    except TypeSafeError:
        return False


def _admit_paid_call(caller, session, questions, facets, question_kind, prompt_sha256):
    """The registry gate, before any transport: the site entry, or JevCallRefused.

    Ordered questions, each a deterministic predicate:
      1. is this a fixture or CI run?             -> refuse, unlogged (never real traffic)
      2. is the registry readable and valid?      -> else refuse everything
      3. is `caller` a registered site?           -> else unregistered_caller
      4. does the call carry the site's attribution (a session, or for
         session_or_job sites a session or a scheduled job's label)?
                                                  -> else unattributed_call
      5. is this an unattended worker, and does the site stay off there?
                                                  -> unattended_worker_off
    """
    if _fixture_offline():
        raise JevCallRefused("Jev unavailable: fixture or CI run (CARR_JEV_OFFLINE/CARR_HOOK_FIXTURE)",
                             code="fixture_offline", site=caller)
    code = None
    entry = None
    try:
        registry = load_call_sites()
    except TypeSafeError:
        code = "call_site_registry_invalid"
    else:
        entry = call_site(caller, registry)
        if entry is None:
            code = "unregistered_caller"
        elif not session and not (entry["attribution"] == "session_or_job" and _job_label()):
            code = "unattributed_call"
        elif not _site_enabled(entry):
            code = "unattended_worker_off"
    if code:
        _record_refusal(questions, facets, caller, question_kind, prompt_sha256, code, session)
        raise JevCallRefused(f"Jev unavailable: {code} ({caller})", code=code, site=caller)
    return registry, entry


_reservation_receipt: ContextVar[dict | None] = ContextVar("jev_reservation_receipt", default=None)


@contextmanager
def capture_paid_reservations(*, caller, session_id, run_id):
    """Observe committed canonical admissions for this execution context.

    This receipt does not admit, refund or reconstruct spending. Each UTC day
    count comes only from the existing reservation transaction after commit,
    including attempts whose transport later fails. Context-local observation
    excludes concurrent callers, sessions and runs, without a second ledger.
    """
    receipt = {"caller": caller, "session_id": session_id, "run_id": run_id, "utc_days": {}}
    token = _reservation_receipt.set(receipt)
    try:
        yield receipt
    finally:
        _reservation_receipt.reset(token)


def _reserve_paid_call(questions, facets, caller, question_kind, prompt_sha256,
                       registry=None, site=None, *, session=None):
    """Atomically reserve one transport attempt across processes and worktrees.

    The small counter lives beside the canonical call log, not inside a session.
    Reservations are never refunded: uncertain delivery can have been billable.
    Cache hits reach neither this function nor the transport. Storage failure
    uses the same TypeSafeError outage contract, so hooks retain their fallback.

    Four budgets, checked in one transaction in this order: the global daily
    cap, the global hourly cap, the site's daily budget, the site's hourly
    budget. 2026-10-04's 300-475 calls an hour overnight ran under an "hourly
    cap" that #1502's own review had removed again; this one is the counter.
    """
    log_path = JEV_DAILY_CAP_LOG
    cap = _daily_cap_limit()
    now = datetime.now(timezone.utc)
    day, hour, next_hour, next_day = _windows(now)
    notice = False
    alerts = []
    refused = None
    probe_token = None
    repair_credit = False
    site_id = site["caller"] if site is not None else caller
    try:
        os.makedirs(os.path.dirname(os.path.abspath(log_path)), exist_ok=True)
        db = sqlite3.connect(_cap_db_path(), timeout=1.0)
        try:
            db.execute("CREATE TABLE IF NOT EXISTS daily_cap "
                       "(day TEXT PRIMARY KEY, attempts INTEGER NOT NULL, notified INTEGER NOT NULL)")
            _budget_tables(db)
            db.execute("BEGIN IMMEDIATE")
            _credit_tables(db)
            instant = time.time()
            repair_credit = _credit_marker().exists() or _cap_db_path() in _CREDIT_STATE_UNSAFE
            if repair_credit:
                db.execute("INSERT OR REPLACE INTO credit_hold VALUES (1,?)",
                           (instant + CREDIT_HOLD_SECONDS,))
                db.execute("DELETE FROM credit_probe WHERE id=1")
            hold = db.execute("SELECT until FROM credit_hold WHERE id=1").fetchone()
            lease = db.execute("SELECT until FROM credit_probe WHERE id=1").fetchone()
            credit_blocked = bool(hold and (hold[0] > instant or (lease and lease[0] > instant)))
            _seed_site_usage(db, log_path, day, hour, registry)
            row = db.execute("SELECT attempts, notified FROM daily_cap WHERE day=?", (day,)).fetchone()
            db.execute("DELETE FROM daily_cap WHERE day < ?", (day,))
            db.execute("DELETE FROM site_usage WHERE day < ?", (day,))
            db.execute("DELETE FROM budget_seed WHERE day < ?", (day,))
            db.execute("DELETE FROM budget_pause WHERE resets_at <= ?", (now.strftime("%Y-%m-%dT%H:%M:%SZ"),))
            allowed = row[0] < cap and not credit_blocked
            if credit_blocked:
                refused = ("vendor_credit_exhausted", caller,
                           datetime.fromtimestamp(max(hold[0], lease[0] if lease else 0),
                                                  timezone.utc).isoformat())
            if row[0] >= cap and not row[1]:
                notice = True
                db.execute("UPDATE daily_cap SET notified=1 WHERE day=?", (day,))
            if allowed and registry is not None and site is not None:
                hour_used = db.execute("SELECT COALESCE(SUM(count),0) FROM site_usage WHERE hour=?",
                                       (hour,)).fetchone()[0]
                # Include concrete names written before wildcard accounting was fixed.
                rows = db.execute("SELECT site,hour,count FROM site_usage WHERE day=?", (day,)).fetchall()
                matched = [(h, n) for name, h, n in rows
                           if name == UNATTRIBUTED_SITE or
                           (call_site(name, registry) or {}).get("caller", name) == site_id]
                site_day = sum(n for _, n in matched)
                site_hour = sum(n for h, n in matched if h == hour)
                if hour_used >= registry["hourly_paid_call_cap"]:
                    refused = ("hourly_paid_call_cap", "*", next_hour)
                elif site_day >= site["daily_budget"]:
                    refused = ("site_daily_budget", site_id, next_day)
                elif site_hour >= site["hourly_budget"]:
                    refused = ("site_hourly_budget", site_id, next_hour)
                if refused:
                    allowed = False
                    db.execute("INSERT OR IGNORE INTO budget_pause VALUES (?,?,?)", refused)
            if allowed:
                if hold:
                    probe_token = str(uuid.uuid4())
                    db.execute("INSERT OR REPLACE INTO credit_probe VALUES (1,?,?)",
                               (probe_token, instant + CREDIT_PROBE_SECONDS))
                db.execute("UPDATE daily_cap SET attempts=attempts+1 WHERE day=?", (day,))
                db.execute("INSERT INTO site_usage VALUES (?,?,?,1) ON CONFLICT(day,hour,site) "
                           "DO UPDATE SET count=count+1", (day, hour, site_id))
            alerts = _claim_spend_alerts(db, log_path, day, row[0], allowed, cap, caller)
            db.commit()
            receipt = _reservation_receipt.get()
            if (allowed and receipt is not None and receipt["caller"] == caller
                    and receipt["session_id"] == session):
                days = receipt["utc_days"]
                days[day] = days.get(day, 0) + 1
        finally:
            db.close()
    except (OSError, sqlite3.Error) as exc:
        raise TypeSafeError(f"Jev unavailable: daily cap accounting failed ({type(exc).__name__})") from None
    if repair_credit:
        try:
            _credit_marker().unlink(missing_ok=True)
            _CREDIT_STATE_UNSAFE.discard(_cap_db_path())
        except OSError:
            pass  # A durable latch that cannot be removed continues to refuse.
    _dispatch_spend_alerts(alerts)
    if refused:
        scope, _, resets_at = refused
        _record_refusal(questions, facets, caller, question_kind, prompt_sha256, scope,
                        _session_id(), now=now)
        raise JevCallRefused(f"Jev unavailable: {scope} reached ({caller}); resets {resets_at}",
                             code=scope, site=caller, scope=scope, resets_at=resets_at)
    if notice:
        _append_call_receipt(questions, facets, None, log_path, caller=caller,
                             question_kind=question_kind, prompt_sha256=prompt_sha256,
                             ok=False, error="daily_paid_call_cap")
    if not allowed:
        raise JevCallRefused(f"Jev unavailable: daily paid call cap reached ({cap}, UTC {day})",
                             code="daily_paid_call_cap", site=caller, scope="daily_paid_call_cap",
                             resets_at=next_day)
    return probe_token


def active_pause(*, sites=None, now=None):
    """The budget pause in force now, as {"scope", "resets_at"}, or None.

    Global pauses (daily cap, hourly cap) always count; a site budget pause
    counts only for the named `sites`. Read-only; storage failures raise
    TypeSafeError so advisory callers stay visible.
    """
    now = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    day, _, _, next_day = _windows(now)
    stamp = now.strftime("%Y-%m-%dT%H:%M:%SZ")
    path = Path(_cap_db_path())
    if not path.exists():
        return None
    try:
        db = sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True, timeout=1)
        try:
            row = db.execute("SELECT attempts FROM daily_cap WHERE day=?", (day,)).fetchone()
            if row and row[0] >= _daily_cap_limit():
                return {"scope": "daily_paid_call_cap", "resets_at": next_day}
            if not db.execute("SELECT 1 FROM sqlite_master WHERE name='budget_pause'").fetchone():
                return None
            registry = load_call_sites()
            names = ["*"] + [(call_site(name, registry) or {}).get("caller", name)
                             for name in sites or []]
            marks = ",".join("?" * len(names))
            found = db.execute(f"SELECT scope,resets_at FROM budget_pause WHERE resets_at > ? "
                               f"AND site IN ({marks}) ORDER BY resets_at DESC LIMIT 1",
                               (stamp, *names)).fetchone()
        finally:
            db.close()
    except (OSError, sqlite3.Error, TypeSafeError) as exc:
        raise TypeSafeError(f"Jev pause storage unavailable ({type(exc).__name__})") from None
    return {"scope": found[0], "resets_at": found[1]} if found else None


PAUSE_WORDS = {"daily_paid_call_cap": "daily paid-call cap",
               "hourly_paid_call_cap": "hourly paid-call cap",
               "site_daily_budget": "this check's daily budget",
               "site_hourly_budget": "this check's hourly budget"}


def pause_notice(session, *, sites=None, now=None):
    """ONE line per session per pause window, then None until the next window.

    Hooks call this instead of printing "[jev ...] unavailable" on every tool
    call and Stop while a cap holds. A missing session gets no line at all.
    """
    pause = active_pause(sites=sites, now=now)
    if not pause or not session:
        return None
    try:
        with closing(sqlite3.connect(_cap_db_path(), timeout=1.0)) as db, db:
            _budget_tables(db)
            fresh = db.execute("INSERT OR IGNORE INTO pause_notice VALUES (?,?,?)",
                               (str(session), pause["scope"], pause["resets_at"])).rowcount == 1
    except (OSError, sqlite3.Error) as exc:
        raise TypeSafeError(f"Jev notice storage unavailable ({type(exc).__name__})") from None
    if not fresh:
        return None
    resumes = pause["resets_at"].replace("T", " ").replace(":00Z", " UTC")
    return (f"[jev] paused: {PAUSE_WORDS.get(pause['scope'], pause['scope'])} reached; "
            f"resumes {resumes}. Jev checks are skipped until then; this is said once.")


def outage_notice(session, *, now=None):
    """ONE line per session per UTC hour while Jev is unavailable for any
    reason other than a budget pause (vendor down, credits exhausted)."""
    if not session:
        return None
    _, hour, _, _ = _windows(now or datetime.now(timezone.utc))
    try:
        with closing(sqlite3.connect(_cap_db_path(), timeout=1.0)) as db, db:
            _budget_tables(db)
            fresh = db.execute("INSERT OR IGNORE INTO pause_notice VALUES (?,?,?)",
                               (str(session), "outage", hour)).rowcount == 1
    except (OSError, sqlite3.Error) as exc:
        raise TypeSafeError(f"Jev notice storage unavailable ({type(exc).__name__})") from None
    if not fresh:
        return None
    return ("[jev] unavailable this hour (vendor or account outage, not a cap); "
            "Jev checks are skipped and this is said once per hour.")


SITE_SPEND_ACTION = ("on breach (a site at its daily budget, or the day past 50% of the cap): "
                     "owner orchestrator · remediation cut the top site's trigger to its judgment "
                     "point or lower its budget in ops/config/jev-call-sites.v1.json · verify "
                     "next UTC day every site under budget · auto-clear at UTC rollover")


def spend_by_site_health(*, now=None):
    """Today's paid attempts per registered site against its budget, one line."""
    now = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    day, hour, _, _ = _windows(now)
    try:
        registry = load_call_sites()
        cap = _daily_cap_limit()
        usage, hour_used, total = {}, 0, 0
        path = Path(_cap_db_path())
        if path.exists():
            db = sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True, timeout=1)
            try:
                if db.execute("SELECT 1 FROM sqlite_master WHERE name='site_usage'").fetchone():
                    usage = dict(db.execute("SELECT site, SUM(count) FROM site_usage WHERE day=? "
                                            "GROUP BY site", (day,)).fetchall())
                    hour_used = db.execute("SELECT COALESCE(SUM(count),0) FROM site_usage WHERE hour=?",
                                           (hour,)).fetchone()[0]
                row = db.execute("SELECT attempts FROM daily_cap WHERE day=?", (day,)).fetchone() \
                    if db.execute("SELECT 1 FROM sqlite_master WHERE name='daily_cap'").fetchone() else None
                total = row[0] if row else 0
            finally:
                db.close()
        grouped = {}
        for name, used in usage.items():
            identity = (call_site(name, registry) or {}).get("caller", name)
            grouped[identity] = grouped.get(identity, 0) + used
        usage = grouped
        unattributed = usage.pop(UNATTRIBUTED_SITE, 0)
        if unattributed:
            usage = {name: usage.get(name, 0) + unattributed for name in registry["sites"]} | {
                name: used for name, used in usage.items() if name not in registry["sites"]}
        over = [name for name, used in usage.items()
                if name in registry["sites"] and used >= registry["sites"][name]["daily_budget"]]
        status = "WARN" if over or total * 100 >= cap * 50 else "OK"
        parts = [f"{name}={used}/{registry['sites'][name]['daily_budget'] if name in registry['sites'] else '?'}"
                 for name, used in sorted(usage.items(), key=lambda kv: (-kv[1], kv[0]))]
        return (f"{status} jev spend by site — UTC {day} · {total}/{cap} paid attempts · this hour "
                f"{hour_used}/{registry['hourly_paid_call_cap']} · {' '.join(parts) or 'no site spend'}"
                f"{' · unattributed=' + str(unattributed) + ' charged to each site' if unattributed else ''}"
                f"{' · over budget: ' + ','.join(sorted(over)) if over else ''} · {SITE_SPEND_ACTION}")
    except (OSError, sqlite3.Error, TypeSafeError) as exc:
        return f"UNKNOWN jev spend by site — {type(exc).__name__} · {SITE_SPEND_ACTION}"


def ask(state, questions, *, model=DEFAULT_MODEL, timeout=TIMEOUT_SECONDS,
        api_key=None, retries=RATE_LIMIT_RETRIES, endpoint=ENDPOINT, opener=None,
        facets=None, calls_log=JEV_CALLS_LOG, deadline=None, caller=None,
        cache_ttl_seconds=JUDGE_CACHE_TTL_SECONDS, cache_path=JUDGE_CACHE_PATH, account=None,
        work_class="system_work", purpose="call", server_runner=None, session_id=None,
        transcript_path=None):
    """Compatibility entrypoint: all existing callers cross the class switch.

    The original transport retains its wire, retry, cache and receipt contract.
    Runtime consumers explicitly pass app_runtime, which cannot use Decisions.
    """
    try:
        return JUDGE.ask(state, questions, jev=partial(_ask_jev, work_class=work_class), work_class=work_class,
                         model=model, timeout=timeout, api_key=api_key, retries=retries,
                         endpoint=endpoint, opener=opener, facets=facets, calls_log=calls_log,
                         deadline=deadline, caller=caller or _caller_name(),
                         cache_ttl_seconds=cache_ttl_seconds, cache_path=cache_path, account=account,
                         purpose=purpose, server_runner=server_runner, session_id=session_id,
                         transcript_path=transcript_path)
    except JUDGE.JudgeUnavailable as exc:
        raise TypeSafeError(str(exc)) from None


def _ask_jev(state, questions, *, model=DEFAULT_MODEL, timeout=TIMEOUT_SECONDS,
             api_key=None, retries=RATE_LIMIT_RETRIES, endpoint=ENDPOINT, opener=None,
             facets=None, calls_log=JEV_CALLS_LOG, deadline=None, caller=None,
             cache_ttl_seconds=JUDGE_CACHE_TTL_SECONDS, cache_path=JUDGE_CACHE_PATH, account=None,
             purpose="call", server_runner=None, session_id=None, work_class="system_work",
             transcript_path=None):
    """Evaluate `state` against a map of questions in ONE request.

    `state` is a string, or a mapping when the context has several parts —
    prefer named fields, and reference them from an instruction with backticked
    paths such as `ticket.messages[0].text`. `questions` maps a caller-chosen
    id to a question built by noul/choice/score; the ids come back unchanged and
    are never sent to the model, so the instruction must carry its full meaning.

    `facets` labels the judgments for diagnostic and historical receipt readers.
    It never imposes per-turn obligations. `transcript_path` optionally binds
    receipts to the dispatching human; otherwise the native session is discovered.
    Retries and late answers keep that owner. Unknown owners stay unbound.

    Returns the decoded response: {"model": ..., "answers": {...},
    "usage": {...}}. `opener` is for the offline selftest and is not used in
    production. On a successful response this also appends one best-effort
    receipt row to `calls_log` (default out/jev-calls.jsonl) — see
    JEV_CALLS_LOG's module-level note for what it carries and why.

    System-work calls probe the Worker's cache before reserving one paid Worker
    attempt. The explicit transport modes are validated before the Worker can
    fetch: an older Worker rejects them and the guarded direct route takes over.
    A failed paid Worker attempt retains its reservation before the direct
    transport. Runtime calls keep the pinned vendor route: the external Worker
    ingress derives system_work and cannot carry a caller-selected runtime class.
    Hook-internal calls remain direct. `purpose`
    distinguishes a normal call from the build advisory; `session_id` binds a
    hook's payload identity, and `server_runner` is an offline test seam.
    The Worker owns caching on that path; direct calls retain the local cache.

    `deadline` is optional: an absolute time.monotonic() value. With one,
    each attempt's timeout and each rate-limit sleep is capped at the time
    remaining, and no attempt or retry starts once it has passed — a caller
    under a hook timeout gets a TypeSafeError instead of a killed hook.
    `retries=0` turns rate-limit retries off entirely.

    `caller` names the invoking code in the usage log; if omitted it is inferred
    from the immediate caller file. Every real caller uses the shared 60s
    on-disk duplicate cache by default; a caller can explicitly pass zero.
    `account` names an account or organization when the caller has one. The
    credential hash also scopes the cache, including when no name is supplied.
    Cache hits return usage=None and cannot count as fresh vendor-call evidence.
    Paid attempts (including retries) are capped per UTC day by
    ops/config/jev-cost-guard.v1.json's required daily_paid_call_cap.
    Offline injected openers do not reserve paid calls or write live receipts.
    """
    if not isinstance(questions, dict) or not questions:
        raise TypeSafeError("ask needs a non-empty map of questions")
    problem = malformed_request(state, questions)
    if problem:
        raise TypeSafeError(f"malformed Jev request, nothing sent: {problem}")

    payload = {"state": state, "model": model, "questions": questions}
    body = json.dumps(payload).encode("utf-8")
    caller = caller or _caller_name()
    question_kind = _question_kind(questions)
    prompt_sha256 = _prompt_sha256(payload)

    state_chars = len(json.dumps(state))
    if state_chars > STATE_BUDGET_CHARS:
        raise TypeSafeError(
            f"state is roughly {state_chars} characters, over this module's "
            f"{STATE_BUDGET_CHARS} guard. Narrow it in code: accuracy falls as "
            "a state fills with detail unrelated to the decision, so trimming "
            "is the fix rather than raising the guard."
        )

    if not isinstance(cache_ttl_seconds, (int, float)) or not math.isfinite(cache_ttl_seconds) or cache_ttl_seconds < 0:
        raise TypeSafeError("cache_ttl_seconds must be a finite nonnegative number")
    server_error = None
    reservation_error = None
    dispatch_binding = _dispatch_binding(transcript_path, session_id)
    registry = site = None
    if opener is None:
        registry, site = _admit_paid_call(caller, dispatch_binding[0], questions, facets,
                                          question_kind, prompt_sha256)
    started = time.monotonic()
    in_hook = os.environ.get(IN_HOOK_ENV) == "1" and purpose != "build_advisory"
    if opener is None and api_key is None and not in_hook and work_class != "app_runtime":
        server_timeout = float(timeout)
        if deadline is not None:
            server_timeout = min(server_timeout, deadline - started)
        if server_timeout <= 0:
            raise TypeSafeError("deadline passed before the request could be sent")
        server_deadline = started + server_timeout * SERVER_SHARE_OF_TIMEOUT
        served, server_error = server_ask(
            state, questions, model=model, facets=facets, purpose=purpose,
            session_id=dispatch_binding[0] or "unbound",
            timeout=server_deadline - time.monotonic(), transport_mode="cache_only", runner=server_runner)
        if served is not None and served.get("cache_hit") is not True:
            raise TypeSafeError("Jev unavailable: Worker cache-only contract violated")
        if served is None and server_error == "cache_miss" and _hold_open("worker_breaker"):
            # The Worker's own vendor call has been failing: go direct with ONE
            # reservation instead of paying a doomed Worker attempt first.
            server_error = "worker_breaker_open"
        elif served is None and server_error == "cache_miss":
            try:
                remaining = server_deadline - time.monotonic()
                if remaining <= 0:
                    raise TypeSafeError("deadline passed before the paid Worker attempt")
                upstream = {}
                served, server_error = server_ask(
                    state, questions, model=model, facets=facets, purpose=purpose,
                    session_id=dispatch_binding[0] or "unbound", caller=caller,
                    timeout=remaining, transport_mode="paid_once", runner=server_runner,
                    upstream=upstream, admission=(registry, site))
            except TypeSafeError as error:
                # A free direct-cache answer may still exist after a Worker
                # cache miss. No transport may run if this reservation failed.
                reservation_error = error
            else:
                if served is None:
                    status, reason = upstream.get("status"), upstream.get("reason")
                    _append_call_receipt(questions, facets,
                        {"http_status": status, "upstream_reason": reason}, calls_log,
                        dispatch_binding=dispatch_binding, caller=caller,
                        question_kind=question_kind, prompt_sha256=prompt_sha256,
                        ok=False, error=server_error, server_error=server_error, session=session_id)
                    reservation_error = upstream.get("refusal")
                elif served.get("cache_hit") is True:
                    raise TypeSafeError("Jev unavailable: Worker paid-once contract violated")
        if served is not None:
            cache_hit = served.get("cache_hit") is True
            # Worker cache hits carry typed answers and a new bound receipt,
            # but no billable usage. Validate their answers without reporting
            # the validation placeholder as measured spend.
            validation = ({**served, "usage": {"input_tokens": 0, "output_tokens": 0}}
                          if cache_hit else served)
            valid = usable_judgment(validation, questions)
            calibration = (_safe_calibration_block(state, questions, served, model)
                           if valid else None)
            _append_call_receipt(questions, facets,
                {**served, "schema_valid": valid, "usable": valid}, calls_log, dispatch_binding=dispatch_binding,
                caller=caller, question_kind=question_kind, prompt_sha256=prompt_sha256,
                ok=valid, cache_hit=cache_hit, calibration=calibration, session=session_id)
            if not valid:
                raise TypeSafeError("TypeSafe returned an unusable judgment")
            served["calibration"] = calibration
            return served
        # Preserve the caller's total budget through the direct fallback.
        deadline = min(deadline, started + float(timeout)) if deadline is not None else started + float(timeout)
        timeout = deadline - time.monotonic()
        if reservation_error is None and timeout < MIN_DIRECT_SECONDS:
            raise TypeSafeError(f"Jev server path failed ({server_error}) and no time is left for a direct call")
    elif in_hook:
        server_error = "in_hook_direct"
    use_cache = cache_ttl_seconds > 0 and opener is None
    if reservation_error is not None and not use_cache:
        raise reservation_error
    try:
        credential = api_key or read_api_key()
    except TypeSafeError:
        if reservation_error is not None:
            raise reservation_error
        raise
    cache_key = (_cache_key(endpoint, account, credential, model, caller,
                            question_kind, prompt_sha256) if use_cache else None)
    if use_cache:
        hit = _cached_result(cache_path, cache_key)
        if hit is not None:
            hit["calibration"] = _safe_calibration_block(state, questions, hit, model)
            _append_call_receipt(questions, facets, hit, calls_log, dispatch_binding=dispatch_binding, caller=caller,
                                 question_kind=question_kind, prompt_sha256=prompt_sha256,
                                 ok=False, cache_hit=True, calibration=hit["calibration"])
            return hit
    if reservation_error is not None:
        raise reservation_error

    request = urllib.request.Request(
        endpoint, data=body, method="POST",
        headers={
            "Authorization": f"Bearer {credential}",
            "Content-Type": "application/json",
            # Cloudflare rejects urllib's default Python-urllib signature with
            # error 1010. Identify this server-side client explicitly.
            "User-Agent": "carr-typesafe-client/1.0",
        },
    )

    send = opener or urllib.request.urlopen
    attempt = 0
    while True:
        attempt_timeout = timeout
        if deadline is not None:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise TypeSafeError("deadline passed before the request could be sent")
            attempt_timeout = min(timeout, remaining)
        probe_token = None
        if opener is None:
            probe_token = _reserve_paid_call(questions, facets, caller,
                                             question_kind, prompt_sha256, registry, site,
                                             session=dispatch_binding[0])
            # Accounting can wait on another worker's transaction. Preserve
            # the caller's absolute deadline before starting any transport.
            if deadline is not None:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise TypeSafeError("deadline passed during daily cap accounting")
                attempt_timeout = min(timeout, remaining)
        try:
            with send(request, timeout=attempt_timeout) as response:
                http_status = getattr(response, "status", None)
                try:
                    result = json.load(response)
                except (json.JSONDecodeError, UnicodeDecodeError):
                    if opener is None:
                        _append_call_receipt(questions, facets,
                            {"http_status": http_status, "schema_valid": False,
                             "usable": False}, calls_log, dispatch_binding=dispatch_binding, caller=caller,
                            question_kind=question_kind, prompt_sha256=prompt_sha256,
                            ok=False, error="invalid_json")
                    raise
            schema_valid = usable_judgment(result, questions)
            usable = (type(http_status) is int and 200 <= http_status < 300 and
                      schema_valid)
            calibration = (_safe_calibration_block(state, questions, result, model)
                           if schema_valid else None)
            # Round-2 fix: only a REAL production call (no opener) writes a
            # receipt. `opener` is the offline selftest/mock path (see the
            # docstring above) — a mock response was never actually seen by
            # the vendor, so a receipt for it would let a selftest run count
            # as this turn's real Jev evidence.
            if opener is None:
                receipt = ({**result, "http_status": http_status,
                            "schema_valid": schema_valid, "usable": usable}
                           if isinstance(result, dict) else
                           {"http_status": http_status, "schema_valid": False,
                            "usable": False})
                _append_call_receipt(questions, facets, receipt, calls_log, dispatch_binding=dispatch_binding,
                                     caller=caller, question_kind=question_kind,
                                     prompt_sha256=prompt_sha256, ok=usable,
                                     calibration=calibration,
                                     server_error=server_error or "direct_call", session=session_id)
            if not usable:
                raise TypeSafeError("TypeSafe returned an unusable judgment")
            _finish_credit_probe(probe_token)
            if use_cache:
                _store_cached_result(cache_path, cache_key, result,
                                     cache_ttl_seconds)
            # Attached after caching, so the cache holds the vendor's answer
            # alone and a hit recomputes the block from it.
            result["calibration"] = calibration
            return result
        except urllib.error.HTTPError as err:
            if opener is None:
                _append_call_receipt(questions, facets, {"http_status": err.code}, calls_log,
                                     dispatch_binding=dispatch_binding, caller=caller,
                                     question_kind=question_kind, prompt_sha256=prompt_sha256,
                                     ok=False, error=f"HTTP {err.code}")
                if err.code == 402:
                    _open_hold("credit_hold", CREDIT_HOLD_SECONDS)
            # 429 is documented as expected under load, and the service's own
            # limits "can change without notice". Honour retry-after when it is
            # sent; fall back to a short backoff when it is not.
            if err.code == 429 and attempt < retries:
                wait = err.headers.get("retry-after") if err.headers else None
                try:
                    delay = float(wait)
                except (TypeError, ValueError):
                    delay = 2.0 * (attempt + 1)
                if deadline is not None and delay >= deadline - time.monotonic():
                    # Sleeping as asked would leave no time for the retry.
                    raise TypeSafeError(
                        "TypeSafe returned HTTP 429 and its retry-after runs past "
                        "the caller's deadline") from None
                time.sleep(delay)
                attempt += 1
                continue
            # Report the status and what came back. NEVER report the request:
            # it carries the bearer token in a header.
            detail = ""
            try:
                detail = err.read().decode("utf-8", "replace")[:400]
            except Exception:
                pass
            raise TypeSafeError(f"TypeSafe returned HTTP {err.code}: {detail}") from None
        except urllib.error.URLError as err:
            if opener is None:
                _append_call_receipt(questions, facets, None, calls_log, dispatch_binding=dispatch_binding, caller=caller,
                                     question_kind=question_kind, prompt_sha256=prompt_sha256,
                                     ok=False, error="network")
            raise TypeSafeError(
                f"could not reach {endpoint}: {err.reason}. If this is a refusal "
                "rather than a network fault, check that the host is still in "
                "KNOWN_HOSTS in hooks/guard-unattended.py."
            ) from None
        except (json.JSONDecodeError, UnicodeDecodeError, TypeSafeError):
            raise
        except Exception as err:
            if opener is None:
                _append_call_receipt(questions, facets, None, calls_log, dispatch_binding=dispatch_binding, caller=caller,
                                     question_kind=question_kind, prompt_sha256=prompt_sha256,
                                     ok=False, error=type(err).__name__)
            raise


def decide(answer, *, yes_at=0.8, no_at=0.2, min_confidence=0.6):
    """Turn one raw answer into an act: accept it, or send it to someone else.

    Returns {"outcome": "yes"|"no"|"value", "escalate": bool, "value": ...,
    "confidence": float|None}. `escalate` true means the caller routes this case
    to a person or to a reasoning model rather than acting on it — the pattern
    every published build used, and the reason a typed answer is safe to
    automate at all.

    THE DEFAULTS ARE PLACEHOLDERS, NOT FINDINGS. Thresholds have to be measured
    on CARR's own data and against the cost of being wrong in that specific
    place, and a threshold that suits gate precision will not suit a client
    document. Treat any number here as a starting point to replace.

    Two cautions the docs make explicitly. A noul near 0.5 means yes and no are
    about equally likely, not that the answer is moderately true. And for a
    choice or score, confidence summarises how concentrated the distribution
    is — it is not a probability that the workflow is correct, and low
    confidence across several equally acceptable options need not invalidate a
    harmless preference.
    """
    kind = answer.get("type")
    if kind == "noul":
        probability = float(answer["noul"])
        if probability >= yes_at:
            return {"outcome": "yes", "escalate": False,
                    "value": probability, "confidence": None}
        if probability <= no_at:
            return {"outcome": "no", "escalate": False,
                    "value": probability, "confidence": None}
        return {"outcome": "no", "escalate": True,
                "value": probability, "confidence": None}
    if kind in ("choice", "score"):
        confidence = answer.get("confidence")
        value = answer.get("choice") if kind == "choice" else answer.get("score")
        return {
            "outcome": "value",
            "escalate": confidence is None or float(confidence) < min_confidence,
            "value": value,
            "confidence": None if confidence is None else float(confidence),
        }
    raise TypeSafeError(f"unknown answer type: {kind!r}")
