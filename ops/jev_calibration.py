"""jev_calibration.py — measure Jev judgments against what later happened.

WHY THIS EXISTS. Joe's ruling: code decides and Jev only judges, and the point
at which code lets a judgment act has to be CALIBRATED PER ACTION, never pooled.
A number that suits a commit-message warning does not suit a client document,
and a number that suits one question family does not transfer to another. So
a threshold here is never typed in, never borrowed from outside CARR, and never
read off numbers pooled across families or consequence classes. It is measured.

FOUR PIECES, one per job:

  1. OUTCOMES. record_outcome() appends what later happened to one judgment —
     a review verdict, a test result, a human correction — to
     out/jev-outcomes.jsonl, keyed by the judgment_id ops/jev_judge.record()
     wrote. A test result is a gold source by construction; a human correction
     names its human; a review verdict counts as validated only when a named
     human stands behind it.

  2. FIXTURES. One versioned labeled fixture per question family under
     ops/fixtures/jev-calibration/<family>.v1.json. Every case says whether its
     label is validated or unvalidated, whether a gold source exists, and which
     split it belongs to (calibration or held_out). A fixture may list its
     cases inline or derive them from committed evaluation data pinned by
     sha256, so a moved source refuses rather than silently relabels.

  3. JOIN. join() links each judgment question to its label — outcome first,
     then fixture — scores the distribution's top option against it, and says
     when two validated sources disagree.

  4. REPORT. report() prints, per family AND consequence class, accuracy and
     reliability by entropy band and by probability band on the HELD-OUT
     split, with the pooled cells shown beside them for context. A proposal is
     made only for a single family and class, only from validated labels, only
     on one recorded model, and only when a band chosen on the calibration
     split is confirmed on the held-out split at the caller's target accuracy
     (a Wilson lower bound, so a small sample cannot pass on luck). Pooled
     cells always refuse. Band edges are quantiles of the calibration split,
     never fixed cutoffs.

A proposal is a candidate for ops/config/jev-calibrated-bands.v1.json, which
ops/jev_judge.route() reads. Copying it there is a reviewed commit; nothing in
this module writes the live bands file.

IT IS A LIBRARY AND MUST STAY ONE: no shebang, no main guard (see
ops/typesafe_client.py for why the construct is described and never spelled).
The command-line front door is ops/jev-calibration-report.py.
"""

import hashlib
import importlib.util
import json
import math
import os
from datetime import datetime, timezone
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
FIXTURE_DIR = REPO / "ops" / "fixtures" / "jev-calibration"
OUTCOME_LOG = str(REPO / "out" / "jev-outcomes.jsonl")
JUDGMENT_LOG = str(REPO / "out" / "jev-judge.jsonl")
LIVE_BANDS_PATH = REPO / "ops" / "config" / "jev-calibrated-bands.v1.json"

FIXTURE_SCHEMA = "carr.jev-calibration-fixture.v1"
BANDS_SCHEMA = "carr.jev-calibrated-bands.v1"
OUTCOME_SOURCES = ("review_verdict", "test_result", "human_correction")
# Which validated label wins when several exist for one judgment question.
LABEL_PRECEDENCE = ("human_correction", "fixture", "test_result", "review_verdict")
SPLITS = ("calibration", "held_out")
LABEL_STATUSES = ("validated", "unvalidated")
QUESTION_TYPES = ("noul", "choice", "score")
POOLED = "*"


class FixtureError(ValueError):
    """A fixture that cannot be trusted as labels. Never skipped silently."""


