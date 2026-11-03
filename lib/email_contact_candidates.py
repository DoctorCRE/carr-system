"""Extract bounded, unverified signature observations from captured text."""
import re

VERSION = 'email-contact-candidates/v1'
LABEL = re.compile(r'^(name|company|title|phone|mobile|email):\s*(\S.*?)\s*$', re.I)
EMAIL = re.compile(r'^[^\s@<>]+@[^\s@<>]+\.[^\s@<>]+$')
PHONE = re.compile(r'^\+?[\d () .-]{7,30}$')
STOP = re.compile(r'^(?:>|on .+ wrote:|from:|sent:|subject:|-----|(?:begin )?forwarded message|confidential|disclaimer)', re.I)


def extract_candidates(body, sender, source_ref, *, complete):
    result = {'version': VERSION, 'identity_observations': [], 'contact_facts': [], 'ambiguity': None}
    def reject(reason):
        result['ambiguity'] = reason
        return result
    if not complete or not isinstance(body, str) or len(body) > 100000:
        return reject('incomplete_or_oversized')
    if not isinstance(sender, str) or not EMAIL.fullmatch(sender) or not source_ref:
        return reject('missing_sender_or_source')
    lines = body.splitlines()
    delimiters = [i for i, line in enumerate(lines) if line == '-- ' or line == '--']
    if len(delimiters) != 1:
        return reject('signature_boundary')
    start = delimiters[0]
    if any(STOP.match(line.strip()) for line in lines[:start]):
        return reject('quoted_or_forwarded')
    region = lines[start + 1:]
    if len(region) > 20 or sum(map(len, region)) > 2000:
        return reject('signature_too_large')
    fields = {}
    for line in region:
        if STOP.match(line.strip()):
            break
        match = LABEL.fullmatch(line.strip())
        if not match:
            break
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
