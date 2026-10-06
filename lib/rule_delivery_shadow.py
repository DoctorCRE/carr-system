"""Append-only evidence primitives for the scoped rule-delivery shadow window."""
from __future__ import annotations

import hashlib
import fcntl
import json
import os
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

SCHEMA = "rule-delivery-shadow-ledger/v1"
HEX64 = set("0123456789abcdef")
WINDOW_SOURCE_PATHS = (
    "bin/rule-delivery-cutover-prod.sh",
    "bin/rule-delivery-shadow-ledger-prod.sh",
    "hooks/rule-pack-drift-gate.py",
    "hooks/rule-pack-preuse-reselection.py",
    "hooks/session-brief.py",
    "hooks/machine-converge.py",
    "lib/rule_delivery_preuse.py",
    "lib/rule_delivery_shadow.py",
    "lib/rule_delivery_activation.py",
    "mcp-server/src/mcp.js",
    "mcp-server/src/engineering-runtime.js",
    "migrations/0291_rule_delivery_layers.sql",
    "migrations/0317_atomic_rule_delivery_cutover.sql",
    "migrations/0321_rule_delivery_policy_seed_repair.sql",
    "ops/config/codex-hooks.json",
    "ops/config/control-enforcement-classes.v1.json",
    "ops/config/gate-baseline.json",
    "ops/config/hooks.json",
    "ops/config/rule-delivery-activation-overlay.v1.json",
    "ops/config-as-code.py",
    "ops/config/rule-enforcement-map.json",
    "ops/config/rule-jit-triggers.v1.json",
    "ops/rule-jit-compile.py",
    "ops/scheduled-tasks/engineering-slice.SKILL.md",
    "ops/scheduled-tasks/nightly-record-layer.SKILL.md",
    "ops/rule-delivery-cutover.py",
    "ops/rule-delivery-shadow-eligibility.py",
    "ops/rule-delivery-shadow-ledger.py",
    "ops/rule-delivery-shadow-watch.py",
    "tools/room-bridge/engineering_dispatch_adapter.py",
)
EPOCH_KEYS = frozenset({"schema", "record_type", "record_id", "ts",
                        "policy_digest", "map_digest", "source_digest", "owner",
                        "reason", "remedy_ref", "rollback_ref"})
DISPOSITION_KEYS = frozenset({"schema", "record_type", "record_id", "ts", "event_id",
                              "disposition", "owner", "remedy_ref", "evidence_ref",
                              "rollback_ref"})
OBSERVATION_COMMON_KEYS = frozenset({
    "schema", "record_type", "event_id", "ts", "hook", "session",
    "map_digest", "source_digest",
})
OBSERVATION_SUCCESS_KEYS = OBSERVATION_COMMON_KEYS | frozenset({
    "mode", "needed", "loaded", "missing", "triggers", "would_omit_count",
    "missed_rules",
})
OBSERVATION_ERROR_KEYS = OBSERVATION_COMMON_KEYS | frozenset({"error", "detail"})


def canonical(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"),
                      ensure_ascii=False).encode("utf-8")


def digest(value: Any) -> str:
    return hashlib.sha256(canonical(value)).hexdigest()


def record_id(row: dict) -> str:
    return digest({key: value for key, value in row.items() if key != "record_id"})


def observation_id(row: dict) -> str:
    return digest({key: value for key, value in row.items() if key != "event_id"})


def valid_digest(value: object) -> bool:
    return isinstance(value, str) and len(value) == 64 and set(value) <= HEX64


def stamp(row: dict) -> datetime | None:
    try:
        return datetime.strptime(str(row.get("ts")), "%Y-%m-%dT%H:%M:%SZ").replace(
            tzinfo=timezone.utc)
    except (TypeError, ValueError):
        return None


def iso(at: datetime | None = None) -> str:
    value = at or datetime.now(timezone.utc)
    return value.astimezone(timezone.utc).replace(microsecond=0).strftime(
        "%Y-%m-%dT%H:%M:%SZ")


def nonempty(row: dict, keys: tuple[str, ...]) -> bool:
    return all(isinstance(row.get(key), str) and bool(row[key].strip()) for key in keys)


def make_epoch(identity: dict, *, owner: str, reason: str, remedy_ref: str,
               rollback_ref: str, at: datetime | None = None) -> dict:
    require_identity(identity)
    row = {"schema": SCHEMA, "record_type": "epoch", "ts": iso(at), **identity,
           "owner": owner, "reason": reason, "remedy_ref": remedy_ref,
           "rollback_ref": rollback_ref}
    row["record_id"] = record_id(row)
    problem = validate_record(row)
    if problem:
        raise ValueError(problem)
    return row


