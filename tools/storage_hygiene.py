#!/usr/bin/env python3
"""Bounded cleanup for CARR-owned scratch and stale Chrome code-sign clones."""
from __future__ import annotations

import argparse
import fcntl
import json
import os
import shutil
import subprocess
import sys
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable


REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))
sys.path.insert(0, str(REPO / "lib"))
from lib.cleanup_budget import BudgetExhausted, check, entries, walk
AGE_SECONDS = 24 * 60 * 60
DATA_THRESHOLD_BYTES = 1_000_000_000_000
TMP_PREFIXES = (
    "carr", "flash", "successor", "presence", "codex-cloud",
    "fake-flash-run", "hookenv-", "syncmap", "ipc", "factory",
    "design", "branch", "doctorcre",
)
TEST_TMP_PREFIXES = (
    "carr-ci-test-",
    "branch-janitor-fixture-", "carr-dispatch-door-", "carr-dispatch-paused-",
    "flash-projects-", "doc-drift-fixture-", "doc-drift-ci-fixture-",
    "doc-drift-pin-fixture-", "doc-drift-missing-pin-", "presence-", "jev-ring",
    "flash-inputs-", "flash-outside-", "fake-flash-run-", "flash-canary-",
    "hookenv-a-", "hookenv-b-", "playwright_chromiumdev_profile-",
    "playwright_firefoxdev_profile-", "playwright_webkitdev_profile-", "playwright-artifacts-",
)

UNKNOWN_SAMPLE_LIMIT = 25
PHYSICAL_ESTIMATE_METHOD = (
    "APFS shared extents make du totals logical, not physical reclaim. "
    "Estimate physical reclaim from volume free space before and after cleanup, "
    "with other writers idle; snapshots can retain blocks.")


@dataclass(frozen=True)
class Candidate:
    kind: str
    path: Path
    age_seconds: float


@dataclass
class CleanupPlan:
    roots: dict[Path, tuple[int, int]] = field(default_factory=dict)
    removable: list[Candidate] = field(default_factory=list)
    protected: set[Path] = field(default_factory=set)
    unrecognized: set[Path] = field(default_factory=set)
    unrecognized_count: int = 0
    clone_count: int = 0
    stop_reason: str | None = None


@dataclass(frozen=True)
class RunResult:
    removed_count: int
    finding_count: int
    record_status: str


def _old(path: Path, now: float, older_than_seconds: float) -> tuple[bool, float]:
    age = now - path.stat(follow_symlinks=False).st_mtime
    return age > older_than_seconds, age


def _inside_worktree(path: Path) -> bool:
    return (path / ".git").exists()


def _open_root(path: Path, expected: tuple[int, int] | None = None) -> int:
    """Open a canonical absolute directory without following any symlink."""
    if not path.is_absolute() or path.resolve() != path or ".." in path.parts:
        raise OSError(f"noncanonical cleanup root: {path}")
    flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
    descriptor = os.open(path.anchor, flags)
    try:
        for part in path.parts[1:]:
            child = os.open(part, flags, dir_fd=descriptor)
            os.close(descriptor)
            descriptor = child
        if expected is not None:
            info = os.fstat(descriptor)
            if (info.st_dev, info.st_ino) != expected:
                raise OSError("cleanup root changed since planning")
        return descriptor
    except BaseException:
        os.close(descriptor)
        raise


