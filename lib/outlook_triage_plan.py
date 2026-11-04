"""Pure planning over supplied observations, CARR find snapshots and folder identities.

Intents are private data, not operational receipts or authority to execute. A
future executor must revalidate every bound identity and enforce read policy
atomically. This module never acquires observations or calls CarrFinder.
"""
from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from types import MappingProxyType
from typing import Mapping

from lib.outlook_triage import FolderGuide, RULES, person_name


class ReadState(Enum):
    READ = 'read'
    UNREAD = 'unread'
    UNKNOWN = 'unknown'


@dataclass(frozen=True)
class MessageRef:
    account: str
    message_id: str
    revision: str
    source_folder_id: str
    read_state: ReadState
    snapshot_digest: str
    decoder_pin: str
    observed_at: str


@dataclass(frozen=True)
class Observation(MessageRef):
    sender: str | None
    subject: str | None
    body: str | None
    recipients: tuple[str, ...] | None
    content_complete: bool

    def reference(self) -> MessageRef:
        return MessageRef(self.account, self.message_id, self.revision, self.source_folder_id,
                          self.read_state, self.snapshot_digest, self.decoder_pin, self.observed_at)


@dataclass(frozen=True)
class Folder:
    account: str
    folder_id: str
    path: str


@dataclass(frozen=True)
class FolderCatalog:
    version: str
    folders: tuple[Folder, ...]


@dataclass(frozen=True)
class RoutingContext:
    version: str
    guide: FolderGuide
    find_results: Mapping[str, Mapping]
    complete: bool

    @classmethod
    def from_find_results(cls, version: str, guide_text: str, results: Mapping,
                          *, deal_prefix: str, vendor_prefix: str, complete: bool) -> RoutingContext:
        """Freeze the fields consumed by the existing find-based routing rules.

        Complete means the caller verified the supplied lookup coverage, including
        the find verb's capped results. An absent query is never an empty result.
        """
        if type(complete) is not bool:
            raise ValueError("invalid_snapshot_completeness")
        frozen = {}
        for query, result in results.items():
            if not isinstance(query, str) or not isinstance(result, Mapping):
                raise ValueError('invalid_find_snapshot')
            rows = {}
            for kind, fields in (('parties', ('name', 'kind', 'ref', 'org_name', 'merged')),
                                 ('deals', ('name', 'client_ref'))):
                values = result.get(kind)
                if not isinstance(values, (list, tuple)):
                    raise ValueError('incomplete_find_snapshot')
                copied = []
                for row in values:
                    if not isinstance(row, Mapping) or any(key not in row for key in fields):
                        raise ValueError('incomplete_find_snapshot')
                    if any(row[key] is not None and not isinstance(row[key], str)
                           for key in fields if key != 'merged'):
                        raise ValueError('invalid_find_snapshot')
                    if kind == 'parties' and type(row['merged']) is not bool:
                        raise ValueError('invalid_find_snapshot')
                    required = ('name', 'kind', 'ref') if kind == 'parties' else ('name', 'client_ref')
                    if any(not row[key] or not row[key].strip() for key in required):
                        raise ValueError('invalid_find_identity')
                    copied.append(MappingProxyType({key: row[key] for key in fields}))
                rows[kind] = tuple(copied)
            frozen[query] = MappingProxyType(rows)
        return cls(version, FolderGuide.parse(guide_text, deal_prefix=deal_prefix,
                                             vendor_prefix=vendor_prefix),
                   MappingProxyType(frozen), complete)


@dataclass(frozen=True)
class Policy:
    account: str
    inbox_folder_id: str
    routing_version: str
    require_read_before_move: bool = True
    max_observations: int = 1000
    max_intents: int = 150

    def __post_init__(self):
        if (not all((self.account, self.inbox_folder_id, self.routing_version))
                or type(self.require_read_before_move) is not bool
                or type(self.max_observations) is not int or self.max_observations < 1
                or type(self.max_intents) is not int or self.max_intents < 1):
            raise ValueError('invalid_policy')


