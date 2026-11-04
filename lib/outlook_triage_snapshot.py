"""Bounded read-only adapter for private supplied planning snapshots.

No producer is implied. Missing decoder evidence never falls back to UI labels.
"""
from datetime import datetime, timezone
import json
import os
import stat

from lib.outlook_triage_plan import (Observation, ReadState, RoutingContext,
                                    Folder, FolderCatalog, Policy)

MAX_BYTES = 8 * 1024 * 1024
MAX_ROWS = 10000
MAX_AGE = 900


class SnapshotError(ValueError):
    pass


def read_snapshot(path, settings, max_intents, *, now=None):
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    except FileNotFoundError:
        raise SnapshotError('snapshot_unavailable') from None
    except OSError:
        raise SnapshotError('snapshot_invalid') from None
    try:
        with os.fdopen(fd, 'rb') as handle:
            info = os.fstat(handle.fileno())
            if (not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid()
                    or stat.S_IMODE(info.st_mode) != 0o600 or info.st_size > MAX_BYTES):
                raise ValueError
            raw = handle.read(MAX_BYTES + 1)
            if len(raw) > MAX_BYTES:
                raise ValueError
        value = json.loads(raw)
        if value['schema'] != 'outlook-triage-snapshot/v1' or value['account'] != settings.account:
            raise ValueError
        if any(not isinstance(value[key], str) or not value[key]
               for key in ('observed_at', 'routing_version', 'inbox_folder_id')):
            raise ValueError
        observed = datetime.fromisoformat(value['observed_at'])
        age = ((now or datetime.now(timezone.utc)) - observed).total_seconds()
        if not 0 <= age <= MAX_AGE:
            raise SnapshotError('snapshot_stale')
        if not all(isinstance(value[key], str) and value[key] for key in ('snapshot_digest', 'decoder_pin')):
            raise ValueError
        rows = value['observations']
        if not isinstance(rows, list) or len(rows) > MAX_ROWS:
            raise ValueError
        observations = []
        for row in rows:
            if any(row[key] != value[key] for key in ('snapshot_digest', 'decoder_pin', 'observed_at')):
                raise ValueError
            if any(not isinstance(row[key], str) for key in (
                    'account', 'message_id', 'revision', 'source_folder_id')):
                raise ValueError
            if any(row[key] is not None and not isinstance(row[key], str)
                   for key in ('sender', 'subject', 'body')):
                raise ValueError
            if (not isinstance(row['recipients'], list)
                    or any(not isinstance(item, str) for item in row['recipients'])
                    or type(row['content_complete']) is not bool):
                raise ValueError
            observations.append(Observation(**(row | {'read_state': ReadState(row['read_state']),
                                                       'recipients': tuple(row['recipients'])})))
        routing = value['routing']
        if (not isinstance(routing['version'], str) or not isinstance(routing['guide'], str)
                or not isinstance(value['catalog']['version'], str)
                or not isinstance(value['catalog']['folders'], list)
                or len(value['catalog']['folders']) > MAX_ROWS):
            raise ValueError
        context = RoutingContext.from_find_results(
            routing['version'], routing['guide'], routing['find_results'],
            deal_prefix=settings.deal_prefix, vendor_prefix=settings.vendor_prefix,
            complete=routing['complete'])
        catalog = FolderCatalog(value['catalog']['version'], tuple(
            Folder(**row) for row in value['catalog']['folders']))
        if len(catalog.folders) > MAX_ROWS or any(
                not all(isinstance(item, str) for item in (f.account, f.folder_id, f.path))
                for f in catalog.folders):
            raise ValueError
        policy = Policy(settings.account, value['inbox_folder_id'], value['routing_version'],
                        require_read_before_move=False, max_intents=min(max_intents, 150))
        effect = value['effect_state']
        if effect not in ('none', 'uncertain', 'interrupted'):
            raise ValueError
        return tuple(observations), context, catalog, policy, effect
    except SnapshotError:
        raise
    except (OSError, ValueError, TypeError, KeyError, AttributeError, OverflowError, RecursionError):
        raise SnapshotError('snapshot_invalid') from None