def _client():
    path = REPO / "ops" / "typesafe_client.py"
    spec = importlib.util.spec_from_file_location("typesafe_client", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


_TSC = None


def _tsc():
    global _TSC
    if _TSC is None:
        _TSC = _client()
    return _TSC


def normalize_label(value):
    """Labels compare as strings; a noul's True/False become 'true'/'false'."""
    if value is None:
        return None
    if isinstance(value, bool):
        return "true" if value else "false"
    return str(value)


def read_jsonl(path, stats=None):
    """Rows of a JSONL log. A missing file is empty; a corrupt line is counted."""
    rows = []
    try:
        with open(path, "r", encoding="utf-8") as handle:
            for line in handle:
                line = line.strip()
                if not line:
                    continue
                try:
                    row = json.loads(line)
                except ValueError:
                    row = None
                if isinstance(row, dict):
                    rows.append(row)
                elif stats is not None:
                    stats["corrupt_lines"] = stats.get("corrupt_lines", 0) + 1
    except OSError:
        pass
    return rows


# ── 1. outcomes ─────────────────────────────────────────────────────────────

def record_outcome(judgment_id, question_id, source, *, label=None, correct=None,
                   validated_by=None, gold_source=None, note=None, log_path=OUTCOME_LOG):
    """Append what later happened to one judgment question. Raises on bad input.

    Give `label` (the true answer, compared with the distribution's top option)
    or `correct` (a verdict on the judgment itself), or both. Unlike
    jev_judge.record(), this refuses loudly: a silently dropped outcome is a
    silently biased accuracy.
    """
    if not isinstance(judgment_id, str) or not judgment_id.strip():
        raise ValueError("judgment_id is required")
    if not isinstance(question_id, str) or not question_id.strip():
        raise ValueError("question_id is required")
    if source not in OUTCOME_SOURCES:
        raise ValueError(f"source must be one of {', '.join(OUTCOME_SOURCES)}")
    if label is None and correct is None:
        raise ValueError("an outcome needs a label or a correct verdict")
    if correct is not None and not isinstance(correct, bool):
        raise ValueError("correct must be true or false")
    if source == "human_correction" and not validated_by:
        raise ValueError("a human correction names the human (validated_by)")
    validated = source == "test_result" or bool(validated_by)
    row = {
        "at": datetime.now(timezone.utc).isoformat(),
        "judgment_id": judgment_id,
        "question_id": question_id,
        "source": source,
        "label": normalize_label(label),
        "correct": correct,
        "label_status": "validated" if validated else "unvalidated",
        "validated_by": validated_by,
        "gold_source_available": source == "test_result" or gold_source is not None,
        "gold_source": gold_source,
        "note": note,
    }
    os.makedirs(os.path.dirname(log_path), exist_ok=True)
    with open(log_path, "a", encoding="utf-8") as handle:
        handle.write(json.dumps(row, sort_keys=True) + "\n")
    return row


# ── 2. fixtures ─────────────────────────────────────────────────────────────

def validate_fixture(doc):
    """Every problem with a materialized fixture, as a list of strings."""
    errors = []
    if not isinstance(doc, dict) or doc.get("schema") != FIXTURE_SCHEMA:
        return [f"schema must be {FIXTURE_SCHEMA}"]
    family = doc.get("family")
    if not isinstance(family, str) or not family.strip() or family == POOLED:
        errors.append("family must name one question family, never the pooled '*'")
    if not isinstance(doc.get("version"), str) or not doc["version"].strip():
        errors.append("version is required")
    if doc.get("question_type") not in QUESTION_TYPES:
        errors.append(f"question_type must be one of {', '.join(QUESTION_TYPES)}")
    classes = doc.get("consequence_classes")
    if not isinstance(classes, list) or not classes:
        errors.append("consequence_classes must list at least one class")
        classes = []
    elif POOLED in classes:
        errors.append("consequence_classes may not include the pooled '*'")
    cases = doc.get("cases")
    if not isinstance(cases, list) or not cases:
        return errors + ["a fixture needs at least one case (no cases)"]
    seen = set()
    seen_join_keys = set()
    for index, item in enumerate(cases):
        where = f"case {index} ({item.get('case_id') if isinstance(item, dict) else '?'})"
        if not isinstance(item, dict):
            errors.append(f"{where}: not an object")
            continue
        case_id = item.get("case_id")
        if not isinstance(case_id, str) or not case_id:
            errors.append(f"{where}: case_id is required")
        elif case_id in seen:
            errors.append(f"{where}: duplicate case_id")
        seen.add(case_id)
        join_key = (item.get("subject_ref"), item.get("consequence_class"),
                    item.get("question_id"))
        if join_key in seen_join_keys:
            errors.append(f"{where}: duplicate join key")
        seen_join_keys.add(join_key)
        if not isinstance(item.get("subject_ref"), str) or not item["subject_ref"]:
            errors.append(f"{where}: subject_ref is required")
        if not isinstance(item.get("question_id"), str) or not item["question_id"]:
            errors.append(f"{where}: question_id is required")
        if item.get("consequence_class") not in classes:
            errors.append(f"{where}: consequence_class {item.get('consequence_class')!r} "
                          "is not declared in consequence_classes")
        if item.get("label") is None:
            errors.append(f"{where}: label is required")
        status = item.get("label_status")
        if status not in LABEL_STATUSES:
            errors.append(f"{where}: label_status must be validated or unvalidated")
        elif status == "validated" and not item.get("validated_by"):
            errors.append(f"{where}: a validated label names who validated it (validated_by)")
        if not isinstance(item.get("gold_source_available"), bool):
            errors.append(f"{where}: gold_source_available must be true or false")
        elif item["gold_source_available"] and not item.get("gold_source"):
            errors.append(f"{where}: gold_source_available needs a gold_source")
        if item.get("split") not in SPLITS:
            errors.append(f"{where}: split must be calibration or held_out")
        judgment = item.get("judgment")
        if judgment is not None and (not isinstance(judgment, dict)
                                     or not isinstance(judgment.get("distribution"), dict)):
            errors.append(f"{where}: judgment needs a distribution object")
    return errors


def _sha256_file(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def _derive_rule_delivery_eval_v2(doc):
    """rule-delivery-eval v2: one noul per (case, live rule), first-pass Jev
    probabilities recorded in labels.v2.json. A pair is VALIDATED only where
    the rule was adjudicated in writing; every other gold bit came from the
    labelling pass itself, so it is unvalidated and has no gold source."""
    source = doc["source"]
    cases_path = REPO / source["cases"]
    labels_path = REPO / source["labels"]
    for key, path in (("cases_sha256", cases_path), ("labels_sha256", labels_path)):
        try:
            actual = _sha256_file(path)
        except OSError as err:
            raise FixtureError(f"{doc.get('family')}: cannot read {path}: {err.strerror}") from None
        if actual != source.get(key):
            raise FixtureError(f"{doc.get('family')}: source {key} is {actual}, fixture pins "
                               f"{source.get(key)}; re-derive and bump the fixture version")
    eval_cases = json.loads(cases_path.read_text(encoding="utf-8"))
    labels = json.loads(labels_path.read_text(encoding="utf-8"))
    rules = labels["rules"]
    consequence_class = doc["default_consequence_class"]
    split_of = {"train": "calibration", "test": "held_out"}
    cases = []
    for item in eval_cases["cases"]:
        probabilities = labels["cases"][item["id"]]
        gold = set(item["gold"])
        adjudicated = set(item["adjudicated"])
        for rule, p in zip(rules, probabilities):
            validated = rule in adjudicated
            cases.append({
                "case_id": f"{item['id']}#{rule}",
                "subject_ref": f"{item['id']}#{rule}",
                "question_id": "q",
                "consequence_class": consequence_class,
                "label": rule in gold,
                "label_status": "validated" if validated else "unvalidated",
                "validated_by": source["validated_by"] if validated else None,
                "gold_source_available": validated,
                "gold_source": source["gold_source"] if validated else None,
                "split": split_of[item["split"]],
                "judgment": {"model": source.get("model"),
                             "distribution": {"true": p, "false": 1 - p}},
            })
    return cases


DERIVERS = {"rule-delivery-eval.v2": _derive_rule_delivery_eval_v2}


def materialize_fixture(doc):
    """Inline fixtures pass through; derived ones are rebuilt from pinned sources."""
    if not isinstance(doc, dict):
        raise FixtureError("a fixture is a JSON object")
    if "source" in doc:
        kind = (doc.get("source") or {}).get("kind")
        if kind not in DERIVERS:
            raise FixtureError(f"{doc.get('family')}: unknown source kind {kind!r}")
        doc = {**doc, "cases": DERIVERS[kind](doc)}
    errors = validate_fixture(doc)
    if errors:
        shown = "; ".join(errors[:10]) + (f"; and {len(errors) - 10} more" if len(errors) > 10 else "")
        raise FixtureError(f"{doc.get('family')}: {shown}")
    return doc


def load_fixtures(directory=FIXTURE_DIR):
    """{family: materialized fixture} for every calibration fixture in a directory.

    Files carrying another schema (the shared distribution vectors) are skipped;
    a calibration fixture that does not validate raises FixtureError.
    """
    fixtures = {}
    for path in sorted(Path(directory).glob("*.json")):
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except ValueError as err:
            raise FixtureError(f"{path.name}: not JSON ({err})") from None
        if not isinstance(raw, dict) or raw.get("schema") != FIXTURE_SCHEMA:
            continue
        try:
            doc = materialize_fixture(raw)
        except FixtureError as err:
            raise FixtureError(f"{path.name}: {err}") from None
        if doc["family"] in fixtures:
            raise FixtureError(f"{path.name}: a second fixture for family {doc['family']}")
        fixtures[doc["family"]] = doc
    return fixtures


# ── 3. join ─────────────────────────────────────────────────────────────────

def _per_question(value, question_id):
    return value.get(question_id) if isinstance(value, dict) else value


def _hash_split(family, subject_ref, held_out_fraction):
    digest = hashlib.sha256(f"{family}\n{subject_ref}".encode("utf-8")).hexdigest()
    return "held_out" if int(digest[:8], 16) / 2 ** 32 < held_out_fraction else "calibration"


def _replay_summary(question_type, distribution):
    if question_type == "noul":
        answer = {"type": "noul", "noul": distribution.get("true")}
    else:
        answer = {"type": question_type, "probabilities": distribution}
    return _tsc().answer_distribution(None, answer)


def _choose_label(candidates, selected):
    """Pick the label to score against; flag validated sources that disagree."""
    if not candidates:
        return None, False
    def verdict(c):
        if c.get("correct") is not None:
            return c["correct"]
        return None if selected is None else normalize_label(c.get("label")) == selected
    validated = [c for c in candidates if c.get("label_status") == "validated"]
    pool = validated or candidates
    pool = sorted(pool, key=lambda c: LABEL_PRECEDENCE.index(c["source"])
                  if c["source"] in LABEL_PRECEDENCE else len(LABEL_PRECEDENCE))
    verdicts = {verdict(c) for c in validated if verdict(c) is not None}
    contradictory = any(c.get("correct") is not None and c.get("label") is not None
                        and selected is not None
                        and c["correct"] != (normalize_label(c["label"]) == selected)
                        for c in validated)
    chosen = pool[0]
    return {**chosen, "correct": verdict(chosen)}, contradictory or len(verdicts) > 1


def join(judgments, outcomes, fixtures, *, held_out_fraction=0.5):
    """Link each judgment question to its label and score it.

    Returns {"units": [...], "skipped": {...}}. A unit is one question of one
    judgment (or one replayed fixture case): its family, consequence class,
    model, entropy, top option and probability, and — when a label exists —
    whether the top option was right, where the label came from, whether it is
    validated, whether a gold source exists, and its split.
    """
    skipped = {"no_family": 0, "no_calibration": 0}
    by_judgment = {}
    for outcome in outcomes:
        key = (outcome.get("judgment_id"), outcome.get("question_id"))
        by_judgment.setdefault(key, []).append(outcome)
    case_index = {}
    for family, fixture in fixtures.items():
        for item in fixture["cases"]:
            case_index[(family, item["subject_ref"], item["consequence_class"],
                        item["question_id"])] = (fixture, item)
    used_cases = set()
    units = []

    def unit_from(*, unit_id, judgment_id, question_id, family, consequence_class,
                  subject_ref, model, summary, candidates, fixture_case, recorded_in,
                  selected_choice=None, model_requested=None, model_pinned=None):
        split = (fixture_case[1]["split"] if fixture_case else
                 _hash_split(family, subject_ref, held_out_fraction))
        selected = selected_choice if summary.get("type") == "choice" else summary.get("top")
        distribution = summary.get("distribution")
        selected_probability = (distribution.get(selected) if isinstance(distribution, dict)
                                and selected in distribution else None)
        chosen, conflict = _choose_label(candidates, selected)
        return {
            "unit_id": unit_id, "judgment_id": judgment_id, "question_id": question_id,
            "family": family,
            "consequence_class": consequence_class or (fixture_case[1]["consequence_class"]
                                                       if fixture_case else None),
            "subject_ref": subject_ref, "model": model,
            "model_requested": model_requested, "model_pinned": model_pinned,
            "entropy_bits": summary.get("entropy_bits"),
            "top": summary.get("top"), "top_probability": summary.get("top_probability"),
            "selected_choice": selected, "selected_probability": selected_probability,
            "distribution_complete": summary.get("distribution_complete") is True,
            "label": chosen.get("label") if chosen else None,
            "correct": chosen.get("correct") if chosen else None,
            "label_status": chosen.get("label_status") if chosen else None,
            "gold_source_available": bool(chosen.get("gold_source_available")) if chosen else False,
            "label_source": chosen.get("source") if chosen else None,
            "fixture_version": fixture_case[0]["version"] if fixture_case else None,
            "split": split, "conflict": conflict, "recorded_in": recorded_in,
        }

    def fixture_candidate(fixture_case):
        _, item = fixture_case
        return {"source": "fixture", "label": normalize_label(item["label"]),
                "label_status": item["label_status"],
                "gold_source_available": item["gold_source_available"]}

    for row in judgments:
        calibration = row.get("calibration")
        questions = calibration.get("questions") if isinstance(calibration, dict) else None
        if not isinstance(questions, dict):
            if "error" not in row:
                skipped["no_calibration"] += 1
            continue
        model = calibration.get("model_answered") or row.get("model")
        for question_id, summary in sorted(questions.items()):
            family = _per_question(row.get("family"), question_id)
            if not family:
                skipped["no_family"] += 1
                continue
            subject_ref = row.get("subject_ref")
            consequence_class = _per_question(row.get("consequence_class"), question_id)
            fixture_case = case_index.get((family, subject_ref, consequence_class, question_id))
            if fixture_case:
                used_cases.add((family, subject_ref, consequence_class, question_id))
            candidates = [dict(o, label=normalize_label(o.get("label")))
                          for o in by_judgment.get((row.get("judgment_id"), question_id), [])]
            if fixture_case:
                candidates.append(fixture_candidate(fixture_case))
            units.append(unit_from(
                unit_id=f"{row.get('judgment_id')}:{question_id}",
                judgment_id=row.get("judgment_id"), question_id=question_id, family=family,
                consequence_class=consequence_class,
                subject_ref=subject_ref, model=model,
                summary=summary if isinstance(summary, dict) else {},
                candidates=candidates, fixture_case=fixture_case, recorded_in="judgment_log",
                model_requested=calibration.get("model_requested"),
                model_pinned=calibration.get("model_pinned"),
                selected_choice=(row.get("answers", {}).get(question_id, {}).get("choice")
                                 if isinstance(row.get("answers"), dict) else None)))

    for family, fixture in fixtures.items():
        for item in fixture["cases"]:
            judgment = item.get("judgment")
            if judgment is None or (family, item["subject_ref"], item["consequence_class"],
                                    item["question_id"]) in used_cases:
                continue
            fixture_case = (fixture, item)
            units.append(unit_from(
                unit_id=f"fixture:{family}:{item['case_id']}", judgment_id=None,
                question_id=item["question_id"], family=family,
                consequence_class=item["consequence_class"],
                subject_ref=item["subject_ref"], model=judgment.get("model"),
                summary=_replay_summary(fixture["question_type"], judgment["distribution"]),
                candidates=[fixture_candidate(fixture_case)], fixture_case=fixture_case,
                recorded_in="fixture", selected_choice=judgment.get("choice"),
                model_requested=judgment.get("model"),
                model_pinned=_tsc().model_is_pinned(judgment.get("model"))))
    return {"units": units, "skipped": skipped}


# ── 4. report ───────────────────────────────────────────────────────────────

def wilson(correct, n, z):
    """Wilson score interval for a proportion; (None, None) with no data."""
    if n <= 0:
        return None, None
    phat = correct / n
    denom = 1 + z * z / n
    centre = (phat + z * z / (2 * n)) / denom
    half = z * math.sqrt(phat * (1 - phat) / n + z * z / (4 * n * n)) / denom
    return max(0.0, centre - half), min(1.0, centre + half)


def quantile_edges(values, bins):
    """Upper band edges at equal-count quantiles of `values` (deduplicated)."""
    ordered = sorted(values)
    if not ordered:
        return []
    bins = max(1, int(bins))
    edges = []
    for k in range(1, bins + 1):
        edge = ordered[max(0, math.ceil(k * len(ordered) / bins) - 1)]
        if not edges or edge > edges[-1]:
            edges.append(edge)
    return edges


def band_table(units, key, edges, z):
    """Held-out accuracy and reliability per band of `key` (upper-inclusive)."""
    bands = []
    lower = None
    for edge in edges + [math.inf]:
        members = [u for u in units
                   if u.get(key, u.get("top_probability")) is not None
                   and (lower is None or u.get(key, u.get("top_probability")) > lower)
                   and u.get(key, u.get("top_probability")) <= edge]
        if edge == math.inf and not members:
            break
        n = len(members)
        correct = sum(1 for u in members if u["correct"])
        accuracy = correct / n if n else None
        mean_p = (sum(u["top_probability"] for u in members if u["top_probability"] is not None)
                  / n) if n else None
        mean_selected = (sum(u.get("selected_probability", u.get("top_probability"))
                             for u in members if u.get("selected_probability", u.get("top_probability")) is not None)
                         / n) if n else None
        low, high = wilson(correct, n, z)
        bands.append({"lower_exclusive": lower, "upper_inclusive": None if edge == math.inf else edge,
                      "n": n, "correct": correct, "accuracy": accuracy,
                      "accuracy_lower": low, "accuracy_upper": high,
                      "mean_top_probability": mean_p,
                      "mean_selected_probability": mean_selected,
                      "gap": None if accuracy is None or mean_selected is None else mean_selected - accuracy})
        lower = edge
    return bands


def _accuracy(units):
    n = len(units)
    return {"n": n, "accuracy": (sum(1 for u in units if u["correct"]) / n) if n else None}


def _scorable(unit):
    selected_probability = unit.get("selected_probability", unit.get("top_probability"))
    return (unit.get("distribution_complete") is True
            and unit.get("entropy_bits") is not None
            and isinstance(selected_probability, (int, float))
            and not isinstance(selected_probability, bool)
            and math.isfinite(selected_probability) and 0 <= selected_probability <= 1)


def _propose(cell, validated, target, z, pooled):
    if pooled:
        return {"status": "refused", "reason": "pooled",
                "detail": "a threshold is proposed per question family AND consequence class, "
                          "never from numbers pooled across them"}
    if target is None:
        return {"status": "no_target",
                "detail": f"no target accuracy for consequence class {cell['consequence_class']!r}; "
                          "pass --target CLASS=ACCURACY (nothing is assumed)"}
    if any(u.get("conflict") for u in validated):
        return {"status": "refused", "reason": "conflicting_validated_labels",
                "detail": "resolve conflicting validated labels before proposing a band"}
    calibration = [u for u in validated if u["split"] == "calibration" and _scorable(u)]
    held_out = [u for u in validated if u["split"] == "held_out" and _scorable(u)]
    if not calibration or not held_out:
        return {"status": "refused", "reason": "insufficient_validated_labels",
                "detail": f"{len(calibration)} calibration and {len(held_out)} held-out validated "
                          "labels with a full distribution; both splits are needed"}
    models = {u.get("model") for u in validated}
    if None in models:
        return {"status": "refused", "reason": "model_unrecorded",
                "detail": "the model that answered was not recorded, and a band never "
                          "transfers between models; re-run on a pinned model"}
    if len(models) > 1:
        return {"status": "refused", "reason": "mixed_models", "models": sorted(models),
                "detail": "a band is measured on one model; split the data by model"}
    if not _tsc().model_is_pinned(next(iter(models))):
        return {"status": "refused", "reason": "model_unpinned",
                "detail": "a moving model alias cannot anchor a calibrated band"}
    if any(u.get("model_pinned", _tsc().model_is_pinned(u.get("model"))) is not True
           or (u.get("model_requested") is not None
               and u["model_requested"] != u.get("model")) for u in validated):
        return {"status": "refused", "reason": "model_unpinned",
                "detail": "each validated judgment needs a pinned requested model matching the answer"}
    chosen = None
    ordered = sorted(calibration, key=lambda u: u["entropy_bits"])
    correct = 0
    for index, unit in enumerate(ordered):
        correct += 1 if unit["correct"] else 0
        is_edge = index + 1 == len(ordered) or ordered[index + 1]["entropy_bits"] > unit["entropy_bits"]
        if is_edge:
            low, _ = wilson(correct, index + 1, z)
            if low >= target:
                chosen = (unit["entropy_bits"], index + 1, correct, low)
    if chosen is None:
        return {"status": "refused", "reason": "no_band_meets_target_on_calibration_split",
                "target_accuracy": target}
    edge, cal_n, cal_correct, cal_low = chosen
    inside = [u for u in held_out if u["entropy_bits"] <= edge]
    held_correct = sum(1 for u in inside if u["correct"])
    held_low, _ = wilson(held_correct, len(inside), z)
    held = {"n": len(inside), "correct": held_correct,
            "accuracy": held_correct / len(inside) if inside else None,
            "accuracy_lower": held_low, "coverage": len(inside) / len(held_out)}
    calibration_numbers = {"n": cal_n, "correct": cal_correct, "accuracy": cal_correct / cal_n,
                           "accuracy_lower": cal_low}
    if not inside or held_low < target:
        return {"status": "refused", "reason": "held_out_did_not_confirm",
                "max_entropy_bits": edge, "target_accuracy": target,
                "calibration": calibration_numbers, "held_out": held}
    return {"status": "proposed", "max_entropy_bits": edge, "model": next(iter(models)),
            "target_accuracy": target, "z": z,
            "calibration": calibration_numbers, "held_out": held}


def _cell(family, consequence_class, units, targets, bins, z):
    pooled = POOLED in (family, consequence_class)
    labeled = [u for u in units if u["correct"] is not None]
    validated = [u for u in labeled if u["label_status"] == "validated"]
    unvalidated = [u for u in labeled if u["label_status"] != "validated"]
    with_entropy = [u for u in validated if _scorable(u)]
    calibration = [u for u in with_entropy if u["split"] == "calibration"]
    held_out = [u for u in with_entropy if u["split"] == "held_out"]
    entropy_edges = quantile_edges([u["entropy_bits"] for u in calibration], bins)
    probability_edges = quantile_edges([
        u.get("selected_probability", u.get("top_probability")) for u in calibration
        if u.get("selected_probability", u.get("top_probability")) is not None], bins)
    probability_bands = band_table(held_out, "selected_probability", probability_edges, z)
    ece = (sum(b["n"] * abs(b["gap"]) for b in probability_bands if b["gap"] is not None)
           / len(held_out)) if held_out else None
    cell = {
        "family": family, "consequence_class": consequence_class, "pooled": pooled,
        "counts": {
            "units": len(units), "labeled": len(labeled), "validated": len(validated),
            "unvalidated": len(unvalidated),
            "gold_source_available": sum(1 for u in validated if u.get("gold_source_available")),
            "no_distribution": sum(1 for u in validated if u["entropy_bits"] is None),
            "conflicts": sum(1 for u in labeled if u.get("conflict")),
        },
        "models": sorted({u.get("model") or "unrecorded" for u in units}),
        "fixture_versions": sorted({u.get("fixture_version") for u in units if u.get("fixture_version")}),
        "calibration_split": {"validated": len(calibration), "entropy_edges": entropy_edges,
                              "probability_edges": probability_edges},
        "held_out": {"validated": len(held_out), **_accuracy(held_out),
                     "entropy_bands": band_table(held_out, "entropy_bits", entropy_edges, z),
                     "probability_bands": probability_bands,
                     "expected_calibration_error": ece},
        "unvalidated": _accuracy(unvalidated),
    }
    cell["proposal"] = _propose(cell, validated, targets.get(consequence_class), z, pooled)
    return cell


def report(units, *, targets=None, bins=4, z=1.96):
    """Per family and consequence class, with pooled cells shown and refused."""
    targets = dict(targets or {})
    if POOLED in targets:
        raise ValueError("a target names one consequence class, never the pooled '*'")
    groups = {}
    for unit in units:
        family, cc = unit["family"], unit.get("consequence_class") or "unclassified"
        for key in ((family, cc), (family, POOLED), (POOLED, cc), (POOLED, POOLED)):
            groups.setdefault(key, []).append(unit)
    order = sorted(groups, key=lambda k: (k[0] == POOLED, k[0], k[1] == POOLED, k[1]))
    return {"schema": "carr.jev-calibration-report.v1", "targets": targets, "bins": bins,
            "z": z, "cells": [_cell(f, c, groups[(f, c)], targets, bins, z) for f, c in order]}


def bands_from_report(report_doc, *, measured_at=None):
    """A candidate bands file holding only the proposed cells. Never the live file."""
    bands = {}
    for cell in report_doc["cells"]:
        proposal = cell["proposal"]
        if cell["pooled"] or proposal.get("status") != "proposed":
            continue
        bands.setdefault(cell["family"], {})[cell["consequence_class"]] = {
            "max_entropy_bits": proposal["max_entropy_bits"], "model": proposal["model"],
            "target_accuracy": proposal["target_accuracy"],
            "held_out_n": proposal["held_out"]["n"],
            "held_out_accuracy": proposal["held_out"]["accuracy"],
            "held_out_accuracy_lower": proposal["held_out"]["accuracy_lower"],
            "fixture_versions": cell["fixture_versions"],
            "measured_at": measured_at or datetime.now(timezone.utc).isoformat(),
        }
    return {"schema": BANDS_SCHEMA,
            "note": "CANDIDATE from ops/jev-calibration-report.py. Review, then copy entries "
                    "into ops/config/jev-calibrated-bands.v1.json in a commit.",
            "bands": bands}


def _fmt(value, digits=3):
    return "-" if value is None else f"{value:.{digits}f}"


def format_report(report_doc):
    """Plain text for a person. JSON (--json) carries every number."""
    lines = [f"Jev calibration report (held-out split; bands are calibration-split quantiles, "
             f"bins={report_doc['bins']}, z={report_doc['z']})"]
    for cell in report_doc["cells"]:
        counts = cell["counts"]
        name = f"{cell['family']} / {cell['consequence_class']}"
        lines.append("")
        lines.append(f"== {name}{'  [POOLED: context only]' if cell['pooled'] else ''}")
        lines.append(f"   models {', '.join(cell['models'])}; fixtures "
                     f"{', '.join(cell['fixture_versions']) or '-'}")
        lines.append(f"   labeled {counts['labeled']} = validated {counts['validated']} "
                     f"(gold source {counts['gold_source_available']}) + unvalidated "
                     f"{counts['unvalidated']}; conflicts {counts['conflicts']}; "
                     f"no distribution {counts['no_distribution']}")
        held = cell["held_out"]
        lines.append(f"   held-out validated {held['validated']}: accuracy {_fmt(held['accuracy'])}, "
                     f"ECE {_fmt(held['expected_calibration_error'])}; unvalidated accuracy "
                     f"{_fmt(cell['unvalidated']['accuracy'])} (never used for a threshold)")
        for label, key in (("entropy bits", "entropy_bands"),
                           ("selected probability", "probability_bands")):
            lines.append(f"   by {label}:")
            for band in held[key]:
                lines.append(f"     ({_fmt(band['lower_exclusive'])}, {_fmt(band['upper_inclusive'])}] "
                             f"n={band['n']} acc={_fmt(band['accuracy'])} "
                             f"[{_fmt(band['accuracy_lower'])}, {_fmt(band['accuracy_upper'])}] "
                             f"mean_p={_fmt(band['mean_selected_probability'])} gap={_fmt(band['gap'])}")
        proposal = cell["proposal"]
        if proposal["status"] == "proposed":
            lines.append(f"   PROPOSED band: entropy <= {proposal['max_entropy_bits']} bits on "
                         f"{proposal['model']}; held-out n={proposal['held_out']['n']} "
                         f"acc={_fmt(proposal['held_out']['accuracy'])} lower="
                         f"{_fmt(proposal['held_out']['accuracy_lower'])} >= target "
                         f"{proposal['target_accuracy']}; coverage {_fmt(proposal['held_out']['coverage'])}")
        else:
            lines.append(f"   threshold: {proposal['status']}"
                         f"{' (' + proposal['reason'] + ')' if proposal.get('reason') else ''}"
                         f"{' - ' + proposal['detail'] if proposal.get('detail') else ''}")
    return "\n".join(lines)
