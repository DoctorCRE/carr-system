#!/usr/bin/env python3
"""check-eval-receipt.py — a change to an LLM-steering surface ships with a measurement.

THE RULE. evals/surfaces.json registers every file in this repository whose
text or configuration changes what a model reads or which model runs. A pull
request that changes one of them carries, for each surface it touches, either

  evals/<surface>/receipt.json   changed IN THIS pull request, and valid, or
  no-eval: <surface>: <reason>   a line in the pull-request body naming why a
                                 measurement is impossible.

The receipt is what /claude-api build-eval and /claude-api hillclimb produce,
written down: cases and their split, repeats, the grader and its validation,
and baseline against candidate on the SEALED TEST split, per dimension, with
confidence intervals and a verdict. evals/README.md is the procedure.

ONE EVALUATION SYSTEM, NOT TWO. CARR's standing evaluation ruling is the
laddered, multidimensional portfolio in tools/room-bridge/evaluation_kernel.py:
rungs smoke / regression / hill_climb / launch, named dimensions with no
aggregate, results bound to user-job stages and to the model + harness +
adapter that produced them, and a critical-dimension regression that no
headline can hide. A receipt is a small projection of that shape, and this file
reuses the kernel's own vocabulary and its critical_dimension_blockers() rather
than carrying a second copy. Offline suites bind through ops/ai_eval.py.

A RECEIPT IS RECOMPUTED, NEVER TRUSTED. Its `evidence` block binds, by
sha256, the harness and scorer code (`source`), every file the measured run
read (`dependencies`), the raw per-case observations of both arms
(`cohorts`), and the labels they are graded against (`expectations`, a
versioned file: the same version must keep the same bytes as at the merge
base, so relabelling is an explicit new version). The check requires the
two cohorts to be the same cases with the same splits and inputs as the
expectations, re-runs the bound scorer, and refuses any dimension, interval,
case count or oracle/null control that differs from what the receipt says.
So deleting a failing row, shrinking the owed denominator, editing result
bytes and carrying over a stale summary all fail.

WHAT A RECEIPT MAY NOT SAY. "ship" when the primary dimension's paired delta
interval contains zero, when a critical dimension failed or regressed, when the
grader failed its validation, or when the eval's noise floor is not smaller
than the smallest gain worth having. A gain inside the noise must say "do not
merge on quality grounds" in its verdict; it may still ship on cost at parity
when it is cheaper per case.

WHERE IT ENFORCES. In a pull_request run (GITHUB_EVENT_NAME=pull_request and a
readable GITHUB_EVENT_PATH) a missing receipt or line fails; an unreadable PR
event refuses the check. A changed surface whose receipt says do_not_merge or
inconclusive also fails, even when that verdict correctly describes its data.
Anywhere else, the
pre-push floor and local runs included, there is no pull-request body to read,
so a missing receipt is reported and does not fail; a MALFORMED receipt, an
unregistered context-emitting hook, or a broken registry fails everywhere.
The event payload is the body as of the last push: edit the body, then push
(or re-run with a fresh event) for the line to count.

Exit 0 pass, 1 refusal, 2 the check could not run (registry unreadable, no base
in a pull-request run).
"""

from __future__ import annotations

import argparse
import hashlib
import io
import tarfile
import tempfile
import json
import os
import re
import subprocess
import sys
import types
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "ops"))
sys.path.insert(0, str(ROOT / "tools" / "room-bridge"))
import ai_eval  # noqa: E402  (also puts room-bridge on the path)
import evaluation_kernel as kernel  # noqa: E402
import execution_contract as contract  # noqa: E402

SURFACE_ID = re.compile(r"^[a-z0-9][a-z0-9-]*$")
CASE_SOURCES = {"production_trace", "human_judged_hard_case", "bug_report", "hand_written", "synthesized"}
REAL_SOURCES = {"production_trace", "human_judged_hard_case"}
GRADER_KINDS = {"programmatic", "pairwise", "pointwise_rubric", "human"}
DECISIONS = {"ship", "ship_cost_at_parity", "do_not_merge", "inconclusive"}
IN_NOISE_PHRASE = "do not merge on quality grounds"
SCHEMA_VERSION = 2
REQUIRED = {"schema_version", "surface", "change", "measured_on", "rung", "adapter", "cases", "split",
            "repeats", "grader", "noise_floor", "min_useful_gain", "primary_dimension", "dimensions",
            "stage_results", "cost", "verdict", "evidence"}
EVIDENCE_FIELDS = {"scorer", "source", "dependencies", "expectations", "cohorts"}
ARMS = ("baseline", "candidate")
HEX64 = re.compile(r"^[0-9a-f]{64}$")
RECOMPUTE_TOLERANCE = 1e-9
CONTROLS = ("oracle_pass_rate", "null_pass_rate")
OPTIONAL = {"overall", "offline_suite", "rounds", "notes"}
MEASURE_FIELDS = {"baseline", "candidate", "delta"}
PLACEHOLDER_REASONS = re.compile(r"^(n/?a|none|tbd|trivial|minor|docs?( only)?|not needed.*|no change.*|skip.*|-+)$", re.I)
MIN_REASON_WORDS = 10
NO_EVAL = re.compile(r"^ {0,3}(?:[-*] )?no-eval:\s*([^:\s]+)\s*:\s*(.*?)\s*$", re.I)
CONTEXT_EMITTER = re.compile(r"additionalContext|systemMessage|hookSpecificOutput")


class GateError(ValueError):
    pass


