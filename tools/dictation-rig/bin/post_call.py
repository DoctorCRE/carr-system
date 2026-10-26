#!/usr/bin/env python3
"""Private Call Mode post-call processing.

This module has no send implementation. Deal Room supplies an exact,
short-lived context index; transcript, report and email body remain in 0600
files in the recording session. Headless Sonnet uses the Claude subscription
login; an injected command contract keeps tests offline. Advisory Jev item
checks in post_call_jev.py can never block the review pack.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import shlex
import subprocess
import sys
import tempfile
import urllib.parse
import fcntl
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable
from zoneinfo import ZoneInfo

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

SCHEMA_VERSION = 1
CONTEXT_FILE = "call-context.json"
REPORT_FILE = "post-call-report.json"
STATUS_FILE = "post-call.json"
DRAFTS_FILE = "outlook-drafts.json"
PUSH_FILE = "backend-push.json"
LOCK_FILE = ".post-call.lock"
OUTLOOK_URL_LIMIT = 1900
CALL_PHASES = ("pending", "research", "site_selection", "negotiation", "legal", "due_diligence", "closing")


def distiller_json_schema() -> dict[str, Any]:
    string_array = {"type": "array", "items": {"type": "string"}}

    def item(properties: dict[str, Any], required: list[str]) -> dict[str, Any]:
        return {
            "type": "object", "additionalProperties": False,
            "properties": properties, "required": required,
        }

    common = {
        "deal_id": {"type": "string"},
        "participant_ids": string_array,
        "evidence": {"type": "string"},
    }
    return {
        "type": "object", "additionalProperties": False,
        "properties": {
            "schema_version": {"type": "integer", "const": SCHEMA_VERSION},
            "report": item({
                "summary": {"type": "string"}, "decisions": string_array,
                "open_questions": string_array,
            }, ["summary", "decisions", "open_questions"]),
            "joe_tasks": {"type": "array", "items": item({
                **common, "title": {"type": "string"},
                "due_on": {"type": ["string", "null"], "format": "date"},
            }, ["title", "deal_id", "participant_ids", "evidence", "due_on"])},
            "dell_tasks": {"type": "array", "items": item({
                **common, "title": {"type": "string"},
                "due_on": {"type": ["string", "null"], "format": "date"},
            }, ["title", "deal_id", "participant_ids", "evidence", "due_on"])},
            "deal_updates": {"type": "array", "items": item({
                **common, "summary": {"type": "string"},
                "phase_move": {"anyOf": [{"type": "null"}, item({
                    "value": {"type": "string", "enum": list(CALL_PHASES)},
                    "started_on": {"type": ["string", "null"], "format": "date"},
                    "needs_review": {"type": ["string", "null"]},
                }, ["value", "started_on", "needs_review"])]},
                "next_step": {"anyOf": [{"type": "null"}, item({
                    "text": {"type": "string", "minLength": 1, "maxLength": 500},
                    "next_date": {"type": ["string", "null"], "format": "date"},
                    "needs_review": {"type": ["string", "null"]},
                }, ["text", "next_date", "needs_review"])]},
            }, ["deal_id", "summary", "participant_ids", "evidence", "next_step", "phase_move"])},
            "draft_proposals": {"type": "array", "items": item({
                **common, "recipient_party_id": {"type": "string"},
                "subject": {"type": "string"}, "body": {"type": "string"},
            }, ["recipient_party_id", "deal_id", "subject", "body", "participant_ids", "evidence"])},
            "review_questions": string_array,
        },
        "required": ["schema_version", "report", "joe_tasks", "dell_tasks",
                     "deal_updates", "draft_proposals", "review_questions"],
    }


class ContractError(ValueError):
    pass


def now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def read_json(path: Path) -> dict[str, Any] | None:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
        return value if isinstance(value, dict) else None
    except (OSError, json.JSONDecodeError):
        return None


def write_private(path: Path, value: dict[str, Any]) -> None:
    temp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    temp.write_text(json.dumps(value, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    temp.chmod(0o600)
    os.replace(temp, path)
    path.chmod(0o600)


def _strict_keys(value: dict[str, Any], required: set[str], optional: set[str] = set()) -> None:
    keys = set(value)
    if not required <= keys or keys - required - optional:
        missing = sorted(required - keys)
        unexpected = sorted(keys - required - optional)
        raise ContractError(
            f"schema fields do not match the exact contract (missing={missing}; unexpected={unexpected})"
        )


def _string(value: Any, field: str, allow_empty: bool = False) -> str:
    if not isinstance(value, str) or (not allow_empty and not value.strip()):
        raise ContractError(f"{field} must be a {'string' if allow_empty else 'non-empty string'}")
    return value


def validate_context(value: Any, session: str) -> dict[str, Any]:
    """Validate Deal Room's exact index; IDs are opaque and never fuzzy-matched."""
    if not isinstance(value, dict):
        raise ContractError("call context must be an object")
    _strict_keys(value, {"session", "workspace_kind", "generated_at", "deals"}, {"account_client_id"})
    if _string(value["session"], "session") != session:
        raise ContractError("context session must equal the local session")
    for field in ("workspace_kind", "generated_at"):
        _string(value[field], field)
    if "account_client_id" in value:
        _string(value["account_client_id"], "account_client_id")
    deals = value["deals"]
    if not isinstance(deals, list):
        raise ContractError("deals must be an array")
    deal_ids: set[str] = set()
    for deal in deals:
        if not isinstance(deal, dict):
            raise ContractError("deal must be an object")
        _strict_keys(deal, {"id", "name", "owner", "operating_state", "participants"})
        deal_id = _string(deal["id"], "deal.id")
        if deal_id in deal_ids:
            raise ContractError("duplicate deal.id is ambiguous")
        deal_ids.add(deal_id)
        for field in ("name", "owner", "operating_state"):
            _string(deal[field], f"deal.{field}", allow_empty=True)
        if not isinstance(deal["participants"], list):
            raise ContractError("deal.participants must be an array")
        party_ids: set[str] = set()
        for party in deal["participants"]:
            if not isinstance(party, dict):
                raise ContractError("participant must be an object")
            _strict_keys(party, {"party_id", "ref", "name", "email", "role"})
            party_id = _string(party["party_id"], "participant.party_id")
            if party_id in party_ids:
                raise ContractError("duplicate participant.party_id is ambiguous")
            party_ids.add(party_id)
            _string(party["ref"], "participant.ref")
            for field in ("name", "email", "role"):
                _string(party[field], f"participant.{field}", allow_empty=True)
    return value


