"""Local checkout retirement behind storage hygiene's scheduled entry point."""
from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path
import time
import uuid

from branch_retirement import GitHub, git, command, process_paths, save, fetch_remote_refs, remote_preserves
from job_watchdog import epoch
from lib.cleanup_budget import BudgetExhausted, bounded, check, entries


def clean_clone(workspace, dispatch, *, provider, process_probe, now, older_than, dry_run, deadline=None):
    workspace = Path(workspace)
    clone = workspace / 'checkout'
    result = {'kind': 'job_clone', 'path': str(workspace), 'status': 'kept'}
    receipt = None

    def keep(reason):
        return {**result, 'reason': reason}

    try:
        check(deadline)
        if workspace.resolve() != workspace or clone.resolve() != clone or not (clone / '.git').is_dir() or (clone / '.git').is_symlink():
            return keep('symlink or unrecognized standalone checkout')
        if not dispatch or dispatch.get('status') != 'completed' or dispatch.get('termination_confirmed') is False:
            return keep('finished dispatch not proven')
        if (dispatch.get('checkout_path') != str(clone) or
                dispatch.get('checkout_workspace') != str(workspace)):
            return keep('dispatch checkout identity mismatch')
        identity = workspace.stat().st_ino, clone.stat().st_ino
        repo = 'jbookout/carr-system'
        try:
            provider.verify_repository(repo, clone)
        except RuntimeError:
            return keep('repository identity unavailable or outside authorized cleanup scope')
        check(deadline)
        fetch_remote_refs(clone)
        check(deadline)
        head = git(clone, 'rev-parse', 'HEAD')
        branch = git(clone, 'symbolic-ref', '--quiet', '--short', 'HEAD', allowed=(0, 1))
        if git(clone, 'status', '--porcelain=v1', '--untracked-files=all'):
            return keep('uncommitted work')
        if not remote_preserves(clone):
            return keep('unpushed work')
        check(deadline)
        busy = process_probe([str(workspace)])
        if busy is None or busy:
            return keep('live process or process evidence unavailable')
        evidence_branch = branch or dispatch.get('checkout_branch')
        pulls = provider.branch_pulls(repo, evidence_branch) if evidence_branch else []
        if not branch:
            pulls.extend(provider.commit_pulls(repo, head))
        if any(p['state'] == 'open' for p in pulls):
            return keep('open PR')
        if not branch and (not evidence_branch or command(['git', '-C', str(clone), 'merge-base', '--is-ancestor',
                head, 'refs/remotes/origin/' + evidence_branch], allowed=(0, 1, 128)).returncode != 0):
            return keep('detached HEAD has uncertain PR or dispatch branch evidence')
        merged = command(['git', '-C', str(clone), 'merge-base', '--is-ancestor', head, 'origin/main'],
                         allowed=(0, 1, 128)).returncode == 0
        closed = any(p['state'] == 'closed' and p['head']['sha'] == head for p in pulls)
        last_activity = max(epoch(dispatch['dispatched_at']), workspace.stat().st_mtime,
                            clone.stat().st_mtime, (clone / '.git/index').stat().st_mtime)
        if not (merged or closed or now - last_activity >= older_than):
            return keep('clean abandoned checkout younger than age limit')
        if (evidence_branch and provider.open_pulls(repo, evidence_branch) or
                not branch and any(p['state'] == 'open' for p in provider.commit_pulls(repo, head))):
            return keep('open PR appeared before staging')
        fetch_remote_refs(clone)
        busy = process_probe([str(workspace)])
        if (busy is None or busy or workspace.resolve() != workspace or clone.resolve() != clone or
                identity != (workspace.stat().st_ino, clone.stat().st_ino) or
                git(clone, 'rev-parse', 'HEAD') != head or
                git(clone, 'status', '--porcelain=v1', '--untracked-files=all') or
                not remote_preserves(clone)):
            return keep('checkout became live or changed before staging')
        check(deadline)
        reason = 'merged branch' if merged else 'closed PR' if closed else 'old clean pushed checkout'
        if dry_run:
            return {**result, 'status': 'would_stage', 'reason': reason}
        staging = workspace.parent / '_to_delete'
        staging.mkdir(exist_ok=True)
        if staging.resolve() != staging:
            return keep('staging root is a symlink')
        target = staging / (workspace.name + '-' + uuid.uuid4().hex)
        target.mkdir()
        receipt = {**result, 'status': 'intent', 'reason': reason, 'head': head,
                   'staged_path': str(target / 'workspace'), 'at': now}
        save(target / 'manifest.json', receipt)
        check(deadline)
        os.rename(workspace, target / 'workspace')
        if git(target / 'workspace/checkout', 'rev-parse', 'HEAD') != head:
            raise RuntimeError('staged checkout readback mismatch')
        receipt['status'] = 'staged'
        save(target / 'manifest.json', receipt)
        return receipt
    except (TimeoutError, subprocess.TimeoutExpired) as exc:
        if receipt is not None and isinstance(exc, BudgetExhausted):
            save(target / 'manifest.json', {**receipt, 'status': 'verified_no_effect'})
        return {**result, 'status': 'deferred' if receipt is not None and not isinstance(exc, BudgetExhausted) else 'kept',
                'reason': 'cleanup evidence or move verification timed out', 'budget_exhausted': True}
    except Exception as exc:
        return {**keep('evidence or staging failed'), 'error': type(exc).__name__}