# ------------------------------------------------------------------ registry
def glob_to_regex(pattern: str) -> re.Pattern[str]:
    out, i = [], 0
    while i < len(pattern):
        if pattern.startswith("**/", i):
            out.append("(?:.*/)?"); i += 3
        elif pattern.startswith("**", i):
            out.append(".*"); i += 2
        elif pattern[i] == "*":
            out.append("[^/]*"); i += 1
        elif pattern[i] == "?":
            out.append("[^/]"); i += 1
        else:
            out.append(re.escape(pattern[i])); i += 1
    return re.compile("^" + "".join(out) + "$")


def glob_match(path: str, pattern: str) -> bool:
    return bool(glob_to_regex(pattern).match(path))


def validate_registry(reg: Any) -> dict[str, Any]:
    if not isinstance(reg, dict) or reg.get("schema_version") != 1:
        raise GateError("registry schema_version must be 1")
    if not isinstance(reg.get("surfaces"), list) or not reg["surfaces"]:
        raise GateError("registry needs surfaces")
    seen = set()
    for s in reg["surfaces"]:
        if not isinstance(s, dict) or not isinstance(s.get("id"), str) or not SURFACE_ID.match(s["id"]):
            raise GateError(f"surface id is invalid: {s.get('id') if isinstance(s, dict) else s!r}")
        if s["id"] in seen:
            raise GateError(f"duplicate surface id {s['id']}")
        seen.add(s["id"])
        if not isinstance(s.get("globs"), list) or not s["globs"] or not all(isinstance(g, str) and g for g in s["globs"]):
            raise GateError(f"surface {s['id']} needs non-empty globs")
    if not isinstance(reg.get("exclude_globs", []), list):
        raise GateError("exclude_globs must be a list")
    return reg


def load_registry(path: Path) -> dict[str, Any]:
    try:
        return validate_registry(json.loads(path.read_text()))
    except (OSError, json.JSONDecodeError) as exc:
        raise GateError(f"cannot read registry {path}: {exc}") from exc


def enforced_registry(base: dict[str, Any] | None, head: dict[str, Any]) -> dict[str, Any]:
    """The registry a pull request is judged by: it can widen coverage, never narrow it.

    Surfaces and globs are the union of the merge base and the head; an exclude
    counts only when both sides carry it. A pull request that drops a glob, drops a
    surface or adds an exclude is still held to the base's coverage for its own
    changes; the narrower registry binds the next pull request after it merges.
    """
    if base is None:
        return head
    merged: dict[str, list[str]] = {}
    for s in [*base["surfaces"], *head["surfaces"]]:
        globs = merged.setdefault(s["id"], [])
        globs += [g for g in s["globs"] if g not in globs]
    head_ex = set(head.get("exclude_globs", []))
    return {
        "schema_version": 1,
        "surfaces": [{"id": sid, "globs": globs} for sid, globs in merged.items()],
        "exclude_globs": [g for g in base.get("exclude_globs", []) if g in head_ex],
    }


def surfaces_for(path: str, reg: dict[str, Any]) -> list[str]:
    if path.startswith("evals/"):
        return []  # the procedure, the registry and the receipts are not steering text
    if any(glob_match(path, g) for g in reg.get("exclude_globs", [])):
        return []
    return [s["id"] for s in reg["surfaces"] if any(glob_match(path, g) for g in s["globs"])]


def unregistered_context_hooks(root: Path, reg: dict[str, Any]) -> list[str]:
    """Hooks that put text in front of a model but that no surface names."""
    missing = []
    for hook in sorted((root / "hooks").glob("*.py")):
        rel = hook.relative_to(root).as_posix()
        try:
            text = hook.read_text(errors="replace")
        except OSError:
            continue
        if CONTEXT_EMITTER.search(text) and not surfaces_for(rel, reg):
            missing.append(rel)
    return missing


# ------------------------------------------------------------------ receipts
def _num(value: Any, lo: float | None = None, hi: float | None = None) -> bool:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return False
    return (lo is None or value >= lo) and (hi is None or value <= hi)


def _int(value: Any, lo: int) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and value >= lo


def _measure(label: str, m: Any, errs: list[str], *, lo: float = 0.0, hi: float = 1.0, point: str = "score") -> dict | None:
    if not isinstance(m, dict) or set(m) != {point, "ci_low", "ci_high"}:
        errs.append(f"{label} must be exactly {{{point}, ci_low, ci_high}}")
        return None
    if not all(_num(m[k], lo, hi) for k in m):
        errs.append(f"{label} values must be numbers in [{lo}, {hi}]")
        return None
    if not m["ci_low"] <= m[point] <= m["ci_high"]:
        errs.append(f"{label}: {point} {m[point]} lies outside its own interval [{m['ci_low']}, {m['ci_high']}]")
        return None
    return m


def _direction(delta: dict) -> str:
    if delta["ci_high"] < 0:
        return "regressed"
    if delta["ci_low"] > 0:
        return "improved"
    return "equivalent"


