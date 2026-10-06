#!/usr/bin/env python3
"""Source-contract fixtures for the cloud backup producer and observer.

Provider effects are deliberately outside this suite.  These checks pin the
observable workflow contract that later mocked-provider and live-provider
drills consume: one named Check, exact run identity, disabled cancellation that
cannot reach success, and a separate observer for the terminal provider state.
"""
from __future__ import annotations

import ast
import hashlib
import importlib.util
import json
import os
import re
import shutil
import stat
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from types import ModuleType


_ROOT = Path(__file__).resolve().parents[1]
_WORKFLOW = _ROOT / ".github" / "workflows" / "backup-nightly.yml"
_STATUS_HELPER = _ROOT / "ops" / "backup-workflow-status.py"
_SERVICES = _ROOT / "ops" / "config" / "services.json"
_METERING_POLICY = _ROOT / "ops" / "config" / "platform-metering.v1.json"

sys.path.insert(0, str(_ROOT))

from lib.platform_metering import (  # noqa: E402
    authorize_metered_execution as _authorize_metered_execution,
)

_passed = 0
_failed: list[str] = []


def _check(label: str, condition: bool, detail: str) -> None:
    global _passed
    if condition:
        _passed += 1
        print(f"PASS  {label}")
    else:
        _failed.append(label)
        print(f"FAIL  {label}: {detail}")


def _executable(path: Path, body: str) -> None:
    path.write_text(body, encoding="utf-8")
    path.chmod(path.stat().st_mode | stat.S_IXUSR)


def _fake_gh(path: Path) -> None:
    """A stateful local implementation of the exact gh api boundary."""
    _executable(path, f"""#!{sys.executable}
import json, os, pathlib, sys
state_path = pathlib.Path(os.environ['CARR_TEST_GH_STATE'])
log_path = pathlib.Path(os.environ['CARR_TEST_GH_LOG'])
args = sys.argv[1:]
method = 'GET'
for i, arg in enumerate(args):
    if arg in ('-X', '--method') and i + 1 < len(args): method = args[i + 1].upper()
    elif arg.startswith('--method='): method = arg.split('=', 1)[1].upper()
url = next((arg for arg in args if arg.startswith('/repos/')), '')
body = {{}}
if '--input' in args:
    try: body = json.load(sys.stdin)
    except Exception: body = {{}}
with log_path.open('a', encoding='utf-8') as log:
    log.write(json.dumps({{'method': method, 'url': url, 'body': body, 'args': args}}, sort_keys=True) + '\\n')
state = json.loads(state_path.read_text(encoding='utf-8'))
def save():
    tmp = state_path.with_suffix('.next')
    tmp.write_text(json.dumps(state, sort_keys=True), encoding='utf-8')
    tmp.replace(state_path)
def answer(value):
    print(json.dumps(value, sort_keys=True))
if url.endswith('/check-runs') and method == 'POST':
    item = dict(body); item['id'] = state.get('next_check_id', 8001)
    state['next_check_id'] = item['id'] + 1
    state.setdefault('check_runs', []).append(item); save(); answer(item)
elif '/check-runs/' in url and method in ('PATCH', 'POST'):
    check_id = int(url.rsplit('/', 1)[1])
    item = next((x for x in state.get('check_runs', []) if x.get('id') == check_id), None)
    if item is None: print('missing check', file=sys.stderr); raise SystemExit(1)
    item.update(body); save(); answer(item)
elif url.endswith('/check-runs'):
    if state.get('check_list_error'):
        print('synthetic check listing failure', file=sys.stderr); raise SystemExit(1)
    rows = state.get('check_runs', [])
    if state.get('check_requires_filter_all') and 'filter=all' not in args:
        rows = []
    if state.get('check_pagination_exhausted'):
        # A full page every time with a total the cap can never reach: the
        # exact shape that must fail closed rather than answer from a partial
        # view of the head's Checks.
        filler = [{{'id': 90000 + i, 'name': 'other', 'head_sha': 'a' * 40}} for i in range(100)]
        answer({{'total_count': 10 ** 6, 'check_runs': filler}})
    else:
        answer({{'total_count': len(rows), 'check_runs': rows}})
elif '/actions/artifacts/' in url:
    artifact_id = int(url.rsplit('/', 1)[1])
    item = next((x for x in state.get('artifacts', []) if x.get('id') == artifact_id), None)
    if item is None: print('missing artifact', file=sys.stderr); raise SystemExit(1)
    answer(item)
elif url.endswith('/artifacts'):
    if state.get('artifact_list_error'):
        print('synthetic artifact listing failure', file=sys.stderr); raise SystemExit(1)
    answer({{'total_count': len(state.get('artifacts', [])), 'artifacts': state.get('artifacts', [])}})
elif url.endswith('/cancel') and method == 'POST':
    state.setdefault('cancel_calls', []).append(url); save()
    if state.get('cancel_error'):
        print('synthetic cancel failure', file=sys.stderr); raise SystemExit(1)
    answer({{}})
elif '/actions/workflows/' in url and url.endswith('/dispatches') and method == 'POST':
    state.setdefault('dispatches', []).append({{'url': url, 'body': body}}); save()
    if state.get('dispatch_error'):
        print('synthetic dispatch failure', file=sys.stderr); raise SystemExit(1)
    answer({{}})
elif '/actions/workflows/' in url and url.endswith('/runs'):
    if state.get('workflow_run_list_error'):
        print('synthetic workflow run listing failure', file=sys.stderr); raise SystemExit(1)
    # ANSWERS LOOSELY ON PURPOSE: every seeded run comes back whatever the
    # caller asked for. A stub that applied the head and workflow scoping
    # itself would make the door's own re-reads of workflow_run.path and
    # workflow_run.head_sha unobservable -- they would be dead code that no
    # control could distinguish from a door that trusted the URL it asked.
    # The scoping the door DOES send is asserted from the call log instead.
    rows = state.get('workflow_runs', [])
    answer({{'total_count': len(rows), 'workflow_runs': rows}})
elif '/actions/runs/' in url:
    answer(state.get('run', {{}}))
elif '/commits/' in url:
    if state.get('branch_head_error'):
        print('synthetic branch read failure', file=sys.stderr); raise SystemExit(1)
    answer(state.get('branch_head', {{}}))
else:
    print('unexpected synthetic gh call: ' + method + ' ' + url, file=sys.stderr)
    raise SystemExit(2)
""")


def _identity() -> dict[str, object]:
    return {
        "repository": "jbookout/carr-system",
        "run_id": 101,
        "run_attempt": 2,
        "head_sha": "a" * 40,
    }


def _artifact() -> dict[str, object]:
    return {
        "id": 7001,
        "name": "carr-backup-run-101-attempt-2",
        "size_in_bytes": 2_097_152,
        "digest": "sha256:" + "b" * 64,
        "created_at": "2026-09-06T12:00:00Z",
        "expires_at": "2026-12-05T12:00:00Z",
        "expired": False,
        "workflow_run": {
            "id": 101,
            "head_sha": "a" * 40,
        },
    }


def _run_state(*, conclusion: str = "success") -> dict[str, object]:
    return {
        "id": 101,
        "run_attempt": 2,
        "head_sha": "a" * 40,
        "status": "completed",
        "conclusion": conclusion,
    }


def _named_check(conclusion: str, *, bound: bool = True) -> dict[str, object]:
    bound_identity = _identity()
    if not bound:
        bound_identity["head_sha"] = "d" * 40
    summary: dict[str, object] = {}
    if conclusion == "failure":
        summary = {"signal": "backup-failure", "reason": "synthetic dump failure"}
    elif conclusion == "success":
        item = _artifact()
        summary = {
            "artifact_id": item["id"],
            "artifact_name": item["name"],
            "artifact_digest": item["digest"],
            "artifact_bytes": item["size_in_bytes"],
            "artifact_created_at": item["created_at"],
            "artifact_expires_at": item["expires_at"],
            "required_steps": ["dump", "encrypt", "upload", "readback"],
        }
    return {
        "id": 8001,
        "name": "Backup artifact",
        "head_sha": "a" * 40,
        "status": "completed",
        "conclusion": conclusion,
        "external_id": json.dumps(bound_identity, sort_keys=True, separators=(",", ":")),
        "output": {"title": "Backup artifact", "summary": json.dumps(summary, sort_keys=True)},
    }


def _fixture_env(
    root: Path,
    state: dict[str, object],
    overrides: dict[str, str] | None = None,
) -> tuple[dict[str, str], Path, Path]:
    bin_dir = root / "bin"
    bin_dir.mkdir()
    _fake_gh(bin_dir / "gh")
    state_path = root / "provider-state.json"
    log_path = root / "provider-calls.jsonl"
    state_path.write_text(json.dumps(state, sort_keys=True), encoding="utf-8")
    env = {
        **os.environ,
        "PATH": str(bin_dir),
        "GH_TOKEN": "synthetic-fixture-only",
        "GITHUB_REPOSITORY": "jbookout/carr-system",
        "GITHUB_RUN_ID": "101",
        "GITHUB_RUN_ATTEMPT": "2",
        "GITHUB_SHA": "a" * 40,
        # The controlled-failure guards read these four. Every fixture sets
        # them explicitly, including the ones that never touch the proof
        # commands, so a guard can never quietly pass by inheriting the real
        # GitHub Actions environment this suite may itself be running inside.
        "GITHUB_EVENT_NAME": "workflow_dispatch",
        "GITHUB_ACTOR": "jbookout",
        "GITHUB_TRIGGERING_ACTOR": "jbookout",
        "GITHUB_REF": "refs/heads/main",
        "CARR_TEST_GH_STATE": str(state_path),
        "CARR_TEST_GH_LOG": str(log_path),
    }
    env.update(overrides or {})
    return env, state_path, log_path


def _invoke(env: dict[str, str], *args: str, timeout: float = 8) -> subprocess.CompletedProcess[str]:
    if not _STATUS_HELPER.is_file():
        return subprocess.CompletedProcess(args, 127, "", f"missing {_STATUS_HELPER}")
    return subprocess.run(
        [sys.executable, str(_STATUS_HELPER), *args],
        cwd=_ROOT,
        env=env,
        text=True,
        capture_output=True,
        timeout=timeout,
        check=False,
    )


def _read_json(path: Path) -> dict[str, object]:
    return json.loads(path.read_text(encoding="utf-8"))


def _calls(path: Path) -> list[dict[str, object]]:
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


def _parsed_output(run: subprocess.CompletedProcess[str]) -> dict[str, object]:
    try:
        return json.loads(run.stdout)
    except (json.JSONDecodeError, TypeError):
        return {}