def shape_record_layer_context(value: Any) -> Any:
    """Bring the record layer's get-call-context rows to the exact contract.

    The 2026-08-17 weekly call stalled in ``awaiting_context`` because this
    boundary refused every index the Deal Room could actually send: the record
    layer returns ``owner: null`` for an unowned deal, returns the partners'
    own ``lead`` rows (an actor, so party_id/ref/name/email are all null), and
    returns ``email: null`` for a party with no address on file.  Any one of
    those failed ``validate_context`` and nothing was stored.

    Only those three shapes are repaired, and nothing is inferred: a null text
    value becomes "", a participant without an exact party_id and ref is not a
    counterparty the distiller may address and is dropped, and one party named
    twice on a deal (two roles) is kept once with both roles.  IDs are never
    matched by name, deals are never added or removed, and every other field
    still goes through the strict validation that follows.
    """
    if not isinstance(value, dict) or not isinstance(value.get("deals"), list):
        return value
    shaped = dict(value)
    deals: list[Any] = []
    for deal in value["deals"]:
        if not isinstance(deal, dict):
            deals.append(deal)
            continue
        deal = dict(deal)
        for field in ("name", "owner", "operating_state"):
            if field in deal and deal[field] is None:
                deal[field] = ""
        participants = deal.get("participants")
        if isinstance(participants, list):
            kept: list[Any] = []
            by_party: dict[str, dict[str, Any]] = {}
            for party in participants:
                if not isinstance(party, dict):
                    kept.append(party)
                    continue
                party_id, ref = party.get("party_id"), party.get("ref")
                if party_id is None and ref is None:
                    continue
                party = dict(party)
                for field in ("name", "email", "role"):
                    if field in party and party[field] is None:
                        party[field] = ""
                if isinstance(party_id, str) and party_id in by_party:
                    first = by_party[party_id]
                    if first.get("ref") == party.get("ref"):
                        roles = [r for r in (first.get("role"), party.get("role")) if isinstance(r, str) and r]
                        first["role"] = ", ".join(dict.fromkeys(roles))
                        continue
                if isinstance(party_id, str):
                    by_party[party_id] = party
                kept.append(party)
            deal["participants"] = kept
        deals.append(deal)
    shaped["deals"] = deals
    return shaped


def store_context(session_dir: Path, context: dict[str, Any]) -> dict[str, Any]:
    context = validate_context(shape_record_layer_context(context), session_dir.name)
    old = read_json(session_dir / CONTEXT_FILE) or {}
    # Retain physical-channel labels written at recording start; only Deal Room's
    # exact index becomes available to the model, and it is consumed on success.
    stored: dict[str, Any] = {key: old[key] for key in ("schema", "mode", "started_at", "recorder", "speaker_labels", "speaker_method") if key in old}
    stored["call_context"] = context
    write_private(session_dir / CONTEXT_FILE, stored)
    return context


def context_from_session(session_dir: Path) -> dict[str, Any] | None:
    whole = read_json(session_dir / CONTEXT_FILE) or {}
    index = whole.get("call_context")
    try:
        return validate_context(index, session_dir.name)
    except ContractError:
        return None


def _word_count(value: str) -> int:
    return len(value.split())


def outlook_draft_url_length(to: str, subject: str, body: str) -> int:
    quote = urllib.parse.quote
    return len(f"mailto:{quote(to, safe='@,')}?subject={quote(subject)}&body={quote(body)}")


def _calendar_date_or_none(value: Any, field: str) -> None:
    if value is None:
        return
    try:
        valid = isinstance(value, str) and date.fromisoformat(value).isoformat() == value
    except ValueError:
        valid = False
    if not valid:
        raise ContractError(f"{field} must be a YYYY-MM-DD calendar date or null")


CALL_TIME_ZONE = "America/Chicago"
WEEKDAYS = ("monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday")
RELATIVE_DAY = re.compile(
    r"\b(day after tomorrow|tomorrow|today|next week|"
    r"(?:(?:next|this)\s+)?(?:" + "|".join(WEEKDAYS) + r"))\b", re.IGNORECASE,
)
CALENDAR_DAY = re.compile(
    r"\b(?:\d{4}-\d{2}-\d{2}|(?:January|February|March|April|May|June|July|August|"
    r"September|October|November|December)\s+\d{1,2})\b", re.IGNORECASE,
)


def recorded_call_date(session_dir: Path) -> str | None:
    stored = read_json(session_dir / CONTEXT_FILE) or {}
    metadata = read_json(session_dir / "meta.json") or {}
    for stamp in (stored.get("started_at"), metadata.get("started")):
        if not isinstance(stamp, str):
            continue
        try:
            instant = datetime.fromisoformat(stamp.replace("Z", "+00:00"))
        except ValueError:
            continue
        if instant.tzinfo is not None:
            return instant.astimezone(ZoneInfo(CALL_TIME_ZONE)).date().isoformat()
    return None


def _relative_phrases(evidence: str) -> list[str]:
    if CALENDAR_DAY.search(evidence):
        return []
    weekdays = "(?:" + "|".join(WEEKDAYS) + ")"
    if re.search(r"\b(?:last|previous|following)\s+" + weekdays +
                 r"\b|\b(?:days?|weeks?|months?)\s+(?:from|after|before)\b|\b" + weekdays +
                 r"\s+after next\b|\bthis time next week\b", evidence, re.IGNORECASE):
        return []
    phrases = [match.group().lower() for match in RELATIVE_DAY.finditer(evidence)
               if not re.match(r"['’]s\b", evidence[match.end():])
               or re.match(r"['’]s\s+lunch call\b", evidence[match.end():], re.IGNORECASE)
               and re.search(r"\bbefore\s+$", evidence[:match.start()], re.IGNORECASE)]
    if "next week" in phrases and any(phrase.split()[-1] in WEEKDAYS for phrase in phrases):
        phrases = ["next " + phrase.split()[-1] if phrase.split()[-1] in WEEKDAYS else phrase
                   for phrase in phrases if phrase != "next week"]
    return phrases