def claim_errors(r: Any, surface: str, root: Path = ROOT) -> list[str]:
    """Does the receipt's claim hold together: shape, kernel vocabulary, and a verdict its numbers allow."""
    errs: list[str] = []
    if not isinstance(r, dict):
        return ["receipt must be a JSON object"]
    missing, unknown = REQUIRED - set(r), set(r) - REQUIRED - OPTIONAL
    if missing:
        errs.append(f"missing fields: {', '.join(sorted(missing))}")
    if unknown:
        errs.append(f"unknown fields: {', '.join(sorted(unknown))}")
    if missing:
        return errs
    if r["schema_version"] != SCHEMA_VERSION:
        errs.append(f"schema_version must be {SCHEMA_VERSION}: a version 1 receipt carried hand-copied numbers "
                    f"with no evidence chain; regenerate it with its producer")
    if r["surface"] != surface:
        errs.append(f"surface is {r['surface']!r} but the receipt lives under evals/{surface}/")
    for key in ("change", "measured_on"):
        if not isinstance(r[key], str) or not r[key].strip():
            errs.append(f"{key} must be a non-empty string")
    if r["rung"] not in kernel.RUNGS:
        errs.append(f"rung must be one of the kernel ladder {sorted(kernel.RUNGS)}")

    # Model + harness + adapter, in the execution contract's own field set.
    a = r["adapter"]
    if not isinstance(a, dict) or set(a) != contract.ADAPTER_FIELDS:
        errs.append(f"adapter must carry exactly {sorted(contract.ADAPTER_FIELDS)}")
    else:
        try:
            if a["surface"] in contract.SURFACES:
                contract._validate_adapter(a)
            else:
                for field in contract.ADAPTER_FIELDS - {"configuration_fingerprint"}:
                    contract._string(a[field], f"adapter {field}")
                contract._digest(a["configuration_fingerprint"], "adapter configuration_fingerprint")
        except contract.ContractError as exc:
            errs.append(str(exc))

    c = r["cases"]
    if not isinstance(c, dict) or not {"total", "train", "test", "should_not_fire", "sources"} <= set(c):
        errs.append("cases must carry total, train, test, should_not_fire, sources")
    else:
        if not (_int(c["total"], 1) and _int(c["train"], 0) and _int(c["test"], 1)):
            errs.append("cases: total and test must be positive integers, train a non-negative integer")
        elif c["train"] + c["test"] != c["total"]:
            errs.append(f"cases: train {c['train']} + test {c['test']} != total {c['total']}")
        if not _int(c["should_not_fire"], 1):
            errs.append("cases.should_not_fire must be at least 1: an eval with no should-not-fire cases rewards firing on everything")
        elif _int(c["total"], 1) and c["should_not_fire"] > c["total"]:
            errs.append("cases.should_not_fire cannot exceed cases.total")
        src = c["sources"]
        if not isinstance(src, list) or not src or not set(src) <= CASE_SOURCES:
            errs.append(f"cases.sources must be a non-empty subset of {sorted(CASE_SOURCES)}")
        elif not set(src) & REAL_SOURCES:
            errs.append("cases.sources must include production_trace or human_judged_hard_case: real traffic and human-judged hard cases come first")

    s = r["split"]
    if not isinstance(s, dict) or not isinstance(s.get("method"), str) or not s["method"].strip():
        errs.append("split.method must name how train and test were drawn")
    elif s.get("sealed_test") is not True:
        errs.append("split.sealed_test must be true: the test split's transcripts are never read while proposing changes")
    if not _int(r["repeats"], 1):
        errs.append("repeats must be a positive integer")

    g = r["grader"]
    grader_ok = False
    if not isinstance(g, dict) or g.get("kind") not in GRADER_KINDS or not isinstance(g.get("validation"), dict):
        errs.append(f"grader needs kind in {sorted(GRADER_KINDS)} and a validation block")
    else:
        v = g["validation"]
        if v.get("graded_twice") is not True:
            errs.append("grader.validation.graded_twice must be true: grade the same outputs twice before trusting the grader")
        if not _num(v.get("agreement"), 0, 1):
            errs.append("grader.validation.agreement must be a number in [0, 1]")
        if v.get("result") not in {"pass", "fail"}:
            errs.append("grader.validation.result must be pass or fail")
        grader_ok = v.get("result") == "pass" and v.get("graded_twice") is True

    if not _num(r["noise_floor"], 0, 1) or not _num(r["min_useful_gain"], 0, 1) or r["min_useful_gain"] == 0:
        errs.append("noise_floor and min_useful_gain must be numbers in [0, 1], min_useful_gain above 0")
        resolvable = False
    else:
        resolvable = r["noise_floor"] < r["min_useful_gain"]

    # Dimensions: measured separately, direction derived from the paired delta.
    dims = r["dimensions"]
    kernel_rows, directions = [], {}
    if not isinstance(dims, list) or not dims:
        errs.append("dimensions must name at least one dimension; a single blended score is not a result")
        dims = []
    for d in dims:
        if not isinstance(d, dict):
            errs.append("each dimension must be an object"); continue
        did = d.get("dimension_id", "?")
        extra = set(d) - kernel.DIMENSION_FIELDS - MEASURE_FIELDS
        if extra or not MEASURE_FIELDS <= set(d):
            errs.append(f"dimension {did}: needs the kernel dimension fields plus baseline, candidate, delta")
            continue
        b = _measure(f"dimension {did} baseline", d["baseline"], errs)
        k = _measure(f"dimension {did} candidate", d["candidate"], errs)
        dl = _measure(f"dimension {did} delta", d["delta"], errs, lo=-1.0, point="value")
        kernel_rows.append({f: d.get(f) for f in kernel.DIMENSION_FIELDS})
        if b and k and dl:
            if abs((k["score"] - b["score"]) - dl["value"]) > 0.011:
                errs.append(f"dimension {did}: delta {dl['value']} does not equal candidate minus baseline "
                            f"({k['score'] - b['score']:.3f})")
            derived = _direction(dl)
            directions[did] = derived
            if d.get("direction_vs_baseline") != derived:
                errs.append(f"dimension {did}: direction_vs_baseline says {d.get('direction_vs_baseline')!r} "
                            f"but the delta interval [{dl['ci_low']}, {dl['ci_high']}] says {derived!r}")
    blockers: list[str] = []
    if kernel_rows:
        try:
            blockers = kernel.critical_dimension_blockers(kernel_rows)
        except kernel.EvalPortfolioError as exc:
            errs.append(f"dimensions: {exc}")
    dim_ids: set[str] = {d["dimension_id"] for d in dims
                         if isinstance(d, dict) and isinstance(d.get("dimension_id"), str)}
    primary = r["primary_dimension"]
    if primary not in dim_ids:
        errs.append(f"primary_dimension {primary!r} is not one of the measured dimensions")

    # User-job stages: every dimension is bound to at least one stage.
    bound: set[str] = set()
    stages = r["stage_results"]
    if not isinstance(stages, list) or not stages:
        errs.append("stage_results must bind the dimensions to user-job stages")
        stages = []
    for st in stages:
        if not isinstance(st, dict) or set(st) != kernel.STAGE_RESULT_FIELDS:
            errs.append(f"stage result must carry exactly {sorted(kernel.STAGE_RESULT_FIELDS)}"); continue
        if st["status"] not in kernel.OUTCOMES:
            errs.append(f"stage {st['stage_id']}: status must be one of {sorted(kernel.OUTCOMES)}")
        ids = st["dimension_ids"] if isinstance(st["dimension_ids"], list) else []
        for unknown_dim in sorted(set(ids) - dim_ids):
            errs.append(f"stage {st['stage_id']} names dimension {unknown_dim} that was not measured")
        bound |= set(ids)
    for unbound in sorted(dim_ids - bound):
        errs.append(f"dimension {unbound} is not bound to any user-job stage")

    cost = r["cost"]
    cheaper = False
    if not isinstance(cost, dict) or not all(_num(cost.get(k), 0) for k in ("baseline_usd_per_case", "candidate_usd_per_case")):
        errs.append("cost must carry baseline_usd_per_case and candidate_usd_per_case (cost at parity is a standing objective)")
    else:
        cheaper = cost["candidate_usd_per_case"] < cost["baseline_usd_per_case"]

    if "offline_suite" in r:
        errs += _offline_suite_errors(r["offline_suite"], root)

    # The verdict has to agree with the numbers.
    verdict = r["verdict"]
    if not isinstance(verdict, dict) or verdict.get("decision") not in DECISIONS or not isinstance(verdict.get("statement"), str):
        errs.append(f"verdict needs decision in {sorted(DECISIONS)} and a statement")
        return errs
    decision, statement = verdict["decision"], verdict["statement"]
    shipping = decision in {"ship", "ship_cost_at_parity"}
    if blockers:
        if decision != "do_not_merge":
            errs.append(f"critical dimension(s) {', '.join(blockers)} failed or regressed: blocking whatever the "
                        f"headline or overall score did; verdict must be do_not_merge")
        for b_id in blockers:
            if b_id not in statement:
                errs.append(f"verdict statement must name the blocking dimension {b_id}")
    primary_dir = directions.get(primary)
    if primary_dir == "regressed" and decision != "do_not_merge":
        errs.append(f"primary dimension {primary} regressed; verdict must be do_not_merge")
    if primary_dir == "equivalent":
        if IN_NOISE_PHRASE not in statement.lower():
            errs.append(f"primary dimension {primary}'s gain is inside the noise (delta interval contains 0); "
                        f"the verdict must say '{IN_NOISE_PHRASE}'")
        if decision == "ship":
            errs.append(f"a gain inside the noise cannot be 'ship'; use do_not_merge, inconclusive, or ship_cost_at_parity")
        if decision == "ship_cost_at_parity" and not cheaper:
            errs.append("ship_cost_at_parity needs the candidate to be cheaper per case than baseline")
    elif decision == "ship_cost_at_parity" and not cheaper:
        errs.append("ship_cost_at_parity needs the candidate to be cheaper per case than baseline")
    if shipping and not grader_ok:
        errs.append("the grader did not pass validation (graded twice, result pass); nothing it scored can ship")
    if shipping and not resolvable:
        errs.append(f"noise floor {r['noise_floor']} is not smaller than the smallest useful gain "
                    f"{r['min_useful_gain']}: this eval cannot see the win it claims")
    return errs


