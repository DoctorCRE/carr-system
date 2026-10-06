#!/usr/bin/env python3
"""Hermetic checks for the Joe prebrief runtime's phased lease failures."""
from __future__ import annotations

import importlib.util
from pathlib import Path
from typing import Any, cast


ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("calendar_prebrief_joe_runtime_under_test", ROOT / "tools/calendar-prebrief-joe-runtime.py")
assert spec and spec.loader
runtime: Any = cast(Any, importlib.util.module_from_spec(spec))
spec.loader.exec_module(runtime)

bad: list[str] = []


def check(name: str, value: bool) -> None:
    print(("  ok " if value else "  FAIL ") + name)
    if not value:
        bad.append(name)


claim = {"job_id": "00000000-0000-4000-8000-000000000001", "lease": "00000000-0000-4000-8000-000000000002", "scheduled_for": "2026-07-13T11:30:00Z"}
result = {"sponsor": "joe", "mode": "live", "attestation_id": "00000000-0000-4000-8000-000000000003", "receipt_id": "00000000-0000-4000-8000-000000000004",
          "unknown_attendees": {"count": 2, "attendee_keys": ["a" * 64, "b" * 64]}}
import tempfile  # noqa: E402
_last_run_dir = tempfile.TemporaryDirectory()
runtime.LAST_RUN = Path(_last_run_dir.name) / "calendar-prebrief-joe-last-run.json"
profile = {
    "CARR_CALENDAR_PREBRIEF_ENABLED": "true", "CARR_DB_JOBS_URL": "postgresql://carr_jobs:fixture@db.example/carr",  # ci-secret-scan: allow — inert fixture
    "CARR_CALENDAR_PREBRIEF_CHILD_PROFILE": "/fixture/child.env", "CARR_CALENDAR_PREBRIEF_COLLECTOR_PUBLIC_KEY": "/fixture/public.pem",
    "CARR_CALENDAR_PREBRIEF_ALLOWLIST": "/fixture/allowlist.json", "CARR_CALENDAR_PREBRIEF_COLLECTOR_PRIVATE_KEY": "/fixture/private.pem",
    "CARR_CALENDAR_PREBRIEF_COLLECTOR_VERSION": "fixture-1", "CARR_CALENDAR_PREBRIEF_EVENTKIT_APP": "/fixture/CARR Calendar Access.app",
}

# Filesystem/profile safety has its own adversarial suite. These fixtures begin
# at the exact jobs-lease boundary and record only query shape/phase ordering.
runtime._secure_file = lambda *_args, **_kwargs: None
runtime.verify_app = lambda _path: None
runtime.schedule = lambda _dsn: None


class FixtureCoordinator:
    class Refusal(RuntimeError):
        pass

    def __init__(self, events: list[str], *, refuse: Exception | None = None):
        self.events = events
        self.refuse = refuse

    def parent_execute(self, **kwargs):
        kwargs["after_claim"](claim)
        self.events.append("child_started")
        if self.refuse is not None:
            raise self.refuse
        return {"claim": dict(claim), "result": dict(result)}


def completion_receipt() -> dict[str, object]:
    return {"job_id": claim["job_id"], "attempt": 1, "state": "succeeded", "attestation_id": result["attestation_id"], "receipt_id": result["receipt_id"], "allowlist_revision_id": "00000000-0000-4000-8000-000000000005", "allowlist_digest": "a" * 64, "scheduled_for": claim["scheduled_for"]}


# 1. A pre-child heartbeat refusal records its own class and stops before the
# coordinator can launch the sponsor child.
pre_events: list[str] = []
pre_calls: list[tuple[str, tuple[object, ...]]] = []


def pre_jobs(_dsn: str, query: str, args=()):
    pre_calls.append((query, tuple(args)))
    if "heartbeat_job" in query:
        return False
    if "fail_job" in query:
        return "retry_wait"
    raise AssertionError(f"unexpected pre-child query: {query}")


runtime._coordinator = lambda: FixtureCoordinator(pre_events)
runtime._jobs_call = pre_jobs
try:
    runtime.run_tick(profile, Path("/fixture/runtime.env"))
except runtime.RecordedJobFailure as exc:
    check("pre-child lease loss keeps its exact failure class", exc.failure_class == runtime.PRE_CHILD_LEASE_CLASS and exc.state == "retry_wait")
else:
    check("pre-child lease loss keeps its exact failure class", False)
check("pre-child lease loss never starts the sponsor child", pre_events == [] and ["heartbeat_job" in query for query, _ in pre_calls] == [True, False])
check("pre-child failure receipt is not mislabeled as child refusal", pre_calls[-1][1][2] == runtime.PRE_CHILD_LEASE_CLASS)


# 2. A refusal raised by the bounded child remains the child-refusal class.
child_events: list[str] = []
child_calls: list[tuple[str, tuple[object, ...]]] = []
child_error = FixtureCoordinator.Refusal("fixture child refusal")


def child_jobs(_dsn: str, query: str, args=()):
    child_calls.append((query, tuple(args)))
    return True if "heartbeat_job" in query else "retry_wait"


runtime._coordinator = lambda: FixtureCoordinator(child_events, refuse=child_error)
runtime._jobs_call = child_jobs
try:
    runtime.run_tick(profile, Path("/fixture/runtime.env"))
except runtime.RecordedJobFailure as exc:
    check("an actual child refusal keeps the child-refusal class", exc.failure_class == runtime.CHILD_REFUSAL_CLASS and exc.__cause__ is child_error)
