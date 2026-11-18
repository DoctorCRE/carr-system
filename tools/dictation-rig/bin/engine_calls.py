"""Call capture's side of the engine's doors 19 (the call), 21 (its suggestions) and 18 (next steps).

The doors are fixed in the engine repository's docs/new-database.md, "Doors the old
system calls". The bridge calls them only when the capture config sets engine_calls;
otherwise nothing here runs and the call's suggestions go to the capture Worker as before.

The call is one activity about every deal the report names, with the report's summary,
decisions and open questions. Each deal update's sentence is a note about its deal. Each task and each stated next step or phase move becomes a
suggestion on that call. A next step the report marked automatic, held to the bridge's
confidence bar, is saved as a next step instead; one the engine refuses becomes a
suggestion with the reason kept for Call Mode. Phase moves are always suggestions: a job
cannot read a deal's phase, which door 16 needs as `from`, and the suggestion door reads it
itself. Email drafts have no door and stay with the capture Worker.

Keys derive from the session and the item, so a crashed run's retry converges. A 5xx or a
lost connection is retried next run with the same key, and a refusal of the caller itself
holds the session; any other 4xx is final and kept on the item.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
import json
from pathlib import Path
import re
from typing import Any, Callable, Dict, Iterator, List, Optional, Tuple
import uuid

import post_call
from lib import engine_mail

JOB = "call_capture"
PROPOSALS = "/jobs/proposals"
TASK_LISTS = ("joe_tasks", "dell_tasks")
PARTNER = re.compile(r"[a-z][a-z0-9_]{0,63}")

Connect = Callable[[str], engine_mail.Door]


@dataclass(frozen=True)
class Move:
    item: Dict[str, Any]
    key: str
    path: str
    body: Dict[str, Any]
    fallback: Optional[Dict[str, Any]] = None
    answer: str = "proposal_id"


def connector(config: Dict[str, Any]) -> Optional[Connect]:
    """None while engine_calls is off; the partner's /jobs door when on. ValueError when on but unusable."""
    if config.get("engine_calls", False) is False:
        return None
    if config["engine_calls"] is not True:
        raise ValueError("engine_calls must be true or false")
    credentials = config.get("engine_credentials")
    if not isinstance(credentials, str) or not Path(credentials).is_absolute() or str(Path(credentials)) != credentials:
        raise ValueError("engine_credentials must be a normalized absolute path when engine_calls is true")
    return lambda partner: engine_mail.connect(Path(credentials), partner, job=JOB)


def partner(session_dir: Path) -> Optional[str]:
    """The partner whose phone import or Call Mode recorded the session."""
    phone = post_call.read_json(session_dir / "phone.json") or {}
    context = post_call.read_json(session_dir / post_call.CONTEXT_FILE) or {}
    slug = str(phone.get("owner") or context.get("recorder") or "").lower()
    return slug if PARTNER.fullmatch(slug) else None


def key(session: str, *parts: str) -> str:
    return str(uuid.uuid5(uuid.NAMESPACE_URL, "|".join(("engine", JOB, session, *parts))))


def happened_at(session_dir: Path, report: Dict[str, Any]) -> str:
    started = (post_call.read_json(session_dir / "meta.json") or {}).get("started")
    for value in (started, report.get("generated_at")):
        try:
            return datetime.fromisoformat(str(value).replace("Z", "+00:00")).isoformat()
        except ValueError:
            continue
    raise post_call.ContractError("the call has no recorded start time")


def summary(report: Dict[str, Any]) -> str:
    """The summary, then decisions and open questions as lists, as the engine's call copy reads them."""
    narrative = report.get("report") or {}
    lists = ["\n".join(f"- {line}" for line in narrative.get(name) or [])
             for name in ("decisions", "open_questions")]
    return "\n\n".join(part for part in (str(narrative.get("summary") or "").strip(), *lists) if part)