def _offline_suite_errors(block: Any, root: Path) -> list[str]:
    if not isinstance(block, dict) or set(block) != {"path", "suite_digest"}:
        return ["offline_suite must be exactly {path, suite_digest}"]
    path = root / block["path"]
    if not block["path"].startswith("evals/ai/"):
        return ["offline_suite.path must name a suite under evals/ai/ (the ops/ai_eval.py kernel)"]
    try:
        digest = ai_eval.load_suite(path)["_digest"]
    except ai_eval.SuiteError as exc:
        return [f"offline_suite {block['path']}: {exc}"]
    if digest != block["suite_digest"]:
        return [f"offline_suite {block['path']}: suite_digest does not match the suite on disk ({digest})"]
    return []


# ------------------------------------------------------------------ evidence
def validate_receipt(r: Any, surface: str, root: Path = ROOT, base: str | None = None) -> list[str]:
    """Every refusal for one receipt: its claim, then the evidence that has to reproduce it."""
    try:
        errs = claim_errors(r, surface, root)
    except (TypeError, KeyError, AttributeError) as exc:
        # Untrusted JSON can carry containers where claim fields require scalars.
        return [f"receipt shape invalid: {type(exc).__name__}: {exc}"]
    if errs:
        return errs
    return evidence_errors(r, surface, root, base)


def _repo_file(root: Path, rel: Any, label: str, errs: list[str], under: str | None = None) -> Path | None:
    if (not isinstance(rel, str) or not rel or rel.startswith("/") or "\\" in rel
            or any(part in ("", ".", "..") for part in rel.split("/"))):
        errs.append(f"{label}: {rel!r} must be a repository-relative path")
        return None
    if under and not rel.startswith(under):
        errs.append(f"{label}: {rel} must live under {under}")
        return None
    path = root / rel
    try:
        resolved = path.resolve(strict=True)
    except OSError:
        errs.append(f"{label}: {rel} does not exist")
        return None
    if not resolved.is_relative_to(root.resolve()) or not resolved.is_file():
        errs.append(f"{label}: {rel} must be a file inside the repository")
        return None
    return path


