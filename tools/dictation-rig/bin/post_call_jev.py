"""Jev checks for post-call review-pack items before Joe or Dell ever sees them.

Decision 008d682a (Joe, 2026-09-23): every draft item in joe_tasks,
dell_tasks, deal_updates, and draft_proposals passes three Jev checks before
it reaches the Deal Room review pack --

  RIGHT DEAL     -- which recorded deal the item's words actually match.
  RIGHT SPEAKER  -- whether the item's attribution matches who actually said
                    it; for joe_tasks/dell_tasks specifically, did Joe or
                    Dell say it.
  RIGHT DETAILS  -- whether the quoted evidence excerpt supports the item's
                    specifics.

A failed check FLAGS the item -- it is never silently dropped.  Joe and Dell
still approve or reject every candidate by hand; this module only adds a
visible signal ahead of that approval.

PRIVACY: the evidence quote, the one transcript segment holding it, the
item's short title, summary or draft subject, and a short list of candidate
deal names go to TypeSafe (api.typesafe.ai) for this check -- never the full
transcript, never the full recorded-deal list, never a draft or email body.
"""
from __future__ import annotations

from post_call import clear_checks_hold, jev_question_id
from pathlib import Path
import call_budget
from concurrent.futures import ThreadPoolExecutor
from contextlib import nullcontext
import re
import hashlib
import json
from typing import Any

from typesafe_sdk import Choice, Noul, RetryPolicy, TypeSafeClient

BUDGET_LEDGER = Path.home() / 'Recordings' / '.jev-budget.json'

CHECKS_SCHEMA = "post-call-jev-checks/v1"

# Keep the candidate-deal state small: the item's own deal plus a handful of
# plausible alternatives, never the whole recorded-deal list.
MAX_CANDIDATE_DEALS = 5
# The evidence quote is at most 15 words; this caps what a malformed one sends.
MAX_QUOTE_CHARS = 300
MAX_SOURCE_CHARS = 4096
MAX_REQUEST_CHARS = 8192
MAX_CLAIM_CHARS = 1000
MAX_CLAIMS_PER_ITEM = 16
# A Choice answer below this confidence is not trusted to confirm the deal.
DEAL_CONFIDENCE_GATE = 0.55
# Claims are judged one call each, at most this many in flight at once.
MAX_CONCURRENT_CALLS = 6

_LIST_PARTNER = {"joe_tasks": "Joe", "dell_tasks": "Dell"}
_LIST_KIND = {
    "joe_tasks": "joe_task", "dell_tasks": "dell_task",
    "deal_updates": "deal_update", "draft_proposals": "draft_proposal",
}


def _tokens(text: Any) -> set[str]:
    return set(re.findall(r"[a-z0-9]{3,}", str(text or "").lower()))


def _other_partner(local_partner: Any) -> str | None:
    if local_partner == "Joe":
        return "Dell"
    if local_partner == "Dell":
        return "Joe"
    return None


def _resolve_partner(speaker: Any, context: dict[str, Any]) -> str | None:
    """Map one transcript segment's speaker label to Joe, Dell, or None.

    Segments already carry "Joe"/"Dell" directly when call-mode.py's
    labels_for paired the weekly call; other modes leave the generic
    "Me"/"Other participant" channel labels in place, resolved here through
    the session's own recorded speaker_labels and local_partner (never a
    guess, never a voiceprint).
    """
    if speaker in ("Joe", "Dell"):
        return speaker
    speaker_labels = context.get("speaker_labels")
    if not isinstance(speaker_labels, dict):
        speaker_labels = {}
    local_partner = context.get("local_partner")
    mic_label = speaker_labels.get("mic") or "Me"
    system_label = speaker_labels.get("system") or "Other participant"
    speaker = str(speaker or "")
    if speaker == mic_label or speaker.startswith(f"{mic_label} "):
        return local_partner if local_partner in ("Joe", "Dell") else None
    if speaker == system_label or speaker.startswith(f"{system_label} "):
        return _other_partner(local_partner)
    return None


def _normalized(text: Any) -> str:
    return " ".join(re.findall(r"\w+", str(text or "").casefold()))


