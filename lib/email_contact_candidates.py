"""Extract bounded, unverified signature observations from captured text."""
import re
from dataclasses import dataclass
from html.parser import HTMLParser

VERSION = 'email-contact-candidates/v2'
LABEL = re.compile(r'^(name|company|title|phone|mobile|email)\s*:\s*(\S.*?)\s*$', re.I)
ATOM = r"[a-z0-9!#$%&'*+/=?^_`{|}~-]+"
DOMAIN_LABEL = r'[a-z0-9](?:[a-z0-9-]*[a-z0-9])?'
EMAIL = re.compile(ATOM + r'(?:\.' + ATOM + r')*@' + DOMAIN_LABEL + r'(?:\.' + DOMAIN_LABEL + r')+', re.I)
PHONE = re.compile(r'^\+?[\d () .-]{7,30}$')
HISTORY = re.compile(
    r'^[ \t]*(?:>|on\s+[\s\S]*?\bwrote:|from:|sent:|subject:|-{5,}|'
    r'(?:begin\s+)?forwarded\s+message\b)', re.I | re.M)
ADDRESS = re.compile(r"(?<![\w.!#$%&'*+/=?^_`{|}~@-])" + EMAIL.pattern
                     + r"(?![\w.!#$%&'*+/=?^_`{|}~@-])", re.I)
DISPLAY_ADDRESS = re.compile(r'([^<>]+)\s*<([^<>]+)>')


class _SignatureHTML(HTMLParser):
    BREAKS = {'br', 'p', 'div', 'tr', 'li', 'table', 'blockquote', 'hr'}
    SKIP = {'head', 'style', 'script'}

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts = []
        self.skipped = []

    def handle_starttag(self, tag, attrs):
        if tag in self.SKIP:
            self.skipped.append(tag)
        if not self.skipped and tag in self.BREAKS:
            self.parts.append('\n')
        if not self.skipped and tag == 'blockquote':
            self.parts.append('> quoted\n')

    def handle_startendtag(self, tag, attrs):
        self.handle_starttag(tag, attrs)
        self.handle_endtag(tag)

    def handle_endtag(self, tag):
        if self.skipped:
            if tag == self.skipped[-1]:
                self.skipped.pop()
            return
        if tag in self.BREAKS:
            self.parts.append('\n')

    def handle_data(self, data):
        if not self.skipped:
            self.parts.append(data)


def signature_text(html):
    """Restore private signature layout without changing router or activity text."""
    if not isinstance(html, str) or len(html) > 1_000_000:
        return ''
    parser = _SignatureHTML()
    parser.feed(html)
    return '\n'.join(' '.join(line.split()) for line in ''.join(parser.parts).splitlines()).strip()


CONTACT_PHONE = re.compile(r'^(?:(phone|tel|office|direct|mobile|cell|m|o|t|p)\s*[:.]?\s*)?'
                           r'(\+?[\d () .-]{7,30})$', re.I)


def _contact_line(line):
    """Only whole contact lines qualify, never prose containing contact tokens."""
    fields = {}
    remainder = line.strip()
    addresses = list(ADDRESS.finditer(remainder))
    if addresses:
        if len(addresses) != 1:
            return None
        address = addresses[0]
        fields['email'] = (address.group(), line)
        remainder = (remainder[:address.start()] + remainder[address.end():]).strip(' |')
        remainder = re.sub(r'^(?:email|e)\s*:\s*', '', remainder, flags=re.I).strip(' |')
    if remainder:
        phone = CONTACT_PHONE.fullmatch(remainder)
        if not phone or not 7 <= sum(c.isdigit() for c in phone[2]) <= 15:
            return None
        field = 'mobile' if (phone[1] or '').lower() in ('mobile', 'cell', 'm') else 'phone'
        fields[field] = (phone[2].strip(), line)
    return fields or None