def _relative_due_date(evidence: str, transcript: dict[str, Any] | None, call_date: str | None) -> str | None:
    if transcript is None:
        return None
    spoken = " ".join(str(segment.get("text", "")) for segment in transcript.get("segments", [])
                      if isinstance(segment, dict))
    if not _words(evidence) or f" {_words(evidence)} " not in f" {_words(spoken)} ":
        return None
    if CALENDAR_DAY.search(evidence):
        return None
    phrases = _relative_phrases(evidence)
    if not phrases and re.search(r"\bbefore (?:the )?lunch call\b", evidence, re.IGNORECASE):
        lunch_anchors = [sentence for sentence in re.split(r"[.!?]", spoken)
                         if re.search(r"\blunch call\b", sentence, re.IGNORECASE)
                         and _words(sentence) != _words(evidence)
                         and (RELATIVE_DAY.search(sentence) or CALENDAR_DAY.search(sentence))]
        if lunch_anchors:
            anchored = [_relative_phrases(sentence) for sentence in lunch_anchors]
            if not all(anchored):
                return None
            phrases = [phrase for group in anchored for phrase in group]
        else:
            phrases = ["today"]
    if not phrases:
        return None
    if call_date is None:
        raise ContractError("A relative deadline requires the recorded call start date in America/Chicago")
    anchor = date.fromisoformat(call_date)
    resolved: set[date] = set()
    for phrase in phrases:
        if phrase in {"today", "tomorrow", "day after tomorrow"}:
            offset = {"today": 0, "tomorrow": 1, "day after tomorrow": 2}[phrase]
        elif phrase == "next week":
            offset = 7 - anchor.weekday()
        else:
            weekday = WEEKDAYS.index(phrase.split()[-1])
            offset = 7 - anchor.weekday() + weekday if phrase.startswith("next ") else (weekday - anchor.weekday()) % 7
        resolved.add(anchor + timedelta(days=offset))
    if len(resolved) != 1:
        raise ContractError("The evidence gives conflicting relative deadlines; clarify the deadline")
    return resolved.pop().isoformat()


UNSAID_REVIEW = "The model did not say whether this was settled."


def _review_note(change: dict[str, Any], field: str) -> None:
    # A missing flag fails closed: only an explicit null means plainly stated.
    note = change.setdefault("needs_review", UNSAID_REVIEW)
    if note is not None and not isinstance(note, str):
        raise ContractError(f"{field} must be a string or null")
    if isinstance(note, str) and not note.strip():
        change["needs_review"] = None


def _validate_distillation(value: Any, *, call_date: str | None = None,
                           transcript: dict[str, Any] | None = None) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ContractError("distiller output must be an object")
    _strict_keys(value, {"schema_version", "report", "joe_tasks", "dell_tasks", "deal_updates", "draft_proposals", "review_questions"})
    if value["schema_version"] != SCHEMA_VERSION:
        raise ContractError("unsupported distiller schema_version")
    report = value["report"]
    if not isinstance(report, dict):
        raise ContractError("report must be an object")
    _strict_keys(report, {"summary", "decisions", "open_questions"})
    _string(report["summary"], "report.summary", allow_empty=True)
    for key in ("decisions", "open_questions", "review_questions"):
        if not isinstance(value[key] if key == "review_questions" else report[key], list) or not all(isinstance(x, str) for x in (value[key] if key == "review_questions" else report[key])):
            raise ContractError(f"{key} must be an array of strings")
    for list_name, required in {
        "joe_tasks": {"title", "deal_id", "participant_ids", "evidence"},
        "dell_tasks": {"title", "deal_id", "participant_ids", "evidence"},
        "deal_updates": {"deal_id", "summary", "participant_ids", "evidence"},
        "draft_proposals": {"recipient_party_id", "deal_id", "subject", "body", "participant_ids", "evidence"},
    }.items():
        items = value[list_name]
        if not isinstance(items, list):
            raise ContractError(f"{list_name} must be an array")
        for item in items:
            if not isinstance(item, dict):
                raise ContractError(f"{list_name} item must be an object")
            is_action = list_name in {"joe_tasks", "dell_tasks"}
            _strict_keys(item, required, {"due_on"} if is_action else {"next_step", "phase_move"} if list_name == "deal_updates" else set())
            if is_action:
                _calendar_date_or_none(item.setdefault("due_on", None), f"{list_name}.due_on")
            step = item.get("next_step") if list_name == "deal_updates" else None
            if step is not None:
                if not isinstance(step, dict):
                    raise ContractError("deal_updates.next_step must be an object or null")
                _strict_keys(step, {"text", "next_date"}, {"needs_review"})
                _review_note(step, "deal_updates.next_step.needs_review")
                text = _string(step["text"], "deal_updates.next_step.text")
                if len(text) > 500:
                    raise ContractError("deal_updates.next_step.text exceeds 500 characters")
                _calendar_date_or_none(step["next_date"], "deal_updates.next_step.next_date")
            phase = item.get("phase_move") if list_name == "deal_updates" else None
            if phase is not None:
                if not isinstance(phase, dict):
                    raise ContractError("deal_updates.phase_move must be an object or null")
                _strict_keys(phase, {"value"}, {"started_on", "needs_review"})
                _review_note(phase, "deal_updates.phase_move.needs_review")
                if phase["value"] not in CALL_PHASES:
                    raise ContractError("deal_updates.phase_move.value must be a supported open phase")
                _calendar_date_or_none(phase.setdefault("started_on", None), "deal_updates.phase_move.started_on")
                if phase["started_on"] is not None and phase["started_on"] > date.today().isoformat():
                    phase["started_on"] = None
            for key in required - {"deal_id", "participant_ids"}:
                _string(item[key], f"{list_name}.{key}")
            if not isinstance(item["deal_id"], str) or not item["deal_id"].strip():
                raise ContractError(f"{list_name}.deal_id must be an exact non-empty string")
            if not isinstance(item["participant_ids"], list) or not all(isinstance(x, str) for x in item["participant_ids"]):
                raise ContractError(f"{list_name}.participant_ids must be string IDs")
            resolved = None
            if is_action or step is not None:
                resolved = _relative_due_date(item["evidence"], transcript, call_date)
                if resolved is not None:
                    if is_action:
                        item["due_on"] = resolved
                    elif step is not None:
                        step["next_date"] = resolved
            if _word_count(item["evidence"]) > 15:
                # Retain the quoted timing inside the remote 15-word ceiling.
                # Other evidence keeps its original leading words.
                words = item["evidence"].split()
                start = 0
                if resolved is not None:
                    timing = list(RELATIVE_DAY.finditer(item["evidence"]))
                    last = timing[-1] if timing else re.search(r"\bbefore (?:the )?lunch call\b", item["evidence"], re.IGNORECASE)
                    if last is not None:
                        end = last.end()
                        day_part = re.match(r"\s+(?:morning|afternoon|evening|night)\b", item["evidence"][end:], re.IGNORECASE)
                        if day_part:
                            end += day_part.end()
                        start = max(0, len(item["evidence"][:end].split()) - 15)
                item["evidence"] = " ".join(words[start:start + 15])
    return value


