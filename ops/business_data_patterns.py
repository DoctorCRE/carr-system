"""business_data_patterns.py — the one judgment of "does this text carry CARR
business, client, deal, person or secret content".

WHY THIS EXISTS. jbookout/carr-system is a PUBLIC repository. The replay
fixtures under ops/fixtures/real-replay/ are extracted from local session
transcripts that also hold client and deal work. This module is the ONE place
the leak judgment lives, so tools/extract-real-replay.py (which drops a whole
record on any hit) and ops/gate-replay.py (which scans every committed fixture
and the verdict snapshot on every CI run) can never drift apart.

IT IS A LIBRARY: no shebang and no entrypoint guard, for the reason
ops/typesafe_client.py spells out.

DELIBERATELY BROAD. A false positive costs the extractor one fixture row. A
false negative is a public leak. Every pattern below is tuned in that direction,
and ops/gate-replay-selftest.py plants one leak per pattern (including every
miss the 2026-09-24 Opus review found in the first version) and requires each
to be caught.

HOW TEXT IS SCANNED. `find_matches` takes one string. `scan_value` walks a
decoded JSON value and scans every KEY and every string VALUE on its own, so a
person name used as a JSON key, or a phrase that only reads as a leak once the
JSON escaping (a literal backslash-n before "Dr") is removed, is still seen.
Scanning only the raw serialized line was one of the misses.
"""
from __future__ import annotations

import re
from typing import Any, Iterable, List, Set, Tuple

# The one UUID that may appear in a fixture: extraction rewrites every real
# session id to this value, and the replay feeds the same value to the gates.
REPLAY_SESSION_ID = "00000000-0000-4000-8000-000000000001"
ALLOWED_UUIDS = frozenset({REPLAY_SESSION_ID})

EMAIL_RE = re.compile(r"(?i)[a-z0-9._%+-]+@[a-z0-9.-]+\.[a-z]{2,}")

URL_HOSTNAME_RE = re.compile(r"(?i)\b(?:https?|ssh|git|ftp|wss?)://[a-z0-9.-]+\.[a-z]{2,}")
BARE_HOSTNAME_RE = re.compile(
    r"(?i)(?<![\w/.-])(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+"
    r"(?:com|net|org|gov|edu|io|ai|us|co|dev|app|cloud|health|dental|med)\b"
)
# scheme://user:password@host — any scheme, including postgres, mysql, redis,
# mongodb+srv and amqp. The password is whatever sits between ':' and '@'.
CREDENTIAL_URL_RE = re.compile(r"(?i)\b[a-z][a-z0-9+.-]*://[^\s:/@'\"]+:[^\s@'\"/]+@")

SECRET_MARKER_RE = re.compile(
    r"(?i)\b(?:api[_-]?key|apikey|token|secret|password|passwd|pwd|bearer|authorization)\b"
    r"\s*[:=]\s*['\"]?[A-Za-z0-9_\-./+]{12,}"
    r"|(?i:\bbearer\s+)[A-Za-z0-9_\-./+]{12,}"
)
# Vendor token shapes that carry no marker word at all.
SECRET_TOKEN_RE = re.compile(
    r"\b(?:sk|rk|pk)_(?:live|test)_[A-Za-z0-9]{8,}"      # Stripe
    r"|\bsk-(?:ant-|proj-)?[A-Za-z0-9_-]{20,}"           # Anthropic / OpenAI
    r"|\bgh[pousr]_[A-Za-z0-9]{20,}"                     # GitHub tokens
    r"|\bgithub_pat_[A-Za-z0-9_]{20,}"
    r"|\b(?:AKIA|ASIA)[0-9A-Z]{16}\b"                    # AWS access key id
    r"|\bxox[abprs]-[A-Za-z0-9-]{10,}"                   # Slack
    r"|\bAIza[0-9A-Za-z_-]{30,}"                         # Google API key
    r"|\beyJ[A-Za-z0-9_-]{10,}\.eyJ[A-Za-z0-9_-]{10,}"   # JWT
    r"|-----BEGIN [A-Z ]*PRIVATE KEY-----"
)