def _bound_bytes(root: Path, rel: Any, digest: Any, label: str, errs: list[str],
                 under: str | None = None) -> bytes | None:
    """The file's bytes, only when they hash to the digest the receipt binds."""
    path = _repo_file(root, rel, label, errs, under)
    if path is None:
        return None
    if not isinstance(digest, str) or not HEX64.match(digest):
        errs.append(f"{label}: {rel} needs a 64-hex sha256")
        return None
    data = path.read_bytes()
    actual = hashlib.sha256(data).hexdigest()
    if actual != digest:
        errs.append(f"{label}: {rel} sha256 is {actual}, but the receipt binds {digest}")
        return None
    return data


def _manifest(root: Path, block: Any, label: str, errs: list[str]) -> dict[str, str]:
    if not isinstance(block, dict) or not block:
        errs.append(f"evidence.{label} must bind at least one file by sha256")
        return {}
    for rel, digest in sorted(block.items()):
        _bound_bytes(root, rel, digest, f"evidence.{label}", errs)
    return block


def _expectations_version_errors(root: Path, rel: str, version: str, data: bytes, base: str | None) -> list[str]:
    """Labels are versioned: the same version at the merge base must be the same bytes."""
    if base is None:
        return []
    # A version belongs to the surface, independent of its filename.
    home = "/".join(rel.split("/")[:2]) + "/"
    rc, listing = git(root, "ls-tree", "-r", "--name-only", base, "--", home)
    if rc != 0:
        return [f"evidence.expectations: cannot list labels at the merge base {base}"]
    for old_rel in listing.splitlines():
        proc = subprocess.run(["git", "-C", str(root), "show", f"{base}:{old_rel}"], capture_output=True)
        if proc.returncode != 0:
            return [f"evidence.expectations: cannot read {old_rel} at the merge base {base}"]
        try:
            doc = json.loads(proc.stdout)
        except (json.JSONDecodeError, UnicodeDecodeError):
            continue
        if isinstance(doc, dict) and "cases" in doc and doc.get("version") == version and proc.stdout != data:
            return [f"evidence.expectations: {rel} changed its labels without a new version (still {version!r}); "
                    f"version identity is frozen across path changes from {old_rel}"]
    return []


def _cohort(rows_bytes: bytes, arm: str, cases: dict[str, Any], errs: list[str]) -> list[dict] | None:
    """Parse one arm and require it to be exactly the expectation set: no gaps, no repeats, same inputs."""
    rows, seen, before = [], set(), len(errs)
    for n, line in enumerate(rows_bytes.decode("utf-8", "replace").splitlines(), 1):
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError as exc:
            errs.append(f"evidence.cohorts.{arm}: line {n} is not JSON: {exc}")
            continue
        cid = row.get("case_id") if isinstance(row, dict) else None
        if not isinstance(cid, str):
            errs.append(f"evidence.cohorts.{arm}: line {n} has no case_id")
            continue
        if cid in seen:
            errs.append(f"evidence.cohorts.{arm} repeats case {cid}")
            continue
        seen.add(cid)
        exp = cases.get(cid)
        if exp is None:
            errs.append(f"evidence.cohorts.{arm} carries case {cid}, which the expectations do not label")
            continue
        if row.get("split") != exp["split"]:
            errs.append(f"evidence.cohorts.{arm}: case {cid} is in split {row.get('split')!r}, "
                        f"the expectations say {exp['split']!r}")
        if row.get("input_sha256") != exp["input_sha256"]:
            errs.append(f"evidence.cohorts.{arm}: case {cid} was run on a different input than the expectations label")
        rows.append(row)
    missing = sorted(set(cases) - seen)
    if missing:
        errs.append(f"evidence.cohorts.{arm} is missing {len(missing)} labelled case(s): {', '.join(missing[:5])}"
                    + (" ..." if len(missing) > 5 else ""))
    return rows if len(errs) == before else None


def _same(a: Any, b: Any) -> bool:
    return _num(a) and _num(b) and abs(a - b) <= RECOMPUTE_TOLERANCE


def _recompute_errors(r: dict, measured: Any) -> list[str]:
    if not isinstance(measured, dict) or not isinstance(measured.get("dimensions"), dict):
        return ["evidence.scorer must return {dimensions, controls}"]
    errs: list[str] = []
    dims = {d.get("dimension_id"): d for d in r["dimensions"] if isinstance(d, dict)}
    for did in sorted(set(measured["dimensions"]) - set(dims)):
        errs.append(f"dimension {did} is measured by the scorer but missing from the receipt")
    for did, d in sorted(dims.items(), key=lambda kv: str(kv[0])):
        m = measured["dimensions"].get(did)
        if not isinstance(m, dict):
            errs.append(f"dimension {did} is not produced by the evidence scorer")
            continue
        for part, keys in (("baseline", ("score", "ci_low", "ci_high")),
                           ("candidate", ("score", "ci_low", "ci_high")),
                           ("delta", ("value", "ci_low", "ci_high"))):
            for key in keys:
                said = (d.get(part) or {}).get(key) if isinstance(d.get(part), dict) else None
                got = (m.get(part) or {}).get(key) if isinstance(m.get(part), dict) else None
                if not _same(said, got):
                    errs.append(f"dimension {did} {part}.{key}: receipt says {said!r}, recomputed from the "
                                f"cohorts it is {got!r}")
    controls = measured.get("controls")
    validation = r["grader"].get("validation", {}) if isinstance(r.get("grader"), dict) else {}
    for key in CONTROLS:
        got = controls.get(key) if isinstance(controls, dict) else None
        if not _same(validation.get(key), got):
            errs.append(f"grader.validation.{key}: receipt says {validation.get(key)!r}, recomputed it is {got!r}")
    return errs