def _review_id(session: str, kind: str, number: int) -> str:
    return hashlib.sha256(f"{session}:{kind}:{number}".encode()).hexdigest()[:16]


def normalize_distillation(value: Any, context: dict[str, Any], session: str, *,
                           call_date: str | None = None, transcript: dict[str, Any] | None = None) -> dict[str, Any]:
    raw = _validate_distillation(value, call_date=call_date, transcript=transcript)
    deal_ids = {deal["id"] for deal in context["deals"]}
    parties_by_deal = {deal["id"]: {party["party_id"]: party for party in deal["participants"]} for deal in context["deals"]}
    review = [{"id": _review_id(session, "model", n), "question": q, "resolved": False} for n, q in enumerate(raw["review_questions"], 1)]

    def clean(items: list[dict[str, Any]], kind: str) -> list[dict[str, Any]]:
        good: list[dict[str, Any]] = []
        for number, item in enumerate(items, 1):
            bad: list[str] = []
            deal_id = item["deal_id"]
            if deal_id is not None and deal_id not in deal_ids:
                bad.append(f"unknown or ambiguous deal_id {deal_id!r}")
            attached = parties_by_deal.get(deal_id, {})
            unknown_parties = [party_id for party_id in item["participant_ids"] if party_id not in attached]
            if unknown_parties:
                bad.append("unknown or ambiguous participant_ids " + repr(unknown_parties))
            recipient = item.get("recipient_party_id")
            if recipient is not None and recipient not in attached:
                bad.append(f"unknown or ambiguous recipient_party_id {recipient!r}")
            if recipient is not None and recipient in attached and not attached[recipient].get("email"):
                bad.append(f"recipient_party_id {recipient!r} has no email")
            if recipient is not None and recipient in attached and attached[recipient].get("email"):
                if outlook_draft_url_length(attached[recipient]["email"], item.get("subject", ""), item.get("body", "")) > OUTLOOK_URL_LIMIT:
                    bad.append("email draft exceeds the local Outlook draft size limit; shorten it")
            if bad:
                review.append({"id": _review_id(session, kind, number), "question": f"{kind} #{number}: " + "; ".join(bad), "resolved": False})
            else:
                good.append(item)
        return good

    report = dict(raw["report"])
    result: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION, "session": session, "generated_at": now(), "report": report,
        "joe_tasks": clean(raw["joe_tasks"], "joe_task"), "dell_tasks": clean(raw["dell_tasks"], "dell_task"),
        "deal_updates": clean(raw["deal_updates"], "deal_update"), "draft_proposals": clean(raw["draft_proposals"], "draft_proposal"),
        "review_questions": review,
    }
    for index, draft in enumerate(result["draft_proposals"], 1):
        recipient = parties_by_deal[draft["deal_id"]].get(draft["recipient_party_id"])
        deal = next((deal for deal in context["deals"] if deal["id"] == draft["deal_id"]), None)
        # This private copy permits later approved draft creation after the
        # ephemeral index is consumed.  It is never in a backend candidate.
        draft["recipient_email"] = recipient["email"] if recipient else ""
        draft["recipient_name"] = recipient["name"] if recipient else ""
        draft["recipient_ref"] = recipient["ref"] if recipient else ""
        draft["deal_name"] = deal["name"] if deal else ""
        draft["draft_id"] = hashlib.sha256(f"{session}:{index}:{draft['recipient_party_id']}".encode()).hexdigest()[:16]
        draft["content_hash"] = hashlib.sha256(json.dumps({k: draft[k] for k in ("recipient_party_id", "subject", "body")}, sort_keys=True).encode()).hexdigest()
        draft["candidate_id"] = None
        draft["candidate_status"] = "unfiled"
    for list_name in ("joe_tasks", "dell_tasks", "deal_updates"):
        for item in result[list_name]:
            item["candidate_id"] = None
            item["candidate_status"] = "unfiled"
    return result


def command_distiller(request: dict[str, Any], command: str | None = None, runner: Callable[..., Any] = subprocess.run) -> dict[str, Any]:
    command = command or os.environ.get("CARR_POST_CALL_DISTILLER_COMMAND")
    if not command:
        raise RuntimeError("no local post-call distiller configured")
    argv = shlex.split(command)
    if not argv:
        raise RuntimeError("local post-call distiller command is empty")
    result = runner(argv, input=json.dumps(request), text=True, capture_output=True, shell=False, timeout=120, check=False)
    if result.returncode != 0:
        raise RuntimeError("local post-call distiller failed")
    try:
        parsed = json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        raise ContractError("local post-call distiller returned invalid JSON") from exc
    if not isinstance(parsed, dict):
        raise ContractError("local post-call distiller returned non-object JSON")
    return parsed


