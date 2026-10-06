#!/usr/bin/env python3
"""Contract tests for Slice 1 of the canonical room queue.

No Hermes task or room row is created here.  The adapter receives a recording
runner and the bridge receives in-memory room functions, which makes the two
important mutations (route-before-consume and removed idempotency) observable.
"""

from __future__ import annotations

import inspect
import json
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import bridge  # noqa: E402
import grammar  # noqa: E402
import kanban_adapter  # noqa: E402
import queue_grammar  # noqa: E402
import queue_projection  # noqa: E402
import state as state_mod  # noqa: E402


FAILURES: list[str] = []


def check(label, fn):
    try:
        fn()
    except AssertionError as exc:
        FAILURES.append(f"{label}: {exc}")
        print(f"  FAIL  {label}\n          {exc}")
    except Exception as exc:  # noqa: BLE001
        FAILURES.append(f"{label}: unexpected {exc!r}")
        print(f"  FAIL  {label}\n          unexpected {exc!r}")
    else:
        print(f"  ok    {label}")


CATALOG = {
    "v": 1,
    "targets": {
        "sol": {"enabled": True, "adapter": "desk", "assignee": "desk:codex-desk",
                "capabilities": ["read"], "effective_model": "gpt-5.6-sol"},
        "claude": {"enabled": True, "adapter": "desk", "assignee": "desk:joe-desk",
                   "capabilities": ["read", "repo-write", "record-write"], "effective_model": "claude"},
        "joe": {"enabled": True, "adapter": "manual", "assignee": "human:joe",
                "capabilities": ["merge-approve", "production", "external-send", "destructive", "credential"],
                "effective_model": "Joe manual lane"},
        "retired": {"enabled": False, "adapter": "hermes", "assignee": "builder",
                    "capabilities": ["read"], "unavailable_reason": "retired"},
    },
}


def turn(body, *, msg_id="11111111-1111-4111-8111-111111111111", seat="codex", seq=12,
         origin_channel="mcp", origin_actor="codex"):
    return {"body": body, "msg_id": msg_id, "seat": seat, "sponsor": "joe", "seq": seq,
            "origin_channel": origin_channel, "origin_actor": origin_actor}


def test_room_writer_binds_nondefault_room_without_overriding_explicit_calls():
    calls = []

    def writer(**kwargs):
        calls.append(kwargs)
        return {"ok": True}

    bound = bridge.bind_room_writer(writer, "model-room")
    bound(body="nonce", seat="claude")
    bound(body="legacy", seat="claude", room="partner-line")
    assert calls[0]["room"] == "model-room"
    assert calls[1]["room"] == "partner-line"


def test_strict_enqueue_and_bounds():
    parsed = queue_grammar.parse(turn(
        "@queue enqueue target=sol cap=read priority=P1 runtime=45m key=attest finish=review :: Attest PR 514\n"
        "Run mutation checks."), CATALOG)
    assert parsed.kind == "enqueue"
    assert parsed.value["priority"] == 3
    assert parsed.value["runtime"] == "45m"
    assert parsed.value["finish"] == "review"
    assert parsed.value["idempotency_key"] == "room:partner-line:attest"

    for body, code in [
        ("@queue enqueue target=sol cap=read model=anything :: nope", "field_unknown"),
        ("@queue enqueue target=sol cap=read target=grok :: nope", "field_duplicate"),
        ("@queue enqueue target=nope cap=read :: nope", "target_unknown"),
        ("@queue enqueue target=sol cap=production :: nope", "capability_human_only"),
        ("@queue enqueue target=retired cap=read :: nope", "target_disabled"),
        ("@queue enqueue target=sol cap=read :: " + ("x" * 201), "title_invalid"),
        ("@queue enqueue target=sol :: no capability", "field_required"),
    ]:
        outcome = queue_grammar.parse(turn(body), CATALOG)
        assert outcome.kind == "rejected", (body, outcome)
        assert outcome.code == code, (body, outcome.code)


