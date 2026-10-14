"""Import the four markdown accumulators into loop_block + loop_item (ORDER 31(c)).

  00_Context/open-loops.md          -> kind open_loop, block 'hot'
  00_Context/open-loops-backlog.md  -> kind open_loop, blocks 'backlog' + 'backlog-orphan'
  DNA/Team/action-required.md       -> kind action_required, blocks 'open' + 'done'
  DNA/Team/team-loops.md            -> kind team_loop, blocks 'open' + 'done'

WHAT FIDELITY MEANS HERE. These files are not a spreadsheet with a tidy schema.
They are four years of human editing in markdown tables, and the importer's job
is to lift them into records WITHOUT correcting them. Everything below that looks
like over-engineering is one measured defect in the real files:

  * NUMBERS COLLIDE. #111 names two different items inside open-loops.md.
    #103, #95, #88 and #108 each name one item in the hot file and a different
    one in the backlog. T34 names one row in team-loops' Open table and another
    in its Done table. Two Done rows use the glyph `🔓` where a number belongs.
    All of it is imported verbatim and REPORTED; renumbering is a content change
    and content changes are Joe's.

  * THREE ROWS DISAGREE WITH THEIR OWN HEADER. backlog #66 carries 5 cells
    against a 6-column header (the Owner cell is simply absent, so 'Joe' and the
    date sit one position left of where they belong). backlog #76 carries 7.
    action-required A10 sits in the 5-column DONE table carrying the OPEN table's
    6. Each gets a per-row col_order so the render puts every cell back exactly
    where it was. Deciding which cell "really" means Owner would be inventing
    data, and the order's stop rule says review list, never a guess.

  * ONE ROW HAS NO HEADER ABOVE IT. backlog #87 sits under the '## Closed'
    heading as a bare table row with no header line. It is open ❓ content filed
    under a Closed heading. Imported into a HEADERLESS block, which renders rows
    with no header row — so it comes back byte-identical, in place, wrong exactly
    the way Joe left it, and it appears on the review list.

  * ONE CELL CONTAINS A LINE BREAK. team-loops T54's Notes cell spans two
    physical lines. The row parser joins on the newline and stores it; the render
    re-emits it.

  * ONE CELL CONTAINS AN ESCAPED PIPE. T36 quotes an email subject with `\\|`.
    The cell splitter respects the escape.

  * MARKERS ARE NOT ALL DATES. `🗓TABLED` is a real value (#86). The literal is
    stored verbatim in marker_literal and re-emitted; due_on is filled only when
    the marker actually is a date. Normalizing '🗓TABLED' into a date column would
    mean either losing the word or inventing a day.

PLACEMENT IS IMPORTED AS FOUND. Six rows in the hot file would not survive the
design's marker-derived promotion rule (#108/#109/#106 carry ✅, #86 reads
🗓TABLED, #93 and #94 are future-dated and hot anyway). The block a row lives in
is where Joe put it; v_loop_promotion_due names what has come due and update-loop
moves it. See the migration header.

IDEMPOTENT by (block_id, render_seq) — position in the source file, because
number is not unique and never will be. A rerun writes 0 new rows.

Usage:
  CARR_IMPORT_DB_URL=... .venv/bin/python -m pipelines.import_loops
      [--source-dir PATH] [--dry-run] [--review-only]
"""

import argparse
import json
import os
import re
import sys
from pathlib import Path

import psycopg

REPO = Path(__file__).resolve().parent.parent
DEFAULT_SOURCE_DIR = REPO / "frozen-sources" / "2026-07-31-loops"
VAULT = Path(os.environ.get("CARR_VAULT") or "/Users/booko/Library/CloudStorage/GoogleDrive-joe.bookout.carr.us@gmail.com/My Drive/CARR AI")

# (vault-relative path, kind, tier, personal_to slug or None, [block_key per table
#  block in file order], {block_key: col_order})
OPEN_LOOP_COLS = ["number", "owner", "body", "since_text", "unblocks", "source_note"]

