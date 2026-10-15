"""Repository-local leak gate. Diagnostics contain locations, never values.

--staged, --range, --ci and --pre-push judge a change only on the lines it adds:
for each commit in scope, the lines its diff against its parents adds (for a merge,
the lines new against every parent, so only conflict resolutions and evil-merge
lines count). Content already upstream is treated as reviewed. A path counts as a
filename finding only where the commit introduces it, and an opaque binary blob
is refused wherever it is added or changed. --history audits whole blobs.
"""
import argparse
import bisect
import html
from collections import Counter, namedtuple
import hashlib
import io
import json
import os
import pathlib
import platform
import re
import subprocess
import sys
import tarfile
import urllib.request
import uuid
import unicodedata

HERE = pathlib.Path(__file__).resolve().parent
VERSION = '8.30.0'
ARCHIVES = {
    'darwin_arm64': 'b251ab2bcd4cd8ba9e56ff37698c033ebf38582b477d21ebd86586d927cf87e7',
    'darwin_x64': 'ca221d012d247080c2f6f61f4b7a83bffa2453806b0c195c795bbe9a8c775ed5',
    'linux_x64': '79a3ab579b53f71efd634f3aaf7e04a0fa0cf206b7ed434638d1547a2470a66e',
    'linux_arm64': 'b4cbbb6ddf7d1b2a603088cd03a4e3f7ce48ee7fd449b51f7de6ee2906f5fa2f',
}
PII = {
    'pii-email': re.compile(r'(?<![A-Za-z0-9.!#$%&\x27*+/=?^_`{|}~-])[A-Za-z0-9.!#$%&\x27*+/=?^_`{|}~-]+@[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)+'),
    'pii-phone': re.compile(r'(?<![\w])(?:\+?1[ .-]?)?(?:\([2-9]\d{2}\)|[2-9]\d{2})[ .-]?[2-9]\d{2}[ .-]?\d{4}(?![\w])'),
    # The street word must be capitalized: "one place" in prose is not an address.
    'pii-address': re.compile(r'\b\d{1,6}\s+(?:[A-Za-z0-9.-]+\s+){1,6}(?:' + '|'.join(
        word + '|' + word.upper() for word in ('Street', 'St', 'Avenue', 'Ave', 'Road', 'Rd', 'Drive', 'Dr', 'Lane',
                                               'Ln', 'Boulevard', 'Blvd', 'Court', 'Ct', 'Way', 'Place', 'Pl',
                                               'Parkway', 'Pkwy')) + r')\b'),
    'pii-ssn': re.compile(r'(?<!\d)\d{3}-\d{2}-\d{4}(?!\d)'),
}
# Mail at a reserved domain (RFC 2606/6761), an IP literal or GitHub's public
# noreply identity reaches no person, so it is not contact data.
RESERVED_MAIL = re.compile(r'(?:^|\.)(?:example|invalid|test|localhost)$|^example\.(?:com|net|org)$'
                           r'|^users\.noreply\.github\.com$|^[\d.]+$', re.I)
# A one-token client value (a first name, a town, a word like "health") matches
# ordinary prose and source identifiers, so the window loop in pii_findings only
# tests windows of two or more tokens. Loading the corpus drops shorter lengths
# so the loop does not hash windows that can never be tested.
MIN_CLIENT_TOKENS = 2
CORPUS_SCHEMA = 'leak-guard-corpus.v2'
# Tokens are hashed with a separator, so "000 0 000" and "0000000" differ.
TOKEN_SEPARATOR = '\0'
# The lines and paths a change introduces. added is the set of 1-based blob line
# numbers it adds, or None when every line counts; named says the path is new.
Change = namedtuple('Change', 'path raw added named')


class Refusal(ValueError):
    """A refusal whose message names no value and is safe to print."""


def client_window(window):
    """All-digit windows match only when phone-shaped (10 or 11 digits)."""
    return not all(token.isdigit() for token in window) or sum(map(len, window)) in (10, 11)


def client_hash(salt, normalized):
    return hashlib.sha256((salt + '\0' + TOKEN_SEPARATOR.join(normalized)).encode()).hexdigest()