def inventory_findings(config, *, tmp_root, roots, user_tmp_root=None):
    from branch_retirement import iter_worktrees
    policy = config['machine_cleanup']
    rows = []
    try:
        import sys
        sys.path.append(str(Path(__file__).resolve().parents[1] / 'tools'))
        from storage_hygiene import allocated_bytes_evidence
        clone_paths = [p for p in Path(tmp_root).glob('room-codex-*') if p.is_dir() and not p.is_symlink()]
        # Staged clones still occupy the volume until their retention is resolved.
        clone_paths.extend(p for p in (Path(tmp_root) / '_to_delete').glob('room-codex-*') if p.is_dir() and not p.is_symlink())
        measurements = [('job_clone_bytes', clone_paths, policy['job_clone_bytes_threshold'])]
        if user_tmp_root is not None:
            measurements.append(('user_temp_bytes', [Path(user_tmp_root)], policy['user_temp_bytes_threshold']))
        for code, paths, threshold in measurements:
            measurement = allocated_bytes_evidence(paths)
            size = measurement['allocated_bytes']
            if size > threshold:
                qualifier = '' if measurement['complete'] else 'at least '
                rows.append({'code': code, 'detail': f'{code}: {qualifier}{size} allocated bytes exceed {threshold}',
                             'measurement': measurement})
            elif not measurement['complete']:
                rows.append({'code': code + '_partial', 'measurement': measurement,
                    'detail': f"{code}: at least {size} allocated bytes; partial evidence after {measurement['entries']} entries ({measurement['reason']}); threshold not resolved"})
    except Exception as exc:
        rows.append({'code': 'cleanup_size_unavailable', 'detail': 'Cleanup size evidence unavailable: ' + type(exc).__name__})
    try:
        count = sum(1 for path in Path(tmp_root).glob('room-codex-*') if path.is_dir() and not path.is_symlink())
        if count > policy['job_clone_threshold']:
            rows.append({'code': 'job_clone_count', 'detail': f'{count} job clones exceed {policy["job_clone_threshold"]}'})
        for repo, path in roots.items():
            path = Path(path).expanduser()
            if not path.exists():
                continue
            count = sum('_to_delete' not in Path(e['path']).parts for e in iter_worktrees(path))
            if count > policy['worktree_threshold']:
                rows.append({'code': 'worktree_count:' + repo, 'detail': f'{repo} has {count} active worktrees, threshold {policy["worktree_threshold"]}'})
    except Exception as exc:
        rows.append({'code': 'cleanup_stale', 'detail': 'Machine cleanup evidence unavailable: ' + type(exc).__name__})
    return rows


def health_findings(root, config, *, tmp_root, roots, now, user_tmp_root=None, inventory=None):
    policy = config['machine_cleanup']
    rows = list(inventory) if inventory is not None else inventory_findings(
        config, tmp_root=tmp_root, roots=roots, user_tmp_root=user_tmp_root)
    try:
        receipt = json.loads((Path(root) / 'out/machine-cleanup.json').read_text())
        last = receipt.get('last_success_at')
        if last is None or now - last > policy['success_max_age_seconds']:
            rows.append({'code': 'cleanup_stale', 'detail': 'Machine cleanup has no success within one day'})
        if receipt.get('errors') or (receipt.get('status') == 'running' and
                now - receipt.get('at', 0) > policy['max_seconds'] + 60):
            rows.append({'code': 'cleanup_failed', 'detail': 'Machine cleanup receipt reports incomplete evidence or failed effects'})
    except Exception as exc:
        rows.append({'code': 'cleanup_stale', 'detail': 'Machine cleanup evidence unavailable: ' + type(exc).__name__})
    return rows