# Only immutable baseline results are reusable. Serialized values keep callers
# from mutating the cache; no receipt-provided manifest participates in its key.
_BASELINE_REPLAYS: dict[tuple[str, str], str] = {}


def _observe_rule_delivery(tree: Path, tmp: Path, arm: str) -> dict:
    harness = "evals/rule-delivery/run_eval.py"
    separately_bound = {harness, "evals/rule-delivery/make_report.py",
                        "evals/rule-delivery/expectations.v1.json"}
    obs, trace = tmp / f"{arm}.jsonl", tmp / f"{arm}.reads.json"
    env = dict(os.environ, PYTHONPYCACHEPREFIX=str(tmp / f"{arm}-cache"))
    subprocess.run([sys.executable, "-B", str(tree / harness), "--observe", str(obs),
                    "--trace-reads", str(trace)], cwd=tree, env=env,
                   capture_output=True, check=True, timeout=120)
    reads = set(json.loads(trace.read_text())) - separately_bound
    reads = {p for p in reads if "__pycache__" not in p.split("/")}
    return {"rows": [json.loads(line) for line in obs.read_text().splitlines()],
            "dependencies": {p: hashlib.sha256((tree / p).read_bytes()).hexdigest()
                             for p in sorted(reads)}}


def replay_rule_delivery(root: Path, baseline_ref: str) -> dict:
    """Authenticate observations, reusing only immutable baseline work.

    Resolve the commit in the caller's repository on every call. The baseline
    cache binds that commit and the candidate harness bytes, lasts only for this
    process, and never caches errors. Candidate source always executes afresh.
    Each subprocess gets an empty bytecode cache namespace and its read trace
    establishes the complete measured set independently of receipt manifests.
    """
    harness = "evals/rule-delivery/run_eval.py"
    code = (root / harness).read_bytes()
    commit = subprocess.run(["git", "-C", str(root), "rev-parse", "--verify",
                             baseline_ref + "^{commit}"], capture_output=True,
                            text=True, check=True).stdout.strip()
    key = (commit, hashlib.sha256(code).hexdigest())
    with tempfile.TemporaryDirectory(prefix="eval-receipt-replay-") as scratch:
        tmp = Path(scratch)
        if key not in _BASELINE_REPLAYS:
            archive = subprocess.run(["git", "-C", str(root), "archive", "--format=tar", commit],
                                     capture_output=True, check=True).stdout
            baseline = tmp / "baseline"
            with tarfile.open(fileobj=io.BytesIO(archive)) as tar:
                tar.extractall(baseline, filter="data")
            (baseline / harness).write_bytes(code)
            result = _observe_rule_delivery(baseline, tmp, "baseline")
            if len(_BASELINE_REPLAYS) >= 4:
                del _BASELINE_REPLAYS[next(iter(_BASELINE_REPLAYS))]
            _BASELINE_REPLAYS[key] = json.dumps(result)
        return {"baseline": json.loads(_BASELINE_REPLAYS[key]),
                "candidate": _observe_rule_delivery(root, tmp, "candidate")}


def _rule_delivery_replay_errors(ev: dict, root: Path, cohorts: dict) -> list[str]:
    baseline = ev.get("baseline")
    if (not isinstance(baseline, dict) or set(baseline) != {"ref", "dependencies"}
            or not isinstance(baseline.get("ref"), str)
            or not re.fullmatch(r"[0-9a-f]{40}", baseline["ref"])
            or not isinstance(baseline.get("dependencies"), dict)):
        return ["evidence.baseline must bind an immutable 40-hex Git ref and its complete replay dependencies"]
    required_source = {"evals/rule-delivery/run_eval.py", "evals/rule-delivery/make_report.py"}
    if set(ev["source"]) != required_source or ev["scorer"] != {
            "path": "evals/rule-delivery/run_eval.py", "function": "score_receipt"}:
        return ["rule-delivery replay requires its canonical harness, producer and scorer"]
    try:
        fresh = replay_rule_delivery(root, baseline["ref"])
    except Exception as exc:
        return [f"rule-delivery replay refused: {type(exc).__name__}: {exc}"]
    errs = []
    for arm in ARMS:
        bound = ev["dependencies"] if arm == "candidate" else baseline["dependencies"]
        if fresh[arm]["dependencies"] != bound:
            errs.append(f"evidence.{arm}: complete replay dependency manifest differs from measured source")
        if fresh[arm]["rows"] != sorted(cohorts[arm], key=lambda row: row["case_id"]):
            errs.append(f"evidence.cohorts.{arm}: observations differ from the authenticated source replay")
    return errs