def _evidence_segments(evidence: str, segments: list[Any]) -> list[dict[str, Any]]:
    quote = _normalized(evidence)
    if not quote:
        return []
    valid = [s for s in segments if isinstance(s, dict)]
    exact = [s for s in valid if " " + quote + " " in " " + _normalized(s.get("text")) + " "]
    if exact:
        return exact[:1]
    scored = sorted(valid, key=lambda s: -len(_tokens(evidence) & _tokens(s.get("text"))))
    return scored[:1] if scored and _tokens(evidence) & _tokens(scored[0].get("text")) else []


def _source_spans(item, segments, ledger):
    if ledger is None and not item.get("fact_ids"):
        return None
    facts = {f["fact_id"]: f for f in (ledger or {}).get("facts", [])}
    spans = []
    ids = item.get("fact_ids") or []
    if not ids or any(i not in facts for i in ids):
        return []
    try:
        for fact_id in ids:
            for span in facts[fact_id]["source_spans"]:
                index = span["segment_index"]
                if type(index) is not int or not 0 <= index < len(segments):
                    return []
                segment = segments[index]
                text = segment["text"]
                start, end = span["char_start"], span["char_end"]
                if (span["source_id"] != f"segment-{index}" or
                        hashlib.sha256(text.encode()).hexdigest() != span["segment_sha256"] or
                        type(start) is not int or type(end) is not int or not 0 <= start < end <= len(text)):
                    return []
                spans.append({**segment, "text": text[start:end]})
    except (KeyError, TypeError, AttributeError):
        return []
    return spans


def _claim_items(kind, item):
    fields = _text_fields(kind, item)
    if kind in ("phase_move", "next_step"):
        return [item]
    key, text = next(iter(fields.items()))
    return [{**item, key: sentence} for sentence in re.split(r"(?<=[.!?])\s+", str(text)) if sentence.strip()]


def _insufficient():
    return {"advisory_only": True, "flagged": True, "evidence_status": "insufficient",
            "evidence_chars": 0, "reasons": ["Insufficient bounded source evidence; review this claim manually."]}


def _aggregate(checks):
    result = dict(checks[0])
    result["claims"] = checks
    result["flagged"] = any(c.get("flagged") or c.get("unavailable") for c in checks)
    result["reasons"] = list(dict.fromkeys(r for c in checks for r in c.get("reasons", [])))
    result["evidence_chars"] = sum(c.get("evidence_chars", 0) for c in checks)
    result["request_chars"] = sum(c.get("request_chars", 0) for c in checks)
    for key in ("deal", "speaker", "details"):
        if key in result:
            result[key] = {**result[key], "pass": all(c.get(key, {}).get("pass", False) for c in checks)}
    holds = sorted({c["reason"] for c in checks if c.get("reason")})
    if holds:
        result.update(unavailable=True, reason=" ".join(holds))
    if any(c.get("unavailable") for c in checks):
        result["unavailable"] = True
    statuses = {c.get("evidence_status") for c in checks}
    result["evidence_status"] = ("contradicted" if "contradicted" in statuses else
                                 "insufficient" if "insufficient" in statuses else
                                 "supported" if statuses == {"supported"} else "unavailable")
    return result