def plan_cleanup(*, clone_root: Path, tmp_root: Path, replay_root: Path,
                 now: float, older_than_seconds: float, max_items: int,
                 is_open: Callable[[Path], bool], is_locked: Callable[[Path], bool],
                 deadline: Callable[[], bool],
                 include_scratch: bool = False, include_replay: bool = False) -> CleanupPlan:
    plan = CleanupPlan()
    pending: dict[str, list[Candidate]] = {
        "chrome-clone": [], "scratch": [], "gate-replay": []}

    def visit(root: Path, kind: str, recognized: Callable[[str], bool]) -> bool:
        try:
            descriptor = _open_root(root)
        except FileNotFoundError:
            return True
        except (OSError, RuntimeError):
            plan.protected.add(root)
            return True
        try:
            info = os.fstat(descriptor)
            plan.roots[root] = (info.st_dev, info.st_ino)
            entries = sorted((root / name for name in os.listdir(descriptor)),
                             key=lambda item: item.name)
        except OSError:
            plan.protected.add(root)
            return True
        finally:
            os.close(descriptor)
        if kind == "chrome-clone":
            plan.clone_count = sum(
                entry.name.startswith("code_sign_clone.")
                and entry.is_dir() and not entry.is_symlink()
                for entry in entries)
        for entry in entries:
            if deadline():
                plan.stop_reason = "time-cap"
                return False
            if entry.is_symlink() or not entry.is_dir() or not recognized(entry.name):
                try:
                    stale, _age = _old(entry, now, older_than_seconds)
                except OSError:
                    plan.protected.add(entry)
                    continue
                if stale:
                    plan.unrecognized_count += 1
                    if len(plan.unrecognized) < UNKNOWN_SAMPLE_LIMIT:
                        plan.unrecognized.add(entry)
                continue
            try:
                stale, age = _old(entry, now, older_than_seconds)
            except OSError:
                plan.protected.add(entry)
                continue
            if not stale:
                continue
            pending[kind].append(Candidate(kind, entry, age))
        return True

    if not visit(clone_root, "chrome-clone", lambda name: name.startswith("code_sign_clone.")):
        return plan
    if include_scratch and not visit(tmp_root, "scratch", lambda name: name.startswith(TMP_PREFIXES)):
        return plan
    if include_replay and not visit(replay_root, "gate-replay", lambda name: name.startswith("run-")):
        return plan

    kinds = tuple(pending)
    for kind in kinds:
        pending[kind].sort(key=lambda item: (-item.age_seconds, str(item.path)))
    indexes = {kind: 0 for kind in kinds}
    selected: list[Candidate] = []
    while len(selected) < max_items:
        progressed = False
        for kind in kinds:
            index = indexes[kind]
            if index >= len(pending[kind]):
                continue
            selected.append(pending[kind][index])
            indexes[kind] += 1
            progressed = True
            if len(selected) >= max_items:
                break
        if not progressed:
            break
    if any(indexes[kind] < len(pending[kind]) for kind in kinds):
        plan.stop_reason = "item-cap"

    for candidate in selected:
        if deadline():
            plan.stop_reason = "time-cap"
            break
        entry = candidate.path
        if _inside_worktree(entry):
            plan.protected.add(entry)
            continue
        try:
            if is_open(entry) or (
                    candidate.kind == "gate-replay" and is_locked(entry)):
                plan.protected.add(entry)
                continue
            descriptor = _open_root(entry.parent, plan.roots[entry.parent])
            os.close(descriptor)
        except (OSError, RuntimeError, subprocess.SubprocessError):
            plan.protected.add(entry)
            continue
        plan.removable.append(candidate)
    return plan


def path_has_open_files(path: Path, timeout_seconds: float = 3.0) -> bool:
    result = subprocess.run(
        ["/usr/sbin/lsof", "-n", "-P", "+D", str(path)],
        stdin=subprocess.DEVNULL, capture_output=True, text=True,
        timeout=timeout_seconds, check=False)
    if result.returncode not in (0, 1) or result.stderr.strip():
        raise OSError(f"incomplete lsof scan (exit {result.returncode})")
    if result.returncode == 0 and not result.stdout.strip():
        raise OSError("lsof succeeded without scan results")
    return bool(result.stdout.strip())


def replay_is_locked(path: Path) -> bool:
    lock_path = path / ".active.lock"
    if not lock_path.is_file() or lock_path.is_symlink():
        return False
    with lock_path.open("r+") as handle:
        try:
            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return True
        fcntl.flock(handle, fcntl.LOCK_UN)
    return False