def test_shared_key_converges_and_source_id_deduplicates():
    first = queue_grammar.parse(turn("@queue enqueue target=sol cap=read key=same :: One"), CATALOG)
    second = queue_grammar.parse(turn("@queue enqueue target=sol cap=read key=same :: Two", msg_id="22222222-2222-4222-8222-222222222222"), CATALOG)
    replay = queue_grammar.parse(turn("@queue enqueue target=sol cap=read :: One"), CATALOG)
    assert first.value["idempotency_key"] == second.value["idempotency_key"] == "room:partner-line:same"
    assert replay.value["idempotency_key"] == "room:partner-line:11111111-1111-4111-8111-111111111111"


def test_no_target_means_auto_and_a_named_target_is_kept():
    routed = queue_grammar.parse(turn("@queue enqueue cap=read :: Shorten this"), CATALOG)
    assert routed.kind == "enqueue" and routed.value["target"] == queue_grammar.AUTO_TARGET, routed
    pinned = queue_grammar.parse(turn("@queue enqueue target=sol cap=read :: Attest PR 514"), CATALOG)
    assert pinned.value["target"] == "sol", pinned
    # Routing never widens authority: a human-only capability is refused before any target is picked.
    human = queue_grammar.parse(turn("@queue enqueue cap=merge-approve :: Approve the merge"), CATALOG)
    assert human.kind == "rejected", human


def test_targets_and_status_parse_without_model():
    assert queue_grammar.parse(turn("@queue targets"), CATALOG).kind == "targets"
    by_id = queue_grammar.parse(turn("@queue status id=t_1234abcd"), CATALOG)
    assert by_id.kind == "status" and by_id.value == {"id": "t_1234abcd"}
    by_state = queue_grammar.parse(turn("@queue status state=review target=sol"), CATALOG)
    assert by_state.kind == "status" and by_state.value["state"] == "review"


def test_server_origin_not_claimed_human_seat_governs_human_only_enqueue():
    attempted = turn("@queue enqueue target=joe cap=merge-approve :: Approve the merge",
                     seat="human", origin_channel="mcp", origin_actor="codex")
    parsed = queue_grammar.parse(attempted, CATALOG)
    assert parsed.kind == "rejected"
    assert parsed.code == "capability_human_only"


def test_browser_human_can_create_only_manual_human_lane_work():
    allowed = queue_grammar.parse(turn(
        "@queue enqueue target=joe cap=merge-approve :: Approve the merge",
        seat="human", origin_channel="browser-human", origin_actor="joe"), CATALOG)
    assert allowed.kind == "enqueue"
    assert allowed.value["manual"] is True
    assert allowed.value["finish"] == "review"
    refused = queue_grammar.parse(turn(
        "@queue enqueue target=claude cap=merge-approve :: Approve the merge",
        seat="human", origin_channel="browser-human", origin_actor="joe"), CATALOG)
    assert refused.kind == "rejected" and refused.code == "capability_human_lane_required"
    legacy = queue_grammar.parse(turn(
        "@queue enqueue target=joe cap=read :: Do work", origin_channel="legacy", origin_actor="legacy"), CATALOG)
    assert legacy.kind == "rejected" and legacy.code == "origin_untrusted"


def test_adapter_constructs_fixed_canonical_create_and_idempotency():
    seen = []

    def runner(argv):
        seen.append(argv)
        return {"id": "t_queue0001", "created": True}

    command = queue_grammar.parse(turn("@queue enqueue target=sol cap=read after=t_parent :: Verify queue"), CATALOG).value
    adapter = kanban_adapter.KanbanAdapter(runner=runner)
    result = adapter.create(command, turn("ignored"), CATALOG["targets"]["sol"])
    argv = seen[0]
    assert result["task_id"] == "t_queue0001"
    assert argv[:5] == ["hermes", "kanban", "--board", "carr-build", "create"]
    assert "--project" in argv and argv[argv.index("--project") + 1] == "carr"
    assert "--idempotency-key" in argv
    assert argv[argv.index("--idempotency-key") + 1] == command["idempotency_key"]
    assert "--max-retries" in argv
    assert argv[argv.index("--max-retries") + 1] == "3"
    assert "--parent" in argv and argv[argv.index("--parent") + 1] == "t_parent"
    body = argv[argv.index("--body") + 1]
    assert body.startswith("[CARR_QUEUE_META ") and '"cap":"read"' in body