def _distiller_instruction() -> str:
    """Require exact context IDs and transcript-supported proposals."""
    return (
        "You distill a Joe-and-Dell weekly commercial-real-estate call. "
        "Use only exact deal_id and party_id values present in CONTEXT; never copy display names into ID fields. "
        "When Joe says he/I/we will do something, create a joe_tasks item. "
        "When Dell says he/I/we will do something, create a dell_tasks item. "
        "For each task, resolve spoken deadlines into due_on as YYYY-MM-DD against CALL_DATE in TIME_ZONE, "
        "the recording's America/Chicago calendar date, never the processing date. "
        "Bare weekdays (including Friday morning) mean the next occurrence including the call day. "
        "Next weekday means that weekday in the following calendar week. Next week means the following Monday. "
        "Today, tomorrow and day after tomorrow are offsets of zero, one and two calendar days. "
        "Before the lunch call means CALL_DATE unless the transcript dates that lunch call differently. "
        "Preserve an explicitly stated calendar date and year. Include the spoken timing in the evidence quote. "
        "Use null only when no time was stated. If a stated deadline cannot be resolved or CALL_DATE is missing, "
        "add a review question rather than invent a date. "
        "A deal status statement creates a deal_updates item with next_step and phase_move null. "
        "Only an explicitly agreed phase change sets phase_move to an object with value and started_on. "
        "started_on is the YYYY-MM-DD the partners say the new phase began, only when they state that calendar date "
        "with its year; otherwise null. "
        "Use exactly one of pending, research, site_selection, negotiation, legal, due_diligence, closing. "
        "Do not infer a phase from a general status update. Closure, loss and invoicing become review_questions. "
        "Only an explicitly agreed replacement for the deal card's next step sets next_step to an object with text and next_date. "
        "Use the exact agreed step text. Apply the task date rules to next_date, using null for missing or ambiguous dates. "
        "Do not turn every assigned task into a replacement next step. "
        "Next steps and phase moves are applied to the cards automatically unless needs_review is set. "
        "Set needs_review to null only when Joe and Dell plainly stated the change for that exact deal and nobody "
        "disputed it. Otherwise set needs_review to one short sentence saying what a partner must decide; it then "
        "waits as an approval card. When they disagree or it is unclear which deal they mean, also add a review question. "
        "A deal update's evidence must be the exact words that name the deal and state its change; when the phase and "
        "the next step were said in different sentences, give them as two deal_updates for the same deal. "
        "A request to tell, update, email, or draft a message to an exact attached participant creates a draft_proposals item. "
        "Each item includes a transcript evidence phrase of at most 15 words and only attached participant_ids (an array of exact party_id strings, [] if none). "
        "Draft bodies are concise, professional, factual, and under 900 characters. "
        "Unknown or ambiguous references (including a recipient with no attached party_id) become review_questions; never invent an ID. "
        "Return ONLY one JSON object matching EXACTLY this shape, with these exact top-level and nested key names "
        "(no renaming, no extra keys, empty arrays/strings when there is nothing to report):\n"
        '{"schema_version": 1, '
        '"report": {"summary": "<string>", "decisions": ["<string>", ...], "open_questions": ["<string>", ...]}, '
        '"joe_tasks": [{"title": "<string>", "due_on": null, "deal_id": "<exact deal id>", "participant_ids": ["<id>", ...], "evidence": "<<=15 words>"}], '
        '"dell_tasks": [{"title": "<string>", "due_on": null, "deal_id": "<exact deal id>", "participant_ids": ["<id>", ...], "evidence": "<<=15 words>"}], '
        '"deal_updates": [{"deal_id": "<exact deal id>", "summary": "<string>", "next_step": null or {"text": "<string>", "next_date": null, "needs_review": null}, "phase_move": null or {"value": "<phase>", "started_on": null, "needs_review": null}, "participant_ids": ["<id>", ...], "evidence": "<<=15 words>"}], '
        '"draft_proposals": [{"recipient_party_id": "<exact party id>", "deal_id": "<exact deal id>", "subject": "<string>", "body": "<string>", "participant_ids": ["<id>", ...], "evidence": "<<=15 words>"}], '
        '"review_questions": ["<string>", ...]}\n'
        "If there is nothing for a section, use an empty array. Never omit schema_version or report. "
        "Return only that JSON object and no transcript field."
    )


def default_distiller(request: dict[str, Any]) -> dict[str, Any]:
    """Use subscription Sonnet unless an explicit command is injected."""
    command = os.environ.get("CARR_POST_CALL_DISTILLER_COMMAND")
    if command:
        return command_distiller(request, command=command)
    from lib.headless_tasks import API_ENV

    argv = [
        "claude", "-p", "--model", "sonnet", "--no-session-persistence",
        "--tools", "", "--setting-sources", "", "--strict-mcp-config",
        "--disable-slash-commands", "--exclude-dynamic-system-prompt-sections",
        "--output-format", "json", "--json-schema", json.dumps(distiller_json_schema()),
        "--system-prompt", _distiller_instruction(),
    ]
    child_env = {k: v for k, v in os.environ.items() if k not in API_ENV and k != "CLAUDECODE"}
    prompt = json.dumps({"CONTEXT": request["context"], "TRANSCRIPT": request["transcript"],
                         "CALL_DATE": request.get("call_date"), "TIME_ZONE": CALL_TIME_ZONE}, ensure_ascii=False)
    try:
        with tempfile.TemporaryDirectory() as empty:
            done = subprocess.run(argv, input=prompt, text=True, capture_output=True,
                                  cwd=empty, env=child_env, timeout=180, check=False)
    except (OSError, subprocess.SubprocessError) as exc:
        raise RuntimeError("headless Sonnet could not complete the post-call review pack") from exc
    if done.returncode:
        raise RuntimeError(f"headless Sonnet exited with code {done.returncode}")
    try:
        reply = json.loads(done.stdout)
    except json.JSONDecodeError as exc:
        raise ContractError("headless Sonnet returned invalid JSON") from exc
    if not isinstance(reply, dict) or reply.get("is_error") or not isinstance(reply.get("structured_output"), dict):
        raise ContractError("headless Sonnet returned no structured review pack")
    return reply["structured_output"]


def run_session(session_dir: Path) -> dict[str, Any]:
    """Keep model dependencies out of the Accessibility-granted companion.

    A ready report is filed and its confident changes applied right away
    through the capture bridge, instead of waiting up to five minutes for
    capture-poll's next sweep while the partners look at the Deal Room.
    """
    entry = Path(__file__).resolve()
    python = os.environ.get("CARR_POST_CALL_PYTHON") or entry.parents[3] / ".venv" / "bin" / "python3"
    result = subprocess.run(
        [str(python), str(entry), str(session_dir)],
        capture_output=True, text=True, check=True, timeout=1800,
    )
    status = json.loads(result.stdout)
    if status.get("state") == "ready_review":
        subprocess.run(
            [sys.executable, str(entry.with_name("capture-bridge.py")), "poll", str(session_dir)],
            capture_output=True, text=True, timeout=600, check=False,
        )
    return status


def process_session(session_dir: Path, distiller: Callable[[dict[str, Any]], dict[str, Any]] = default_distiller) -> dict[str, Any]:
    existing = read_json(session_dir / STATUS_FILE)
    if (session_dir / REPORT_FILE).exists() and existing is not None:
        return existing
    lock_path = session_dir / LOCK_FILE
    lock_path.touch(exist_ok=True)
    lock_path.chmod(0o600)
    with lock_path.open("a+") as lock:
        try:
            fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise RuntimeError("post-call distillation already active for this session") from exc
        return _process_session_locked(session_dir, distiller)


