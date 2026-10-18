#!/usr/bin/env python3
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import verb_io  # noqa: E402


def fake_client(argv, *, env, **_kwargs) -> subprocess.CompletedProcess:
    """Stands where `./run.sh call` would: the actor is derived from the
    client profile the call selected, exactly as the Worker derives it."""
    args = json.loads(argv[3])
    actor = "hermes-pilot" if env.get("CARR_MCP_CLIENT_PROFILE") == "hermes-projector" else "joe-local"
    return subprocess.CompletedProcess(argv, 0, json.dumps({
        "ok": True, "room": "partner-line", "sponsor": "joe", "seat": "hermes", "kind": "receipt",
        "origin_channel": "mcp", "origin_actor": actor, "msg_id": args["msg_id"],
        "idempotency_key": args["idempotency_key"], "seq": 9}), "")


def test_projector_uses_hermes_profile_and_accepts_exact_provenance() -> None:
    out = verb_io.project_room_queue("{}", msg_id="aaaaaaaa-bbbb-4ccc-8ddd-eeeeeeeeeeee",
                                     runner=fake_client)
    assert out["origin_actor"] == "hermes-pilot"


def test_normal_room_turn_stays_joe_local() -> None:
    out = verb_io.add_room_turn("hello", "hermes", kind="receipt",
                                msg_id="aaaaaaaa-bbbb-4ccc-8ddd-eeeeeeeeeeee",
                                runner=fake_client)
    assert out["origin_actor"] == "joe-local"


def test_room_turn_accepts_stable_callback_idempotency() -> None:
    out = verb_io.add_room_turn(
        "{}", "claude", msg_id="aaaaaaaa-bbbb-4ccc-8ddd-eeeeeeeeeeee",
        idempotency_key="queue-completion:t_queue0001", runner=fake_client,
    )
    assert out["idempotency_key"] == "queue-completion:t_queue0001"


def test_projector_rejects_reader_incompatible_append_response() -> None:
    prior = os.environ.get("CARR_MCP_CLIENT_PROFILE")
    os.environ["CARR_MCP_CLIENT_PROFILE"] = "local"
    try:
        try:
            verb_io.project_room_queue("{}", msg_id="aaaaaaaa-bbbb-4ccc-8ddd-eeeeeeeeeeee",
                                        runner=fake_client, client_profile="local")
        except RuntimeError as exc:
            assert "rejected provenance" in str(exc)
        else:
            raise AssertionError("joe-local response was accepted as projector health")
    finally:
        if prior is None:
            os.environ.pop("CARR_MCP_CLIENT_PROFILE", None)
        else:
            os.environ["CARR_MCP_CLIENT_PROFILE"] = prior


def test_inherited_profile_never_reaches_normal_room_traffic() -> None:
    prior = os.environ.get("CARR_MCP_CLIENT_PROFILE")
    os.environ["CARR_MCP_CLIENT_PROFILE"] = "hermes-projector"
    try:
        out = verb_io.add_room_turn("hello", "hermes", kind="receipt",
                                    msg_id="aaaaaaaa-bbbb-4ccc-8ddd-eeeeeeeeeeee",
                                    runner=fake_client)
    finally:
        if prior is None:
            os.environ.pop("CARR_MCP_CLIENT_PROFILE", None)
        else:
            os.environ["CARR_MCP_CLIENT_PROFILE"] = prior
    assert out["origin_actor"] == "joe-local"


def test_a_refused_write_raises_instead_of_reading_as_success() -> None:
    def refusing(argv, **_kwargs):
        return subprocess.CompletedProcess(argv, 0, json.dumps({"ok": False, "kind": "conflict"}), "")
    try:
        verb_io.add_room_turn("hello", "hermes", runner=refusing)
    except RuntimeError as exc:
        assert "add-room-turn refused" in str(exc)
    else:
        raise AssertionError("an ok:false reply was returned as a written turn")


def main() -> int:
    tests = [value for name, value in globals().items() if name.startswith("test_")]
    for test_case in tests:
        test_case()
    print(f"all {len(tests)} room-bridge verb identity tests passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