# Money. "$18.50", "$ 1,200", "$/SF", "1.2M", "450k/yr", "2.5 million",
# "USD 4,000", "4,000 USD".
DOLLAR_RE = re.compile(r"\$\s?\d[\d,]*(?:\.\d+)?|\$\s?/\s?SF\b")
MONEY_SHORTHAND_RE = re.compile(
    # "1.2M", "3MM", "2B", "450k", "450K/yr". Lowercase m and b are left out on
    # purpose: they are durations and sizes in shell ("sleep 5m", "1b").
    r"\b\d+(?:\.\d+)?\s?(?:MM|M|BN|B|K|k)\b(?:\s?(?:/|per\s)\s?(?i:yr|year|mo|month|sf|annum))?"
    r"|(?i:\b\d+(?:\.\d+)?\s?(?:million|billion|thousand)\b)"
    r"|(?i:\b\d[\d,]*(?:\.\d+)?\s?(?:/|per\s)\s?(?:yr|year|mo|month|annum)\b)"
)
CURRENCY_CODE_RE = re.compile(r"\bUSD\b|\bUS\$|(?i:\b\d[\d,.]*\s?(?:usd|dollars?)\b)")
SQFT_RE = re.compile(r"(?i)\b\d[\d,.]*\s?(?:sq\.?\s?ft\.?|square\s+f(?:ee|oo)t|sf|rsf|usf)\b")

_STREET_WORDS = (
    r"st|street|ave|avenue|blvd|boulevard|rd|road|dr|drive|ln|lane|way|ct|court|"
    r"pkwy|parkway|hwy|highway|suite|ste|pl|place|cir|circle|ter|terrace|trl|trail|"
    r"loop|sq|square|pike|row"
)
# Any case: "1204 Oak Street" and "1204 oak st" are the same leak.
ADDRESS_RE = re.compile(
    r"(?i)\b\d{1,6}\s+(?:[a-z][a-z'.-]*\s+){1,4}(?:" + _STREET_WORDS + r")\b\.?"
)
PO_BOX_RE = re.compile(r"(?i)\bp\.?\s?o\.?\s+box\s+\d+|\b(?:suite|ste\.?)\s*#?\d+")
ZIP_STATE_RE = re.compile(r"\b(?:AL|FL|GA|MS|LA|TN|TX)\s+\d{5}(?:-\d{4})?\b")

# Practices and their naming: honorifics with or without the period, dental and
# medical credentials, and specialty words that name a practice
# ("<Name> Orthodontics").
# Title case or all caps, with or without the period ("Dr Smith", "DR SMITH",
# "Dr. Smith"), and lowercase only WITH the period ("dr. smith"): a bare
# lowercase "ms" is milliseconds far more often than it is a title.
HONORIFIC_NAME_RE = re.compile(
    r"\b(?:Dr|DR|Mr|MR|Mrs|MRS|Ms|MS|Mx|Prof|PROF|Doctor|DOCTOR)\.?\s+[A-Za-z][a-zA-Z'-]+"
    r"|\b(?:dr|mr|mrs|ms|prof)\.\s*[a-z][a-z'-]+"
)
CREDENTIAL_RE = re.compile(r"\b(?:DDS|DMD|MD|DVM|DPM|PA-C|RDH|MSD|FACS|FAAP)\b")
PRACTICE_WORD_RE = re.compile(
    r"(?i)\b(?:clinic|clinics|dental|dentistry|dentist|orthodontics?|orthodontist|"
    r"periodontics?|endodontics?|prosthodontics?|pediatrics?|pediatric|dermatology|"
    r"chiropractic|chiropractor|optometry|optometrist|ophthalmology|veterinary|"
    r"physical\s+therapy|urgent\s+care|oral\s+surgery|surgery\s+center|medical\s+group|"
    r"family\s+medicine|practice|practices|patients?|landlord|tenant|lessee|lessor|"
    r"broker|brokerage|loi|letter\s+of\s+intent)\b"
)
LEASE_TERM_RE = re.compile(
    r"(?i)\b(?:NNN|triple\s+net|CAM(?:\s+charges?)?|TI\s+allowance|tenant\s+improvements?|"
    r"cap\s+rate|base\s+rent|lease\s+term|lease\s+rate|rent\s+abatement|free\s+rent|"
    r"rent\s+escalations?|renewal\s+option|square\s+footage|asking\s+rent|commission)\b"
)