def evidence_errors(r: dict, surface: str, root: Path = ROOT, base: str | None = None) -> list[str]:
    """Re-derive the receipt from the files it binds; any disagreement is a refusal."""
    ev = r["evidence"]
    fields = EVIDENCE_FIELDS | ({"baseline"} if surface == "rule-delivery" else set())
    if not isinstance(ev, dict) or set(ev) != fields:
        return [f"evidence must carry exactly {sorted(fields)}"]
    errs: list[str] = []
    home = f"evals/{surface}/"
    source = _manifest(root, ev["source"], "source", errs)
    _manifest(root, ev["dependencies"], "dependencies", errs)

    scorer = ev["scorer"]
    if (not isinstance(scorer, dict) or set(scorer) != {"path", "function"}
            or not isinstance(scorer.get("function"), str) or not isinstance(scorer.get("path"), str)):
        errs.append("evidence.scorer must be exactly {path, function}")
        scorer = None
    elif scorer["path"] not in source:
        errs.append(f"evidence.scorer {scorer['path']} must be bound in evidence.source, or nothing pins what scored")
        scorer = None

    cases: dict[str, Any] | None = None
    expectations = None
    x = ev["expectations"]
    if not isinstance(x, dict) or set(x) != {"path", "version", "sha256"} or not isinstance(x.get("version"), str):
        errs.append("evidence.expectations must be exactly {path, version, sha256}")
    else:
        data = _bound_bytes(root, x["path"], x["sha256"], "evidence.expectations", errs, under=home)
        if data is not None:
            errs += _expectations_version_errors(root, x["path"], x["version"], data, base)
            try:
                expectations = json.loads(data)
            except json.JSONDecodeError as exc:
                errs.append(f"evidence.expectations: {x['path']} is not JSON: {exc}")
            if expectations is not None:
                cases = _expectation_cases(expectations, x["version"], errs)

    cohorts: dict[str, list[dict]] = {}
    c = ev["cohorts"]
    if not isinstance(c, dict) or set(c) != set(ARMS):
        errs.append(f"evidence.cohorts must be exactly {list(ARMS)}: the two arms of one paired comparison")
    else:
        for arm in ARMS:
            block = c[arm]
            if not isinstance(block, dict) or set(block) != {"path", "sha256"}:
                errs.append(f"evidence.cohorts.{arm} must be exactly {{path, sha256}}")
                continue
            data = _bound_bytes(root, block["path"], block["sha256"], f"evidence.cohorts.{arm}", errs, under=home)
            if data is not None and cases is not None:
                rows = _cohort(data, arm, cases, errs)
                if rows is not None:
                    cohorts[arm] = rows

    if cases is not None and isinstance(r.get("cases"), dict):
        counts = {"total": len(cases),
                  "train": sum(1 for v in cases.values() if v["split"] == "train"),
                  "test": sum(1 for v in cases.values() if v["split"] == "test"),
                  "should_not_fire": sum(1 for v in cases.values() if v["should_not_fire"])}
        for key, n in counts.items():
            if r["cases"].get(key) != n:
                errs.append(f"cases.{key} says {r['cases'].get(key)!r}; the bound expectations hold {n}")

    if errs or scorer is None or len(cohorts) != len(ARMS):
        return errs
    try:
        path = root / scorer["path"]
        code = _bound_bytes(root, scorer["path"], source[scorer["path"]], "evidence.scorer", errs)
        if code is None:
            return errs
        module = types.ModuleType(f"eval_scorer_{source[scorer['path']][:16]}")
        module.__file__ = str(path)
        exec(compile(code, str(path), "exec"), module.__dict__)
        measured = getattr(module, scorer["function"])(expectations, cohorts["baseline"], cohorts["candidate"])
    except Exception as exc:  # the scorer refusing its own evidence is a finding, not a crash
        return [f"evidence.scorer {scorer['path']}:{scorer['function']} refused the evidence: "
                f"{type(exc).__name__}: {exc}"]
    errs = _recompute_errors(r, measured)
    if surface == "rule-delivery":
        errs += _rule_delivery_replay_errors(ev, root, cohorts)
    return errs


def _expectation_cases(doc: Any, version: str, errs: list[str]) -> dict[str, Any] | None:
    if not isinstance(doc, dict) or doc.get("version") != version:
        errs.append(f"evidence.expectations: the file's version is "
                    f"{doc.get('version') if isinstance(doc, dict) else None!r}, the receipt binds {version!r}")
        return None
    cases = doc.get("cases")
    if not isinstance(cases, dict) or not cases:
        errs.append("evidence.expectations must label at least one case under cases")
        return None
    for cid, case in cases.items():
        if (not isinstance(case, dict) or case.get("split") not in ("train", "test")
                or not isinstance(case.get("should_not_fire"), bool)
                or not isinstance(case.get("input_sha256"), str) or not HEX64.match(case["input_sha256"])):
            errs.append(f"evidence.expectations: case {cid} needs split (train|test), should_not_fire (bool) "
                        f"and input_sha256")
            return None
    return cases


# ------------------------------------------------------------------ PR body
def parse_no_eval(body: str | None, known: set[str]) -> tuple[dict[str, str], list[str]]:
    found: dict[str, str] = {}
    errs: list[str] = []
    text = re.sub(r"<!--.*?-->", "", body or "", flags=re.S)
    fence: tuple[str, int] | None = None
    for line in text.replace("\r\n", "\n").split("\n"):
        marker = re.match(r"^ {0,3}(`{3,}|~{3,})(.*)$", line)
        if fence:
            if marker and marker.group(1)[0] == fence[0] and len(marker.group(1)) >= fence[1] and not marker.group(2).strip():
                fence = None
            continue
        if marker:
            fence = (marker.group(1)[0], len(marker.group(1)))
            continue
        m = NO_EVAL.match(line)
        if not m:
            continue
        surface, reason = m.group(1), m.group(2).strip()
        if surface not in known:
            errs.append(f"no-eval line names unknown surface {surface!r}; registered: {', '.join(sorted(known))}")
            continue
        if PLACEHOLDER_REASONS.match(reason) or len(reason.split()) < MIN_REASON_WORDS:
            errs.append(f"no-eval line for {surface} gives no reason a measurement is impossible "
                        f"(need at least {MIN_REASON_WORDS} words): {reason!r}")
            continue
        found[surface] = reason
    return found, errs


# ------------------------------------------------------------------ git
def git(root: Path, *args: str) -> tuple[int, str]:
    p = subprocess.run(["git", "-C", str(root), *args], capture_output=True, text=True)
    return p.returncode, (p.stdout or "").strip()


def merge_base(root: Path, base: str) -> str | None:
    rc, mb = git(root, "merge-base", "HEAD", base)
    return mb if rc == 0 and mb else None


