#!/usr/bin/env python3
"""The room bridge's supported, canonical seam into Hermes kanban.

Hermes owns task state.  This adapter uses its CLI for mutations and JSON reads
for discovery; it never reaches into kanban.db and never creates a fallback
queue when Hermes is unavailable.
"""

from __future__ import annotations

import json
import re
import subprocess
from pathlib import Path
from typing import List, TypedDict

import queue_grammar


BOARD = "carr-build"
PROJECT = "carr"
CATALOG_PATH = Path(__file__).with_name("queue-targets.json")
QUEUE_MAX_RETRIES = 3
QUEUE_TRANSIENT_PREFIX = "queue_transient:"
# ``review`` is a terminal Hermes state (the queue dispatcher treats it as
# terminal too); it must not be re-opened or re-blocked by reconciliation.
NONTERMINAL_STATUSES = ("triage", "todo", "ready", "scheduled", "running")
META_PREFIX = "[CARR_QUEUE_META "
META_FIELDS = {"v", "target", "cap", "source_seq", "source_msg_id", "finish"}
# Optional: tasks created before origin stamping (and any without server provenance) carry the six fields above.
META_OPTIONAL = {"origin"}
ORIGIN_VALUE = re.compile(r"[a-z][a-z-]{0,31}:[A-Za-z0-9][A-Za-z0-9_.-]{0,63}")
RECONCILIATION_DIAGNOSTIC_LIMIT = 25


class QueueError(RuntimeError):
    def __init__(self, code: str, reason: str):
        super().__init__(reason)
        self.code = code
        self.reason = reason


class ReconciliationResult(TypedDict):
    scanned: int
    blocked: list[str]
    diagnostics: list[dict[str, str]]


def load_catalog(path: Path = CATALOG_PATH) -> dict:
    try:
        data = json.loads(path.read_text())
    except (OSError, json.JSONDecodeError) as exc:
        raise QueueError("queue_unavailable", f"queue target catalog could not be read: {exc}") from exc
    targets = data.get("targets") if isinstance(data, dict) else None
    if data.get("v") != 1 or not isinstance(targets, dict):
        raise QueueError("queue_unavailable", "queue target catalog is not version 1")
    for alias, entry in targets.items():
        if not queue_grammar.SLUG.fullmatch(alias) or not isinstance(entry, dict):
            raise QueueError("queue_unavailable", "queue target catalog has an invalid target")
        if entry.get("adapter") not in {"desk", "hermes", "manual"} or not isinstance(entry.get("assignee"), str):
            raise QueueError("queue_unavailable", f"queue target {alias!r} has no valid adapter")
        if not isinstance(entry.get("capabilities"), list):
            raise QueueError("queue_unavailable", f"queue target {alias!r} has no capabilities")
    return data


def _subprocess_runner(argv: list[str]) -> dict:
    try:
        completed = subprocess.run(argv, capture_output=True, text=True, timeout=30)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise QueueError("queue_unavailable", f"Hermes queue is unavailable: {exc}") from exc
    if completed.returncode:
        detail = (completed.stderr or completed.stdout).strip().splitlines()[0:1]
        raise QueueError("queue_unavailable", f"Hermes queue is unavailable: {' '.join(detail)[:400]}")
    try:
        return json.loads(completed.stdout)
    except json.JSONDecodeError as exc:
        raise QueueError("queue_unavailable", "Hermes queue returned invalid JSON") from exc


def _command_runner(argv: list[str]) -> str:
    """Run one supported Hermes mutation without interpreting human output."""
    try:
        completed = subprocess.run(argv, capture_output=True, text=True, timeout=30)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise QueueError("queue_unavailable", f"Hermes queue is unavailable: {exc}") from exc
    if completed.returncode:
        detail = (completed.stderr or completed.stdout).strip().splitlines()[0:1]
        raise QueueError("queue_unavailable", f"Hermes queue mutation failed: {' '.join(detail)[:400]}")
    return completed.stdout