def _process_session_locked(session_dir: Path, distiller: Callable[[dict[str, Any]], dict[str, Any]]) -> dict[str, Any]:
    context = context_from_session(session_dir)
    if context is None:
        status = {"schema_version": SCHEMA_VERSION, "session": session_dir.name, "state": "awaiting_context", "updated_at": now()}
        write_private(session_dir / STATUS_FILE, status)
        return status
    transcript = read_json(session_dir / "transcript.json")
    if transcript is None:
        raise ContractError("transcript.json is required before post-call processing")
    call_date = recorded_call_date(session_dir)
    request = {"schema_version": SCHEMA_VERSION, "session": session_dir.name, "context": context,
               "transcript": transcript, "call_date": call_date}
    try:
        result = normalize_distillation(distiller(request), context, session_dir.name,
                                       call_date=call_date, transcript=transcript)
    except (RuntimeError, ContractError) as exc:
        status = {"schema_version": SCHEMA_VERSION, "session": session_dir.name, "state": "blocked", "updated_at": now(), "reason": str(exc)}
        write_private(session_dir / STATUS_FILE, status)
        return status
    try:
        import post_call_jev

        stored_context = read_json(session_dir / CONTEXT_FILE) or {}
        jev_context = {**context, "speaker_labels": stored_context.get("speaker_labels"),
                       "local_partner": stored_context.get("recorder")}
        result = post_call_jev.check_distillation(result, jev_context, transcript, client=_jev_fixture_client())
    except Exception:
        pass  # Jev never blocks the pack; a change without a passing deal check stays a card.
    result = mark_automatic(result, transcript, context)
    write_private(session_dir / REPORT_FILE, result)
    # Context is single-use: no later caller gets a searchable customer index.
    stored = read_json(session_dir / CONTEXT_FILE) or {}
    stored.pop("call_context", None)
    write_private(session_dir / CONTEXT_FILE, stored)
    status = {"schema_version": SCHEMA_VERSION, "session": session_dir.name, "state": "ready_review", "updated_at": now(), "review_questions": len(result["review_questions"]), "candidate_counts": {k: len(result[k]) for k in ("joe_tasks", "dell_tasks", "deal_updates", "draft_proposals")}}
    write_private(session_dir / STATUS_FILE, status)
    return status


def _report_candidates(report: dict[str, Any]) -> list[dict[str, Any]]:
    items = [item for key in ("joe_tasks", "dell_tasks", "deal_updates", "draft_proposals") for item in report.get(key, [])]
    return items + [update[field] for update in report.get("deal_updates", [])
                    for field in ("next_step", "phase_move") if update.get(field) is not None]


def apply_candidate_statuses(session_dir: Path, statuses: dict[str, str]) -> dict[str, Any]:
    """Persist CARR's human candidate dispositions; no backend write occurs here."""
    report = read_json(session_dir / REPORT_FILE)
    if report is None:
        raise ContractError("post-call report is not ready")
    known: dict[str, dict[str, Any]] = {}
    for item in _report_candidates(report):
        candidate_id = item.get("candidate_id")
        if isinstance(candidate_id, str) and candidate_id:
            known[candidate_id] = item
    if not statuses or any(key not in known or value not in {"confirmed", "skipped", "rejected"} for key, value in statuses.items()):
        raise ContractError("candidate dispositions must name known candidate IDs")
    for candidate_id, status in statuses.items():
        known[candidate_id]["candidate_status"] = status
    write_private(session_dir / REPORT_FILE, report)
    return {"updated": len(statuses)}