def _behavioral_contract() -> None:
    artifact_args = (
        "--artifact-id", "7001",
        "--artifact-name", "carr-backup-run-101-attempt-2",
        "--artifact-digest", "sha256:" + "b" * 64,
        "--artifact-bytes", "2097152",
        "--artifact-created-at", "2026-09-06T12:00:00Z",
        "--artifact-expires-at", "2026-12-05T12:00:00Z",
    )

    with tempfile.TemporaryDirectory(prefix="carr-wr54-status-complete-") as raw:
        env, state_path, log_path = _fixture_env(
            Path(raw),
            {
                "run": _run_state(),
                "artifacts": [_artifact()],
                "check_runs": [],
                "check_requires_filter_all": True,
            },
        )
        started = _invoke(env, "producer-start")
        after_start = _read_json(state_path)
        start_checks = after_start.get("check_runs", [])
        start_item = start_checks[0] if isinstance(start_checks, list) and start_checks else {}
        try:
            start_identity = json.loads(start_item.get("external_id", ""))
        except (json.JSONDecodeError, TypeError):
            start_identity = {}
        _check(
            "producer-start creates one in-progress Check with exact immutable identity",
            started.returncode == 0
            and start_item.get("name") == "Backup artifact"
            and start_item.get("status") == "in_progress"
            and start_identity == _identity(),
            started.stderr,
        )

        completed = _invoke(env, "producer-complete", *artifact_args)
        after_complete = _read_json(state_path)
        complete_checks = after_complete.get("check_runs", [])
        complete_item = (
            complete_checks[0]
            if isinstance(complete_checks, list) and len(complete_checks) == 1
            else {}
        )
        output = complete_item.get("output", {}) if isinstance(complete_item, dict) else {}
        summary_raw = output.get("summary", "{}") if isinstance(output, dict) else "{}"
        try:
            summary = json.loads(summary_raw) if isinstance(summary_raw, str) else summary_raw
        except json.JSONDecodeError:
            summary = {}
        _check(
            "producer-complete readbacks exact artifact metadata before one success Check",
            completed.returncode == 0
            and complete_item.get("conclusion") == "success"
            and summary.get("artifact_id") == 7001
            and summary.get("artifact_name") == _artifact()["name"]
            and summary.get("artifact_digest") == _artifact()["digest"]
            and summary.get("artifact_bytes") == _artifact()["size_in_bytes"]
            and summary.get("artifact_created_at") == _artifact()["created_at"]
            and summary.get("artifact_expires_at") == _artifact()["expires_at"]
            and summary.get("required_steps") == ["dump", "encrypt", "upload", "readback"]
            and any("/actions/artifacts/7001" in str(call.get("url", "")) for call in _calls(log_path)),
            completed.stderr,
        )
        _check(
            "producer uses all Checks and exact run-attempt readback",
            any(
                isinstance(args := call.get("args"), list) and "filter=all" in args
                for call in _calls(log_path)
            )
            and any(
                str(call.get("url", "")).endswith("/actions/runs/101/attempts/2")
                for call in _calls(log_path)
            ),
            json.dumps(_calls(log_path), sort_keys=True),
        )

    with tempfile.TemporaryDirectory(prefix="carr-wr54-status-mismatch-") as raw:
        wrong = _artifact()
        wrong["size_in_bytes"] = 17
        env, state_path, _ = _fixture_env(
            Path(raw),
            {"run": _run_state(), "artifacts": [wrong], "check_runs": []},
        )
        _invoke(env, "producer-start")
        mismatched = _invoke(env, "producer-complete", *artifact_args)
        mismatch_checks = _read_json(state_path).get("check_runs", [])
        _check(
            "artifact metadata mismatch cannot publish backup success",
            _STATUS_HELPER.is_file()
            and mismatched.returncode != 0
            and isinstance(mismatch_checks, list)
            and all(item.get("conclusion") != "success" for item in mismatch_checks),
            mismatched.stderr,
        )

    with tempfile.TemporaryDirectory(prefix="carr-wr54-status-attempt-name-") as raw:
        wrong_attempt = _artifact()
        wrong_attempt["name"] = "carr-backup-run-101-attempt-20"
        env, state_path, _ = _fixture_env(
            Path(raw),
            {"run": _run_state(), "artifacts": [wrong_attempt], "check_runs": []},
        )
        _invoke(env, "producer-start")
        embedded_attempt = _invoke(
            env,
            "producer-complete",
            "--artifact-id", "7001",
            "--artifact-name", "carr-backup-run-101-attempt-20",
            "--artifact-digest", "sha256:" + "b" * 64,
            "--artifact-bytes", "2097152",
            "--artifact-created-at", "2026-09-06T12:00:00Z",
            "--artifact-expires-at", "2026-12-05T12:00:00Z",
        )
        attempt_checks = _read_json(state_path).get("check_runs", [])
        _check(
            "producer attempt 2 rejects an artifact whose exact name says attempt 20",
            embedded_attempt.returncode != 0
            and isinstance(attempt_checks, list)
            and all(item.get("conclusion") != "success" for item in attempt_checks),
            embedded_attempt.stderr,
        )

    with tempfile.TemporaryDirectory(prefix="carr-wr54-status-fail-") as raw:
        env, state_path, _ = _fixture_env(
            Path(raw),
            {"run": _run_state(conclusion="failure"), "artifacts": [], "check_runs": []},
        )
        failed_run = _invoke(env, "producer-fail", "--reason", "synthetic dump failure")
        failure_checks = _read_json(state_path).get("check_runs", [])
        failure_item = failure_checks[0] if isinstance(failure_checks, list) and failure_checks else {}
        try:
            failure_identity = json.loads(failure_item.get("external_id", ""))
            failure_summary = json.loads(failure_item.get("output", {}).get("summary", "{}"))
        except (json.JSONDecodeError, TypeError, AttributeError):
            failure_identity, failure_summary = {}, {}
        _check(
            "producer-fail writes only an exact-bound backup-failure Check",
            failed_run.returncode == 0
            and failure_item.get("name") == "Backup artifact"
            and failure_item.get("conclusion") == "failure"
            and failure_identity == _identity()
            and failure_summary.get("signal") == "backup-failure",
            failed_run.stderr,
        )

    with tempfile.TemporaryDirectory(prefix="carr-wr54-status-neutral-") as raw:
        env, state_path, log_path = _fixture_env(
            Path(raw),
            {"run": _run_state(conclusion="cancelled"), "artifacts": [], "check_runs": []},
        )
        proc: subprocess.Popen[str] | None = None
        if _STATUS_HELPER.is_file():
            proc = subprocess.Popen(
                [sys.executable, str(_STATUS_HELPER), "neutral-cancel"],
                cwd=_ROOT,
                env=env,
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )
            deadline = time.monotonic() + 4
            while time.monotonic() < deadline:
                observed = _calls(log_path)
                if any(str(call.get("url", "")).endswith("/actions/runs/101/cancel")
                       for call in observed):
                    break
                if proc.poll() is not None:
                    break
                time.sleep(0.05)
            alive_after_cancel = proc.poll() is None
            proc.terminate()
            try:
                proc.communicate(timeout=2)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.communicate()
        else:
            alive_after_cancel = False
        neutral_state = _read_json(state_path)
        neutral_checks = neutral_state.get("check_runs", [])
        neutral_item = neutral_checks[0] if isinstance(neutral_checks, list) and neutral_checks else {}
        neutral_calls = _calls(log_path)
        cancel_urls = [
            str(item.get("url", ""))
            for item in neutral_calls
            if str(item.get("url", "")).endswith("/cancel")
        ]
        create_index = next(
            (i for i, item in enumerate(neutral_calls)
             if item.get("method") == "POST" and str(item.get("url", "")).endswith("/check-runs")),
            -1,
        )
        readback_index = next(
            (i for i, item in enumerate(neutral_calls)
             if i > create_index and item.get("method") == "GET"
             and str(item.get("url", "")).endswith("/check-runs")),
            -1,
        )
        cancel_index = next(
            (i for i, item in enumerate(neutral_calls)
             if str(item.get("url", "")).endswith("/actions/runs/101/cancel")),
            -1,
        )
        _check(
            "neutral path creates and reads back neutral, cancels only its run, then waits for kill",
            neutral_item.get("conclusion") == "neutral"
            and create_index >= 0 < readback_index < cancel_index
            and cancel_urls == ["/repos/jbookout/carr-system/actions/runs/101/cancel"]
            and alive_after_cancel,
            json.dumps(neutral_calls, sort_keys=True),
        )

    with tempfile.TemporaryDirectory(prefix="carr-wr54-status-cancel-error-") as raw:
        env, state_path, log_path = _fixture_env(
            Path(raw),
            {
                "run": _run_state(conclusion="cancelled"),
                "artifacts": [],
                "check_runs": [],
                "cancel_error": True,
            },
        )
        if _STATUS_HELPER.is_file():
            proc = subprocess.Popen(
                [sys.executable, str(_STATUS_HELPER), "neutral-cancel"],
                cwd=_ROOT,
                env=env,
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )
            deadline = time.monotonic() + 4
            while time.monotonic() < deadline and proc.poll() is None:
                if any(str(call.get("url", "")).endswith("/cancel") for call in _calls(log_path)):
                    time.sleep(0.1)
                    break
                time.sleep(0.05)
            if proc.poll() is None:
                proc.terminate()
                try:
                    proc.communicate(timeout=2)
                except subprocess.TimeoutExpired:
                    proc.kill()
                    proc.communicate()
        cancel_error_checks = _read_json(state_path).get("check_runs", [])
        _check(
            "cancel API failure cannot turn a neutral Check into success or backup-failure",
            isinstance(cancel_error_checks, list)
            and len(cancel_error_checks) == 1
            and cancel_error_checks[0].get("conclusion") == "neutral"
            and "backup-failure" not in json.dumps(cancel_error_checks),
            json.dumps(cancel_error_checks, sort_keys=True),
        )

    def observe_case(
        label: str,
        state: dict[str, object],
        expected_state: str | None,
        expected_signal: str | None,
        expected_rc: int | None,
    ) -> None:
        with tempfile.TemporaryDirectory(prefix="carr-wr54-status-observe-") as raw:
            env, _, log_path = _fixture_env(Path(raw), state)
            observed = _invoke(env, "observe")
            result = _parsed_output(observed)
            ok = (
                (expected_rc is None or observed.returncode == expected_rc)
                and (expected_state is None or result.get("state") == expected_state)
                and result.get("signal") == expected_signal
                and any("/actions/runs/101" in str(call.get("url", ""))
                        for call in _calls(log_path))
            )
            _check(label, ok, f"rc={observed.returncode} out={observed.stdout!r} err={observed.stderr!r}")

    observe_case(
        "observer accepts exact cancelled neutral run with zero artifacts as disabled",
        {"run": _run_state(conclusion="cancelled"), "artifacts": [], "check_runs": [_named_check("neutral")]},
        "disabled",
        None,
        0,
    )
    observe_case(
        "observer refuses mismatched neutral provenance without backup-failure",
        {"run": _run_state(conclusion="cancelled"), "artifacts": [], "check_runs": [_named_check("neutral", bound=False)]},
        "unknown",
        None,
        2,
    )
    observe_case(
        "infrastructure cancellation without neutral Check cannot signal backup-failure",
        {"run": _run_state(conclusion="cancelled"), "artifacts": [], "check_runs": []},
        "unknown",
        None,
        2,
    )
    observe_case(
        "observer emits backup-failure for the exact-bound failure Check",
        {"run": _run_state(conclusion="cancelled"), "artifacts": [], "check_runs": [_named_check("failure")]},
        "failure",
        "backup-failure",
        1,
    )
    observe_case(
        "observer suppresses backup-failure for mismatched failure provenance",
        {"run": _run_state(conclusion="failure"), "artifacts": [], "check_runs": [_named_check("failure", bound=False)]},
        "unknown",
        None,
        2,
    )
    observe_case(
        "observer accepts success only with exact artifact metadata",
        {"run": _run_state(), "artifacts": [_artifact()], "check_runs": [_named_check("success")]},
        "success",
        None,
        0,
    )


_PROOF_ID = "3f1c2d4e-5a6b-4c7d-8e9f-0a1b2c3d4e5f"
_PROOF_HEAD = "a" * 40
_PROOF_REASON = "approved WR54 controlled failure before dump"
_CONTROLLED_FAILURE_EXIT = 9
# A proof spent on this head by an EARLIER run. Registered, never derived from
# anything a caller passes: the positive control below needs a head that has
# already seeded one failure, because that is the situation the seam's own
# exclusion exists for.
_EARLIER_PROOF_ID = "1a2b3c4d-5e6f-4a7b-8c9d-0e1f2a3b4c5d"

# ── registered check-run fixture shapes ─────────────────────────────────────
# NOTHING A CALLER PASSES TO _ci_check REACHES THE RETURNED ROW. Every argument
# is a KEY into one of these registries, an unregistered key raises, and only
# the registered VALUE is ever placed in the fixture. That is the closed shape
# the standing authority rule asks of a registry lookup keyed by caller input:
# the key selects, it never supplies. The helper is module-private for the same
# rule -- its rows carry GitHub's "completed"/"success" vocabulary, and an
# exported builder that took that vocabulary from its caller would be a
# privileged outcome minted from caller input under a fixture's name.
_CHECK_STATES: dict[str, dict[str, object]] = {
    "concluded_clean": {"status": "completed", "conclusion": "success"},
    "unconcluded": {"status": "in_progress", "conclusion": None},
    "concluded_red": {"status": "completed", "conclusion": "failure"},
    # NOT RED, AND NOT EVIDENCE EITHER. NON_FAILING_CHECK_CONCLUSIONS accepts
    # neutral, so a row in this state passes the no-red half untouched; the
    # positive half must still refuse it, because a neutral required check means
    # the suite did not run.
    "concluded_neutral": {"status": "completed", "conclusion": "neutral"},
}
# "seam_label" is the exact label the retired name-only exclusion trusted. It
# is registered here precisely so the controls below can attach it to rows the
# seam did not write.
# "required_ci" is the exact context AGENTS.md says the ruleset requires and
# ops/config/automerge-pilot-policy.v1.json registers. It is a LABEL here like
# every other: the controls below attach it to rows no ci.yml run produced, and
# the door must refuse every one of them.
_CHECK_LABELS = {
    "ordinary_ci": "gates",
    "other_ci": "coverage",
    "required_ci": "ops/ci.sh --strict",
    "seam_label": "Backup artifact",
}
_CHECK_PRODUCERS: dict[str, dict[str, object] | None] = {
    # The producer the door authenticates: GitHub's own Actions app, which is
    # what stamps a Check created with a workflow run's GITHUB_TOKEN.
    "actions_app": {"id": 15368, "slug": "github-actions"},
    # Any other installation. It has the same free text available to it -- name,
    # title, output, external_id -- and a different stamp.
    "foreign_app": {"id": 99001, "slug": "impostor-checks"},
    "unattributed": None,
}
# The envelope is no longer AUTHORITY -- the door ignores it entirely -- so the
# only registered shape left is the forgery: the exact bytes this seam writes
# into external_id, which any creator may copy, and which the controls below
# attach to rows the seam did not write.
_CHECK_ENVELOPES: dict[str, dict[str, object] | None] = {
    "absent": None,
    "seam_this_head": {
        "repository": "jbookout/carr-system", "run_id": 99,
        "run_attempt": 1, "head_sha": _PROOF_HEAD,
    },
}
# THE PROVIDER-BACKED HALF, and the one the door now decides on. A check-run's
# ``check_suite`` is assigned by GitHub, not by whoever posted the Check, and
# the door resolves which suites belong to this seam from the workflow-run
# listing below rather than believing any row about itself.
_SEAM_WORKFLOW_PATH = ".github/workflows/backup-nightly.yml"
_REQUIRED_WORKFLOW_PATH = ".github/workflows/ci.yml"
_CHECK_SUITES: dict[str, dict[str, object] | None] = {
    "absent": None,
    # The suite of a backup-nightly.yml run on the proof head: the seam's own.
    "seam_run": {"id": 4200},
    # A run of a DIFFERENT workflow file in the same repository. Same Actions
    # app, same head, real suite -- and neither this seam nor the required CI.
    "other_workflow": {"id": 4300},
    # A run of the SEAM'S OWN workflow file bound to another head. Authentic
    # elsewhere, worthless here.
    "other_head_run": {"id": 4400},
    # The suite of a ci.yml run on the proof head: the ONE suite a row may sit
    # in and authenticate as this repository's required check.
    "required_run": {"id": 4500},
    # A ci.yml run bound to another head. Same workflow file, same app, real
    # suite -- and it says nothing about this commit.
    "required_other_head_run": {"id": 4600},
}