def test_adapter_normalizes_worker_bigint_sequence_to_integer_metadata():
    seen = []
    command = queue_grammar.parse(
        turn("@queue enqueue target=sol cap=read :: Verify queue"), CATALOG).value
    adapter = kanban_adapter.KanbanAdapter(
        runner=lambda argv: seen.append(argv) or {"id": "t_queue0002", "created": True})
    adapter.create(command, turn("ignored", seq="8522"), CATALOG["targets"]["sol"])
    body = seen[0][seen[0].index("--body") + 1]
    meta = json.loads(body.split("]", 1)[0][len("[CARR_QUEUE_META "):])
    assert meta["source_seq"] == 8522
    assert isinstance(meta["source_seq"], int)


def test_catalog_exactly_allowlists_yellow_and_human_capabilities():
    catalog = kanban_adapter.load_catalog()
    assert catalog["targets"]["sol"]["capabilities"] == ["read"]
    assert catalog["targets"]["claude"]["capabilities"] == ["read", "repo-write", "record-write"]
    human_only = set(queue_grammar.HUMAN_ONLY)
    for alias, entry in catalog["targets"].items():
        if alias == "joe":
            assert set(entry["capabilities"]) == human_only
        else:
            assert not human_only.intersection(entry["capabilities"]), alias


def test_manual_create_is_atomically_blocked_before_dispatch_can_see_it():
    seen = []
    command = queue_grammar.parse(turn("@queue enqueue target=joe cap=production :: Release", seat="human",
                                      origin_channel="browser-human", origin_actor="joe"), CATALOG).value
    adapter = kanban_adapter.KanbanAdapter(runner=lambda argv: seen.append(argv) or {"id": "t_manual0001", "created": True})
    adapter.create(command, turn("ignored"), CATALOG["targets"]["joe"])
    argv = seen[0]
    assert argv[argv.index("--initial-status") + 1] == "blocked"


def test_adapter_reclaims_only_through_the_supported_hermes_transition():
    seen = []
    adapter = kanban_adapter.KanbanAdapter(command_runner=lambda argv: seen.append(argv) or "")
    adapter.reclaim("t_queue0001", "queue_transient:provider_quota")
    assert seen == [[
        "hermes", "kanban", "--board", "carr-build", "reclaim", "t_queue0001",
        "--reason", "queue_transient:provider_quota",
    ]]


def test_adapter_uses_only_canonical_queue_reclaim_evidence_for_attempts():
    payload = {
        "task": {"id": "t_queue0001", "max_retries": 3},
        "events": [
            {"kind": "reclaimed", "payload": {"reason": "queue_transient:provider_quota"}},
            {"kind": "reclaimed", "payload": {"reason": "operator recovery"}},
        ],
    }
    adapter = kanban_adapter.KanbanAdapter(runner=lambda _argv: payload)
    assert adapter.retry_attempt("t_queue0001") == (1, 3)
    malformed = kanban_adapter.KanbanAdapter(runner=lambda _argv: {"task": {"max_retries": 3}})
    try:
        malformed.retry_attempt("t_queue0001")
    except kanban_adapter.QueueError as exc:
        assert exc.code == "queue_unavailable"
    else:
        raise AssertionError("missing canonical evidence must fail closed")


def test_mutation_guard_idempotency_is_not_optional():
    """Removing the idempotency flag makes this test fail before a bridge retry can duplicate work."""
    source = inspect.getsource(kanban_adapter.KanbanAdapter.create)
    assert '"--idempotency-key"' in source