def make_disposition(event_id: str, disposition: str, *, owner: str,
                     remedy_ref: str, evidence_ref: str, rollback_ref: str,
                     at: datetime | None = None) -> dict:
    row = {"schema": SCHEMA, "record_type": "disposition", "ts": iso(at),
           "event_id": event_id, "disposition": disposition, "owner": owner,
           "remedy_ref": remedy_ref, "evidence_ref": evidence_ref,
           "rollback_ref": rollback_ref}
    row["record_id"] = record_id(row)
    problem = validate_record(row)
    if problem:
        raise ValueError(problem)
    return row


def _string_list(value: object) -> bool:
    return (isinstance(value, list)
            and all(isinstance(item, str) and bool(item.strip()) for item in value))


def _triggers(value: object) -> bool:
    return (isinstance(value, dict)
            and all(isinstance(key, str) and bool(key.strip()) and _string_list(items)
                    for key, items in value.items()))


def make_observation(*, session: str, map_digest: str, source_digest: str,
                     result: dict, at: datetime | None = None) -> dict:
    row = {"schema": "rule-delivery-shadow-observation/v2",
           "record_type": "observation", "ts": iso(at),
           "hook": "rule-pack-drift-gate", "session": session,
           "map_digest": map_digest, "source_digest": source_digest, **result}
    row["event_id"] = observation_id(row)
    problem = validate_observation(row)
    if problem:
        raise ValueError(problem)
    return row


def make_error_observation(*, session: str, error: str, detail: str,
                           map_digest: str, source_digest: str,
                           at: datetime | None = None) -> dict:
    row = {"schema": "rule-delivery-shadow-observation/v2",
           "record_type": "observation", "ts": iso(at),
           "hook": "rule-pack-drift-gate", "session": session,
           "map_digest": map_digest, "source_digest": source_digest,
           "error": error, "detail": detail}
    row["event_id"] = observation_id(row)
    problem = validate_observation(row)
    if problem:
        raise ValueError(problem)
    return row


def validate_observation(row: dict) -> str | None:
    keys = set(row)
    if keys not in {OBSERVATION_SUCCESS_KEYS, OBSERVATION_ERROR_KEYS}:
        return "malformed observation keys"
    if (row.get("schema") != "rule-delivery-shadow-observation/v2"
            or row.get("record_type") != "observation"
            or row.get("hook") != "rule-pack-drift-gate" or stamp(row) is None):
        return "malformed observation schema or timestamp"
    if not nonempty(row, ("session", "map_digest", "source_digest")):
        return "malformed observation identity"
    if not valid_digest(row["map_digest"]) or not valid_digest(row["source_digest"]):
        return "malformed observation digest"
    if row.get("event_id") != observation_id(row):
        return "malformed observation event_id"
    if keys == OBSERVATION_ERROR_KEYS:
        if not nonempty(row, ("error", "detail")):
            return "malformed observation error"
        return None
    if row.get("mode") not in {None, "shadow", "enforced"}:
        return "malformed observation mode"
    if not all(_string_list(row.get(key)) for key in
               ("needed", "loaded", "missing", "missed_rules")):
        return "malformed observation list"
    if not _triggers(row.get("triggers")):
        return "malformed observation triggers"
    count = row.get("would_omit_count")
    if isinstance(count, bool) or not isinstance(count, int) or count < 0:
        return "malformed observation would_omit_count"
    return None


def read_jsonl_handle(handle) -> tuple[list[dict], int]:
    handle.seek(0)
    rows: list[dict] = []
    bad = 0
    for line in handle:
        try:
            row = json.loads(line)
            if not isinstance(row, dict):
                raise ValueError
            rows.append(row)
        except ValueError:
            bad += 1
    return rows, bad


def _open_append(path: Path):
    created = False
    try:
        fd = os.open(path, os.O_RDWR | os.O_CREAT | os.O_EXCL | os.O_APPEND, 0o600)
        created = True
    except FileExistsError:
        fd = os.open(path, os.O_RDWR | os.O_APPEND)
    return os.fdopen(fd, "a+", encoding="utf-8"), created


@contextmanager
def locked_read(path: Path):
    """Hold the same ledger lock as appenders for the full caller transaction."""
    with path.open("r", encoding="utf-8") as handle:
        fcntl.flock(handle.fileno(), fcntl.LOCK_SH)
        rows, bad = read_jsonl_handle(handle)
        if bad:
            raise RuntimeError(f"refusing read: {bad} unreadable telemetry line(s)")
        yield rows