# Person names. A regex cannot know every name, so this is the broad net: a
# common given name followed by a word that could be a surname, in ANY case, so
# "Sarah Jones", "sarah jones" and {"SARAH JONES": ...} all hit. The given-name
# list is ordinary US census vocabulary, not anyone's data. English words that
# are also given names (will, mark, bill, grant, rich, art, max, ...) are left
# out on purpose: including them would drop most real commands.
_GIVEN_NAMES = (
    "aaron adam alan albert alex alexander alice alicia allison amanda amber amy "
    "andrea andrew angela anna anne anthony antonio ashley barbara benjamin betty "
    "beverly brandon brenda brian brittany bruce bryan carl carlos carol caroline "
    "catherine charles cheryl christina christine christopher cynthia daniel "
    "danielle david deborah debra denise dennis diana diane donald donna dorothy "
    "douglas dylan edward elizabeth emily emma eric ethan evelyn frances frank "
    "gary george gloria gregory hannah harold heather helen henry jacob jacqueline "
    "james janet janice jason jeffrey jennifer jeremy jerry jessica joan john "
    "jonathan jose joseph joshua joyce juan judith judy julia julie justin karen "
    "katherine kathleen kathryn kayla keith kelly kenneth kevin kimberly kyle "
    "larry laura lauren linda lisa logan lori madison margaret maria marie marilyn "
    "martha mary matthew megan melissa michael michelle nancy natalie nathan "
    "nicholas nicole olivia pamela patricia patrick paul peter philip rachel "
    "ralph raymond rebecca richard robert roger ronald rose russell ruth ryan "
    "samantha samuel sandra sara sarah scott sean sharon shirley sophia stephanie "
    "stephen steven susan teresa terry theresa thomas timothy tyler victoria "
    "vincent virginia walter wayne william zachary"
).split()
# NOT listed: the two partners' own first names. Both already appear throughout
# this public tree (CLAUDE.md, hook docstrings, doctrine citations), and every
# delivered-rule receipt quotes doctrine that names them, so listing them would
# drop every such receipt without keeping anything private. A client's name
# that is not on this list is the known limit of any name list, which is why
# prose-shaped records (agent prompts, user prompts, verb inputs) are never
# extracted from real transcripts at all.
PERSON_NAME_RE = re.compile(
    r"(?i)\b(?:" + "|".join(_GIVEN_NAMES) + r")\s+(?!(?:and|or|the|to|of|in|on|at|is|was|"
    r"has|had|for|with|from|said|says|asked|wants|will|would|can|should|may|who|that|"
    r"this|it|he|she|we|they|you|i|a|an|as|by|if|so|but|not|no|yes)\b)[a-z][a-z'-]{2,}\b"
)