def retention_ready(session_dir: Path, report_sha256: str) -> dict[str, Any] | None:
    """Return a purge-safe filing receipt only after every candidate is final."""
    report = read_json(session_dir / REPORT_FILE)
    if report is None or not isinstance(report_sha256, str) or re.fullmatch(r"[0-9a-fA-F]{64}", report_sha256) is None:
        return None
    items = _report_candidates(report)
    if any(item.get("candidate_status") not in {"confirmed", "skipped", "rejected"} for item in items):
        return None
    digest = hashlib.sha256(json.dumps(report, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
    report.update({"backend_report_sha256": report_sha256, "aggregate_report_hash": digest, "filed_at": now()})
    write_private(session_dir / REPORT_FILE, report)
    status = read_json(session_dir / STATUS_FILE) or {"schema_version": SCHEMA_VERSION, "session": session_dir.name}
    status.update({"state": "filed", "updated_at": now(), "pending_items": 0, "aggregate_report_hash": digest})
    write_private(session_dir / STATUS_FILE, status)
    return {"aggregate_report_hash": digest, "backend_report_sha256": report_sha256, "pending_items": 0}


AUTOMATIC_FIELDS = ("next_step", "phase_move")


def _words(value: Any) -> str:
    return " ".join(re.findall(r"[a-z0-9]+", str(value or "").lower()))


# Words too common in deal names to tie a quote to one deal.
_GENERIC_NAME_WORDS = {"the", "and", "for", "llc", "inc", "medical", "dental", "clinic", "health",
                       "office", "building", "lease", "expansion", "relocation", "center", "suite"}
_STOP_WORDS = {"the", "and", "for", "with", "from", "that", "this", "will", "about", "into", "next", "step"}
MIN_EVIDENCE_WORDS = 4


def _jev_fixture_client() -> Any:
    """The rehearsal's offline Jev stand-in, a seam like the distiller command; None means live Jev."""
    path = os.environ.get("CARR_POST_CALL_JEV_FIXTURE")
    if not path:
        return None
    import importlib.util
    spec = importlib.util.spec_from_file_location("post_call_jev_fixture", path)
    if spec is None or spec.loader is None:
        raise ContractError("CARR_POST_CALL_JEV_FIXTURE is not a Python file")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.client()


def _supports(evidence: str, field: str, value: dict[str, Any], deal_name: str) -> str | None:
    """Why the quote does not show this deal and this value, or None when it does."""
    said = set(evidence.split())
    if len(evidence.split()) < MIN_EVIDENCE_WORDS:
        return "The quoted evidence is too short to show the change."
    names = {word for word in _words(deal_name).split() if len(word) >= 3 and word not in _GENERIC_NAME_WORDS}
    if not names & said:
        return "The quoted evidence does not name this deal."
    if field == "phase_move":
        wanted = set(_words(str(value.get("value", "")).replace("_", " ")).split())
        if not wanted or not wanted <= said:
            return "The quoted evidence does not state this phase."
    else:
        wanted = {word for word in _words(value.get("text")).split() if len(word) >= 4 and word not in _STOP_WORDS}
        if not wanted or len(wanted & said) < min(2, len(wanted)):
            return "The quoted evidence does not state this next step."
    return None


def _automatic_hold(update: dict[str, Any], field: str, report: dict[str, Any], spoken: str,
                    deal_name: str) -> str | None:
    """Why one stated change must stay a card for Joe or Dell, or None to apply it.

    Joe's 2026-10-26 ruling: confident changes apply themselves; anything the
    model cannot place is kicked back as an approval card. Confident here is
    deterministic and checkable, never the model's own say-so.
    """
    value: dict[str, Any] = update[field]
    if value.get("needs_review"):
        return str(value["needs_review"])
    if field == "next_step" and not value.get("next_date"):
        return "No date was stated for this next step."
    evidence = _words(update.get("evidence"))
    if not evidence or f" {evidence} " not in f" {spoken} ":
        return "The quoted evidence was not found word for word in the transcript."
    unsupported = _supports(evidence, field, value, deal_name)
    if unsupported:
        return unsupported
    same = [other for other in report.get("deal_updates", [])
            if other.get("deal_id") == update.get("deal_id") and other.get(field) is not None]
    if len(same) > 1:
        return "The call stated more than one version of this change."
    # Jev's RIGHT DEAL answers "unclear which deal". Its RIGHT DETAILS judges
    # the update's summary, not this change, so it stays advisory here.
    deal_check = (update.get("checks") or {}).get("deal")
    if not isinstance(deal_check, dict):
        return "The automatic deal check did not run."
    if deal_check.get("pass") is True:
        return None
    if deal_check.get("pass") is False and deal_check.get("value") not in (None, "", "none"):
        return "The automatic check matched these words to a different deal."
    return "The automatic check could not confirm which deal this is."


def mark_automatic(result: dict[str, Any], transcript: dict[str, Any], context: dict[str, Any]) -> dict[str, Any]:
    """Label each stated next step and phase move automatic or for review."""
    segments = transcript.get("segments") if isinstance(transcript, dict) else None
    spoken = _words(" ".join(str(seg.get("text", "")) for seg in segments or [] if isinstance(seg, dict)))
    names = {deal.get("id"): str(deal.get("name") or "") for deal in context.get("deals", []) if isinstance(deal, dict)}
    for update in result.get("deal_updates", []):
        for field in AUTOMATIC_FIELDS:
            value = update.get(field)
            if isinstance(value, dict):
                hold = _automatic_hold(update, field, result, spoken, names.get(update.get("deal_id"), ""))
                value["apply"] = "review" if hold else "automatic"
                value["review_reason"] = hold
    return result


def automatic_candidates(session_dir: Path) -> list[dict[str, Any]]:
    """Filed, still-pending changes this call may apply without a confirm."""
    report = read_json(session_dir / REPORT_FILE) or {}
    return [update[field] for update in report.get("deal_updates", []) for field in AUTOMATIC_FIELDS
            if isinstance(update.get(field), dict) and update[field].get("apply") == "automatic"
            and update[field].get("candidate_id") and update[field].get("candidate_status") == "pending"]


def record_automatic(session_dir: Path, outcomes: dict[str, str | None]) -> dict[str, Any]:
    """Store which changes applied (None) and which stayed cards (the reason)."""
    report = read_json(session_dir / REPORT_FILE)
    if report is None:
        raise ContractError("post-call report is not ready")
    changes = [update[field] for update in report.get("deal_updates", []) for field in AUTOMATIC_FIELDS
               if isinstance(update.get(field), dict)]
    for value in changes:
        if value.get("candidate_id") not in outcomes:
            continue
        reason = outcomes[value["candidate_id"]]
        if reason is None:
            value.update({"candidate_status": "confirmed", "applied_automatically": True})
        else:
            value.update({"apply": "review", "review_reason": reason})
    report["automatic"] = {"finished_at": now(),
                           "applied": sum(1 for value in changes if value.get("applied_automatically")),
                           "kept_for_review": sum(1 for value in changes if value.get("apply") == "review")}
    write_private(session_dir / REPORT_FILE, report)
    return report["automatic"]


def report_for_deal_room(session_dir: Path) -> dict[str, Any]:
    report = read_json(session_dir / REPORT_FILE)
    status = read_json(session_dir / STATUS_FILE) or {"state": "awaiting_context", "session": session_dir.name}
    if report is None:
        return {"status": status}
    # Do not mutate the private report while presenting it.  Outlook creation
    # is a local, idempotent side effect with its own receipt; fold only that
    # receipt into the loopback response so a reload cannot offer the same
    # draft as though it had never been made.
    visible = json.loads(json.dumps(report))
    for update in visible.get("deal_updates", []):
        phase = update.get("phase_move")
        if phase is not None:
            verb = "Phase moved to" if phase.get("applied_automatically") else "Move phase to"
            since = f" (started {phase['started_on']})" if phase.get("started_on") else ""
            update["summary"] += f"\n{verb}: {phase['value']}{since}"
        step = update.get("next_step")
        if step is not None:
            next_date = step["next_date"] or "not set (clears the current date)"
            verb = "Next step set to" if step.get("applied_automatically") else "Replace next step with"
            update["summary"] += f"\n{verb}: {step['text']}\nNext date: {next_date}"
    receipts = (read_json(session_dir / DRAFTS_FILE) or {}).get("drafts", {})
    if isinstance(receipts, dict):
        for draft in visible.get("draft_proposals", []):
            receipt = receipts.get(draft.get("draft_id"))
            if isinstance(receipt, dict) and receipt.get("content_hash") == draft.get("content_hash"):
                draft["status"] = "created"
                draft["created_at"] = receipt.get("created_at")
    return {"status": status, "report": visible}


def resolve_question(session_dir: Path, question_id: str, resolution: str) -> dict[str, Any]:
    report = read_json(session_dir / REPORT_FILE)
    if report is None:
        raise ContractError("post-call report is not ready")
    _string(resolution, "resolution")
    for question in report.get("review_questions", []):
        if question.get("id") == question_id:
            question["resolved"] = True
            question["resolution"] = resolution
            write_private(session_dir / REPORT_FILE, report)
            return {"question_id": question_id, "resolved": True}
    raise ContractError("unknown question id")


def _candidate_binding(list_name: str, index: int, remote: str) -> dict[str, Any]:
    return {"list_name": list_name, "index": index, "remote": remote}


def sanitized_candidates(session_dir: Path) -> dict[str, Any]:
    """Return exact Worker payloads plus local-only report bindings.

    The returned post_call and legacy lists are intentionally separate because
    they have separate Worker validation routes.  Neither list contains a
    transcript, recipient email, or email body.  ``bindings`` never leaves the
    local capture bridge and lets the opaque Worker IDs be written back to the
    correct private report item.
    """
    report = read_json(session_dir / REPORT_FILE)
    if report is None:
        raise ContractError("post-call report is not ready")
    post_call_items: list[dict[str, Any]] = []
    legacy_items: list[dict[str, Any]] = []
    bindings: list[dict[str, Any]] = []
    for owner in ("joe", "dell"):
        list_name = f"{owner}_tasks"
        for index, task in enumerate(report.get(list_name, [])):
            if task.get("candidate_id"):
                continue
            post_call_items.append({
                "kind": "assigned_action", "deal_id": task["deal_id"], "assignee": owner,
                "action": task["title"], "due_on": task.get("due_on"), "evidence_quote": task["evidence"],
                "confidence": 0.5,
            })
            bindings.append(_candidate_binding(list_name, index, "post_call"))
    for index, update in enumerate(report.get("deal_updates", [])):
        if not update.get("candidate_id"):
            legacy_items.append({
                "kind": "activity",
                "payload": {"ref": update["deal_id"], "kind": "note", "summary": update["summary"]},
                "evidence_quote": update["evidence"], "confidence": 0.5,
            })
            bindings.append(_candidate_binding("deal_updates", index, "legacy"))
        step = update.get("next_step")
        if step is not None and not step.get("candidate_id"):
            legacy_items.append({
                "kind": "next_step",
                "payload": {"deal": update["deal_id"], "text": step["text"], "next_date": step["next_date"]},
                "evidence_quote": update["evidence"], "confidence": 0.5,
            })
            bindings.append({**_candidate_binding("deal_updates", index, "legacy"), "field": "next_step"})
        phase = update.get("phase_move")
        if phase is not None and not phase.get("candidate_id"):
            payload = {"deal": update["deal_id"], "field": "phase", "value": phase["value"]}
            if phase.get("started_on"):
                payload["started_on"] = phase["started_on"]
            legacy_items.append({
                "kind": "phase_move",
                "payload": payload,
                "evidence_quote": update["evidence"], "confidence": 0.5,
            })
            bindings.append({**_candidate_binding("deal_updates", index, "legacy"), "field": "phase_move"})
    for index, draft in enumerate(report.get("draft_proposals", [])):
        if draft.get("candidate_id"):
            continue
        recipient_ref = draft.get("recipient_ref")
        if not isinstance(recipient_ref, str) or not recipient_ref:
            raise ContractError("email draft proposal is missing its exact recipient ref")
        post_call_items.append({
            "kind": "email_draft", "deal_id": draft["deal_id"],
            "recipient_party_id": draft["recipient_party_id"], "recipient_ref": recipient_ref,
            "subject": draft["subject"], "body_sha256": draft["content_hash"],
            "evidence_quote": draft["evidence"], "confidence": 0.5,
        })
        bindings.append(_candidate_binding("draft_proposals", index, "post_call"))
    return {"schema_version": SCHEMA_VERSION, "session": session_dir.name,
            "post_call_items": post_call_items, "legacy_items": legacy_items,
            "bindings": bindings}


def apply_candidate_ids(session_dir: Path, bindings: list[dict[str, Any]], candidate_ids: list[str]) -> dict[str, Any]:
    """Atomically attach opaque Worker IDs to their private report items."""
    if len(bindings) != len(candidate_ids) or not all(isinstance(item, str) and item for item in candidate_ids):
        raise ContractError("candidate receipt does not match local proposal count")
    report = read_json(session_dir / REPORT_FILE)
    if report is None:
        raise ContractError("post-call report is not ready")
    for binding, candidate_id in zip(bindings, candidate_ids):
        if not isinstance(binding, dict):
            raise ContractError("candidate binding is invalid")
        list_name = binding.get("list_name")
        index = binding.get("index")
        if list_name not in {"joe_tasks", "dell_tasks", "deal_updates", "draft_proposals"} or not isinstance(index, int):
            raise ContractError("candidate binding is invalid")
        items = report.get(list_name)
        if not isinstance(items, list) or index < 0 or index >= len(items):
            raise ContractError("candidate binding points outside the report")
        target = items[index]
        field = binding.get("field")
        if field is not None:
            if list_name != "deal_updates" or field not in {"next_step", "phase_move"} or not isinstance(target.get(field), dict):
                raise ContractError("candidate binding is invalid")
            target = target[field]
        if target.get("candidate_id") not in {None, candidate_id}:
            raise ContractError("candidate binding conflicts with an existing receipt")
        target["candidate_id"] = candidate_id
        target["candidate_status"] = "pending"
    write_private(session_dir / REPORT_FILE, report)
    return {"updated": len(candidate_ids)}


def report_sha256(session_dir: Path) -> str:
    report = read_json(session_dir / REPORT_FILE)
    if report is None:
        raise ContractError("post-call report is not ready")
    return hashlib.sha256(json.dumps(report, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def create_outlook_draft(session_dir: Path, draft_id: str, approved_content_hash: str, runner: Callable[..., Any] = subprocess.run) -> dict[str, Any]:
    report = read_json(session_dir / REPORT_FILE)
    if report is None:
        raise ContractError("post-call report is not ready")
    draft = next((item for item in report["draft_proposals"] if item["draft_id"] == draft_id), None)
    if draft is None or approved_content_hash != draft["content_hash"]:
        raise ContractError("explicit approval must name the current draft content hash")
    if not draft.get("candidate_id") or draft.get("candidate_status") != "confirmed":
        raise ContractError("Outlook draft requires an already-confirmed CARR email candidate")
    drafts = read_json(session_dir / DRAFTS_FILE) or {"drafts": {}}
    existing = drafts["drafts"].get(draft_id)
    if isinstance(existing, dict) and existing.get("content_hash") == approved_content_hash:
        return {"draft_id": draft_id, "content_hash": approved_content_hash, "idempotent": True}
    # The address is retained only in the private report so consuming the index
    # never leaks recipient data into the candidate push payload.
    if not draft.get("recipient_email"):
        raise ContractError("recipient email is unavailable for this proposal")
    if outlook_draft_url_length(draft["recipient_email"], draft["subject"], draft["body"]) > OUTLOOK_URL_LIMIT:
        raise ContractError("email draft exceeds the local Outlook draft size limit")
    repo_root = Path(__file__).resolve().parents[3]
    command = [str(repo_root / "bin" / "outlook-draft.py")]
    payload = {"to": draft["recipient_email"], "subject": draft["subject"], "body": draft["body"]}
    result = runner(command, input=json.dumps(payload), text=True, capture_output=True, timeout=45, check=False)
    if result.returncode != 0:
        raise RuntimeError("draft-only creator failed")
    drafts["drafts"][draft_id] = {"content_hash": approved_content_hash, "created_at": now()}
    write_private(session_dir / DRAFTS_FILE, drafts)
    return {"draft_id": draft_id, "content_hash": approved_content_hash, "idempotent": False}


if __name__ == "__main__":
    from credential_env import load_carr_tokens

    for name, value in load_carr_tokens(
        ["TYPESAFE_API_KEY"], path=Path.home() / ".config/carr/typesafe.env",
    ).items():
        os.environ.setdefault(name, value)
    print(json.dumps(process_session(Path(sys.argv[1]).resolve())))
