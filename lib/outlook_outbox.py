"""Consume app-submitted Outlook drafts and durably replay delivery receipts."""
from dataclasses import dataclass
from datetime import datetime
import fcntl
import hashlib
import json
import logging
import os
import re
from pathlib import Path
import uuid

from lib import outlook_send as send
from lib.outlook_settings import OutlookSettings, load_settings
from lib.outlook_runtime import receipt_line


@dataclass(frozen=True)
class SubmittedDraft:
    proposal_id: str
    version: int
    sha256: str
    mail: send.Mail

    @classmethod
    def parse(cls, row: dict, settings: OutlookSettings):
        ident = str(uuid.UUID(row['proposal_id']))
        version = row['version']
        subject, body = row['subject'], row['body']
        if (type(version) is not int or version < 1 or not isinstance(subject, str)
                or not subject.strip() or len(subject) > 998 or '\r' in subject or '\n' in subject
                or not isinstance(body, str) or not body.strip() or len(body) > 20000):
            raise ValueError('invalid_submission')
        text_hash = hashlib.sha256(json.dumps(dict(subject=subject, body=body),
            ensure_ascii=False, separators=(',', ':')).encode()).hexdigest()
        if row['sha256'] != text_hash:
            raise ValueError('submission_hash_mismatch')
        to, cc = send._addresses(row['to']), send._addresses(row['cc'])
        approved = row.get('attachments', [])
        if not isinstance(approved, list) or any(not isinstance(a, dict) or set(a) != {'path', 'sha256', 'size'} for a in approved):
            raise ValueError('invalid_submission')
        attached = send.attachments([a['path'] for a in approved])
        if any(a.sha256 != expected['sha256'] or str(a.path) != expected['path']
               or type(expected['size']) is not int or a.size != expected['size']
               for a, expected in zip(attached, approved)):
            raise ValueError('submission_attachment_mismatch')
        mail = send.Mail(to, cc, (), subject, body, attached)
        if not send.partner_copied(mail, settings):
            raise ValueError('submission_recipients_invalid')
        return cls(ident, version, text_hash, mail)

    @property
    def key(self):
        return str(uuid.uuid5(uuid.NAMESPACE_URL, f'outlook-outbox|{self.proposal_id}|{self.version}'))

    def receipt(self, result: send.Result):
        args = dict(idempotency_key=self.key, proposal_id=self.proposal_id,
                    base_version=self.version, sha256=self.sha256)
        if result.state is send.State.DONE and result.sent_item_id and result.sent_at:
            return dict(args, status='sent', sent_item_id=result.sent_item_id,
                        sent_at=result.sent_at, sender_account=send.account())
        reason = result.reason if (result.reason == 'aborted_before_dispatch' or result.reason.startswith('unconfirmed_after_')) else f'{result.state.value}:{result.reason}'
        return dict(args, status='failed', reason=reason)


