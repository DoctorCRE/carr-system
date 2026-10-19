"""Reconcile scheduler registrations without changing provider-owned state."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import re
from typing import Any

from lib.claude_scheduler_native import discover_snapshot
from lib.control_plane_scheduler_cutover import CutoverRefusal
from lib.headless_tasks import install_main as headless_install


def read_json(path: Path) -> Any:
    def unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        result = {}
        for key, value in pairs:
            if key in result:
                raise CutoverRefusal('scheduler inventory has duplicate object keys')
            result[key] = value
        return result
    return json.loads(path.read_text(), object_pairs_hook=unique_object)


def native_tasks(home: Path) -> dict[str, dict[str, Any]]:
    try:
        document = read_json(discover_snapshot(home))
        rows = document['scheduledTasks']
        if not isinstance(rows, list):
            raise ValueError('task list')
        tasks = {}
        for row in rows:
            if not isinstance(row, dict) or not isinstance(row.get('id'), str) or not row['id']:
                raise ValueError('task identity')
            if row['id'] in tasks or type(row.get('enabled')) is not bool:
                raise ValueError('ambiguous identity or state')
            tasks[row['id']] = row
        return tasks
    except (OSError, ValueError, KeyError, TypeError) as exc:
        raise CutoverRefusal('native scheduler inventory is unreadable or ambiguous') from exc


def workflows(repo: Path) -> dict[str, dict[str, Any]]:
    document = read_json(repo / 'ops/config/control-plane-workflows.v1.json')
    rows = document['workflows']
    if not isinstance(rows, list):
        raise CutoverRefusal('workflow inventory is unavailable')
    result = {}
    for row in rows:
        key = row['key']
        if key in result:
            raise CutoverRefusal('workflow identity is ambiguous')
        result[key] = row
    return result


def install_refusal(task_id: str, settings: dict, registered: dict, native: dict) -> str | None:
    workflow = registered.get(task_id)
    if not workflow or workflow.get('enabled') is not True or workflow.get('legacy_schedule', {}).get('status') == 'disabled':
        return 'workflow is paused or unregistered'
    recurrence = workflow.get('recurrence', {})
    cron = settings['tasks'][task_id]['cron']
    if recurrence.get('cron') != cron or recurrence.get('timezone') != settings['timezone']:
        return 'headless recurrence differs from the reviewed workflow'
    row = native.get(task_id)
    if row:
        if row.get('cronExpression') != cron:
            return 'native recurrence differs from the reviewed workflow'
        if row['enabled']:
            return 'native registration is still enabled'
    return None


def install_main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description='Install headless jobs only after native registration retirement.')
    parser.add_argument('action', choices=['install', 'uninstall'])
    parser.add_argument('task_ids', nargs='*')
    args = parser.parse_args(argv)
    if args.action == 'install':
        home = Path.home()
        repo = home / 'carr-system'
        try:
            settings = read_json(repo / 'ops/headless-tasks/tasks.json')
            selected = args.task_ids or sorted(settings['tasks'])
            if set(selected) - set(settings['tasks']):
                raise CutoverRefusal('unknown task id')
            registered, native = workflows(repo), native_tasks(home)
            for task_id in selected:
                reason = install_refusal(task_id, settings, registered, native)
                if reason:
                    raise CutoverRefusal(f'{task_id}: {reason}')
        except (OSError, ValueError, KeyError, TypeError) as exc:
            parser.error(str(exc))
    return headless_install([args.action, *args.task_ids])


def ephemeral_one_shot(home: Path, task_id: str, row: dict) -> bool:
    if ('cronExpression' not in row or row['cronExpression'] is not None or row['enabled']
            or not row.get('lastRunAt') or row.get('lastRunStatus') != 'succeeded'):
        return False
    if Path(task_id).name != task_id or task_id in {'.', '..'}:
        return False
    try:
        lines = (home / '.claude/scheduled-tasks' / task_id / 'SKILL.md').read_text().splitlines()
    except OSError:
        return False
    if not lines or lines[0].strip() != '---':
        return False
    try:
        end = lines.index('---', 1)
    except ValueError:
        return False
    metadata = {}
    for line in lines[1:end]:
        if not line.strip():
            continue
        field = re.fullmatch(r'([a-z][a-z0-9_-]*):[ \t]+([A-Za-z0-9].*)', line)
        if not field or field[1] in metadata or re.search(r':[ \t]', field[2]):
            return False
        metadata[field[1]] = field[2].rstrip()
    return metadata.get('ephemeral') == 'true'


def report(repo: Path, home: Path) -> dict:
    settings = read_json(repo / 'ops/headless-tasks/tasks.json')
    registered, native = workflows(repo), native_tasks(home)
    rows = []
    installed_ids = {path.name.removeprefix('com.carr.headless.').removesuffix('.plist')
                     for path in (home / 'Library/LaunchAgents').glob('com.carr.headless.*.plist')
                     if path.is_file()}
    native_ids = {key for key, workflow in registered.items()
                  if 'claude-code' in workflow.get('legacy_schedule', {}).get('provider', '')}
    for task_id in sorted(native_ids | installed_ids | set(settings['tasks'])):
        workflow = registered.get(task_id)
        row = native.get(task_id)
        installed = task_id in installed_ids
        if not workflow:
            action = 'orphan_headless_registration' if installed else 'unregistered_headless_task'
        elif workflow.get('enabled') is not True or workflow.get('legacy_schedule', {}).get('status') == 'disabled':
            action = 'pause_violation' if installed or (row and row['enabled']) else 'keep_paused'
        elif installed and row and row['enabled']:
            action = 'duplicate_registration'
        elif installed and task_id not in settings['tasks']:
            action = 'unexpected_headless_registration'
        elif task_id in settings['tasks']:
            refusal = install_refusal(task_id, settings, registered, native)
            if refusal:
                action = 'retire_native' if refusal == 'native registration is still enabled' else 'reconcile_recurrence'
            else:
                action = 'headless_registered' if installed else 'ready_for_explicit_cutover'
        else:
            action = 'retain_native' if row else 'missing_native_registration'
        rows.append({'task_id': task_id, 'action': action,
                     'native_registered': row is not None,
                     'native_enabled': row['enabled'] if row else None,
                     'headless_plist_present': installed})
    for task_id, row in sorted(native.items()):
        if task_id not in registered and ephemeral_one_shot(home, task_id, row):
            rows.append({'task_id': task_id, 'action': 'delete_completed_one_shot'})
    return {'schema': 'carr-scheduler-hygiene/v1', 'read_only': True,
            'launchd_loaded_state': 'unobserved', 'tasks': rows}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description='Report registration drift; never activate or delete tasks.')
    parser.add_argument('--repo', type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument('--home', type=Path, default=Path.home())
    args = parser.parse_args(argv)
    try:
        result = report(args.repo, args.home)
    except (OSError, ValueError, KeyError, TypeError) as exc:
        print(json.dumps({'read_only': True, 'error': str(exc)}))
        return 2
    print(json.dumps(result, sort_keys=True))
    return int(any(row['action'] in {'duplicate_registration', 'pause_violation', 'reconcile_recurrence',
                                     'missing_native_registration', 'orphan_headless_registration',
                                     'unexpected_headless_registration', 'unregistered_headless_task'}
                   for row in result['tasks']))
