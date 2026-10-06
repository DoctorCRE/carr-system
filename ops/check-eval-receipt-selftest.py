#!/usr/bin/env python3
"""Selftest for ops/check-eval-receipt.py, the LLM-steering eval-receipt gate.

Written before the check. Half of these cases MUST FAIL: a receipt whose gain
sits inside the noise and still says "ship", a critical dimension that regressed
while the headline rose, a grader nobody graded twice, a test split that was
not sealed, a no-eval line that gives no reason. A gate that only ever passes
its fixtures proves nothing about what it refuses.

The evidence chain has its own refusals, run against the real rule-delivery
receipt: deleting a failing candidate row, shrinking the owed-rule denominator,
changing result bytes and reusing a stale summary each fail the check.
"""

from __future__ import annotations

import copy
from functools import lru_cache
import hashlib
import importlib.util
import json
import os
import py_compile
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "ops"))
from git_env import fixture_env  # noqa: E402

SPEC = importlib.util.spec_from_file_location("check_eval_receipt", ROOT / "ops" / "check-eval-receipt.py")
assert SPEC and SPEC.loader
cer = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(cer)

REGISTRY = cer.load_registry(ROOT / "evals" / "surfaces.json")
SURFACE_IDS = {s["id"] for s in REGISTRY["surfaces"]}


def score(s, lo, hi):
    return {"score": s, "ci_low": lo, "ci_high": hi}


def dimension(dim_id, *, critical, base, cand, delta, direction, status="passed"):
    return {
        "dimension_id": dim_id,
        "critical": critical,
        "status": status,
        "direction_vs_baseline": direction,
        "evidence_refs": [f"evals/jev-judgments/runs/v3/{dim_id}.jsonl"],
        "baseline": score(*base),
        "candidate": score(*cand),
        "delta": {"value": delta[0], "ci_low": delta[1], "ci_high": delta[2]},
    }


def good_receipt():
    """A clear, attributable win on the primary dimension, nothing critical lost.

    Its numbers are hand-written, so only claim_errors() may judge it; a receipt
    that has to pass the whole gate comes from evidenced_receipt()."""
    return {
        "schema_version": 2,
        "surface": "jev-judgments",
        "change": "State the refusal condition once, before the examples, in the done-claim question family.",
        "measured_on": "2026-09-29",
        "rung": "hill_climb",
        "adapter": {
            "surface": "claude_code_cli",
            "adapter_id": "adapter:claude-api-hillclimb",
            "adapter_version": "2.1.284",
            "harness_id": "harness:ops-jev-judge",
            "harness_version": "c1fb71b",
            "provider_id": "provider:anthropic",
            "model_id": "model:claude-sonnet-5-5",
            "native_session_ref": "session:hillclimb-jev-v3",
            "configuration_fingerprint": "sha256:" + "a" * 64,
        },
        "cases": {"total": 80, "train": 40, "test": 40, "should_not_fire": 16,
                  "sources": ["production_trace", "human_judged_hard_case"]},
        "split": {"method": "random, stratified by first tag", "seed": 11, "sealed_test": True},
        "repeats": 3,
        "grader": {"kind": "programmatic",
                   "validation": {"graded_twice": True, "agreement": 0.98, "oracle_pass_rate": 1.0,
                                  "null_pass_rate": 0.0, "result": "pass"}},
        "noise_floor": 0.06,
        "min_useful_gain": 0.10,
        "primary_dimension": "correct-judgment",
        "dimensions": [
            dimension("correct-judgment", critical=True, base=(0.62, 0.55, 0.69), cand=(0.81, 0.75, 0.87),
                      delta=(0.19, 0.12, 0.26), direction="improved"),
            dimension("should-not-fire-precision", critical=True, base=(0.94, 0.89, 0.98), cand=(0.95, 0.90, 0.99),
                      delta=(0.01, -0.03, 0.05), direction="equivalent"),
        ],
        "stage_results": [
            {"stage_id": "intake", "status": "passed", "dimension_ids": ["should-not-fire-precision"],
             "evidence_refs": ["evals/jev-judgments/runs/v3/intake.jsonl"]},
            {"stage_id": "judgment", "status": "passed", "dimension_ids": ["correct-judgment"],
             "evidence_refs": ["evals/jev-judgments/runs/v3/judgment.jsonl"]},
        ],
        "cost": {"baseline_usd_per_case": 0.0041, "candidate_usd_per_case": 0.0043},
        "verdict": {"decision": "ship", "statement": "Test-split judgment accuracy rose 0.62 to 0.81, outside noise."},
        "evidence": {},
    }


# ------------------------------------------------------------------ evidence
def sha_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha_file(path: Path) -> str:
    return sha_bytes(path.read_bytes())


SYNTH_SCORER = '''\
import random


def _interval(draws):
    draws = sorted(draws)
    return draws[int(0.025 * len(draws))], draws[int(0.975 * len(draws)) - 1]


def score(expectations, baseline, candidate):
    ids = sorted(cid for cid, case in expectations["cases"].items() if case["split"] == "test")
    b = {r["case_id"]: r for r in baseline}
    n = {r["case_id"]: r for r in candidate}
    out = {}
    for dim in expectations["dimensions"]:
        rng = random.Random(7)
        picks = [[rng.choice(ids) for _ in ids] for _ in range(400)]

        def mean(side, pick):
            return sum(side[i]["scores"][dim] for i in pick) / len(pick)
        blo, bhi = _interval([mean(b, p) for p in picks])
        clo, chi = _interval([mean(n, p) for p in picks])
        dlo, dhi = _interval([mean(n, p) - mean(b, p) for p in picks])
        out[dim] = {"baseline": {"score": mean(b, ids), "ci_low": blo, "ci_high": bhi},
                    "candidate": {"score": mean(n, ids), "ci_low": clo, "ci_high": chi},
                    "delta": {"value": mean(n, ids) - mean(b, ids), "ci_low": dlo, "ci_high": dhi}}
    return {"dimensions": out, "controls": {"oracle_pass_rate": 1.0, "null_pass_rate": 0.0}}
'''


def write_jsonl(path: Path, rows: list[dict]) -> str:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(r, sort_keys=True) + "\n" for r in rows))
    return sha_file(path)


def evidenced_receipt(root: Path, surface: str, *, judgment=(25, 33), precision=(36, 36)) -> dict:
    """good_receipt() backed by a real evidence chain written under root.

    80 cases (40 train, 40 test, 16 should-not-fire); on the test split the
    baseline gets judgment[0] cases right and the candidate judgment[1], and the
    same for precision. Every measured number comes from the synthetic scorer."""
    edir = root / "evals" / surface
    edir.mkdir(parents=True, exist_ok=True)
    (edir / "score.py").write_text(SYNTH_SCORER)
    dims = ["correct-judgment", "should-not-fire-precision"]
    cases: dict[str, dict] = {}
    base_rows: list[dict] = []
    cand_rows: list[dict] = []
    for split, prefix in (("train", "r"), ("test", "t")):
        for i in range(40):
            cid = f"{prefix}{i:02d}"
            inp = sha_bytes(cid.encode())
            cases[cid] = {"split": split, "should_not_fire": i < 8, "input_sha256": inp}
            for rows, (right, kept) in ((base_rows, (judgment[0], precision[0])),
                                        (cand_rows, (judgment[1], precision[1]))):
                on_test = split == "test"
                rows.append({"case_id": cid, "split": split, "input_sha256": inp,
                             "scores": {dims[0]: int(on_test and i < right),
                                        dims[1]: int(on_test and i < kept)}})
    expectations = {"version": "synthetic-expectations/v1", "dimensions": dims, "cases": cases}
    exp_path = edir / "expectations.v1.json"
    exp_path.write_text(json.dumps(expectations, indent=1, sort_keys=True) + "\n")
    evidence = {
        "scorer": {"path": f"evals/{surface}/score.py", "function": "score"},
        "source": {f"evals/{surface}/score.py": sha_file(edir / "score.py")},
        "dependencies": {"evals/surfaces.json": sha_file(root / "evals" / "surfaces.json")},
        "expectations": {"path": f"evals/{surface}/expectations.v1.json",
                         "version": "synthetic-expectations/v1", "sha256": sha_file(exp_path)},
        "cohorts": {"baseline": {"path": f"evals/{surface}/baseline.jsonl",
                                 "sha256": write_jsonl(edir / "baseline.jsonl", base_rows)},
                    "candidate": {"path": f"evals/{surface}/candidate.jsonl",
                                  "sha256": write_jsonl(edir / "candidate.jsonl", cand_rows)}},
    }
    spec = importlib.util.spec_from_file_location(f"synth_{surface}", edir / "score.py")
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    measured = mod.score(expectations, base_rows, cand_rows)["dimensions"]
    r = good_receipt()
    r["surface"] = surface
    r["evidence"] = evidence
    for d in r["dimensions"]:
        m = measured[d["dimension_id"]]
        d.update(baseline=m["baseline"], candidate=m["candidate"], delta=m["delta"],
                 direction_vs_baseline=cer._direction(m["delta"]))
    return r