def storage_snapshot(data_volume: Path, clone_root: Path) -> tuple[int, int]:
    used = shutil.disk_usage(data_volume).used
    try:
        clones = sum(entry.is_dir() and not entry.is_symlink()
                     and entry.name.startswith("code_sign_clone.")
                     for entry in clone_root.iterdir())
    except OSError:
        clones = -1
    return used, clones


def logical_allocated_bytes(candidates: list[Candidate], timeout_seconds: float) -> int | None:
    if not candidates:
        return 0
    try:
        result = subprocess.run(
            ["/usr/bin/du", "-sk", *(str(item.path) for item in candidates)],
            stdin=subprocess.DEVNULL, capture_output=True, text=True,
            timeout=max(0.1, timeout_seconds), check=False)
    except subprocess.TimeoutExpired:
        return None
    if result.returncode:
        return None
    try:
        return sum(int(line.split(None, 1)[0]) * 1024
                   for line in result.stdout.splitlines() if line.strip())
    except (ValueError, IndexError):
        return None


def allocated_bytes_evidence(paths, *, max_seconds=30):
    import hashlib
    paths = sorted({str(path) for path in paths}, key=lambda p: hashlib.sha256(p.encode()).digest())
    population_size = len(paths)
    paths = paths[:32]
    result = {'allocated_bytes': 0, 'entries': 0, 'complete': True, 'reason': 'complete',
              'population_size': population_size, 'sample_size': len(paths), 'estimated': population_size > len(paths),
              'method': 'du -sk; sample by path hash; APFS shared extents may be counted repeatedly'}
    if not paths:
        return result
    try:
        measured = subprocess.run(['/usr/bin/du', '-sk', *paths], stdin=subprocess.DEVNULL,
            capture_output=True, text=True, timeout=max_seconds, check=False)
        output = measured.stdout
        if measured.returncode:
            result.update(complete=False, reason='du scan incomplete')
    except subprocess.TimeoutExpired as exc:
        output = exc.stdout or ''
        if isinstance(output, bytes):
            output = output.decode('utf-8', errors='replace')
        # A killed du can leave the last output row incomplete.
        output = output[:output.rfind('\n') + 1]
        result.update(complete=False, reason='time budget')
    except OSError as exc:
        result.update(complete=False, reason=type(exc).__name__)
        return result
    for line in output.splitlines():
        try:
            size, _path = line.split(None, 1)
            result['allocated_bytes'] += int(size) * 1024
            result['entries'] += 1
        except (ValueError, IndexError):
            result.update(complete=False, reason='invalid du evidence')
    if result['entries'] != len(paths):
        result.update(complete=False, reason=result['reason'] if not result['complete'] else 'missing du rows')
    result['measured_allocated_bytes'] = result['allocated_bytes']
    if result['complete'] and result['estimated']:
        result['allocated_bytes'] = result['allocated_bytes'] * population_size // len(paths)
    return result


def health_row(*, used_bytes: int, clone_count: int,
               threshold_bytes: int = DATA_THRESHOLD_BYTES) -> str:
    state = "WARN" if used_bytes > threshold_bytes else "OK"
    comparison = ">" if used_bytes > threshold_bytes else "<="
    action = ("on breach: owner orchestrator; scheduled tools/storage_hygiene.py opens "
              "the storage investigation loop; remediation: fix the producer and "
              "run bounded cleanup; verify: run.sh health --section storage; "
              "auto-clear below threshold")
    return (f"{state} storage hygiene — data volume used {used_bytes / 1e12:.3f} TB "
            f"{comparison} {threshold_bytes / 1e12:.3f} TB; "
            f"Chrome code-sign clones={clone_count} · {action}")


