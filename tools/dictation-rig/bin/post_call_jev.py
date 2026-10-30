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
from typing import Any

from typesafe_sdk import Choice, Noul, RetryPolicy, TypeSafeClient, TypeSafeError

BUDGET_LEDGER = Path.home() / 'Recordings' / '.jev-budget.json'

CHECKS_SCHEMA = "post-call-jev-checks/v1"

# Keep the candidate-deal state small: the item's own deal plus a handful of
# plausible alternatives, never the whole recorded-deal list.
MAX_CANDIDATE_DEALS = 5
# The evidence quote is at most 15 words; this caps what a malformed one sends.
MAX_QUOTE_CHARS = 300
# A Choice answer below this confidence is not trusted to confirm the deal.
DEAL_CONFIDENCE_GATE = 0.55
# Items are judged one call each, at most this many in flight at once.
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


def _evidence_segments(evidence: str, segments: list[Any]) -> list[dict[str, Any]]:
    """The one transcript segment where the evidence text actually appears,
    found by simple token overlap -- never the whole transcript."""
    ev_tokens = _tokens(evidence)
    if not ev_tokens or not segments:
        return []
    scored: list[tuple[int, int]] = []
    for index, segment in enumerate(segments):
        if not isinstance(segment, dict):
            continue
        overlap = len(ev_tokens & _tokens(segment.get("text")))
        if overlap:
            scored.append((overlap, index))
    if not scored:
        return []
    scored.sort(key=lambda pair: (-pair[0], pair[1]))
    return [segments[scored[0][1]]]


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
        "details_supported": Noul(
            instructions="Read `item.evidence_excerpt` against `transcript_excerpt`. Does it "
            "actually support the specifics recorded in `item.fields`?",
            criteria={"true": "The excerpt supports the item's specifics.",
            "false": "The excerpt does not support the item's specifics, or overstates them."},
        ),
    }


def _item_request(kind: str, list_partner: str | None,
                 item: dict[str, Any], deals: list[Any], segments: list[Any],
                 context: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
    evidence = item.get("evidence", "")
    matched = _evidence_segments(evidence, segments)
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
            "fields": _text_fields(kind, item), "evidence_excerpt": str(evidence)[:MAX_QUOTE_CHARS],
        },
        "transcript_excerpt": [
            {
                "speaker": segment.get("speaker", ""),
                "partner": _resolve_partner(segment.get("speaker"), context),
                "text": str(segment.get("text", ""))[:600],
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
    details_pass = details.noul > 0.5
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
    if not details_pass:
        reasons.append("Right details: the excerpt does not support the item's specifics.")
    return {
        "advisory_only": True,
        "deal": {"value": deal.choice, "confidence": deal.confidence, "pass": deal_pass},
        "speaker": {"value": speaker.noul, "pass": speaker_pass},
        "details": {"value": details.noul, "pass": details_pass},
        "flagged": bool(reasons),
        "reasons": reasons,
    }


def _unavailable() -> dict[str, Any]:
    return {"unavailable": True}


def check_distillation(result: dict[str, Any], context: dict[str, Any],
                        transcript: dict[str, Any], *,
                        client: Any = None, budget_ledger: Path | None = None) -> dict[str, Any]:
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
    `checks: {"unavailable": True}` and one review_questions entry records
    that the automatic checks did not run.
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
    pending: list[tuple[dict[str, Any], dict[str, Any], dict[str, Any]]] = []
    for list_name, kind in lists:
        for item in result.get(list_name, []):
            if resuming and not (item.get("checks") or {}).get("reason"):
                continue
            if not item.get("evidence"):
                item["checks"] = {"advisory_only": True, "flagged": True,
                                  "reasons": ["No bounded evidence available; independent review required."]}
                continue
            local_state, local_questions = _item_request(kind, _LIST_PARTNER.get(list_name),
                                                       item, deals, segments, context)
            pending.append((item, local_state, local_questions))
    if pending:
        def judge(entry: tuple[dict[str, Any], dict[str, Any], dict[str, Any]]) -> None:
            item, local_state, local_questions = entry
            try:
                call_budget.reserve(budget_ledger if budget_ledger is not None else BUDGET_LEDGER)
                with nullcontext(client) if client is not None else TypeSafeClient(model=call_budget.MODEL) as sdk:
                    response = sdk.system_one(state=local_state, questions=local_questions,
                                              model=call_budget.MODEL, retry=RetryPolicy(max_retries=0))
                item["checks"] = _item_checks(item, {name: response.answers[name]
                                                    for name in local_questions})
            except call_budget.BudgetHold as exc:
                item["checks"] = {"unavailable": True, "reason": str(exc)}
            except TypeSafeError:
                item["checks"] = _unavailable()

        with ThreadPoolExecutor(max_workers=MAX_CONCURRENT_CALLS) as pool:
            list(pool.map(judge, pending))

    holds = sorted({item['checks']['reason'] for item, _, _ in pending if item['checks'].get('reason')})
    if holds:
        result['checks_hold'] = ' '.join(holds)
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
                " ".join(holds),
            "resolved": False,
        }
        if question is None:
            questions.append(replacement)
        else:
            question.update(replacement)
    elif question is not None:
        question.update(resolved=True, resolution="Automatic Jev checks resumed.")
    return result