def test_service_creates_for_every_room_seat_and_keeps_status_bounded():
    class FakeAdapter:
        def __init__(self):
            self.creates = []

        def create(self, command, incoming, target):
            self.creates.append((command, incoming, target))
            return {"task_id": "t_queue0001", "created": False}

        def block(self, *_args, **_kwargs):
            raise AssertionError("a duplicate must not be reblocked")

        def show(self, task_id):
            return {"task": {"id": task_id, "title": "x" * 1000, "status": "todo", "body": "never receipt this"}}

        def list(self, state, target, catalog):
            return {"tasks": [{"id": "t_queue0002", "title": "A", "status": state, "assignee": "desk:codex-desk"}]}

    adapter = FakeAdapter()
    service = kanban_adapter.QueueService(catalog=CATALOG, adapter=adapter)
    for seat in ("human", "claude", "hermes", "codex", "grok"):
        result = service.handle(turn("@queue enqueue target=sol cap=read key=shared :: Read only", seat=seat), room="partner-line")
        accepted = result["receipt"]["queue_accepted"]
        assert accepted["task_id"] == "t_queue0001" and accepted["status"] == "duplicate"
    assert len(adapter.creates) == 5
    status = service.handle(turn("@queue status id=t_queue0001"), room="partner-line")
    rendered = json.dumps(status["receipt"])
    assert "never receipt this" not in rendered and len(rendered) < 1000


def test_create_before_receipt_crash_replays_one_immutable_card():
    """A second ingress after a lost room receipt may only receive Hermes' original card."""
    class FakeAdapter:
        def __init__(self):
            self.calls = []
            self.created = False

        def create(self, command, incoming, target):
            self.calls.append((command, incoming, target))
            first = not self.created
            self.created = True
            return {"task_id": "t_queue0001", "created": first}

    adapter = FakeAdapter()
    service = kanban_adapter.QueueService(catalog=CATALOG, adapter=adapter)
    incoming = turn("@queue enqueue target=sol cap=read key=crash-safe :: Read only")
    first = service.handle(incoming, room="partner-line")
    replay = service.handle(incoming, room="partner-line")
    assert first["receipt"]["queue_accepted"] == {
        "source_seq": 12, "source_msg_id": incoming["msg_id"], "task_id": "t_queue0001",
        "target": "sol", "cap": "read", "idempotency_key": "room:partner-line:crash-safe", "status": "created",
    }
    assert replay["receipt"]["queue_accepted"]["task_id"] == "t_queue0001"
    assert replay["receipt"]["queue_accepted"]["status"] == "duplicate"
    assert len(adapter.calls) == 2
    assert adapter.calls[0][0]["idempotency_key"] == adapter.calls[1][0]["idempotency_key"]
    assert adapter.calls[0][0]["body"] == adapter.calls[1][0]["body"]


def test_human_manual_lane_is_canonically_blocked_and_sol_write_never_creates():
    class FakeAdapter:
        def __init__(self):
            self.creates = []

        def create(self, command, incoming, target):
            self.creates.append((command, target))
            return {"task_id": "t_manual0001", "created": True}

    adapter = FakeAdapter()
    service = kanban_adapter.QueueService(catalog=CATALOG, adapter=adapter)
    human = turn("@queue enqueue target=joe cap=production :: Release it", seat="human",
                 origin_channel="browser-human", origin_actor="joe")
    out = service.handle(human, room="partner-line")
    assert out["receipt"]["queue_accepted"]["status"] == "blocked"
    command = adapter.creates[0][0]
    assert command["manual"] is True
    sol_write = turn("@queue enqueue target=sol cap=repo-write :: Change it")
    rejected = service.handle(sol_write, room="partner-line")
    assert rejected["receipt"]["queue_rejected"]["code"] == "capability_target_refused"
    assert len(adapter.creates) == 1


def test_legacy_assignment_is_deprecated_without_a_local_log():
    posted = []
    outcome, parsed, _reason = grammar.classify(turn("@sol assign WR-9 Legacy title", seat="human"))
    assert outcome == "ok" and parsed is not None
    result = grammar.apply_assignment(turn("ignored", seat="human"), parsed,
                                      add_room_turn=lambda **kwargs: posted.append(kwargs) or {"seq": 13})
    assert result["record"]["status"] == "deprecated_use_queue"
    assert posted and "assignment_deprecated" in posted[0]["body"]
    assert "DEFAULT_ASSIGNMENTS_LOG" not in inspect.getsource(grammar)