def _workflow_runs() -> list[dict[str, object]]:
    """What the provider answers when asked for backup-nightly.yml's runs.

    Every row comes back on every call, because the fake provider deliberately
    ignores the scoping the caller sent. Two of these three rows are therefore
    the door's OWN job to discard: run 4002 is this seam's workflow file on
    ANOTHER head, and run 4003 is another workflow file on this one. If either
    re-read is dropped from the resolver, that run's suite joins the seam's set
    and the matching control below dispatches on a red head.
    """
    return [
        {
            "id": 4001, "path": _SEAM_WORKFLOW_PATH, "head_sha": _PROOF_HEAD,
            "check_suite_id": 4200, "status": "completed", "conclusion": "failure",
        },
        {
            "id": 4002, "path": _SEAM_WORKFLOW_PATH, "head_sha": "d" * 40,
            "check_suite_id": 4400, "status": "completed", "conclusion": "failure",
        },
        # A RUN OF ANOTHER WORKFLOW FILE, deliberately left in an answer the
        # real endpoint scopes to one workflow. The door re-reads
        # ``workflow_run.path`` on every row rather than trusting the URL it
        # asked, and this row is what makes that re-read load-bearing: drop it
        # from the fixture and a door that skipped the check would still pass.
        {
            "id": 4003, "path": ".github/workflows/gates.yml", "head_sha": _PROOF_HEAD,
            "check_suite_id": 4300, "status": "completed", "conclusion": "success",
        },
        # THE REQUIRED CHECK'S OWN WORKFLOW FILE, on this head. Suite 4500 is
        # the only suite a row may sit in and authenticate as `ops/ci.sh
        # --strict`, and it reaches the door through the provider's listing --
        # never through anything the Check itself says about itself.
        {
            "id": 4004, "path": _REQUIRED_WORKFLOW_PATH, "head_sha": _PROOF_HEAD,
            "check_suite_id": 4500, "status": "completed", "conclusion": "success",
        },
        # The same workflow file on ANOTHER head. It is here for the same reason
        # run 4002 is: if the head re-read is dropped, suite 4600 joins the
        # required set and a Check bound to a different commit authenticates
        # this one.
        {
            "id": 4005, "path": _REQUIRED_WORKFLOW_PATH, "head_sha": "d" * 40,
            "check_suite_id": 4600, "status": "completed", "conclusion": "success",
        },
    ]
_CHECK_SUMMARIES: dict[str, dict[str, object] | None] = {
    "absent": None,
    "earlier_proof": {
        "signal": "backup-failure", "reason": _PROOF_REASON,
        "proof_id": _EARLIER_PROOF_ID,
    },
}
# One registered ID per (producer, label) pair. Distinct IDs matter: the helper
# that reads a head's Checks de-duplicates by ID, so two fixture rows sharing
# one would silently collapse into a single Check and a control would pass
# while proving nothing.
_CHECK_IDS = {
    ("actions_app", "ordinary_ci", "other_workflow"): 7300,
    ("actions_app", "ordinary_ci", "absent"): 7310,
    ("actions_app", "other_ci", "other_workflow"): 7340,
    ("actions_app", "seam_label", "seam_run"): 7301,
    ("actions_app", "seam_label", "other_workflow"): 7302,
    ("actions_app", "seam_label", "other_head_run"): 7303,
    ("actions_app", "seam_label", "absent"): 7304,
    # THE AUTHENTIC REQUIRED CHECK and its four near-misses. Each differs from
    # 7320 in exactly one provider fact, which is what makes the refusals below
    # attributable to that fact rather than to a door that refuses everything.
    ("actions_app", "required_ci", "required_run"): 7320,
    ("actions_app", "required_ci", "other_workflow"): 7321,
    ("actions_app", "required_ci", "required_other_head_run"): 7322,
    ("actions_app", "required_ci", "absent"): 7323,
    ("foreign_app", "required_ci", "required_run"): 7420,
    ("unattributed", "required_ci", "required_run"): 7520,
    ("foreign_app", "ordinary_ci", "absent"): 7400,
    ("foreign_app", "seam_label", "seam_run"): 7401,
    ("unattributed", "ordinary_ci", "absent"): 7500,
    ("unattributed", "seam_label", "seam_run"): 7501,
}


def _ci_check(
    state: str,
    *,
    label: str = "ordinary_ci",
    producer: str = "actions_app",
    envelope: str = "absent",
    summary: str = "absent",
    suite: str = "other_workflow",
) -> dict[str, object]:
    """One check-run on the proof head, assembled only from registered values.

    The default is the ordinary case the door must be able to stand on: a
    concluded, clean CI Check stamped by the Actions app, carrying no seam
    envelope, sitting in the check-suite of some OTHER workflow file in this
    repository -- which is what every real gates run on the head looks like.
    """
    key = (producer, label, suite)
    if (state not in _CHECK_STATES or label not in _CHECK_LABELS
            or producer not in _CHECK_PRODUCERS or envelope not in _CHECK_ENVELOPES
            or summary not in _CHECK_SUMMARIES or suite not in _CHECK_SUITES
            or key not in _CHECK_IDS):
        raise ValueError("unregistered check-run fixture shape")
    row: dict[str, object] = {
        "id": _CHECK_IDS[key],
        "name": _CHECK_LABELS[label],
        "head_sha": _PROOF_HEAD,
        **_CHECK_STATES[state],
    }
    assigned = _CHECK_SUITES[suite]
    if assigned is not None:
        row["check_suite"] = dict(assigned)
    stamp = _CHECK_PRODUCERS[producer]
    if stamp is not None:
        row["app"] = dict(stamp)
    carried = _CHECK_ENVELOPES[envelope]
    if carried is not None:
        row["external_id"] = json.dumps(carried, sort_keys=True, separators=(",", ":"))
    reported = _CHECK_SUMMARIES[summary]
    if reported is not None:
        row["output"] = {
            "title": _CHECK_LABELS[label],
            "summary": json.dumps(reported, sort_keys=True),
        }
    return row


def _required_check(state: str = "concluded_clean", **shape: str) -> dict[str, object]:
    """The repository's required check on the proof head, authentic by default.

    Authentic means every one of the four provider facts the door authenticates:
    the Actions app stamp, this head, and a check-suite the provider assigned to
    a ci.yml run on this head. A keyword override changes exactly one of them,
    which is how each near-miss control below is built.
    """
    return _ci_check(state, label="required_ci", **{"suite": "required_run", **shape})


def _green_head(*extra: dict[str, object]) -> list[dict[str, object]]:
    """A head that genuinely satisfies BOTH halves of the door's evidence test.

    WHY THIS EXISTS RATHER THAN A BARE ORDINARY CHECK. Every refusal control
    below seeds the row it is actually testing beside a head that would
    otherwise dispatch. If the base head did not carry an authentic `ops/ci.sh
    --strict` success, every one of those controls would refuse for the MISSING
    REQUIRED CHECK instead of for the row under test -- passing while proving
    nothing, which is how a fixture fakes a pass. The positive half is seeded
    here once, so a refusal below is attributable to the extra row.
    """
    return [_ci_check("concluded_clean"), _required_check(), *extra]


def _proof_args(*, proof_id: str = _PROOF_ID, head: str = _PROOF_HEAD) -> tuple[str, ...]:
    return ("--proof-id", proof_id, "--expected-head", head)


def _spent_proof_check(proof_id: str = _PROOF_ID) -> dict[str, object]:
    """A proof Check left on this head by an EARLIER run.

    Its run identity is 99/1, not this fixture's 101/2, on purpose: the reuse
    scan has to find a proof spent by any run on the head, not merely one bound
    to the current run's own external_id.
    """
    earlier = {
        "repository": "jbookout/carr-system",
        "run_id": 99,
        "run_attempt": 1,
        "head_sha": _PROOF_HEAD,
    }
    summary = {"signal": "backup-failure", "reason": _PROOF_REASON, "proof_id": proof_id}
    return {
        "id": 8500,
        "name": "Backup artifact",
        "head_sha": _PROOF_HEAD,
        "status": "completed",
        "conclusion": "failure",
        # The provider stamp a real seam Check carries, from the one registered
        # spelling of it. Without it this fixture would be an impostor row, and
        # the refusal it proves would be the wrong refusal.
        "app": dict(_CHECK_PRODUCERS["actions_app"] or {}),
        # And the provider-assigned suite of a real backup-nightly run on this
        # head. Without it this row is not the seam's own under the door's own
        # test, and the refusal it proves would be the wrong refusal.
        "check_suite": dict(_CHECK_SUITES["seam_run"] or {}),
        "external_id": json.dumps(earlier, sort_keys=True, separators=(",", ":")),
        "output": {"title": "Backup artifact", "summary": json.dumps(summary, sort_keys=True)},
    }


