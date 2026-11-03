"""Extract bounded, unverified signature observations from captured text."""
import re
from dataclasses import dataclass

VERSION = 'email-contact-candidates/v1'
LABEL = re.compile(r'^(name|company|title|phone|mobile|email):\s*(\S.*?)\s*$', re.I)
EMAIL = re.compile(r'^[^\s@<>]+@[^\s@<>]+\.[^\s@<>]+$')
PHONE = re.compile(r'^\+?[\d () .-]{7,30}$')
HISTORY = re.compile(
    r'^[ \t]*(?:>|on\s+[\s\S]*?\bwrote:|from:|sent:|subject:|-{5,}|'
    r'(?:begin\s+)?forwarded\s+message\b|confidential|disclaimer)', re.I | re.M)
ADDRESS = re.compile(r'[^\s@<>]+@[^\s@<>]+\.[^\s@<>]+')


@dataclass(frozen=True)
class MessageSections:
    current: tuple[str, ...]
    history: tuple[str, ...]

    @classmethod
    def parse(cls, body):
        boundary = HISTORY.search(body)
        offset = boundary.start() if boundary else len(body)
        return cls(tuple(body[:offset].splitlines()), tuple(body[offset:].splitlines()))


@dataclass(frozen=True)
class SignatureBlock:
    labels: tuple[str, ...]
    identity_lines: tuple[str, ...]

    @classmethod
    def parse(cls, lines):
        block = lines[:next((i for i, line in enumerate(lines) if not line.strip()), len(lines))]
        end = next((i for i, line in enumerate(block) if not LABEL.fullmatch(line.strip())), len(block))
        return cls(tuple(block[:end]), tuple(block[end:]))


def extract_candidates(body, sender, source_ref, *, complete):
    result = {'version': VERSION, 'identity_observations': [], 'contact_facts': [], 'ambiguity': None}
    def reject(reason):
        result['ambiguity'] = reason
        return result
    if not complete or not isinstance(body, str) or len(body) > 100000:
        return reject('incomplete_or_oversized')
    if not isinstance(sender, str) or not EMAIL.fullmatch(sender) or not source_ref:
        return reject('missing_sender_or_source')
    message = MessageSections.parse(body)
    lines = message.current
    delimiters = [i for i, line in enumerate(lines) if line == '-- ' or line == '--']
    if len(delimiters) != 1:
        return reject('quoted_or_forwarded' if message.history else 'signature_boundary')
    start = delimiters[0]
    region = lines[start + 1:]
    if len(region) > 20 or sum(map(len, region)) > 2000:
        return reject('signature_too_large')
    signature = SignatureBlock.parse(region)
    addresses = ADDRESS.findall('\n'.join(signature.identity_lines))
    if any(address.casefold() != sender.casefold() for address in addresses):
        return reject('competing_identity')
    fields = {}
    for line in signature.labels:
        match = LABEL.fullmatch(line.strip())
        field, value = match.group(1).lower(), match.group(2)
        if field in fields:
            return reject('conflicting_fields')
        if field == 'email' and not EMAIL.fullmatch(value):
            return reject('invalid_email')
        if field in ('phone', 'mobile') and (not PHONE.fullmatch(value) or sum(c.isdigit() for c in value) < 7):
            return reject('invalid_phone')
        fields[field] = (value, line)
    if 'email' not in fields or fields['email'][0].casefold() != sender.casefold():
        return reject('sender_binding')
    for field, (value, evidence) in fields.items():
        item = {'field': field, 'value': value, 'evidence': evidence, 'source_ref': source_ref,
                'sender': sender, 'extraction_version': VERSION, 'verified': False}
        key = 'identity_observations' if field in ('name', 'company') else 'contact_facts'
        result[key].append(item)
    return result
