#!/usr/bin/env python3
# Run nightly deal engine jobs: workflow suggestions, attention alerts, and checklist reminders.
from __future__ import annotations

import argparse
from dataclasses import dataclass
from datetime import datetime
from http.client import HTTPException
import json
from pathlib import Path
import sys
from typing import Callable
import uuid
from zoneinfo import ZoneInfo

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from lib import engine_mail, outlook_settings


@dataclass(frozen=True)
class NightlyDealJob:
    job: str
    path: str
    counts: tuple[str, ...]
    label: str
    load_settings: Callable[[], outlook_settings.OutlookSettings | None]
    request_body: Callable[[str], dict[str, str]]


def idempotency_key(job: str, run_date: str) -> str:
    return str(uuid.uuid5(uuid.NAMESPACE_URL, f'engine|{job}|{run_date}'))


def uuid5_body(job: str) -> Callable[[str], dict[str, str]]:
    def build(run_date: str) -> dict[str, str]:
        return {'idempotency_key': idempotency_key(job, run_date)}
    return build


def checklist_body(run_date: str) -> dict[str, str]:
    return {'run_date': run_date, 'idempotency_key': str(uuid.uuid4())}


JOBS: dict[str, NightlyDealJob] = {
    'workflow-suggestions': NightlyDealJob(
        job='workflow_suggestions',
        path='/jobs/deals/workflow-suggestions',
        counts=('proposed', 'already_proposed'),
        label='workflow suggestions',
        load_settings=outlook_settings.load_workflow_suggestions_settings,
        request_body=uuid5_body('workflow_suggestions'),
    ),
    'attention-alerts': NightlyDealJob(
        job='attention_alerts',
        path='/jobs/deals/attention-alerts',
        counts=('alerted', 'unchanged', 'cleared'),
        label='attention alerts',
        load_settings=outlook_settings.load_attention_alerts_settings,
        request_body=uuid5_body('attention_alerts'),
    ),
    'checklist-reminders': NightlyDealJob(
        job='nightly_checklist',
        path='/jobs/deals/checklist-reminders',
        counts=('queued', 'already_queued'),
        label='checklist reminders',
        load_settings=outlook_settings.load_checklist_reminder_settings,
        request_body=checklist_body,
    ),
}


def run_nightly(spec: NightlyDealJob, *, check_only: bool) -> int:
    try:
        settings = spec.load_settings()
        if settings is None:
            return 78
        if check_only:
            return 0
        assert settings.engine_credentials is not None
        door = engine_mail.connect(settings.engine_credentials, job=spec.job)
        run_date = datetime.now(ZoneInfo('America/Chicago')).date().isoformat()
        body = spec.request_body(run_date)
        reply = door('POST', spec.path, json.dumps(body).encode())
    except (OSError, ValueError, HTTPException):
        if check_only:
            return 0  # Let the scheduled step log invalid settings for an enabled switch.
        print(f'{spec.label}: engine request failed', file=sys.stderr)
        return 1
    if reply.status != 200:
        print(f'{spec.label}: engine request failed (status {reply.status})', file=sys.stderr)
        return 1
    counts = {key: reply.body.get(key) for key in spec.counts}
    if any(type(count) is not int or count < 0 for count in counts.values()):
        print(f'{spec.label}: invalid engine counts', file=sys.stderr)
        return 1
    print(json.dumps(counts))
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description='Run nightly deal jobs through the engine.')
    parser.add_argument('job', choices=tuple(JOBS))
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument('--nightly', action='store_true', help='Run the engine sweep when enabled.')
    mode.add_argument('--check-nightly-switch', action='store_true', help='Check whether to schedule the engine sweep.')
    args = parser.parse_args(argv)
    return run_nightly(JOBS[args.job], check_only=args.check_nightly_switch)


if __name__ == '__main__':
    raise SystemExit(main())