def append_locked(path: Path, build) -> dict:
    """Build and fsync one record while holding the ledger's exclusive lock."""
    path.parent.mkdir(parents=True, exist_ok=True)
    handle, created = _open_append(path)
    with handle:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
        rows, bad = read_jsonl_handle(handle)
        if bad:
            raise RuntimeError(f"refusing append: {bad} unreadable telemetry line(s)")
        existing = inspect(rows)
        if existing["errors"]:
            raise ValueError("existing ledger validation failed")
        row = build(rows)
        if not isinstance(row, dict):
            raise ValueError("append builder did not return a record")
        if row.get("record_type") in {"epoch", "disposition"}:
            problem = validate_record(row)
            if problem:
                raise ValueError(problem)
        elif row.get("record_type") == "observation":
            problem = validate_observation(row)
            if problem:
                raise ValueError(problem)
        if row.get("record_type") == "epoch":
            validate_epoch_append(rows, row)
        appended = inspect([*rows, row])
        if appended["errors"]:
            raise ValueError("appended record violates ledger order or schema")
        handle.seek(0, os.SEEK_END)
        handle.write(json.dumps(row, sort_keys=True, separators=(",", ":")) + "\n")
        handle.flush()
        os.fsync(handle.fileno())
        if created:
            directory_fd = os.open(path.parent, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
            try:
                os.fsync(directory_fd)
            finally:
                os.close(directory_fd)
        return row


def require_identity(identity: dict | None) -> None:
    if not isinstance(identity, dict) or set(identity) != {
            "policy_digest", "map_digest", "source_digest"}:
        raise ValueError("current policy/map/source identity is absent or has extra keys")
    for key, value in identity.items():
        if not valid_digest(value):
            raise ValueError(f"current {key} is not a sha256 digest")


def file_sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def source_sha256(repo: Path) -> str:
    hasher = hashlib.sha256()
    for relative in WINDOW_SOURCE_PATHS:
        path = repo / relative
        if not path.is_file():
            raise FileNotFoundError(f"shadow source identity path is absent: {relative}")
        payload = path.read_bytes()
        hasher.update(relative.encode("utf-8") + b"\0")
        hasher.update(str(len(payload)).encode("ascii") + b"\0")
        hasher.update(payload)
    return hasher.hexdigest()


def policy_sha256(mode: object, changed_by: object, reason: object,
                  changed_at: object) -> str:
    if isinstance(changed_at, datetime):
        changed = changed_at.astimezone(timezone.utc).isoformat(timespec="microseconds")
    else:
        changed = str(changed_at)
    return digest({"mode": mode, "changed_by": changed_by,
                   "reason": reason, "changed_at": changed})


def current_identity(repo: Path, policy_row: tuple | list | None) -> dict:
    if not policy_row or len(policy_row) != 4 or policy_row[0] not in {"shadow", "enforced"}:
        raise ValueError("current rule-delivery policy row is absent")
    return {"policy_digest": policy_sha256(*policy_row),
            "map_digest": file_sha256(repo / "ops/config/rule-enforcement-map.json"),
            "source_digest": source_sha256(repo)}


def validate_record(row: dict) -> str | None:
    kind = row.get("record_type")
    if kind not in {"epoch", "disposition"}:
        return None
    keys = EPOCH_KEYS if kind == "epoch" else DISPOSITION_KEYS
    if set(row) != keys:
        return f"malformed {kind} record keys"
    if row.get("schema") != SCHEMA or stamp(row) is None:
        return f"malformed {kind} schema or timestamp"
    if row.get("record_id") != record_id(row):
        return f"malformed {kind} record_id"
    if not nonempty(row, tuple(keys - {"schema", "record_type", "record_id", "ts"})):
        return f"malformed {kind} empty field"
    if kind == "epoch":
        try:
            require_identity({key: row[key] for key in
                              ("policy_digest", "map_digest", "source_digest")})
        except ValueError as exc:
            return f"malformed epoch: {exc}"
    else:
        if not valid_digest(row.get("event_id")):
            return "malformed disposition event_id"
        if row.get("disposition") not in {"explained", "remediated"}:
            return "malformed disposition kind"
    return None


def finding(row: dict) -> bool:
    return ((isinstance(row.get("missed_rules"), list) and len(row["missed_rules"]) > 0)
            or (set(row) == OBSERVATION_ERROR_KEYS and isinstance(row.get("error"), str)))


def scoped(row: dict) -> bool:
    return (row.get("record_type") in {None, "observation"}
            and row.get("mode") == "shadow" and isinstance(row.get("loaded"), list)
            and len(row["loaded"]) > 0
            and isinstance(row.get("would_omit_count"), int)
            and not isinstance(row.get("would_omit_count"), bool)
            and row["would_omit_count"] > 0 and stamp(row) is not None)


def inspect(rows: list[dict]) -> dict:
    observations: dict[str, tuple[int, dict]] = {}
    dispositions: dict[str, tuple[int, dict]] = {}
    epochs: list[tuple[int, dict]] = []
    errors: list[str] = []
    seen_records: set[str] = set()
    newest_prior: datetime | None = None
    first_epoch_index = next((index for index, row in enumerate(rows)
                              if isinstance(row, dict)
                              and row.get("record_type") == "epoch"), None)
    for index, row in enumerate(rows):
        if not isinstance(row, dict):
            errors.append(f"malformed ledger row {index + 1}")
            continue
        kind = row.get("record_type")
        if kind not in {None, "observation", "epoch", "disposition"}:
            errors.append(f"malformed unknown record_type at row {index + 1}")
            continue
        if kind in {"epoch", "disposition"}:
            problem = validate_record(row)
            if problem:
                errors.append(f"{problem} at row {index + 1}")
                continue
            if row["record_id"] in seen_records:
                errors.append(f"duplicate ledger record {row['record_id']}")
            seen_records.add(row["record_id"])
            if kind == "epoch":
                epoch_seen = stamp(row)
                assert epoch_seen is not None
                if newest_prior is not None and epoch_seen < newest_prior:
                    errors.append(f"backdated/out-of-order epoch at row {index + 1}")
                epochs.append((index, row))
            else:
                target = row["event_id"]
                if target in dispositions:
                    errors.append(f"duplicate disposition for {target}")
                dispositions[target] = (index, row)
            seen = stamp(row)
            if seen is not None and (newest_prior is None or seen > newest_prior):
                newest_prior = seen
            continue
        if kind == "observation":
            problem = validate_observation(row)
            if problem:
                errors.append(f"{problem} at row {index + 1}")
                continue
        elif first_epoch_index is not None and index > first_epoch_index:
            errors.append(f"legacy observation after explicit epoch at row {index + 1}")
            continue
        seen = stamp(row)
        if seen is None:
            errors.append(f"malformed observation timestamp at row {index + 1}")
            continue
        event_id = observation_id(row)
        supplied = row.get("event_id")
        if supplied is not None and supplied != event_id:
            errors.append(f"malformed observation event_id at row {index + 1}")
        if event_id in observations:
            errors.append(f"duplicate observation event {event_id}")
        observations[event_id] = (index, row)
        if newest_prior is None or seen > newest_prior:
            newest_prior = seen
    for target, (disp_index, disp) in dispositions.items():
        observed = observations.get(target)
        if observed is None:
            errors.append(f"orphan disposition for {target}")
        else:
            disposition_seen = stamp(disp)
            observation_seen = stamp(observed[1])
            assert disposition_seen is not None and observation_seen is not None
            if disp_index <= observed[0] or disposition_seen < observation_seen:
                errors.append(f"disposition precedes observation {target}")
    strict_started = False
    last_strict: datetime | None = None
    for index, row in enumerate(rows):
        if not isinstance(row, dict):
            continue
        kind = row.get("record_type")
        valid = ((kind in {"epoch", "disposition"} and validate_record(row) is None)
                 or (kind == "observation" and validate_observation(row) is None))
        if kind == "epoch" and valid:
            strict_started = True
        if not strict_started or not valid:
            continue
        seen = stamp(row)
        assert seen is not None
        if last_strict is not None and seen < last_strict:
            errors.append(f"reverse-order strict timestamp at row {index + 1}")
        if last_strict is None or seen > last_strict:
            last_strict = seen
    return {"observations": observations, "dispositions": dispositions,
            "epochs": epochs, "errors": errors}


def can_start_epoch(rows: list[dict], identity: dict) -> tuple[bool, str]:
    try:
        require_identity(identity)
    except ValueError as exc:
        return False, str(exc)
    state = inspect(rows)
    if state["errors"]:
        return False, "; ".join(state["errors"])
    epochs = state["epochs"]
    start_index = epochs[-1][0] if epochs else -1
    findings = [(event_id, index, row) for event_id, (index, row)
                in state["observations"].items() if index > start_index and finding(row)]
    open_ids = [event_id for event_id, _index, _row in findings
                if event_id not in state["dispositions"]]
    if open_ids:
        return False, f"{len(open_ids)} finding(s) lack a disposition"
    if not epochs:
        return True, "initial explicit epoch after legacy findings were dispositioned"
    prior = epochs[-1][1]
    prior_identity = {key: prior[key] for key in
                      ("policy_digest", "map_digest", "source_digest")}
    if prior_identity != identity:
        return True, "policy/map/source identity changed"
    remediated = [event_id for event_id, _index, _row in findings
                  if state["dispositions"][event_id][1]["disposition"] == "remediated"]
    if remediated:
        return True, "post-remedy epoch required"
    return False, "no remediated finding or identity change permits rolling reset"


def validate_epoch_append(rows: list[dict], epoch: dict) -> None:
    problem = validate_record(epoch)
    if problem:
        raise ValueError(problem)
    epoch_seen = stamp(epoch)
    assert epoch_seen is not None
    prior = [seen for row in rows if isinstance(row, dict)
             and (seen := stamp(row)) is not None]
    if prior and epoch_seen < max(prior):
        raise ValueError("epoch timestamp is earlier than prior ledger evidence")