def call(session_dir: Path, report: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """The call's door 19 request, or None when it names no deal."""
    deals = dict.fromkeys(item["deal_id"] for name in (*TASK_LISTS, "deal_updates", "draft_proposals")
                          for item in report.get(name, []) if item.get("deal_id"))
    if not deals:
        return None
    request = {"idempotency_key": key(session_dir.name, "call"), "kind": "call",
               "happened_at": happened_at(session_dir, report), "about": [{"deal_id": d} for d in deals]}
    text = summary(report)
    return {**request, "summary": text} if text else request


def moves(session_dir: Path, report: Dict[str, Any]) -> Iterator[Move]:
    """Each note and suggestion the Worker does not already hold, with its door and request."""
    session = session_dir.name

    def proposal(name: str, kind: str, deal_id: str, evidence: str, what: Optional[str] = None,
                 due_on: Optional[str] = None) -> Dict[str, Any]:
        body = {"idempotency_key": key(session, name, "proposal"), "kind": kind, "deal_id": deal_id,
                "evidence": evidence}
        return {**body, **({"what": what} if what else {}), **({"due_on": due_on} if due_on else {})}

    for list_name in TASK_LISTS:
        for index, task in enumerate(report.get(list_name, [])):
            name = f"{list_name}:{index}"
            if not task.get("candidate_id"):
                yield Move(task, name, PROPOSALS, proposal(
                    name, "next_step", task["deal_id"], task["evidence"], task["title"], task.get("due_on")))
    for index, update in enumerate(report.get("deal_updates", [])):
        if not update.get("candidate_id"):
            name = f"deal_updates:{index}"
            note = {"idempotency_key": key(session, name, "note"), "kind": "note",
                    "happened_at": happened_at(session_dir, report), "about": [{"deal_id": update["deal_id"]}]}
            text = str(update.get("summary") or "").strip()
            yield Move(update, name, "/jobs/activities", {**note, "summary": text} if text else note,
                       answer="activity_id")
        step, phase = update.get("next_step"), update.get("phase_move")
        if isinstance(step, dict) and not step.get("candidate_id"):
            name = f"deal_updates:{index}:next_step"
            suggestion = proposal(name, "next_step", update["deal_id"], update["evidence"], step["text"],
                                  step.get("next_date"))
            if step.get("apply") == "automatic":
                saved = {"idempotency_key": key(session, name, "next_step"), "deal_id": update["deal_id"],
                         "what": step["text"], **({"due_on": step["next_date"]} if step.get("next_date") else {})}
                yield Move(step, name, "/jobs/next-steps", saved, suggestion)
            else:
                yield Move(step, name, PROPOSALS, suggestion)
        if isinstance(phase, dict) and not phase.get("candidate_id"):
            name = f"deal_updates:{index}:phase_move"
            yield Move(phase, name, PROPOSALS,
                       proposal(name, "phase_move", update["deal_id"], update["evidence"]))


def worker_drafts(shaped: Dict[str, Any]) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    """The email drafts, the only suggestions still filed with the capture Worker, and their bindings."""
    pairs = [(item, binding) for item, binding in zip(
        shaped.get("post_call_items") or [],
        [b for b in shaped.get("bindings") or [] if b.get("remote") == "post_call"]) if item["kind"] == "email_draft"]
    return [item for item, _ in pairs], [binding for _, binding in pairs]


class Run:
    """One pass over a session's engine requests; outcomes are written back to the report."""

    def __init__(self, door: engine_mail.Door, session_dir: Path, log: Callable[[str], None]):
        self.door, self.session_dir, self.log = door, session_dir, log
        self.outcomes: Dict[str, Dict[str, Any]] = {}
        self.blocked = False

    def post(self, path: str, body: Dict[str, Any]) -> Optional[engine_mail.Reply]:
        """The final answer, or None when it must be retried next run."""
        reply = self.door("POST", path, json.dumps(body).encode())
        judged = engine_mail.verdict(reply)
        if judged in ("retry", "caller"):
            self.log(f"ENGINE {judged} session={self.session_dir.name} path={path} "
                     f"status={reply.status} error={reply.error}")
            self.blocked = True
            return None
        return reply

    def publish(self) -> bool:
        """True once the call and every suggestion it moves have a final answer."""
        report = post_call.read_json(self.session_dir / post_call.REPORT_FILE) or {}
        stored = report.get("engine_call") or {}
        if "outcome" not in stored:
            request = call(self.session_dir, report)
            reply = self.post("/jobs/activities", request) if request else None
            if request and reply is None:
                return False
            stored = settled(reply, "activity_id") if reply else {"outcome": "about_nothing"}
        activity_id = stored.get("activity_id")
        for move in moves(self.session_dir, report):
            if "engine" in move.item or self.blocked:
                continue
            if activity_id is None and move.path == PROPOSALS:
                self.outcomes[move.key] = {"outcome": "call_not_stored"}
                continue
            self.outcomes[move.key] = self.send(move, activity_id)
        self.outcomes = {name: outcome for name, outcome in self.outcomes.items() if outcome}
        if self.outcomes or report.get("engine_call") != stored:
            self.record(stored)
        return not self.blocked

    def send(self, move: Move, activity_id: Optional[str]) -> Dict[str, Any]:
        if move.fallback is not None:
            reply = (self.post(move.path, move.body) if confident(move.body["what"])
                     else engine_mail.Reply(422, {"error": "below_confidence_bar"}))
            if reply is None:
                return {}
            if engine_mail.verdict(reply) == "done":
                return {"outcome": "applied", "next_step_id": reply.body.get("next_step_id")}
            refused = {"refused": reply.error}
            if activity_id is None:
                return {"outcome": "call_not_stored", **refused}
            reply = self.post(PROPOSALS, {**move.fallback, "activity_id": activity_id})
            return {**settled(reply, "proposal_id"), **refused} if reply else {}
        body = {**move.body, "activity_id": activity_id} if move.path == PROPOSALS else move.body
        reply = self.post(move.path, body)
        return settled(reply, move.answer) if reply else {}

    def record(self, stored: Dict[str, Any]) -> None:
        with post_call.update_report(self.session_dir) as report:
            report["engine_call"] = stored
            for move in moves(self.session_dir, report):
                outcome = self.outcomes.get(move.key)
                if not outcome:
                    continue
                move.item["engine"] = outcome
                if outcome["outcome"] == "applied":
                    move.item["applied_automatically"] = True
                elif "refused" in outcome:
                    move.item.update(apply="review", review_reason=(
                        f"Could not apply automatically ({outcome['refused']}); review it here."))
            changes = [update[field] for update in report.get("deal_updates", [])
                       for field in post_call.AUTOMATIC_FIELDS if isinstance(update.get(field), dict)]
            report["automatic"] = {"finished_at": post_call.now(),
                                   "applied": sum(1 for value in changes if value.get("applied_automatically")),
                                   "kept_for_review": sum(1 for value in changes if value.get("apply") == "review")}


def confident(what: str) -> bool:
    """The record layer's bar the bridge's own does not hold: two words a person can act on."""
    return sum(1 for word in what.split() if re.search(r"[a-z0-9]", word, re.IGNORECASE)) >= 2


def settled(reply: engine_mail.Reply, id_field: str) -> Dict[str, Any]:
    if engine_mail.verdict(reply) != "done":
        return {"outcome": reply.error}
    outcome = {"outcome": "existing" if reply.body.get("existing") is True else "stored"}
    value = reply.body.get(id_field)
    return {**outcome, id_field: value} if isinstance(value, str) else outcome
