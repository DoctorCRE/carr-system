"""One canonical reservation ledger, plus claims in existing local ledgers."""
import json
from pathlib import Path


def reservation_paths(worktrees):
    """Git lists the primary checkout first; its out directory owns new claims."""
    return list(dict.fromkeys(
        (Path(tree) / "out/migration-reservations.jsonl").resolve()
        for tree in worktrees
    ))


def read_reservation_rows(paths):
    rows = []
    for path in paths:
        try:
            with open(path, encoding="utf-8") as stream:
                for line in stream:
                    try:
                        row = json.loads(line)
                    except ValueError:
                        continue
                    if isinstance(row, dict) and isinstance(row.get("number"), int):
                        rows.append(row)
        except FileNotFoundError:
            continue
    return rows