PHONE_RE = re.compile(r"(?<!\d)(?:\+?1[-.\s]?)?\(?\d{3}\)?[-.\s]\d{3}[-.\s]\d{4}(?!\d)")
SSN_RE = re.compile(r"(?<!\d)\d{3}-\d{2}-\d{4}(?!\d)")
UUID_RE = re.compile(
    r"(?i)\b[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\b"
)
CLIENT_REF_RE = re.compile(r"(?i)\b[LCVD]-\d{2,}\b")
# Any IPv4 address except loopback and the unspecified address: a tailnet or
# LAN address names a partner's machine as surely as a hostname does.
IPV4_RE = re.compile(
    r"(?<![\d.])(?!127\.)(?!0\.0\.0\.0(?![\d.]))(?:25[0-5]|2[0-4]\d|1?\d?\d)"
    r"(?:\.(?:25[0-5]|2[0-4]\d|1?\d?\d)){3}(?![\d.])"
)
# A path into the places client documents live: the Drive vault, Downloads,
# Desktop, Documents. The file NAME there is routinely a client, a property or a
# report ("~/Downloads/<Client> <Vendor> Report.pdf"), and no name list can
# recognise it, so the whole path shape counts.
PERSONAL_PATH_RE = re.compile(
    r"(?i)(?:~|\{\{HOME\}\}|/Users/[^/\s\"']+|/home/[^/\s\"']+)/"
    r"(?:Downloads|Desktop|Documents|Pictures|Movies|Library/CloudStorage|My Drive)\b"
    r"|\bCARR AI/|\bMy Drive/"
)

# name -> compiled pattern, in report order.
PATTERNS = {
    "dollar_amount": DOLLAR_RE,
    "money_shorthand": MONEY_SHORTHAND_RE,
    "currency_code": CURRENCY_CODE_RE,
    "sqft": SQFT_RE,
    "street_address": ADDRESS_RE,
    "po_box": PO_BOX_RE,
    "state_zip": ZIP_STATE_RE,
    "honorific_name": HONORIFIC_NAME_RE,
    "credential": CREDENTIAL_RE,
    "practice_word": PRACTICE_WORD_RE,
    "lease_term": LEASE_TERM_RE,
    "person_name": PERSON_NAME_RE,
    "phone": PHONE_RE,
    "ssn": SSN_RE,
    "email": EMAIL_RE,
    "url_hostname": URL_HOSTNAME_RE,
    "bare_hostname": BARE_HOSTNAME_RE,
    "credential_url": CREDENTIAL_URL_RE,
    "secret": SECRET_MARKER_RE,
    "secret_token": SECRET_TOKEN_RE,
    "uuid": UUID_RE,
    "client_ref": CLIENT_REF_RE,
    "personal_file_path": PERSONAL_PATH_RE,
    "ip_address": IPV4_RE,
}


# ------------------------------------------------------------------ local roster
#
# THE REGEXES ABOVE CANNOT KNOW A NAME. A name without a title, a credential or
# a practice word matches no shape. Where a local roster file exists, every
# string is also checked against it; it is cheap, and where none exists the
# shape patterns above are the whole check.
#
# The roster is a local, untracked file, one name per line, `#` comments
# allowed, first found wins: $CARR_CLIENT_ROSTER, out/client-roster.local.txt
# in this checkout, then ~/carr-system/out/client-roster.local.txt. Nothing
# derived from it is committed.
#
# Text is split on camelCase before matching, and only [a-z0-9] runs count as
# words, so a listed name hides behind neither case, punctuation, a hyphen, an
# underscore, a path, nor running its words together. Names shorter than
# MIN_NAME_CHARS are ignored.

import functools  # noqa: E402
import os  # noqa: E402
from pathlib import Path  # noqa: E402
from typing import Optional  # noqa: E402

_REPO = Path(__file__).resolve().parent.parent
ROSTER_ENV = "CARR_CLIENT_ROSTER"
LOCAL_ROSTER = Path("out") / "client-roster.local.txt"
_CAMEL = re.compile(r"(?<=[a-z])(?=[A-Z])")
_WORD = re.compile(r"[a-z0-9]+")
MIN_NAME_CHARS = 4


def name_tokens(text: str) -> List[str]:
    return _WORD.findall(_CAMEL.sub(" ", text).lower().replace("'", "").replace("’", ""))


