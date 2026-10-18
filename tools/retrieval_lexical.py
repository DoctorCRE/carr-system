#!/usr/bin/env python3

from __future__ import annotations

import csv
import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable


STOP = {
    "the", "a", "an", "and", "or", "of", "to", "in", "on", "for", "with",
    "is", "are", "was", "be", "do", "does", "how", "what", "which", "who",
    "when", "where", "why", "we", "our", "my", "your", "i", "you", "it",
    "this", "that", "these", "those", "about", "from", "at", "by", "as",
    "into", "can", "should", "would", "could", "did", "have", "has", "had",
    "will", "there", "any", "all", "me", "us", "they", "them", "their",
    "his", "her", "its", "if", "then", "than", "so", "not", "no", "get",
    "got", "need", "want", "find", "show", "tell", "give", "look", "up",
    "out", "new", "use",
}
KEEP_SHORT = {"al", "fl", "x", "ai", "cre", "npi", "dso", "sos", "loi", "na", "t1", "t2", "t3", "cpa"}


def toks(text: str) -> list[str]:
    out = []
    for token in re.split(r"[^a-z0-9]+", text.lower()):
        if not token or token in STOP:
            continue
        if len(token) < 3 and token not in KEEP_SHORT:
            continue
        out.append(token[:-1] if len(token) > 4 and token.endswith("s") else token)
    return out


@dataclass(frozen=True)
class IndexRow:
    path: str
    start: int
    end: int
    level: int
    header: str
    parents: str
    gist: str
    source: str = "file"


@dataclass(frozen=True)
class RankedRow:
    score: float
    row: IndexRow


def load_index(path: str | os.PathLike[str]) -> list[IndexRow]:
    rows: list[IndexRow] = []
    with Path(path).open(encoding="utf-8") as handle:
        reader = csv.reader((line for line in handle if not line.startswith("#")), delimiter="\t")
        for raw in reader:
            if len(raw) < 7:
                continue
            rows.append(IndexRow(
                path=raw[0], start=int(raw[1]), end=int(raw[2]), level=int(raw[3]),
                header=raw[4], parents=raw[5], gist=raw[6],
                source=raw[7] if len(raw) > 7 else "file",
            ))
    return rows


def score_row(row: IndexRow, query: str) -> float:
    query_lower = query.lower()
    terms = set(toks(query_lower))
    filename = os.path.splitext(os.path.basename(row.path))[0]
    directories = os.path.dirname(row.path)
    score = 0.0
    score += 3.0 * len(terms & set(toks(row.header)))
    score += 3.0 * len(terms & set(toks(filename)))
    score += 2.0 * len(terms & set(toks(row.parents)))
    score += 2.0 * len(terms & set(toks(row.gist)))
    score += 1.0 * len(terms & set(toks(directories)))
    if len(query_lower) > 6 and (query_lower in row.header.lower() or query_lower in row.gist.lower()):
        score += 4.0
    return score


def rank_rows(
    rows: Iterable[IndexRow], query: str, *, top: int | None = 8,
    migrated_paths: set[str] | None = None,
) -> list[RankedRow]:
    migrated = migrated_paths or set()
    scored: list[RankedRow] = []
    for row in rows:
        score = score_row(row, query)
        if score <= 0:
            continue
        if row.source == "file" and row.path in migrated:
            continue
        scored.append(RankedRow(score=score, row=row))

    best: dict[str, RankedRow] = {}
    for candidate in sorted(
        scored,
        key=lambda item: (-item.score, item.row.level == 0, item.row.end - item.row.start),
    ):
        prior = best.get(candidate.row.path)
        if (
            prior is None
            or candidate.score > prior.score + 0.01
            or (
                abs(candidate.score - prior.score) <= 0.01
                and candidate.row.level > prior.row.level
            )
        ):
            best[candidate.row.path] = candidate
    ranked = sorted(best.values(), key=lambda item: -item.score)
    return ranked[:top] if top is not None else ranked


def rank_index(
    index_path: str | os.PathLike[str], query: str, *, top: int | None = 8,
    migrated_paths: set[str] | None = None,
) -> list[RankedRow]:
    return rank_rows(load_index(index_path), query, top=top, migrated_paths=migrated_paths)