def test_bridge_consumes_queue_before_routing_and_posts_receipt():
    class FakeQueue:
        def handle(self, incoming, *, room):
            assert room == "partner-line"
            return {"handled": True, "kind": "targets", "receipt": {"queue_targets": {"targets": []}}}

    posted = []
    def add_room_turn(**kwargs):
        posted.append(kwargs)
        return {"seq": 13}

    with tempfile.TemporaryDirectory() as root:
        state_path = Path(root) / "state.json"
        summary = bridge.run_once(
            state_path=state_path,
            read_room=lambda *_args, **_kwargs: {"turns": [turn("@queue targets")]},
            add_room_turn=add_room_turn,
            registry=type("Registry", (), {"entries": lambda self: {}, "path": Path(root) / "desks.json"})(),
            queue_service=FakeQueue(),
            read_profiles=lambda: [],
            log=lambda _msg: None,
        )
    assert summary["routed"]["11111111-1111-4111-8111-111111111111"] == []
    assert summary["queue"][0]["kind"] == "targets"
    assert posted and posted[0]["kind"] == "receipt"


def test_bridge_reconciliation_failure_fails_closed_before_queue_dispatch():
    class FakeQueue:
        catalog = {"targets": {"sol": {"enabled": True, "adapter": "desk", "desk": "codex-desk"}}}
        def reconcile_disabled_targets(self):
            raise RuntimeError("raw provider secret")

    class FakeExecutor:
        catalog = FakeQueue.catalog
        def start(self, **_kwargs):
            raise AssertionError("queue dispatch must be fenced")

    registry = type("Registry", (), {
        "entries": lambda self: {"codex-desk": {"room_seat": "sol"}},
        "path": Path("/tmp/nonexistent-desks.json"),
    })()
    with tempfile.TemporaryDirectory() as root:
        summary = bridge.run_once(
            state_path=Path(root) / "state.json", read_room=lambda *_a, **_k: {"turns": []},
            add_room_turn=lambda **_k: {"seq": 1}, registry=registry,
            queue_service=FakeQueue(), queue_executor=FakeExecutor(), queue_projector=lambda **_k: [],
            probe_auth=lambda _entry: False, read_profiles=lambda: [], log=lambda _msg: None,
        )
    assert summary["queue_reconciliation"] == {"scanned": 0, "blocked": [], "diagnostics": []}
    assert summary["errors"] == [{"desk": "(queue-reconciler)", "error": "queue_reconciliation_failed",
                                   "detail": "queue reconciliation failed"}]


def test_bridge_invalid_reconciliation_shape_fails_closed():
    class FakeQueue:
        catalog = {"targets": {"sol": {"enabled": True, "adapter": "desk", "desk": "codex-desk"}}}
        def reconcile_disabled_targets(self):
            return {"scanned": 0, "blocked": [],
                    "diagnostics": [{"code": "queue_metadata_malformed", "task_id": "raw-secret"}]}

    class FakeExecutor:
        catalog = FakeQueue.catalog
        def start(self, **_kwargs):
            raise AssertionError("invalid reconciliation must fence dispatch")

    registry = type("Registry", (), {"entries": lambda self: {"codex-desk": {"room_seat": "sol"}},
                                      "path": Path("/tmp/nonexistent-desks.json")})()
    with tempfile.TemporaryDirectory() as root:
        summary = bridge.run_once(state_path=Path(root) / "state.json",
            read_room=lambda *_a, **_k: {"turns": []}, add_room_turn=lambda **_k: {"seq": 1},
            registry=registry, queue_service=FakeQueue(), queue_executor=FakeExecutor(),
            queue_projector=lambda **_k: [], probe_auth=lambda _entry: False,
            read_profiles=lambda: [], log=lambda _msg: None)
    assert summary["errors"][0]["error"] == "queue_reconciliation_failed"
    assert "raw-secret" not in json.dumps(summary)


def test_projector_health_is_persisted_and_redacts_failure_detail():
    """A failed pass must age visibly without leaking its underlying failure."""
    class FakeQueue:
        catalog = CATALOG

        def handle(self, _incoming, *, room):
            assert room == "partner-line"
            return {"handled": False}

    with tempfile.TemporaryDirectory() as root:
        state_path = Path(root) / "state.json"
        bridge.run_once(
            state_path=state_path,
            read_room=lambda *_args, **_kwargs: {"turns": []},
            add_room_turn=lambda **_kwargs: {"seq": 13},
            registry=type("Registry", (), {"entries": lambda self: {}, "path": Path(root) / "desks.json"})(),
            queue_service=FakeQueue(),
            queue_projector=lambda **_kwargs: (_ for _ in ()).throw(RuntimeError("provider raw secret")),
            read_profiles=lambda: [],
            log=lambda _msg: None,
        )
        saved = json.loads(state_path.read_text())
    assert saved["queue_projection_checked_at"]
    assert saved["queue_projection_last_success_at"] is None
    assert saved["queue_projection_error"] == "queue_projection_failed"
    assert "secret" not in json.dumps(saved)