FILES = [
    {
        "rel": "00_Context/open-loops.md",
        "kind": "open_loop",
        "tier": "personal",
        "personal_to": "joe",
        "block_keys": ["hot"],
        "col_order": {"hot": OPEN_LOOP_COLS},
        "open_blocks": {"hot"},
    },
    {
        "rel": "00_Context/open-loops-backlog.md",
        "kind": "open_loop",
        "tier": "personal",
        "personal_to": "joe",
        # the second table block in this file is the headerless orphan run (#87)
        "block_keys": ["backlog", "backlog-orphan"],
        "col_order": {"backlog": OPEN_LOOP_COLS, "backlog-orphan": OPEN_LOOP_COLS},
        "open_blocks": {"backlog", "backlog-orphan"},
    },
    {
        "rel": "DNA/Team/action-required.md",
        "kind": "action_required",
        "tier": "shared",
        "personal_to": None,
        "block_keys": ["open", "done"],
        "col_order": {
            "open": ["number", "owner", "title", "unblocks", "since_text", "source_note"],
            "done": ["number", "owner", "title", "closed_text", "outcome"],
        },
        "open_blocks": {"open"},
    },
    {
        "rel": "DNA/Team/team-loops.md",
        "kind": "team_loop",
        "tier": "shared",
        "personal_to": None,
        "block_keys": ["open", "done"],
        "col_order": {
            "open": ["number", "owner", "title", "since_text", "body"],
            "done": ["number", "owner", "title", "closed_text", "outcome"],
        },
        "open_blocks": {"open"},
    },
]

CANONICAL_COLS = {"number", "owner", "title", "body", "since_text", "unblocks",
                  "source_note", "closed_text", "outcome"}

# ── markdown table mechanics ─────────────────────────────────────────────────
# An escaped \| belongs INSIDE its cell. build_clients_active writes that escape
# on the way out (a raw pipe silently splits the row and shifts every cell after
# it left — C-131's "Marietta | Smyrna" did exactly that), so the reader has to
# respect it on the way in.
CELL_SPLIT = re.compile(r"(?<!\\)\|")
DIVIDER_RE = re.compile(r"\|(\s*-{3,}\s*\|)+")


def split_cells(logical):
    inner = logical[1:]
    stripped = inner.rstrip()
    if stripped.endswith("|") and not stripped.endswith("\\|"):
        inner = stripped[:-1]
    return [c.strip() for c in CELL_SPLIT.split(inner)]


def is_divider(line):
    return bool(DIVIDER_RE.fullmatch(line.strip()))


def parse_file(text):
    """-> [('prose', md)] | [('table', header_cells|None, divider|None, [logical rows])]

    A logical row may span several physical lines: a cell containing a line break
    leaves the physical line not ending in '|', so the row continues.
    """
    lines = text.split("\n")
    blocks, prose, i = [], [], 0

    def take_rows(start):
        rows, j = [], start
        while j < len(lines) and lines[j].startswith("|"):
            logical = lines[j]
            while not logical.rstrip().endswith("|") and j + 1 < len(lines):
                j += 1
                logical += "\n" + lines[j]
            rows.append(logical)
            j += 1
        return rows, j

    while i < len(lines):
        line = lines[i]
        if line.startswith("|"):
            if prose:
                blocks.append(("prose", "\n".join(prose)))
                prose = []
            if i + 1 < len(lines) and is_divider(lines[i + 1]):
                header, divider = split_cells(line), lines[i + 1]
                rows, i = take_rows(i + 2)
                blocks.append(("table", header, divider, rows))
            else:
                # a headerless run — backlog #87. Kept where it is, never relocated.
                rows, i = take_rows(i)
                blocks.append(("table", None, None, rows))
            continue
        prose.append(line)
        i += 1
    if prose:
        blocks.append(("prose", "\n".join(prose)))
    return blocks


# ── marker parsing ───────────────────────────────────────────────────────────
MARKER_RE = re.compile(r"^(🔔|❓|🗓\S*)(\s+)(.*)$", re.DOTALL)


