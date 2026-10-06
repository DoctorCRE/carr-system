#!/usr/bin/env python3
"""Canonical doctrine retrieval with an explicit Drive recovery path.

Normal use queries ``search_doctrine_situations`` in the record layer and never
opens CARR_VAULT, Google Drive, or the generated section index.  The old TSV
reader remains available only as a loud, operator-selected recovery mode:

  ./run.sh retrieve "which lenders do we intro for dental startups"
  ./run.sh retrieve --recovery "record-layer outage procedure"

``--vault`` is accepted only with ``--recovery``.  A normal store failure is an
unavailable result, not permission to return an old export as current truth.
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any

from retrieval_lexical import rank_index, toks

DEFAULT_RECOVERY_VAULT = (
    Path.home() / "Library" / "CloudStorage"
    / "GoogleDrive-joe.bookout.carr.us@gmail.com" / "My Drive" / "CARR AI"
)
EX_UNAVAILABLE = 69


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Search canonical doctrine; Drive is explicit recovery only."
    )
    parser.add_argument("-n", type=int, default=8, dest="top")
    parser.add_argument(
        "--recovery",
        action="store_true",
        help="also use the generated Drive section index as outage recovery",
    )
    parser.add_argument(
        "--vault",
        help="recovery vault root; refused unless --recovery is also present",
    )
    parser.add_argument("words", nargs="+")
    args = parser.parse_args(argv)
    if args.top < 1:
        parser.error("-n must be at least 1")
    if args.vault and not args.recovery:
        parser.error("--vault is a recovery source; pass --recovery explicitly")
    return args


def refusal_detail(exc: Exception) -> str:
    """Say WHY, not merely which exception class.

    query_store already raises with the useful text — the verb subprocess's own
    stderr, or a named envelope failure. Printing only type(exc).__name__ threw
    all of it away and left the caller reading "(RuntimeError)", which names the
    machinery and not the fault. That is the same refusal-must-name-itself rule
    the doctrine search path already follows; this path had not been brought
    along. Class name is kept as a prefix so an exception with an empty message
    still identifies itself.
    """
    message = " ".join(str(exc).split())
    return f"{type(exc).__name__}: {message}" if message else type(exc).__name__


def query_store(words: list[str], top: int) -> list[Any]:
    """Use the authenticated local-token verb path; no actor is caller data."""
    repo = Path(__file__).resolve().parent.parent
    payload = json.dumps({"q": " ".join(words), "limit": top}, separators=(",", ":"))
    proc = subprocess.run(
        [sys.executable, str(repo / "tools" / "call-verb.py"),
         "search-doctrine", payload],
        cwd=repo,
        text=True,
        capture_output=True,
        timeout=45,
    )
    if proc.returncode != 0:
        raise RuntimeError(proc.stderr.strip() or "authenticated doctrine read failed")
    try:
        result = json.loads(proc.stdout)
    except ValueError as exc:
        raise RuntimeError("authenticated doctrine read returned non-JSON output") from exc
    hits = result.get("hits") if isinstance(result, dict) else None
    if not isinstance(hits, list) or not all(isinstance(hit, dict) for hit in hits):
        raise RuntimeError("authenticated doctrine read returned an invalid hit envelope")
    return hits


def recovery_vault(explicit: str | None) -> Path:
    """Resolve Drive only after the caller has selected recovery mode."""
    return Path(explicit or os.environ.get("CARR_VAULT") or DEFAULT_RECOVERY_VAULT)


def print_store_hits(store_hits: list[Any]) -> None:
    if not store_hits:
        return
    # Fallback rows only exist when no section matched every query word; the
    # ranker marks each one, and the two lanes never mix in a single answer.
    if all(isinstance(hit, dict) and (hit.get("provenance") or {}).get("fallback")
           for hit in store_hits):
        print(
            "retrieve: no section matched every word; these are best-effort "
            "any-word matches — treat as leads, not as the answer:"
        )
    print("retrieve: STORE hits (live canonical copies):")
    for hit in store_hits:
        if isinstance(hit, dict):
            slug = hit.get("doc_slug")
            key = hit.get("section_key")
            title = hit.get("title")
            snippet = hit.get("snippet") or ""
        else:  # compatibility for focused test fixtures
            slug, key, title, _rank, snippet = hit
        label = f"{slug} § {key}" + (f" - {title}" if title else "")
        print(f"  [store]  {label}")
        print(f"           read-doctrine {{\"document\":\"{slug}\"}} · {snippet}")


def recovery_hits(vault: Path, query: str, top: int, store_available: bool) -> list[Any]:
    """Read the generated TSV only inside the explicit recovery boundary."""
    migrated_rel: set[str] = set()
    if store_available:
        try:
            from lib.record_sources import doctrine_migrated_paths
            migrated_rel = set(doctrine_migrated_paths(str(vault)))
        except Exception as exc:
            print(
                "retrieve: RECOVERY could not classify migrated file copies "
                f"({refusal_detail(exc)}); refusing the Drive index",
                file=sys.stderr,
            )
            raise RuntimeError("recovery classification unavailable") from exc

    index = vault / "Automation" / "section-index.tsv"
    if not index.exists():
        raise FileNotFoundError(
            f"RECOVERY index missing: {index}; recovery does not rebuild or mutate it"
        )
    return rank_index(str(index), query, top=None, migrated_paths=migrated_rel)[:top]


def main(argv: list[str] | None = None) -> int:
    args = parse_args(list(sys.argv[1:] if argv is None else argv))
    words = " ".join(args.words).split()
    query = " ".join(words).lower()
    keywords = set(toks(query))
    if not keywords:
        print("retrieve: query reduced to nothing after stopwords; be more specific")
        return 1

    store_available = True
    try:
        store_hits = query_store(words, args.top)
    except Exception as exc:
        store_available = False
        store_hits = []
        if not args.recovery:
            print(
                "retrieve: canonical store unavailable "
                f"({refusal_detail(exc)}); normal mode refuses Drive fallback. "
                "Use --recovery only for an acknowledged outage.",
                file=sys.stderr,
            )
            return EX_UNAVAILABLE
        print(
            "retrieve: RECOVERY MODE - canonical store unavailable "
            f"({refusal_detail(exc)}); reading the generated Drive index",
            file=sys.stderr,
        )

    print_store_hits(store_hits)
    if not args.recovery:
        if not store_hits:
            print("retrieve: no canonical doctrine hits")
        return 0

    vault = recovery_vault(args.vault)
    print(f"retrieve: RECOVERY MODE - Drive index at {vault}", file=sys.stderr)
    try:
        ranked = recovery_hits(vault, query, args.top, store_available)
    except (FileNotFoundError, RuntimeError) as exc:
        print(f"retrieve: {exc}", file=sys.stderr)
        return EX_UNAVAILABLE

    if not ranked and not store_hits:
        print("retrieve: no recovery index hits")
        return 0
    if ranked:
        print(f"retrieve: RECOVERY file hits for: {' '.join(sorted(keywords))}")
    for item in ranked:
        row = item.row
        crumb = f"{row.parents} > {row.header}" if row.parents else row.header
        if row.source == "store":
            slug = row.path.split("doctrine:", 1)[1]
            print(f"  {item.score:5.1f}  [store export]  {crumb}")
            print(f"           read-doctrine {{\"document\":\"{slug}\"}}")
        else:
            span = f"lines {row.start}-{row.end}" if row.level else f"whole file (1-{row.end})"
            print(f"  {item.score:5.1f}  {row.path}  [{span}]  {crumb}")
    print("RECOVERY output is an export, not current canonical truth")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