def changed_paths(root: Path, base: str) -> list[str] | None:
    mb = merge_base(root, base)
    if mb is None:
        return None
    rc, out = git(root, "diff", "--name-only", f"{mb}..HEAD")
    return None if rc != 0 else [p for p in out.splitlines() if p]


def base_registry(root: Path, mb: str) -> dict[str, Any] | None:
    """The registry at the merge base, or None when the base predates it."""
    rel = "evals/surfaces.json"
    rc, _ = git(root, "cat-file", "-e", f"{mb}:{rel}")
    if rc != 0:
        return None
    rc, text = git(root, "show", f"{mb}:{rel}")
    if rc != 0:
        raise GateError(f"cannot read {rel} at the merge base {mb}")
    try:
        return validate_registry(json.loads(text))
    except json.JSONDecodeError as exc:
        raise GateError(f"cannot read {rel} at the merge base {mb}: {exc}") from exc


def pr_body_from_event() -> tuple[bool, str]:
    if os.environ.get("GITHUB_EVENT_NAME") != "pull_request":
        return False, ""
    try:
        event = json.loads(Path(os.environ["GITHUB_EVENT_PATH"]).read_text())
    except (KeyError, OSError, json.JSONDecodeError) as exc:
        raise GateError(f"cannot read pull-request event: {exc}") from exc
    if not isinstance(event, dict) or not isinstance(event.get("pull_request"), dict):
        raise GateError("pull-request event has no pull_request object")
    body = event["pull_request"].get("body")
    if body is not None and not isinstance(body, str):
        raise GateError("pull-request event body must be a string or null")
    return True, body or ""


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--root", default=str(ROOT))
    ap.add_argument("--base", default=os.environ.get("CARR_EVAL_RECEIPT_BASE", "origin/main"))
    ap.add_argument("--pr-body-file", help="enforce as a pull request, reading the body from this file")
    args = ap.parse_args(argv)
    root = Path(args.root).resolve()

    try:
        reg = load_registry(root / "evals" / "surfaces.json")
    except GateError as exc:
        print(f"check-eval-receipt: {exc}", file=sys.stderr)
        return 2
    known = {s["id"] for s in reg["surfaces"]}
    try:
        if args.pr_body_file:
            in_pr, body = True, Path(args.pr_body_file).read_text()
        else:
            in_pr, body = pr_body_from_event()
    except (GateError, OSError) as exc:
        print(f"check-eval-receipt: {exc}", file=sys.stderr)
        return 2

    failures: list[str] = []
    advisories: list[str] = []
    for hook in unregistered_context_hooks(root, reg):
        failures.append(f"{hook} emits context into a session but no surface in evals/surfaces.json names it")

    changed = changed_paths(root, args.base)
    if changed is None:
        msg = f"cannot compute the change set against {args.base}"
        if in_pr:
            print(f"check-eval-receipt: {msg}", file=sys.stderr)
            return 2
        print(f"check-eval-receipt: {msg}; nothing to judge here")
        changed = []

    judged = reg
    mb = merge_base(root, args.base) if changed else None
    if mb is not None:
        try:
            judged = enforced_registry(base_registry(root, mb), reg)
        except GateError as exc:
            print(f"check-eval-receipt: {exc}", file=sys.stderr)
            return 2
    known |= {s["id"] for s in judged["surfaces"]}

    touched: dict[str, list[str]] = {}
    for path in changed:
        for sid in surfaces_for(path, judged):
            touched.setdefault(sid, []).append(path)
    receipts_changed = {p.split("/")[1] for p in changed
                        if re.fullmatch(r"evals/[^/]+/receipt\.json", p)}
    no_eval, line_errs = parse_no_eval(body, known) if in_pr else ({}, [])
    failures += line_errs

    for sid in sorted(receipts_changed):
        rel = f"evals/{sid}/receipt.json"
        if sid not in known:
            failures.append(f"{rel}: {sid} is not a registered surface")
            continue
        try:
            receipt = json.loads((root / rel).read_text())
        except FileNotFoundError:
            continue  # a deleted receipt is not a receipt
        except (OSError, json.JSONDecodeError) as exc:
            failures.append(f"{rel}: unreadable: {exc}")
            continue
        failures += [f"{rel}: {e}" for e in validate_receipt(receipt, sid, root, mb)]
        verdict = receipt.get("verdict") if isinstance(receipt, dict) else None
        decision = verdict.get("decision") if isinstance(verdict, dict) else None
        if sid in touched and decision in ("do_not_merge", "inconclusive"):
            failures.append(f"{rel}: verdict {decision} does not authorize shipping the changed {sid} surface")

    for sid, paths in sorted(touched.items()):
        shown = ", ".join(paths[:4]) + (f" (+{len(paths) - 4} more)" if len(paths) > 4 else "")
        if sid in receipts_changed and (root / f"evals/{sid}/receipt.json").exists():
            print(f"  receipt   {sid}: {shown}")
        elif sid in no_eval:
            print(f"  no-eval   {sid}: {no_eval[sid]}")
        else:
            need = (f"{sid} changed ({shown}) with no evals/{sid}/receipt.json in this change and no "
                    f"'no-eval: {sid}: <reason>' line in the pull-request body. Run /claude-api build-eval "
                    f"then /claude-api hillclimb (evals/README.md).")
            (failures if in_pr else advisories).append(need)

    for a in advisories:
        print(f"  advisory  {a} (not a pull-request run; CI enforces this on the pull request)")
    if failures:
        for f in failures:
            print(f"  FAIL      {f}", file=sys.stderr)
        return 1
    print(f"check-eval-receipt: OK ({len(touched)} surface(s) touched, {len(receipts_changed)} receipt(s) checked)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