def test_projector_health_records_a_successful_empty_check():
    class FakeQueue:
        catalog = CATALOG

        def handle(self, _incoming, *, room):
            assert room == "partner-line"
            return {"handled": False}

    with tempfile.TemporaryDirectory() as root:
        state_path = Path(root) / "state.json"
        bridge.run_once(
            state_path=state_path,
            read_room=lambda *_args, **_kwargs: {"turns": []},
            add_room_turn=lambda **_kwargs: {"seq": 13},
            registry=type("Registry", (), {"entries": lambda self: {}, "path": Path(root) / "desks.json"})(),
            queue_service=FakeQueue(), queue_projector=lambda **_kwargs: [],
            now_fn=lambda: "2026-08-24T12:00:00+00:00", read_profiles=lambda: [], log=lambda _msg: None,
        )
        saved = json.loads(state_path.read_text())
    assert saved["queue_projection_checked_at"] == "2026-08-24T12:00:00+00:00"
    assert saved["queue_projection_last_success_at"] == "2026-08-24T12:00:00+00:00"
    assert saved["queue_projection_error"] is None


def test_projector_identity_rejection_cannot_record_local_health():
    """PR #605 mocked append success and never exercised writer identity."""
    class FakeQueue:
        catalog = CATALOG

        def handle(self, _incoming, *, room):
            return {"handled": False}

    def projector(**kwargs):
        health = queue_projection.projection_health_receipt(
            checked_at=kwargs["checked_at"], cursor=0, projection_digest=None)
        kwargs["add_room_turn"](
            body=json.dumps(health, separators=(",", ":")), seat="hermes", kind="receipt",
            msg_id=queue_projection.health_msg_id("carr-build", 0, kwargs["checked_at"]))
        return []

    def rejected_provenance(**_kwargs):
        raise RuntimeError("project-room-queue rejected provenance")

    with tempfile.TemporaryDirectory() as root:
        state_path = Path(root) / "state.json"
        summary = bridge.run_once(
            state_path=state_path, read_room=lambda *_a, **_k: {"turns": []},
            add_room_turn=lambda **_k: {"seq": 1}, project_room_turn=rejected_provenance,
            registry=type("Registry", (), {"entries": lambda self: {}, "path": Path(root) / "desks.json"})(),
            queue_service=FakeQueue(), queue_projector=projector,
            now_fn=lambda: "2026-08-24T12:00:00+00:00", read_profiles=lambda: [], log=lambda _msg: None)
        saved = json.loads(state_path.read_text())
    assert saved["queue_projection_last_success_at"] is None
    assert saved["queue_projection_error"] == "queue_projection_failed"
    assert any(error["desk"] == "(queue-projector)" for error in summary["errors"])


def test_bridge_passes_one_cycle_stamp_to_projector_for_idle_health_receipt():
    class FakeQueue:
        catalog = {"targets": {}}

        def handle(self, _incoming, *, room):
            return {"handled": False}

    projector_calls = []
    posted = []

    def projector(**kwargs):
        projector_calls.append(kwargs)
        health = queue_projection.projection_health_receipt(
            checked_at=kwargs["checked_at"], cursor=0, projection_digest=None)
        kwargs["add_room_turn"](
            body=json.dumps(health, separators=(",", ":")), seat="hermes",
            kind="receipt", msg_id=queue_projection.health_msg_id("carr-build", 0, kwargs["checked_at"]))
        return []

    with tempfile.TemporaryDirectory() as root:
        state_path = Path(root) / "state.json"
        bridge.run_once(
            state_path=state_path, read_room=lambda *_a, **_k: {"turns": []},
            add_room_turn=lambda **kwargs: posted.append(kwargs) or {"seq": 1},
            project_room_turn=lambda **kwargs: posted.append(kwargs) or {"seq": 1},
            registry=type("Registry", (), {"entries": lambda self: {}, "path": Path(root) / "desks.json"})(),
            queue_service=FakeQueue(), queue_projector=projector,
            now_fn=lambda: "2026-08-24T12:00:00+00:00", read_profiles=lambda: [], log=lambda _msg: None,
        )
        saved = json.loads(state_path.read_text())
    assert projector_calls[0]["checked_at"] == "2026-08-24T12:00:00+00:00"
    assert json.loads(posted[0]["body"])["queue_projection_health"]["status"] == "ok"
    assert saved["queue_projection_last_success_at"] == "2026-08-24T12:00:00+00:00"