def _append_ledger(path: Path, row: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    flags = os.O_WRONLY | os.O_CREAT | os.O_APPEND
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    descriptor = os.open(path, flags, 0o600)
    try:
        os.write(descriptor, (json.dumps(row, sort_keys=True) + "\n").encode())
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _previous_run(path: Path) -> dict:
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
        value = json.loads(lines[-1]) if lines else {}
        return value if isinstance(value, dict) else {}
    except (OSError, ValueError, TypeError):
        return {}


def _finding_facts(plan: CleanupPlan, disk_used: int | None) -> dict:
    return {
        "removable": len(plan.removable),
        "protected": len(plan.protected),
        "unrecognized": plan.unrecognized_count,
        "clone_count": plan.clone_count,
        "disk_used": disk_used,
        "stop_reason": plan.stop_reason,
    }


def _finding_body(facts: dict) -> str:
    return ("Storage hygiene finding: " + json.dumps(facts, sort_keys=True)
            + ". Bound action: investigate the producer or capacity breach, preserve "
              "open paths, and rerun tools/storage_hygiene.py --dry-run until clear.")


def _finding_payload(plan: CleanupPlan, *, episode_key: str,
                     disk_used: int | None = None) -> dict:
    return {
        "idempotency_key": str(uuid.uuid5(
            uuid.NAMESPACE_URL, "carr-storage-hygiene:" + episode_key)),
        "kind": "open_loop",
        "owner": "claude",  # add-loop owners are joe/dell/claude; the orchestrator is the Claude seat
        "domain": "system",
        "marker": "none",
        "blocker": "other_lane",
        "blocker_detail": "Orchestrator source-owner investigation and bounded cleanup",
        "body": _finding_body(_finding_facts(plan, disk_used)),
    }


def _record_finding(payload: dict) -> dict:
    from lib.record_call import call_verb
    result = call_verb("add-loop", payload, timeout=30)
    reply = result.reply if isinstance(result.reply, dict) else {}
    return {"ok": result.ok, "status": result.kind, "detail": result.describe(),
            "loop_id": reply.get("loop_id")}


def _update_finding(loop: dict, body: str) -> dict:
    """Refresh an existing episode loop with a fresh mutation key.

    The creation call is replayed unchanged (same key, same payload), so the
    changed facts travel only through update-loop, never through a reused key.
    """
    from lib.record_call import call_verb
    loop_id = loop.get("loop_id")
    if not loop_id:
        return {"ok": False, "status": "refused", "detail": "no loop_id to update"}
    read = call_verb("read-loop", {"loop_id": loop_id}, timeout=30)
    if not read.ok:
        return {"ok": False, "status": read.kind, "detail": read.describe()}
    current = read.reply.get("loop") if isinstance(read.reply, dict) else None
    if not isinstance(current, dict) or type(current.get("version")) is not int:
        return {"ok": False, "status": "refused",
                "detail": "read-loop response requires an integer loop.version"}
    result = call_verb("update-loop", {
        "idempotency_key": str(uuid.uuid4()), "loop_id": loop_id,
        "base_version": current["version"], "body": body}, timeout=30)
    return {"ok": result.ok, "status": result.kind, "detail": result.describe()}


def apply_plan(plan: CleanupPlan, *, dry_run: bool, ledger_path: Path,
               record_finding: Callable[[dict], dict],
               update_finding: Callable[[dict, str], dict] = _update_finding,
               disk_used: int | None = None,
               disk_threshold: int = DATA_THRESHOLD_BYTES,
               eligible_logical_bytes: int | None = None,
               is_open: Callable[[Path], bool] = path_has_open_files,
               is_locked: Callable[[Path], bool] = replay_is_locked,
               deadline: Callable[[], bool] = lambda: False,
               now: Callable[[], float] = time.time,
               older_than_seconds: float = AGE_SECONDS) -> RunResult:
    removed = 0
    if not dry_run:
        for candidate in plan.removable:
            if deadline():
                plan.stop_reason = "time-cap"
                break
            path = candidate.path
            descriptor = None
            try:
                root = path.parent
                if root not in plan.roots:
                    raise OSError("candidate has no pinned cleanup root")
                descriptor = _open_root(root, plan.roots[root])
                if not path.is_dir() or path.is_symlink():
                    plan.protected.add(path)
                    continue
                stale, _age = _old(path, now(), older_than_seconds)
                active = not stale or is_open(path) or (
                    candidate.kind == "gate-replay" and is_locked(path))
                if active:
                    plan.protected.add(path)
                    continue
                rechecked = _open_root(root, plan.roots[root])
                os.close(rechecked)
                if _inside_worktree(path):
                    plan.protected.add(path)
                    continue
                shutil.rmtree(path.name, dir_fd=descriptor)
                removed += 1
            except (OSError, RuntimeError, subprocess.SubprocessError):
                plan.protected.add(path)
            finally:
                if descriptor is not None:
                    os.close(descriptor)
    findings = (len(plan.removable) + plan.unrecognized_count
                + int(disk_used is not None and disk_used > disk_threshold))
    previous = _previous_run(ledger_path)
    at = datetime.now(timezone.utc).isoformat(timespec="seconds")
    episode_key: str | None = None
    if findings:
        previous_episode = previous.get("finding_episode")
        episode_key = (previous_episode
                       if (previous.get("finding_count", 0)
                           and isinstance(previous_episode, str))
                       else at)
    status = "skipped-dry-run" if dry_run else "not-needed"
    creation: dict | None = None
    if findings:
        carried = previous.get("finding_creation")
        creation = (carried if (episode_key == previous.get("finding_episode")
                                and isinstance(carried, dict)) else None)
    if findings and not dry_run:
        assert episode_key is not None
        try:
            # The creation payload is immutable for the episode: it is built
            # once, kept in the ledger, and replayed byte-for-byte. A changed
            # body under the same key is rejected as key_reuse.
            if creation is None:
                creation = _finding_payload(
                    plan, episode_key=episode_key, disk_used=disk_used)
            answer = record_finding(dict(creation))
            status = "recorded" if answer.get("ok") else "record-failed"
            fresh = _finding_body(_finding_facts(plan, disk_used))
            if status == "recorded" and fresh != creation["body"]:
                refreshed = update_finding(answer, fresh)
                if not refreshed.get("ok"):
                    status = "record-failed"
        except Exception:
            status = "record-failed"
    row = {
        "schema": "storage-hygiene-run/v1",
        "at": at,
        "previous_run_at": previous.get("at"),
        "mode": "dry-run" if dry_run else "apply",
        "eligible": len(plan.removable),
        "eligible_logical_bytes": eligible_logical_bytes,
        "physical_reclaim_bytes": None,
        "physical_estimate_method": PHYSICAL_ESTIMATE_METHOD,
        "removed": removed,
        "protected": len(plan.protected),
        "unrecognized": plan.unrecognized_count,
        "clone_count": plan.clone_count,
        "disk_used_bytes": disk_used,
        "finding_count": findings,
        "finding_episode": episode_key,
        "finding_creation": creation,
        "stop_reason": plan.stop_reason,
        "record_status": status,
    }
    _append_ledger(ledger_path, row)
    return RunResult(removed, findings, status)


def _temp_activity(path, deadline, *, max_entries=None):
    import hashlib
    digest = hashlib.sha256()
    latest = 0.0
    size = 0
    seen = set()
    def inspect(entry):
        nonlocal latest, size
        check(deadline)
        if max_entries is not None and len(seen) >= max_entries:
            raise TimeoutError('fixture inspection budget')
        try:
            info = entry.lstat()
        except FileNotFoundError:
            return
        identity = info.st_dev, info.st_ino
        if identity not in seen:
            seen.add(identity)
            size += info.st_blocks * 512
        latest = max(latest, info.st_mtime)
        digest.update(repr((str(entry.relative_to(path)), info.st_dev, info.st_ino,
                            info.st_mode, info.st_size, info.st_mtime_ns)).encode())
    inspect(path)
    for parent, dirs, files in walk(path, deadline):
        dirs.sort()
        for name in sorted(dirs + files):
            inspect(Path(parent) / name)
    return digest.hexdigest(), latest, size


def recover_test_temp(pending, *, deadline):
    from branch_retirement import save
    source = Path(pending['path'])
    manifest = Path(pending['manifest_path'])
    if (source.resolve() != source or manifest.resolve() != manifest or
            manifest.parent.parent != source.parent / '_to_delete'):
        raise RuntimeError('interrupted fixture staging identity mismatch')
    row = json.loads(manifest.read_text())
    staged = Path(row['staged_path'])
    if (row['path'] != str(source) or staged != manifest.parent / 'fixture' or
            staged.resolve() != staged or row['status'] not in {'intent', 'staged', 'verified_no_effect'}):
        raise RuntimeError('interrupted fixture manifest mismatch')
    if source.exists() == staged.exists():
        raise RuntimeError('interrupted fixture move is ambiguous')
    actual = staged if staged.exists() else source
    info = actual.lstat()
    if (info.st_dev, info.st_ino) != tuple(row['identity']):
        raise RuntimeError('interrupted fixture directory changed')
    if actual == staged and _temp_activity(staged, deadline)[0] != row['activity']:
        raise RuntimeError('interrupted staged fixture changed')
    check(deadline)
    row.update(status='staged' if actual == staged else 'verified_no_effect',
               reason='interrupted fixture move read back before retry')
    save(manifest, row)
    return row


def cleanup_test_temps(root, *, now, min_age, max_items, cursor, process_probe,
                       deadline, dry_run, record=lambda row: None):
    import heapq
    from branch_retirement import save
    root = Path(root)
    report = {'removed': [], 'kept': [], 'cursor': cursor, 'staged_allocated_bytes': 0,
              'freed_bytes': 0, 'budget_exhausted': False, 'errors': []}
    descriptor = _open_root(root)
    identity = os.fstat(descriptor)
    try:
        try:
            names = heapq.nsmallest(max_items, (e.name for e in entries(descriptor, deadline)
                if e.name.startswith(TEST_TMP_PREFIXES) and e.name > cursor))
        except BudgetExhausted:
            report['budget_exhausted'] = True
            return report
        for offset in range(0, len(names), 50):
            if deadline():
                report['budget_exhausted'] = True
                break
            batch = names[offset:offset + 50]
            busy = process_probe([str(root / name) for name in batch])
            for name in batch:
                if deadline():
                    report['budget_exhausted'] = True
                    break
                path = root / name
                row = {'kind': 'test_temp', 'path': str(path), 'status': 'kept', 'reason': 'recent fixture'}
                target = None
                try:
                    info = path.lstat()
                    if path.is_symlink() or not path.is_dir():
                        row['reason'] = 'symlink or non-directory'
                    elif busy is None or str(path) in busy:
                        row['reason'] = 'live process or process evidence unavailable'
                    else:
                        inspection_deadline = time.monotonic() + 2
                        activity, latest, size = _temp_activity(path,
                            lambda: deadline() or time.monotonic() >= inspection_deadline, max_entries=10000)
                        if now - latest >= min_age:
                            if not dry_run:
                                staging = root / '_to_delete'
                                staging.mkdir(exist_ok=True)
                                if staging.resolve() != staging:
                                    raise OSError('staging root is a symlink')
                                target = staging / (name + '-' + uuid.uuid4().hex)
                                target.mkdir()
                                row.update(staged_path=str(target / 'fixture'), manifest_path=str(target / 'manifest.json'),
                                    identity=[info.st_dev, info.st_ino], activity=activity, allocated_bytes=size, at=now)
                                save(target / 'manifest.json', {**row, 'status': 'intent'})
                                record({**row, 'status': 'intent'})
                            final_busy = process_probe([str(path)])
                            # A nested write can leave the candidate directory's mtime unchanged.
                            final_activity, newest, _ = _temp_activity(path, deadline)
                            rechecked = _open_root(root, (identity.st_dev, identity.st_ino))
                            os.close(rechecked)
                            if final_busy is None or str(path) in final_busy or activity != final_activity or now - newest < min_age:
                                row['reason'] = 'fixture became live or changed before staging'
                            elif dry_run:
                                row.update(status='would_stage', reason='old inactive disposable test fixture', allocated_bytes=size)
                            else:
                                assert target is not None
                                check(deadline)
                                os.rename(name, target / 'fixture', src_dir_fd=descriptor)
                                staged_busy = process_probe([str(path), str(target / 'fixture')])
                                verification_deadline = time.monotonic() + 2
                                if (staged_busy is None or any(p in staged_busy for p in (str(path), str(target / 'fixture'))) or
                                        _temp_activity(target / 'fixture', lambda: time.monotonic() >= verification_deadline,
                                                       max_entries=10000)[0] != activity):
                                    if path.exists() or path.is_symlink():
                                        raise RuntimeError('fixture restore destination occupied')
                                    os.rename(target / 'fixture', name, dst_dir_fd=descriptor)
                                    row['reason'] = 'fixture became live or changed during staging; restored'
                                else:
                                    row.update(status='staged', reason='old inactive disposable test fixture')
                                    report['staged_allocated_bytes'] += size
                            if target is not None:
                                save(target / 'manifest.json', {**row,
                                    'status': 'staged' if row['status'] == 'staged' else 'verified_no_effect'})
                except FileNotFoundError:
                    if not path.exists() and not path.is_symlink() and (target is None or not (target / 'fixture').exists()):
                        row.update(status='verified_no_effect', reason='fixture vanished before staging')
                        if target is not None:
                            save(target / 'manifest.json', row)
                    else:
                        row.update(error='FileNotFoundError', reason='fixture staging readback unavailable')
                        report['errors'].append({'kind': 'test_temp', 'error': 'FileNotFoundError'})
                except TimeoutError:
                    if target is not None and (target / 'fixture').exists():
                        row.update(error='TimeoutError', reason='staged fixture inspection incomplete')
                        report['errors'].append({'kind': 'test_temp', 'error': 'TimeoutError'})
                    else:
                        row.update(status='kept', reason='fixture inspection budget exhausted; no move performed')
                        if target is not None:
                            save(target / 'manifest.json', {**row, 'status': 'verified_no_effect'})
                    report['budget_exhausted'] = deadline()
                except (OSError, RuntimeError, subprocess.SubprocessError) as exc:
                    row.update(status='kept', reason='fixture evidence or staging failed', error=type(exc).__name__)
                    report['errors'].append({'kind': 'test_temp', 'error': type(exc).__name__})
                report['removed' if row['status'] == 'staged' else 'kept'].append(row)
                report['cursor'] = name
                record(row)
                if row.get('error'):
                    return report
            if report['budget_exhausted']:
                break
        report['budget_exhausted'] |= len(names) == max_items or deadline()
        if not report['budget_exhausted']:
            report['cursor'] = ''
        return report
    finally:
        os.close(descriptor)


def storage_roots() -> tuple[Path, Path, Path]:
    if sys.platform != "darwin":
        raise OSError("storage hygiene root discovery requires macOS")
    result = subprocess.run(
        ["/usr/bin/getconf", "DARWIN_USER_TEMP_DIR"],
        capture_output=True, text=True, check=True, timeout=3)
    tmp_root = Path(result.stdout.strip()).resolve()
    if (not tmp_root.is_absolute() or tmp_root.name != "T"
            or not tmp_root.is_relative_to(Path("/private/var/folders"))):
        raise OSError("getconf did not return the macOS user temporary directory")
    clone_root = tmp_root.parent / "X" / "com.google.Chrome.code_sign_clone"
    replay_root = Path.home() / ".cache" / "carr-gate-replay"
    return tmp_root, clone_root, replay_root


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--machine", action="store_true", help="also retire local worktrees, finished job clones, and old completed desk threads")
    parser.add_argument("--health-row", action="store_true")
    parser.add_argument("--include-scratch", action="store_true",
                        help="also clean allowlisted scratch in the pinned user temp root")
    parser.add_argument("--include-gate-replay", action="store_true",
                        help="also clean unlocked runs in ~/.cache/carr-gate-replay")
    parser.add_argument("--max-items", type=int, default=100)
    parser.add_argument("--max-seconds", type=float, default=60.0)
    parser.add_argument("--older-than-hours", type=float, default=AGE_SECONDS / 3600)
    args = parser.parse_args(argv)
    if args.max_items < 1 or args.max_seconds <= 0 or args.older_than_hours <= 0:
        parser.error("caps and age must be positive")

    tmp_root, clone_root, replay_root = storage_roots()
    used, clone_count = storage_snapshot(Path("/System/Volumes/Data"), clone_root)
    row = health_row(used_bytes=used, clone_count=clone_count)
    if args.health_row:
        print(row)
        return int(used > DATA_THRESHOLD_BYTES)

    started = time.monotonic()
    if args.machine:
        import importlib.util
        from job_watchdog import load_config, repository_roots
        from machine_cleanup import run as clean_machine
        config = load_config(REPO / "ops/config/job-watchdog.json")
        config["machine_cleanup"]["max_seconds"] = min(config["machine_cleanup"]["max_seconds"], args.max_seconds)
        spec = importlib.util.spec_from_file_location("cleanup_worktree_hook", REPO / "hooks/worktree-self-plumb.py")
        assert spec is not None and spec.loader is not None
        hook = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = hook
        spec.loader.exec_module(hook)
        machine = clean_machine(REPO, config, hook=hook, tmp_root=Path("/private/tmp"),
            dispatch_path=Path.home() / ".config/carr/hermes-dispatch-results.jsonl",
            roots=repository_roots(config), user_tmp_root=tmp_root, dry_run=args.dry_run)
        print(json.dumps({k: v for k, v in machine.items() if k not in {"removed", "kept"}}))
        print(row)
        return int(bool(machine["errors"]))
    plan = plan_cleanup(
        clone_root=clone_root, tmp_root=tmp_root, replay_root=replay_root,
        include_scratch=args.include_scratch, include_replay=args.include_gate_replay,
        now=time.time(), older_than_seconds=args.older_than_hours * 3600,
        max_items=args.max_items, is_open=path_has_open_files,
        is_locked=replay_is_locked,
        deadline=lambda: time.monotonic() - started >= args.max_seconds,
    )
    plan.clone_count = clone_count
    remaining = max(0.1, args.max_seconds - (time.monotonic() - started))
    estimated = logical_allocated_bytes(plan.removable, remaining)
    result = apply_plan(plan, dry_run=args.dry_run,
                        ledger_path=REPO / "out" / "storage-hygiene.jsonl",
                        record_finding=_record_finding, disk_used=used,
                        eligible_logical_bytes=estimated,
                        is_open=path_has_open_files, is_locked=replay_is_locked,
                        deadline=lambda: time.monotonic() - started >= args.max_seconds,
                        older_than_seconds=args.older_than_hours * 3600)
    action = "would remove" if args.dry_run else "removed"
    print(row)
    size = ("unknown logical allocated size" if estimated is None
            else f"{estimated / 1e9:.3f} GB logical allocated size")
    print(f"storage hygiene: {action} {len(plan.removable) if args.dry_run else result.removed_count} "
          f"director{'y' if (len(plan.removable) if args.dry_run else result.removed_count) == 1 else 'ies'}; "
          f"total={size}; "
          f"protected={len(plan.protected)} unrecognized={plan.unrecognized_count} "
          f"stop={plan.stop_reason or 'complete'} record={result.record_status}")
    print(PHYSICAL_ESTIMATE_METHOD)
    for candidate in plan.removable:
        print(f"  {candidate.kind}: {candidate.path}")
    for path in sorted(plan.unrecognized):
        print(f"  unrecognized sample, report only: {path}")
    return int(result.record_status == "record-failed")


if __name__ == "__main__":
    raise SystemExit(main())