def _controlled_failure_contract() -> None:
    """The WR54 seam: refuse loudly, or fail exactly once with a bound proof."""

    # ── the read-only validation passes on an exact request, writing nothing ──
    with tempfile.TemporaryDirectory(prefix="carr-wr54-proof-validate-") as raw:
        env, state_path, _ = _fixture_env(
            Path(raw), {"run": _run_state(), "artifacts": [], "check_runs": []},
        )
        validated = _invoke(env, "validate-controlled-failure", *_proof_args())
        after_validate = _read_json(state_path)
        _check(
            "validate-controlled-failure accepts an exact request and writes nothing",
            validated.returncode == 0
            and after_validate.get("check_runs") == []
            and after_validate.get("artifacts") == [],
            f"rc={validated.returncode} err={validated.stderr!r} "
            f"state={json.dumps(after_validate, sort_keys=True)}",
        )

    # ── every wrong guard refuses, and none of them mutates a Check ──────────
    refusals: list[tuple[str, dict[str, object], dict[str, str], tuple[str, ...]]] = [
        (
            "a wrong actor cannot spend a controlled failure",
            {"run": _run_state(), "artifacts": [], "check_runs": []},
            {"GITHUB_ACTOR": "someone-else"},
            _proof_args(),
        ),
        (
            "a wrong triggering actor cannot spend a controlled failure",
            {"run": _run_state(), "artifacts": [], "check_runs": []},
            {"GITHUB_TRIGGERING_ACTOR": "someone-else"},
            _proof_args(),
        ),
        (
            "a scheduled event can never reach the controlled failure",
            {"run": _run_state(), "artifacts": [], "check_runs": []},
            {"GITHUB_EVENT_NAME": "schedule"},
            _proof_args(),
        ),
        (
            "a non-main ref cannot spend a controlled failure",
            {"run": _run_state(), "artifacts": [], "check_runs": []},
            {"GITHUB_REF": "refs/heads/topic"},
            _proof_args(),
        ),
        (
            "another repository cannot spend a controlled failure",
            {"run": _run_state(), "artifacts": [], "check_runs": []},
            {"GITHUB_REPOSITORY": "someone-else/carr-system"},
            _proof_args(),
        ),
        (
            "an expected head that is not this run's GITHUB_SHA refuses",
            {"run": _run_state(), "artifacts": [], "check_runs": []},
            {},
            _proof_args(head="b" * 40),
        ),
        (
            "a short expected head refuses",
            {"run": _run_state(), "artifacts": [], "check_runs": []},
            {},
            _proof_args(head="abc123"),
        ),
        (
            "an uppercase expected head refuses rather than being normalised",
            {"run": _run_state(), "artifacts": [], "check_runs": []},
            {},
            _proof_args(head="A" * 40),
        ),
        (
            "a malformed proof ID refuses",
            {"run": _run_state(), "artifacts": [], "check_runs": []},
            {},
            _proof_args(proof_id="not-a-uuid"),
        ),
        (
            "an uppercase proof ID is not canonical and refuses",
            {"run": _run_state(), "artifacts": [], "check_runs": []},
            {},
            _proof_args(proof_id=_PROOF_ID.upper()),
        ),
        (
            "a proof ID already spent on this head refuses",
            {"run": _run_state(), "artifacts": [], "check_runs": [_spent_proof_check()]},
            {},
            _proof_args(),
        ),
        (
            "an artifact already on this run refuses before any Check write",
            {"run": _run_state(), "artifacts": [_artifact()], "check_runs": []},
            {},
            _proof_args(),
        ),
        (
            "exhausted Check pagination fails closed instead of reporting a clean head",
            {
                "run": _run_state(),
                "artifacts": [],
                "check_runs": [],
                "check_pagination_exhausted": True,
            },
            {},
            _proof_args(),
        ),
        (
            "a provider Check-listing failure refuses",
            {"run": _run_state(), "artifacts": [], "check_runs": [], "check_list_error": True},
            {},
            _proof_args(),
        ),
        (
            "a provider artifact-listing failure refuses",
            {"run": _run_state(), "artifacts": [], "check_runs": [], "artifact_list_error": True},
            {},
            _proof_args(),
        ),
    ]
    for label, state, overrides, args in refusals:
        for command in ("validate-controlled-failure", "controlled-failure"):
            with tempfile.TemporaryDirectory(prefix="carr-wr54-proof-refuse-") as raw:
                env, state_path, _ = _fixture_env(Path(raw), dict(state), overrides)
                before = json.dumps(_read_json(state_path).get("check_runs"), sort_keys=True)
                refused = _invoke(env, command, *args)
                after = json.dumps(_read_json(state_path).get("check_runs"), sort_keys=True)
                _check(
                    f"{command}: {label}",
                    refused.returncode != 0
                    and refused.returncode != _CONTROLLED_FAILURE_EXIT
                    and after == before,
                    f"rc={refused.returncode} err={refused.stderr!r} checks={after}",
                )

    # ── the exact proof: one Check, flipped once, and a failing exit ─────────
    with tempfile.TemporaryDirectory(prefix="carr-wr54-proof-write-") as raw:
        env, state_path, log_path = _fixture_env(
            Path(raw),
            {"run": _run_state(conclusion="failure"), "artifacts": [], "check_runs": []},
        )
        started = _invoke(env, "producer-start")
        proved = _invoke(env, "controlled-failure", *_proof_args())
        rows = _read_json(state_path).get("check_runs", [])
        item = rows[0] if isinstance(rows, list) and len(rows) == 1 else {}
        try:
            summary = json.loads(item.get("output", {}).get("summary", "{}"))
            bound = json.loads(item.get("external_id", ""))
        except (json.JSONDecodeError, TypeError, AttributeError):
            summary, bound = {}, {}
        _check(
            "controlled-failure flips only this run's Check to a structured proof failure",
            started.returncode == 0
            and proved.returncode == _CONTROLLED_FAILURE_EXIT
            and item.get("name") == "Backup artifact"
            and item.get("status") == "completed"
            and item.get("conclusion") == "failure"
            and bound == _identity()
            and summary == {
                "signal": "backup-failure",
                "reason": _PROOF_REASON,
                "proof_id": _PROOF_ID,
            },
            f"rc={proved.returncode} err={proved.stderr!r} checks={json.dumps(rows, sort_keys=True)}",
        )
        _check(
            "the controlled proof reads no credential and creates no artifact",
            _read_json(state_path).get("artifacts") == []
            and not any(
                "/actions/artifacts" in str(call.get("url", ""))
                for call in _calls(log_path)
            ),
            json.dumps(_calls(log_path), sort_keys=True),
        )
        observed = _invoke(env, "observe")
        result = _parsed_output(observed)
        _check(
            "the observer reports the proof failure with its structured proof_id",
            observed.returncode == 1
            and result.get("state") == "failure"
            and result.get("signal") == "backup-failure"
            and result.get("proof_id") == _PROOF_ID
            and result.get("detail") == _PROOF_REASON,
            f"rc={observed.returncode} out={observed.stdout!r}",
        )
        replayed = _invoke(env, "validate-controlled-failure", *_proof_args())
        _check(
            "the same proof ID cannot be spent twice on the same head",
            replayed.returncode != 0 and replayed.returncode != _CONTROLLED_FAILURE_EXIT,
            f"rc={replayed.returncode} err={replayed.stderr!r}",
        )

    # ── the write boundary needs this attempt's own in-progress Check ────────
    with tempfile.TemporaryDirectory(prefix="carr-wr54-proof-no-check-") as raw:
        env, state_path, _ = _fixture_env(
            Path(raw), {"run": _run_state(), "artifacts": [], "check_runs": []},
        )
        orphan = _invoke(env, "controlled-failure", *_proof_args())
        _check(
            "controlled-failure without a started Check refuses and creates none",
            orphan.returncode != 0
            and orphan.returncode != _CONTROLLED_FAILURE_EXIT
            and _read_json(state_path).get("check_runs") == [],
            f"rc={orphan.returncode} err={orphan.stderr!r}",
        )

    # A Check that exists but has not reached in_progress is the case the
    # conclusion guard alone does NOT cover: it carries no conclusion, so only
    # the status guard can refuse it. A mutation run found this uncovered.
    with tempfile.TemporaryDirectory(prefix="carr-wr54-proof-queued-") as raw:
        queued = _named_check("neutral")
        queued["status"] = "queued"
        queued["conclusion"] = None
        env, state_path, _ = _fixture_env(
            Path(raw), {"run": _run_state(), "artifacts": [], "check_runs": [queued]},
        )
        premature = _invoke(env, "controlled-failure", *_proof_args())
        rows = _read_json(state_path).get("check_runs", [])
        _check(
            "controlled-failure refuses a Check that has not reached in_progress",
            premature.returncode != 0
            and premature.returncode != _CONTROLLED_FAILURE_EXIT
            and isinstance(rows, list)
            and all(
                row.get("status") == "queued" and row.get("conclusion") is None
                for row in rows
            ),
            f"rc={premature.returncode} err={premature.stderr!r} checks={json.dumps(rows, sort_keys=True)}",
        )

    with tempfile.TemporaryDirectory(prefix="carr-wr54-proof-success-") as raw:
        env, state_path, _ = _fixture_env(
            Path(raw),
            {"run": _run_state(), "artifacts": [_artifact()], "check_runs": [_named_check("success")]},
        )
        over_success = _invoke(env, "controlled-failure", *_proof_args())
        rows = _read_json(state_path).get("check_runs", [])
        _check(
            "controlled-failure refuses to overwrite an artifact-backed success Check",
            over_success.returncode != 0
            and over_success.returncode != _CONTROLLED_FAILURE_EXIT
            and isinstance(rows, list)
            and all(row.get("conclusion") == "success" for row in rows),
            f"rc={over_success.returncode} err={over_success.stderr!r} checks={json.dumps(rows, sort_keys=True)}",
        )

    with tempfile.TemporaryDirectory(prefix="carr-wr54-proof-ambiguous-") as raw:
        second = _named_check("neutral")
        second["id"] = 8002
        env, state_path, _ = _fixture_env(
            Path(raw),
            {
                "run": _run_state(),
                "artifacts": [],
                "check_runs": [_named_check("neutral"), second],
            },
        )
        ambiguous = _invoke(env, "controlled-failure", *_proof_args())
        rows = _read_json(state_path).get("check_runs", [])
        _check(
            "two exact Checks make the proof ambiguous and it refuses",
            ambiguous.returncode != 0
            and ambiguous.returncode != _CONTROLLED_FAILURE_EXIT
            and isinstance(rows, list)
            and all(row.get("conclusion") == "neutral" for row in rows),
            f"rc={ambiguous.returncode} err={ambiguous.stderr!r}",
        )


def _workflow_mode_contract(source: str, backup_job: str) -> None:
    """The gate resolves three modes, and every effect step names exactly one."""
    dispatch = re.search(
        r"(?ms)^  workflow_dispatch:\s*\n(?P<body>.*?)(?=^\S|^concurrency:|\Z)", source
    )
    dispatch_body = dispatch.group("body") if dispatch else ""
    # EACH INPUT IS READ FROM ITS OWN BLOCK. A single regex walking the whole
    # dispatch body with .*? will happily satisfy one input's "required: false"
    # from the OTHER input's, so flipping either one to required stays green —
    # which is exactly what a mutation run caught this assertion doing.
    # NO DOTALL HERE. With (?s) the `.` in `.*\n` matches newlines too, so the
    # first input's block ran straight through the second one and the whole
    # per-input scoping was decorative — the same mutation caught that as well.
    def input_block(name: str) -> str:
        match = re.search(
            rf"(?m)^      {name}:\n(?P<body>(?:^ {{8,}}[^\n]*\n)*)",
            dispatch_body,
        )
        return match.group("body") if match else ""

    optional = {
        name: block
        for name in ("wr54_failure_proof_id", "wr54_failure_proof_expected_head")
        if (block := input_block(name))
        and re.search(r"(?m)^        type: string\s*$", block)
        and re.search(r"(?m)^        required: false\s*$", block)
        and re.search(r"(?m)^        default: \"\"\s*$", block)
    }
    _check(
        "both proof inputs are optional and default to empty",
        len(optional) == 2,
        "an ordinary manual dispatch must stay byte-for-behavior unchanged, which "
        "means both inputs optional with an empty default; satisfied: "
        + json.dumps(sorted(optional)),
    )
    _check(
        "the gate resolves exactly the three declared modes",
        all(f"MODE={mode}" in backup_job for mode in ("backup", "disabled", "failure-proof"))
        and 'echo "mode=$MODE" >> "$GITHUB_OUTPUT"' in backup_job
        and "gate.outputs.enabled" not in backup_job,
        "the three-mode gate replaces the old boolean enabled output",
    )
    # ORDER IS ASSERTED ON THE INVOCATIONS, NOT ON THE PROSE. Comments name
    # these commands too, so an index into the raw job text would measure where
    # a sentence sits rather than where a step runs.
    start_call = "backup-workflow-status.py producer-start"
    validate_call = "backup-workflow-status.py validate-controlled-failure"
    convert_call = "backup-workflow-status.py controlled-failure"
    _check(
        "a half-filled proof request is refused at the gate, before producer-start",
        "refusing before producer-start" in backup_job
        and start_call in backup_job
        and backup_job.index("refusing before producer-start") < backup_job.index(start_call),
        "the refusal must precede the first durable write, so nothing needs unwinding",
    )
    _check(
        "a non-dispatch event carrying proof inputs is refused rather than downgraded",
        bool(re.search(
            r'\[ "\$EVENT_NAME" != "workflow_dispatch" \][^\n]*\\\n[^\n]*'
            r'\{ \[ -n "\$PROOF_ID" \] \|\| \[ -n "\$PROOF_EXPECTED_HEAD" \]; \}',
            backup_job,
        )),
        "a scheduled event must never enter failure-proof mode, and must not fall back to backup",
    )

    steps = _workflow_steps(backup_job)
    effect_tokens = (
        "secrets.BACKUP_DATABASE_URL",
        "secrets.R2_ACCESS_KEY_ID",
        "secrets.R2_SECRET_ACCESS_KEY",
        "vars.CLOUDFLARE_ACCOUNT_ID",
        "backup-dump.sh",
        "upload-artifact",
        "s3api",
        "producer-complete",
        "setup-python",
        "requirements.lock",
        "postgresql-client-18",
    )
    ungated = [
        step.splitlines()[0].strip()
        for step in steps
        if any(token in step for token in effect_tokens)
        and "steps.gate.outputs.mode == 'backup'" not in step
    ]
    _check(
        "every credential, dependency, dump, upload and R2 step is gated to mode=backup",
        bool(steps) and not ungated,
        "ungated effect steps: " + json.dumps(ungated),
    )
    invoking = {
        call: [step for step in steps if call in step]
        for call in (start_call, validate_call, convert_call,
                     "backup-workflow-status.py producer-fail",
                     "backup-workflow-status.py neutral-cancel")
    }
    proof_steps = invoking[validate_call] + invoking[convert_call]
    _check(
        "validation runs read-only before producer-start, and the write step after it",
        len(invoking[validate_call]) == 1
        and len(invoking[convert_call]) == 1
        and all(
            _step_condition(step) == "steps.gate.outputs.mode == 'failure-proof'"
            for step in proof_steps
        )
        and backup_job.index(validate_call)
        < backup_job.index(start_call)
        < backup_job.index(convert_call),
        "expected exactly one failure-proof-gated validate step and one convert step, "
        "in validate/start/convert order",
    )
    _check(
        "producer-start is the one step shared by backup and failure-proof",
        len(invoking[start_call]) == 1
        and _step_condition(invoking[start_call][0])
        == "steps.gate.outputs.mode == 'backup' || steps.gate.outputs.mode == 'failure-proof'",
        "the proof needs a real in-progress Check to convert, and nothing else in common",
    )
    recorder = invoking["backup-workflow-status.py producer-fail"]
    _check(
        "the generic pipeline-failure recorder stays mode=backup only",
        len(recorder) == 1
        and "steps.gate.outputs.mode == 'backup'" in _step_condition(recorder[0])
        and "failure-proof" not in _step_condition(recorder[0]),
        "a generic recorder reachable in proof mode could overwrite or duplicate the "
        "proof Check: " + json.dumps([_step_condition(s) for s in recorder]),
    )
    canceller = invoking["backup-workflow-status.py neutral-cancel"]
    _check(
        "the disabled scheduled branch keeps its neutral self-cancel path",
        len(canceller) == 1
        and _step_condition(canceller[0]) == "steps.gate.outputs.mode == 'disabled'"
        and 'CLOUD_BACKUP_ENABLED:-}" = "true"' in backup_job,
        "the reviewed disabled predicate and its neutral/self-cancel semantics are unchanged",
    )


def _workflow_steps(job_body: str) -> list[str]:
    """Split a job body into its step blocks on the six-space list markers.

    A run of comment lines directly above a marker belongs to the step it
    introduces, not to the one it follows. Attaching it to the previous block
    would file every explanatory header under the wrong step and quietly
    corrupt any scan that reads a block's text.
    """
    blocks: list[str] = []
    current: list[str] = []
    pending: list[str] = []
    for line in job_body.splitlines():
        if re.match(r"^      - (?:name|uses):", line):
            if current:
                blocks.append("\n".join(current))
            current = [*pending, line]
            pending = []
        elif re.match(r"^      #", line):
            pending.append(line)
        elif current:
            current.extend(pending)
            pending = []
            current.append(line)
    if current:
        current.extend(pending)
        blocks.append("\n".join(current))
    return blocks


def _step_condition(step: str) -> str:
    """The step's own `if:` expression, or empty when it has none."""
    match = re.search(r"(?m)^        if: (?P<expr>.*)$", step)
    return match.group("expr").strip() if match else ""


def _service_identity_contract() -> None:
    """One cloud-backup service identity, and it aliases neither neighbour."""
    try:
        catalog = json.loads(_SERVICES.read_text(encoding="utf-8"))
        services = catalog.get("services", [])
    except (OSError, json.JSONDecodeError) as exc:
        _check("service catalog parses", False, str(exc))
        return
    rows = [row for row in services if row.get("key") == "backup-nightly-cloud"]
    row = rows[0] if len(rows) == 1 else {}
    environments = row.get("environments", []) if isinstance(row, dict) else []
    production = [e for e in environments if e.get("environment") == "production"]
    _check(
        "the catalog declares exactly one backup-nightly-cloud production identity",
        len(rows) == 1
        and len(environments) == 1
        and len(production) == 1
        and row.get("repo_path") == ".github/workflows/backup-nightly.yml"
        and row.get("runtime") == "github-actions"
        and row.get("owner_actor") == "joe",
        json.dumps(rows, sort_keys=True),
    )
    environment = production[0] if production else {}
    _check(
        "the cloud backup declares no CARR cadence and says why in its own notes",
        "expected_cadence_seconds" not in environment
        and "cadence_grace_seconds" not in environment
        and "not continuously ingested into ops.run" in str(environment.get("notes", ""))
        and "does not detect a missed or skipped schedule" in str(environment.get("notes", ""))
        and "health remains unknown" in str(environment.get("notes", "")),
        "a declared cadence would invent continuous ingestion; the notes must carry the "
        "unknown-state limit and must not claim the failure email detects a missed schedule: "
        + json.dumps(environment, sort_keys=True),
    )
    _check(
        "the cloud backup never aliases nightly-record-layer or restore-rehearse-weekly",
        row.get("key") == "backup-nightly-cloud"
        and row.get("repo_path") not in ("bin/nightly.sh",)
        and all(
            other.get("repo_path") != row.get("repo_path")
            for other in services
            if other.get("key") != "backup-nightly-cloud"
        ),
        "the Mac-local nightly chain and the weekly restore rehearsal are distinct services",
    )


