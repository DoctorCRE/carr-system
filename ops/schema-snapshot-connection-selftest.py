#!/usr/bin/env python3
"""Hermetic coverage for schema snapshot's non-argv libpq connection path."""
from __future__ import annotations

import importlib.util
import os
import shutil
import stat
import subprocess
import sys
import tempfile
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
HELPER = ROOT / "ops" / "schema-snapshot-connection.py"
SNAPSHOT = ROOT / "bin" / "schema-snapshot.sh"
SPEC = importlib.util.spec_from_file_location("schema_snapshot_connection", HELPER)
assert SPEC and SPEC.loader
connection = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(connection)

DSN = "postgresql://snapshot_user:fixture-password@db.example.test:5432/carr?sslmode=require&channel_binding=require"  # ci-secret-scan: allow — synthetic parser fixture

lines = connection.service_lines(DSN)
assert lines[0] == "[schema_snapshot]"
assert "host=db.example.test" in lines
assert "port=5432" in lines
assert "sslmode=require" in lines
assert "channel_binding=require" in lines
assert all("fixture-password" not in line for line in lines if line.startswith(("host=", "port=", "dbname=", "user=")))

for malformed in ("", "https://db.example.test/carr", "postgresql://db.example.test/a/b", "postgresql://u:p@db.example.test/carr?host=elsewhere", "postgresql://u:p@db.example.test/carr?service=other"):
    try:
        connection.service_lines(malformed)
    except ValueError:
        pass
    else:
        raise AssertionError(malformed)

with tempfile.TemporaryDirectory() as directory:
    service_file = Path(directory) / "service.conf"
    pass_file = Path(directory) / "passfile"
    service_file.touch()
    pass_file.touch()
    result = subprocess.run(
        [sys.executable, str(HELPER), "--write-service", str(service_file), "--write-passfile", str(pass_file)],
        input=DSN,
        text=True,
        capture_output=True,
        check=False,
        env={**os.environ},
    )
    assert result.returncode == 0 and result.stdout == "" and result.stderr == ""
    assert stat.S_IMODE(service_file.stat().st_mode) == stat.S_IMODE(pass_file.stat().st_mode) == 0o600
    assert "fixture-password" not in service_file.read_text(encoding="utf-8")
    assert "fixture-password" in pass_file.read_text(encoding="utf-8")

    # The first psql read happens before the later snapshot temp files and their
    # broader cleanup trap.  Force that read to fail and prove the service file
    # is still removed on this early-exit path.
    fixture = Path(directory) / "fixture"
    (fixture / "bin").mkdir(parents=True)
    (fixture / "ops").mkdir()
    copied_snapshot = fixture / "bin" / "schema-snapshot.sh"
    snapshot_text = SNAPSHOT.read_text(encoding="utf-8")
    psql_lookup = 'PSQL=""\nfor c in /opt/homebrew/opt/libpq/bin/psql /usr/local/opt/libpq/bin/psql psql; do'
    assert psql_lookup in snapshot_text
    copied_snapshot.write_text(snapshot_text.replace(psql_lookup, 'PSQL="$CARR_TEST_PSQL"\nfor c in; do'), encoding="utf-8")
    copied_snapshot.chmod(0o755)
    shutil.copyfile(HELPER, fixture / "ops" / "schema-snapshot-connection.py")
    # Hosted CI has no repository venv; exercise the python3 fallback here.
    neon = fixture / "mcp-server" / "node_modules" / ".bin" / "neonctl"
    neon.parent.mkdir(parents=True)
    neon.write_text("#!/bin/sh\nprintf '%s\\n' 'postgresql://owner:fixture-password@db.example.test/carr?sslmode=require'\n", encoding="utf-8")  # ci-secret-scan: allow — synthetic local fixture
    neon.chmod(0o755)
    fake_bin = fixture / "fake-bin"
    fake_bin.mkdir()
    (fake_bin / "python3").symlink_to(sys.executable)
    psql_marker = fixture / "psql-called"
    for name, body in {"pg_dump": "#!/bin/sh\necho 'pg_dump (PostgreSQL) 18.4'\n", "psql": "#!/bin/sh\ntouch \"$CARR_TEST_PSQL_MARKER\"\nexit 71\n", "mktemp": "#!/bin/sh\ncount_file=\"$CARR_TEST_TMP_COUNT\"\ncount=$(cat \"$count_file\" 2>/dev/null || printf 0)\ncount=$((count + 1))\nprintf '%s' \"$count\" > \"$count_file\"\npath=\"$CARR_TEST_TMP/private-$count\"\n: > \"$path\"\nprintf '%s\\n' \"$path\"\n"}.items():
        path = fake_bin / name
        path.write_text(body, encoding="utf-8")
        path.chmod(0o755)
    private_tmp = fixture / "tmp"
    private_tmp.mkdir()
    early_failure = subprocess.run(
        ["/bin/zsh", str(copied_snapshot)],
        text=True,
        capture_output=True,
        check=False,
        env={**os.environ, "PATH": f"{fake_bin}:/usr/bin:/bin", "CARR_TEST_TMP": str(private_tmp), "CARR_TEST_TMP_COUNT": str(fixture / "mktemp-count"), "CARR_TEST_PSQL": str(fake_bin / "psql"), "CARR_TEST_PSQL_MARKER": str(psql_marker)},
    )
    assert early_failure.returncode != 0 and psql_marker.exists()
    assert list(private_tmp.iterdir()) == []

source = SNAPSHOT.read_text(encoding="utf-8")
assert "unset URL" in source
assert "PGSERVICE=schema_snapshot" in source
assert "$URL" not in source.split("unset URL", 1)[1]
assert '"$PG_DUMP" --schema-only --no-owner --no-acl "$URL"' not in source
assert '"$PSQL" "$URL"' not in source

print("schema snapshot connection selftest: private service transport verified")
