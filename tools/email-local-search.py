#!/usr/bin/env python3
"""Build a private local FTS index from an explicitly supplied decoded research copy."""
import argparse
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from lib.email_local_search import build_index, IndexError as PublicationError


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest='command', required=True)
    build = commands.add_parser('build', help='Atomically publish all supplied decoded rows')
    build.add_argument('--source', type=Path, required=True, help='Closed decoded research-copy database')
    build.add_argument('--index', type=Path, required=True, help='Destination in an existing owner-only directory')
    build.add_argument('--mailbox', required=True, help='Explicit source mailbox label')
    build.add_argument('--account-id', required=True, type=int, help='Exact decoded account identity')
    build.add_argument('--source-revision', required=True, help='Explicit source capture or decoder revision')
    build.add_argument('--allow-empty', action='store_true',
                       help='Allow zero supplied messages to replace an existing index')
    args = parser.parse_args()
    try:
        result = build_index(args.source, args.index, mailbox=args.mailbox,
                             account_id=args.account_id, source_revision=args.source_revision,
                             allow_empty=args.allow_empty)
    except PublicationError as error:
        if str(error) == 'empty_source_would_replace_index':
            print('Empty source refused. Use --allow-empty to replace an existing index with zero messages.',
                  file=sys.stderr)
        else:
            print('Private index publication refused or failed.', file=sys.stderr)
        return 2
    except OSError:
        print('Private index publication refused or failed.', file=sys.stderr)
        return 2
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == '__main__':
    sys.exit(main())
