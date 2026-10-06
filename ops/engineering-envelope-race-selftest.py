#!/usr/bin/env python3
"""Exercise the race gate with an expensive deferred fixture commit."""
from __future__ import annotations

import importlib.util
import io
import os
from contextlib import redirect_stderr
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

spec = importlib.util.spec_from_file_location(
    "engineering_race", Path(__file__).with_name("engineering-envelope-race-local-pg-gate.py"))
assert spec and spec.loader
race = importlib.util.module_from_spec(spec)
spec.loader.exec_module(race)

clock = 0
claimed_at = None
fixture_pending = False
job_id, envelope_id, session_id, token = "job", "envelope", "session", "lease"

class Connection:
    def __enter__(self):
        return self
    def __exit__(self, *args):
        return None
    def cursor(self):
        return self
    def execute(self, sql, params=None):
        if "state='cancelled'" in sql:
            raise race.psycopg.errors.RaiseException("engineering session terminalization deferred while its dispatch lease is live")
    def commit(self):
        global clock, fixture_pending
        if fixture_pending:
            clock += 31
            fixture_pending = False
    def rollback(self):
        pass


def fixture(cur):
    global fixture_pending
    fixture_pending = True
    return job_id, envelope_id, session_id, None, None, None, None


def one(cur, sql, params):
    global claimed_at
    if "engineering_claim_slice" in sql:
        claimed_at = clock
        return job_id, token
    if "engineering_controller_binding" in sql:
        return ({"envelope_id": envelope_id} if params[2] == token and clock - claimed_at <= 30 else None,)
    if "engineering_envelope_currentness" in sql:
        raise race.psycopg.errors.InsufficientPrivilege("permission denied for function engineering_envelope_currentness")
    raise AssertionError(sql)


gate = SimpleNamespace(fixture=fixture, one=one,
                       grant_settable_runtime_roles=lambda *args: None,
                       set_local_role=lambda *args: None)
errors = io.StringIO()
with (patch.dict(os.environ, {"DATABASE_URL": "postgres://127.0.0.1/fixture"}),
      patch.object(race, "load_claim_gate", return_value=gate),
      patch.object(race.psycopg, "connect", return_value=Connection()),
      redirect_stderr(errors)):
    result = race.main()
assert result == 0, errors.getvalue()
assert claimed_at == 31, "fixture commit must finish before the scoped claim starts"
print("engineering race selftest passed: delayed fixture commit preserves full binding runway; terminalization still refuses")
