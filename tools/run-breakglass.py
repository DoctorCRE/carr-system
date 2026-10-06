#!/usr/bin/env python3
"""run-breakglass.py — the TRACKED, REVIEWED launcher for the break-glass
receipt driver (WR-000048 registry re-pin; replaces the out-of-index interim
shim `out/frontier-finding/bin/run_breakglass.py` documented in
docs/frontier-finding/README.md).

The driver (docs/frontier-finding/breakglass_run.py) and its generator are
deliberately import-only libraries — no shebang, no __main__ — so the sealed
SCAC mutation registry never sees THEM as entrypoints. This launcher is the
one reviewed entrypoint that reaches the driver's main(), inventoried as a
break-glass-class row beside tools/db-tap.py and tools/call-verb.py.

INVOCATION (through db-tap's break-glass path, same as before, launcher
swapped in):

    CARR_BREAK_GLASS=1 .venv/bin/python tools/db-tap.py --reason "<WR note ref>" \\
        run tools/run-breakglass.py -- \\
        --approved <run>.sql --receipt <receipt>.json

db-tap `run` mode execs this file with DATABASE_URL already set in the child
environment. It forwards extra argv VERBATIM, including any literal "--"
separator — stripped here so the driver's own parser never sees it (the same
stray-token class that broke the progressive loop's function-fetch lane,
RESULT.md ADDENDUM 4).
"""

import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]


def main(argv=None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if args and args[0] == "--":
        args = args[1:]
    sys.path.insert(0, str(REPO / "docs" / "frontier-finding"))
    import breakglass_run
    return int(breakglass_run.main(args) or 0)


if __name__ == "__main__":
    sys.exit(main())