def _save(path: Path, receipt: dict):
    temp = path.with_suffix('.tmp')
    fd = os.open(temp, os.O_CREAT | os.O_WRONLY | os.O_TRUNC, 0o600)
    with os.fdopen(fd, 'w') as handle:
        os.fchmod(handle.fileno(), 0o600)
        json.dump(receipt, handle)
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(temp, path)
    fd = os.open(path.parent, os.O_RDONLY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def run(settings: OutlookSettings, ports: send.Ports) -> dict[str, int]:
    """One fakeable run. Receipt failures never cause another Outlook send."""
    settings.require_service('outbox')
    if (send._addresses([settings.partner_cc]) != (settings.partner_cc,)
            or not settings.partner_cc or settings.partner_cc == settings.account):
        raise ValueError('partner_copy_required')
    counts = dict(sent=0, failed=0, deferred=0, receipt_failed=0, compose_window_open=0, sent_unrecorded=0, quarantined=0, daily_cap_reached=0, daily_cap_escalated=0)
    ports.out.mkdir(parents=True, exist_ok=True, mode=0o700)
    os.chmod(ports.out, 0o700)
    root = ports.out / 'outbox'
    root.mkdir(parents=True, exist_ok=True, mode=0o700)
    os.chmod(root, 0o700)
    with open(root / 'lock', 'a') as lock:
        os.chmod(root / 'lock', 0o600)
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            counts['deferred'] += 1
            return counts
        pending = set()
        for path in sorted(root.glob('*.json')):
            receipt = json.loads(path.read_text())
            pending.add(receipt['proposal_id'])
            try:
                if receipt.get('status') == 'sent' and not receipt.get('sent_at'):
                    snapshot = ports.mailbox.snapshot()
                    item = next(r for r in snapshot.sent if r.item_id == receipt['sent_item_id']
                                and r.sender.lower() == settings.account)
                    receipt = dict(receipt, sent_at=item.sent_at,
                                   sender_account=settings.account)
                    _save(path, receipt)
                reply = ports.carr('record-email-sent', receipt)
                if reply.get('ok') is not True:
                    raise ValueError('receipt_refused')
            except Exception:
                counts['receipt_failed'] += 1
            else:
                path.unlink()
        box = ports.carr('outbox-for-sender', {})
        if (box.get('schema_version') != 'email-outbox.v1' or box.get('owner') != settings.owner
                or not isinstance(box.get('drafts'), list)):
            raise ValueError('outbox_identity_or_schema_mismatch')
        quarantine = root / 'quarantine'
        quarantine.mkdir(exist_ok=True, mode=0o700)
        os.chmod(quarantine, 0o700)
        for row in box['drafts']:
            row_hash = hashlib.sha256(json.dumps(row, sort_keys=True, ensure_ascii=False,
                separators=(',', ':')).encode()).hexdigest()
            quarantined = quarantine / f'{row_hash}.json'
            if quarantined.exists():
                counts['quarantined'] += 1
                continue
            if isinstance(row, dict) and isinstance(row.get('proposal_id'), str) and row['proposal_id'] in pending:
                continue
            try:
                draft = SubmittedDraft.parse(row, settings)
            except (ValueError, KeyError, TypeError, AttributeError) as exc:
                counts['failed'] += 1
                try:
                    ident = str(uuid.UUID(row['proposal_id']))
                    version, digest = row['version'], row['sha256']
                    if type(version) is not int or version < 1 or not isinstance(digest, str) or not re.fullmatch(r'[a-f0-9]{64}', digest):
                        raise ValueError('invalid_identity')
                except (ValueError, KeyError, TypeError, AttributeError):
                    _save(quarantined, dict(row_sha256=row_hash, reason='invalid_submission'))
                    counts['quarantined'] += 1
                    continue
                key = str(uuid.uuid5(uuid.NAMESPACE_URL, f'outlook-outbox|{ident}|{version}'))
                reason = str(exc) if str(exc) in (
                    'invalid_submission', 'submission_hash_mismatch', 'submission_recipients_invalid',
                    'submission_attachment_mismatch', 'attachment_settings_missing', 'attachment_root_missing',
                    'attachment_path_not_absolute', 'attachment_missing', 'attachment_outside_root',
                    'attachment_not_regular_file', 'attachment_unreadable', 'attachments_too_large') else 'invalid_submission'
                receipt = dict(idempotency_key=key, proposal_id=ident, base_version=version,
                               sha256=digest, status='failed', reason=reason)
            else:
                result = send.send_submitted(draft, ports=ports)
                if result.state in (send.State.PRESSED_UNCONFIRMED, send.State.WINDOW_NOT_CLOSED):
                    counts['failed'] += 1
                    if result.reason == 'sent_time_unavailable':
                        counts['sent_unrecorded'] += 1
                        logging.getLogger(__name__).warning('outlook-outbox sent_time_unavailable')
                    continue
                if (result.state is send.State.NOT_IDLE
                        or result.reason in ('daily_cap', 'another_write_running', 'compose_window_open', 'screen_locked')):
                    counts['deferred'] += 1
                    if result.reason == 'daily_cap':
                        counts['daily_cap_reached'] += 1
                    if result.reason == 'compose_window_open':
                        counts['compose_window_open'] += 1
                    continue
                receipt = draft.receipt(result)
                key = draft.key
                counts[receipt['status']] += 1
            path = root / f'{key}.json'
            _save(path, receipt)
            try:
                reply = ports.carr('record-email-sent', receipt)
                if reply.get('ok') is not True:
                    raise ValueError('receipt_refused')
            except Exception:
                counts['receipt_failed'] += 1
            else:
                path.unlink()
        cap_path = root / 'daily-cap-state'
        today = datetime.fromtimestamp(ports.now(), settings.timezone).date().isoformat()
        previous = json.loads(cap_path.read_text()) if cap_path.exists() else {}
        if counts['daily_cap_reached']:
            escalated = previous.get('escalated', False) or bool(previous.get('date') and previous['date'] != today)
            _save(cap_path, dict(date=today, escalated=escalated))
            if escalated:
                counts['daily_cap_escalated'] = 1
                counts['failed'] += 1
        else:
            cap_path.unlink(missing_ok=True)
    return counts


def main() -> int:
    repo = Path(__file__).resolve().parents[1]
    counts = {}
    failed, reason = True, 'setup_failed'
    try:
        settings = load_settings()
        settings.require_repo(repo)
        repo = settings.repo
        counts = run(settings, send.default_ports())
        print('outlook-outbox ' + ' '.join(f'{key}={value}' for key, value in counts.items()))
        failed = bool(counts['failed'] or counts['receipt_failed'])
        reason = 'daily_cap_reached' if counts.get('daily_cap_escalated') else ('send_failed' if counts['failed'] else ('receipt_failed' if counts['receipt_failed']
                  else ('deferred' if counts['deferred'] else 'processed')))
    except Exception as exc:
        print(f'outlook-outbox failed {type(exc).__name__}')
    try:
        print(receipt_line('outlook-outbox', 'failed' if failed else ('skipped' if counts.get('deferred') else 'succeeded'), reason, counts, repo))
    except Exception:
        pass
    return 1 if failed else (78 if counts.get('deferred') else 0)


if __name__ == '__main__':
    raise SystemExit(main())