def test_corrupt_retry_timing_is_migration_safe_and_bounded():
    with tempfile.TemporaryDirectory() as root:
        state_path = Path(root) / "state.json"
        state_path.write_text(json.dumps({"queue_retry_at": ["not", "a", "mapping"]}))
        assert state_mod.load_state(state_path)["queue_retry_at"] == {}
        entries = {f"t_{i:08x}": "2026-08-24T12:00:00+00:00" for i in range(state_mod.QUEUE_RETRY_CAP + 1)}
        state_path.write_text(json.dumps({"queue_retry_at": entries}))
        assert len(state_mod.load_state(state_path)["queue_retry_at"]) == state_mod.QUEUE_RETRY_CAP


def test_mutation_guard_queue_parse_precedes_route_turn():
    """Moving route_turn above queue classification reopens desk injection for malformed commands."""
    source = inspect.getsource(bridge.run_once)
    assert source.index("queue_service.handle") < source.index("state_mod.route_turn")


def test_disabled_target_reconciliation_is_exact_and_terminal_safe():
    def body(target, *, finish="done", valid=True):
        if not valid:
            return "ordinary prose with no envelope"
        meta = {"v": 1, "target": target, "cap": "read", "source_seq": 1,
                "source_msg_id": "msg-1", "finish": finish}
        return "[CARR_QUEUE_META " + json.dumps(meta, separators=(",", ":")) + "]\nwork"

    rows = [
        {"id": "t_retired01", "status": "ready", "assignee": "builder", "body": body("ox-alpha")},
        {"id": "t_cross_b", "status": "ready", "assignee": "builder", "body": body("retired-b")},
        {"id": "t_cross_a", "status": "ready", "assignee": "reviewer", "body": body("ox-alpha")},
        {"id": "t_retired_b", "status": "ready", "assignee": "reviewer", "body": body("retired-b")},
        {"id": "t_review01", "status": "review", "assignee": "builder", "body": body("ox-alpha")},
        {"id": "t_other01", "status": "ready", "assignee": "builder", "body": body("grok")},
        {"id": "t_statusless", "assignee": "builder", "body": body("ox-alpha")},
        {"id": "t_bad01", "status": "ready", "assignee": "builder", "body": body("ox-alpha", valid=False)},
    ]
    calls = []
    adapter = kanban_adapter.KanbanAdapter(
        runner=lambda argv: calls.append(argv) or rows,
        command_runner=lambda argv: calls.append(argv) or "",
    )
    result = adapter.reconcile_disabled_targets({"v": 1, "targets": {
        "ox-alpha": {"enabled": False, "assignee": "builder"},
        "retired-b": {"enabled": False, "assignee": "reviewer"},
        "grok": {"enabled": True, "assignee": "builder"},
    }})
    assert result["blocked"] == ["t_retired01", "t_retired_b"]
    assert result["diagnostics"] == [{"code": "queue_metadata_malformed", "task_id": "t_bad01"}]
    reads = [call for call in calls if call[4:6] == ["list", "--assignee"]]
    assert len(reads) == 2 and all(read[-1] == "--json" for read in reads)