def tokens(text):
    if not text.isascii():
        text = unicodedata.normalize('NFKD', text)
        text = ''.join(c for c in text if not unicodedata.combining(c))
    return re.findall(r'[^\W_]+', text.lower())


def decode_source(text):
    text = re.sub(r'\\(\\|u[0-9a-fA-F]{4}|[nrtbf])',
                  lambda m: '\\' if m[1] == '\\' else chr(int(m[1][1:], 16)) if m[1].startswith('u') else ' ', text)
    return html.unescape(text)


def validate_corpus(corpus):
    if corpus.get('schema') != CORPUS_SCHEMA:
        raise Refusal('corpus schema is not ' + CORPUS_SCHEMA + '; rebuild it with ops/export-leak-guard-hashes.py')
    if (not re.fullmatch(r'[0-9a-f]{64}', corpus.get('salt', ''))
        or not corpus.get('hashes') or not corpus.get('lengths')
        or any(type(n) is not int or not 1 <= n <= 64 for n in corpus['lengths'])
        or any(not re.fullmatch(r'[0-9a-f]{64}', h) for h in corpus['hashes'])):
        raise ValueError('invalid corpus')
    if 'starts' in corpus and (not isinstance(corpus['starts'], list) or not corpus['starts']
                              or any(not re.fullmatch(r'[0-9a-f]{64}', h) for h in corpus['starts'])):
        raise ValueError('invalid corpus starts')
    if 'start_lengths' in corpus and (not isinstance(corpus['start_lengths'], dict)
        or set(corpus['start_lengths']) != set(corpus.get('starts', []))
        or any(not sizes or any(type(n) is not int or n not in corpus['lengths'] for n in sizes)
               for sizes in corpus['start_lengths'].values())):
        raise ValueError('invalid corpus length index')
    return corpus


def make_corpus(values, salt):
    hashes, lengths, starts, start_lengths = set(), set(), set(), {}
    for value in values:
        normalized = tokens(str(value))
        if not normalized or not client_window(normalized):
            continue
        if len(normalized) > 64:
            raise ValueError('export value exceeds window')
        lengths.add(len(normalized))
        hashes.add(client_hash(salt, normalized))
        first = client_hash(salt, normalized[:1])
        starts.add(first)
        start_lengths.setdefault(first, set()).add(len(normalized))
    return validate_corpus({'schema': CORPUS_SCHEMA, 'salt': salt,
                            'lengths': sorted(lengths), 'hashes': sorted(hashes), 'starts': sorted(starts),
                            'start_lengths': {h: sorted(ns) for h, ns in sorted(start_lengths.items())}})


def prepare_corpus(corpus):
    start_lengths = {h: {n for n in ns if n >= MIN_CLIENT_TOKENS}
                     for h, ns in corpus.get('start_lengths', {}).items()}
    start_lengths = {h: ns for h, ns in start_lengths.items() if ns}
    return {**corpus, '_hashes': set(corpus['hashes']),
            '_lengths': {n for n in corpus['lengths'] if n >= MIN_CLIENT_TOKENS},
            '_prefix': hashlib.sha256((corpus['salt'] + '\0').encode()),
            '_starts': set(start_lengths) if 'start_lengths' in corpus else
                       set(corpus['starts']) if 'starts' in corpus else None,
            '_start_lengths': start_lengths, '_first_cache': {}, '_window_cache': {}}