RD = "evals/rule-delivery"


def evidence_paths(receipt: dict) -> set[str]:
    ev = receipt["evidence"]
    return (set(ev["source"]) | set(ev["dependencies"]) | {ev["expectations"]["path"]}
            | {c["path"] for c in ev["cohorts"].values()})


@lru_cache(maxsize=128)
def historical_dependency(root: Path, rel: str, expected: str) -> bytes:
    """Find the immutable bytes by digest, including fetched merge parents."""
    env = fixture_env()
    revisions = subprocess.check_output(
        ["git", "log", "--all", "--format=%H", "--", rel], cwd=root, env=env, text=True)
    for rev in revisions.splitlines():
        result = subprocess.run(["git", "show", rev + ":" + rel], cwd=root, env=env,
                                capture_output=True)
        if result.returncode == 0 and hashlib.sha256(result.stdout).hexdigest() == expected:
            return result.stdout
    raise AssertionError("historical receipt source binding unavailable: " + rel)


def mirror(receipt: dict, dest: Path) -> None:
    """Copy the receipt's bound bytes, including pending and historical evidence."""
    ev = receipt['evidence']
    hashes = {**ev['source'], **ev['dependencies'],
              ev['expectations']['path']: ev['expectations']['sha256'],
              **{c['path']: c['sha256'] for c in ev['cohorts'].values()}}
    for rel in evidence_paths(receipt) | {"evals/surfaces.json"}:
        (dest / rel).parent.mkdir(parents=True, exist_ok=True)
        data = (ROOT / rel).read_bytes()
        expected = hashes.get(rel)
        if expected is not None and hashlib.sha256(data).hexdigest() != expected:
            data = historical_dependency(ROOT, rel, expected)
        (dest / rel).write_bytes(data)
    if not (dest / ".git").exists():
        env = fixture_env()
        subprocess.run(["git", "init", "-q", str(dest)], env=env, check=True, capture_output=True)
        common = subprocess.run(["git", "rev-parse", "--path-format=absolute", "--git-common-dir"],
                                cwd=ROOT, env=env, check=True, capture_output=True, text=True).stdout.strip()
        (dest / ".git" / "objects" / "info" / "alternates").write_text(common + "/objects\n")
    (dest / RD / "receipt.json").write_text(json.dumps(receipt, indent=1, sort_keys=True) + "\n")