def parse_marker(cell):
    """-> (marker, marker_literal, remainder). Non-destructive."""
    m = MARKER_RE.match(cell)
    if not m:
        return "none", None, cell
    literal, gap, rest = m.group(1), m.group(2), m.group(3)
    if gap != " ":
        return "none", None, cell          # unusual spacing: leave it alone
    if literal == "🔔":
        marker = "bell"
    elif literal == "❓":
        marker = "decision"
    else:
        marker = "dated"
    return marker, literal, rest


DATE_RE = re.compile(r"^🗓(\d{4}-\d{2}-\d{2})$")


def marker_due(literal):
    if not literal:
        return None
    m = DATE_RE.match(literal)
    return m.group(1) if m else None


def has_drift(*cells):
    return any("⚡" in (c or "") for c in cells)


# ── row -> semantic fields ───────────────────────────────────────────────────
def map_row(cells, block_col_order, review, where):
    """-> (fields dict, extra_cells dict, row_col_order or None)

    The block's column order applies when the widths agree. When they do not, the
    row keeps its OWN order: every cell stays in the position the file put it in,
    and the disagreement goes on the review list instead of being resolved by a
    guess about which cell means what.
    """
    order = list(block_col_order)
    row_order = None
    if len(cells) != len(order):
        row_order = infer_row_order(cells, order, review, where)
        order = row_order
    fields, extra = {}, {}
    for name, value in zip(order, cells):
        if name.startswith("extra:"):
            extra[name.split(":", 1)[1]] = value
        else:
            fields[name] = value
    return fields, extra, row_order


def infer_row_order(cells, block_order, review, where):
    """A width mismatch is NEVER silently reshaped.

    Short row  -> the trailing columns are dropped from the order, so every cell
                  keeps its index. (#66: 5 cells, so the 6th name goes unused and
                  the cells sit under number/owner/body/since/unblocks as written
                  — which is exactly how the file reads, Owner cell absent.)
    Long row   -> the surplus cells become extra:<n>, appended after the known
                  ones. (#76: a 7th cell the header has no name for.)
    A10 is neither: it is a 6-cell row in a 5-column table whose 6 cells are the
    OPEN table's own shape, so it is named explicitly rather than inferred.
    """
    n, m = len(cells), len(block_order)
    if n < m:
        order = block_order[:n]
        review.append(f"{where}: {n} cells against a {m}-column header — the trailing "
                      f"column(s) {block_order[n:]} have no cell. Every cell kept at its "
                      f"own index; the correct mapping is a human call.")
    else:
        order = list(block_order) + [f"extra:{k}" for k in range(1, n - m + 1)]
        review.append(f"{where}: {n} cells against a {m}-column header — {n - m} surplus "
                      f"cell(s) stored as extra_cells, positions preserved.")
    return order


# A10 is the one row whose surplus cell has a KNOWN meaning: it is the OPEN
# table's 'Why it matters' column, left behind when the row moved to DONE. Named,
# not inferred, and still reported.
A10_ORDER = ["number", "owner", "title", "unblocks", "closed_text", "outcome"]