def test_disabled_target_reconciliation_is_idempotent_and_mutation_narrow():
    meta = {"v": 1, "target": "ox-alpha", "cap": "read", "source_seq": 1,
            "source_msg_id": "msg-1", "finish": "done"}
    active = {"id": "t_retired02", "status": "ready", "assignee": "builder",
              "body": "[CARR_QUEUE_META " + json.dumps(meta) + "]\nwork"}
    state = {"blocked": False}
    calls = []

    def runner(argv):
        calls.append(argv)
        return [] if state["blocked"] else [active]

    def mutate(argv):
        calls.append(argv)
        state["blocked"] = True

    adapter = kanban_adapter.KanbanAdapter(runner=runner, command_runner=mutate)
    catalog = {"v": 1, "targets": {"ox-alpha": {"enabled": False, "assignee": "builder"}}}
    assert adapter.reconcile_disabled_targets(catalog)["blocked"] == ["t_retired02"]
    assert adapter.reconcile_disabled_targets(catalog)["blocked"] == []
    assert [call[4] for call in calls if len(call) > 4] == ["list", "block", "list"]
    assert all("claim" not in call and "reassign" not in call and "unblock" not in call for call in calls)


def main():
    check("bridge writers stay on the room being polled", test_room_writer_binds_nondefault_room_without_overriding_explicit_calls)
    check("strict enqueue grammar and bounds", test_strict_enqueue_and_bounds)
    check("shared key and delivery idempotency", test_shared_key_converges_and_source_id_deduplicates)
    check("targets and status are model-free reads", test_targets_and_status_parse_without_model)
    check("server origin beats claimed human seat", test_server_origin_not_claimed_human_seat_governs_human_only_enqueue)
    check("browser humans use only the manual human lane", test_browser_human_can_create_only_manual_human_lane_work)
    check("adapter fixed canonical create", test_adapter_constructs_fixed_canonical_create_and_idempotency)
    check("Worker bigint sequence normalizes to integer metadata", test_adapter_normalizes_worker_bigint_sequence_to_integer_metadata)
    check("catalog explicitly denies unreviewed capability grants", test_catalog_exactly_allowlists_yellow_and_human_capabilities)
    check("manual create is atomically blocked", test_manual_create_is_atomically_blocked_before_dispatch_can_see_it)
    check("retry reclaim uses only the canonical Hermes transition", test_adapter_reclaims_only_through_the_supported_hermes_transition)
    check("retry count comes only from canonical Hermes evidence", test_adapter_uses_only_canonical_queue_reclaim_evidence_for_attempts)
    check("all model seats enqueue and status is bounded", test_service_creates_for_every_room_seat_and_keeps_status_bounded)
    check("create-before-receipt crash replays one immutable card", test_create_before_receipt_crash_replays_one_immutable_card)
    check("manual cards block and Sol refuses writes before create", test_human_manual_lane_is_canonically_blocked_and_sol_write_never_creates)
    check("legacy assignment is deprecation-only", test_legacy_assignment_is_deprecated_without_a_local_log)
    check("mutation guard: idempotency", test_mutation_guard_idempotency_is_not_optional)
    check("queue command is consumed before routing", test_bridge_consumes_queue_before_routing_and_posts_receipt)
    check("reconciliation failure fences queue dispatch", test_bridge_reconciliation_failure_fails_closed_before_queue_dispatch)
    check("invalid reconciliation shape fences queue dispatch", test_bridge_invalid_reconciliation_shape_fails_closed)
    check("projector health persists a redacted failure", test_projector_health_is_persisted_and_redacts_failure_detail)
    check("projector health records a successful empty check", test_projector_health_records_a_successful_empty_check)
    check("projector identity rejection cannot record local health", test_projector_identity_rejection_cannot_record_local_health)
    check("bridge passes one cycle stamp to idle projector", test_bridge_passes_one_cycle_stamp_to_projector_for_idle_health_receipt)
    check("retry timing migration is safe and bounded", test_corrupt_retry_timing_is_migration_safe_and_bounded)
    check("mutation guard: consumption order", test_mutation_guard_queue_parse_precedes_route_turn)
    check("disabled target reconciliation is exact and terminal-safe", test_disabled_target_reconciliation_is_exact_and_terminal_safe)
    check("disabled target reconciliation is idempotent and mutation-narrow", test_disabled_target_reconciliation_is_idempotent_and_mutation_narrow)
    if FAILURES:
        print(f"{len(FAILURES)} queue test(s) failed", file=sys.stderr)
        return 1
    print("all queue unit tests passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