def read_jsonl(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


def load_scorer(root: Path, receipt: dict):
    sc = receipt["evidence"]["scorer"]
    spec = importlib.util.spec_from_file_location(f"scorer_{abs(hash(str(root)))}", root / sc["path"])
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return getattr(mod, sc["function"])


def failing_candidate_case(root: Path, receipt: dict) -> tuple[str, str]:
    """A test-split candidate case that misses an owed rule, and one rule it misses."""
    ev = receipt["evidence"]
    exp = json.loads((root / ev["expectations"]["path"]).read_text())["cases"]
    for row in read_jsonl(root / ev["cohorts"]["candidate"]["path"]):
        case = exp[row["case_id"]]
        missed = sorted(set(case["expected"]) - set(row["delivered"]))
        if case["split"] == "test" and missed:
            return row["case_id"], missed[0]
    raise AssertionError("no failing candidate case to tamper with")


def in_noise_receipt():
    r = good_receipt()
    r["dimensions"][0] = dimension("correct-judgment", critical=True, base=(0.62, 0.55, 0.69),
                                   cand=(0.65, 0.58, 0.72), delta=(0.03, -0.03, 0.09), direction="equivalent")
    return r


class Registry(unittest.TestCase):
    def test_real_registry_names_every_family_the_task_listed(self):
        must_cover = [
            "ops/jev_judge.py", "ops/typesafe_client.py", "mcp-server/src/jev-call-receipt.js",
            "ops/rule_trigger_delivery.py", "lib/rule_delivery_preuse.py",
            "hooks/session-brief.py", "hooks/rule-pack-preuse-reselection.py",
            "CLAUDE.md", "AGENTS.md", ".claude/skills/any/SKILL.md", "claude-tree/skills/council/SKILL.md",
            "pipelines/run_codex_review.py", "ops/config/model-routes.v1.json",
        ]
        for path in must_cover:
            self.assertTrue(cer.surfaces_for(path, REGISTRY), f"{path} is not registered")

    def test_tests_and_fixtures_are_not_surfaces(self):
        for path in ["ops/jev-judge-selftest.py", "tools/test-flash-prompt-rules.py",
                     "ops/fixtures/rule-delivery-eval/cases.v2.json", "hooks/__pycache__/x.pyc",
                     "evals/README.md", "evals/surfaces.json", "evals/jev-judgments/receipt.json"]:
            self.assertEqual(cer.surfaces_for(path, REGISTRY), [], path)

    def test_every_literal_path_exists_and_every_surface_matches_something(self):
        tracked = subprocess.run(["git", "-C", str(ROOT), "ls-files"], capture_output=True, text=True).stdout.split()
        for surface in REGISTRY["surfaces"]:
            for glob in surface["globs"]:
                if not any(ch in glob for ch in "*?["):
                    self.assertTrue((ROOT / glob).exists(), f"{surface['id']}: {glob} does not exist")
            self.assertTrue(any(surface["id"] in cer.surfaces_for(p, REGISTRY) for p in tracked),
                            f"{surface['id']} matches no tracked file")

    def test_every_context_emitting_hook_is_registered(self):
        self.assertEqual(cer.unregistered_context_hooks(ROOT, REGISTRY), [])

    def test_a_new_context_hook_is_caught(self):
        with tempfile.TemporaryDirectory() as tmp:
            (Path(tmp) / "hooks").mkdir()
            (Path(tmp) / "hooks" / "new-nudge.py").write_text('print({"additionalContext": "x"})\n')
            self.assertEqual(cer.unregistered_context_hooks(Path(tmp), REGISTRY), ["hooks/new-nudge.py"])

    def test_malformed_registries_fail(self):
        base = json.loads((ROOT / "evals" / "surfaces.json").read_text())
        for mutate, why in [
            (lambda d: d["surfaces"].append(copy.deepcopy(d["surfaces"][0])), "duplicate"),
            (lambda d: d["surfaces"][0].update(id="Bad Id"), "id"),
            (lambda d: d["surfaces"][0].update(globs=[]), "globs"),
        ]:
            d = copy.deepcopy(base)
            mutate(d)
            with self.assertRaisesRegex(cer.GateError, why):
                cer.validate_registry(d)


class Globs(unittest.TestCase):
    def test_double_star_crosses_directories_single_star_does_not(self):
        self.assertTrue(cer.glob_match("a/b/c/CLAUDE.md", "**/CLAUDE.md"))
        self.assertTrue(cer.glob_match("CLAUDE.md", "**/CLAUDE.md"))
        self.assertTrue(cer.glob_match(".claude/skills/x/y/SKILL.md", ".claude/skills/**"))
        self.assertTrue(cer.glob_match("ops/jev_judge.py", "ops/jev_*.py"))
        self.assertFalse(cer.glob_match("ops/sub/jev_judge.py", "ops/jev_*.py"))
        self.assertFalse(cer.glob_match("ops/jev-judge-selftest.py", "ops/jev_*.py"))


class Receipts(unittest.TestCase):
    def errors(self, receipt, surface="jev-judgments"):
        return cer.claim_errors(receipt, surface, ROOT)

    def test_clear_win_passes(self):
        self.assertEqual(self.errors(good_receipt()), [])

    def test_in_noise_gain_that_says_ship_fails(self):
        errs = self.errors(in_noise_receipt())
        self.assertTrue(any("do not merge on quality grounds" in e for e in errs), errs)

    def test_in_noise_gain_that_says_do_not_merge_passes(self):
        r = in_noise_receipt()
        r["verdict"] = {"decision": "do_not_merge",
                        "statement": "Gain is inside the noise: do not merge on quality grounds."}
        self.assertEqual(self.errors(r), [])

    def test_cost_at_parity_is_allowed_only_when_cheaper(self):
        r = in_noise_receipt()
        r["verdict"] = {"decision": "ship_cost_at_parity",
                        "statement": "Quality within noise, do not merge on quality grounds; cheaper per case."}
        r["cost"] = {"baseline_usd_per_case": 0.0041, "candidate_usd_per_case": 0.0020}
        self.assertEqual(self.errors(r), [])
        r["cost"] = {"baseline_usd_per_case": 0.0041, "candidate_usd_per_case": 0.0050}
        self.assertTrue(any("cheaper" in e for e in self.errors(r)))

    def test_critical_regression_blocks_even_when_the_overall_score_rises(self):
        r = good_receipt()
        r["dimensions"][1] = dimension("should-not-fire-precision", critical=True, base=(0.94, 0.89, 0.98),
                                       cand=(0.80, 0.74, 0.86), delta=(-0.14, -0.20, -0.08), direction="regressed")
        r["overall"] = {"baseline": 0.70, "candidate": 0.82}
        errs = self.errors(r)
        self.assertTrue(any("should-not-fire-precision" in e and "blocking" in e for e in errs), errs)
        r["verdict"] = {"decision": "do_not_merge",
                        "statement": "Blocked: critical dimension should-not-fire-precision regressed."}
        self.assertEqual(self.errors(r), [])

    def test_a_regression_cannot_be_declared_equivalent(self):
        r = good_receipt()
        r["dimensions"][1] = dimension("should-not-fire-precision", critical=False, base=(0.94, 0.89, 0.98),
                                       cand=(0.80, 0.74, 0.86), delta=(-0.14, -0.20, -0.08), direction="equivalent")
        self.assertTrue(any("direction" in e for e in self.errors(r)))

    def test_noncritical_regression_is_reported_but_does_not_block(self):
        r = good_receipt()
        r["dimensions"].append(dimension("brevity", critical=False, base=(0.70, 0.64, 0.76),
                                         cand=(0.60, 0.54, 0.66), delta=(-0.10, -0.16, -0.04), direction="regressed"))
        r["stage_results"][1]["dimension_ids"].append("brevity")
        self.assertEqual(self.errors(r), [])

    def test_failed_critical_dimension_status_blocks(self):
        r = good_receipt()
        r["dimensions"][1]["status"] = "failed"
        self.assertTrue(any("blocking" in e for e in self.errors(r)))

    def test_primary_regression_must_say_do_not_merge(self):
        r = good_receipt()
        r["dimensions"][0] = dimension("correct-judgment", critical=False, base=(0.62, 0.55, 0.69),
                                       cand=(0.50, 0.43, 0.57), delta=(-0.12, -0.18, -0.06), direction="regressed")
        self.assertTrue(self.errors(r))

    def test_every_dimension_is_bound_to_a_user_job_stage(self):
        r = good_receipt()
        r["stage_results"] = r["stage_results"][1:]
        self.assertTrue(any("stage" in e for e in self.errors(r)))

    def test_stage_naming_an_unknown_dimension_fails(self):
        r = good_receipt()
        r["stage_results"][0]["dimension_ids"].append("ghost")
        self.assertTrue(any("ghost" in e for e in self.errors(r)))

    def test_no_dimensions_means_a_blended_score_and_fails(self):
        r = good_receipt()
        r["dimensions"] = []
        self.assertTrue(self.errors(r))

    def test_primary_dimension_must_exist(self):
        r = good_receipt()
        r["primary_dimension"] = "overall"
        self.assertTrue(any("primary_dimension" in e for e in self.errors(r)))

    def test_model_harness_and_adapter_are_required(self):
        for field in ["model_id", "harness_id", "adapter_id", "harness_version", "configuration_fingerprint"]:
            r = good_receipt()
            del r["adapter"][field]
            self.assertTrue(self.errors(r), field)
        r = good_receipt()
        r["adapter"]["model_id"] = " "
        self.assertTrue(self.errors(r))

    def test_rung_uses_the_kernel_ladder(self):
        r = good_receipt()
        r["rung"] = "vibes"
        self.assertTrue(any("rung" in e for e in self.errors(r)))
        r["rung"] = "launch"
        self.assertEqual(self.errors(r), [])

    def test_required_fields_each_fail_when_missing(self):
        for field in ["cases", "split", "repeats", "grader", "noise_floor", "min_useful_gain",
                      "dimensions", "stage_results", "cost", "verdict", "change", "adapter", "rung"]:
            r = good_receipt()
            del r[field]
            self.assertTrue(self.errors(r), f"missing {field} was accepted")

    def test_split_arithmetic_and_sealing(self):
        r = good_receipt(); r["cases"]["train"] = 41
        self.assertTrue(self.errors(r))
        r = good_receipt(); r["split"]["sealed_test"] = False
        self.assertTrue(any("sealed" in e for e in self.errors(r)))
        r = good_receipt(); r["cases"]["test"] = 0; r["cases"]["train"] = 80
        self.assertTrue(self.errors(r))

    def test_should_not_fire_and_real_sources_are_required(self):
        r = good_receipt(); r["cases"]["should_not_fire"] = 0
        self.assertTrue(any("should_not_fire" in e for e in self.errors(r)))
        r = good_receipt(); r["cases"]["should_not_fire"] = 99
        self.assertTrue(any("should_not_fire" in e and "total" in e for e in self.errors(r)))
        r = good_receipt(); r["cases"]["should_not_fire"] = r["cases"]["total"]
        self.assertEqual(self.errors(r), [])
        r = good_receipt(); r["cases"]["sources"] = ["synthesized"]
        self.assertTrue(any("production" in e for e in self.errors(r)))

    def test_grader_must_be_graded_twice_and_pass_validation(self):
        r = good_receipt(); r["grader"]["validation"]["graded_twice"] = False
        self.assertTrue(any("twice" in e for e in self.errors(r)))
        r = good_receipt(); r["grader"]["validation"]["result"] = "fail"
        self.assertTrue(any("grader" in e for e in self.errors(r)))
        r = good_receipt(); r["grader"]["kind"] = "vibes"
        self.assertTrue(self.errors(r))

    def test_noise_must_be_smaller_than_the_smallest_useful_gain_to_ship(self):
        r = good_receipt(); r["noise_floor"] = 0.12
        self.assertTrue(any("noise" in e for e in self.errors(r)))

    def test_score_outside_its_own_interval_fails(self):
        r = good_receipt(); r["dimensions"][0]["candidate"] = score(0.81, 0.83, 0.87)
        self.assertTrue(self.errors(r))

    def test_delta_must_match_the_scores(self):
        r = good_receipt(); r["dimensions"][0]["delta"]["value"] = 0.40
        self.assertTrue(any("delta" in e for e in self.errors(r)))

    def test_surface_mismatch_fails(self):
        self.assertTrue(any("surface" in e for e in self.errors(good_receipt(), "rule-delivery")))

    def test_repeats_must_be_positive(self):
        r = good_receipt(); r["repeats"] = 0
        self.assertTrue(self.errors(r))

    def test_offline_suite_binds_to_the_ai_eval_kernel_digest(self):
        suite_path = "evals/ai/model-boundary.v1.json"
        digest = cer.ai_eval.load_suite(ROOT / suite_path)["_digest"]
        r = good_receipt(); r["offline_suite"] = {"path": suite_path, "suite_digest": digest}
        self.assertEqual(self.errors(r), [])
        r["offline_suite"]["suite_digest"] = "0" * 64
        self.assertTrue(any("digest" in e for e in self.errors(r)))


class NoEvalLines(unittest.TestCase):
    REASON = ("the only consumer is Joe's local Flash seat and no transcript of it is retained, "
              "so there is nothing to replay")

    def test_valid_line_is_parsed(self):
        body = f"Summary\n\nno-eval: rule-delivery: {self.REASON}\n"
        found, errs = cer.parse_no_eval(body, SURFACE_IDS)
        self.assertEqual(errs, [])
        self.assertIn("rule-delivery", found)

    def test_unreasoned_lines_are_rejected(self):
        for reason in ["n/a", "trivial", "docs only", "not needed here", "tbd"]:
            found, errs = cer.parse_no_eval(f"no-eval: rule-delivery: {reason}", SURFACE_IDS)
            self.assertNotIn("rule-delivery", found, reason)
            self.assertTrue(errs, reason)

    def test_unknown_surface_is_an_error(self):
        found, errs = cer.parse_no_eval(f"no-eval: rules: {self.REASON}", SURFACE_IDS)
        self.assertEqual(found, {})
        self.assertTrue(any("rules" in e for e in errs))

    def test_lines_in_comments_and_code_fences_do_not_count(self):
        body = (f"<!--\nno-eval: rule-delivery: {self.REASON}\n-->\n"
                f"```\nno-eval: context-hooks: {self.REASON}\n```\n"
                f"~~~markdown\nno-eval: session-instructions: {self.REASON}\n~~~\n")
        found, errs = cer.parse_no_eval(body, SURFACE_IDS)
        self.assertEqual(found, {})

    def test_longer_fence_does_not_close_on_shorter_marker(self):
        body = (f"~~~~\n~~~\nno-eval: rule-delivery: {self.REASON}\n~~~~\n"
                f"no-eval: context-hooks: {self.REASON}\n")
        found, errs = cer.parse_no_eval(body, SURFACE_IDS)
        self.assertEqual(errs, [])
        self.assertEqual(set(found), {"context-hooks"})

    def test_quoted_and_indented_examples_are_not_exemptions(self):
        body = (f"> no-eval: rule-delivery: {self.REASON}\n"
                f"    no-eval: context-hooks: {self.REASON}\n"
                f"- no-eval: session-instructions: {self.REASON}\n")
        found, errs = cer.parse_no_eval(body, SURFACE_IDS)
        self.assertEqual(errs, [])
        self.assertEqual(set(found), {"session-instructions"})


class EndToEnd(unittest.TestCase):
    """A real throwaway repository, outside the checkout, git scrubbed."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.repo = Path(self.tmp.name) / "repo"
        self.repo.mkdir()
        self.env = fixture_env()
        self.git("init", "-q", "-b", "main")
        self.git("config", "user.email", "selftest@example.invalid")
        self.git("config", "user.name", "selftest")
        (self.repo / "evals").mkdir()
        (self.repo / "evals" / "surfaces.json").write_text((ROOT / "evals" / "surfaces.json").read_text())
        (self.repo / "hooks").mkdir()
        (self.repo / "AGENTS.md").write_text("boot\n")
        (self.repo / "README.md").write_text("readme\n")
        self.git("add", "-A"); self.git("commit", "-qm", "base")
        self.git("branch", "base")
        self.git("checkout", "-qb", "work")

    def tearDown(self):
        self.tmp.cleanup()

    def git(self, *args):
        subprocess.run(["git", *args], cwd=self.repo, env=self.env, check=True, capture_output=True)

    def run_check(self, body=None, pr=True, event_text=None, missing_event=False):
        args = [sys.executable, str(ROOT / "ops" / "check-eval-receipt.py"), "--root", str(self.repo), "--base", "base"]
        env = dict(self.env)
        env.pop("GITHUB_EVENT_NAME", None); env.pop("GITHUB_EVENT_PATH", None)
        if pr:
            event = self.repo.parent / "event.json"
            if not missing_event:
                event.write_text(event_text if event_text is not None else
                                 json.dumps({"pull_request": {"body": body}}))
            env.update(GITHUB_EVENT_NAME="pull_request", GITHUB_EVENT_PATH=str(event))
        return subprocess.run(args, cwd=self.repo, env=env, capture_output=True, text=True)

    def commit(self, path, text):
        p = self.repo / path
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text)
        self.git("add", "-A"); self.git("commit", "-qm", f"edit {path}")

    def test_unrelated_change_passes(self):
        self.commit("README.md", "changed\n")
        self.assertEqual(self.run_check("").returncode, 0)

    def test_surface_change_without_receipt_or_line_fails_in_a_pr(self):
        self.commit("AGENTS.md", "boot, changed\n")
        out = self.run_check("Just a tweak.")
        self.assertEqual(out.returncode, 1, out.stdout + out.stderr)
        self.assertIn("session-instructions", out.stdout + out.stderr)

    def _shrink_registry(self, mutate):
        reg = json.loads((self.repo / "evals" / "surfaces.json").read_text())
        mutate(reg)
        return json.dumps(reg, indent=2) + "\n"

    def test_dropping_a_glob_in_the_same_pr_does_not_exempt_the_file(self):
        def drop(reg):
            for s in reg["surfaces"]:
                s["globs"] = [g for g in s["globs"] if "AGENTS.md" not in g] or ["nowhere/never"]
        self.commit("evals/surfaces.json", self._shrink_registry(drop))
        self.commit("AGENTS.md", "boot, changed\n")
        out = self.run_check("Just a tweak.")
        self.assertEqual(out.returncode, 1, out.stdout + out.stderr)
        self.assertIn("session-instructions", out.stdout + out.stderr)

    def test_dropping_a_whole_surface_in_the_same_pr_does_not_exempt_it(self):
        def drop(reg):
            reg["surfaces"] = [s for s in reg["surfaces"] if s["id"] != "session-instructions"]
        self.commit("evals/surfaces.json", self._shrink_registry(drop))
        self.commit("AGENTS.md", "boot, changed\n")
        out = self.run_check("Just a tweak.")
        self.assertEqual(out.returncode, 1, out.stdout + out.stderr)
        self.assertIn("session-instructions", out.stdout + out.stderr)

    def test_adding_an_exclude_in_the_same_pr_does_not_exempt_the_file(self):
        self.commit("evals/surfaces.json",
                    self._shrink_registry(lambda reg: reg.setdefault("exclude_globs", []).append("AGENTS.md")))
        self.commit("AGENTS.md", "boot, changed\n")
        out = self.run_check("Just a tweak.")
        self.assertEqual(out.returncode, 1, out.stdout + out.stderr)
        self.assertIn("session-instructions", out.stdout + out.stderr)

    def test_a_surface_added_in_the_same_pr_is_enforced(self):
        def add(reg):
            reg["surfaces"].append({"id": "new-surface", "globs": ["README.md"]})
        self.commit("evals/surfaces.json", self._shrink_registry(add))
        self.commit("README.md", "changed\n")
        out = self.run_check("Just a tweak.")
        self.assertEqual(out.returncode, 1, out.stdout + out.stderr)
        self.assertIn("new-surface", out.stdout + out.stderr)

    def test_null_pr_body_is_treated_as_empty(self):
        self.commit("AGENTS.md", "boot, changed\n")
        self.assertEqual(self.run_check(None).returncode, 1)

    def test_unreadable_or_malformed_pr_event_fails_closed(self):
        self.commit("AGENTS.md", "boot, changed\n")
        for opts in ({"missing_event": True}, {"event_text": "{bad json"},
                     {"event_text": "{}"}, {"event_text": '{"pull_request": {"body": 12}}'}):
            out = self.run_check(**opts)
            self.assertEqual(out.returncode, 2, out.stdout + out.stderr)
            self.assertIn("event", out.stdout + out.stderr)

    def test_outside_a_pr_it_is_advisory(self):
        self.commit("AGENTS.md", "boot, changed\n")
        out = self.run_check(pr=False)
        self.assertEqual(out.returncode, 0, out.stdout + out.stderr)
        self.assertIn("session-instructions", out.stdout + out.stderr)

    def test_reasoned_no_eval_line_passes(self):
        self.commit("AGENTS.md", "boot, changed\n")
        out = self.run_check(f"no-eval: session-instructions: {NoEvalLines.REASON}")
        self.assertEqual(out.returncode, 0, out.stdout + out.stderr)

    def fails(self, out) -> list[str]:
        return [line for line in out.stderr.splitlines() if "FAIL" in line]

    def commit_receipt(self, r):
        self.commit("evals/session-instructions/receipt.json", json.dumps(r))

    def test_valid_receipt_changed_in_the_pr_passes(self):
        self.commit("AGENTS.md", "boot, changed\n")
        self.commit_receipt(evidenced_receipt(self.repo, "session-instructions"))
        out = self.run_check("")
        self.assertEqual(out.returncode, 0, out.stdout + out.stderr)

    def test_nonshipping_receipt_cannot_satisfy_a_changed_surface(self):
        for decision in ("do_not_merge", "inconclusive"):
            with self.subTest(decision=decision):
                r = evidenced_receipt(self.repo, "session-instructions")
                r["verdict"] = {"decision": decision, "statement": "Measured result does not authorize shipping."}
                self.commit("AGENTS.md", f"boot, changed for {decision}\n")
                self.commit_receipt(r)
                out = self.run_check(f"no-eval: session-instructions: {NoEvalLines.REASON}")
                self.assertEqual(out.returncode, 1, out.stdout + out.stderr)
                self.assertEqual(len(self.fails(out)), 1, out.stderr)
                self.assertIn(decision, out.stdout + out.stderr)

    def test_critical_regression_do_not_merge_receipt_blocks_pr(self):
        r = evidenced_receipt(self.repo, "session-instructions", precision=(36, 20))
        self.assertEqual(r["dimensions"][1]["direction_vs_baseline"], "regressed")
        r["verdict"] = {"decision": "do_not_merge",
                        "statement": "Blocked: critical dimension should-not-fire-precision regressed."}
        self.assertEqual(cer.validate_receipt(r, "session-instructions", self.repo), [])
        self.commit("AGENTS.md", "boot, changed\n")
        self.commit_receipt(r)
        out = self.run_check("")
        self.assertEqual(out.returncode, 1, out.stdout + out.stderr)
        self.assertEqual(len(self.fails(out)), 1, out.stderr)
        self.assertIn("do_not_merge", out.stdout + out.stderr)

    def test_stale_receipt_from_an_earlier_change_does_not_count(self):
        self.git("checkout", "-q", "--detach", "base")
        self.commit_receipt(evidenced_receipt(self.repo, "session-instructions"))
        self.git("branch", "-f", "base", "HEAD")
        self.git("checkout", "-qb", "work2")
        self.commit("AGENTS.md", "boot, changed again\n")
        self.assertEqual(self.run_check("").returncode, 1)

    def test_in_noise_receipt_saying_ship_fails(self):
        r = evidenced_receipt(self.repo, "session-instructions", judgment=(25, 26))
        self.assertEqual(r["dimensions"][0]["direction_vs_baseline"], "equivalent")
        self.commit("AGENTS.md", "boot, changed\n")
        self.commit_receipt(r)
        out = self.run_check("")
        self.assertEqual(out.returncode, 1)
        self.assertIn("do not merge on quality grounds", out.stdout + out.stderr)

    def test_hand_numbered_receipt_without_evidence_fails(self):
        self.commit("AGENTS.md", "boot, changed\n")
        self.commit_receipt(dict(good_receipt(), surface="session-instructions"))
        out = self.run_check("")
        self.assertEqual(out.returncode, 1, out.stdout + out.stderr)
        self.assertIn("evidence", out.stderr)

    def test_malformed_receipt_fails_even_when_its_surface_did_not_change(self):
        self.commit("evals/rule-delivery/receipt.json", "{not json")
        self.assertEqual(self.run_check("").returncode, 1)


class EvidenceChain(unittest.TestCase):
    """The generic chain on a small synthetic eval: hashes, pairing, counts, recompute."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        (self.root / "evals").mkdir()
        shutil.copy2(ROOT / "evals" / "surfaces.json", self.root / "evals" / "surfaces.json")
        self.r = evidenced_receipt(self.root, "jev-judgments")
        self.cand = self.root / "evals" / "jev-judgments" / "candidate.jsonl"

    def tearDown(self):
        self.tmp.cleanup()

    def errors(self, r=None):
        return cer.validate_receipt(self.r if r is None else r, "jev-judgments", self.root)

    def rebind(self, which="candidate"):
        c = self.r["evidence"]["cohorts"][which]
        c["sha256"] = sha_file(self.root / c["path"])

    def test_evidenced_receipt_passes(self):
        self.assertEqual(self.errors(), [])

    def test_schema_version_1_receipts_are_refused(self):
        r = copy.deepcopy(self.r); r["schema_version"] = 1
        self.assertTrue(any("schema_version" in e for e in self.errors(r)))

    def test_missing_evidence_fails(self):
        r = copy.deepcopy(self.r); del r["evidence"]
        self.assertTrue(any("evidence" in e for e in self.errors(r)))

    def test_duplicate_candidate_row_fails(self):
        rows = read_jsonl(self.cand)
        write_jsonl(self.cand, rows + rows[-1:])
        self.rebind()
        self.assertTrue(any("repeats case" in e for e in self.errors()), self.errors())

    def test_changed_candidate_input_fails(self):
        rows = read_jsonl(self.cand)
        rows[0]["input_sha256"] = "0" * 64
        write_jsonl(self.cand, rows)
        self.rebind()
        self.assertTrue(any("input" in e for e in self.errors()), self.errors())

    def test_scorer_must_be_bound_source(self):
        r = copy.deepcopy(self.r); r["evidence"]["source"] = {}
        self.assertTrue(any("scorer" in e for e in self.errors(r)))

    def test_dependencies_must_be_bound(self):
        r = copy.deepcopy(self.r); r["evidence"]["dependencies"] = {}
        self.assertTrue(any("dependencies" in e for e in self.errors(r)))

    def test_changed_dependency_fails(self):
        (self.root / "evals" / "surfaces.json").write_text("{}\n")
        self.assertTrue(any("evals/surfaces.json" in e and "sha256" in e for e in self.errors()))

    def test_paths_outside_the_repository_fail(self):
        r = copy.deepcopy(self.r)
        r["evidence"]["dependencies"]["../outside.json"] = "0" * 64
        self.assertTrue(any("repository-relative" in e for e in self.errors(r)))

    def test_dropping_a_measured_dimension_fails(self):
        r = copy.deepcopy(self.r)
        r["dimensions"] = r["dimensions"][:1]
        r["stage_results"] = r["stage_results"][1:]
        self.assertTrue(any("should-not-fire-precision" in e and "scorer" in e for e in self.errors(r)),
                        self.errors(r))

    def test_case_counts_must_match_the_expectations(self):
        r = copy.deepcopy(self.r)
        r["cases"].update(total=79, train=39)
        self.assertTrue(any("cases.total" in e for e in self.errors(r)), self.errors(r))

    def test_oracle_and_null_controls_are_recomputed(self):
        r = copy.deepcopy(self.r)
        r["grader"]["validation"]["null_pass_rate"] = 0.2
        self.assertTrue(any("null_pass_rate" in e for e in self.errors(r)), self.errors(r))

    def test_cached_scorer_executes_hashed_source_bytes(self):
        path = self.root / self.r["evidence"]["scorer"]["path"]
        py_compile.compile(str(path), doraise=True)
        stamp = path.stat()
        path.write_text(path.read_text().replace('"oracle_pass_rate": 1.0', '"oracle_pass_rate": 0.5'))
        os.utime(path, ns=(stamp.st_atime_ns, stamp.st_mtime_ns))
        self.r["evidence"]["source"][self.r["evidence"]["scorer"]["path"]] = sha_file(path)
        self.assertTrue(any("oracle_pass_rate" in e for e in self.errors()), self.errors())

    def test_partial_receipts_return_findings(self):
        for field in cer.REQUIRED:
            with self.subTest(field=field):
                r = copy.deepcopy(self.r)
                del r[field]
                self.assertTrue(self.errors(r))

    def test_malformed_claim_prerequisites_return_findings(self):
        mutations = [lambda r: r.update(rung=[]),
                     lambda r: r["grader"].update(validation=[]),
                     lambda r: r["cases"].update(sources=[{}]),
                     lambda r: r["stage_results"][0].update(dimension_ids=[{}]),
                     lambda r: r["verdict"].update(decision=[])]
        for n, mutate in enumerate(mutations):
            with self.subTest(shape=n):
                r = copy.deepcopy(self.r)
                mutate(r)
                self.assertTrue(self.errors(r))

    def test_malformed_scorer_paths_return_findings(self):
        for value in ([], {}, None, 4):
            with self.subTest(path=value):
                r = copy.deepcopy(self.r)
                r["evidence"]["scorer"]["path"] = value
                self.assertTrue(self.errors(r))

    def test_moved_expectations_keep_version_identity(self):
        env = fixture_env()
        def git(*args):
            return subprocess.run(["git", *args], cwd=self.root, env=env,
                                  check=True, capture_output=True).stdout.decode().strip()
        git("init", "-q", "-b", "main")
        git("config", "user.email", "selftest@example.invalid")
        git("config", "user.name", "selftest")
        git("add", "evals")
        git("commit", "-qm", "base")
        base = git("rev-parse", "HEAD")
        x = self.r["evidence"]["expectations"]
        old = self.root / x["path"]
        moved = old.with_name("moved-labels.json")
        doc = json.loads(old.read_text())
        doc["cases"]["r00"]["extra-label"] = "changed"
        moved.write_text(json.dumps(doc))
        x.update(path=moved.relative_to(self.root).as_posix(), sha256=sha_file(moved))
        errs = cer.validate_receipt(self.r, "jev-judgments", self.root, base=base)
        self.assertTrue(any("without a new version" in e for e in errs), errs)

    def test_version_identity_survives_filename_extension_change(self):
        env = fixture_env()
        def git(*args):
            return subprocess.run(["git", *args], cwd=self.root, env=env,
                                  check=True, capture_output=True).stdout.decode().strip()
        git("init", "-q", "-b", "main")
        git("config", "user.email", "selftest@example.invalid")
        git("config", "user.name", "selftest")
        x = self.r["evidence"]["expectations"]
        old = self.root / x["path"]
        renamed = old.with_suffix(".labels")
        old.rename(renamed)
        x["path"] = renamed.relative_to(self.root).as_posix()
        git("add", "evals")
        git("commit", "-qm", "base")
        base = git("rev-parse", "HEAD")
        x = self.r["evidence"]["expectations"]
        old = self.root / x["path"]
        moved = old.with_name("moved-labels.json")
        doc = json.loads(old.read_text())
        doc["cases"]["r00"]["extra-label"] = "changed"
        moved.write_text(json.dumps(doc))
        x.update(path=moved.relative_to(self.root).as_posix(), sha256=sha_file(moved))
        errs = cer.validate_receipt(self.r, "jev-judgments", self.root, base=base)
        self.assertTrue(any("without a new version" in e for e in errs), errs)


class ReplayReuse(unittest.TestCase):
    def test_baseline_reuse_binds_commit_and_harness_and_returns_independent_values(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            harness = root / RD / "run_eval.py"
            harness.parent.mkdir(parents=True)
            code = r"""import argparse, json
from pathlib import Path
p = argparse.ArgumentParser()
p.add_argument('--observe')
p.add_argument('--trace-reads')
a = p.parse_args()
value = int(Path('input.txt').read_text()) * FACTOR
Path(a.observe).write_text(json.dumps({'value': value}) + '\n')
Path(a.trace_reads).write_text(json.dumps(['input.txt']))
"""
            harness.write_text(code.replace("FACTOR", "1"))
            source = root / "input.txt"
            source.write_text("7")
            env = fixture_env()

            def git(*args):
                return subprocess.run(["git", *args], cwd=root, env=env,
                                      check=True, capture_output=True, text=True).stdout.strip()

            git("init", "-q")
            git("config", "user.name", "selftest")
            git("config", "user.email", "selftest@example.invalid")
            git("add", "input.txt", f"{RD}/run_eval.py")
            git("commit", "-qm", "baseline")
            first_ref = git("rev-parse", "HEAD")
            first = cer.replay_rule_delivery(root, first_ref)
            self.assertEqual(first["baseline"]["rows"], [{"value": 7}])
            first["baseline"]["rows"].clear()
            source.write_text("11")
            second = cer.replay_rule_delivery(root, first_ref)
            self.assertEqual(second["baseline"]["rows"], [{"value": 7}])
            self.assertEqual(second["candidate"]["rows"], [{"value": 11}])
            harness.write_text(code.replace("FACTOR", "2"))
            third = cer.replay_rule_delivery(root, first_ref)
            self.assertEqual(third["baseline"]["rows"], [{"value": 14}])
            self.assertEqual(third["candidate"]["rows"], [{"value": 22}])
            git("add", "input.txt", f"{RD}/run_eval.py")
            git("commit", "-qm", "new baseline")
            fourth = cer.replay_rule_delivery(root, git("rev-parse", "HEAD"))
            self.assertEqual(fourth["baseline"]["rows"], [{"value": 22}])


class ReceiptMirrorTests(unittest.TestCase):
    def test_pending_receipt_and_historical_receipt_bind_their_own_sources(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / 'repo'
            root.mkdir()
            env = fixture_env()
            def git(*args):
                return subprocess.run(['git', *args], cwd=root, env=env,
                                      check=True, capture_output=True).stdout
            git('init', '-q')
            git('config', 'user.name', 'selftest')
            git('config', 'user.email', 'selftest@example.invalid')
            source = root / 'input.txt'
            source.write_text('committed source')
            (root / 'evals').mkdir()
            (root / 'evals/surfaces.json').write_text('{}')
            receipt_path = root / RD / 'receipt.json'
            receipt_path.parent.mkdir(parents=True)
            receipt_path.write_text('{}')
            git('add', 'input.txt', 'evals/surfaces.json', f'{RD}/receipt.json')
            git('commit', '-qm', 'fixture receipt')
            historical = {'evidence': {'source': {'input.txt': sha_file(source)},
                'dependencies': {}, 'expectations': {'path': 'input.txt', 'sha256': sha_file(source)},
                'cohorts': {}}}
            source.write_text('pending source')
            pending = copy.deepcopy(historical)
            pending['evidence']['source']['input.txt'] = sha_file(source)
            pending['evidence']['expectations']['sha256'] = sha_file(source)
            with patch.dict(globals(), ROOT=root):
                for receipt, expected in ((pending, 'pending source'), (historical, 'committed source')):
                    dest = Path(tmp) / expected
                    (dest / RD).mkdir(parents=True)
                    mirror(receipt, dest)
                    self.assertEqual((dest / 'input.txt').read_text(), expected)
                broken = copy.deepcopy(pending)
                broken['evidence']['source']['input.txt'] = '0' * 64
                broken['evidence']['expectations']['sha256'] = '0' * 64
                with self.assertRaisesRegex(AssertionError, 'input.txt'):
                    mirror(broken, Path(tmp) / 'missing')


class MirrorBindings(unittest.TestCase):
    def test_receipt_update_does_not_replace_digest_bound_source(self):
        with tempfile.TemporaryDirectory() as tmp:
            root, dest = Path(tmp, "repo"), Path(tmp, "mirror")
            root.mkdir()
            env = fixture_env()
            def git(*args):
                return subprocess.run(["git", *args], cwd=root, env=env,
                                      check=True, capture_output=True)
            git("init", "-q")
            git("config", "user.name", "Fixture")
            git("config", "user.email", "fixture@example.invalid")
            rel = "ops/source.py"
            bound = b"value = 1\n"
            receipt = {"evidence": {"source": {}, "dependencies": {
                rel: hashlib.sha256(bound).hexdigest()},
                "expectations": {"path": RD + "/expectations.json",
                    "sha256": hashlib.sha256(b"{}").hexdigest()}, "cohorts": {}}}
            for path, body in ((rel, bound), (RD + "/receipt.json", json.dumps(receipt).encode()),
                               (RD + "/expectations.json", b"{}"), ("evals/surfaces.json", b"{}")):
                target = root / path
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(body)
            message = Path(tmp, "message")
            message.write_text("Record bound source\n")
            git("add", rel, RD + "/receipt.json", RD + "/expectations.json", "evals/surfaces.json")
            git("commit", "-q", "-F", str(message))
            (root / rel).write_text("value = 2\n")
            receipt["note"] = "A later receipt amendment keeps the measured dependency."
            (root / RD / "receipt.json").write_text(json.dumps(receipt))
            message.write_text("Amend receipt after source changes\n")
            git("add", rel, RD + "/receipt.json")
            git("commit", "-q", "-F", str(message))
            with patch.dict(globals(), ROOT=root):
                mirror(receipt, dest)
            self.assertEqual((dest / rel).read_bytes(), bound)
            receipt["evidence"]["dependencies"][rel] = "0" * 64
            with patch.dict(globals(), ROOT=root), self.assertRaisesRegex(
                    AssertionError, "historical receipt source binding unavailable"):
                mirror(receipt, Path(tmp, "unbound"))


class RuleDeliveryEvidenceChain(unittest.TestCase):
    """The four refusals the evidence chain exists for, on the real rule-delivery receipt."""

    @classmethod
    def setUpClass(cls):
        cls.receipt = json.loads((ROOT / RD / "receipt.json").read_text())

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.r = copy.deepcopy(self.receipt)
        mirror(self.r, self.root)
        ev = self.r["evidence"]
        self.cand = self.root / ev["cohorts"]["candidate"]["path"]
        self.exp = self.root / ev["expectations"]["path"]

    def tearDown(self):
        self.tmp.cleanup()

    def errors(self, root=None):
        return cer.validate_receipt(self.r, "rule-delivery", root or self.root)

    def test_checked_in_receipt_passes_against_its_own_evidence(self):
        self.assertEqual(self.r["schema_version"], 2)
        self.assertEqual(self.errors(), [])

    def test_current_dependency_cannot_replace_historical_evidence(self):
        path = self.root / "ops/typesafe_client.py"
        path.write_bytes(path.read_bytes() + b"\n# changed dependency\n")
        self.assertTrue(any("ops/typesafe_client.py" in error and "sha256" in error
                            for error in self.errors()))

    def test_repeated_validation_reuses_immutable_baseline_across_roots(self):
        # One baseline snapshot per commit/harness, even for separate fixtures.
        # Candidate observations still need a fresh run on every validation.
        with tempfile.TemporaryDirectory() as tmp:
            other = Path(tmp)
            mirror(self.receipt, other)
            with patch.object(cer.subprocess, "run", wraps=subprocess.run) as run:
                self.assertEqual(self.errors(), [])
                self.assertEqual(cer.validate_receipt(self.receipt, "rule-delivery", other), [])
            commands = [call.args[0] for call in run.call_args_list]
            archives = [cmd for cmd in commands if "archive" in cmd]
            observations = [cmd for cmd in commands if "--observe" in cmd]
            self.assertLessEqual(len(archives), 1, "repeated immutable baseline extraction")
            self.assertLessEqual(len(observations), 3, "repeated immutable baseline replay")
            self.assertGreaterEqual(len(observations), 2, "candidate must always replay")

    def test_deleting_a_failing_candidate_row_fails(self):
        self.assertEqual(self.errors(), [])
        case_id, _ = failing_candidate_case(self.root, self.r)
        write_jsonl(self.cand, [row for row in read_jsonl(self.cand) if row["case_id"] != case_id])
        self.assertTrue(any("sha256" in e for e in self.errors()), "unbound bytes went unnoticed")
        self.r["evidence"]["cohorts"]["candidate"]["sha256"] = sha_file(self.cand)
        errs = self.errors()
        self.assertTrue(any("candidate" in e and "missing" in e and case_id in e for e in errs), errs)

    def test_shrinking_the_owed_rule_denominator_fails(self):
        self.assertEqual(self.errors(), [])
        case_id, rule = failing_candidate_case(self.root, self.r)
        doc = json.loads(self.exp.read_text())
        doc["cases"][case_id]["expected"].remove(rule)
        self.exp.write_text(json.dumps(doc, indent=1, sort_keys=True) + "\n")
        self.r["evidence"]["expectations"]["sha256"] = sha_file(self.exp)
        # Summary left as it was: the recompute disagrees.
        self.assertTrue(any("recomputed" in e for e in self.errors()), self.errors())
        # Summary rewritten to match the smaller denominator: the version still binds the old labels.
        scorer = load_scorer(self.root, self.r)
        measured = scorer(doc, read_jsonl(self.root / self.r["evidence"]["cohorts"]["baseline"]["path"]),
                          read_jsonl(self.cand))
        for d in self.r["dimensions"]:
            m = measured["dimensions"][d["dimension_id"]]
            d.update(baseline=m["baseline"], candidate=m["candidate"], delta=m["delta"],
                     direction_vs_baseline=cer._direction(m["delta"]))
        self.assertEqual(self.errors(), [], "the shrunk chain is internally consistent")
        repo = self.root
        env = fixture_env()

        def git(*args):
            subprocess.run(["git", *args], cwd=repo, env=env, check=True, capture_output=True)
        git("init", "-q", "-b", "main")
        git("config", "user.email", "selftest@example.invalid"); git("config", "user.name", "selftest")
        shrunk = self.exp.read_bytes()
        mirror(self.receipt, repo)  # the merge base carries the original labels and receipt
        git("add", "-A"); git("commit", "-qm", "base"); git("branch", "base")
        self.exp.write_bytes(shrunk)
        (repo / RD / "receipt.json").write_text(json.dumps(self.r, indent=1, sort_keys=True) + "\n")
        git("commit", "-qam", "shrink the owed rules, same version")
        body = repo.parent / "body.md"; body.write_text("")
        out = subprocess.run([sys.executable, str(ROOT / "ops" / "check-eval-receipt.py"), "--root", str(repo),
                              "--base", "base", "--pr-body-file", str(body)],
                             cwd=repo, env=env, capture_output=True, text=True)
        self.assertEqual(out.returncode, 1, out.stdout + out.stderr)
        self.assertIn("without a new version", out.stderr)

    def test_changing_result_bytes_fails(self):
        self.assertEqual(self.errors(), [])
        rows = read_jsonl(self.cand)
        case_id, rule = failing_candidate_case(self.root, self.r)
        for row in rows:
            if row["case_id"] == case_id:
                row["delivered"] = sorted(set(row["delivered"]) | {rule})
        write_jsonl(self.cand, rows)
        errs = self.errors()
        self.assertTrue(any(self.r["evidence"]["cohorts"]["candidate"]["path"] in e and "sha256" in e
                            for e in errs), errs)

    def test_reusing_a_stale_summary_fails(self):
        self.assertEqual(self.errors(), [])
        rows = read_jsonl(self.cand)
        case_id, rule = failing_candidate_case(self.root, self.r)
        for row in rows:
            if row["case_id"] == case_id:
                row["delivered"] = sorted(set(row["delivered"]) | {rule})
        write_jsonl(self.cand, rows)
        self.r["evidence"]["cohorts"]["candidate"]["sha256"] = sha_file(self.cand)
        errs = self.errors()
        self.assertTrue(any("human-required-recall" in e and "recomputed" in e for e in errs), errs)


    def test_changed_measured_source_cannot_reuse_observations(self):
        rel = "hooks/rule-pack-preuse-reselection.py"
        path = self.root / rel
        original = path.read_bytes()
        for mode in ("drop", "rebind"):
            with self.subTest(mode=mode):
                self.r = copy.deepcopy(self.receipt)
                path.write_bytes(original + b"\nrouted_rule_ids = lambda *args, **kwargs: []\nmatched_triggers = lambda *args, **kwargs: []\n")
                if mode == "drop":
                    del self.r["evidence"]["dependencies"][rel]
                else:
                    self.r["evidence"]["dependencies"][rel] = sha_file(path)
                errs = self.errors()
                self.assertTrue(any("replay" in e or "complete" in e for e in errs), errs)

    def test_cold_and_warm_imports_bind_same_sources(self):
        with tempfile.TemporaryDirectory() as tmp:
            tree = Path(tmp) / "tree"
            mirror(self.receipt, tree)
            manifests = []
            env = dict(os.environ)
            env.pop("PYTHONDONTWRITEBYTECODE", None)
            env.pop("PYTHONPYCACHEPREFIX", None)
            for run in ("cold", "warm"):
                out = Path(tmp) / run
                out.mkdir()
                obs, trace = out / "observations.jsonl", out / "reads.json"
                subprocess.run([sys.executable, str(tree / RD / "run_eval.py"),
                                "--observe", str(obs), "--trace-reads", str(trace)], env=env, check=True)
                rows, reads = read_jsonl(obs), json.loads(trace.read_text())
                self.assertEqual(rows, read_jsonl(self.cand))
                manifests.append({p for p in reads if "__pycache__" not in p.split("/")})
                self.assertTrue(list((tree / "hooks" / "__pycache__").glob("rule-pack-preuse-reselection*.pyc")))
            self.assertEqual(manifests[0], manifests[1])
            self.assertIn("hooks/rule-pack-preuse-reselection.py", manifests[1])

    def test_verified_input_hashes_carry_forward(self):
        """Frozen labels and unchanged compiler inputs retain their verified bytes."""
        deps = self.r["evidence"]["dependencies"]
        for path, digest in {
            "evals/rule-delivery/hard_cases.v1.json": "abc3a372b4ea3c25bd2b1db10850b3ebf1d5239049711ab3a015df378cd844ff",
            "ops/fixtures/rule-delivery-eval/cases.v2.json": "20d0a652e02559241e25a8b40ebb2f700a939c7ef7dc38114d5d7978a559e0f7",
            "ops/rule_trigger_compile.py": "26e0595e793aaba54df070f206edfa9d72f33d861f77741e0f3947bf7dbccdce",
        }.items():
            self.assertEqual(deps.get(path), digest, path)


CONTROL_KEY = "eval_receipt"
RULE_ID = "6cbaa63a-be57-4c2e-955f-4ea7b5c0405d"
MIGRATION = ROOT / "migrations" / "0753_eval_receipt_control.sql"

_SYNC_SPEC = importlib.util.spec_from_file_location("sync_control_catalog", ROOT / "ops" / "sync_control_catalog.py")
assert _SYNC_SPEC and _SYNC_SPEC.loader
sync = importlib.util.module_from_spec(_SYNC_SPEC)
_SYNC_SPEC.loader.exec_module(sync)


class ControlRegistration(unittest.TestCase):
    """Rule 6cbaa63a names 'eval_receipt CI check' as its carrying control.

    approve-rule accepts a control only when ops.enforcement_control_catalog
    carries it installed and verified, in a class that can enforce (deny_gate,
    stop_gate, schema, transactional_schema). The catalog is compiled from the
    repository's declarations by ops/sync_control_catalog.py and seeded by a
    migration rendered from the same compiler.
    """

    @classmethod
    def setUpClass(cls):
        cls.rows = {r["control_key"]: r for r in sync.compile_catalog()}

    def test_the_control_is_declared_and_compiles_as_installed(self):
        row = self.rows.get(CONTROL_KEY)
        self.assertIsNotNone(row, f"{CONTROL_KEY} is not declared")
        self.assertTrue(row["installed"], row["not_installed_reason"])

    def test_it_is_a_class_approve_rule_accepts(self):
        self.assertIn(self.rows[CONTROL_KEY]["enforcement_class"],
                      {"deny_gate", "stop_gate", "schema", "transactional_schema"})

    def test_it_names_this_check_and_this_selftest(self):
        row = self.rows[CONTROL_KEY]
        self.assertIn("ops/check-eval-receipt.py", row["implementation_ref"].split("; "))
        self.assertIn("ops/check-eval-receipt-selftest.py", row["test_ref"].split("; "))

    def test_the_declaration_names_the_rule_it_carries(self):
        side = json.loads((ROOT / "ops" / "config" / "control-enforcement-classes.v1.json").read_text())
        entry = side["controls_absent_from_the_map"][CONTROL_KEY]
        self.assertIn(RULE_ID, entry.get("_note", ""))

    def test_the_enforcement_map_is_not_touched(self):
        # audits/guidance-situation-curation-review.v1.json pins the map by
        # sha256 while that review is still proposed; declaring the control
        # beside the map is the route that keeps the pin true.
        review = json.loads((ROOT / "audits" / "guidance-situation-curation-review.v1.json").read_text())
        pinned = next(i["sha256"] for i in _walk(review)
                      if isinstance(i, dict) and i.get("path") == "ops/config/rule-enforcement-map.json")
        import hashlib
        actual = hashlib.sha256((ROOT / "ops" / "config" / "rule-enforcement-map.json").read_bytes()).hexdigest()
        self.assertEqual(actual, pinned)

    def test_the_seed_migration_is_exactly_the_compiler_output(self):
        text = MIGRATION.read_text()
        self.assertIn(sync.render_control_upsert_sql(self.rows[CONTROL_KEY]), text)
        self.assertNotRegex(text, r"(?im)^\s*(begin|commit)\s*;")  # the runner owns the transaction

    def test_the_migration_slot_is_not_shared(self):
        slot = MIGRATION.name[:4]
        same = [p.name for p in (ROOT / "migrations").glob(f"{slot}*.sql")]
        self.assertEqual(same, [MIGRATION.name])


def _walk(value):
    if isinstance(value, dict):
        yield value
        for v in value.values():
            yield from _walk(v)
    elif isinstance(value, list):
        for v in value:
            yield from _walk(v)


if __name__ == "__main__":
    unittest.main(verbosity=1)
