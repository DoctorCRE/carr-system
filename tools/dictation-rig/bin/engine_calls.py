"""Send a call's activities, suggestions, and automatic deal changes to the engine.

The doors are fixed in the engine repository's docs/new-database.md, "Doors the old
system calls". The bridge calls them only when the capture config sets engine_calls;
otherwise nothing here runs and the call's suggestions go to the capture Worker as before.

Door 19 stores the call and its per-deal notes. Automatic phase moves use door 16
and automatic next steps use door 18. Door 21 keeps the changes that need review.
Email drafts stay with the capture Worker.

Keys derive from the session and the item, so a crashed run's retry converges. A 5xx or a
lost connection is retried next run with the same key, and a refusal of the caller itself
holds the session; any other 4xx is final and kept on the item.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
import fcntl
import json
from pathlib import Path
import re
from typing import Any, Callable, Dict, Iterator, List, Optional, Tuple
import uuid

import post_call
from lib import engine_mail

JOB = "call_capture"
PROPOSALS = "/jobs/proposals"
TASK_OWNERS = {"joe_tasks": "joe", "dell_tasks": "dell"}
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
    deals = dict.fromkeys(item["deal_id"] for name in (*TASK_OWNERS, "deal_updates", "draft_proposals")
                          for item in report.get(name, []) if item.get("deal_id"))
    if not deals:
        return None
    request = {"idempotency_key": key(session_dir.name, "call"), "kind": "call",
               "happened_at": happened_at(session_dir, report), "about": [{"deal_id": d} for d in deals]}
    text = summary(report)
    return {**request, "summary": text} if text else request


def moves(session_dir: Path, report: Dict[str, Any], recorder: str) -> Iterator[Move]:
    """Each note and suggestion the Worker does not already hold, with its door and request."""
    session = session_dir.name

    def proposal(name: str, kind: str, deal_id: str, evidence: str, **fields: Optional[str]) -> Dict[str, Any]:
        return {"idempotency_key": key(session, name, "proposal"), "kind": kind, "deal_id": deal_id,
                "evidence": evidence, **{field: value for field, value in fields.items() if value is not None}}

    for list_name, owner in TASK_OWNERS.items():
        for index, task in enumerate(report.get(list_name, [])):
            name = f"{list_name}:{index}"
            if not task.get("candidate_id"):
                yield Move(task, name, PROPOSALS, proposal(
                    name, "next_step", task["deal_id"], task["evidence"], what=task["title"],
                    due_on=task.get("due_on"), owner=owner))
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
            suggestion = proposal(name, "next_step", update["deal_id"], update["evidence"], what=step["text"],
                                  due_on=step.get("next_date"))
            if step.get("apply") == "automatic":
                saved = {"idempotency_key": key(session, name, "next_step"), "deal_id": update["deal_id"],
                         "what": step["text"], "owner": recorder, "replaces": "current",
                         **({"due_on": step["next_date"]} if step.get("next_date") else {})}
                yield Move(step, name, "/jobs/next-steps", saved, suggestion, answer="next_step_id")
            else:
                yield Move(step, name, PROPOSALS, suggestion)
        if isinstance(phase, dict) and not phase.get("candidate_id"):
            name = f"deal_updates:{index}:phase_move"
            target = {"to": phase["value"], **({"on": phase["started_on"]} if phase.get("started_on") else {})}
            suggestion = proposal(name, "phase_move", update["deal_id"], update["evidence"], **target)
            if phase.get("apply") == "automatic":
                saved = {"idempotency_key": key(session, name, "phase"), "phase": target}
                yield Move(phase, name, f"/jobs/deals/{update['deal_id']}/changes", saved, suggestion,
                           answer="deal_id")
            else:
                yield Move(phase, name, PROPOSALS, suggestion)


def worker_drafts(shaped: Dict[str, Any]) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    """The email drafts, the only suggestions still filed with the capture Worker, and their bindings."""
    pairs = [(item, binding) for item, binding in zip(
        shaped.get("post_call_items") or [],
        [b for b in shaped.get("bindings") or [] if b.get("remote") == "post_call"]) if item["kind"] == "email_draft"]
    return [item for item, _ in pairs], [binding for _, binding in pairs]


class Run:
    """One pass over a session's engine requests; outcomes are written back to the report."""

    def __init__(self, door: engine_mail.Door, session_dir: Path, log: Callable[[str], None], *, recorder: str):
        self.door, self.session_dir, self.log = door, session_dir, log
        self.recorder = recorder
        self.outcomes: Dict[str, Dict[str, Any]] = {}
        self.blocked = False

    def request(self, path: str, body: Optional[Dict[str, Any]] = None) -> Optional[engine_mail.Reply]:
        """The final answer, or None when it must be retried next run."""
        reply = self.door("GET", path, None) if body is None else self.door("POST", path, json.dumps(body).encode())
        judged = engine_mail.verdict(reply)
        if judged in ("retry", "caller"):
            self.log(f"ENGINE {judged} session={self.session_dir.name} path={path} "
                     f"status={reply.status} error={reply.error}")
            self.blocked = True
            return None
        return reply

    def publish(self) -> bool:
        """True once the call and every suggestion it moves have a final answer."""
        lock_path = self.session_dir / ".engine-publish.lock"
        lock_path.touch(mode=0o600, exist_ok=True)
        with lock_path.open("a+") as lock:
            try:
                fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                return False
            return self._publish()

    def _publish(self) -> bool:
        report = post_call.read_json(self.session_dir / post_call.REPORT_FILE) or {}
        stored = report.get("engine_call") or {}
        if "outcome" not in stored:
            request = call(self.session_dir, report)
            reply = self.request("/jobs/activities", request) if request else None
            if request and reply is None:
                return False
            stored = settled(reply, "activity_id") if reply else {"outcome": "about_nothing"}
        activity_id = stored.get("activity_id")
        for move in moves(self.session_dir, report, self.recorder):
            if "engine" in move.item or self.blocked:
                continue
            self.outcomes[move.key] = self.send(move, activity_id)
        self.outcomes = {name: outcome for name, outcome in self.outcomes.items() if outcome}
        if self.outcomes or report.get("engine_call") != stored:
            self.record(stored)
        return not self.blocked

    def send(self, move: Move, activity_id: Optional[str]) -> Dict[str, Any]:
        path, body, answer = move.path, move.body, move.answer
        reason = None
        if move.fallback is not None:
            reason = (move.item.get("engine_pending") or {}).get("refused")
            if reason is None:
                if move.fallback["kind"] == "phase_move":
                    reply = self.apply_phase(move, move.fallback["deal_id"])
                elif confident(body["what"]):
                    reply = self.request(path, body)
                else:
                    reply = engine_mail.Reply(422, {"error": "below_confidence_bar"})
                if reply is None:
                    return {}
                if engine_mail.verdict(reply) == "done":
                    return {**settled(reply, answer), "outcome": "applied"}
                reason = reply.error
                self.checkpoint(move, refused=reason)
            path, body, answer = PROPOSALS, move.fallback, "proposal_id"
        refused = {"refused": reason} if reason is not None else {}
        if path == PROPOSALS:
            if activity_id is None:
                return {"outcome": "call_not_stored", **refused}
            body = {**body, "activity_id": activity_id}
        reply = self.request(path, body)
        return {**settled(reply, answer), **refused} if reply else {}

    def apply_phase(self, move: Move, deal_id: str) -> Optional[engine_mail.Reply]:
        pending = (move.item.get("engine_pending") or {}).get("request")
        if pending is not None:
            return self.request(move.path, pending)

        read = self.request(f"/jobs/deals/{deal_id}")
        if read is None or engine_mail.verdict(read) != "done":
            return read
        deal = read.body
        current, target = deal.get("phase"), move.body["phase"]
        is_requested_deal = deal.get("deal_id") == deal_id
        if is_requested_deal and current == target["to"]:
            return engine_mail.Reply(200, {"deal_id": deal_id})

        saved, seen = deal.get("phase_saved_at"), deal.get("change_id")
        phase_saved = timestamp(saved)
        has_phase_fields = {"phase", "phase_saved_at"} <= deal.keys()
        has_change_id = type(seen) is int and seen >= 0
        has_valid_phase = (saved is None if current is None else
                           isinstance(current, str) and bool(current) and phase_saved is not None)
        if not (is_requested_deal and has_phase_fields and has_change_id and has_valid_phase):
            return engine_mail.Reply(422, {"error": "missing_phase_chronology"})

        started = (post_call.read_json(self.session_dir / "meta.json") or {}).get("started")
        call_start = timestamp(started)
        if call_start is None:
            return engine_mail.Reply(422, {"error": "missing_phase_chronology"})
        if phase_saved is not None and phase_saved >= call_start:
            return engine_mail.Reply(409, {"error": "newer_change_exists"})
        if current in {"closed", "invoiced", "dead"}:
            return engine_mail.Reply(422, {"error": "phase_not_eligible"})

        request = {**move.body, "seen_change_id": seen, "phase": {"from": current, **target}}
        # A lost save response must replay the same body, even if the deal has moved.
        self.checkpoint(move, request=request)
        return self.request(move.path, request)

    def checkpoint(self, move: Move, **pending: Any) -> None:
        with post_call.update_report(self.session_dir) as report:
            current = next(current for current in moves(self.session_dir, report, self.recorder)
                           if current.key == move.key)
            current.item["engine_pending"] = pending

    def record(self, stored: Dict[str, Any]) -> None:
        with post_call.update_report(self.session_dir) as report:
            report["engine_call"] = stored
            for move in moves(self.session_dir, report, self.recorder):
                outcome = self.outcomes.get(move.key)
                if not outcome:
                    continue
                move.item.pop("engine_pending", None)
                move.item["engine"] = outcome
                if outcome["outcome"] == "applied":
                    move.item["applied_automatically"] = True
                elif "refused" in outcome:
                    reason = outcome["refused"]
                    move.item.update(apply="review", review_reason=(
                        "Check whether the deal moved after the call before applying this phase change."
                        if reason in {"missing_phase_chronology", "newer_change_exists"}
                        else f"Could not apply automatically ({reason}); review it here."))
            changes = [update[field] for update in report.get("deal_updates", [])
                       for field in post_call.AUTOMATIC_FIELDS if isinstance(update.get(field), dict)]
            report["automatic"] = {"finished_at": post_call.now(),
                                   "applied": sum(1 for value in changes if value.get("applied_automatically")),
                                   "kept_for_review": sum(1 for value in changes if value.get("apply") == "review")}


def confident(what: str) -> bool:
    """The record layer's bar the bridge's own does not hold: two words a person can act on."""
    return sum(1 for word in what.split() if re.search(r"[a-z0-9]", word, re.IGNORECASE)) >= 2


def timestamp(value: Any) -> Optional[datetime]:
    if not isinstance(value, str):
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo is not None else None


def settled(reply: engine_mail.Reply, id_field: str) -> Dict[str, Any]:
    if engine_mail.verdict(reply) != "done":
        return {"outcome": reply.error}
    outcome = {"outcome": "existing" if reply.body.get("existing") is True else "stored"}
    value = reply.body.get(id_field)
    return {**outcome, id_field: value} if isinstance(value, str) else outcome
