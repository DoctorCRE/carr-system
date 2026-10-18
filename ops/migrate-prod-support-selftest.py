#!/usr/bin/env python3
"""Regression tests for migrate-prod refusal escalation readback."""
from __future__ import annotations

import importlib.util
import json
import os
import shutil
import subprocess
import tempfile
import sys
from urllib.parse import urlunsplit
from pathlib import Path
from typing import Any


REPO = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    "migrate_prod_support", REPO / "tools" / "migrate-prod-support.py"
)
assert SPEC and SPEC.loader
support: Any = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(support)


def main() -> int:
    # Execute the production wrapper only in a disposable repository, with
    # neonctl and the record door stubbed. No production database or write.
    with tempfile.TemporaryDirectory(prefix="carr-neon-failure-") as temp:
        root = Path(temp)
        for directory in ('bin', 'tools', 'lib', 'out'):
            (root / directory).mkdir()
        (root / '.venv' / 'bin').mkdir(parents=True)
        (root / '.venv' / 'bin' / 'python').symlink_to(sys.executable)
        for relative in ('bin/migrate-prod.sh', 'tools/migrate-prod-support.py', 'lib/secret_redaction.py'):
            shutil.copy2(REPO / relative, root / relative)
        stub = root / 'bin' / 'neonctl'
        stub.write_text('#!/bin/sh\nprintf "%s\\n" "fixture neon failure: $NEON_API_KEY $TEST_PROVIDER_DSN" >&2\nexit 7\n')
        stub.chmod(0o700)
        door = root / 'bin' / 'door'
        door.write_text('#!/bin/sh\nexit 1\n')
        door.chmod(0o700)
        env = {name: value for name, value in os.environ.items()
               if not any(word in name.upper() for word in ('KEY', 'TOKEN', 'SECRET', 'PASSWORD', 'DATABASE_URL'))}
        # A generated canary verifies known-secret masking without a credential
        # literal in source or output.
        canary = __import__('uuid').uuid4().hex
        env.update(PATH=str(root / 'bin') + os.pathsep + os.environ['PATH'],
                   NEON_API_KEY=canary, CARR_MIGRATE_PROD_RUN_DOOR=str(door),
                   TEST_PROVIDER_DSN=urlunsplit(('postgresql', f'fixture:{canary}@invalid', '/db', '', '')))
        result = subprocess.run(['zsh', str(root / 'bin' / 'migrate-prod.sh')],
                                env=env, capture_output=True, text=True, timeout=15)
        assert result.returncode != 0
        receipts = list((root / 'out').glob('migrate-prod-refusal-receipt.*.json'))
        assert len(receipts) == 1
        receipt = json.loads(receipts[0].read_text())
        assert receipt['reason_class'] == 'dsn_unavailable', receipt['reason_class']
        persisted = ''.join(path.read_text() for path in (root / 'out').iterdir() if path.is_file())
        combined = result.stdout + result.stderr + persisted
        assert 'fixture neon failure' in combined
        assert 'neonctl rc=7' in combined
        assert canary not in combined
        assert 'postgresql://' not in combined
        print('PASS neonctl failure named, rc preserved in reason, stderr redacted')
    calls: list[tuple[str, dict]] = []

    def string_sequence(_door: str, verb: str, payload: dict) -> tuple[int, str, str]:
        calls.append((verb, payload))
        if verb == "add-room-turn":
            return 0, json.dumps({"seq": "42"}), ""
        if verb == "read-room":
            return 0, json.dumps({"turns": [{"seq": "42", "body": "blocked"}]}), ""
        raise AssertionError(verb)

    original = support._call_verb
    try:
        support._call_verb = string_sequence
        assert support._add_room_turn_and_readback("run.sh", "blocked") is True
        assert [verb for verb, _payload in calls] == ["add-room-turn", "read-room"]
        assert calls[0][1]["body"] == "blocked"
        assert calls[1][1] == {"room": support.ROOM, "after_seq": 41}

        calls.clear()

        def malformed(_door: str, verb: str, payload: dict) -> tuple[int, str, str]:
            calls.append((verb, payload))
            return 0, json.dumps({"seq": "not-a-sequence"}), ""

        support._call_verb = malformed
        assert support._add_room_turn_and_readback("run.sh", "blocked") is False
        assert [verb for verb, _payload in calls] == ["add-room-turn"]
    finally:
        support._call_verb = original

    print("migrate-prod support selftest: string sequence readback normalized; malformed refused")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