def _find_task_id(value) -> str | None:
    if isinstance(value, dict):
        task_id = value.get("task_id") or value.get("id")
        if isinstance(task_id, str) and task_id.startswith("t_"):
            return task_id
        for nested in value.values():
            found = _find_task_id(nested)
            if found:
                return found
    if isinstance(value, list):
        for nested in value:
            found = _find_task_id(nested)
            if found:
                return found
    return None


class KanbanAdapter:
    def __init__(self, *, runner=_subprocess_runner, command_runner=_command_runner):
        self.runner = runner
        self.command_runner = command_runner

    def create(self, command: dict, turn: dict, target: dict) -> dict:
        raw_source_seq = turn.get("seq")
        if isinstance(raw_source_seq, bool):
            raise QueueError("source_invalid", "room source sequence is invalid")
        if isinstance(raw_source_seq, int):
            source_seq = raw_source_seq
        elif isinstance(raw_source_seq, str) and raw_source_seq.isdigit():
            # Postgres bigint values cross the deployed Worker boundary as
            # JSON strings. Normalize at queue ingress so the durable task
            # metadata retains the integer contract its executor validates.
            source_seq = int(raw_source_seq)
        else:
            raise QueueError("source_invalid", "room source sequence is invalid")
        meta = {
            "v": 1, "target": command["target"], "cap": command["cap"],
            "source_seq": source_seq, "source_msg_id": turn.get("msg_id"),
            "finish": command["finish"],
        }
        # Server-derived provenance (queue_grammar._origin's fields, never `seat` or body text), stamped into the
        # adapter-built first line so the desk can require a trusted origin for a Flash code task whatever target
        # was named. A posted body cannot forge it: the executor reads only this first line as metadata.
        origin = f"{turn.get('origin_channel')}:{turn.get('origin_actor')}"
        if ORIGIN_VALUE.fullmatch(origin):
            meta["origin"] = origin
        task_body = f"[CARR_QUEUE_META {json.dumps(meta, separators=(',', ':'))}]\n{command['body']}".rstrip()
        argv = [
            "hermes", "kanban", "--board", BOARD, "create", "--project", PROJECT,
            "--assignee", target["assignee"], "--priority", str(command["priority"]),
            "--max-runtime", command["runtime"], "--idempotency-key", command["idempotency_key"],
            "--max-retries", str(QUEUE_MAX_RETRIES),
            "--created-by", str(turn.get("seat") or "room"), "--body", task_body,
        ]
        if command.get("after"):
            argv.extend(["--parent", command["after"]])
        if target.get("model"):
            argv.extend(["--model", target["model"]])
        if target.get("provider"):
            argv.extend(["--provider", target["provider"]])
        if command.get("manual"):
            # Hermes creates this manual card blocked atomically.  It can never
            # appear in a ready list between a create and a later transition.
            argv.extend(["--initial-status", "blocked"])
        argv.extend(["--json", command["title"]])
        raw = self.runner(argv)
        task_id = _find_task_id(raw)
        if not task_id:
            raise QueueError("queue_unavailable", "Hermes create returned no task id")
        return {"task_id": task_id, "created": bool(raw.get("created", True)), "raw": raw}

    def show(self, task_id: str) -> dict:
        return self.runner(["hermes", "kanban", "--board", BOARD, "show", task_id, "--json"])

    def list(self, state: str, target: str | None, catalog: dict) -> dict:
        argv = ["hermes", "kanban", "--board", BOARD, "list", "--status", state, "--json"]
        if target:
            argv.extend(["--assignee", catalog["targets"][target]["assignee"]])
        return self.runner(argv)

    def list_nonterminal(self, *, assignees: set[str] | None = None) -> List[dict]:
        """Read every nonterminal card through Hermes' supported adapter seam.

        Hermes can filter by assignee; the bridge applies its explicit
        nonterminal status set client-side so terminal or statusless rows can
        never be quarantined accidentally. De-duplicating IDs makes the read
        safe if Hermes returns overlapping results.
        """
        rows: list[dict] = []
        seen: set[str] = set()
        if assignees is not None and not assignees:
            return rows
        for assignee in sorted(assignees) if assignees is not None else [None]:
            command = ["hermes", "kanban", "--board", BOARD, "list"]
            if assignee is not None:
                command.extend(["--assignee", assignee])
            command.append("--json")
            payload = self.runner(command)
            if not isinstance(payload, list):
                raise QueueError("queue_unavailable", "Hermes nonterminal list returned an invalid shape")
            for row in payload:
                if not isinstance(row, dict):
                    continue
                row_status = row.get("status")
                if not isinstance(row_status, str) or row_status not in NONTERMINAL_STATUSES:
                    continue
                task_id = row.get("id") or row.get("task_id")
                if isinstance(task_id, str) and task_id.startswith("t_") and task_id not in seen:
                    seen.add(task_id)
                    rows.append(row)
        return rows

    @staticmethod
    def _reconciliation_meta(task: dict) -> tuple[dict | None, str | None]:
        """Parse only the exact queue envelope, without trusting task prose."""
        body = task.get("body")
        if not isinstance(body, str):
            return None, "metadata_missing"
        first, separator, _instructions = body.partition("\n")
        if not separator or not first.startswith(META_PREFIX) or not first.endswith("]"):
            return None, "metadata_malformed"
        try:
            value = json.loads(first[len(META_PREFIX):-1])
        except (TypeError, json.JSONDecodeError):
            return None, "metadata_malformed"
        if not isinstance(value, dict) or not META_FIELDS <= set(value) <= META_FIELDS | META_OPTIONAL:
            return None, "metadata_malformed"
        if "origin" in value and not (isinstance(value["origin"], str) and ORIGIN_VALUE.fullmatch(value["origin"])):
            return None, "metadata_malformed"
        if (value.get("v") != 1 or not isinstance(value.get("target"), str)
                or not isinstance(value.get("cap"), str)
                or not isinstance(value.get("source_msg_id"), str)
                or not value["source_msg_id"]
                or not isinstance(value.get("source_seq"), int)
                or isinstance(value["source_seq"], bool) or value["source_seq"] < 0
                or value.get("finish") not in {"done", "review"}):
            return None, "metadata_malformed"
        return value, None

    def reconcile_disabled_targets(self, catalog: dict, *, diagnostic_limit: int = RECONCILIATION_DIAGNOSTIC_LIMIT) -> ReconciliationResult:
        """Block exact metadata matches for disabled aliases, once per cycle.

        This method intentionally does not use assignee as an identity key.
        A card is eligible only when its exact CARR_QUEUE_META target matches a
        disabled catalog alias; all other cards remain untouched.
        """
        disabled = {
            alias: target.get("assignee")
            for alias, target in catalog.get("targets", {}).items()
            if isinstance(alias, str) and isinstance(target, dict)
            and not target.get("enabled") and isinstance(target.get("assignee"), str)
        }
        result: ReconciliationResult = {"scanned": 0, "blocked": [], "diagnostics": []}
        diagnosed: set[str] = set()
        if not disabled:
            return result
        assignees = {
            assignee for assignee in disabled.values() if isinstance(assignee, str)
        }
        for task in self.list_nonterminal(assignees=assignees):
            result["scanned"] += 1
            task_id = task.get("id") or task.get("task_id")
            meta, error = self._reconciliation_meta(task)
            if error:
                diagnostic_id = task_id if isinstance(task_id, str) and task_id.startswith("t_") else "unknown"
                if diagnostic_id not in diagnosed and len(result["diagnostics"]) < diagnostic_limit:
                    diagnosed.add(diagnostic_id)
                    result["diagnostics"].append({
                        "code": "queue_metadata_malformed", "task_id": diagnostic_id,
                    })
                continue
            if meta is None:
                continue
            if meta["target"] not in disabled or task.get("assignee") != disabled[meta["target"]]:
                continue
            if not isinstance(task_id, str) or not task_id.startswith("t_"):
                diagnostic_id = task_id if isinstance(task_id, str) and task_id.startswith("t_") else "unknown"
                if diagnostic_id not in diagnosed and len(result["diagnostics"]) < diagnostic_limit:
                    diagnosed.add(diagnostic_id)
                    result["diagnostics"].append({
                        "code": "queue_task_identity_invalid", "task_id": diagnostic_id,
                    })
                continue
            self.block(task_id, "target_retired")
            result["blocked"].append(task_id)
        return result

    def ready_for(self, assignee: str) -> List[dict]:
        payload = self.runner([
            "hermes", "kanban", "--board", BOARD, "list", "--status", "ready",
            "--assignee", assignee, "--sort", "created", "--json",
        ])
        if not isinstance(payload, list):
            raise QueueError("queue_unavailable", "Hermes ready list returned an invalid shape")
        return [row for row in payload if isinstance(row, dict)]

    def claim(self, task_id: str) -> None:
        self.command_runner([
            "hermes", "kanban", "--board", BOARD, "claim", task_id, "--ttl", "900",
        ])

    def reclaim(self, task_id: str, reason: str) -> None:
        """Release a just-claimed retryable task through Hermes' recovery API."""
        self.command_runner([
            "hermes", "kanban", "--board", BOARD, "reclaim", task_id,
            "--reason", reason,
        ])

    def retry_attempt(self, task_id: str, prefix: str = QUEUE_TRANSIENT_PREFIX) -> tuple[int, int]:
        """Read the durable queue-specific failure count and its card bound.

        The bridge never owns an attempt counter.  Hermes' reclaim events are
        the durable evidence; the closed reason prefix keeps unrelated manual
        recoveries out of this queue policy.
        """
        payload = self.show(task_id)
        task = payload.get("task") if isinstance(payload, dict) else None
        events = payload.get("events") if isinstance(payload, dict) else None
        limit = task.get("max_retries") if isinstance(task, dict) else None
        if (not isinstance(limit, int) or isinstance(limit, bool) or limit < 1
                or not isinstance(events, list)):
            raise QueueError("queue_unavailable", "Hermes retry evidence is unavailable")
        attempts = 0
        for event in events:
            if not isinstance(event, dict) or event.get("kind") != "reclaimed":
                continue
            payload_value = event.get("payload")
            if not isinstance(payload_value, dict):
                raise QueueError("queue_unavailable", "Hermes retry evidence is invalid")
            reason = payload_value.get("reason")
            if isinstance(reason, str) and reason.startswith(prefix):
                attempts += 1
        return attempts, limit

    def comment(self, task_id: str, summary: str) -> None:
        self.command_runner([
            "hermes", "kanban", "--board", BOARD, "comment", task_id, summary,
            "--author", "queue-dispatch",
        ])

    def complete(self, task_id: str, summary: str, metadata: dict) -> None:
        self.command_runner([
            "hermes", "kanban", "--board", BOARD, "complete", task_id,
            "--result", summary, "--summary", summary,
            "--metadata", json.dumps(metadata, separators=(",", ":")),
        ])

    def request_review(self, task_id: str, summary: str, metadata: dict) -> None:
        self.command_runner([
            "hermes", "kanban", "--board", BOARD, "request-review", task_id,
            "--summary", summary,
            "--metadata", json.dumps(metadata, separators=(",", ":")),
        ])

    def block(self, task_id: str, reason: str, *, kind: str | None = None) -> None:
        argv = ["hermes", "kanban", "--board", BOARD, "block"]
        if kind:
            argv.extend(["--kind", kind])
        argv.extend([task_id, reason])
        self.command_runner(argv)