def roster_candidates() -> List[Path]:
    override = os.environ.get(ROSTER_ENV)
    paths = [Path(override)] if override else []
    return paths + [_REPO / LOCAL_ROSTER, Path.home() / "carr-system" / LOCAL_ROSTER]


class Roster:
    """A local name list: `hits(text)` is a bool; `describe()` never names a name."""

    def __init__(self, names: Iterable[str]) -> None:
        self.full: Set[str] = set()
        for name in names:
            key = " ".join(name_tokens(name))
            if len(key) >= MIN_NAME_CHARS:
                self.full.add(key)
        self.lengths = sorted({len(k.split()) for k in self.full})

    def hits(self, text: str) -> bool:
        tokens = name_tokens(text)
        for n in self.lengths:
            for i in range(len(tokens) - n + 1):
                if " ".join(tokens[i:i + n]) in self.full:
                    return True
        return False

    def describe(self) -> str:
        return f"local roster ({len(self.full)} names)"


def read_roster(path: Path) -> Roster:
    lines = path.read_text(encoding="utf-8").splitlines()
    return Roster(l.strip() for l in lines if l.strip() and not l.lstrip().startswith("#"))


def load_roster(candidates: Optional[Iterable[Path]] = None) -> Optional[Roster]:
    for path in roster_candidates() if candidates is None else candidates:
        if path.is_file():
            return read_roster(path)
    return None


@functools.lru_cache(maxsize=1)
def roster() -> Optional[Roster]:
    """The local roster, or None where this machine has none."""
    return load_roster()


def find_matches(text: str, *, exempt_uuids: Iterable[str] = ALLOWED_UUIDS) -> List[str]:
    """Names of every pattern that matches `text`, in PATTERNS order.

    `exempt_uuids` is the narrow, explicit exemption for UUIDs known not to be
    business data (by default only REPLAY_SESSION_ID). Every OTHER UUID-shaped
    token still counts.
    """
    if not isinstance(text, str) or not text:
        return []
    exempt = {u.lower() for u in exempt_uuids}
    hits = []
    for name, pattern in PATTERNS.items():
        if name == "uuid":
            if any(m.lower() not in exempt for m in pattern.findall(text)):
                hits.append(name)
            continue
        if pattern.search(text):
            hits.append(name)
    names = roster()
    if names is not None and names.hits(text):
        hits.append("roster_name")
    return hits


def scan_value(value: Any, *, exempt_uuids: Iterable[str] = ALLOWED_UUIDS) -> List[Tuple[str, str]]:
    """Every (pattern, where) hit inside a decoded JSON value.

    Keys and string values are scanned one by one, decoded, so neither JSON
    escaping nor a leak placed in a key hides anything. `where` is a short JSON
    path, never the matched text, so a report cannot itself republish a leak.
    """
    found: List[Tuple[str, str]] = []
    seen: Set[Tuple[str, str]] = set()

    def add(names: List[str], where: str) -> None:
        for name in names:
            if (name, where) not in seen:
                seen.add((name, where))
                found.append((name, where))

    def walk(node: Any, where: str) -> None:
        if isinstance(node, dict):
            for key, child in node.items():
                add(find_matches(str(key), exempt_uuids=exempt_uuids), f"{where}.<key>")
                walk(child, f"{where}.{key}" if len(where) < 80 else where)
        elif isinstance(node, list):
            for index, child in enumerate(node):
                walk(child, f"{where}[{index}]" if len(where) < 80 else where)
        elif isinstance(node, str):
            add(find_matches(node, exempt_uuids=exempt_uuids), where)

    walk(value, "$")
    return found


def has_business_data(value: Any, *, exempt_uuids: Iterable[str] = ALLOWED_UUIDS) -> bool:
    """True when `value` (a string, or any decoded JSON value) carries a hit."""
    if isinstance(value, str):
        return bool(find_matches(value, exempt_uuids=exempt_uuids))
    return bool(scan_value(value, exempt_uuids=exempt_uuids))
