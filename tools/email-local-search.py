#!/usr/bin/env python3
"""Build a private local FTS index from an explicitly supplied decoded research copy."""
import argparse
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from lib.email_local_search import build_index, query_index, IndexError as PublicationError


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
    query = commands.add_parser('query', help='Look up a literal phrase in one private local index')
    query.add_argument('--source', type=Path, required=True, help='Current closed private decoded copy')
    query.add_argument('--source-revision', required=True, help='Current capture or decoder revision; must match the index')
    query.add_argument('--index', type=Path, required=True)
    query.add_argument('--account-id', type=int, required=True)
    query.add_argument('--query', required=True, help='Literal person, email address or property phrase; no FTS operators')
    query.add_argument('--limit', type=int, default=20, help='Page size, 1 to 100')
    query.add_argument('--cursor', help='Next cursor from the same query and index generation')
    query.add_argument('--local-excerpts', action='store_true', help='Include bounded subject and body excerpts locally')
    args = parser.parse_args()
    try:
        if args.command == 'query':
            result = query_index(args.index, source=args.source, source_revision=args.source_revision,
                                 account_id=args.account_id, query=args.query,
                                 limit=args.limit, cursor=args.cursor, excerpts=args.local_excerpts)
        else:
            result = build_index(args.source, args.index, mailbox=args.mailbox,
                                 account_id=args.account_id, source_revision=args.source_revision,
                                 allow_empty=args.allow_empty)
    except PublicationError as error:
        if str(error) == 'empty_source_would_replace_index':
            print('Empty source refused. Use --allow-empty to replace an existing index with zero messages.',
                  file=sys.stderr)
        elif str(error) in ('stale_index', 'index_freshness_unavailable'):
            print('Private index stale or freshness unavailable. Rebuild from the current decoded copy.', file=sys.stderr)
        else:
            print('Private index operation refused or failed.', file=sys.stderr)
        return 2
    except OSError:
        print('Private index operation refused or failed.', file=sys.stderr)
        return 2
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == '__main__':
    sys.exit(main())