def public_targets(catalog: dict) -> list[dict]:
    return [
        {"alias": alias, "enabled": bool(entry.get("enabled")),
         "capabilities": entry.get("capabilities", []),
         "effective_model": entry.get("effective_model"),
         "unavailable_reason": entry.get("unavailable_reason") if not entry.get("enabled") else None}
        for alias, entry in sorted(catalog["targets"].items())
    ]


def bounded_status(payload: object, *, limit: int = 50) -> dict:
    """Return only display-safe card fields from a Hermes read.

    Status is a discovery receipt, not a second copy of task bodies, comments,
    worker output, or attachments.  Recursive collection tolerates the small
    shape differences between Hermes ``show`` and ``list`` JSON responses.
    """
    rows: list[dict] = []

    def visit(value: object) -> None:
        if len(rows) >= limit:
            return
        if isinstance(value, dict):
            task_id = value.get("task_id") or value.get("id")
            if isinstance(task_id, str) and task_id.startswith("t_"):
                row = {"task_id": task_id}
                for key in ("title", "status", "assignee", "priority", "updated_at"):
                    item = value.get(key)
                    if isinstance(item, (str, int)):
                        row[key] = str(item)[:200]
                rows.append(row)
                return
            for item in value.values():
                visit(item)
        elif isinstance(value, list):
            for item in value:
                visit(item)

    visit(payload)
    return {"tasks": rows, "truncated": len(rows) >= limit}