def _candidate_deals(item_deal_id: Any, deals: list[Any],
                      matched_segments: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """The item's chosen deal plus up to MAX_CANDIDATE_DEALS - 1 plausible
    alternatives, ranked by simple name/segment-text token overlap."""
    by_id = {deal.get("id"): deal for deal in deals if isinstance(deal, dict)}
    text_tokens: set[str] = set()
    for segment in matched_segments:
        text_tokens |= _tokens(segment.get("text"))
    scored: list[tuple[int, dict[str, Any]]] = []
    for deal in deals:
        if not isinstance(deal, dict):
            continue
        scored.append((len(_tokens(deal.get("name")) & text_tokens), deal))
    scored.sort(key=lambda pair: -pair[0])
    candidates: list[dict[str, Any]] = []
    chosen = by_id.get(item_deal_id)
    if isinstance(chosen, dict):
        candidates.append(chosen)
    seen_ids = {c.get("id") for c in candidates}
    for _, deal in scored:
        if len(candidates) >= MAX_CANDIDATE_DEALS:
            break
        if deal.get("id") not in seen_ids:
            candidates.append(deal)
            seen_ids.add(deal.get("id"))
    return candidates


def _text_fields(kind: str, item: dict[str, Any]) -> dict[str, str]:
    if kind in ("joe_task", "dell_task"):
        return {"title": item.get("title", "")}
    if kind == "phase_move":
        return {"value": item.get("value", ""), "started_on": item.get("started_on", "")}
    if kind == "next_step":
        return {"text": item.get("text", ""), "next_date": item.get("next_date", "")}
    if kind == "deal_update":
        return {"summary": item.get("summary", "")}
    return {"subject": item.get("subject", "")}


def _questions(deal_options: dict[str, str], attributed: str | None) -> dict[str, Any]:
    if attributed:
        speaker_instructions = (
            f"Do `transcript_excerpt` show that {attributed} actually said or "
            "committed to `item`, rather than the other participant or neither "
            "one?"
        )
    else:
        speaker_instructions = (
            "Do `transcript_excerpt` show that a call participant actually said "
            "or committed to `item`, rather than `item` being unsupported by "
            "the excerpt?"
        )
    return {
        "deal_match": Choice(
            instructions="Given `item` and `transcript_excerpt`, which of `candidate_deals` "
            "does `item`'s words match best?",
            criteria=deal_options,
        ),
        "deal_in_candidates": Noul(
            instructions="Given `item` and `transcript_excerpt`, does any of "
            "`candidate_deals` actually match `item`'s words?",
            criteria={"true": "At least one candidate deal matches this item.",
            "false": "None of the candidate deals match this item."},
        ),
        "speaker_right": Noul(
            instructions=speaker_instructions,
            criteria={"true": "The transcript excerpt supports that attribution.",
            "false": "The transcript excerpt contradicts it or does not support it."},
        ),
        "evidence_sufficient": Noul(
            instructions="Does the targeted source contain enough information to decide every specific in item.fields? "
            "Answer false for omitted context, incomplete multi-turn facts, or an unlocatable claim. "
            "An explicit conflicting value is sufficient information to decide that a claim is wrong.",
            criteria={"true": "Enough evidence to confirm or contradict the claim.",
                      "false": "Insufficient evidence to decide the whole claim."},
        ),
        "details_supported": Noul(
            instructions="Read `item.evidence_excerpt` against `transcript_excerpt`. Does it "
            "actually support the specifics recorded in `item.fields`?",
            criteria={"true": "The excerpt supports the item's specifics.",
            "false": "The excerpt does not support the item's specifics, or overstates them."},
        ),
    }


def _item_request(kind: str, list_partner: str | None,
                 item: dict[str, Any], deals: list[Any], segments: list[Any],
                 context: dict[str, Any], source_spans=None) -> tuple[dict[str, Any], dict[str, Any]]:
    evidence = item.get("evidence", "")
    matched = _evidence_segments(evidence, segments) if source_spans is None else _evidence_segments(
        " ".join(str(v) for v in _text_fields(kind, item).values()), source_spans)
    if source_spans and not matched:
        matched = _evidence_segments(evidence, source_spans) or source_spans[:1]
    candidates = _candidate_deals(item.get("deal_id"), deals, matched)

    deal_options: dict[str, str] = {}
    for index, deal in enumerate(sorted(candidates, key=lambda d: str(d.get("id")))):
        raw_id = deal.get("id")
        deal_id: str = raw_id if isinstance(raw_id, str) and raw_id else f"deal-{index}"
        deal_options[deal_id] = str(deal.get("name") or deal_id)

    attributed = list_partner
    state = {
        "item": {
            "kind": kind, "attributed_partner": attributed,
            "fields": {k: v for k, v in _text_fields(kind, item).items() if v is not None},
            "evidence_excerpt": (" ".join(matched[0]["text"].split()[:15])
                                 if source_spans and matched else str(evidence))[:MAX_QUOTE_CHARS],
        },
        "transcript_excerpt": [
            {
                "speaker": segment.get("speaker", ""),
                "partner": _resolve_partner(segment.get("speaker"), context),
                "text": str(segment.get("text", "")),
            }
            for segment in matched
        ],
        "candidate_deals": [
            {"id": deal.get("id"), "name": deal.get("name", "")} for deal in candidates
        ],
    }

    return state, _questions(deal_options, attributed)


def _item_checks(item, answers):
    deal = answers["deal_match"]
    speaker = answers["speaker_right"]
    details = answers["details_supported"]
    in_candidates = answers["deal_in_candidates"]
    confident = deal.confidence >= DEAL_CONFIDENCE_GATE
    deal_pass = (deal.choice == item.get("deal_id") and confident
                 and in_candidates.noul > 0.5)
    # Noul is a yes/no probability: take the more likely answer.
    speaker_pass = speaker.noul > 0.5
    sufficient = answers["evidence_sufficient"].noul > 0.5
    details_pass = details.noul > 0.5 and sufficient
    reasons = []
    if not deal_pass:
        if in_candidates.noul <= 0.5:
            reasons.append("Right deal: Jev found no candidate deal that matches.")
        elif not confident:
            reasons.append("Right deal: Jev was not confident which deal this matches.")
        else:
            reasons.append(f"Right deal: Jev matched a different deal ({deal.choice!r}).")
    if not speaker_pass:
        reasons.append("Right speaker: the excerpt does not support the attribution.")
    if not sufficient:
        reasons.append("Right details: insufficient source evidence to decide this claim.")
    elif not details_pass:
        reasons.append("Right details: the source contradicts the item's specifics.")
    return {
        "advisory_only": True,
        "evidence_status": "supported" if details_pass else "contradicted" if sufficient else "insufficient",
        "deal": {"value": deal.choice, "confidence": deal.confidence, "pass": deal_pass},
        "speaker": {"value": speaker.noul, "pass": speaker_pass},
        "details": {"value": details.noul, "pass": details_pass},
        "flagged": bool(reasons),
        "reasons": reasons,
    }


def _unavailable(exc: Exception) -> dict[str, Any]:
    message = str(exc).lower()
    cause = "missing key" if "key" in message else "budget exhausted" if "budget" in message else "request failed"
    return {"unavailable": True, "flagged": True,
            "reasons": [f"Jev deal check unavailable ({type(exc).__name__}, {cause}); review this proposal manually."]}


def check_distillation(result: dict[str, Any], context: dict[str, Any],
                        transcript: dict[str, Any], *,
                        client: Any = None, budget_ledger: Path | None = None,
                        coverage_ledger: dict[str, Any] | None = None) -> dict[str, Any]:
    """Attach Jev's RIGHT DEAL / RIGHT SPEAKER / RIGHT DETAILS checks in place.

    `result` is normalize_distillation's already-normalized return value.
    `context` is the call's stored context: at least `{"deals": [...]}` (the
    same shape normalize_distillation validated), optionally carrying
    `speaker_labels` and `local_partner` the way call-mode.py's
    write_call_context records them, used to resolve "Me"/"Other participant"
    segments to Joe or Dell. `transcript` is the session's transcript.json.
    `client` may supply an SDK client for offline tests. Dollar reservations
    share the Studio ledger, including failed requests. A budget hold is visible
    in the narrative and holds automatic application for the entire pack.

    Mutates and returns `result`. A missing Jev key or SDK request failure never blocks the
    review pack. Every affected item instead gets
    `checks` with an unavailable flag and a safe exception-class reason.
    A review question records that the automatic checks did not run.
    """
    deals = context.get("deals") if isinstance(context, dict) else None
    deals = deals if isinstance(deals, list) else []
    segments = transcript.get("segments") if isinstance(transcript, dict) else None
    segments = segments if isinstance(segments, list) else []
    session = result.get("session") or "unknown-session"
    context = context if isinstance(context, dict) else {}

    lists = [(name, _LIST_KIND[name]) for name in
             ("joe_tasks", "dell_tasks", "deal_updates", "draft_proposals")]

    resuming = bool(result.get("checks_hold"))
    clear_checks_hold(result)
    pending = []
    targets = []
    parents = []
    for list_name, kind in lists:
        for parent in result.get(list_name, []):
            children = [(kind, parent)]
            if kind == "deal_update":
                parents.append(parent)
                children.extend((field, parent[field]) for field in ("phase_move", "next_step") if parent.get(field))
            for claim_kind, item in children:
                if resuming and item.get("checks") and not item["checks"].get("reason"):
                    continue
                outcomes: list[dict[str, Any]] = []
                targets.append((item, outcomes))
                claims = _claim_items(claim_kind, item)
                if not claims or len(claims) > MAX_CLAIMS_PER_ITEM:
                    outcomes.append(_insufficient())
                    continue
                source_spans = _source_spans(item, segments, coverage_ledger)
                for claim in claims:
                    request_item = {**claim, "deal_id": parent.get("deal_id"),
                                    "evidence": claim.get("evidence", parent.get("evidence", ""))}
                    local_state, local_questions = _item_request(claim_kind, _LIST_PARTNER.get(list_name),
                        request_item, deals, segments, context, source_spans)
                    excerpt = local_state["transcript_excerpt"]
                    fields = local_state["item"]["fields"]
                    if (not request_item.get("evidence") or not excerpt or
                            sum(len(s["text"]) for s in excerpt) > MAX_SOURCE_CHARS or
                            sum(len(str(v)) for v in fields.values()) > MAX_CLAIM_CHARS or
                            len(json.dumps(local_state, ensure_ascii=False)) > MAX_REQUEST_CHARS):
                        outcomes.append(_insufficient())
                        continue
                    pending.append((request_item, local_state, local_questions, outcomes))
    if pending:
        def judge(entry) -> None:
            item, local_state, local_questions, outcomes = entry
            try:
                call_budget.reserve(budget_ledger if budget_ledger is not None else BUDGET_LEDGER)
                with nullcontext(client) if client is not None else TypeSafeClient(model=call_budget.MODEL) as sdk:
                    response = sdk.system_one(state=local_state, questions=local_questions,
                                              model=call_budget.MODEL, retry=RetryPolicy(max_retries=0))
                checks = _item_checks(item, {name: response.answers[name] for name in local_questions})
                excerpt = local_state["transcript_excerpt"]
                checks["evidence_chars"] = sum(len(s["text"]) for s in excerpt)
                checks["request_chars"] = len(json.dumps(local_state, ensure_ascii=False))
                if local_state["item"]["attributed_partner"] and any(s["partner"] is None for s in excerpt):
                    checks["speaker"]["pass"] = False
                    checks["flagged"] = True
                    checks["reasons"].append("Right speaker: the source speaker is unresolved.")
                outcomes.append(checks)
            except call_budget.BudgetHold as exc:
                outcomes.append({"unavailable": True, "reason": str(exc)})
            except Exception as exc:
                if resuming:
                    raise
                outcomes.append(_unavailable(exc))

        with ThreadPoolExecutor(max_workers=MAX_CONCURRENT_CALLS) as pool:
            list(pool.map(judge, pending))

    for item, outcomes in targets:
        item["checks"] = _aggregate(outcomes)
    for parent in parents:
        if not any(item is parent or any(item is parent.get(f) for f in ("phase_move", "next_step"))
                   for item, _ in targets):
            continue
        children = [parent[field]["checks"] for field in ("phase_move", "next_step")
                    if parent.get(field) and parent[field].get("checks")]
        if children:
            parent["checks"] = _aggregate([parent["checks"], *children])
    holds = sorted({item['checks']['reason'] for item, _ in targets if item['checks'].get('reason')})
    if holds:
        result['checks_hold'] = ' '.join(holds)
        result['checks_hold_day'] = call_budget.budget_day()
        result.setdefault('report', {}).setdefault('open_questions', []).append(result['checks_hold'])
    any_unavailable = any((item.get("checks") or {}).get("unavailable")
                          for list_name, _ in lists for item in result.get(list_name, []))
    questions = result.setdefault("review_questions", [])
    question_id = jev_question_id(session)
    question = next((q for q in questions if q.get("id") == question_id), None)
    if any_unavailable:
        replacement = {
            "id": question_id,
            "question": "Automatic Jev checks did not run for one or more items in this pack. " +
                " ".join(holds + sorted({reason for item, _ in targets
                                         if item["checks"].get("unavailable")
                                         for reason in item["checks"].get("reasons", [])})),
            "resolved": False,
        }
        if question is None:
            questions.append(replacement)
        else:
            question.update(replacement)
    elif question is not None:
        question.update(resolved=True, resolution="Automatic Jev checks resumed.")
    return result