@dataclass(frozen=True)
class MoveIntent:
    observation: MessageRef
    destination: Folder
    rule: str
    routing_version: str
    catalog_version: str
    require_read_before_move: bool


@dataclass(frozen=True)
class LeaveDecision:
    observation: MessageRef
    reason: str


@dataclass(frozen=True)
class Plan:
    moves: tuple[MoveIntent, ...]
    leaves: tuple[LeaveDecision, ...]


def _eligibility(message: Observation, policy: Policy) -> str | None:
    if message.account != policy.account:
        return 'wrong_account'
    if message.source_folder_id != policy.inbox_folder_id:
        return 'not_inbox'
    if not all((message.message_id, message.revision, message.snapshot_digest,
                message.decoder_pin, message.observed_at)):
        return 'identity_unverified'
    try:
        if datetime.fromisoformat(message.observed_at).utcoffset() is None:
            return 'identity_unverified'
    except ValueError:
        return 'identity_unverified'
    if not isinstance(message.read_state, ReadState) or message.read_state is ReadState.UNKNOWN:
        return 'unknown_read_state'
    if policy.require_read_before_move and message.read_state is not ReadState.READ:
        return 'unread'
    if (message.content_complete is not True or not message.sender or not message.sender.strip()
            or message.subject is None or message.body is None or not message.body.strip()
            or not message.recipients or any(not r.strip() for r in message.recipients)):
        return 'incomplete_content'
    return None


def plan_triage(observations: tuple[Observation, ...], routing_context: RoutingContext,
                folder_catalog: FolderCatalog, policy: Policy) -> Plan:
    """Return deterministic, bounded intents without mutating or acquiring data.

    Observation order determines cap priority. Duplicate mailbox/message identities
    are all held, even when identical. Full paths replace the UI's unique-leaf
    restriction; matching semantics and ordered ambiguity come from RULES.
    """
    moves: list[MoveIntent] = []
    leaves: list[LeaveDecision] = []
    identities = Counter((m.account, m.message_id) for m in observations)
    targets: dict[tuple[str, str], list[Folder]] = {}
    folder_ids = Counter((f.account, f.folder_id) for f in folder_catalog.folders)
    for folder in folder_catalog.folders:
        targets.setdefault((folder.account, folder.path), []).append(folder)
    for index, message in enumerate(observations):
        reason = _eligibility(message, policy)
        if index >= policy.max_observations:
            reason = 'observation_cap_reached'
        elif not reason and identities[message.account, message.message_id] > 1:
            reason = 'duplicate_identity'
        elif not reason and (not routing_context.version
                             or routing_context.version != policy.routing_version):
            reason = 'stale_policy'
        elif not reason and (not routing_context.complete or not folder_catalog.version):
            reason = 'routing_unavailable'
        query = person_name(message.sender or '')
        if not reason and query not in routing_context.find_results:
            reason = 'routing_unavailable'
        if reason:
            leaves.append(LeaveDecision(message.reference(), reason))
            continue
        for rule, match in RULES:
            hits = set(match(message.sender or '', routing_context.guide,
                             routing_context.find_results.__getitem__) or ())
            if not hits:
                continue
            if len(hits) != 1:
                reason = f'{rule}_ambiguous'
                break
            candidates = targets.get((policy.account, next(iter(hits))), ())
            if len(candidates) != 1:
                reason = 'destination_unresolved'
                break
            target = candidates[0]
            if (not target.folder_id or not target.path
                    or folder_ids[target.account, target.folder_id] != 1):
                reason = 'destination_unresolved'
            elif target.folder_id == message.source_folder_id:
                reason = 'already_at_destination'
            elif len(moves) >= policy.max_intents:
                reason = 'intent_cap_reached'
            else:
                moves.append(MoveIntent(message.reference(), target, rule, routing_context.version,
                                        folder_catalog.version, policy.require_read_before_move))
            break
        else:
            reason = 'no_rule_matched'
        if reason:
            leaves.append(LeaveDecision(message.reference(), reason))
    return Plan(tuple(moves), tuple(leaves))
