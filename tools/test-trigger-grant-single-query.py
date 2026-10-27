#!/usr/bin/env python3
"""The trigger grant gate reads catalog and privilege facts in one client call."""

import contextlib
import importlib.util
import io
import os
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
CHECK = REPO / "ops" / "trigger-grant-check.py"
SEP = "\x1f"

with tempfile.TemporaryDirectory(prefix="trigger-grant-single-query-") as tmp:
    root = Path(tmp)
    home = root / "home"
    client = home / ".local" / "bin" / "psql"
    client.parent.mkdir(parents=True)
    calls = root / "calls"
    query_file = root / "query"
    rows = [
        ("rel", "public", "write_table", "", "", ""),
        ("rel", "public", "read_table", "", "", ""),
        ("rel", "public", "MixedCase", "", "", ""),
        ("trg", "public.write_table", "guard_read", "guard_read_fn", "f",
         'begin perform side from read_table; perform side from "MixedCase"; return new; end;'),
        ("role", "carr_writer", "", "", "", ""),
        ("w", "carr_writer", "public.write_table", "", "", ""),
        ("x", "carr_writer", "public.read_table", "side", "1", ""),
        ("c", "carr_writer", "public.MixedCase", "side", "1", ""),
    ]
    output = "\n".join(SEP.join(row) for row in rows)
    client.write_text(
        "#!/usr/bin/env " + sys.executable + "\n"
        "import sys\n"
        "from pathlib import Path\n"
        f"calls = Path({str(calls)!r})\n"
        f"Path({str(query_file)!r}).write_text(sys.argv[-1])\n"
        "calls.write_text(calls.read_text() + 'x' if calls.exists() else 'x')\n"
        f"print({output!r})\n"
    )
    client.chmod(0o755)
    spec = importlib.util.spec_from_file_location("trigger_grant_check", CHECK)
    if spec is None or spec.loader is None:
        sys.exit("trigger-grant test: could not load ops/trigger-grant-check.py")
    check = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(check)
    check.db_tap.psql_bin = lambda: str(client)
    os.environ["CARR_CI_DATABASE_URL"] = "synthetic://fixture"
    stdout = io.StringIO()
    with contextlib.redirect_stdout(stdout):
        returncode = check.main()
    observed = stdout.getvalue()
    count = calls.read_text() if calls.exists() else ""
    query = query_file.read_text()
    expected = (
        returncode == 1
        and "guard_read" in observed
        and "public.read_table" in observed
        and "carr_writer" in observed
        and len(count) == 1
        and "nspname || chr(46) || c.relname as tbl" in query
        and "format('%I.%I'" not in query
    )
    if not expected:
        print("FAIL one connection reports the seeded missing column")
        print(observed)
        print(f"psql launches: {len(count)}")
        raise SystemExit(1)

    rows[6] = ("c", "carr_writer", "public.read_table", "side", "1", "")
    output = "\n".join(SEP.join(row) for row in rows)
    client.write_text(
        "#!/usr/bin/env " + sys.executable + "\n"
        "from pathlib import Path\n"
        f"calls = Path({str(calls)!r})\n"
        "calls.write_text(calls.read_text() + 'x' if calls.exists() else 'x')\n"
        f"print({output!r})\n"
    )
    with contextlib.redirect_stdout(stdout := io.StringIO()):
        returncode = check.main()
    if returncode != 0 or "trigger-grant: OK" not in stdout.getvalue():
        print("FAIL one connection accepts the granted column")
        print(stdout.getvalue())
        raise SystemExit(1)
    if len(calls.read_text()) != 2:
        print("FAIL each result used exactly one PostgreSQL client")
        raise SystemExit(1)
    print("OK one connection reports the seeded missing column and accepts its grant")
