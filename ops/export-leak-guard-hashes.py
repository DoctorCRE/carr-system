"""Read the record layer and write a hash-only repository scanning corpus.

Values listed in --exclude-file (the partners' and staff's own names and
contacts, one plaintext value per line, kept untracked under out/) are left out
of the corpus. Only counts are printed, never a value.
"""
import argparse
import json
import pathlib
import secrets
import subprocess
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'security'))
from leak_guard import CORPUS_SCHEMA, Refusal, make_corpus, tokens

QUERIES = (
    'select name,email,phone,cell from party',
    'select "Name","Practice Address","Email","Phone" from v_export_pool_all',
    'select "Name","Practice / Entity","Contact","Email","Phone" from v_export_clients',
    'select "Contact Name","Practice","Email","Phone" from v_export_leads',
    'select "Name","Company","Email","Phone" from v_export_vendors',
)


def exclusion_file(root, path):
    """Refuse an exclusion list that git tracks or that lives outside the ignored out/ tree."""
    path = path.resolve()
    if not path.is_relative_to(root.resolve() / 'out'):
        raise Refusal('exclude file must be under out/')
    tracked = subprocess.run(['git', '-C', str(root), 'ls-files', '--', str(path)],
                             check=True, capture_output=True).stdout
    if tracked.strip():
        raise Refusal('exclude file is tracked by git')
    if not path.is_file():
        raise Refusal('exclude file not found: ' + str(path.relative_to(root.resolve())))
    return [line for line in path.read_text().splitlines() if line.strip()]


def without_excluded(values, excluded):
    """Drop a value equal to an excluded entry, or containing one of two or more tokens."""
    exact, phrases = set(), set()
    for entry in excluded:
        normalized = tuple(tokens(entry))
        if normalized:
            exact.add(normalized)
            if len(normalized) >= 2:
                phrases.add(normalized)
    sizes = {len(phrase) for phrase in phrases}
    kept = []
    for value in values:
        normalized = tuple(tokens(value))
        if normalized in exact or any(normalized[i:i + n] in phrases
                                      for n in sizes for i in range(len(normalized) - n + 1)):
            continue
        kept.append(value)
    return kept


def export(values, excluded, output):
    """Write the corpus for the kept values; return (values read, values excluded, hashes written)."""
    kept = without_excluded(values, excluded)
    previous = json.loads(output.read_text()) if output.exists() else {}
    # A corpus of another schema hashed differently; its salt is not carried forward.
    salt = previous['salt'] if previous.get('schema') == CORPUS_SCHEMA else secrets.token_hex(32)
    corpus = make_corpus(kept, salt)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(corpus, indent=2) + '\n')
    return len(values), len(values) - len(kept), len(corpus['hashes'])


def record_values():
    sys.path.insert(0, str(ROOT))
    from exporters.common import connect
    values = []
    with connect() as connection:
        connection.execute('set transaction read only')
        connection.execute("set local statement_timeout = '30s'")
        for query in QUERIES:
            rows = connection.execute(query).fetchall()
            if not rows:
                raise ValueError('empty export source')
            values.extend(str(value) for row in rows for value in row if value and str(value).strip())
    return values


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=pathlib.Path, default=ROOT / 'security/client-hashes.json')
    parser.add_argument('--exclude-file', type=pathlib.Path, default=ROOT / 'out/leak-guard/staff-exclusions.txt')
    args = parser.parse_args()
    excluded = exclusion_file(ROOT, args.exclude_file)
    read, dropped, written = export(record_values(), excluded, args.output)
    print(f'Hash-only export written: {read} values read, {dropped} values excluded, {written} hashes written.')


if __name__ == '__main__':
    try:
        main()
    except Refusal as refusal:
        print('Hash export refused: ' + str(refusal) + '; existing corpus retained.', file=sys.stderr)
        sys.exit(2)
    except Exception:
        print('Hash export failed; existing corpus retained.', file=sys.stderr)
        sys.exit(2)
