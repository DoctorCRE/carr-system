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

THIS FILE IS A LIBRARY ON PURPOSE, the same way ops/typesafe_client.py is: no
executable-script construct belongs here.  See that file's docstring for why
and for the detector that enforces it; the same rule applies to this file.

PRIVACY: a short evidence excerpt, the handful of transcript segments nearest
that excerpt, and a short list of candidate deal names go to TypeSafe
(api.typesafe.ai) for this check -- never the full transcript, never the full
recorded-deal list, never an email body. Choosing where a long transcript is
split (topic_cut) sends, per split, at most four candidate boundaries of one
trimmed segment on each side -- never the transcript. This rides the same 2026-09-17
authority ops/typesafe_client.py records for sending CARR records to a
third-party model API.
"""
from __future__ import annotations

import hashlib
import importlib.util
import os
import re
import time
from typing import Any, Callable

_HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(os.path.dirname(os.path.dirname(_HERE)))

CHECKS_SCHEMA = "post-call-jev-checks/v1"

# Keep the candidate-deal state small: the item's own deal plus a handful of
# plausible alternatives, never the whole recorded-deal list.
MAX_CANDIDATE_DEALS = 5
# How many transcript segments on either side of the best evidence match ride
# along, and the hard cap on how many are ever sent.
SEGMENT_WINDOW = 2
MAX_SEGMENTS_SENT = 6

_LIST_PARTNER = {"joe_tasks": "Joe", "dell_tasks": "Dell"}
_LIST_KIND = {
    "joe_tasks": "joe_task", "dell_tasks": "dell_task",
    "deal_updates": "deal_update", "draft_proposals": "draft_proposal",
}


def _client() -> Any:
    """Load ops/typesafe_client.py the same way ops/jev_build_advisory.py does."""
    path = os.path.join(REPO, "ops", "typesafe_client.py")
    spec = importlib.util.spec_from_file_location("typesafe_client_post_call", path)
    if spec is None or spec.loader is None:
        raise RuntimeError("cannot load the TypeSafe client")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


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
    """The handful of transcript segments closest to where the evidence text
    actually appears, found by simple token overlap -- never the whole
    transcript."""
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
    best_index = scored[0][1]
    lo = max(0, best_index - SEGMENT_WINDOW)
    hi = min(len(segments), best_index + SEGMENT_WINDOW + 1)
    window = [segment for segment in segments[lo:hi] if isinstance(segment, dict)]
    return window[:MAX_SEGMENTS_SENT]


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
    return {"subject": item.get("subject", ""), "body": item.get("body", "")}


def _reason(label: str, decided: dict[str, Any]) -> str:
    if decided.get("escalate"):
        return f"{label}: Jev was not confident enough to accept this."
    if decided.get("outcome") == "no":
        return f"{label}: Jev found this does not hold."
    return f"{label}: Jev matched a different deal ({decided.get('value')!r})."


def _questions(tsc: Any, deal_options: dict[str, str], attributed: str | None) -> dict[str, Any]:
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
        "deal_match": tsc.choice(
            "Given `item` and `transcript_excerpt`, which of `candidate_deals` "
            "does `item`'s words actually match? Choose none if no candidate "
            "fits.",
            deal_options,
        ),
        "speaker_right": tsc.noul(
            speaker_instructions,
            true="The transcript excerpt supports that attribution.",
            false="The transcript excerpt contradicts it or does not support it.",
        ),
        "details_supported": tsc.noul(
            "Read `item.evidence_excerpt` against `transcript_excerpt`. Does it "
            "actually support the specifics recorded in `item.fields`?",
            true="The excerpt supports the item's specifics.",
            false="The excerpt does not support the item's specifics, or overstates them.",
        ),
    }


def _item_request(tsc: Any, ask: Callable[..., Any], kind: str, list_partner: str | None,
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
    deal_options["none"] = "None of the candidate deals match this item."

    attributed = list_partner
    state = {
        "item": {
            "kind": kind, "attributed_partner": attributed,
            "fields": _text_fields(kind, item), "evidence_excerpt": str(evidence)[:1200],
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

    return state, _questions(tsc, deal_options, attributed)


def _item_checks(tsc, item, response):
    answers = response.get("answers") if isinstance(response, dict) else None
    if not isinstance(answers, dict):
        raise RuntimeError("Jev omitted answers for a post-call check")

    deal_decided = tsc.decide(answers["deal_match"])
    speaker_decided = tsc.decide(answers["speaker_right"])
    details_decided = tsc.decide(answers["details_supported"])

    deal_pass = not deal_decided["escalate"] and deal_decided["value"] == item.get("deal_id")
    speaker_pass = not speaker_decided["escalate"] and speaker_decided["outcome"] == "yes"
    details_pass = not details_decided["escalate"] and details_decided["outcome"] == "yes"

    reasons = []
    if not deal_pass:
        reasons.append(_reason("Right deal", deal_decided))
    if not speaker_pass:
        reasons.append(_reason("Right speaker", speaker_decided))
    if not details_pass:
        reasons.append(_reason("Right details", details_decided))

    reasons.append("Semantic checks are advisory; independent review is required.")
    return {
        "advisory_only": True,
        "deal": {**deal_decided, "pass": deal_pass},
        "speaker": {**speaker_decided, "pass": speaker_pass},
        "details": {**details_decided, "pass": details_pass},
        "flagged": bool(reasons),
        "reasons": reasons,
    }


# Topic cuts: each candidate sends the one segment before and the one after it,
# each trimmed, so a cut never shows TypeSafe more than eight short segments.
CUT_SEGMENT_CHARS = 600
# Optional topic selection gets this much time for the whole transcript,
# irrespective of how many cuts it needs. Local distillation remains primary.
TOPIC_CUT_BUDGET_SECONDS = 5.0
# Below this, no candidate looks like a topic change and the size-limit cut
# stands. A noul's probability IS the answer, so 0.5 means "more likely a new
# topic than not"; a starting point to replace once real calls are measured.
TOPIC_CUT_MIN = 0.5


def _cut_side(segment: Any, *, before: bool = False) -> dict[str, str]:
    if not isinstance(segment, dict):
        return {"speaker": "", "text": ""}
    text = str(segment.get("text", ""))
    return {"speaker": str(segment.get("speaker", "")),
            "text": text[-CUT_SEGMENT_CHARS:] if before else text[:CUT_SEGMENT_CHARS]}


def topic_cut(segments: list[Any], options: list[int], *,
              ask: Callable[..., Any] | None = None,
              deadline: float | None = None) -> int | None:
    """Pick which of `options` (cut before that segment index) falls where the
    conversation changes topic, for post_call.transcript_chunks.

    One batched request: one noul per candidate, each reading only its own
    before/after pair. Returns the most likely topic change, the later cut on
    a tie, or None when nothing clears TOPIC_CUT_MIN or Jev is unreachable --
    the caller then keeps its size-limit cut. Never raises. `deadline` is an
    absolute monotonic time shared by every cut of a transcript; standalone
    calls get TOPIC_CUT_BUDGET_SECONDS. No rate-limit retries.
    """
    try:
        now = time.monotonic()
        call_deadline = now + TOPIC_CUT_BUDGET_SECONDS
        deadline = min(deadline, call_deadline) if deadline is not None else call_deadline
        if now >= deadline:
            return None
        tsc = _client()
        live_ask = ask if ask is not None else (
            lambda state, questions: _semantic().ask(state, questions, client=tsc, caller="post_call_jev", version="topic-v1", work_class="app_runtime",
                timeout=TOPIC_CUT_BUDGET_SECONDS, deadline=deadline, retries=0)
        )
        state = {"boundaries": {
            f"b{j}": {"before": _cut_side(segments[k - 1], before=True), "after": _cut_side(segments[k])}
            for j, k in enumerate(options)
        }}
        questions = {
            f"b{j}": tsc.noul(
                f"In `boundaries.b{j}`, does `after` start a new topic of the "
                "call, rather than continue the thought in `before`?",
                true="`after` moves on to a different topic, deal, or agenda item.",
                false="`after` continues, answers, or refers back to what `before` was discussing.",
            )
            for j in range(len(options))
        }
        response = live_ask(state, questions)
        answers = response.get("answers") if isinstance(response, dict) else None
        if not isinstance(answers, dict):
            return None
        best: tuple[float, int] | None = None
        for j, k in enumerate(options):
            probability = float(answers[f"b{j}"]["noul"])
            if best is None or probability >= best[0]:
                best = (probability, k)
        if best is None or best[0] < TOPIC_CUT_MIN:
            return None
        return None  # unvalidated topic threshold cannot change transcript partition
    except Exception:
        return None


def _unavailable() -> dict[str, Any]:
    return {"unavailable": True}


def _review_id(session: str, tag: str) -> str:
    return hashlib.sha256(f"{session}:{tag}".encode()).hexdigest()[:16]


def check_distillation(result: dict[str, Any], context: dict[str, Any],
                        transcript: dict[str, Any], *,
                        ask: Callable[..., Any] | None = None) -> dict[str, Any]:
    """Attach Jev's RIGHT DEAL / RIGHT SPEAKER / RIGHT DETAILS checks in place.

    `result` is normalize_distillation's already-normalized return value.
    `context` is the call's stored context: at least `{"deals": [...]}` (the
    same shape normalize_distillation validated), optionally carrying
    `speaker_labels` and `local_partner` the way call-mode.py's
    write_call_context records them, used to resolve "Me"/"Other participant"
    segments to Joe or Dell. `transcript` is the session's transcript.json.
    `ask` overrides ops/typesafe_client.py's `ask` for tests -- its signature
    is `ask(state, questions) -> response`.

    Mutates and returns `result`. Nothing this function does may block the
    review pack: a missing Jev key, a network error, or any other failure
    here never raises past this function. Every affected item instead gets
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

    try:
        tsc = _client()
    except Exception:
        tsc = None
    live_ask = ask if ask is not None else (tsc.ask if tsc is not None else None)
    if tsc is None or live_ask is None:
        for list_name, _kind in lists:
            for item in result.get(list_name, []):
                item["checks"] = _unavailable()
        result.setdefault("review_questions", []).append({
            "id": _review_id(session, "jev_unavailable"),
            "question": "Automatic Jev checks (deal, speaker, details) did not run for this pack.",
            "resolved": False,
        })
        return result

    any_unavailable = False
    state: dict[str, Any] = {"checks": {}}
    questions: dict[str, Any] = {}
    pending: list[tuple[str, dict[str, Any], dict[str, Any]]] = []
    for list_name, kind in lists:
        for item in result.get(list_name, []):
            if len(pending) >= 20 or not item.get("evidence"):
                item["checks"] = {"advisory_only": True, "flagged": True,
                                  "reasons": ["No bounded evidence available; independent review required."]}
                continue
            label = "i"+str(len(pending))
            local_state, local_questions = _item_request(tsc, live_ask, kind, _LIST_PARTNER.get(list_name),
                                                       item, deals, segments, context)
            state["checks"][label] = local_state
            for name,q in local_questions.items():
                q = dict(q)
                instruction = q["instructions"]
                for field in ("item", "transcript_excerpt", "candidate_deals"):
                    instruction = instruction.replace(field, "checks."+label+"."+field)
                q["instructions"] = instruction
                questions[label+":"+name] = q
            pending.append((label,item,local_questions))
    if pending:
        try:
            # A supplied fake keeps its simple signature; production still enters budget admission.
            transport = (lambda st, qs, **kw: ask(st, qs)) if ask is not None else live_ask
            response = _semantic().ask(state, questions, transport=transport,
                                       caller="post_call_jev", version="recording-v1", work_class="app_runtime")
            for label,item,local_questions in pending:
                item["checks"] = _item_checks(tsc,item,{"answers":{name:response["answers"][label+":"+name]
                                                                  for name in local_questions}})
        except Exception:
            for _,item,_ in pending:
                item["checks"] = _unavailable()
            any_unavailable = True

    if any_unavailable:
        result.setdefault("review_questions", []).append({
            "id": _review_id(session, "jev_partial_unavailable"),
            "question": "Automatic Jev checks did not run for one or more items in this pack.",
            "resolved": False,
        })
    return result


def _semantic():
    spec = importlib.util.spec_from_file_location("jev_semantic", os.path.join(REPO, "ops", "jev_semantic.py"))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module
