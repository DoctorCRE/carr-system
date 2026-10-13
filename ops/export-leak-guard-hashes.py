#!/usr/bin/env python3
"""Read the record layer and write a hash-only repository scanning corpus."""
import argparse
import json
import pathlib
import secrets
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / 'security'))
from exporters.common import connect
from leak_guard import make_corpus


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=pathlib.Path, default=ROOT / 'security/client-hashes.json')
    args = parser.parse_args()
    values = []
    with connect() as connection:
        connection.execute('set transaction read only')
        connection.execute("set local statement_timeout = '30s'")
        queries = (
            'select name,email,phone,cell from party',
            'select "Name","Practice Address","Email","Phone" from v_export_pool_all',
            'select "Name","Practice / Entity","Contact","Email","Phone" from v_export_clients',
            'select "Contact Name","Practice","Email","Phone" from v_export_leads',
            'select "Name","Company","Email","Phone" from v_export_vendors',
        )
        for query in queries:
            rows = connection.execute(query).fetchall()
            if not rows:
                raise ValueError('empty export source')
            values.extend(str(value) for row in rows for value in row if value and str(value).strip())
    salt = json.loads(args.output.read_text())['salt'] if args.output.exists() else secrets.token_hex(32)
    corpus = make_corpus(values, salt)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(corpus, indent=2) + '\n')
    print('Hash-only export written; no record values emitted.')


if __name__ == '__main__':
    try:
        main()
    except Exception:
        print('Hash export failed; existing corpus retained.', file=sys.stderr)
        sys.exit(2)