def pii_findings(text, corpus):
    words, numbers = [], []
    for number, line in enumerate(text.split('\n'), 1):
        line = decode_source(line)
        for rule, regex in PII.items():
            if any(rule != 'pii-email' or not RESERVED_MAIL.search(match[0].rpartition('@')[2])
                   for match in regex.finditer(line)):
                yield number, rule, ''
        normalized = tokens(line)
        words.extend(normalized)
        numbers.extend([number] * len(normalized))
    if corpus and corpus['_lengths']:
        hashes = corpus['_hashes']
        prefix = corpus['_prefix']
        lengths = corpus['_lengths']
        starts = corpus['_starts']
        first_cache = corpus['_first_cache']
        window_cache = corpus['_window_cache']
        for start in range(len(words)):
            word = words[start]
            if word not in first_cache:
                first_digest = prefix.copy()
                first_digest.update(word.encode())
                first = first_digest.hexdigest()
                first_cache[word] = first if starts is None or first in starts else None
            first = first_cache[word]
            if first is None:
                continue
            candidates = corpus['_start_lengths'].get(first, lengths)
            longest = max(candidates)
            window = tuple(words[start:start + longest])
            if window in window_cache:
                for fingerprint in window_cache[window]:
                    yield numbers[start], 'client-data', fingerprint
                continue
            digest = prefix.copy()
            digest.update(word.encode())
            found = []
            for end in range(start + 1, min(len(words), start + longest)):
                digest.update((TOKEN_SEPARATOR + words[end]).encode())
                if (end - start + 1 in candidates and client_window(words[start:end + 1])
                        and digest.copy().hexdigest() in hashes):
                    found.append(digest.hexdigest())
                    yield numbers[start], 'client-data', digest.hexdigest()
            if len(window_cache) >= 100000:
                window_cache.clear()
            window_cache[window] = found


def gitleaks():
    arch = {'aarch64': 'arm64', 'arm64': 'arm64', 'x86_64': 'x64', 'AMD64': 'x64'}[platform.machine()]
    target = platform.system().lower() + '_' + arch
    checksum = ARCHIVES[target]
    cache = pathlib.Path.home() / '.cache' / 'leak-guard' / VERSION / target
    cache.mkdir(parents=True, exist_ok=True, mode=0o700)
    archive = cache / 'release.tar.gz'
    if not archive.exists():
        url = f'https://github.com/gitleaks/gitleaks/releases/download/v{VERSION}/gitleaks_{VERSION}_{target}.tar.gz'
        data = urllib.request.urlopen(url, timeout=60).read()
        if hashlib.sha256(data).hexdigest() != checksum:
            raise ValueError('release checksum mismatch')
        archive.write_bytes(data)
    data = archive.read_bytes()
    if hashlib.sha256(data).hexdigest() != checksum:
        raise ValueError('cached release checksum mismatch')
    with tarfile.open(fileobj=io.BytesIO(data), mode='r:gz') as bundle:
        binary = bundle.extractfile('gitleaks').read()
    executable = cache / 'gitleaks'
    if not executable.exists() or executable.read_bytes() != binary:
        executable.write_bytes(binary)
        executable.chmod(0o700)
    return executable


def git(root, *args):
    return subprocess.run(['git', '--no-replace-objects', '-C', str(root), *args], check=True, capture_output=True).stdout


HUNK = re.compile(rb'^@@ -\d+(?:,\d+)? \+(\d+)(?:,(\d+))? @@', re.M)


def added_lines(patch):
    """The new-side line numbers a zero-context unified diff adds."""
    added = set()
    for start, count in HUNK.findall(patch):
        added.update(range(int(start), int(start) + (int(count) if count else 1)))
    return added


def change_scope(parent_blobs, added_against):
    """Lines a change adds against every parent blob, or None when no parent had the path."""
    added = None
    for parent in parent_blobs:
        lines = added_against(parent)
        added = lines if added is None else added & lines
    return added


def in_scope(change, line, rule):
    if rule == 'opaque-binary':
        return True
    if line == 0:
        return change.named
    return change.added is None or line in change.added


def raw_records(output):
    """Parse `--raw -z` output into (parent blobs, blob, path), skipping deletions and submodules."""
    fields_and_paths = output.split(b'\0')
    index = 0
    while index < len(fields_and_paths):
        header = fields_and_paths[index].lstrip(b'\n')
        index += 1
        if not header:
            continue
        if not header.startswith(b':') or index >= len(fields_and_paths):
            raise ValueError('invalid raw history')
        parents = len(header) - len(header.lstrip(b':'))
        fields = header.lstrip(b':').split()
        if len(fields) != 2 * parents + 3:
            raise ValueError('invalid raw history')
        path = fields_and_paths[index].decode('utf-8', 'surrogateescape')
        index += 1
        if b'U' in fields[-1]:
            raise ValueError('unmerged index')
        if fields[parents] in (b'000000', b'160000'):
            continue
        parent_blobs = tuple(oid for mode, oid in zip(fields[:parents], fields[parents + 1:2 * parents + 1])
                             if mode not in (b'000000', b'160000'))
        yield parent_blobs, fields[2 * parents + 1], path


