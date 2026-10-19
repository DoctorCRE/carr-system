"""Collect local scheduler measurements without granting canonical receipt authority."""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import plistlib
import re
import subprocess
import sys
from uuid import UUID

from lib.claude_scheduler_native import discover_snapshot, read_native_task, render_task_definition
from lib.control_plane_scheduler_cutover import CutoverRefusal
from lib.headless_tasks import calendar_entries
from lib.launchd_scheduler_native import Runner, read_native_launchd
from lib.scheduler_hygiene import native_tasks, read_json, workflows


def _digest(value: object) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=True).encode()).hexdigest()


def _run(command: list[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(command, capture_output=True, text=True, check=False, timeout=30)


def _revision(repo: Path, runner: Runner) -> str:
    revision = runner(['git', '-C', str(repo), 'rev-parse', 'HEAD'])
    status = runner(['git', '-C', str(repo), 'status', '--porcelain', '--untracked-files=no'])
    if (revision.returncode or status.returncode or status.stdout.strip()
            or re.fullmatch(r'[0-9a-f]{40}(?:[0-9a-f]{24})?', revision.stdout.strip()) is None):
        raise CutoverRefusal('scheduler source revision is unavailable or has tracked changes')
    return revision.stdout.strip()


def _device(runner: Runner, uid: int) -> dict:
    result = runner(['/usr/sbin/ioreg', '-a', '-d', '2', '-c', 'IOPlatformExpertDevice'])
    if result.returncode:
        raise CutoverRefusal('device hardware identity is unavailable')
    try:
        rows = plistlib.loads(result.stdout.encode())
        values = [row['IOPlatformUUID'] for row in rows if isinstance(row, dict) and 'IOPlatformUUID' in row]
        if len(values) != 1:
            raise ValueError('ambiguous hardware identity')
        hardware = UUID(values[0])
        if not hardware.int:
            raise ValueError('nil hardware identity')
        identity = hardware.hex
    except (ValueError, TypeError, AttributeError, plistlib.InvalidFileException) as exc:
        raise CutoverRefusal('device hardware identity is malformed or ambiguous') from exc
    return {'hardware_fingerprint': hashlib.sha256(identity.encode()).hexdigest(), 'uid': uid,
            'binding': 'local_hardware_observation'}


def _require_direct_path(path: Path, home: Path) -> None:
    for ancestor in (path, *path.parents):
        if ancestor == home:
            return
        if ancestor.is_symlink():
            raise CutoverRefusal('observation path must not traverse a symlink')
    raise CutoverRefusal('observation path is outside the device home')


def collect_receipt(task_id: str, *, repo: Path, home: Path, runner: Runner = _run,
                    observed_at: datetime | None = None, host_timezone: str | None = None,
                    uid: int | None = None) -> dict:
    """Measure a reviewed headless task and its retained native registration."""
    if re.fullmatch(r'[a-z][a-z0-9-]*', task_id) is None:
        raise CutoverRefusal('task identity is invalid')
    settings = read_json(repo / 'ops/headless-tasks/tasks.json')
    registered = workflows(repo)
    workflow = registered.get(task_id)
    if task_id not in settings['tasks'] or not workflow:
        raise CutoverRefusal('task has no reviewed headless workflow')
    version = workflow.get('version')
    if type(version) is not int or version < 1 or type(workflow.get('enabled')) is not bool:
        raise CutoverRefusal('workflow version or enabled state is invalid')
    cron, zone = settings['tasks'][task_id]['cron'], settings['timezone']
    if workflow.get('recurrence') is None or (workflow['recurrence'].get('cron'), workflow['recurrence'].get('timezone')) != (cron, zone):
        raise CutoverRefusal('headless recurrence differs from the workflow version')
    runtime_repo = home / 'carr-system'
    revision = _revision(repo, runner)
    if runtime_repo != repo and _revision(runtime_repo, runner) != revision:
        raise CutoverRefusal('installed runtime revision differs from the observed source')
    instant = observed_at or datetime.now(timezone.utc)
    if instant.tzinfo is None or instant.utcoffset() is None:
        raise CutoverRefusal('observation clock must have a timezone')
    instant = instant.astimezone(timezone.utc)
    device = _device(runner, os.getuid() if uid is None else uid)
    snapshot = discover_snapshot(home)
    _require_direct_path(snapshot, home)
    prompt = home / '.claude/scheduled-tasks' / task_id / 'SKILL.md'
    _require_direct_path(prompt, home)
    snapshot_digest = hashlib.sha256(snapshot.read_bytes()).hexdigest()
    native = native_tasks(home)
    portable = repo / 'ops/scheduled-tasks' / f'{task_id}.SKILL.md'
    definition = hashlib.sha256(portable.read_bytes()).hexdigest()
    if task_id in native:
        native_read = read_native_task(
            home=home, repo=runtime_repo, locator=task_id, expected_cron=cron, expected_timezone=zone,
            portable_definition_sha256=definition, observed_at=instant, host_timezone=host_timezone)
        if native_read['provider_revision'] != snapshot_digest:
            raise CutoverRefusal('native scheduler changed during observation')
        native_read['state'] = 'enabled' if native_read['enabled'] else 'disabled'
    else:
        expected_prompt = render_task_definition(portable.read_bytes(), repo=runtime_repo, home=home)
        if prompt.read_text() != expected_prompt:
            raise CutoverRefusal('retained headless prompt differs from the source definition')
        native_read = {'state': 'absent', 'enabled': None, 'provider_revision': snapshot_digest,
                       'definition_sha256': definition}
    label = 'com.carr.headless.' + task_id
    relpath = f'ops/headless-tasks/{label}.plist'
    plist = plistlib.loads((repo / relpath).read_bytes())
    if (plist.get('StartCalendarInterval') != calendar_entries(cron)
            or plist.get('ProgramArguments') != ['{{REPO}}/bin/headless-task', task_id]
            or plist.get('WorkingDirectory') != '{{REPO}}'):
        raise CutoverRefusal('headless plist differs from the reviewed workflow')
    schedule = {key: plist[key] for key in ('StartCalendarInterval', 'StartInterval', 'StartOnMount', 'KeepAlive') if key in plist}
    headless = read_native_launchd(
        home=home, repo=repo, locator=label, repo_plist_relpath=relpath,
        installed_plist_name=label + '.plist', expected_program_arguments=plist['ProgramArguments'],
        plist_sha256=_digest(plist), schedule_sha256=_digest(schedule), expected_timezone=zone,
        runner=runner, observed_at=instant, host_timezone=host_timezone, uid=device['uid'])
    headless['definition_binding'] = 'installed_files_only'
    if discover_snapshot(home) != snapshot or hashlib.sha256(snapshot.read_bytes()).hexdigest() != snapshot_digest:
        raise CutoverRefusal('native scheduler changed during observation')
    if (_revision(repo, runner) != revision
            or (runtime_repo != repo and _revision(runtime_repo, runner) != revision)):
        raise CutoverRefusal('scheduler source revision changed during observation')
    native_enabled = native_read['enabled'] is True
    state = {(False, True): 'headless_only', (True, False): 'native_only',
             (True, True): 'duplicate', (False, False): 'inactive'}[(native_enabled, headless['enabled'])]
    if not workflow['enabled'] and (native_enabled or headless['enabled']):
        state = 'pause_violation'
    receipt = {
        'schema': 'carr-scheduler-observation-receipt/v1',
        'kind': 'local_scheduler_observation_receipt', 'canonical_receipt_ref': None,
        'observed_at': instant.isoformat().replace('+00:00', 'Z'), 'device': device,
        'workflow': {'key': task_id, 'version': version, 'enabled': workflow['enabled'],
                     'source_revision': revision, 'definition_sha256': definition,
                     'manifest_sha256': hashlib.sha256((repo / 'ops/headless-tasks/tasks.json').read_bytes()).hexdigest()},
        'native': native_read, 'headless': headless, 'cutover_state': state,
    }
    receipt['receipt_ref'] = 'local-scheduler-observation:sha256:' + _digest(receipt)
    return receipt


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description='Read local scheduler observations; no activation or canonical receipt writes.')
    parser.add_argument('task_id')
    args = parser.parse_args(argv)
    try:
        receipt = collect_receipt(args.task_id, repo=Path(__file__).resolve().parents[1], home=Path.home())
    except (OSError, ValueError, TypeError, KeyError, subprocess.SubprocessError):
        print('FAIL scheduler-observation-receipts: observation unavailable', file=sys.stderr)
        return 2
    print(json.dumps(receipt, sort_keys=True))
    return 0