# The closed union of privileged words from the standing authority rule, swept
# as exact match AND substring. A receipt key or value that lands inside it is a
# label pretending to be authority, which is why this suite refuses one.
_PRIVILEGED_WORDS = (
    "allow", "commit", "prompt", "suppress", "release", "read", "covered",
    "drafted", "proposed", "queued", "healthy", "passing", "ok", "pass",
    "satisfied", "complete", "admitted", "resumed", "attended", "verified",
    "present", "equivalent", "operational", "active", "green", "joins_exactly",
    "coverage_complete", "favorable",
)


def _privileged_hits(value: object, path: str = "") -> list[str]:
    """Every place a privileged word appears in a receipt's keys or string values."""
    hits: list[str] = []
    if isinstance(value, dict):
        for key, item in value.items():
            text = str(key).lower()
            hits.extend(f"{path}{key} (key: {word})" for word in _PRIVILEGED_WORDS
                        if word in text)
            if re.search(r"^would_|_if_authoritative", text):
                hits.append(f"{path}{key} (key: reserved prefix or suffix)")
            hits.extend(_privileged_hits(item, f"{path}{key}."))
    elif isinstance(value, list):
        for index, item in enumerate(value):
            hits.extend(_privileged_hits(item, f"{path}{index}."))
    elif isinstance(value, str):
        text = value.lower()
        hits.extend(f"{path} (value {value!r}: {word})" for word in _PRIVILEGED_WORDS
                    if word in text)
        if re.search(r"^would_|_if_authoritative", text):
            hits.append(f"{path} (value {value!r}: reserved prefix or suffix)")
    return hits


_PROVIDER_BINDING = """    suite_id = _provider_suite_id(item, identity)
    return suite_id is not None and suite_id in seam_suites"""
# What the EXCLUSION decided on before the re-review forged it: the envelope the
# Check's own creator wrote. The control below puts exactly this back.
_FORGEABLE_BINDING = "    return check_identity(item) is not None"
# The POSITIVE half's binding, and the shape it must never collapse to. The
# retired exclusion trusted a name outright; the control below asks what happens
# if the required check is trusted the same way, and the answer must be that a
# success from another workflow file starts carrying the head.
_POSITIVE_BINDING = """    suite_id = _provider_suite_id(item, identity)
    if suite_id is None or suite_id not in required_suites:
        return False
    return item.get("name") == REQUIRED_CHECK_NAME"""
_NAME_ONLY_BINDING = '    return item.get("name") == REQUIRED_CHECK_NAME'


def _mutated_status_tree(original: str, replacement: str, prefix: str) -> Path | None:
    """A copy of the door's own tree with ONE decision rewritten, nothing else.

    THE CONTROL THIS EXISTS FOR. Every refusal below is consistent with a door
    that refuses everything -- a predicate hard-wired to ``return False`` would
    pass all of them and fail only the positive control. What has to be shown is
    that one specific decision is what refuses a given fixture. So a tree is
    built whose only difference is that decision, and the caller watches the same
    fixture DISPATCH there.

    Returns None if ``original`` is not found exactly once, and every caller
    reports a FAIL rather than skipping: a control that silently stops
    controlling is worse than no control.
    """
    source = _STATUS_HELPER.read_text(encoding="utf-8")
    if source.count(original) != 1:
        return None
    root = Path(tempfile.mkdtemp(prefix=prefix))
    (root / "ops" / "config").mkdir(parents=True)
    (root / "lib").mkdir()
    (root / "ops" / _STATUS_HELPER.name).write_text(
        source.replace(original, replacement, 1), encoding="utf-8")
    shutil.copy2(_ROOT / "lib" / "platform_metering.py", root / "lib" / "platform_metering.py")
    shutil.copy2(_METERING_POLICY, root / "ops" / "config" / _METERING_POLICY.name)
    return root


def _unbound_predicate_tree() -> Path | None:
    """The door's tree with the seam EXCLUSION's provider binding taken back out."""
    return _mutated_status_tree(
        _PROVIDER_BINDING, _FORGEABLE_BINDING, "carr-dispatch-unbound-")


def _name_only_required_tree() -> Path | None:
    """The door's tree with the REQUIRED CHECK authenticated by its name alone."""
    return _mutated_status_tree(
        _POSITIVE_BINDING, _NAME_ONLY_BINDING, "carr-dispatch-nameonly-")


def _paused_policy_tree() -> Path:
    """A copy of the door's own tree whose metering policy re-imposes the pause.

    The door resolves its policy from its OWN resolved location, not from an
    environment variable, which is the property that makes it a door rather than
    a bypass -- so the only honest way to watch a real metering refusal is to run
    the door from a tree whose policy says no. Nothing is patched, injected or
    rebound: the same bytes of the same script read a different sealed file.
    """
    root = Path(tempfile.mkdtemp(prefix="carr-dispatch-paused-"))
    (root / "ops" / "config").mkdir(parents=True)
    (root / "lib").mkdir()
    shutil.copy2(_STATUS_HELPER, root / "ops" / _STATUS_HELPER.name)
    shutil.copy2(_ROOT / "lib" / "platform_metering.py", root / "lib" / "platform_metering.py")
    policy = json.loads(_METERING_POLICY.read_text(encoding="utf-8"))
    policy["temporary_controls"]["github_actions_pause"]["repository_actions_enabled"] = False
    (root / "ops" / "config" / "platform-metering.v1.json").write_text(
        json.dumps(policy, indent=2), encoding="utf-8")
    return root


def _code_only(body: str) -> str:
    """A function body with its leading docstring removed.

    The checks below assert that a decision never CONSULTS something -- the
    Check's name, its output, the envelope its creator wrote. Those same words
    have to appear in the docstrings, because a reader who does not know what
    was retired cannot tell a hardening from an omission. Searching the raw body
    therefore reports a violation for the prose that explains the rule, so the
    prose is stripped and only the statements are searched.
    """
    parts = body.split('"""')
    return parts[2] if len(parts) >= 3 else body