def jsonable(v):
    return v


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--source-dir", default=str(DEFAULT_SOURCE_DIR),
                    help="folder holding the four .md files (default: the 2026-07-31 freeze)")
    ap.add_argument("--from-vault", action="store_true",
                    help="read the LIVE vault files instead of the freeze")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--review-only", action="store_true",
                    help="parse and print the review list; touch no database")
    a = ap.parse_args()

    review, rep = [], {"blocks": 0, "items": 0, "skipped_existing": 0,
                       "by_file": {}, "numbers": {}}

    parsed = []
    for spec in FILES:
        src = (VAULT / spec["rel"]) if a.from_vault else Path(a.source_dir) / Path(spec["rel"]).name
        text = src.read_text()
        blocks = parse_file(text)
        parsed.append((spec, src, text, blocks))

    # ── shape the rows, and collect every anomaly BEFORE writing anything ──────
    plan = []          # (spec, block_seq, block_key, prose_md, header_cols, col_order, [rows])
    for spec, src, text, blocks in parsed:
        rel = spec["rel"]
        rep["by_file"][rel] = {"open": 0, "done": 0}
        table_i = 0
        # None means "no prose seen yet"; "" is a REAL prose block. Three of the
        # four files end in an empty prose block, and that block is what carries
        # the file's trailing newline — treating "" as absent cost exactly one
        # byte per file and the round-trip diff caught all three.
        pending_prose = None
        for b in blocks:
            if b[0] == "prose":
                pending_prose = b[1] if pending_prose is None else pending_prose + "\n" + b[1]
                continue
            _, header, divider, raw_rows = b
            if table_i >= len(spec["block_keys"]):
                review.append(f"{rel}: an UNEXPECTED {table_i + 1}th table block was found "
                              f"({len(raw_rows)} rows). Not imported — the file's shape changed "
                              f"since the order was written.")
                continue
            key = spec["block_keys"][table_i]
            table_i += 1
            base_order = spec["col_order"][key]
            if header is not None and len(header) != len(base_order):
                review.append(f"{rel} [{key}]: header has {len(header)} columns, the mapping "
                              f"declares {len(base_order)}. Not imported.")
                continue
            rows = []
            for seq, logical in enumerate(raw_rows, start=1):
                cells = split_cells(logical)
                number = cells[0] if cells else ""
                where = f"{rel} [{key}] row {seq} (#{number})"
                if number == "A10":
                    order = A10_ORDER
                    review.append(f"{where}: 6 cells in the 5-column DONE table — it kept the "
                                  f"OPEN table's 'Why it matters' column when it was closed. "
                                  f"Mapped explicitly to {order}; nothing dropped.")
                    fields = dict(zip(order, cells))
                    extra, row_order = {}, order
                else:
                    fields, extra, row_order = map_row(cells, base_order, review, where)
                rows.append((seq, logical, cells, fields, extra, row_order))
                rep["numbers"].setdefault(number, []).append(f"{rel}[{key}]")
            plan.append((spec, key, pending_prose or "", header, divider, base_order, rows))
            pending_prose = None
        if pending_prose is not None:
            plan.append((spec, None, pending_prose, None, None, None, []))

    # ── collisions: report, never resolve ────────────────────────────────────
    for number, wheres in sorted(rep["numbers"].items()):
        if len(wheres) > 1:
            review.append(f"NUMBER COLLISION: '{number}' names {len(wheres)} different rows "
                          f"({', '.join(wheres)}). Imported verbatim in all positions; "
                          f"renumbering is a content change and is Joe's call.")

    # ── round-trip proof, before the database is touched ─────────────────────
    for spec, src, text, blocks in parsed:
        back = reassemble(blocks)
        if back != text:
            sys.exit(f"PARSE IS LOSSY on {spec['rel']} — refusing to import. "
                     f"(orig {len(text)} chars, reassembled {len(back)})")

    if a.review_only:
        print_review(review, rep)
        return

    url = os.environ.get("CARR_IMPORT_DB_URL") or os.environ.get("DATABASE_URL")
    if not url:
        raise SystemExit("CARR_IMPORT_DB_URL not set")

    with psycopg.connect(url) as conn, conn.cursor() as cur:
        cur.execute("select id from actor where slug = 'system'")
        sys_id = cur.fetchone()[0]
        actor_ids = {}
        cur.execute("select slug, id from actor")
        for slug, aid in cur.fetchall():
            actor_ids[slug] = aid

        block_seq_by_file = {}
        for spec, key, prose, header, divider, base_order, rows in plan:
            rel = spec["rel"]
            seq = block_seq_by_file.get(rel, 0) + 1
            block_seq_by_file[rel] = seq

            cur.execute("select id from loop_block where rel_path=%s and seq=%s", (rel, seq))
            got = cur.fetchone()
            if got:
                block_id = got[0]
            else:
                # renders_closed: action-required.md and team-loops.md carry their
                # Done tables INLINE, so those blocks render closed rows. The
                # open-loops pair has no Done table — a closed row leaves for
                # open-loops-closed.md — so their blocks render open rows only.
                renders_closed = key is not None and key not in spec["open_blocks"]
                cur.execute(
                    """insert into loop_block
                         (rel_path, kind, seq, block_key, prose_md, header_cols, col_order,
                          renders_closed, created_by, updated_by)
                       values (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) returning id""",
                    (rel, spec["kind"], seq, key, prose, header, base_order,
                     renders_closed, sys_id, sys_id))
                block_id = cur.fetchone()[0]
                rep["blocks"] += 1

            if key is None:
                continue
            status = "open" if key in spec["open_blocks"] else "done"
            personal = actor_ids[spec["personal_to"]] if spec["personal_to"] else None

            for rseq, logical, cells, fields, extra, row_order in rows:
                cur.execute("select id from loop_item where block_id=%s and render_seq=%s",
                            (block_id, rseq))
                if cur.fetchone():
                    rep["skipped_existing"] += 1
                    continue

                # the marker rides on whichever cell carries the item's own text
                marker_cell_name = "body" if "body" in fields else "title"
                cell = fields.get(marker_cell_name) or ""
                marker, literal, rest = parse_marker(cell)
                if literal is not None:
                    fields[marker_cell_name] = rest

                # a closed row's outcome IS its close_outcome — the file's own column.
                close_outcome = None
                if status != "open":
                    close_outcome = fields.get("outcome") or None
                    if not close_outcome:
                        review.append(f"{rel} [{key}] #{fields.get('number')}: sits in a "
                                      f"closed section with an EMPTY Outcome cell. Not "
                                      f"imported — the record requires an outcome to be "
                                      f"closed, and inventing one is not available.")
                        continue

                cur.execute(
                    """insert into loop_item
                         (kind, number, block_id, render_seq, col_order,
                          title, body, owner, since_text, unblocks, source_note,
                          closed_text, outcome, extra_cells,
                          marker, marker_literal, due_on, drift_critical,
                          status, close_outcome, closed_by, closed_at,
                          tier, personal_to, created_by, updated_by)
                       values (%s,%s,%s,%s,%s,
                               %s,%s,%s,%s,%s,%s,
                               %s,%s,%s,
                               %s,%s,%s,%s,
                               %s,%s,%s,
                               case when %s = 'open' then null else now() end,
                               %s,%s,%s,%s)""",
                    (spec["kind"], fields.get("number", ""), block_id, rseq,
                     row_order,
                     fields.get("title"), fields.get("body"), fields.get("owner"),
                     fields.get("since_text"), fields.get("unblocks"),
                     fields.get("source_note"), fields.get("closed_text"),
                     fields.get("outcome"), json.dumps(extra),
                     marker, literal, marker_due(literal),
                     has_drift(fields.get("title"), fields.get("body"),
                               fields.get("unblocks"), literal),
                     status, close_outcome,
                     sys_id if status != "open" else None,
                     # closed_at: the file's own Closed column is a DATE STRING and
                     # sometimes a phrase, so it stays verbatim in closed_text. This
                     # stamp records when the RECORD was closed, which for a legacy
                     # row is the import. Two different facts, both kept, neither
                     # pretending to be the other.
                     status,
                     spec["tier"], personal, sys_id, sys_id))
                rep["items"] += 1
                rep["by_file"][rel]["open" if status == "open" else "done"] += 1

        if a.dry_run:
            conn.rollback()
            print("DRY RUN — rolled back")
        else:
            conn.commit()

    print_review(review, rep)


def reassemble(blocks):
    out = []
    for b in blocks:
        if b[0] == "prose":
            out.append(b[1])
        else:
            _, header, divider, rows = b
            if header is not None:
                out.append("| " + " | ".join(header) + " |")
                out.append(divider)
            out.extend(rows)
    return "\n".join(out)


def print_review(review, rep):
    print(f"blocks inserted {rep['blocks']} · items inserted {rep['items']} · "
          f"skipped-existing {rep['skipped_existing']}")
    for rel, c in rep["by_file"].items():
        print(f"  {rel}: open {c['open']} · done {c['done']}")
    print(f"\nREVIEW LIST — {len(review)} item(s), none guessed:")
    for r in review:
        print(f"  * {r}")


if __name__ == "__main__":
    main()