def introduced(root, records, whole=False):
    """Read each introduced blob once and attach the lines and path it introduces."""
    blobs = {}
    for parent_blobs, oid, path in records:
        blobs.setdefault(oid, {}).setdefault(path, []).append(parent_blobs)
    diffs = {}

    def added_against(old, new):
        if (old, new) not in diffs:
            diffs[old, new] = added_lines(git(root, '-c', 'diff.algorithm=myers', 'diff', '-U0', '--text',
                                              '--inter-hunk-context=0', '--no-color', '--no-ext-diff',
                                              '--no-textconv', old.decode(), new.decode()))
        return diffs[old, new]

    child = subprocess.Popen(['git', '--no-replace-objects', '-C', str(root), 'cat-file', '--batch'],
                             stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
    try:
        for oid, paths in blobs.items():
            child.stdin.write(oid + b'\n')
            child.stdin.flush()
            header = child.stdout.readline().split()
            if len(header) != 3 or header[0] != oid or header[1] != b'blob':
                raise ValueError('unreadable blob')
            raw = child.stdout.read(int(header[2]))
            if len(raw) != int(header[2]) or child.stdout.read(1) != b'\n':
                raise ValueError('truncated blob')
            # Git counts UTF-16 lines by byte, not by character, so its diff lines cannot be trusted.
            whole_blob = whole or raw.startswith((b"\xff\xfe", b"\xfe\xff"))
            for path, changes in sorted(paths.items()):
                scopes = [None if whole_blob else change_scope(parents, lambda old: added_against(old, oid))
                          for parents in changes]
                added = None if None in scopes else frozenset().union(*scopes)
                yield Change(path, raw, added, whole or any(not parents for parents in changes))
    finally:
        child.stdin.close()
        child.stdout.close()
        if child.wait() != 0:
            raise ValueError('git object reader failed')


def staged(root):
    return introduced(root, list(raw_records(git(root, 'diff', '--cached', '--raw', '-z', '--no-abbrev',
                                                 '--no-renames'))))


def history(root, revisions, whole=False):
    if git(root, 'rev-parse', '--is-shallow-repository').strip() != b'false':
        raise ValueError('shallow history')
    # A merge lists only the paths whose result differs from every parent, and
    # within them only the lines new against every parent count: a line equal to
    # one parent's was introduced by that parent's own commit, judged there or
    # already upstream.
    changes = git(root, 'log', '--format=', '--raw', '-z', '--no-abbrev', '--no-renames',
                  '--root', '--diff-merges=combined', *revisions, '--')
    return introduced(root, list(raw_records(changes)), whole)


def upstream(root, remote_name):
    """Exclusions for every tracking ref of the remote: commits it already holds were judged there."""
    if not re.fullmatch(r'[A-Za-z0-9._-]+', remote_name):
        return []
    known = git(root, 'for-each-ref', '--format=%(objectname)', 'refs/remotes/' + remote_name)
    return ['^' + oid for oid in known.decode().splitlines()]


def pushed(root, stream, remote_name):
    revisions = []
    zero = '0' * 40
    for line in stream.splitlines():
        fields = line.split()
        if len(fields) != 4 or not all(re.fullmatch(r'[0-9a-f]{40}', fields[i]) for i in (1, 3)):
            raise ValueError('invalid push refs')
        _, local, _, remote = fields
        if local == zero:
            continue
        git(root, 'rev-parse', '--verify', local + '^{commit}')
        # Every intermediate commit is scanned; ancestry the receiver already has
        # (the ref's old value, or any of its tracking refs, e.g. a merged-in main)
        # is excluded.
        revisions.append(local)
        if remote != zero:
            git(root, 'rev-parse', '--verify', remote + '^{commit}')
            revisions.append('^' + remote)
    return history(root, revisions + upstream(root, remote_name)) if revisions else iter(())


def load_allowlist(path):
    if not path.exists():
        return set()
    data = json.loads(path.read_text())
    if data.get('schema') != 'leak-guard-allowlist.v1' or not isinstance(data.get('entries'), list):
        raise ValueError('invalid allowlist')
    entries = set()
    for entry in data['entries']:
        if (set(entry) not in ({'path', 'sha256', 'rule', 'reason'}, {'path', 'sha256', 'rule', 'reason', 'value_sha256'})
            or not all(isinstance(entry[k], str) and entry[k].strip() for k in entry)
            or not re.fullmatch(r'[0-9a-f]{64}', entry['sha256'])
            or (entry['rule'] == 'client-data' and not re.fullmatch(r'[0-9a-f]{64}', entry.get('value_sha256', '')))
            or '*' in entry['path']
            or pathlib.PurePosixPath(entry['path']).is_absolute() or '..' in pathlib.PurePosixPath(entry['path']).parts):
            raise ValueError('invalid allowlist entry')
        entries.add((entry['path'], entry['sha256'], entry['rule'], entry.get('value_sha256', '')))
    return entries


def scan(root, source, corpus, allowed, report_path=None):
    private = root / 'out' / '_to_delete' / 'leak-guard' / str(uuid.uuid4())
    private.mkdir(parents=True, mode=0o700)
    template = private / 'report.tmpl'
    template.write_text(r'{{range .}}{{printf "{\"line\":%d,\"rule\":%q}\n" .StartLine .RuleID}}{{end}}')
    report = private / 'locations.jsonl'
    command = [str(gitleaks()), 'stdin', '--redact=100', '--no-banner', '--no-color',
               '--ignore-gitleaks-allow', '--report-format=template', '--report-template', str(template),
               '--report-path', str(report)]
    env = {k: v for k, v in os.environ.items() if not k.startswith('GITLEAKS_')}
    findings, sensitive_paths, pii_cache = set(), set(), {}
    if corpus:
        corpus = prepare_corpus(corpus)
    chunks, changes, starts, digests, hits = [], [], [], [], []
    line, batch_bytes = 1, 0

    def flush():
        if not chunks:
            return
        report.write_text('')
        result = subprocess.run(command, input=b''.join(chunks), capture_output=True, cwd=private, env=env)
        if result.returncode not in (0, 1):
            raise ValueError('gitleaks failed')
        secret_count = 0
        for row in report.read_text().splitlines():
            hit = json.loads(row)
            index = bisect.bisect_right(starts, hit['line']) - 1
            if index < 0 or hit['line'] >= line:
                raise ValueError('invalid secret location')
            offset = hit['line'] - starts[index]
            if offset == 0:
                sensitive_paths.add(changes[index].path)
            hits.append((index, offset, hit['rule'], ''))
            secret_count += 1
        if (result.returncode == 1) != bool(secret_count):
            raise ValueError('gitleaks verdict/report disagreement')
        # Line 0 is the filename. The reported line stays the blob's line number.
        findings.update((changes[i].path, max(1, number), rule) for i, number, rule, fingerprint in hits
                        if in_scope(changes[i], number, rule)
                        and (changes[i].path, digests[i], rule, fingerprint) not in allowed)
        chunks.clear()
        changes.clear()
        starts.clear()
        digests.clear()
        hits.clear()

    for change in source:
        path, raw = change.path, change.raw
        digest = hashlib.sha256(raw).hexdigest()
        utf16 = raw.startswith((b"\xff\xfe", b"\xfe\xff"))
        opaque = (b'\0' in raw and not utf16) or raw.startswith((b'%PDF-', b'PK\x03\x04', b'\x1f\x8b'))
        # Uninspectable binary content is refused as a whole, never silently skipped.
        text = '' if opaque else raw.decode('utf-16' if utf16 else 'utf-8', errors='replace')
        filename_line = path.replace('\r', ' ').replace('\n', ' ')
        secret_input = filename_line.encode('utf-8', 'surrogateescape') + b'\n' + text.encode('utf-8') + b'\n'
        if chunks and batch_bytes + len(secret_input) > 8 * 1048576:
            flush()
            line, batch_bytes = 1, 0
        index = len(changes)
        starts.append(line)
        changes.append(change)
        digests.append(digest)
        if opaque:
            hits.append((index, 1, 'opaque-binary', ''))
        else:
            if digest not in pii_cache:
                pii_cache[digest] = list(pii_findings(text, corpus))
            hits.extend((index, number, rule, fingerprint) for number, rule, fingerprint in pii_cache[digest])
        hits.extend((index, 0, rule, fingerprint) for _, rule, fingerprint in pii_findings(path, corpus))
        chunks.append(secret_input)
        line += secret_input.count(b'\n')
        batch_bytes += len(secret_input)
    flush()
    findings = sorted(findings)
    if report_path:
        relative = report_path.resolve().relative_to(root.resolve() / 'out')
        if not relative.parts:
            raise ValueError('report must be under ignored out')
        git(root, 'check-ignore', str(report_path))
        if git(root, 'ls-files', '--', str(report_path)).strip():
            raise ValueError('tracked report')
        report_path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        report_path.write_text(json.dumps({'schema': 'leak-guard-private-report.v1',
                              'counts': dict(Counter(rule for _, _, rule in findings)),
                              'files': dict(sorted(Counter(path for path, _, _ in findings).items()))}, indent=2) + '\n')
        report_path.chmod(0o600)
    else:
        for path, number, rule in findings:
            name = '[redacted-path:' + hashlib.sha256(path.encode('utf-8', 'surrogateescape')).hexdigest()[:12] + ']'
            if path not in sensitive_paths and not list(pii_findings(path, corpus)):
                name = json.dumps(path, ensure_ascii=True)[1:-1]
            print(f'{name}:{number} {rule}')
    return int(bool(findings))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', type=pathlib.Path, default=HERE.parent)
    scope = parser.add_mutually_exclusive_group()
    scope.add_argument('--staged', action='store_true', help='lines the index adds over HEAD (the default)')
    scope.add_argument('--history', action='store_true',
                       help='audit every blob in all history as a whole; not limited to added lines')
    scope.add_argument('--range', help='lines added by each commit in BASE..HEAD not already on --remote')
    scope.add_argument('--pre-push', action='store_true',
                       help='lines added by each pushed commit not already on --remote (refs on stdin)')
    scope.add_argument('--ci', action='store_true', help="lines added by each commit of the pull request event")
    parser.add_argument('--generic-only', action='store_true')
    parser.add_argument('--corpus', type=pathlib.Path, default=HERE / 'client-hashes.json')
    parser.add_argument('--allowlist', type=pathlib.Path, default=HERE / 'allowlist.json')
    parser.add_argument('--report', type=pathlib.Path)
    parser.add_argument('--remote', default='origin')
    args = parser.parse_args()
    corpus = None if args.generic_only else validate_corpus(json.loads(args.corpus.read_text()))
    allowed = load_allowlist(args.allowlist)
    if args.pre_push:
        source = pushed(args.root, sys.stdin.read(), args.remote)
    elif args.history:
        source = history(args.root, ['--all'], whole=True)
    elif args.range:
        if not re.fullmatch(r'[0-9a-f]{40}\.\.[0-9a-f]{40}', args.range):
            raise ValueError('range must bind full commit hashes')
        source = history(args.root, [args.range, *upstream(args.root, args.remote)])
    elif args.ci:
        if os.environ.get('GITHUB_EVENT_NAME') != 'pull_request':
            raise ValueError('CI scope requires a pull request event')
        event = json.loads(pathlib.Path(os.environ['GITHUB_EVENT_PATH']).read_text())
        base, head = (event['pull_request'][k]['sha'] for k in ('base', 'head'))
        if not all(re.fullmatch(r'[0-9a-f]{40}', oid) for oid in (base, head)):
            raise ValueError('invalid pull request commits')
        source = history(args.root, [base + '..' + head])
    else:
        source = staged(args.root)
    return scan(args.root, source, corpus, allowed, args.report)


if __name__ == '__main__':
    try:
        sys.exit(main())
    except Refusal as refusal:
        print('leak-guard: ' + str(refusal) + '; push/CI refused', file=sys.stderr)
        sys.exit(2)
    except Exception:
        print('leak-guard: scan could not complete; push/CI refused', file=sys.stderr)
        sys.exit(2)