def _dispatch_door_contract() -> None:
    """The reviewed dispatch door: admit the spend, or reach no vendor at all.

    The door exists because hooks/guard-unattended.py refuses a session-issued
    `gh workflow run` and names a reviewed in-process admission as the sanctioned
    shape instead. These checks hold the three properties that make it that
    rather than a bypass: the budget refusal happens BEFORE the dispatch POST and
    is proved by making the budget actually refuse, the only workflow, ref and
    input names it can reach are literals, and the fact the budget gate requires
    is established from the head's own Checks rather than asserted.
    """
    dispatch = "dispatch-controlled-failure"
    status_source = _STATUS_HELPER.read_text(encoding="utf-8")
    # The door's exact signature is the anchor for everything below, so it is
    # asserted first and the later reads are written to report FAIL rather than
    # raise when it is gone. A renamed door used to take this whole function down
    # with a traceback, which reads as a broken suite instead of a missing door.
    signature = "def _dispatch_controlled_failure(proof_id: str, head: str) -> int:"
    _check(
        "the dispatch door is module-private and takes no caller object",
        signature in status_source
        and "\ndef dispatch_controlled_failure" not in status_source,
        "a public door that reads attributes off a caller's object runs caller code "
        "between the guards and the POST",
    )
    # Read the DOOR'S OWN BODY, not the file. Asked of the whole file, "does it
    # call the metering authorizer" is answered yes by the import line alone
    # — which is how this check first passed against a door whose admission had
    # been deleted outright.
    door_body = (status_source.split(signature, 1)[-1].split("\ndef ", 1)[0]
                 if signature in status_source else "")
    # THE PRIVATE SPELLING IS PART OF THE ASSERTION. The door must call the gate
    # through the underscore-aliased import, because the public one would also
    # be an export of this repository's decision function through the status
    # module -- which is the finding _public_surface_contract now closes.
    _check(
        "the dispatch door admits the metered spend in its own body",
        "_authorize_metered_execution(" in door_body
        and '"github-actions-remote-ci"' in door_body,
        "a dispatcher that skips admission is the bypass the metering gate exists to stop",
    )
    _check(
        "the dispatch door admits BEFORE it reaches the vendor",
        "_authorize_metered_execution(" in door_body and "/dispatches" in door_body
        and door_body.index("_authorize_metered_execution(") < door_body.index("/dispatches"),
        "admitting after the POST spends the minutes the admission was guarding",
    )
    _check(
        "the dispatch door establishes green local checks before it admits",
        "_require_head_checks_clean(identity)" in door_body
        and "_authorize_metered_execution(" in door_body
        and door_body.index("_require_head_checks_clean(identity)")
        < door_body.index("_authorize_metered_execution("),
        "handing the budget gate a literal True makes the admission decorative",
    )
    # HOW the head evidence is scoped, read off the two functions that decide
    # it. A source read rather than a behavioural one because the behavioural
    # controls below can only prove the outcome for the shapes they seed; this
    # proves the DECISION never consults the Check's name at all.
    clean_signature = "def _require_head_checks_clean(identity: Identity) -> None:"
    clean_body = (status_source.split(clean_signature, 1)[-1].split("\ndef ", 1)[0]
                  if clean_signature in status_source else "")
    # The predicates' exact signatures, and the suite set is part of each: a
    # predicate that still took only (item, identity) could not consult the
    # provider at all, and these reads report FAIL rather than matching a
    # renamed or narrowed one.
    predicate_signature = (
        "def _is_seam_producer_check(\n"
        "    item: dict[str, Any],\n"
        "    identity: Identity,\n"
        "    seam_suites: frozenset[int],\n"
        ") -> bool:"
    )
    predicate_body = (status_source.split(predicate_signature, 1)[-1].split("\ndef ", 1)[0]
                      if predicate_signature in status_source else "")
    required_signature = (
        "def _is_authentic_required_check(\n"
        "    item: dict[str, Any],\n"
        "    identity: Identity,\n"
        "    required_suites: frozenset[int],\n"
        ") -> bool:"
    )
    required_body = (status_source.split(required_signature, 1)[-1].split("\ndef ", 1)[0]
                     if required_signature in status_source else "")
    stamp_signature = (
        "def _provider_suite_id(item: dict[str, Any], identity: Identity) -> int | None:")
    stamp_body = (status_source.split(stamp_signature, 1)[-1].split("\ndef ", 1)[0]
                  if stamp_signature in status_source else "")
    clean_code = _code_only(clean_body)
    predicate_code = _code_only(predicate_body)
    required_code = _code_only(required_body)
    stamp_code = _code_only(stamp_body)
    _check(
        "the head-evidence scan excludes by authenticated producer, not by Check name",
        "_is_seam_producer_check(item, identity, seam_suites)" in clean_code
        and "CHECK_NAME" not in clean_code.replace("REQUIRED_CHECK_NAME", ""),
        "excluding by name lets anyone who can attach that label hide a red Check "
        "from the evidence the budget admission stands on",
    )
    _check(
        "the provider stamp reader decides on provider facts only, never on creator text",
        "ACTIONS_APP_ID" in stamp_code
        and "ACTIONS_APP_SLUG" in stamp_code
        and 'item.get("check_suite")' in stamp_code
        and 'item.get("head_sha", "")' in stamp_code
        and "check_identity(" not in stamp_code
        and "external_id" not in stamp_code
        and "BACKUP_CHECK_ENVELOPE_KEYS" not in status_source
        and "CHECK_NAME" not in stamp_code
        and '.get("name")' not in stamp_code,
        "the app stamp is shared with every other Actions Check on the head and "
        "external_id is text any creator can copy, so the pair authenticated nothing: "
        "only the provider's own check-suite binding does",
    )
    _check(
        "the seam exclusion turns on the provider's suite set and nothing else",
        "_provider_suite_id(item, identity)" in predicate_code
        and "seam_suites" in predicate_code
        and "external_id" not in predicate_code
        and "CHECK_NAME" not in predicate_code
        and '.get("name")' not in predicate_code,
        "a row the seam did not write must not be excluded by anything its creator "
        "could attach to it",
    )
    # THE POSITIVE HALF, and the property that makes consulting the name safe
    # there: the provider authentication runs FIRST, and the name can only narrow
    # a set the provider has already attributed to ci.yml on this head.
    _check(
        "the required check authenticates through the provider before it reads a name",
        "_provider_suite_id(item, identity)" in required_code
        and "required_suites" in required_code
        and 'item.get("name") == REQUIRED_CHECK_NAME' in required_code
        and required_code.index("_provider_suite_id(item, identity)")
        < required_code.index('item.get("name")')
        and required_code.index("required_suites")
        < required_code.index('item.get("name")')
        and "external_id" not in required_code,
        "a name read before or instead of the provider's suite binding is the retired "
        "name-only rule wearing the required check's label",
    )
    resolver_signature = (
        "def _workflow_run_check_suites(\n"
        "    identity: Identity,\n"
        "    workflow_file: str,\n"
        "    workflow_path: str,\n"
        ") -> frozenset[int]:"
    )
    resolver_body = (status_source.split(resolver_signature, 1)[-1].split("\ndef ", 1)[0]
                     if resolver_signature in status_source else "")
    resolver_code = _code_only(resolver_body)
    _check(
        "check-suites are resolved from the provider's own run listing",
        "workflow_file" in resolver_code
        and 'row.get("path") != workflow_path' in resolver_code
        and '"head_sha": identity.head_sha' in resolver_code
        and 'str(row.get("head_sha", "")).lower() != identity.head_sha' in resolver_code
        and '"check_suite_id"' in resolver_code
        and "external_id" not in resolver_code,
        "a suite set built from anything the Check itself carries would hand both "
        "decisions straight back to the creator",
    )
    _check(
        "both suite resolvers name their workflow with this file's own literals",
        "return _workflow_run_check_suites(\n"
        "        identity, BACKUP_WORKFLOW_FILE, BACKUP_WORKFLOW_PATH)" in status_source
        and "return _workflow_run_check_suites(\n"
        "        identity, REQUIRED_CHECK_WORKFLOW_FILE, REQUIRED_CHECK_WORKFLOW_PATH)"
        in status_source
        and 'BACKUP_WORKFLOW_PATH = ".github/workflows/" + BACKUP_WORKFLOW_FILE'
        in status_source
        and 'REQUIRED_CHECK_WORKFLOW_PATH = ".github/workflows/" '
            "+ REQUIRED_CHECK_WORKFLOW_FILE" in status_source
        and "args." not in resolver_code,
        "a resolver whose workflow file came from the caller would authenticate nothing",
    )
    # THE REGISTERED IDENTITY THE LITERALS RESTATE. AGENTS.md names the required
    # context and ops/config/automerge-pilot-policy.v1.json registers its
    # provider identity; the door restates both as literals because it has to run
    # from a copied tree with no second file to load. Equal spellings are
    # asserted here so the restatement cannot drift into a check nobody produces.
    registered_raw = json.loads(
        (_ROOT / "ops" / "config" / "automerge-pilot-policy.v1.json")
        .read_text(encoding="utf-8")).get("required_check")
    registered = registered_raw if isinstance(registered_raw, dict) else {}
    _check(
        "the door's required-check literals are the ones the repository registers",
        'REQUIRED_CHECK_NAME = "ops/ci.sh --strict"' in status_source
        and 'REQUIRED_CHECK_WORKFLOW_FILE = "ci.yml"' in status_source
        and 'REQUIRED_CHECK_CONCLUSION = "success"' in status_source
        and registered.get("name") == "ops/ci.sh --strict"
        and registered.get("workflow_file") == "ci.yml"
        and registered.get("app_slug") == "github-actions"
        and 'ACTIONS_APP_SLUG = "github-actions"' in status_source,
        f"the door would demand a check nobody produces: registered {registered!r}",
    )
    _check(
        "the head-evidence scan resolves both suite sets before it scans",
        "_seam_run_check_suites(identity)" in clean_code
        and "_required_check_suites(identity)" in clean_code
        and "_is_seam_producer_check(item, identity, seam_suites)" in clean_code
        and "_is_authentic_required_check(item, identity, required_suites)" in clean_code
        and max(clean_code.index("_seam_run_check_suites(identity)"),
                clean_code.index("_required_check_suites(identity)"))
        < min(clean_code.index("_is_seam_producer_check(item, identity, seam_suites)"),
              clean_code.index(
                  "_is_authentic_required_check(item, identity, required_suites)")),
        "a predicate asked without the provider's suite set could only fall back on "
        "what the row says about itself",
    )
    _check(
        "absence of red is not enough: an authenticated required success is demanded",
        "if not authentic:" in clean_code
        and "raise StatusError(" in clean_code.split("if not authentic:", 1)[-1]
        and 'item.get("conclusion") == REQUIRED_CHECK_CONCLUSION' in clean_code,
        "a head whose only evidence is the absence of failures is a head a caller can "
        "manufacture with one neutral Check",
    )
    _check(
        "the registered producer identity is a literal pair, not a caller argument",
        "ACTIONS_APP_ID = 15368" in status_source
        and 'ACTIONS_APP_SLUG = "github-actions"' in status_source
        and "ACTIONS_APP_ID" not in door_body,
        "a producer identity taken from the caller would authenticate nothing",
    )
    _check(
        "the dispatch door can start exactly one named workflow",
        'BACKUP_WORKFLOW_FILE = "backup-nightly.yml"' in status_source
        and "BACKUP_WORKFLOW_FILE" in door_body
        and "args." not in door_body,
        "a caller-named workflow would make this a general door",
    )

    def fixture(state_extra: dict[str, object] | None = None):
        root = Path(tempfile.mkdtemp(prefix="carr-dispatch-door-"))
        state: dict[str, object] = {
            "run": _run_state(), "artifacts": [],
            # BOTH halves of the door's evidence test, seeded green: one
            # ordinary Check with nothing failing, and the repository's own
            # required check authenticated to a ci.yml run on this head.
            "check_runs": _green_head(),
            "branch_head": {"sha": _PROOF_HEAD},
            # What the provider answers about backup-nightly.yml's own runs.
            # The door reads this to learn which check-suites are the seam's;
            # a state that omitted it would exclude nothing at all.
            "workflow_runs": _workflow_runs(),
        }
        state.update(state_extra or {})
        return _fixture_env(root, state)

    # THE HAPPY PATH, and the only one that may reach the vendor.
    env, state_path, log_path = fixture()
    result = _invoke(env, dispatch, *_proof_args())
    raw_dispatched = _read_json(state_path).get("dispatches")
    dispatched: list[dict[str, object]] = [
        row for row in raw_dispatched if isinstance(row, dict)
    ] if isinstance(raw_dispatched, list) else []
    body = dispatched[0].get("body") if dispatched else {}
    inputs = body.get("inputs") if isinstance(body, dict) else {}
    _check(
        f"{dispatch}: an admitted request dispatches the seeded inputs on main",
        result.returncode == 0
        and len(dispatched) == 1
        and str(dispatched[0].get("url", "")).endswith(
            "/actions/workflows/backup-nightly.yml/dispatches")
        and isinstance(body, dict) and body.get("ref") == "main"
        and isinstance(inputs, dict)
        and inputs.get("wr54_failure_proof_id") == _PROOF_ID
        and inputs.get("wr54_failure_proof_expected_head") == _PROOF_HEAD,
        f"expected one dispatch carrying both proof inputs, got rc={result.returncode} "
        f"{result.stderr.strip()} {dispatched!r}",
    )
    listings = [
        row for row in _calls(log_path)
        if str(row.get("url", "")).endswith("/actions/workflows/backup-nightly.yml/runs")
    ]
    def scoped_to_this_head(row: dict[str, object]) -> bool:
        """Did this recorded call carry the head as a query parameter?

        The logged ``args`` is read back as ``object``, so it is narrowed to a
        list before being iterated rather than trusted to be one.
        """
        raw = row.get("args")
        return f"head_sha={_PROOF_HEAD}" in (
            [str(item) for item in raw] if isinstance(raw, list) else [])

    _check(
        f"{dispatch}: the seam's suites are asked of one workflow file on this head",
        bool(listings) and all(scoped_to_this_head(row) for row in listings),
        "the door must scope its own question even though it re-reads every answer: "
        f"got {listings!r}",
    )
    required_listings = [
        row for row in _calls(log_path)
        if str(row.get("url", "")).endswith("/actions/workflows/ci.yml/runs")
    ]
    _check(
        f"{dispatch}: the required check's suites are asked of ci.yml on this head",
        bool(required_listings)
        and all(scoped_to_this_head(row) for row in required_listings),
        "the positive half has to resolve the required workflow's own suites from the "
        f"provider before it trusts any row that carries that name: got {required_listings!r}",
    )
    receipt = _parsed_output(result)
    digest = receipt.get("metering_gate_digest")
    _check(
        f"{dispatch}: the receipt records WHICH admission it stands on, opaquely",
        receipt.get("state") == "dispatched"
        and receipt.get("metering_gate") == "github-actions-remote-ci"
        and isinstance(digest, str) and bool(re.fullmatch(r"[0-9a-f]{16}", digest)),
        f"expected an opaque 16-hex admission digest beside the gate key, got {receipt!r}",
    )
    _check(
        f"{dispatch}: no receipt key or value is a privileged word",
        not _privileged_hits(receipt),
        f"a receipt an auditor can read as the outcome is a label wearing authority: "
        f"{_privileged_hits(receipt)}",
    )
    # The digest is EVIDENCE, not decoration: it must reproduce from the gate's
    # own answer for this same request, and must not be some constant the door
    # could print without ever calling the gate.
    expected = _authorize_metered_execution(
        json.loads(_METERING_POLICY.read_text(encoding="utf-8")),
        "github-actions-remote-ci",
        {"candidate_sha": _PROOF_HEAD, "local_checks_green": True},
    )
    recomputed = hashlib.sha256(json.dumps({
        key: expected.get(key)
        for key in ("admitted", "authority", "gate", "platform", "policy_schema_version")
    }, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()[:16]
    _check(
        f"{dispatch}: the receipt's digest reproduces from the gate's own answer",
        digest == recomputed,
        f"expected {recomputed}, got {digest!r}",
    )

    # EVERY REFUSAL MUST LEAVE THE VENDOR UNTOUCHED. A door that refuses after
    # the POST has already spent the minutes it was guarding.
    refusals: list[tuple[str, dict[str, object], tuple[str, ...]]] = [
        ("a malformed proof ID refuses", {}, _proof_args(proof_id="not-a-uuid")),
        ("an uppercase proof ID refuses rather than being normalised", {},
         _proof_args(proof_id=_PROOF_ID.upper())),
        ("a short expected head refuses", {}, _proof_args(head="abc123")),
        ("an uppercase expected head refuses rather than being normalised", {},
         _proof_args(head="A" * 40)),
        ("an expected head that is not the live main head refuses",
         {"branch_head": {"sha": "b" * 40}}, _proof_args()),
        ("an unreadable main head refuses rather than dispatching blind",
         {"branch_head_error": True}, _proof_args()),
        ("a proof ID already spent on this head refuses",
         {"check_runs": [_spent_proof_check()]}, _proof_args()),
        ("exhausted Check pagination fails closed instead of dispatching",
         {"check_pagination_exhausted": True}, _proof_args()),
        # The fact the budget gate requires is READ, not asserted: these are the
        # head states that cannot support it. Every row that is NOT about a
        # missing required check is seeded on _green_head(), so it refuses for
        # the row under test rather than for the positive half being absent.
        ("a head carrying no Checks at all refuses rather than claiming green",
         {"check_runs": []}, _proof_args()),
        ("a head whose Check has not concluded refuses",
         {"check_runs": _green_head(_ci_check("unconcluded", label="other_ci"))},
         _proof_args()),
        ("a head carrying a failed Check refuses",
         {"check_runs": _green_head(_ci_check("concluded_red", label="other_ci"))},
         _proof_args()),
        # ── THE MUTATION CONTROLS for the POSITIVE half ────────────────────
        # Absence of red is not evidence of green. Each row below is a SUCCESS
        # carrying the required check's exact registered name, and each differs
        # from the authentic one in exactly one provider fact.
        # (1) NOTHING BUT ORDINARY GREEN. The head the happy path used to run on:
        # a concluded, clean Check from some other workflow, and no `ops/ci.sh
        # --strict` row at all. It has no red, and it is not green either.
        ("a head with no required check at all refuses rather than claiming green",
         {"check_runs": [_ci_check("concluded_clean")]}, _proof_args()),
        # (2) UNAUTHENTICATED POSITIVE, no producer stamp. The exact shape the
        # re-review named: a caller-created Check named like the required one.
        ("an unstamped Check named like the required one is not the required check",
         {"check_runs": [_ci_check("concluded_clean"),
                         _required_check(producer="unattributed")]},
         _proof_args()),
        # (3) UNAUTHENTICATED POSITIVE, foreign installation. Same free text,
        # same claimed suite, a different app stamp.
        ("a foreign app's success named like the required one is not the required check",
         {"check_runs": [_ci_check("concluded_clean"),
                         _required_check(producer="foreign_app")]},
         _proof_args()),
        # (4) A FOREIGN WORKFLOW'S SUCCESS. A genuine Actions row in a genuine
        # provider-assigned suite -- produced by another workflow file. The
        # required suite set is scoped by workflow_run.path, so it is not this.
        ("a success from another workflow file is not the required check",
         {"check_runs": [_ci_check("concluded_clean"),
                         _required_check(suite="other_workflow")]},
         _proof_args()),
        # (5) THE RIGHT WORKFLOW, THE WRONG COMMIT. ci.yml really did produce
        # suite 4600 -- on commit d…d. The required set is resolved from a
        # listing filtered to THIS head and re-read per row.
        ("a ci.yml success bound to another head cannot carry this one",
         {"check_runs": [_ci_check("concluded_clean"),
                         _required_check(suite="required_other_head_run")]},
         _proof_args()),
        # (6) NO PROVIDER SUITE AT ALL. A row GitHub placed in no check-suite
        # cannot be attributed to any workflow run.
        ("a required-named Check in no check-suite authenticates nothing",
         {"check_runs": [_ci_check("concluded_clean"),
                         _required_check(suite="absent")]},
         _proof_args()),
        # (7) AUTHENTIC, AND NOT A SUCCESS. This row passes the no-red half
        # untouched -- NON_FAILING_CHECK_CONCLUSIONS accepts neutral -- so it is
        # the one shape that proves the positive half demands REQUIRED_CHECK_
        # CONCLUSION rather than merely "did not fail".
        ("an authentic required check concluded neutral is not a green head",
         {"check_runs": [_ci_check("concluded_clean"),
                         _required_check("concluded_neutral")]},
         _proof_args()),
        # ── THE MUTATION CONTROLS for the producer-identity exclusion ──────
        # Each seeds a CONCLUDED-RED row carrying the seam's own label "Backup
        # artifact" beside a head that would otherwise dispatch, and none of them
        # is the seam's own row. The first shape retired here was the name-only
        # exclusion; the second was the app stamp PLUS the seam's external_id
        # envelope, which review forged -- the envelope is creator text and the
        # stamp is shared with every Actions Check on the head. So every row
        # below carries the forged envelope under the genuine Actions stamp,
        # and differs from the seam only in what the PROVIDER says about it.
        ("a failed Check merely NAMED like the seam's own still counts as red",
         {"check_runs": _green_head(
             _ci_check("concluded_red", label="seam_label", suite="absent"))},
         _proof_args()),
        # THE FORGERY THE RE-REVIEW PERFORMED: the shared Actions app stamp and
        # a byte-perfect copy of this seam's envelope for this very head, on a
        # row the seam never wrote. It was excluded before; it is red now.
        ("a copied seam envelope under the shared Actions stamp counts as red",
         {"check_runs": _green_head(
             _ci_check("concluded_red", label="seam_label",
                       envelope="seam_this_head", suite="absent"))},
         _proof_args()),
        # (1) SAME APP, ANOTHER WORKFLOW FILE. A real Actions run in this
        # repository, with a real provider-assigned suite -- and the suite set
        # is scoped by workflow_run.path, so it is not this seam's.
        ("a failed seam-labelled Check from another workflow file counts as red",
         {"check_runs": _green_head(
             _ci_check("concluded_red", label="seam_label",
                       envelope="seam_this_head", suite="other_workflow"))},
         _proof_args()),
        # (2) SAME WORKFLOW FILE, ANOTHER HEAD. backup-nightly.yml really did
        # produce suite 4400 -- on commit d…d. The suite set is resolved from a
        # listing filtered to THIS head, so it carries no authority here.
        ("a seam-workflow suite bound to another head counts as red on this one",
         {"check_runs": _green_head(
             _ci_check("concluded_red", label="seam_label",
                       envelope="seam_this_head", suite="other_head_run"))},
         _proof_args()),
        ("a failed seam-labelled Check from a foreign app counts as red even when it "
         "claims the seam's own suite",
         {"check_runs": _green_head(
             _ci_check("concluded_red", label="seam_label",
                       producer="foreign_app", suite="seam_run"))},
         _proof_args()),
        ("a failed seam-labelled Check with no producer stamp at all counts as red",
         {"check_runs": _green_head(
             _ci_check("concluded_red", label="seam_label",
                       producer="unattributed", suite="seam_run"))},
         _proof_args()),
        # (3, second half) The authentic seam failure IS excluded -- and being
        # excluded buys the head nothing while any OTHER Check is red.
        ("an excluded seam failure cannot carry a head whose other Check is red",
         {"check_runs": [_ci_check("concluded_red"), _required_check(),
                         _ci_check("concluded_red", label="seam_label",
                                   envelope="seam_this_head", suite="seam_run",
                                   summary="earlier_proof")]},
         _proof_args()),
        # The excluded row is not evidence, so a head carrying ONLY the seam's
        # own authentic failure has nothing left to stand on and refuses rather
        # than reading as clean.
        ("an authentic seam failure alone leaves no evidence to stand on",
         {"check_runs": [_ci_check("concluded_red", label="seam_label",
                                   envelope="seam_this_head", suite="seam_run",
                                   summary="earlier_proof")]},
         _proof_args()),
        # The suite set is read from the provider, so an unreadable listing
        # refuses rather than scanning with an empty one.
        ("an unreadable workflow-run listing refuses rather than dispatching blind",
         {"workflow_run_list_error": True}, _proof_args()),
    ]
    for label, extra, args in refusals:
        env, state_path, log_path = fixture(extra)
        result = _invoke(env, dispatch, *args)
        posted = [
            row for row in _calls(log_path)
            if str(row.get("url", "")).endswith("/dispatches")
        ]
        _check(
            f"{dispatch}: {label}",
            result.returncode != 0 and not posted
            and not (_read_json(state_path).get("dispatches") or []),
            f"expected a refusal with no dispatch POST, got rc={result.returncode} "
            f"posted={posted!r} stderr={result.stderr.strip()}",
        )

    # THE OTHER HALF OF THE MUTATION CONTROL. The exclusion has to still WORK
    # for the one row it was built for, or the door refuses every head that
    # ever seeded a proof -- which is the reason it exists. An authentic seam
    # failure, provider-stamped and carrying this seam's envelope for this head
    # with an EARLIER proof ID in its summary, is excluded; the ordinary green
    # Check beside it carries the admission and a fresh proof dispatches.
    env, state_path, log_path = fixture({"check_runs": _green_head(
        _ci_check("concluded_red", label="seam_label", envelope="seam_this_head",
                  suite="seam_run", summary="earlier_proof"),
    )})
    seeded = _invoke(env, dispatch, *_proof_args())
    raw_after_seed = _read_json(state_path).get("dispatches")
    after_seed = [row for row in raw_after_seed if isinstance(row, dict)] \
        if isinstance(raw_after_seed, list) else []
    _check(
        f"{dispatch}: a head whose only red Check is the seam's own still dispatches",
        seeded.returncode == 0 and len(after_seed) == 1,
        "if the seam's own seeded failure is not excluded, the first proof on a head "
        "poisons every later one: "
        f"rc={seeded.returncode} {seeded.stderr.strip()} {after_seed!r}",
    )

    # MUTATION CONTROL (4): WITHOUT THE PROVIDER BINDING, (1) GOES GREEN.
    # The same fixture as "another workflow file" above -- a red seam-labelled
    # row carrying a copied envelope under the shared Actions stamp, sitting in
    # some other workflow's suite -- run against a tree whose predicate decides
    # on the envelope again. It dispatches, which is what makes the refusal
    # above attributable to the binding rather than to a door that refuses
    # everything.
    unbound_root = _unbound_predicate_tree()
    forged: dict[str, object] = {"check_runs": _green_head(
        _ci_check("concluded_red", label="seam_label", envelope="seam_this_head",
                  suite="other_workflow"),
    )}
    if unbound_root is None:
        _check(
            f"{dispatch}: the provider binding is present to be mutated",
            False,
            "the control could not find the suite-membership decision verbatim, so it "
            "proves nothing about the refusal above",
        )
    else:
        env, state_path, log_path = fixture(forged)
        unbound = subprocess.run(
            [sys.executable, str(unbound_root / "ops" / _STATUS_HELPER.name),
             dispatch, *_proof_args()],
            cwd=unbound_root, env=env, text=True, capture_output=True,
            timeout=8, check=False,
        )
        raw_unbound = _read_json(state_path).get("dispatches")
        unbound_posted = [row for row in raw_unbound if isinstance(row, dict)] \
            if isinstance(raw_unbound, list) else []
        _check(
            f"{dispatch}: removing the provider binding lets the forged row through",
            unbound.returncode == 0 and len(unbound_posted) == 1,
            "if the envelope-only predicate ALSO refuses this fixture, the refusal "
            "above is not evidence that the provider binding does anything: "
            f"rc={unbound.returncode} {unbound.stderr.strip()} {unbound_posted!r}",
        )

    # MUTATION CONTROL (5): THE AUTHENTIC REQUIRED CHECK *DOES* CARRY THE HEAD.
    # The A/B for the positive half. Both heads below carry one ordinary green
    # Check and one success named exactly `ops/ci.sh --strict`; they differ in
    # ONE provider fact -- which check-suite GitHub put that success in. The
    # other-workflow suite refuses in the table above; the ci.yml suite for this
    # head dispatches here. Without this pair, every refusal above is equally
    # consistent with a door that refuses every head it is shown.
    env, state_path, log_path = fixture({"check_runs": [
        _ci_check("concluded_clean"), _required_check(),
    ]})
    authentic = _invoke(env, dispatch, *_proof_args())
    raw_authentic = _read_json(state_path).get("dispatches")
    authentic_posted = [row for row in raw_authentic if isinstance(row, dict)] \
        if isinstance(raw_authentic, list) else []
    _check(
        f"{dispatch}: the provider-authenticated required success carries the head",
        authentic.returncode == 0 and len(authentic_posted) == 1,
        "if the authentic required check does not dispatch, the refusals above are "
        "not attributable to the suite binding: "
        f"rc={authentic.returncode} {authentic.stderr.strip()} {authentic_posted!r}",
    )

    # MUTATION CONTROL (6): WITHOUT THE PROVIDER BINDING, THE FOREIGN-WORKFLOW
    # SUCCESS GOES GREEN. The same fixture the table refuses at "a success from
    # another workflow file is not the required check", run against a tree whose
    # required-check predicate trusts the NAME alone -- the retired rule, moved
    # onto the positive half. It dispatches, which is what makes that refusal
    # attributable to the binding rather than to a door that refuses everything.
    name_only_root = _name_only_required_tree()
    foreign_success: dict[str, object] = {"check_runs": [
        _ci_check("concluded_clean"), _required_check(suite="other_workflow"),
    ]}
    if name_only_root is None:
        _check(
            f"{dispatch}: the required check's provider binding is present to be mutated",
            False,
            "the control could not find the required-check authentication verbatim, so "
            "it proves nothing about the refusal above",
        )
    else:
        env, state_path, log_path = fixture(foreign_success)
        name_only = subprocess.run(
            [sys.executable, str(name_only_root / "ops" / _STATUS_HELPER.name),
             dispatch, *_proof_args()],
            cwd=name_only_root, env=env, text=True, capture_output=True,
            timeout=8, check=False,
        )
        raw_name_only = _read_json(state_path).get("dispatches")
        name_only_posted = [row for row in raw_name_only if isinstance(row, dict)] \
            if isinstance(raw_name_only, list) else []
        _check(
            f"{dispatch}: trusting the required check's name alone lets a foreign "
            "workflow carry the head",
            name_only.returncode == 0 and len(name_only_posted) == 1,
            "if the name-only predicate ALSO refuses this fixture, the refusal above is "
            "not evidence that the suite binding does anything: "
            f"rc={name_only.returncode} {name_only.stderr.strip()} {name_only_posted!r}",
        )

    # THE ONE REFUSAL THE SOURCE SEARCHES ABOVE CANNOT PROVE: the BUDGET GATE
    # itself saying no. Run the door's own bytes from a tree whose sealed policy
    # re-imposes the Actions pause, and watch it reach no vendor at all.
    paused_root = _paused_policy_tree()
    env, state_path, log_path = fixture()
    refused = subprocess.run(
        [sys.executable, str(paused_root / "ops" / _STATUS_HELPER.name), dispatch, *_proof_args()],
        cwd=paused_root, env=env, text=True, capture_output=True, timeout=8, check=False,
    )
    posted = [row for row in _calls(log_path) if str(row.get("url", "")).endswith("/dispatches")]
    _check(
        f"{dispatch}: a refusing budget gate stops the dispatch before the vendor",
        refused.returncode != 0
        and "metered execution refused" in refused.stderr
        and not posted
        and not (_read_json(state_path).get("dispatches") or []),
        f"expected the metering refusal to reach no vendor, got rc={refused.returncode} "
        f"posted={posted!r} stderr={refused.stderr.strip()}",
    )


def _module_level_names(text: str) -> set[str]:
    """Every module-level name a source binds, read with a real parser.

    ast, not a regex: the standing authority rule requires a real parser for a
    static surface guard, and a regex over ``def`` lines cannot tell a
    module-level function from a nested one or see an annotated assignment.
    Import statements are deliberately not counted -- an imported name is
    upstream vocabulary, and question 2 of the sweep covers what this suite
    re-exports at runtime instead.
    """
    bound: set[str] = set()
    for node in ast.parse(text).body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            bound.add(node.name)
        elif isinstance(node, ast.Assign):
            bound.update(t.id for t in node.targets if isinstance(t, ast.Name))
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            bound.add(node.target.id)
    return bound


def _repo_reexports(module: ModuleType, home: Path) -> set[str]:
    """Public names on a module that came from ANOTHER module in this repository.

    THE QUESTION THIS ANSWERS is the one the standing authority rule asks of any
    changed module: can a caller reach a privileged decision function through
    this file's public surface? An `from lib.x import decide` at module level
    binds ``decide`` as a public attribute here, so importing this file re-exports
    the gate -- under this file's name, which the rule says is still a name.

    THE ORDERED QUESTIONS, per attribute:
      1. Is it public, and not a module object? Underscore names are not exports
         and `import json` binds a module, not a decision.
      2. Was it DEFINED in this file? A function whose code object lives here is
         this module's own surface, judged by the checks around this one.
      3. Otherwise, does its ``__module__`` resolve to a .py file inside this
         repository? Then it is one of ours, reached through a file that is not
         its own -- which is the re-export.

    Standard-library and third-party names are deliberately not counted: they
    are upstream vocabulary, not this repository's decisions.
    """
    found: set[str] = set()
    resolved = str(home.resolve())
    for name, value in vars(module).items():
        if name.startswith("_") or isinstance(value, ModuleType):
            continue
        code = getattr(value, "__code__", None)
        if code is not None and str(Path(code.co_filename).resolve()) == resolved:
            continue
        origin = str(getattr(value, "__module__", "") or "")
        if origin and (_ROOT / (origin.replace(".", "/") + ".py")).is_file():
            found.add(name)
    return found


def _public_repo_imports(text: str) -> set[str]:
    """Public names a source binds by importing them FROM this repository's modules.

    The static half of the same question, and it needs a real parser rather than
    a regex: the reachable shape is `from lib.platform_metering import
    authorize_metered_execution`, and its spellings (parenthesised lists,
    aliases, multiple names per statement) are exactly what a regex gets wrong.
    ``from __future__`` is skipped, and so is any module that does not resolve to
    a file in this repository -- stdlib vocabulary is not a re-export.
    """
    bound: set[str] = set()
    for node in ast.parse(text).body:
        if not isinstance(node, ast.ImportFrom) or not node.module:
            continue
        if node.module == "__future__" or node.level:
            continue
        if not (_ROOT / (node.module.replace(".", "/") + ".py")).is_file():
            continue
        bound.update(alias.asname or alias.name for alias in node.names)
    return {name for name in bound if not name.startswith("_")}


def _load_module(path: Path, name: str) -> ModuleType:
    """Import a file by path, including one whose name is not an identifier.

    Registered in ``sys.modules`` under the probe name BEFORE it executes,
    because @dataclass resolves its own class's module through that table while
    the module body is still running. The name is a probe-only one, so nothing
    else in the process resolves to it.
    """
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise ImportError(f"cannot load {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    try:
        spec.loader.exec_module(module)
    except BaseException:
        sys.modules.pop(name, None)
        raise
    return module


def _reexport_probe_module() -> tuple[ModuleType, str]:
    """A module that DOES re-export the metering authorizer, for the control.

    This is the exact line the re-review found on the status helper, alone in a
    file of its own: the mutation the guard above has to catch. The caller
    proves two things with it -- that the sweep finds the name, and that the name
    it finds really does hand a caller the privileged outcome from the caller's
    own policy and request.
    """
    source = ("import sys\n"
              f"sys.path.insert(0, {str(_ROOT)!r})\n"
              "from lib.platform_metering import authorize_metered_execution\n")
    root = Path(tempfile.mkdtemp(prefix="carr-reexport-probe-"))
    probe = root / "reexport_probe.py"
    probe.write_text(source, encoding="utf-8")
    return _load_module(probe, "carr_reexport_probe"), source


def _public_surface_contract() -> None:
    """The public surface of BOTH modules this slice changed, swept.

    WHY THE SURFACE AND NOT THE FIXTURES. The standing authority rule forbids an
    EXPORTED function that returns a privileged outcome built from caller input.
    The fixtures in this file legitimately carry GitHub's own vocabulary --
    "completed", "success" -- which is upstream text the suite reports rather
    than authority it mints. The closeable property is therefore the surface: if
    the only public name this file defines is main(), which returns a process
    exit code, then no caller-input-to-privileged-outcome path is exported at
    all, under ANY name. A previous revision exported green_ci_check(name=...,
    status=...) and privileged_hits(), which is the shape this sweep catches.

    AND WHY IT NOW SWEEPS THE STATUS MODULE TOO. An earlier revision of this
    function proved that property about ITSELF while the module it exercises
    grew a public `from lib.platform_metering import authorize_metered_execution`
    -- the exact re-export named in the docstring below it. A read-only probe
    imported ops/backup-workflow-status.py, reached the gate through it, and got
    the privileged outcome back from its own objects. Proving a rule about the
    test file while the changed file breaks it is worse than not proving it: it
    reads as covered.

    THE ORDERED QUESTIONS:
      1. Statically, what module-level names does THIS file bind, and is the
         public subset exactly {"main"} with an int return?
      2. At runtime, does this module expose anything of its own -- or anything
         re-exported from this repository's modules -- beyond main()?
      3. Does the closed union of privileged words appear anywhere in that
         public surface?
      4. Does the STATUS module bind any of this repository's names publicly,
         statically (a real parser, not a regex) or at runtime -- and does it
         still reach the gate privately, rather than not at all?
      5. MUTATION CONTROLS: re-adding the exact re-export must turn both status
         sweeps red, and the name it re-exports must really yield the privileged
         outcome; and question 1's reader must see the helper shape the earlier
         review flagged, when it is given one.
    """
    source = Path(__file__).read_text(encoding="utf-8")
    public = {name for name in _module_level_names(source) if not name.startswith("_")}
    _check(
        "the selftest binds exactly one public module-level name, main()",
        public == {"main"},
        "a public test helper is an export: it can be imported and called with "
        f"caller input, whatever its docstring says. Public: {sorted(public)}",
    )
    returns = [
        node.returns for node in ast.parse(source).body
        if isinstance(node, ast.FunctionDef) and node.name == "main"
    ]
    _check(
        "the one public name returns a process exit code, not a classification",
        len(returns) == 1
        and isinstance(returns[0], ast.Name) and returns[0].id == "int",
        "an exported callable that can return a verdict object is the privileged "
        "outcome the standing rule closes, whatever it is named",
    )

    module = sys.modules[__name__]
    this_file = str(Path(__file__).resolve())
    own = {
        name for name, value in vars(module).items()
        if not name.startswith("_") and not isinstance(value, ModuleType)
        and getattr(value, "__code__", None) is not None
        and str(Path(value.__code__.co_filename).resolve()) == this_file
    }
    reexported = _repo_reexports(module, Path(__file__))
    _check(
        "at runtime the suite exposes only main() from its own body",
        own == {"main"},
        f"a name bound some other way is still an export: {sorted(own)}",
    )
    _check(
        "the suite re-exports nothing from this repository's own modules",
        not reexported,
        "importing authorize_metered_execution under a public name would re-export "
        f"the gate's verdict through this file: {sorted(reexported)}",
    )
    _check(
        "no privileged word appears in the suite's public surface",
        not _privileged_hits(sorted(public)),
        f"the closed union reached the exported surface: "
        f"{_privileged_hits(sorted(public))}",
    )


    # ── THE CHANGED MODULE'S OWN SURFACE ───────────────────────────────────
    # The re-review found this suite proving a property about ITSELF while the
    # module it exercises grew the very re-export this function's docstring
    # names. A read-only probe imported ops/backup-workflow-status.py, reached
    # authorize_metered_execution through it, and got {"admitted": true} back
    # from its own policy and request objects. The sweep now walks the STATUS
    # module too, statically and at runtime, and the control below re-adds the
    # exact line and watches it turn red.
    status_source = _STATUS_HELPER.read_text(encoding="utf-8")
    status_imports = _public_repo_imports(status_source)
    _check(
        "the status module imports nothing of this repository's under a public name",
        not status_imports,
        "a module-level `from lib.x import decide` binds decide as a public attribute "
        "of the status module, so importing that file re-exports the decision: "
        f"{sorted(status_imports)}",
    )
    status_module = _load_module(_STATUS_HELPER, "carr_backup_workflow_status_probe")
    status_reexports = _repo_reexports(status_module, _STATUS_HELPER)
    _check(
        "at runtime the status module re-exports nothing of this repository's",
        not status_reexports,
        "the metering gate must not be reachable through the door's own file under any "
        f"name: {sorted(status_reexports)}",
    )
    _check(
        "the status module still admits its own spend through a private name",
        getattr(status_module, "_authorize_metered_execution", None) is not None
        and getattr(status_module, "_MeteringRefusal", None) is not None
        and not hasattr(status_module, "authorize_metered_execution")
        and not hasattr(status_module, "MeteringRefusal"),
        "the fix is a PRIVATE binding, not a deleted admission: a door that stopped "
        "calling the gate would also pass the two checks above",
    )

    # THE MUTATION CONTROL FOR BOTH SWEEPS. A module carrying exactly the line
    # the re-review found, and nothing else. It has to (a) be caught by the
    # static reader, (b) be caught by the runtime sweep, and (c) actually hand a
    # caller the privileged outcome -- because a control that flags a harmless
    # name proves nothing about what the guard is for.
    probe_module, probe_source = _reexport_probe_module()
    probe_public = _public_repo_imports(probe_source)
    probe_runtime = _repo_reexports(probe_module, Path(probe_module.__file__ or ""))
    privileged_outcome = getattr(probe_module, "authorize_metered_execution")(
        json.loads(_METERING_POLICY.read_text(encoding="utf-8")),
        "github-actions-remote-ci",
        {"candidate_sha": _PROOF_HEAD, "local_checks_green": True},
    )
    _check(
        "re-adding the re-export turns both sweeps red",
        probe_public == {"authorize_metered_execution"}
        and probe_runtime == {"authorize_metered_execution"},
        "a sweep that cannot find the exact line the re-review found is measuring "
        f"nothing: static {sorted(probe_public)}, runtime {sorted(probe_runtime)}",
    )
    _check(
        "the re-export the sweeps catch is the privileged outcome, not a harmless name",
        isinstance(privileged_outcome, dict)
        and privileged_outcome.get("admitted") is True,
        "if reaching the gate through a public re-export did NOT yield the privileged "
        f"outcome, the two sweeps above would be guarding nothing: {privileged_outcome!r}",
    )

    flagged = _module_level_names(
        "import json\n"
        "PRIVILEGED_WORDS = ('pass',)\n"
        "def green_ci_check(name='gates', status='completed'):\n"
        "    return {'name': name, 'status': status}\n"
        "def _private():\n"
        "    def green_ci_check():\n"
        "        return {}\n"
    )
    _check(
        "the surface reader sees the exact shape the re-review flagged",
        {"green_ci_check", "PRIVILEGED_WORDS", "_private"} == flagged
        and bool(_privileged_hits(
            [n for n in sorted(flagged) if not n.startswith("_")])),
        "a reader that cannot find a public green_ci_check in a source that "
        f"defines one is measuring nothing: saw {sorted(flagged)}",
    )


def main() -> int:
    source = _WORKFLOW.read_text(encoding="utf-8")
    status_source = _STATUS_HELPER.read_text(encoding="utf-8") if _STATUS_HELPER.is_file() else ""
    backup_match = re.search(
        r"(?ms)^  backup:\s*\n(?P<body>.*?)(?=^  [A-Za-z0-9_-]+:\s*\n|\Z)",
        source,
    )
    backup_job = backup_match.group("body") if backup_match else ""

    _check(
        "workflow grants only the additional provider permissions its status contract needs",
        bool(re.search(r"(?m)^    permissions:\s*$", backup_job))
        and bool(re.search(r"(?m)^      checks:\s*write\s*$", backup_job))
        and bool(re.search(r"(?m)^      actions:\s*write\s*$", backup_job))
        and bool(re.search(r"(?m)^      contents:\s*read\s*$", backup_job))
        and not bool(re.search(r"(?m)^  (?:checks|actions):\s*write\s*$", source)),
        "expected job-scoped checks:write/actions:write with contents:read",
    )
    _check(
        "producer writes the named Backup artifact Check",
        "backup-workflow-status.py" in source and "Backup artifact" in status_source,
        "workflow must call the narrow status adapter and name the Check",
    )
    _check(
        "Check identity binds repository, run, attempt and head",
        all(token in status_source for token in (
            "GITHUB_REPOSITORY", "GITHUB_RUN_ID", "GITHUB_RUN_ATTEMPT", "GITHUB_SHA",
        ))
        and "external_id" in status_source,
        "a green Check without all four immutable producer locators is ambiguous",
    )
    _check(
        "successful status carries exact artifact evidence",
        all(token in status_source for token in (
            "artifact_id", "artifact_name", "artifact_digest", "artifact_bytes",
            "artifact_created_at", "artifact_expires_at",
        )),
        "run success alone is not a backup receipt",
    )
    _check(
        "disabled scheduled path records neutral before cancelling only itself",
        "neutral" in status_source.lower()
        and "GITHUB_RUN_ID" in status_source
        and bool(re.search(r"actions/runs/[^\n]*GITHUB_RUN_ID[^\n]*/cancel", status_source))
        and "gh run list" not in status_source,
        "disabled flow must not search for or cancel another run",
    )
    _check(
        "disabled cancellation has no success continuation",
        bool(re.search(
            r"(?s)neutral.{0,4000}(?:while\s+true|while\s+True|sleep\s*\(.{0,100}(?:\n|$))",
            status_source,
            re.I,
        )),
        "after requesting cancellation the producing job must wait until killed",
    )
    _check(
        "producer workflow does not recursively observe its own workflow_run",
        not bool(re.search(r"(?m)^\s*workflow_run:\s*$", source)),
        "post-terminal verification is a separate consumer invocation, not a recursive workflow trigger",
    )
    _check(
        "status adapter exposes separate post-terminal cancelled/zero-artifact observation",
        all(token in status_source.lower() for token in ("observer", "cancelled", "artifact")),
        "the terminating producer cannot certify its own terminal provider state",
    )
    _check(
        "disabled neutral is excluded from backup-failure alerting",
        "Backup artifact" in status_source
        and "backup-failure" in status_source
        and bool(re.search(r"neutral|cancelled", status_source, re.I))
        and bool(re.search(r"conclusion[^\n]{0,100}(?:failure|failed)", status_source, re.I)),
        "adapter must consume named Check failure and explicitly exclude neutral/cancel infrastructure states; overall red is insufficient",
    )
    _check(
        "artifact name binds producer run and attempt",
        "GITHUB_RUN_ID" in source
        and "GITHUB_RUN_ATTEMPT" in source
        and bool(re.search(r"artifact[^\n]{0,200}(?:run|RUN_ID)[^\n]{0,200}(?:attempt|RUN_ATTEMPT)", source, re.I)),
        "reruns must not overwrite or inherit another attempt's artifact identity",
    )
    _workflow_mode_contract(source, backup_job)
    _service_identity_contract()
    _behavioral_contract()
    _controlled_failure_contract()
    _dispatch_door_contract()
    _public_surface_contract()

    print(f"\nbackup-workflow-selftest: {_passed}/{_passed + len(_failed)} passed")
    if _failed:
        print("missing contract: " + ", ".join(_failed), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