REPO = Path(__file__).resolve().parents[2]


def _model_router():
    """ops/jev_model_route.py, loaded by path like every ops sibling; paid for only on target=auto."""
    import importlib.util
    spec = importlib.util.spec_from_file_location("jev_model_route", REPO / "ops" / "jev_model_route.py")
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _flash_is_up() -> bool:
    import flash_wire
    # A disabled demand-startable server is an available route, not an outage.
    return flash_wire.is_up() or (Path.home() / "Library/LaunchAgents/local.ds4-flash-next.plist").is_file()


class QueueService:
    def __init__(self, *, catalog: dict | None = None, adapter: KanbanAdapter | None = None,
                 router=None, flash_up=None):
        self.catalog = catalog or load_catalog()
        self.adapter = adapter or KanbanAdapter()
        self._router = router
        self._flash_up = flash_up

    def route_auto(self, command: dict) -> dict:
        """Pick the target for a target=auto task. Jev routes it through the Model Room policy
        (ops/config/model-routes.v1.json); code then checks that the picked target is enabled and accepts the
        capability, and that Flash is up for a Flash route. Any miss goes to the policy's fallback target, with
        the reason recorded."""
        router = self._router or _model_router()
        policy = router.load_policy()
        up = (self._flash_up or _flash_is_up)()
        row = router.decide(command["title"], command.get("body") or "", flash_free=up, policy=policy)
        targets = policy.get("queue_targets", {})
        alias = targets.get(row["route"])
        entry = self.catalog["targets"].get(alias) if alias else None
        reason = None
        if not isinstance(entry, dict) or not entry.get("enabled"):
            reason = "target_unavailable"
        elif command["cap"] not in entry.get("capabilities", []):
            reason = "capability_target_refused"
        elif row.get("overflow"):
            reason = "flash_busy_or_down"
        elif (row.get("fallback") or row.get("jev_error")) and alias == "flash":
            # Jev unreachable, or no score cleared its cutoff: the abstain route never lands on the cheapest desk
            reason = "jev_abstained"
        elif row["route"] == "script" and alias == "flash":
            # Flash's script protocol needs the data named, in the policy's data folders (flash_wire.script_inputs)
            import flash_wire
            # data lines count in the body only, exactly as the desk reads them (flash_wire.task_parts)
            paths, _, refusal = flash_wire.script_inputs(command.get("body") or "",
                                                         roots=policy.get("script_data_roots") or [])
            if refusal:
                reason = "script_data_refused"
            elif paths is None:
                reason = "script_needs_data"
        elif row["route"] == "code" and alias == "flash":
            # Defense in depth: a Flash code run executes model-driven code on this host, so it is accepted only from
            # a server-derived trusted origin (never `seat`), and only when the body names one allowlisted git
            # project and one bounded test command, read exactly as the desk reads them (flash_wire.code_inputs over
            # flash_wire.task_parts' body).
            origin = f"{command.get('origin_channel')}:{command.get('origin_actor')}"
            import flash_wire
            spec, _, refusal = flash_wire.code_inputs(command.get("body") or "",
                                                      roots=policy.get("code_project_roots") or [])
            if origin not in (policy.get("code_task_origins") or []):
                reason = "code_origin_untrusted"
            elif refusal:
                reason = "code_project_refused"
            elif spec is None:
                reason = "code_needs_project"
        if reason:
            alias = targets.get("fallback")
        return {"target": alias, "route": row["route"], "model": row.get("model"), "effort": row.get("effort"),
                "scores": row.get("scores"), "fallback_reason": reason, "jev_error": row.get("jev_error")}

    def handle(self, turn: dict, *, room: str) -> dict:
        parsed = queue_grammar.parse({**turn, "room": room}, self.catalog)
        if parsed.kind == "not_command":
            return {"handled": False}
        source = {"source_seq": turn.get("seq"), "source_msg_id": turn.get("msg_id")}
        if parsed.kind == "rejected":
            return {"handled": True, "kind": "rejected", "receipt": {"queue_rejected": {
                **source, "code": parsed.code, "reason": parsed.reason, "hint": parsed.hint,
            }}}
        if parsed.kind == "targets":
            return {"handled": True, "kind": "targets", "receipt": {"queue_targets": {
                **source, "targets": public_targets(self.catalog),
            }}}
        try:
            if parsed.kind == "status":
                value = parsed.value or {}
                payload = self.adapter.show(value["id"]) if "id" in value else self.adapter.list(
                    value["state"], value.get("target"), self.catalog)
                return {"handled": True, "kind": "status", "receipt": {"queue_status": {
                    **source, "query": value, "result": bounded_status(payload),
                }}}
            assert parsed.kind == "enqueue" and parsed.value is not None
            command = parsed.value
            routed = None
            if command["target"] == queue_grammar.AUTO_TARGET:
                routed = self.route_auto(command)
                entry = self.catalog["targets"].get(routed["target"] or "")
                if not isinstance(entry, dict) or not entry.get("enabled") \
                        or command["cap"] not in entry.get("capabilities", []):
                    return {"handled": True, "kind": "rejected", "receipt": {"queue_rejected": {
                        **source, "code": "auto_route_unavailable", "route": routed,
                        "reason": "no enabled target accepts this task", "hint": "Name a target explicitly",
                    }}}
                command = {**command, "target": routed["target"]}
            created = self.adapter.create(command, turn, self.catalog["targets"][command["target"]])
            # A retried target=auto command re-runs route_auto on every call, so its
            # idempotency key (from the room message, not the task) can land on an
            # EXISTING task that was originally routed somewhere else — Flash's
            # liveness or Jev's decision can differ between the original send and the
            # retry. On a duplicate FROM AN AUTO ROUTE (routed is not None), the
            # receipt must report where that existing task actually went, not this
            # call's freshly recomputed route. An explicit target can never drift this
            # way — the command names its own target every time — so this stays a
            # no-op read for every explicit-target create, matching prior behavior.
            report_target = command["target"]
            if routed is not None and not created["created"]:
                report_target = self._existing_task_target(created["task_id"], default=report_target)
            return {"handled": True, "kind": "accepted", "receipt": {"queue_accepted": {
                **source, "task_id": created["task_id"], "target": report_target,
                **({"route": routed} if routed else {}),
                "cap": command["cap"], "idempotency_key": command["idempotency_key"],
                "status": "blocked" if command.get("manual") and created["created"] else
                          ("created" if created["created"] else "duplicate"),
            }}}
        except QueueError as exc:
            return {"handled": True, "kind": "rejected", "receipt": {"queue_rejected": {
                **source, "code": exc.code, "reason": exc.reason, "hint": "Try again after Hermes is available",
            }}}

    def reconcile_disabled_targets(self) -> ReconciliationResult:
        return self.adapter.reconcile_disabled_targets(self.catalog)

    def _existing_task_target(self, task_id: str, *, default: str) -> str:
        """The target actually bound to an already-existing queue task.

        Best-effort: a fresh read that fails or a body that does not parse as the
        exact queue envelope falls back to the caller's own default rather than
        failing the whole receipt over a display fact.
        """
        try:
            payload = self.adapter.show(task_id)
        except QueueError:
            return default
        task = payload.get("task") if isinstance(payload, dict) else None
        if not isinstance(task, dict):
            return default
        meta, error = KanbanAdapter._reconciliation_meta(task)
        if error is not None or meta is None:
            return default
        return meta["target"]
