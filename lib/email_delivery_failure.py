"""Conservative parsing of untrusted, complete delivery-status reports."""
from dataclasses import dataclass
import re


@dataclass(frozen=True)
class DeliveryFailure:
    original_id: str | None
    recipient: str
    status: str
    diagnostic: str


def message_id(value: str | None) -> str | None:
    value = (value or '').strip()
    if value.startswith('<') and value.endswith('>'):
        value = value[1:-1]
    return value if re.fullmatch(r'[^\s<>@]+@[^\s<>@]+', value) and len(value) <= 998 else None


def parse_report(body: str, *, full: bool, in_reply_to: str | None = None) -> DeliveryFailure | None:
    if not full or len(body) > 65_536 or '\x00' in body:
        return None
    lines = body.strip().splitlines()
    if not lines or not re.fullmatch(r'Reporting-MTA:\s*dns;\s*[^\s;]+', lines[0], re.I):
        return None
    if any(re.match(r'\s*(>|From:|Subject:|[- ]*(?:forwarded|original) message)', line, re.I) for line in lines):
        return None
    fields: dict[str, list[str]] = {}
    for line in lines:
        if ':' in line:
            name, value = line.split(':', 1)
            fields.setdefault(name.lower(), []).append(value.strip())
    required = ('reporting-mta', 'final-recipient', 'action', 'status', 'diagnostic-code')
    if any(len(fields.get(name, [])) != 1 for name in required):
        return None
    if fields['action'][0].lower() != 'failed':
        return None
    status = fields['status'][0]
    diagnostic = fields['diagnostic-code'][0]
    recipient = re.fullmatch(r'rfc822;\s*([^\s<>@]+@[^\s<>@]+)', fields['final-recipient'][0], re.I)
    if (not recipient or not re.fullmatch(r'5\.\d{1,3}\.\d{1,3}', status)
            or not re.match(r'smtp;\s*5\d\d(?:\s|$)', diagnostic, re.I) or len(diagnostic) > 1000):
        return None
    originals = fields.get('original-message-id', [])
    if len(originals) > 1:
        return None
    original = message_id(originals[0]) if originals else None
    reply = message_id(in_reply_to)
    if (originals and not original) or (in_reply_to and not reply) or (original and reply and original != reply):
        return None
    return DeliveryFailure(original or reply, recipient[1].lower(), status, diagnostic)
