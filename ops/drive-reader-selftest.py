#!/usr/bin/env python3
"""Control-plane check: records mode must not require retired Drive feeds.

The files remain an explicit, noncanonical recovery surface.
This check protects the normal code path: choosing
records must read the canonical pool/views without first globbing a deprecated
file merely to render a label or feed a second writer.
"""

from pathlib import Path
import ast
import sys


ROOT = Path(__file__).resolve().parents[1]


def require(condition, message):
    if not condition:
        raise AssertionError(message)


def source(path):
    return (ROOT / path).read_text()


def block_after(text, anchor, width=1800):
    start = text.index(anchor)
    return text[start:start + width]


def main():
    board = source("generators/build-lead-board.py")
    portable_board = source("shared/build-lead-board-template.py")
    promote = source("pipelines/lead-promote.py")
    mapper = source("pipelines/map_radar_lanes.py")
    corroborate = source("pipelines/radar/corroborate.py")
    date_founders = source("pipelines/radar/date-founders.py")
    dso_match = source("pipelines/dso-match.py")
    renewal_feed = source("generators/build-renewal-feed.py")
    record_sources = source("lib/record_sources.py")

    # The records branch must not resolve a router spreadsheet.  File mode may:
    # it is the documented recovery path for a machine without a record reader.
    for label, board_copy in (("lead board", board), ("portable lead board", portable_board)):
        board_sources = block_after(board_copy, "# ── where the raw rows come from")
        require("if MODE == MODE_RECORDS:" in board_sources,
                f"{label} has no distinct canonical records branch")
        require("router_path = latest_router()" not in block_after(board_sources,
                "if MODE == MODE_RECORDS:", 350),
                f"{label} records mode still requires the deprecated router spreadsheet")
        require("router_path = latest_router()" in block_after(board_sources,
                "else:", 300),
                f"{label} recovery mode lost its router-file reader")

    promote_sources = block_after(promote, "# ---------- sources ----------")
    require("RESERVOIR =" not in block_after(promote_sources,
            "if MODE == MODE_RECORDS:", 450),
            "lead-promote records mode still resolves the deprecated router spreadsheet")
    require("RESERVOIR =" in block_after(promote_sources, "else:", 500),
            "lead-promote recovery mode lost its router-file reader")

    # Two less-frequent routine consumers were repointed with the same boundary:
    # their default source is the router's canonical pool projection, while an
    # explicit --files/path run remains a recovery exercise rather than a hidden
    # prerequisite for the live path.
    founders_sources = block_after(date_founders, "def load_founders()", 650)
    require('rows = load_pool((ROUTER_SOURCE,))[ROUTER_SOURCE]' in founders_sources,
            "date-founders does not read post-sale founders from candidate_pool")
    require('path = latest(os.path.join(LEADS_DIR, "lead-router-*.xlsx"))' in founders_sources,
            "date-founders recovery mode lost its router-file reader")

    dso_sources = block_after(dso_match, "if MODE == MODE_RECORDS:\n    rows = load_pool", 550)
    require('rows = load_pool((ROUTER_SOURCE,))[ROUTER_SOURCE]' in dso_sources,
            "dso-match records mode still requires the deprecated router spreadsheet")
    require('sorted(glob.glob(os.path.join(ROOT, "DNA", "Leads", "lead-router-*.xlsx")))' in dso_match,
            "dso-match recovery mode lost its explicit router-file reader")

    # The renewal spreadsheet is an external source writer-side, not a record
    # projection.  Its routine registry suppressor nevertheless must use the
    # canonical lead view first; otherwise an export delay could alter a feed.
    renewal_registry = block_after(renewal_feed, "def load_registry()", 1200)
    require('for r in load_leads(ROOT, MODE):' in renewal_registry,
            "renewal-feed suppressor does not use the canonical lead projection")

    # Writers map the exact rows they just produced; a manual --lane invocation
    # retains its file reader for recovery/backfill.
    module = ast.parse(mapper)
    run_lane = next(n for n in module.body if isinstance(n, ast.FunctionDef) and n.name == "run_lane")
    args = [a.arg for a in run_lane.args.args]
    require("rows" in args, "run_lane cannot accept a writer's in-memory canonical rows")
    # Corroborate currently has no canonical upstream-signal ingress.  Its only
    # executable path is now explicit, reasoned recovery, so those noncanonical
    # rows must never be promoted into candidate_pool.
    require("canonical upstream signal ingress is not implemented" in corroborate,
            "corroborate does not name its canonical-ingress gap")
    require("run_lane(\"upstream\", rows=candidates)" not in corroborate,
            "corroborate maps NONCANONICAL recovery rows into candidate_pool")

    # The upstream suppressor must query canonical candidate_pool/lead views
    # whenever that read path is available, retaining a loud file fallback.
    require("load_pool" in corroborate and "load_leads" in corroborate,
            "corroborate lacks canonical candidate-pool and lead-view readers")
    require("pool_reach" in corroborate,
            "corroborate has no reachability check before canonical reads")
    require('os.environ.get("CARR_DB_JOBS_URL")' in record_sources,
            "scheduled jobs cannot use their narrow credential for canonical pool reads")

    # Direct writer handoff must not touch its recovery file.  This is the
    # behavioral half of the AST contract above.
    sys.path.insert(0, str(ROOT))
    from pipelines.map_radar_lanes import lane_rows
    supplied = [{"n": "Example canonical row"}]
    require(lane_rows({"path": Path("/definitely-missing-lane.json")}, supplied) == supplied,
            "in-memory lane handoff still attempts to read the recovery file")

    print("drive-reader-selftest: 18 checks passed")


if __name__ == "__main__":
    try:
        main()
    except (AssertionError, ValueError) as exc:
        print(f"drive-reader-selftest FAILED: {exc}", file=sys.stderr)
        raise SystemExit(1)