else:
    check("an actual child refusal keeps the child-refusal class", False)


# 3. Once the child returned, heartbeat/completion refusal is a separate phase.
post_events: list[str] = []
post_calls: list[tuple[str, tuple[object, ...]]] = []
heartbeats = 0


def post_jobs(_dsn: str, query: str, args=()):
    global heartbeats
    post_calls.append((query, tuple(args)))
    if "heartbeat_job" in query:
        heartbeats += 1
        return heartbeats == 1
    if "fail_job" in query:
        return "retry_wait"
    raise AssertionError("completion must not run after the post-child heartbeat refused")


runtime._coordinator = lambda: FixtureCoordinator(post_events)
runtime._jobs_call = post_jobs
try:
    runtime.run_tick(profile, Path("/fixture/runtime.env"))
except runtime.RecordedJobFailure as exc:
    check("post-child protection failure keeps its exact failure class", exc.failure_class == runtime.POST_CHILD_FAILURE_CLASS)
else:
    check("post-child protection failure keeps its exact failure class", False)
check("post-child failure receipt is not mislabeled as child refusal", post_events == ["child_started"] and post_calls[-1][1][2] == runtime.POST_CHILD_FAILURE_CLASS)


# 3b. Completion refusal after a successful second heartbeat has the same
# post-child class; it cannot fall back to a child-refusal label either.
completion_events: list[str] = []
completion_calls: list[tuple[str, tuple[object, ...]]] = []
completion_error = runtime.Refusal("fixture completion receipt refusal")


def completion_jobs(_dsn: str, query: str, args=()):
    completion_calls.append((query, tuple(args)))
    if "heartbeat_job" in query:
        return True
    if "complete_calendar_prebrief_joe_live_job" in query:
        raise completion_error
    if "fail_job" in query:
        return "retry_wait"
    raise AssertionError(f"unexpected completion-refusal query: {query}")


runtime._coordinator = lambda: FixtureCoordinator(completion_events)
runtime._jobs_call = completion_jobs
try:
    runtime.run_tick(profile, Path("/fixture/runtime.env"))
except runtime.RecordedJobFailure as exc:
    check("completion refusal keeps the post-child class and original cause", exc.failure_class == runtime.POST_CHILD_FAILURE_CLASS and exc.__cause__ is completion_error)
else:
    check("completion refusal keeps the post-child class and original cause", False)
check("completion refusal writes only the post-child failure class", completion_calls[-1][1][2] == runtime.POST_CHILD_FAILURE_CLASS)


# 4. A rejected fail_job surfaces both events: the original child cause stays
# chained while the exact recording rejection remains inspectable.
rejected_events: list[str] = []
recording_error = runtime.Refusal("fixture fail_job rejection")


def rejected_jobs(_dsn: str, query: str, args=()):
    if "heartbeat_job" in query:
        return True
    if "fail_job" in query:
        raise recording_error
    raise AssertionError(f"unexpected rejected-receipt query: {query}")


runtime._coordinator = lambda: FixtureCoordinator(rejected_events, refuse=child_error)
runtime._jobs_call = rejected_jobs
try:
    runtime.run_tick(profile, Path("/fixture/runtime.env"))
except runtime.FailureReceiptRejected as exc:
    check("rejected fail_job preserves the original phase and cause", exc.failure_class == runtime.CHILD_REFUSAL_CLASS and exc.__cause__ is child_error)
    check("rejected fail_job surfaces its own recording error separately", exc.recording_error is recording_error)
else:
    check("rejected fail_job preserves the original phase and cause", False)
    check("rejected fail_job surfaces its own recording error separately", False)


# 5. The ordinary success path renews before and after child execution, then
# completes exactly once.
success_events: list[str] = []
success_calls: list[tuple[str, tuple[object, ...]]] = []


def success_jobs(_dsn: str, query: str, args=()):
    success_calls.append((query, tuple(args)))
    if "heartbeat_job" in query:
        return True
    if "complete_calendar_prebrief_joe_live_job" in query:
        return completion_receipt()
    raise AssertionError(f"unexpected success query: {query}")


runtime._coordinator = lambda: FixtureCoordinator(success_events)
runtime._jobs_call = success_jobs
success = runtime.run_tick(profile, Path("/fixture/runtime.env"))
check("success renews both lease phases before exact completion", ["heartbeat_job" in query for query, _ in success_calls] == [True, True, False])
check("success remains in the dedicated Joe live boundary", success_events == ["child_started"] and success["claimed"] == 1 and success["completion"]["state"] == "succeeded")
import json  # noqa: E402
_summary = json.loads(runtime.LAST_RUN.read_text(encoding="utf-8"))
check("success reports skipped unknown attendees as count plus opaque keys (0735)",
      success["unknown_attendees"] == result["unknown_attendees"]
      and _summary["unknown_attendees"] == result["unknown_attendees"]
      and "@" not in runtime.LAST_RUN.read_text(encoding="utf-8"))
_missing = dict(result); _missing.pop("unknown_attendees")
try:
    runtime.complete("postgresql://carr_jobs:fixture@db.example/carr", claim, _missing)  # ci-secret-scan: allow — inert fixture
    check("a child result without the unknown-attendee report is refused", False)
except runtime.Refusal:
    check("a child result without the unknown-attendee report is refused", True)

print("PASS" if not bad else "FAIL " + ", ".join(bad))
raise SystemExit(bool(bad))
