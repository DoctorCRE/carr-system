"""Start an agent task for business work from a scheduled job.

A job such as the Outlook intake names a record, an action and short
instructions. This module queues that as one task for the record-layer desk
through `dispatch.py send --brief business`, the same door every dispatch uses.
The desk reads the record itself, so instructions should say what to do and
leave the client details in CARR.

The card id is uuid5 over action, record and source, so a retried run is
refused as a duplicate instead of starting the work twice. A Run caps how many
tasks one job run may start. Nothing here raises: every outcome is a Receipt.
"""
from __future__ import annotations

from dataclasses import dataclass, field
import json
from pathlib import Path
import subprocess
import sys
import threading
import unicodedata
from typing import Callable, Literal
import uuid

DISPATCH = Path(__file__).resolve().parents[1] / 'tools' / 'room-bridge' / 'dispatch.py'
DESK = 'business'
ACTIONS = {
    'draft_reply': 'Draft reply',
    'pull_comps': 'Pull comps',
    'client_packet': 'Client packet',
    'research_contact': 'Research contact',
}


@dataclass(frozen=True)
class Receipt:
    status: Literal['queued', 'duplicate', 'capped', 'refused']
    card_id: str
    detail: str = ''


def card_id(action: str, record_ref: str, source_id: str) -> str:
    return 'biz:' + str(uuid.uuid5(uuid.NAMESPACE_URL, json.dumps(['biz', action, record_ref, source_id])))


def brief(action: str, record_ref: str, source_id: str, instructions: str) -> str:
    return f'{ACTIONS[action]} for record {record_ref} (source {source_id}).\n\n{instructions.strip()}'


@dataclass
class Run:
    cap: int = 3
    desk: str = DESK
    actor: str = 'business-dispatch'
    run: Callable[..., subprocess.CompletedProcess] = subprocess.run
    queued: list[str] = field(default_factory=list)
    _lock: threading.Lock = field(default_factory=threading.Lock, init=False, repr=False)

    def enqueue(self, record_ref: str, action: str, instructions: str, source_id: str) -> Receipt:
        with self._lock:
            return self._enqueue(record_ref, action, instructions, source_id)

    def _enqueue(self, record_ref: str, action: str, instructions: str, source_id: str) -> Receipt:
        fields = ((record_ref, 1000), (action, 64), (instructions, 32000),
                  (source_id, 1000), (self.desk, 128), (self.actor, 128))
        for index, (value, limit) in enumerate(fields):
            if (not isinstance(value, str) or not value.strip() or len(value) > limit
                    or any(unicodedata.category(c) in ('Cc', 'Cs')
                           and not (index == 2 and c in '\n\t') for c in value)):
                return Receipt('refused', '', 'inputs must be bounded nonempty text without control characters')
        if type(self.cap) is not int or self.cap < 0:
            return Receipt('refused', '', 'cap must be a nonnegative integer')
        if action not in ACTIONS:
            return Receipt('refused', '', f'unknown action {action!r}; one of {", ".join(ACTIONS)}')
        card = card_id(action, record_ref, source_id)
        if len(self.queued) >= self.cap:
            return Receipt('capped', card, f'this run already queued {self.cap}')
        argv = [sys.executable, str(DISPATCH), 'send', self.desk, '-', '--brief', 'business',
                '--card-id', card, '--actor', self.actor, '--title', ACTIONS[action],
                '--description', f'{ACTIONS[action]} for record {record_ref}']
        try:
            proc = self.run(argv, input=brief(action, record_ref, source_id, instructions),
                            capture_output=True, text=True, timeout=60)
        except (OSError, subprocess.SubprocessError, ValueError) as exc:
            return Receipt('refused', card, f'dispatch did not run: {type(exc).__name__}')
        if proc.returncode == 0:
            self.queued.append(card)
            return Receipt('queued', card)
        if 'refused (no_record_layer_desk)' in proc.stderr:
            return Receipt('refused', card, 'no_record_layer_desk')
        if 'card already exists' in proc.stderr:
            return Receipt('duplicate', card)
        return Receipt('refused', card, proc.stderr.strip()[-500:])