def _undelimited_fields(lines, sender):
    text = '\n'.join(lines)
    if re.search(r'^\s*assistant(?: contact)?\s*:', text, re.I | re.M):
        return {}, 'competing_identity'
    if text.count('@') != len(ADDRESS.findall(text)):
        return {}, 'invalid_identity'
    if any(address.casefold() != sender.casefold()
           for line in lines for address in ADDRESS.findall(line)):
        return {}, 'competing_identity'
    nonempty = [line for line in lines if line.strip()]
    runs, run = [], []
    for index, line in enumerate(nonempty + ['']):
        parsed = _contact_line(line)
        if parsed:
            run.append((index, line, parsed))
        elif run:
            runs.append(run)
            run = []
    candidates = [run for run in runs if any('email' in f and f['email'][0].casefold() == sender.casefold()
                                           for _, _, f in run)]
    if len(candidates) != 1:
        return {}, 'signature_boundary'
    region = candidates[0]
    if region[-1][0] < len(nonempty) - 12:
        return {}, 'signature_boundary'
    if len(region) > 6 or sum(len(line) for _, line, _ in region) > 1000:
        return {}, 'signature_too_large'
    fields = {}
    for _, _, parsed in region:
        for field, value in parsed.items():
            if field in fields:
                return {}, 'conflicting_fields'
            fields[field] = value
    if not ({'phone', 'mobile'} & fields.keys()):
        return {}, 'signature_boundary'
    return fields, None


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
class SignatureEntity:
    lines: tuple[str, ...]

    def fields_for(self, sender):
        text = '\n'.join(self.lines)
        addresses = ADDRESS.findall(text)
        if any(address.casefold() != sender.casefold() for address in addresses):
            return {}, 'competing_identity'
        if text.count('@') != len(addresses):
            return {}, 'invalid_identity'
        fields = {}
        names = set()
        for line in self.lines:
            value_line = line.strip()
            if not value_line:
                continue
            match = LABEL.fullmatch(value_line)
            value = match.group(2) if match else value_line
            display = DISPLAY_ADDRESS.fullmatch(value)
            names.update(identity.group(1).strip().casefold()
                         for identity in DISPLAY_ADDRESS.finditer(value))
            if match:
                field = match.group(1).lower()
                if field in fields:
                    return {}, 'conflicting_fields'
                if field == 'email' and not EMAIL.fullmatch(value):
                    return {}, 'invalid_email'
                if field in ('phone', 'mobile') and (not PHONE.fullmatch(value) or sum(c.isdigit() for c in value) < 7):
                    return {}, 'invalid_phone'
                if field == 'name':
                    names.add(display.group(1).strip().casefold() if display else value.casefold())
                fields[field] = (value, line)
            elif value_line.casefold() == sender.casefold():
                continue
            elif display:
                continue
            else:
                return {}, 'ambiguous_entity'
        if len(names) > 1:
            return {}, 'competing_identity'
        if 'email' not in fields or fields['email'][0].casefold() != sender.casefold():
            return {}, 'sender_binding'
        return fields, None


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
    if not delimiters:
        fields, ambiguity = _undelimited_fields(lines, sender)
        if ambiguity:
            return reject('quoted_or_forwarded' if message.history else ambiguity)
    elif len(delimiters) == 1:
        region = lines[delimiters[0] + 1:]
        if len(region) > 20 or sum(map(len, region)) > 2000:
            return reject('signature_too_large')
        fields, ambiguity = SignatureEntity(tuple(region)).fields_for(sender)
        if ambiguity:
            return reject(ambiguity)
    else:
        return reject('signature_boundary')
    for field, (value, evidence) in fields.items():
        item = {'field': field, 'value': value, 'evidence': evidence, 'source_ref': source_ref,
                'sender': sender, 'extraction_version': VERSION, 'verified': False}
        key = 'identity_observations' if field in ('name', 'company') else 'contact_facts'
        result[key].append(item)
    return result