def latest_dispatch(path, *, workspace=None, thread_id=None, deadline=None):
    latest = None
    if not Path(path).exists():
        return None
    with Path(path).open() as stream:
        for line in bounded(stream, deadline):
            row = json.loads(line)
            if (workspace is not None and row.get('checkout_workspace') == str(workspace) or
                    thread_id is not None and row.get('thread_id') == thread_id):
                latest = row
    return latest


def recover_clone(workspace, *, deadline=None):
    workspace = Path(workspace)
    observed = None
    staging = workspace.parent / '_to_delete'
    candidates = entries(staging, deadline) if staging.is_dir() else ()
    for entry in candidates:
        if not entry.name.startswith(workspace.name + '-'):
            continue
        manifest = Path(entry.path) / 'manifest.json'
        check(deadline)
        if not manifest.is_file():
            continue
        row = json.loads(manifest.read_text())
        check(deadline)
        if row.get('path') != str(workspace) or row.get('status') not in {'intent', 'staged'}:
            continue
        staged = Path(row['staged_path'])
        if workspace.exists() and not staged.exists():
            if git(workspace / 'checkout', 'rev-parse', 'HEAD') != row['head']:
                raise RuntimeError('interrupted clone candidate changed')
            check(deadline)
            save(manifest, {**row, 'status': 'verified_no_effect'})
            continue
        if observed or workspace.exists() or not staged.exists() or git(staged / 'checkout', 'rev-parse', 'HEAD') != row['head']:
            raise RuntimeError('interrupted clone move is ambiguous')
        observed = {**row, 'status': 'staged', 'reason': 'interrupted clone move read back before retry'}
        check(deadline)
        save(manifest, observed)
    if not workspace.exists() and observed is None:
        raise RuntimeError('interrupted clone destination unavailable')
    return observed


def run(root, config, *, hook, tmp_root, dispatch_path, roots, provider=None,
        process_probe=process_paths, archive_thread=None, user_tmp_root=None, dry_run=False):
    import heapq
    import sys
    from branch_retirement import FleetReaper, append
    from job_watchdog import locked
    root, tmp_root = Path(root).resolve(), Path(tmp_root).resolve()
    policy = config['machine_cleanup']
    provider = provider or GitHub()
    if archive_thread is None:
        sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools/room-bridge'))
        from codex_wire import archive_finished_thread
        archive_thread = archive_finished_thread
    receipt_path = root / ('out/machine-cleanup-dry.json' if dry_run else 'out/machine-cleanup.json')
    cursor_path = root / ('out/machine-cleanup-dry-cursor.json' if dry_run else 'out/machine-cleanup-cursor.json')
    with locked(root / 'out/machine-cleanup.lock', blocking=False):
        previous = json.loads(receipt_path.read_text()) if receipt_path.exists() else {}
        cursor = json.loads(cursor_path.read_text()) if cursor_path.exists() else {}
        now = time.time()
        deadline = time.monotonic() + policy['max_seconds']
        report = {'schema': 'machine-cleanup/v1', 'at': now, 'status': 'running', 'dry_run': dry_run,
                  'last_success_at': previous.get('last_success_at'), 'removed': [], 'kept': [], 'errors': [], 'deferred': [],
                  'temp_staged_allocated_bytes': 0, 'temp_freed_bytes': 0, 'budget_exhausted': False}
        def finish():
            report['budget_exhausted'] |= time.monotonic() >= deadline
            if report['budget_exhausted']:
                report['deferred'].append({'kind': 'sweep', 'reason': 'cleanup budget exhausted; resume from saved cursor',
                                           'cursor': dict(cursor)})
            report['status'] = 'failed' if report['errors'] else 'deferred' if report['deferred'] else 'completed'
            if report['status'] == 'completed' and not dry_run:
                report['last_success_at'] = now
            save(cursor_path, cursor)
            save(receipt_path, report)
            append(root / 'out/machine-cleanup-runs.jsonl', {k: v for k, v in report.items() if k not in {'removed', 'kept'}})
            return report

        pending = previous.get('pending')
        recovered = None
        if pending:
            try:
                check(lambda: time.monotonic() >= deadline)
                kind = pending['kind']
                if kind == 'job_clone':
                    recovered = recover_clone(pending['path'], deadline=lambda: time.monotonic() >= deadline)
                    if recovered and recovered['status'] == 'staged':
                        cursor['clone'] = Path(pending['path']).name
                elif kind == 'test_temp':
                    sys.path.append(str(Path(__file__).resolve().parents[1] / 'tools'))
                    from storage_hygiene import recover_test_temp
                    recovered = recover_test_temp(pending, deadline=lambda: time.monotonic() >= deadline)
                    slot = 'test_temp:' + str(Path(pending['path']).parent)
                    if recovered['status'] == 'staged':
                        cursor[slot] = Path(pending['path']).name
                elif kind == 'thread':
                    before = pending.get('before', previous['at'] - policy['thread_age_seconds'])
                    recovered = archive_thread(pending['thread_id'], before=before,
                        process_probe=process_probe, timeout=min(30, policy['max_seconds']), dry_run=True)
                    if recovered.get('status') == 'would_archive':
                        # The interrupted effect did not happen. Revisit dispatch evidence normally.
                        recovered = {**recovered, 'status': 'verified_no_effect',
                                     'reason': 'interrupted thread archive had no effect'}
                    if recovered.get('status') == 'archived' and 'thread_offset' in pending:
                        cursor['thread_offset'] = pending['thread_offset']
                    if recovered.get('error'):
                        raise RuntimeError('interrupted thread archive readback failed')
                else:
                    raise RuntimeError('unknown pending cleanup kind')
                if recovered and recovered.get('budget_exhausted'):
                    report.update(budget_exhausted=True, pending=pending)
                    report['deferred'].append(recovered)
                    return finish()
                append(root / 'out/machine-cleanup-recovery.jsonl',
                       {'previous': previous, 'resolution': recovered, 'at': now})
            except (TimeoutError, subprocess.TimeoutExpired):
                report.update(budget_exhausted=True, pending=pending)
                return finish()
            except Exception as exc:
                error = {'kind': pending.get('kind'), 'error': type(exc).__name__, 'reason': str(exc)}
                errors = previous.get('errors', [])
                failed = {**previous, 'status': 'failed', 'errors': errors if error in errors else errors + [error]}
                save(receipt_path, failed)
                return failed
        save(receipt_path, report)

        def record(row):
            report['budget_exhausted'] |= row.get('budget_exhausted', False)
            if row.get('kind') == 'test_temp' and row.get('status') == 'staged':
                report['temp_staged_allocated_bytes'] += row['allocated_bytes']
            collection = ('deferred' if row.get('status') == 'deferred' else
                          'removed' if row.get('status') in {'staged', 'archived', 'removed'} else 'kept')
            report[collection].append(row)
            if row.get('error'):
                report['errors'].append({'kind': row['kind'], 'error': row['error']})
            if not row.get('error') and row.get('status') != 'deferred':
                report.pop('pending', None)
            save(receipt_path, report)
            save(cursor_path, cursor)

        if recovered:
            record(recovered)

        existing = {repo: path for repo, path in roots.items() if Path(path).expanduser().is_dir()}
        if existing:
            reaper = FleetReaper(root, hook, provider=provider, process_probe=process_probe, config=config)
            reaper_deadline = min(deadline, time.monotonic() + policy['max_seconds'] / 3)
            trees = reaper.run(existing, execute=not dry_run, local_only=True,
                               max_items=policy['max_items'], max_seconds=max(0, reaper_deadline - time.monotonic()))
            report['errors'].extend(trees['errors'])
            report['deferred'].extend(trees['deferred'])
            report['budget_exhausted'] |= time.monotonic() >= reaper_deadline
            actions = {a['candidate'].get('path'): a for a in trees['actions']}
            for row in trees['rows']:
                action = actions.get(row.get('path'))
                record({**row, 'status': action['status'] if action else 'kept',
                        'reason': action.get('reason', row['reason']) if action else row['reason'],
                        **({'staged_path': action['staged_path']} if action and 'staged_path' in action else {})})
        if user_tmp_root is not None:
            sys.path.append(str(Path(__file__).resolve().parents[1] / 'tools'))
            from storage_hygiene import cleanup_test_temps
            temp_deadline = time.monotonic() + max(0, deadline - time.monotonic()) / 3
            temp_roots = list(dict.fromkeys((Path(user_tmp_root).resolve(), tmp_root)))
            for index, temp_root in enumerate(temp_roots):
                root_deadline = time.monotonic() + max(0, temp_deadline - time.monotonic()) / (len(temp_roots) - index)
                slot = 'test_temp:' + str(temp_root)
                def temp_record(row):
                    if row['status'] == 'intent':
                        report['pending'] = row
                        save(receipt_path, report)
                    else:
                        if not row.get('error') and row['status'] != 'deferred':
                            cursor[slot] = Path(row['path']).name
                        record(row)
                temps = cleanup_test_temps(temp_root, now=now, min_age=policy['test_temp_age_seconds'],
                    max_items=policy['test_temp_max_items'], cursor=cursor.get(slot, ''),
                    process_probe=process_probe, deadline=lambda: time.monotonic() >= root_deadline,
                    dry_run=dry_run, record=temp_record)
                report['budget_exhausted'] |= temps['budget_exhausted']
                cursor[slot] = temps['cursor']
                save(cursor_path, cursor)
                save(receipt_path, report)
                if report.get('pending'):
                    return finish()
        remaining = max(0, deadline - time.monotonic())
        clone_deadline = time.monotonic() + remaining / 2
        def clone_candidates():
            for entry in entries(tmp_root, lambda: time.monotonic() >= clone_deadline):
                if entry.name.startswith('room-codex-') and entry.name > cursor.get('clone', '') and entry.is_dir():
                    yield Path(entry.path)
        try:
            candidates = heapq.nsmallest(policy['max_items'], clone_candidates(), key=lambda p: p.name)
        except BudgetExhausted:
            candidates = []
            report['budget_exhausted'] = True
        for workspace in candidates:
            if time.monotonic() >= clone_deadline:
                report['budget_exhausted'] = True
                break
            report['pending'] = {'kind': 'job_clone', 'path': str(workspace)}
            save(receipt_path, report)
            try:
                dispatch = latest_dispatch(dispatch_path, workspace=workspace,
                    deadline=lambda: time.monotonic() >= clone_deadline)
            except BudgetExhausted:
                report.pop('pending', None)
                report['budget_exhausted'] = True
                break
            row = clean_clone(workspace, dispatch,
                provider=provider, process_probe=process_probe, now=now,
                older_than=policy['checkout_age_seconds'], dry_run=dry_run,
                deadline=lambda: time.monotonic() >= clone_deadline)
            if not row.get('error') and not row.get('budget_exhausted'):
                cursor['clone'] = workspace.name
            record(row)
            if row.get('error') or row.get('budget_exhausted'):
                break
        else:
            if len(candidates) < policy['max_items'] and not report['budget_exhausted']:
                cursor.pop('clone', None)
                save(cursor_path, cursor)
        if not report.get('pending') and Path(dispatch_path).exists():
            with Path(dispatch_path).open('rb') as stream:
                offset = cursor.get('thread_offset', 0)
                if offset > os.fstat(stream.fileno()).st_size:
                    offset = 0
                stream.seek(offset)
                processed = 0
                while processed < policy['max_items'] and time.monotonic() < deadline:
                    line = stream.readline()
                    if not line:
                        cursor['thread_offset'] = 0
                        save(cursor_path, cursor)
                        break
                    if not line.endswith(b'\n'):
                        break
                    row = json.loads(line)
                    tid = row.get('thread_id')
                    if tid:
                        processed += 1
                        try:
                            latest = latest_dispatch(dispatch_path, thread_id=tid,
                                deadline=lambda: time.monotonic() >= deadline)
                            check(lambda: time.monotonic() >= deadline)
                        except BudgetExhausted:
                            report['budget_exhausted'] = True
                            break
                        if latest.get('status') != 'completed' or latest.get('termination_confirmed') is False:
                            result = {'kind': 'thread', 'thread_id': tid, 'status': 'kept', 'reason': 'finished dispatch not proven'}
                        elif now - epoch(latest['dispatched_at']) < policy['thread_age_seconds']:
                            result = {'kind': 'thread', 'thread_id': tid, 'status': 'kept', 'reason': 'recent dispatch'}
                        else:
                            report['pending'] = {'kind': 'thread', 'thread_id': tid,
                                                 'before': now - policy['thread_age_seconds'],
                                                 'thread_offset': stream.tell()}
                            save(receipt_path, report)
                            if time.monotonic() >= deadline:
                                report.pop('pending', None)
                                report['budget_exhausted'] = True
                                break
                            result = archive_thread(tid, before=now - policy['thread_age_seconds'],
                                process_probe=process_probe, timeout=min(30, max(.1, deadline - time.monotonic())),
                                dry_run=dry_run)
                        if not result.get('error') and not result.get('budget_exhausted'):
                            cursor['thread_offset'] = stream.tell()
                        record(result)
                        if result.get('error') or result.get('budget_exhausted'):
                            break
                    else:
                        cursor['thread_offset'] = stream.tell()
                        save(cursor_path, cursor)
        return finish()
