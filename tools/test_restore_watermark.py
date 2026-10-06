"""Tests for tools/restore-watermark.py and bin/restore-rehearse.sh phase 5 (V5-F08 item 4).

Three layers:
  * unit — the COPY-block watermark (rows AND content digest), the exact
    comparison and the CLI exit codes, on fixture text;
  * the copy record and verify-restore — the producer's "Backup artifact"
    Check and the artifact API are read through the REAL
    ops/backup-workflow-status.py matching and cross-check code, with only its
    `api` transport, the `gh` download and the `age` binary replaced by
    fixtures. A run not on main, not scheduled or dispatched, whose commit is
    not in main, or whose backup workflow file differs from main's; a Check in
    a suite not bound to the run; or a summary that disagrees with the store
    are all refused. Review H1: verify-restore takes NO receipt, watermark or
    copy file; it looks the Check up, downloads, hashes, decrypts and counts
    the artifact, reads the restored target and the target's start instant
    itself. A hand-made receipt has no path in. The outbound census is read
    from a restored queue and the provider's own readers, never handed in;
  * end to end — a real pg_dump of a throwaway local cluster, restored with the
    rehearsal's OWN restore filter (read out of bin/restore-rehearse.sh, so the
    script text is what is tested) into a second throwaway database, then
    verify-restore run in-process against that real database (the real
    local-cluster start read, the real restored reader), and the bound receipt
    judged by mcp-server/bin/recovery-matrix-evaluate.mjs. Then one restored
    row is deleted, and separately one restored row is CHANGED with the count
    intact, and both must fail. Skips (reason printed) only when the
    PostgreSQL client/server binaries are not installed.

The `age` stand-in is a script that prints the stored file: the digest is
taken over whatever bytes are stored, and the decrypt is age's own.

  .venv/bin/python -m unittest tools/test_restore_watermark.py
"""
from __future__ import annotations

import hashlib
import importlib.util
import io
import json
import math
import os
import re
import shutil
import socket
import subprocess
import sys
import tempfile
import unittest
import zipfile
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest import mock

REPO = Path(__file__).resolve().parents[1]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))
from lib import recovery_evidence  # noqa: E402
from lib.disposable_pg_fixture import DisposablePostgres
TOOL = REPO / "tools" / "restore-watermark.py"
REHEARSE = REPO / "bin" / "restore-rehearse.sh"
EVALUATOR = REPO / "mcp-server" / "bin" / "recovery-matrix-evaluate.mjs"

spec = importlib.util.spec_from_file_location("restore_watermark", TOOL)
if spec is None or spec.loader is None:
    raise ImportError(f"cannot load {TOOL}")
rw = importlib.util.module_from_spec(spec)
spec.loader.exec_module(rw)


REAL_TRUSTED_BINARY = rw._trusted_binary


def pin_binaries(case: unittest.TestCase, bindir) -> None:
    """Point the verifier's trusted-binary resolution at fixture stand-ins for this test."""
    patcher = mock.patch.object(rw, "_trusted_binary", lambda name: str(Path(bindir) / name))
    patcher.start()
    case.addCleanup(patcher.stop)

DUMP = '''--
-- PostgreSQL database dump
--
CREATE TABLE public.party (id integer, name text);
COPY public.party (id, name) FROM stdin;
1\tAcme
2\tline\\nbreak
3\tback\\\\.slash
\\.
COPY public."Odd ""Name""" (id) FROM stdin;
7
\\.
COPY public.empty_t (id) FROM stdin;
\\.
COPY ops.run (id) FROM stdin;
1
2
\\.
-- PostgreSQL database dump complete
'''


def sha(lines: list[str]) -> str:
    h = hashlib.sha256()
    for line in sorted(x.encode() for x in lines):
        h.update(line + b"\n")
    return "sha256:" + h.hexdigest()


def D(ch: str) -> str:
    return "sha256:" + ch * 64


COPY_RECORD = {
    "copy_id": "carr-backup-20260924-run-101-attempt-1",
    "custody_domain": "github-actions-artifacts",
    "primary_domain": "neon-primary",
    "produced_at": "2026-09-24T03:00:00Z",
    "producer_id": "backup-nightly-workflow",
    "recorded_artifact_digest": D("a"),
    "recorded_digest_source": {"kind": "github_actions_backup_check", "check_run_id": 555, "workflow_run_id": 101},
    "store_readback_digest": D("a"),
}


def run_tool(*args, stdin=None, env=None):
    return subprocess.run([sys.executable, str(TOOL), *args], input=stdin, text=True, capture_output=True,
                          env=env)


class Watermark(unittest.TestCase):
    def test_rows_and_content_digest_per_qualified_table_including_quoted_and_empty(self):
        wm = rw.watermark_from_dump(DUMP.splitlines(keepends=True))
        self.assertEqual({t: e["rows"] for t, e in wm.items()},
                         {"public.party": 3, 'public.Odd "Name"': 1, "public.empty_t": 0, "ops.run": 2})
        self.assertEqual(wm["ops.run"]["content_digest"], sha(["1", "2"]))
        self.assertEqual(wm["public.empty_t"]["content_digest"], sha([]))
        self.assertEqual(wm["public.party"]["copy_columns"], "(id, name)")
        self.assertEqual(rw.count_copy_rows(DUMP.splitlines())["public.party"], 3)

    def test_content_digest_is_order_free_but_value_exact(self):
        swapped = DUMP.replace("1\n2\n\\.\n-- PostgreSQL", "2\n1\n\\.\n-- PostgreSQL")
        changed = DUMP.replace("1\tAcme", "1\tAcmf")
        base = rw.watermark_from_dump(DUMP.splitlines())
        self.assertEqual(rw.watermark_from_dump(swapped.splitlines()), base)
        moved = rw.watermark_from_dump(changed.splitlines())["public.party"]
        self.assertEqual(moved["rows"], 3)
        self.assertNotEqual(moved["content_digest"], base["public.party"]["content_digest"])

    def test_escaped_terminator_inside_a_row_is_a_row_and_only_the_exact_line_ends_a_block(self):
        # "3\tback\\\\.slash" is a data row whose text contains \. — never the terminator.
        self.assertEqual(rw.count_copy_rows(DUMP.splitlines())["public.party"], 3)
        # A row that merely STARTS with backslash-dot is data, not the end of the block.
        tricky = "COPY public.t (v) FROM stdin;\n\\.x\n\\.\n"
        self.assertEqual(rw.count_copy_rows(tricky.splitlines()), {"public.t": 1})

    def test_two_copy_blocks_for_one_table_raise(self):
        twice = DUMP + "COPY ops.run (id) FROM stdin;\n3\n\\.\n"
        with self.assertRaisesRegex(ValueError, "two COPY blocks"):
            rw.count_copy_rows(twice.splitlines())

    def test_truncated_stream_and_stream_without_blocks_raise(self):
        truncated = DUMP.split("\\.\nCOPY ops.run")[0].rsplit("\\.", 1)[0]
        with self.assertRaisesRegex(ValueError, "truncated"):
            rw.count_copy_rows(truncated.splitlines())
        with self.assertRaisesRegex(ValueError, "no COPY blocks"):
            rw.count_copy_rows(["-- nothing here\n"])

    def test_quoted_identifier_with_doubled_quotes_unquotes_exactly(self):
        self.assertIn('public.Odd "Name"', rw.count_copy_rows(DUMP.splitlines()))

    def test_compare_is_exact_on_rows_and_content_both_ways(self):
        a = {"public.party": {"rows": 3, "content_digest": D("1")}, "ops.run": {"rows": 2, "content_digest": D("2")}}
        self.assertEqual(rw.compare(a, json.loads(json.dumps(a))), [])
        self.assertEqual(rw.compare(a, {**a, "ops.run": {"rows": 1, "content_digest": D("2")}}),
                         [{"table": "ops.run", "artifact_rows": 2, "restored_rows": 1, "content_differs": False}])
        self.assertEqual(rw.compare(a, {**a, "ops.run": {"rows": 2, "content_digest": D("9")}}),
                         [{"table": "ops.run", "artifact_rows": 2, "restored_rows": 2, "content_differs": True}])
        self.assertEqual(rw.compare(a, {"public.party": a["public.party"]}),
                         [{"table": "ops.run", "artifact_rows": 2, "restored_rows": None, "content_differs": True}])
        self.assertEqual(rw.compare(a, {**a, "public.extra": {"rows": 0, "content_digest": D("0")}}),
                         [{"table": "public.extra", "artifact_rows": None, "restored_rows": 0, "content_differs": True}])

    def test_verify_restore_refuses_production_or_an_unnamed_branch_before_reading_anything(self):
        common = dict(repository="o/r", run_id=1, identity=Path("/nonexistent"), database="carr_restore",
                      work_dir=Path("/nonexistent"))
        with mock.patch.object(rw, "read_copy_record", side_effect=AssertionError("read")), \
             mock.patch.object(rw, "production_head_lsn", side_effect=AssertionError("head")):
            with self.assertRaisesRegex(ValueError, "target kind must be"):
                rw.verify_restore(target_kind="production", admin_dsn="host=/tmp", **common)
            with self.assertRaisesRegex(ValueError, "needs --project-id and the branch handed over"):
                rw.verify_restore(target_kind="disposable_branch", project_id="p", **common)
            with self.assertRaisesRegex(ValueError, "needs --project-id and the branch handed over"):
                rw.verify_restore(target_kind="disposable_branch", handoff=lambda: ("b", "d"), **common)
            with self.assertRaisesRegex(ValueError, "needs its admin DSN"):
                rw.verify_restore(target_kind="disposable_local_cluster", **common)
            for bad in ("", "Carr", "carr-restore", "x;drop", "a" * 64):
                with self.assertRaisesRegex(ValueError, "not a plain lower-case identifier"):
                    rw.verify_restore(target_kind="disposable_local_cluster", admin_dsn="host=/tmp",
                                      **{**common, "database": bad})

    def test_the_restore_stream_drops_exactly_the_scripts_statement_classes_and_never_copy_data(self):
        dump = (b"CREATE SCHEMA public;\nCREATE SCHEMA ops;\nALTER DEFAULT PRIVILEGES FOR ROLE x GRANT ALL;\n"
                b"GRANT ALL ON TABLE public.t TO x;\nREVOKE ALL ON SCHEMA public FROM PUBLIC;\n"
                b"ALTER TABLE public.t OWNER TO x;\nCOMMENT ON EXTENSION pgcrypto IS 'x';\n"
                b"CREATE TABLE public.t (v text);\nCOPY public.t (v) FROM stdin;\nGRANT \\.\nREVOKE me\n\\.\n"
                b"ALTER TABLE public.t ADD CONSTRAINT c CHECK (true);\n")
        kept = b"".join(rw.restore_stream(dump.splitlines(keepends=True)))
        self.assertEqual(kept, b"CREATE SCHEMA ops;\nCREATE TABLE public.t (v text);\nCOPY public.t (v) FROM stdin;\n"
                               b"GRANT \\.\nREVOKE me\n\\.\nALTER TABLE public.t ADD CONSTRAINT c CHECK (true);\n")
        # Outside COPY data it drops exactly what the rehearsal's own sed filter drops.
        statements = [ln for ln in dump.decode().splitlines(keepends=True) if not ln.startswith(("GRANT \\", "REVOKE me", "\\."))
                      and "FROM stdin" not in ln]
        sed = subprocess.run(["sed", "-E", _script_block(r"RESTORE_FILTER='([^']*)'")], input="".join(statements),
                             text=True, capture_output=True, check=True).stdout
        mine = b"".join(rw.restore_stream("".join(statements).encode().splitlines(keepends=True))).decode()
        self.assertEqual(mine, sed)

    def test_psql_gets_every_connection_parameter_in_its_environment(self):
        with mock.patch.dict(os.environ, {"PGPASSWORD": "stale", "PGSERVICE": "x", "KEEP": "1",
                                          "NEON_API_KEY": "provider-key", "GH_TOKEN": "t", "LANG": "en_US.UTF-8",
                                          "PATH": "/tmp/shim:/usr/bin"}):
            env = rw._pg_env("host=h.example.test port=5433 user=u password=pw dbname=d sslmode=require")
            base = rw._base_env()
        self.assertEqual({k: env[k] for k in ("PGHOST", "PGPORT", "PGUSER", "PGPASSWORD", "PGDATABASE", "PGSSLMODE")},
                         {"PGHOST": "h.example.test", "PGPORT": "5433", "PGUSER": "u", "PGPASSWORD": "pw",
                          "PGDATABASE": "d", "PGSSLMODE": "require"})
        self.assertNotIn("PGSERVICE", env)
        # Round-5 review: an ALLOW-list. The provider key and anything else not named never reach a child.
        for leaked in ("KEEP", "NEON_API_KEY", "GH_TOKEN"):
            self.assertNotIn(leaked, env)
            self.assertNotIn(leaked, base)
        self.assertEqual(env["LANG"], "en_US.UTF-8")
        self.assertEqual(env["PATH"], "/usr/bin:/bin")
        self.assertEqual(base, {k: v for k, v in env.items() if not k.startswith("PG")})
        self.assertEqual(set(base) - {"PATH"}, set(base) & set(rw.SAFE_ENV_KEYS))
        with self.assertRaisesRegex(ValueError, "unrecognised connection parameter"):
            rw._pg_env("host=h dbname=d service=s")

    def test_the_load_pipes_the_decrypted_filtered_dump_into_psql_with_nothing_on_its_argv(self):
        with tempfile.TemporaryDirectory() as tmp:
            t = Path(tmp)
            (t / "dump.age").write_bytes(b"CREATE SCHEMA public;\nCOPY public.t (v) FROM stdin;\nGRANT x\n\\.\n")
            (t / "id").write_text("x\n")
            (t / "age").write_text("#!/bin/sh\ncat \"$4\"\n")
            (t / "psql").write_text(f"#!/bin/sh\necho \"$*\" > '{t}/argv'\necho \"$PGDATABASE|$PGPASSWORD\" > '{t}/env'\n"
                                    f"env > '{t}/psql.env'\ncat > '{t}/stdin'\n"
                                    f"[ -f '{t}/fail' ] && {{ cat '{t}/fail' >&2; exit 3; }}\nexit 0\n")
            for tool in ("age", "psql"):
                (t / tool).chmod(0o755)
            with mock.patch.object(rw, "_trusted_binary", lambda name: str(t / name)):
                rw.load_artifact(t / "dump.age", t / "id", "host=/tmp dbname=target password=pw", t)
                self.assertEqual((t / "stdin").read_bytes(), b"COPY public.t (v) FROM stdin;\nGRANT x\n\\.\n")
                self.assertEqual((t / "argv").read_text().strip(),
                                 "-X -q -v ON_ERROR_STOP=1 -v VERBOSITY=terse -v SHOW_CONTEXT=never")
                self.assertEqual((t / "env").read_text().strip(), "target|pw")
                # Round-5 review: a failed COPY's offending row never leaves psql's stderr.
                (t / "fail").write_text(
                    'psql:<stdin>:5: ERROR:  invalid input syntax for type uuid: "prod-secret-row"\n'
                    "CONTEXT:  COPY party, line 7: \"prod-secret-row\tJane Client\t555-0100\"\n"
                    "DETAIL:  Failing row contains (7, Jane Client, 555-0100).\n"
                    "7\tJane Client\t555-0100\n")
                with mock.patch.dict(os.environ, {"NEON_API_KEY": "provider-key", "UNLISTED": "x"}):
                    with self.assertRaisesRegex(ValueError, r"did not decrypt and load \(age 0, psql 3\)") as raised:
                        rw.load_artifact(t / "dump.age", t / "id", "host=/tmp dbname=target", t)
                message = str(raised.exception)
                on_disk = (t / "load.err").read_text()
                for text in (message, on_disk):
                    self.assertIn("ERROR:  invalid input syntax for type uuid: <redacted>", text)
                    for leak in ("prod-secret-row", "Jane", "555-0100", "CONTEXT", "DETAIL"):
                        self.assertNotIn(leak, text)
                # Round-5 review: psql's environment is the allow-list plus its PG* variables.
                psql_env = dict(ln.split("=", 1) for ln in (t / "psql.env").read_text().splitlines() if "=" in ln)
                self.assertNotIn("NEON_API_KEY", psql_env)
                self.assertNotIn("UNLISTED", psql_env)
                self.assertEqual(psql_env["PATH"], "/usr/bin:/bin")
                (t / "fail").unlink()
                (t / "age").write_text(f"#!/bin/sh\nenv > '{t}/age.env'\necho 'age: error: no identity matched' >&2\nexit 1\n")
                with mock.patch.dict(os.environ, {"NEON_API_KEY": "provider-key"}):
                    with self.assertRaisesRegex(ValueError, r"\(age 1, psql 0\).*no identity matched"):
                        rw.load_artifact(t / "dump.age", t / "id", "host=/tmp dbname=target", t)
                self.assertNotIn("NEON_API_KEY", (t / "age.env").read_text())
                # the counting decrypt gets the same allow-listed environment
                with mock.patch.dict(os.environ, {"NEON_API_KEY": "provider-key"}):
                    with self.assertRaisesRegex(ValueError, "age could not decrypt"):
                        rw.artifact_watermark_from(t / "dump.age", t / "id")
                self.assertNotIn("NEON_API_KEY", (t / "age.env").read_text())
                self.assertIn("PATH=/usr/bin:/bin", (t / "age.env").read_text())

    def test_redact_errors_keeps_only_error_lines_and_no_values(self):
        raw = ('psql:<stdin>:12: ERROR:  duplicate key value violates unique constraint "party_pkey"\n'
               "DETAIL:  Key (id)=(42) already exists.\n"
               "CONTEXT:  COPY party, line 3\n"
               "42\tJane\n"
               "LINE 1: insert into x values ('Jane')\n"
               "HINT:  something\n"
               "ERROR:  value too long for type character varying(10)\n"
               "FATAL:  password authentication failed for user 'neondb_owner'\n"
               "age: error: no identity matched any of the recipients\n"
               "random noise\n")
        self.assertEqual(rw.redact_errors(raw), [
            "psql:<stdin>:12: ERROR:  duplicate key value violates unique constraint <redacted>",
            "ERROR:  value too long for type character varying<redacted>",
            "FATAL:  password authentication failed for user <redacted>",
            "age: error: no identity matched any of the recipients",
        ])
        self.assertEqual(rw.redact_errors(""), [])
        self.assertEqual(len(rw.redact_errors("ERROR: " + "x" * 1000)[0]), 300)
        out = run_tool("redact-errors", stdin=raw)
        self.assertEqual(out.returncode, 0)
        self.assertNotIn("Jane", out.stdout)
        self.assertNotIn("42", out.stdout.replace("psql:<stdin>:12", ""))



class Cli(unittest.TestCase):
    def test_count_digest_compare_exit_codes(self):
        with tempfile.TemporaryDirectory() as tmp:
            t = Path(tmp)
            out = run_tool("count", stdin=DUMP)
            self.assertEqual(out.returncode, 0, out.stderr)
            (t / "a.json").write_text(out.stdout)
            restored = {k: {"rows": v["rows"], "content_digest": v["content_digest"]} for k, v in json.loads(out.stdout).items()}
            (t / "r.json").write_text(json.dumps(restored))
            self.assertEqual(run_tool("compare", "--artifact", str(t / "a.json"), "--restored", str(t / "r.json")).returncode, 0)
            restored["public.party"]["content_digest"] = D("f")
            (t / "r.json").write_text(json.dumps(restored))
            bad = run_tool("compare", "--artifact", str(t / "a.json"), "--restored", str(t / "r.json"))
            self.assertEqual(bad.returncode, 1)
            self.assertIn("MISMATCH public.party: artifact=3 restored=3 content_differs=true", bad.stdout)
            (t / "f").write_bytes(b"abc")
            self.assertEqual(run_tool("digest", str(t / "f")).stdout.strip(),
                             "sha256:ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad")
            self.assertEqual(run_tool("count", stdin="COPY public.t (id) FROM stdin;\n1\n").returncode, 2)

    def test_restored_reads_its_dsn_from_the_environment_only(self):
        with tempfile.TemporaryDirectory() as tmp:
            (Path(tmp) / "a.json").write_text("{}")
            env = {k: v for k, v in os.environ.items() if k != "RESTORE_DSN"}
            out = run_tool("restored", "--artifact", str(Path(tmp) / "a.json"), env=env)
            self.assertEqual(out.returncode, 2)
            self.assertIn("RESTORE_DSN is empty", out.stderr)
            # there is no argument that takes a DSN
            self.assertEqual(run_tool("restored", "--artifact", "x", "--dsn", "postgresql://h/d").returncode, 2)


class RestoredReader(unittest.TestCase):
    """`restored` against a stand-in driver: chunk boundaries, session settings, column lists."""

    def fake_psycopg(self, chunks_by_table, executed):
        class Copy:
            def __init__(self, chunks):
                self.chunks = chunks

            def __enter__(self):
                return iter(self.chunks)

            def __exit__(self, *exc):
                return False

        class Cursor:
            def __enter__(self):
                return self

            def __exit__(self, *exc):
                return False

            def execute(self, sql, params=None):
                executed.append((sql, params))

            def fetchall(self):
                return [tuple(k.split(".")) for k in chunks_by_table]

            def copy(self, statement):
                executed.append((statement, None))
                table = next(k for k in chunks_by_table if f'"{k.split(".")[1]}"' in statement)
                return Copy(chunks_by_table[table])

        class Conn:
            def __enter__(self):
                return self

            def __exit__(self, *exc):
                return False

            def cursor(self):
                return Cursor()

        def connect(dsn, autocommit, options=None):
            executed.append(("connect-options", options))
            return Conn()

        return mock.Mock(connect=connect)

    def test_rows_split_across_chunks_are_reassembled_and_hashed_like_the_artifact(self):
        executed: list = []
        driver = self.fake_psycopg({"public.party": [b"1\tAc", b"me\n2\tline\\nbreak\n", b"3\tback\\\\.slash\n"]}, executed)
        artifact = rw.watermark_from_dump(DUMP.splitlines())
        with mock.patch.dict(sys.modules, {"psycopg": driver}):
            got = rw.restored_watermark("dsn", artifact)
        self.assertEqual(got["public.party"], {k: artifact["public.party"][k] for k in ("rows", "content_digest")})
        # K5: read-only from the very first statement, not only after a set_config
        self.assertEqual(executed[0], ("connect-options", "-c default_transaction_read_only=on"))
        self.assertIn(("select set_config(%s, %s, false)", ("default_transaction_read_only", "on")), executed)
        self.assertIn(("select set_config(%s, %s, false)", ("TimeZone", "UTC")), executed)
        self.assertIn(('COPY "public"."party" (id, name) TO STDOUT', None), executed)

    def test_output_that_does_not_end_in_a_newline_is_refused(self):
        driver = self.fake_psycopg({"public.party": [b"1\tAcme\n2\tcut"]}, [])
        with mock.patch.dict(sys.modules, {"psycopg": driver}):
            with self.assertRaisesRegex(ValueError, "did not end with a newline"):
                rw.restored_watermark("dsn", {})


# ── the copy record, read from the provider ──────────────────────────────────

RUN_ID, ATTEMPT, HEAD, SUITE, OTHER_SUITE = 101, 1, "a" * 40, 9001, 9002
REPO_SLUG = "jbookout/carr-system"


def _zip_bytes() -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("carr-20260924.sql.age", b"age-encrypted-bytes")
    return buf.getvalue()


ZIP = _zip_bytes()
ZIP_DIGEST = "sha256:" + hashlib.sha256(ZIP).hexdigest()


class FakeProvider:
    """The GitHub API as ops/backup-workflow-status.py sees it, for one nightly run."""

    def __init__(self):
        self.artifact = {
            "id": 77, "name": f"carr-backup-20260924-run-{RUN_ID}-attempt-{ATTEMPT}", "digest": ZIP_DIGEST,
            "size_in_bytes": len(ZIP), "created_at": "2026-09-24T03:05:00Z", "expires_at": "2099-01-01T00:00:00Z",
            "expired": False, "workflow_run": {"id": RUN_ID, "run_attempt": ATTEMPT, "head_sha": HEAD},
        }
        self.summary = {
            "artifact_id": 77, "artifact_name": self.artifact["name"], "artifact_digest": ZIP_DIGEST,
            "artifact_bytes": len(ZIP), "artifact_created_at": "2026-09-24T03:05:00Z",
            "artifact_expires_at": "2099-01-01T00:00:00Z", "required_steps": ["dump", "encrypt", "upload", "readback"],
        }
        self.check = {
            "id": 555, "name": "Backup artifact", "head_sha": HEAD, "status": "completed", "conclusion": "success",
            "external_id": json.dumps({"head_sha": HEAD, "repository": REPO_SLUG, "run_attempt": ATTEMPT, "run_id": RUN_ID},
                                      sort_keys=True, separators=(",", ":")),
            "app": {"id": 15368, "slug": "github-actions"}, "check_suite": {"id": SUITE},
            "started_at": "2026-09-24T03:01:00Z", "completed_at": "2026-09-24T03:06:00Z",
            "output": {"summary": None},
        }
        self.run = {"id": RUN_ID, "run_attempt": ATTEMPT, "head_sha": HEAD, "conclusion": "success",
                    "path": ".github/workflows/backup-nightly.yml", "check_suite_id": SUITE,
                    "head_branch": "main", "event": "schedule",
                    "run_started_at": "2026-09-24T03:00:30Z", "updated_at": "2026-09-24T03:07:00Z"}
        # As observed live: the Check lands in ANOTHER github-actions suite on the same head.
        self.suites = {OTHER_SUITE: {"id": OTHER_SUITE, "app": {"id": 15368, "slug": "github-actions"},
                                     "head_branch": "main", "head_sha": HEAD}}
        self.compare = {"status": "ahead", "ahead_by": 3, "behind_by": 0}
        self.extra_checks: list[dict] = []
        # the backup workflow file's git blob at each ref (contents API)
        self.blobs: dict[str, object] = {HEAD: "c" * 40, "main": "c" * 40}
        self.zip = ZIP

    def api(self, path, *, method="GET", body=None, query=None):
        self.check["output"]["summary"] = json.dumps(self.summary)
        if path == f"/repos/{REPO_SLUG}/actions/runs/{RUN_ID}":
            return self.run
        if path == f"/repos/{REPO_SLUG}/commits/{HEAD}/check-runs":
            rows = [self.check, *self.extra_checks]
            return {"check_runs": rows, "total_count": len(rows)}
        if path == f"/repos/{REPO_SLUG}/actions/runs/{RUN_ID}/artifacts":
            return {"artifacts": [self.artifact], "total_count": 1}
        if path == f"/repos/{REPO_SLUG}/compare/{HEAD}...main":
            return self.compare
        if path == f"/repos/{REPO_SLUG}/contents/.github/workflows/backup-nightly.yml":
            sha_ = self.blobs.get((query or {}).get("ref"))
            return {"sha": sha_, "path": ".github/workflows/backup-nightly.yml"} if sha_ is not None else []
        if path.startswith(f"/repos/{REPO_SLUG}/check-suites/"):
            # an unknown suite answers with something that is not a suite object
            return self.suites.get(int(path.rsplit("/", 1)[1]), [])
        raise AssertionError(f"unexpected API path {path}")


class CopyRecordFromProvider(unittest.TestCase):
    def setUp(self):
        self.fake = FakeProvider()
        pin_binaries(self, "/pinned")
        real = rw._status_module()
        real.api = self.fake.api
        patcher = mock.patch.object(rw, "_status_module", return_value=real)
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_the_record_is_the_producers_check_cross_checked_against_the_store(self):
        rec = rw.read_copy_record(REPO_SLUG, RUN_ID)
        self.assertEqual(rec["recorded_artifact_digest"], ZIP_DIGEST)
        self.assertEqual(rec["store_readback_digest"], ZIP_DIGEST)
        self.assertEqual(rec["produced_at"], "2026-09-24T03:05:00Z")
        self.assertEqual(rec["recorded_digest_source"],
                         {"kind": "github_actions_backup_check", "check_run_id": 555, "workflow_run_id": RUN_ID})

    def test_g1_a_check_in_another_actions_suite_on_this_main_head_inside_the_run_window_is_accepted(self):
        # The live shape: GITHUB_TOKEN Checks do not land in their own run's suite.
        self.fake.check["check_suite"] = {"id": OTHER_SUITE}
        self.assertEqual(rw.read_copy_record(REPO_SLUG, RUN_ID)["recorded_digest_source"]["check_run_id"], 555)

    def test_g1_a_check_in_a_suite_not_bound_to_this_run_fails_closed(self):
        self.fake.check["check_suite"] = {"id": OTHER_SUITE}
        cases = [
            lambda s: s.update(app={"id": 1, "slug": "someone-else"}),
            lambda s: s.update(head_branch="feature"),
            lambda s: s.update(head_sha="b" * 40),
        ]
        for mutate in cases:
            original = json.loads(json.dumps(self.fake.suites[OTHER_SUITE]))
            mutate(self.fake.suites[OTHER_SUITE])
            with self.assertRaisesRegex(ValueError, "found 0 .*refusing"):
                rw.read_copy_record(REPO_SLUG, RUN_ID)
            self.fake.suites[OTHER_SUITE] = original
        self.fake.check["check_suite"] = {"id": 424242}  # a suite the provider cannot read back
        with self.assertRaisesRegex(ValueError, "found 0"):
            rw.read_copy_record(REPO_SLUG, RUN_ID)

    def test_h2_the_checks_creator_set_times_decide_nothing(self):
        # started_at/completed_at are whatever the Check's creator sent, so they bind nothing.
        self.fake.check.update(started_at="2020-01-01T00:00:00Z", completed_at=None)
        self.assertEqual(rw.read_copy_record(REPO_SLUG, RUN_ID)["recorded_digest_source"]["check_run_id"], 555)

    def test_h2_a_run_whose_backup_workflow_file_differs_from_mains_is_refused(self):
        for at_run, on_main in (("d" * 40, "c" * 40), (None, "c" * 40), ("c" * 40, None), ("C" * 40, "C" * 40),
                                ("short", "short")):
            self.fake.blobs = {HEAD: at_run, "main": on_main}
            with self.assertRaisesRegex(ValueError, "is not the one on main now"):
                rw.read_copy_record(REPO_SLUG, RUN_ID)
        self.fake.blobs = {HEAD: "e" * 40, "main": "e" * 40}
        self.assertEqual(rw.read_copy_record(REPO_SLUG, RUN_ID)["copy_id"], self.fake.artifact["name"])

    def test_h2_the_check_is_looked_up_never_named_by_a_caller(self):
        import inspect
        self.assertEqual(list(inspect.signature(rw.read_copy_record).parameters), ["repository", "run_id"])
        self.assertEqual(list(inspect.signature(rw.fetch_copy).parameters), ["repository", "run_id", "out_dir"])
        self.assertNotIn("check_run_id", inspect.signature(rw.verify_restore).parameters)

    def test_g1_only_a_scheduled_or_dispatched_run_on_main_whose_commit_is_in_main_counts(self):
        for field, bad, message in (("head_branch", "pr-branch", "not main"),
                                    ("event", "pull_request", "not schedule or workflow_dispatch"),
                                    ("event", "push", "not schedule or workflow_dispatch")):
            original = self.fake.run[field]
            self.fake.run[field] = bad
            with self.assertRaisesRegex(ValueError, message):
                rw.read_copy_record(REPO_SLUG, RUN_ID)
            self.fake.run[field] = original
        self.fake.run["event"] = "workflow_dispatch"
        self.assertEqual(rw.read_copy_record(REPO_SLUG, RUN_ID)["copy_id"], self.fake.artifact["name"])
        for compare in ({"status": "diverged", "ahead_by": 2, "behind_by": 1},
                        {"status": "diverged", "ahead_by": 2, "behind_by": 0},  # status alone decides too
                        {"status": "behind", "ahead_by": 0, "behind_by": 4},
                        {"status": "ahead", "ahead_by": 1, "behind_by": 1}, {}, []):
            self.fake.compare = compare
            with self.assertRaisesRegex(ValueError, "not an ancestor of main"):
                rw.read_copy_record(REPO_SLUG, RUN_ID)
        self.fake.compare = {"status": "identical", "ahead_by": 0, "behind_by": 0}
        self.assertEqual(rw.read_copy_record(REPO_SLUG, RUN_ID)["copy_id"], self.fake.artifact["name"])

    def test_a_forged_second_check_with_the_same_name_and_envelope_does_not_count(self):
        forged = json.loads(json.dumps(self.fake.check))
        forged.update(id=556, app={"id": 1, "slug": "someone-else"})
        self.fake.extra_checks.append(forged)
        self.assertEqual(rw.read_copy_record(REPO_SLUG, RUN_ID)["recorded_digest_source"]["check_run_id"], 555)

    def test_two_authentic_checks_for_one_run_are_ambiguous_and_refused(self):
        twin = json.loads(json.dumps(self.fake.check))
        twin["id"] = 557
        self.fake.extra_checks.append(twin)
        with self.assertRaisesRegex(ValueError, "found 2"):
            rw.read_copy_record(REPO_SLUG, RUN_ID)

    def test_fetch_copy_refuses_an_archive_that_is_not_exactly_one_dump(self):
        for members in ({"carr-20260924.sql.age": b"x", "extra.txt": b"y"}, {"notes.txt": b"x"}):
            buf = io.BytesIO()
            with zipfile.ZipFile(buf, "w") as zf:
                for name, data in members.items():
                    zf.writestr(name, data)
            blob = buf.getvalue()
            self.fake.artifact.update(digest="sha256:" + hashlib.sha256(blob).hexdigest(), size_in_bytes=len(blob))
            self.fake.summary.update(artifact_digest=self.fake.artifact["digest"], artifact_bytes=len(blob))
            with tempfile.TemporaryDirectory() as tmp:
                t = Path(tmp)
                (t / "artifact.bin").write_bytes(blob)
                gh = t / "bin" / "gh"
                gh.parent.mkdir()
                gh.write_text(f"#!/bin/sh\ncat '{t / 'artifact.bin'}'\n")
                gh.chmod(0o755)
                with mock.patch.object(rw, "_trusted_binary", lambda name, d=gh.parent: str(d / name)):
                    with self.assertRaisesRegex(ValueError, "exactly one carr-YYYYMMDD.sql.age"):
                        rw.fetch_copy(REPO_SLUG, RUN_ID, t / "out")

    def test_a_summary_that_disagrees_with_the_store_is_refused(self):
        self.fake.artifact["digest"] = D("b")
        with self.assertRaisesRegex(ValueError, "does not match the artifact"):
            rw.read_copy_record(REPO_SLUG, RUN_ID)

    def test_a_run_of_another_workflow_or_a_failed_run_is_refused(self):
        self.fake.run["path"] = ".github/workflows/ci.yml"
        with self.assertRaisesRegex(ValueError, "not a run of"):
            rw.read_copy_record(REPO_SLUG, RUN_ID)
        self.fake.run.update(path=".github/workflows/backup-nightly.yml", conclusion="failure")
        with self.assertRaisesRegex(ValueError, "did not conclude success"):
            rw.read_copy_record(REPO_SLUG, RUN_ID)

    def test_fetch_copy_downloads_extracts_and_writes_the_record(self):
        with tempfile.TemporaryDirectory() as tmp:
            t = Path(tmp)
            (t / "artifact.bin").write_bytes(ZIP)
            gh = t / "bin" / "gh"
            gh.parent.mkdir()
            gh.write_text(f"#!/bin/sh\ncat '{t / 'artifact.bin'}'\n")
            gh.chmod(0o755)
            with mock.patch.object(rw, "_trusted_binary", lambda name, d=gh.parent: str(d / name)):
                archive, dump = rw.fetch_copy(REPO_SLUG, RUN_ID, t / "out")
            self.assertEqual(rw.file_digest(archive), ZIP_DIGEST)
            self.assertEqual(dump.read_bytes(), b"age-encrypted-bytes")
            record = json.loads((t / "out" / "copy.json").read_text())
            self.assertEqual(sorted(record), sorted(rw.COPY_KEYS))



def _zip_of(member: str, data: bytes) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr(member, data)
    return buf.getvalue()


class VerifyRestore(unittest.TestCase):
    """Review H1: every decisive fact in the receipt is read by verify-restore itself."""

    NOW = datetime(2026, 9, 24, 10, 30, tzinfo=timezone.utc)
    server_now = NOW

    def setUp(self):
        self.fake = FakeProvider()
        self.tmp = Path(tempfile.mkdtemp(prefix="carr-f08-verify-"))
        pin_binaries(self, self.tmp / "bin")
        real = rw._status_module()
        real.api = self.fake.api
        patcher = mock.patch.object(rw, "_status_module", return_value=real)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.serve(DUMP.encode())
        bindir = self.tmp / "bin"
        bindir.mkdir()
        (bindir / "gh").write_text(f"#!/bin/sh\ncat '{self.tmp / 'artifact.bin'}'\n")
        # The age stand-in prints the stored file: `age --decrypt -i IDENTITY FILE`.
        (bindir / "age").write_text("#!/bin/sh\n[ \"$1\" = --decrypt ] && [ \"$2\" = -i ] && [ -f \"$3\" ] || exit 1\ncat \"$4\"\n")
        for tool in ("gh", "age"):
            (bindir / tool).chmod(0o755)
        self.identity = self.tmp / "identity.txt"
        self.identity.write_text("stand-in identity\n")

    def serve(self, plaintext: bytes) -> None:
        blob = _zip_of("carr-20260924.sql.age", plaintext)
        (self.tmp / "artifact.bin").write_bytes(blob)
        digest = "sha256:" + hashlib.sha256(blob).hexdigest()
        self.fake.artifact.update(digest=digest, size_in_bytes=len(blob))
        self.fake.summary.update(artifact_digest=digest, artifact_bytes=len(blob))
        self.digest = digest

    def verify(self, restored=None, work="w"):
        artifact_seen = {}

        def restored_reader(dsn, artifact):
            self.assertEqual(dsn, "host=/tmp/sock dbname=carr_restore")  # the database it created
            artifact_seen.update(artifact)
            got = rw.strip_columns(artifact) if restored is None else restored
            return json.loads(json.dumps(got))

        admin = "host=/tmp/sock dbname=postgres"
        target = "host=/tmp/sock dbname=carr_restore"
        with mock.patch.object(rw, "local_target_start", return_value="2026-09-24T10:00:00Z") as start, \
             mock.patch.object(rw, "create_target_database", return_value=target) as create, \
             mock.patch.object(rw, "load_artifact") as load, \
             mock.patch.object(rw, "server_clock", return_value=self.server_now) as clock, \
             mock.patch.object(rw, "restored_watermark", restored_reader):
            receipt = rw.verify_restore(repository=REPO_SLUG, run_id=RUN_ID, identity=self.identity,
                                        target_kind="disposable_local_cluster", database="carr_restore",
                                        admin_dsn=admin, work_dir=self.tmp / work, now=lambda: self.NOW)
        start.assert_called_once_with(admin)
        # it creates the database, loads the DOWNLOADED dump into it, and reads THAT database back
        create.assert_called_once_with(admin, "carr_restore")
        self.assertEqual(load.call_args.args[1:3], (self.identity, target))
        self.assertTrue(str(load.call_args.args[0]).endswith("carr-20260924.sql.age"))
        clock.assert_called_once_with(target)
        return receipt, artifact_seen

    def evaluate(self, receipt):
        path = self.tmp / "receipt.json"
        path.write_text(json.dumps(receipt))
        return subprocess.run(["node", "--input-type=module", "-e",
                               f"import {{evaluateRestoreExercise as e}} from '{REPO / 'mcp-server/src/recovery-matrix.v5.js'}';"
                               "import fs from 'node:fs';"
                               f"console.log(JSON.stringify(e(JSON.parse(fs.readFileSync('{path}','utf8')),"
                               f"{{now_ms:Date.parse('2026-09-24T10:31:00Z')}})))"],
                              capture_output=True, text=True, check=True)

    def test_the_receipt_is_built_from_this_processs_own_reads_and_bound(self):
        receipt, artifact_seen = self.verify()
        wm = rw.watermark_from_dump(DUMP.splitlines())
        self.assertEqual(artifact_seen, wm)  # decrypted and counted from the DOWNLOADED artifact
        self.assertEqual(receipt["artifact_watermark"], rw.strip_columns(wm))
        self.assertEqual(receipt["observed_artifact_digest"], self.digest)
        self.assertEqual(receipt["copy"]["recorded_artifact_digest"], self.digest)
        self.assertEqual(receipt["copy"]["recorded_digest_source"],
                         {"kind": "github_actions_backup_check", "check_run_id": 555, "workflow_run_id": RUN_ID})
        self.assertEqual(sorted(receipt["copy"]), sorted(rw.COPY_KEYS))
        self.assertEqual((receipt["started_at"], receipt["finished_at"]), ("2026-09-24T10:00:00Z", "2026-09-24T10:30:00Z"))
        self.assertEqual((receipt["target_kind"], receipt["oracle_id"]), ("disposable_local_cluster", "restore-rehearse"))
        facts = {k: v for k, v in receipt.items() if k != "verification"}
        self.assertEqual(receipt["verification"], {"verifier": "tools/restore-watermark.py verify-restore",
                                                   "verified_at": "2026-09-24T10:30:00Z",
                                                   "facts_digest": recovery_evidence.facts_digest(facts)})
        self.assertEqual(json.loads(self.evaluate(receipt).stdout)["reason_id"], "restore_exercise_exact")

    def test_a_restored_target_that_differs_from_the_artifact_fails_the_evaluator(self):
        wm = rw.strip_columns(rw.watermark_from_dump(DUMP.splitlines()))
        lost = {**wm, "ops.run": {"rows": 1, "content_digest": sha(["1"])}}
        receipt, _ = self.verify(restored=lost)
        self.assertEqual(json.loads(self.evaluate(receipt).stdout)["reason_id"], "watermark_mismatch")

    def test_h1_a_hand_made_receipt_has_no_path_in(self):
        # The reviewer's reproduction: the REAL run's copy block wrapped around an
        # invented one-table restore. Round 2's verify-receipt re-read the copy
        # block, stamped it and passed it; that command is gone, and no command
        # takes a receipt, a watermark or a copy record from a file.
        rec = rw.read_copy_record(REPO_SLUG, RUN_ID)
        rec.pop("_artifact_id")
        one = {"public.t": {"rows": 1, "content_digest": D("1")}}
        forged = {"receipt_kind": rw.RECEIPT_KIND, "target_kind": "disposable_branch", "copy": rec,
                  "oracle_id": "restore-rehearse", "observed_artifact_digest": rec["recorded_artifact_digest"],
                  "artifact_watermark": one, "restored_watermark": one,
                  "started_at": "2026-09-24T10:00:00Z", "finished_at": "2026-09-24T10:01:00Z"}
        path = self.tmp / "forged.json"
        path.write_text(json.dumps(forged))
        for argv in (["verify-receipt", "--repository", REPO_SLUG, str(path)],
                     ["receipt", "--copy-record", str(path)],
                     ["verify-restore", "--repository", REPO_SLUG, "--run-id", str(RUN_ID), "--identity", str(self.identity),
                      "--target-kind", "disposable_local_cluster", "--database", "x", "--work-dir", str(self.tmp),
                      "--receipt", str(path)],
                     ["verify-restore", "--repository", REPO_SLUG, "--run-id", str(RUN_ID), "--identity", str(self.identity),
                      "--target-kind", "disposable_local_cluster", "--database", "x", "--work-dir", str(self.tmp),
                      "--artifact", str(path)],
                     ["verify-restore", "--repository", REPO_SLUG, "--run-id", str(RUN_ID), "--identity", str(self.identity),
                      "--target-kind", "disposable_branch", "--database", "x", "--work-dir", str(self.tmp),
                      "--branch-id", "br-1"]):
            with self.assertRaises(SystemExit) as refused, mock.patch("sys.stderr", io.StringIO()):
                rw.main(argv)
            self.assertEqual(refused.exception.code, 2)
        self.assertFalse(hasattr(rw, "verify_receipt") or hasattr(rw, "build_receipt"))
        # Files left in the work directory (fetch-copy's operator copy, a planted
        # watermark) are overwritten or ignored: the verdict comes from the reads.
        work = self.tmp / "w"
        work.mkdir()
        (work / "copy.json").write_text(json.dumps({**rec, "recorded_artifact_digest": D("f")}))
        (work / "artifact.json").write_text(json.dumps(one))
        receipt, _ = self.verify(work="w")
        self.assertNotEqual(receipt["artifact_watermark"], one)
        self.assertEqual(receipt["copy"]["recorded_artifact_digest"], self.digest)

    def test_the_artifact_watermark_is_the_downloaded_copys_not_a_claim(self):
        # A copy that carries the core tables but not the rest: the verifier counts
        # what the DOWNLOADED copy carries, and a full restore no longer matches it.
        self.serve(b"COPY public.party (id) FROM stdin;\n1\n\\.\nCOPY ops.run (id) FROM stdin;\n1\n\\.\n")
        full = rw.strip_columns(rw.watermark_from_dump(DUMP.splitlines()))
        receipt, _ = self.verify(restored=full)
        self.assertEqual(sorted(receipt["artifact_watermark"]), ["ops.run", "public.party"])
        self.assertEqual(json.loads(self.evaluate(receipt).stdout)["reason_id"], "watermark_mismatch")

    def test_k2_an_artifact_without_rows_in_every_core_table_is_refused(self):
        # The reviewer's shim printed ONE COPY row; that is not a record-layer dump.
        for plaintext in (b"COPY public.t (id) FROM stdin;\n1\n\\.\n",
                          b"COPY public.party (id) FROM stdin;\n1\n\\.\n",
                          b"COPY public.party (id) FROM stdin;\n1\n\\.\nCOPY ops.run (id) FROM stdin;\n\\.\n"):
            self.serve(plaintext)
            with self.assertRaisesRegex(ValueError, "no rows for core table"):
                self.verify()

    def test_k2_a_path_shim_is_never_used(self):
        # A shim `age` first on PATH that prints a plausible dump: it is not consulted.
        shim = self.tmp / "shim"
        shim.mkdir()
        (shim / "age").write_text("#!/bin/sh\nprintf 'COPY public.party (id) FROM stdin;\\n1\\n\\\\.\\n'\n")
        (shim / "age").chmod(0o755)
        empty = self.tmp / "no-binaries-here"
        empty.mkdir()
        with mock.patch.dict(os.environ, {"PATH": f"{shim}:{os.environ['PATH']}"}), \
             mock.patch.object(rw, "TRUSTED_BIN_DIRS", (str(empty),)):
            with self.assertRaisesRegex(ValueError, "age is not installed in any of"):
                REAL_TRUSTED_BINARY("age")
            # and the shim's directory, named as trusted, is refused: it is a temp directory
            with mock.patch.object(rw, "TRUSTED_BIN_DIRS", (str(shim),)):
                with self.assertRaisesRegex(ValueError, "refusing age .*lives under"):
                    REAL_TRUSTED_BINARY("age")
        # a trusted-looking directory whose entry resolves into the repository is refused
        link_dir = self.tmp / "linkdir"
        link_dir.mkdir()
        (link_dir / "age").symlink_to(REPO / "bin" / "restore-rehearse.sh")
        with mock.patch.object(rw, "TRUSTED_BIN_DIRS", (str(link_dir),)):
            with self.assertRaisesRegex(ValueError, f"lives under {REPO}"):
                REAL_TRUSTED_BINARY("age")
        # a group- or world-writable binary is refused wherever it lives
        loose = self.tmp / "loose"
        loose.write_text("#!/bin/sh\n")
        loose.chmod(0o777)
        self.assertEqual(rw._untrusted_location(loose.resolve(), roots=()), "it is group- or world-writable")
        # a non-executable file, or a directory, with the name is skipped, not used
        first, second = self.tmp / "d1", self.tmp / "d2"
        for d in (first, second):
            d.mkdir()
        (first / "age").write_text("#!/bin/sh\n")
        (first / "age").chmod(0o644)
        (first / "gh").mkdir()
        for name in ("age", "gh"):
            (second / name).write_text("#!/bin/sh\n")
            (second / name).chmod(0o755)
        with mock.patch.object(rw, "TRUSTED_BIN_DIRS", (str(first), str(second))), \
             mock.patch.object(rw, "_untrusted_roots", lambda: ()):
            self.assertEqual(REAL_TRUSTED_BINARY("age"), str((second / "age").resolve()))
            self.assertEqual(REAL_TRUSTED_BINARY("gh"), str((second / "gh").resolve()))
        loose.chmod(0o775)  # group-writable alone is enough
        self.assertEqual(rw._untrusted_location(loose.resolve(), roots=()), "it is group- or world-writable")
        loose.chmod(0o755)
        self.assertIsNone(rw._untrusted_location(loose.resolve(), roots=()))

    def test_k4_a_local_clock_skewed_from_the_targets_is_refused(self):
        for skew in (timedelta(minutes=4), -timedelta(minutes=4)):
            self.server_now = self.NOW + skew
            with self.assertRaisesRegex(ValueError, "local clock differs from the restore target server"):
                self.verify()
        self.server_now = self.NOW + timedelta(minutes=2)
        self.verify()

    def test_a_copy_age_cannot_decrypt_or_an_absent_identity_is_refused(self):
        (self.tmp / "bin" / "age").write_text("#!/bin/sh\necho 'no identity matched' >&2\nexit 1\n")
        with self.assertRaisesRegex(ValueError, "age could not decrypt"):
            self.verify()
        (self.tmp / "bin" / "age").write_text("#!/bin/sh\ncat \"$4\"\n")
        self.serve(b"-- decrypted, but not a data dump\n")
        with self.assertRaisesRegex(ValueError, "no COPY blocks"):
            self.verify()
        self.identity.unlink()
        with self.assertRaisesRegex(ValueError, "identity file does not exist"):
            self.verify()

    def test_a_stored_copy_whose_bytes_differ_from_the_record_fails_on_hash(self):
        self.fake.artifact["digest"] = self.fake.summary["artifact_digest"] = D("0")
        receipt, _ = self.verify()
        self.assertEqual(receipt["observed_artifact_digest"], self.digest)
        self.assertEqual(json.loads(self.evaluate(receipt).stdout)["reason_id"], "artifact_hash_mismatch")

    def test_the_cli_reads_the_dsn_from_the_environment_only(self):
        with mock.patch.dict(os.environ, {"RESTORE_ADMIN_DSN": ""}), mock.patch("sys.stderr", io.StringIO()) as err:
            self.assertEqual(rw.main(["verify-restore", "--repository", REPO_SLUG, "--run-id", str(RUN_ID),
                                      "--identity", str(self.identity), "--target-kind", "disposable_local_cluster",
                                      "--database", "carr_restore", "--work-dir", str(self.tmp / "w")]), 2)
        self.assertIn("environment only", err.getvalue())

    def test_a_branch_target_is_armed_before_the_branch_exists_and_handed_over_on_stdin(self):
        order = []
        branch = ("br-restore", "host=ep.example.test dbname=neondb")
        point = {"head_lsn": "0/1000", "parent_lsn": "0/1010", "gap_bytes": 16}
        with mock.patch.object(rw, "production_head_lsn", side_effect=lambda p: order.append("head") or 0x1000), \
             mock.patch.object(rw, "branch_target_start",
                               side_effect=lambda *a: order.append(("target", a)) or ("2026-09-24T10:00:00Z", point)), \
             mock.patch.object(rw, "create_target_database", return_value="host=ep.example.test dbname=carr_restore"), \
             mock.patch.object(rw, "load_artifact"), \
             mock.patch.object(rw, "server_clock", return_value=self.NOW), \
             mock.patch.object(rw, "restored_watermark", lambda dsn, a: rw.strip_columns(a)):
            receipt = rw.verify_restore(repository=REPO_SLUG, run_id=RUN_ID, identity=self.identity,
                                        target_kind="disposable_branch", database="carr_restore", project_id="proj",
                                        work_dir=self.tmp / "w", armed=lambda: order.append("armed"),
                                        sleep=lambda s: order.append(("settle", s)),
                                        handoff=lambda: order.append("handoff") or branch, now=lambda: self.NOW)
        self.assertEqual(order, ["head", ("settle", 30), "armed", "handoff",
                                 ("target", ("proj", "br-restore", branch[1], "carr_restore", 0x1000))])
        self.assertEqual(receipt["target_kind"], "disposable_branch")
        self.assertEqual(receipt["target_point"], {**point, "settle_seconds": 30})
        self.assertEqual(json.loads(self.evaluate(receipt).stdout)["target_point"], receipt["target_point"])
        # the settle wait is bounded both ways, and checked before production is read
        for bad in (0, 4, 301):
            with mock.patch.object(rw, "production_head_lsn") as head:
                with self.assertRaisesRegex(ValueError, r"settle wait must be 5\.\.300 s"):
                    rw.verify_restore(repository=REPO_SLUG, run_id=RUN_ID, identity=self.identity,
                                      target_kind="disposable_branch", database="carr_restore", project_id="proj",
                                      work_dir=self.tmp / "w", handoff=lambda: branch, settle_seconds=bad)
                head.assert_not_called()
        # on the CLI the handover is two lines of stdin; nothing handed over is refused
        with mock.patch("sys.stdin", io.StringIO("br-restore\nhost=ep dbname=neondb\n")):
            self.assertEqual(rw._stdin_handoff(), ("br-restore", "host=ep dbname=neondb"))
        for text in ("", "br-restore\n"):
            with mock.patch("sys.stdin", io.StringIO(text)):
                with self.assertRaisesRegex(ValueError, "no branch was handed over"):
                    rw._stdin_handoff()
        signal = self.tmp / "armed"
        rw._signal_armed(str(signal))()
        self.assertEqual(signal.read_text(), "armed\n")
        self.assertIsNone(rw._signal_armed(None))


    # ── round-5 review: storage ingest lag ────────────────────────────────
    # A model of the provider: production writes every 60 s (the last one 2 s
    # before arming); the compute's FLUSHED position moves at each write; the
    # storage layer has ingested what was flushed `lag` seconds ago; a branch
    # made at time t has parent_lsn = what storage had ingested at t.
    HEAD = 0x16_B3740000

    def flushed(self, t):
        return self.HEAD + 500 * int(math.floor((t + 2) / 60))

    def rehearse_on_lagging_storage(self, lag, settle=30, parent_at=None):
        clock = {"t": 0.0}
        state = {}
        provider = RestoreTarget()
        provider.setUp()

        def sleep(seconds):
            clock["t"] += seconds

        def handoff():
            created = clock["t"] if parent_at is None else parent_at
            lsn = self.flushed(created - lag)
            state["parent"] = lsn
            provider.branches[provider.BRANCH]["parent_lsn"] = rw.format_lsn(lsn)
            return provider.BRANCH, provider.DSN

        with mock.patch.object(rw, "production_head_lsn", side_effect=lambda p: self.flushed(clock["t"])), \
             mock.patch.object(rw, "_neon", provider.neon), \
             mock.patch.object(rw, "create_target_database", return_value="host=ep-restore.example.test dbname=carr_restore"), \
             mock.patch.object(rw, "load_artifact"), \
             mock.patch.object(rw, "server_clock", return_value=self.NOW), \
             mock.patch.object(rw, "restored_watermark", lambda dsn, a: rw.strip_columns(a)):
            receipt = rw.verify_restore(repository=REPO_SLUG, run_id=RUN_ID, identity=self.identity,
                                        target_kind="disposable_branch", database="carr_restore", project_id="proj",
                                        work_dir=self.tmp / "w", handoff=handoff, sleep=sleep,
                                        settle_seconds=settle, now=lambda: self.NOW)
        return receipt, state

    def test_round5_ordinary_storage_lag_passes_after_the_settle_wait(self):
        # The reviewer's failure: 8 s of ingest lag behind a write 2 s before arming.
        # Without the wait, the branch made at arming would be one write short and refused.
        self.assertLess(self.flushed(0 - 8), self.flushed(0))
        for lag in (0, 1, 8, 25, 30):
            receipt, state = self.rehearse_on_lagging_storage(lag)
            self.assertEqual(receipt["target_point"]["head_lsn"], rw.format_lsn(self.HEAD))
            self.assertEqual(receipt["target_point"]["parent_lsn"], rw.format_lsn(state["parent"]))
            self.assertEqual(receipt["target_point"]["gap_bytes"], state["parent"] - self.HEAD)
            self.assertGreaterEqual(receipt["target_point"]["gap_bytes"], 0)
            self.assertEqual(receipt["target_point"]["settle_seconds"], 30)
            self.assertEqual(json.loads(self.evaluate(receipt).stdout)["reason_id"], "restore_exercise_exact")
        # a longer wait covers a longer lag, and the wait actually used is what is recorded
        receipt, _ = self.rehearse_on_lagging_storage(100, settle=120)
        self.assertGreaterEqual(receipt["target_point"]["gap_bytes"], 0)
        self.assertEqual(receipt["target_point"]["settle_seconds"], 120)
        # the bounds themselves are admissible
        for settle in (5, 300):
            receipt, _ = self.rehearse_on_lagging_storage(0, settle=settle)
            self.assertEqual(receipt["target_point"]["settle_seconds"], settle)

    def test_round5_a_point_in_time_child_from_dump_time_is_still_refused(self):
        # the nightly dump ran hours before; a child taken there precedes the flushed head by every write since
        for hours in (7, 1, 0.1):
            with self.assertRaisesRegex(ValueError, r"parent_lsn .* bytes before production's flushed head"):
                self.rehearse_on_lagging_storage(0, parent_at=-hours * 3600)

    def test_round5_lag_longer_than_the_wait_is_refused_with_the_gap_named(self):
        with self.assertRaisesRegex(ValueError, r"is 500 bytes before production's flushed head 16/B3740000"):
            self.rehearse_on_lagging_storage(45)


@unittest.skipUnless(importlib.util.find_spec("psycopg") is not None, "psycopg not installed")
class RestoreTarget(unittest.TestCase):
    """The target and its start instant, read from the provider (branch) or the cluster (local)."""

    PROJECT, DEFAULT, BRANCH = "proj", "br-prod", "br-restore"
    DSN = "host=ep-restore.example.test port=5432 dbname=neondb user=u"  # the ADMIN connection
    HEAD = 0x16_B374D848

    def setUp(self):
        self.branches = {
            self.DEFAULT: {"id": self.DEFAULT, "default": True},
            self.BRANCH: {"id": self.BRANCH, "default": False, "protected": False, "parent_id": self.DEFAULT,
                          "created_at": "2026-09-24T09:59:58.700Z", "parent_lsn": "16/B374D848",
                          # an idle production: the last WAL was hours before the branch
                          "parent_timestamp": "2026-09-24T03:05:00Z"},
        }
        self.endpoints = {self.BRANCH: [{"host": "ep-restore.example.test"}]}
        self.parent_databases = [{"name": "neondb"}, {"name": "carr_archive"}]

    def neon(self, path):
        base = f"/projects/{self.PROJECT}/branches"
        if path == base:
            return {"branches": list(self.branches.values())}
        if path == f"{base}/{self.DEFAULT}/databases":
            return {"databases": self.parent_databases}
        if path.endswith("/endpoints"):
            return {"endpoints": self.endpoints.get(path.split("/")[-2], [])}
        bid = path.rsplit("/", 1)[1]
        if bid not in self.branches:
            raise ValueError(f"provider GET {path} answered 404")
        return {"branch": self.branches[bid]}

    def start(self, dsn=None, database="restore_rehearse", head="default"):
        head_lsn = self.HEAD if head == "default" else head
        with mock.patch.object(rw, "_neon", self.neon):
            return rw.branch_target_start(self.PROJECT, self.BRANCH, dsn or self.DSN, database, head_lsn)

    def test_the_start_is_the_providers_branch_creation_floored(self):
        self.assertEqual(self.start(), ("2026-09-24T09:59:58Z",
                                        {"head_lsn": "16/B374D848", "parent_lsn": "16/B374D848", "gap_bytes": 0}))

    def test_production_protected_foreign_or_unreachable_targets_are_refused(self):
        cases = [
            (lambda: self.branches[self.BRANCH].update(default=True), "exactly one default"),
            (lambda: self.branches[self.BRANCH].update(protected=True), "protected"),
            (lambda: self.branches[self.BRANCH].update(parent_id="br-dev"), "not a child"),
            (lambda: self.branches[self.BRANCH].update(created_at=None), "no creation instant"),
            (lambda: self.branches.pop(self.BRANCH), "404"),
            (lambda: self.branches[self.DEFAULT].update(default=False), "exactly one default"),
            (lambda: self.branches.setdefault("br-2", {"id": "br-2", "default": True}), "exactly one default"),
            (lambda: self.endpoints.update({self.BRANCH: [{"host": "ep-other.example.test"}]}), "does not point at"),
        ]
        for mutate, message in cases:
            self.setUp()
            mutate()
            with self.assertRaisesRegex(ValueError, message):
                self.start()
        self.setUp()
        with self.assertRaisesRegex(ValueError, r"is the production \(default\) or a protected branch"):
            with mock.patch.object(rw, "_neon", self.neon):
                rw.branch_target_start(self.PROJECT, self.DEFAULT, self.DSN, "restore_rehearse", self.HEAD)

    def test_k1_an_inherited_database_is_refused_even_when_it_matches_the_artifact(self):
        # The reviewer's bypass: the branch's inherited neondb. Defence in depth
        # now that verify-restore creates and loads the database itself.
        with self.assertRaisesRegex(ValueError, "one the branch inherits"):
            self.start(database="neondb")
        with self.assertRaisesRegex(ValueError, "also exists on the production branch"):
            self.start(database="carr_archive")
        with self.assertRaisesRegex(ValueError, "one the branch inherits"):
            self.start(database="")
        self.parent_databases = []
        with self.assertRaisesRegex(ValueError, "no databases on the production branch"):
            self.start()

    def test_m3_a_head_branch_of_an_idle_production_passes_however_long_it_has_been_quiet(self):
        # parent_timestamp is hours old (the last WAL); the LSN equals the head read at arming.
        self.assertEqual(self.start()[1]["gap_bytes"], 0)
        # production wrote between arming and the create: the branch is later still
        self.branches[self.BRANCH]["parent_lsn"] = "16/B374FFFF"
        self.assertEqual(self.start(), ("2026-09-24T09:59:58Z",
                                        {"head_lsn": "16/B374D848", "parent_lsn": "16/B374FFFF", "gap_bytes": 0x27B7}))
        self.branches[self.BRANCH]["parent_lsn"] = "17/0"
        self.assertEqual(self.start()[1], {"head_lsn": "16/B374D848", "parent_lsn": "17/0",
                                           "gap_bytes": 0x17_00000000 - self.HEAD})

    def test_m3_a_point_in_time_child_is_refused(self):
        for earlier in ("16/B374D847", "15/FFFFFFFF", "0/0"):
            self.branches[self.BRANCH]["parent_lsn"] = earlier
            with self.assertRaisesRegex(ValueError, rf"parent_lsn {earlier.upper()} is \d+ bytes before production's "
                                                    r"flushed head 16/B374D848"):
                self.start()

    def test_m3_a_missing_lsn_or_head_reading_fails_closed(self):
        for bad in (None, "", "zz/1", "16", "1/2/3", 42):
            self.setUp()
            self.branches[self.BRANCH]["parent_lsn"] = bad
            with self.assertRaisesRegex(ValueError, "no parent_lsn"):
                self.start()
        self.setUp()
        del self.branches[self.BRANCH]["parent_lsn"]
        with self.assertRaisesRegex(ValueError, "no parent_lsn"):
            self.start()
        self.setUp()
        with self.assertRaisesRegex(ValueError, "no production head LSN was read"):
            self.start(head=None)

    def test_m3_production_head_is_read_on_a_provider_issued_read_only_session(self):
        seen = {}

        class Conn:
            def __enter__(self):
                return self

            def __exit__(self, *exc):
                return False

            def execute(self, sql):
                seen["sql"] = sql
                return mock.Mock(fetchone=lambda: (seen.get("answer", "16/B374D848"),))

        def connect(uri, **kw):
            seen.update(uri=uri, kw=kw)
            return Conn()

        def neon(path):
            if path.endswith("/branches"):
                return {"branches": list(self.branches.values())}
            seen["path"] = path
            return {"uri": "postgresql://neondb_owner@prod.example.test/neondb"}

        with mock.patch.object(rw, "_neon", neon), mock.patch.dict(sys.modules, {"psycopg": mock.Mock(connect=connect)}):
            self.assertEqual(rw.production_head_lsn(self.PROJECT), self.HEAD)
            self.assertIn(f"branch_id={self.DEFAULT}", seen["path"])
            self.assertEqual(seen["kw"]["options"], "-c default_transaction_read_only=on")
            self.assertIn("pg_current_wal_flush_lsn()", seen["sql"])
            seen["answer"] = None
            with self.assertRaisesRegex(ValueError, "no head LSN"):
                rw.production_head_lsn(self.PROJECT)
        with mock.patch.object(rw, "_neon", lambda path: {"branches": list(self.branches.values())} if path.endswith("/branches") else {}):
            with self.assertRaisesRegex(ValueError, "issued no production connection"):
                rw.production_head_lsn(self.PROJECT)

    def test_parse_lsn(self):
        self.assertEqual(rw.parse_lsn("0/0"), 0)
        self.assertEqual(rw.parse_lsn("1/0"), 1 << 32)
        self.assertEqual(rw.parse_lsn("16/B374D848"), 0x16_B374D848)
        self.assertEqual(rw.parse_lsn("ffffffff/FFFFFFFF"), (1 << 64) - 1)
        for bad in (None, "", "g/1", "1/", "/1", "123456789/0", 5):
            self.assertIsNone(rw.parse_lsn(bad))

    def test_a_local_cluster_target_must_be_a_socket_or_loopback(self):
        with self.assertRaisesRegex(ValueError, "socket path or loopback"):
            rw.local_target_start("host=db.example.test dbname=r")

    def test_round5_a_loopback_tunnel_to_a_provider_server_is_refused(self):
        seen = []
        answers = {}

        class Conn:
            def __enter__(self):
                return self

            def __exit__(self, *exc):
                return False

            def execute(self, sql):
                seen.append(sql)
                if sql == rw.PROVIDER_SETTINGS_SQL:
                    return mock.Mock(fetchone=lambda: answers["provider"])
                return mock.Mock(fetchone=lambda: (datetime(2026, 9, 24, 9, 0, 0, 900000, tzinfo=timezone.utc),))

        def connect(dsn, **kw):
            self.assertEqual(kw["options"], "-c default_transaction_read_only=on")
            return Conn()

        with mock.patch.dict(sys.modules, {"psycopg": mock.Mock(connect=connect)}):
            answers["provider"] = (0, "")
            self.assertEqual(rw.local_target_start("host=127.0.0.1 port=5432 dbname=postgres"), "2026-09-24T09:00:00Z")
            self.assertEqual(seen[0], rw.PROVIDER_SETTINGS_SQL)
            for provider in ((12, "neon"), (1, ""), (0, "pg_stat_statements,neon"), (0, "NEON_utils"), None):
                answers["provider"] = provider
                seen.clear()
                with self.assertRaisesRegex(ValueError, "reports provider settings"):
                    rw.local_target_start("host=localhost dbname=postgres")
                self.assertEqual(seen, [rw.PROVIDER_SETTINGS_SQL])  # nothing read or created after it

    def test_the_provider_credential_is_required_and_never_on_an_argument(self):
        with mock.patch.dict(os.environ, {"NEON_API_KEY": ""}):
            with self.assertRaisesRegex(ValueError, "NEON_API_KEY is not loaded"):
                rw._neon("/projects/p/branches")


class PinnedGh(unittest.TestCase):
    def test_k2_the_status_modules_github_reads_use_the_pinned_gh(self):
        with mock.patch.object(rw, "_trusted_binary", lambda name: f"/pinned/{name}"):
            module = rw._status_module()
        calls = []
        with mock.patch.object(rw.subprocess, "run", lambda argv, *a, **k: calls.append(argv)):
            module.subprocess.run(["gh", "api", "/x"], capture_output=True)
            module.subprocess.run(["git", "status"])
        self.assertEqual(calls, [["/pinned/gh", "api", "/x"], ["git", "status"]])
        self.assertIs(module.subprocess.TimeoutExpired, subprocess.TimeoutExpired)
        # with no trusted gh installed, the module cannot be loaded at all
        with mock.patch.object(rw, "TRUSTED_BIN_DIRS", ("/nonexistent-carr-bin",)):
            with self.assertRaisesRegex(ValueError, "gh is not installed"):
                rw._status_module()


def _script_function(name: str) -> str:
    text = REHEARSE.read_text()
    m = re.search(rf"(^{name}\(\) \{{\n.*?\n\}}\n)", text, re.S | re.M)
    if m is None:
        raise AssertionError(f"function {name} not found in bin/restore-rehearse.sh")
    return m.group(1)


class RehearsalReceiptGate(unittest.TestCase):
    """Round-4 item 3, M3, K3: the rehearsal's own functions, run with stubs.

    verify-restore is armed before the branch exists, gets the branch over the
    FIFO (never an argument), performs the load itself, and its stdout goes
    straight into the evaluator, whose verdict is the authority.
    """

    def run_flow(self, verify_exit: int = 0, verdict: str = "restore_exercise_exact", arm: bool = True,
                 fails: int = 0):
        end = re.search(r'(if \[ -n "\$BACKUP_RUN_ID" \]; then\n  mkdir -p "\$REPO/out"\n.*?\nfi\n)',
                        REHEARSE.read_text(), re.S)
        if end is None:
            self.fail("receipt block not found in bin/restore-rehearse.sh")
        with tempfile.TemporaryDirectory() as tmp:
            t = Path(tmp)
            (t / "out").mkdir()
            (t / "out" / "restore-exercise-receipt.json").write_text('{"stale": true}')
            stub = t / "stub.sh"
            # `$PY -c ...` runs the real interpreter; verify-restore is a stand-in that
            # signals armed, then reads the branch handover from stdin like the real one.
            stub.write_text(
                f"#!/bin/sh\nif [ \"$1\" = -c ]; then exec '{sys.executable}' \"$@\"; fi\n"
                f"echo \"$*\" >> '{t}/verify.args'\n"
                "armed=''; prev=''; for a in \"$@\"; do [ \"$prev\" = --armed-file ] && armed=\"$a\"; prev=\"$a\"; done\n"
                # The file is the readiness signal. Log first so the parent cannot
                # observe readiness and append branch-created before this marker.
                + (f"echo armed >> '{t}/order'; echo armed > \"$armed\"\n" if arm else "exit 1\n")
                + f"IFS= read -r branch; IFS= read -r admin; echo \"$branch|$admin\" > '{t}/handover'; "
                f"echo handover >> '{t}/order'\n"
                f"[ {verify_exit} -eq 0 ] && echo '{{\"bound\":1}}'; exit {verify_exit}\n")
            stub.chmod(0o755)
            node = (f"node() {{ print -r -- \"$*\" >> '{t}/node.args'; cat > '{t}/node.stdin'; "
                    f"[ -s '{t}/node.stdin' ] || return 2; "
                    f"print -r -- '{{\"reason_id\":\"{verdict}\"}}'; "
                    f"[ '{verdict}' = restore_exercise_exact ]; }}\n")
            script = ("say() { print -r -- \"$*\"; }\n"
                      "die() { print -r -- \"DIE: $*\"; exit 1; }\n" + node +
                      f"REPO='{t}'; WORKDIR='{t}'; COPYDIR='{t}'; PY='{stub}'; BACKUP_RUN_ID=101\n"
                      "BACKUP_REPOSITORY=o/r; IDENTITY=/k; PROJECT_ID=proj; RESTORE_DB=restore_rehearse\n"
                      "EXERCISE_VERDICT=''; VERIFY_PID=''; VERIFY_FIFO=''; VERIFY_ARM_TIMEOUT=5\n"
                      + _script_function("exercise_reason") + _script_function("arm_verify_restore")
                      + _script_function("hand_over_to_verify_restore")
                      + "arm_verify_restore\n"
                      f"echo branch-created >> '{t}/order'\n"
                      "BRANCH_ID=br-r; BRANCH_ADMIN_URL=admin-dsn-value\n"
                      "if hand_over_to_verify_restore; then HANDOVER=0; else HANDOVER=$?; fi\n"
                      f"FAILS={fails}\n"
                      + end.group(1) +
                      "print -r -- \"HANDOVER=$HANDOVER\"; print -r -- \"VERDICT=$EXERCISE_VERDICT\"\n")
            got = subprocess.run(["zsh", "-c", script], capture_output=True, text=True, timeout=60)
            read = lambda name: (t / name).read_text() if (t / name).exists() else None  # noqa: E731
            self.verify_args, self.order, self.handover = read("verify.args") or "", read("order") or "", read("handover")
            self.node_args, self.node_stdin = read("node.args") or "", read("node.stdin")
            receipt = t / "out" / "restore-exercise-receipt.json"
            return got.stdout + got.stderr, receipt.read_text() if receipt.exists() else None

    def test_a_clean_run_arms_first_hands_over_on_the_fifo_and_judges_the_piped_output(self):
        out, receipt = self.run_flow()
        # armed (production head read) BEFORE the branch exists; the branch arrives afterwards
        self.assertEqual(self.order.split(), ["armed", "branch-created", "handover"])
        self.assertEqual(self.handover.strip(), "br-r|admin-dsn-value")  # over the FIFO ...
        self.assertNotIn("br-r", self.verify_args)                       # ... never on argv
        self.assertNotIn("admin-dsn-value", self.verify_args)
        self.assertIn("--target-kind disposable_branch --project-id proj --database restore_rehearse", self.verify_args)
        for handed in ("--branch-id", "--artifact", "--restored", "--copy-record", "--started-at", "--finished-at"):
            self.assertNotIn(handed, self.verify_args)
        # K3: the evaluator read verify-restore's stdout through the pipe
        self.assertIn("recovery-matrix-evaluate.mjs restore -", self.node_args)
        self.assertEqual(json.loads(self.node_stdin), {"bound": 1})
        self.assertIn("HANDOVER=0", out)
        self.assertIn("VERDICT=restore_exercise_exact", out)
        self.assertEqual(json.loads(receipt), {"bound": 1})  # the tee's copy, kept only on a clean run
        self.assertIn("not authority", out)

    def test_a_failed_phase_4_keeps_no_receipt_copy_and_removes_a_stale_one(self):
        out, receipt = self.run_flow(fails=1)
        self.assertIsNone(receipt)
        self.assertIn("no restore-exercise receipt copy", out)

    def test_a_refused_verify_fails_the_handover(self):
        out, _ = self.run_flow(verify_exit=1)
        self.assertNotIn("HANDOVER=0", out)
        self.assertIn("VERDICT=verify_restore_refused", out)

    def test_an_evaluator_failure_on_the_piped_receipt_fails_the_handover(self):
        out, _ = self.run_flow(verdict="watermark_mismatch")
        self.assertNotIn("HANDOVER=0", out)
        self.assertIn("VERDICT=watermark_mismatch", out)

    def test_a_verifier_that_never_arms_stops_the_rehearsal_before_any_branch(self):
        out, _ = self.run_flow(arm=False)
        self.assertIn("DIE: verify-restore did not arm", out)
        self.assertNotIn("branch-created", self.order)

    def test_the_script_no_longer_loads_on_the_backup_path_and_records_the_piped_verdict(self):
        text = REHEARSE.read_text()
        self.assertIn('if [ -n "$BACKUP_RUN_ID" ]; then\n  step "phase 1b: arm verify-restore"', text)
        self.assertLess(text.index('step "phase 1b: arm verify-restore"'), text.index('step "phase 2: throwaway branch"'))
        self.assertIn("elif ! PGURL_ADMIN=", text)  # the script's own create/load runs only without --backup-run-id
        self.assertIn('fi  # the local-dump path; with --backup-run-id verify-restore loaded it above', text)
        self.assertIn("exercise=$EXERCISE_VERDICT (piped verify-restore verdict; receipt file is a copy; "
                      "binary check stops PATH shims only, same-OS-user adversary out of scope)", text)


    def test_round5_every_tail_that_can_hold_psql_output_is_redacted(self):
        with tempfile.TemporaryDirectory() as tmp:
            t = Path(tmp)
            (t / "verify.err").write_text(
                'restore-watermark: the artifact did not decrypt and load (age 0, psql 3): '
                'psql:<stdin>:5: ERROR:  invalid input syntax for type uuid: "prod-secret-row"\n'
                "CONTEXT:  COPY party, line 7: \"prod-secret-row\tJane Client\"\n"
                "7\tJane Client\t555-0100\n"
                "ERROR:  duplicate key value violates unique constraint \"party_pkey\"\n")
            script = (f"REPO='{REPO}'; PY='{sys.executable}'\n" + _script_function("redact_tail")
                      + f"redact_tail 10 '{t}/verify.err'\n")
            got = subprocess.run(["zsh", "-c", script], capture_output=True, text=True, timeout=60)
            self.assertEqual(got.stdout, "")
            self.assertEqual(got.stderr.splitlines(), [
                "restore-watermark: the artifact did not decrypt and load <redacted>: psql:<stdin>:5: ERROR:  "
                "invalid input syntax for type uuid: <redacted>",
                "ERROR:  duplicate key value violates unique constraint <redacted>"])
        text = REHEARSE.read_text()
        self.assertIn('redact_tail 5 "$WORKDIR/verify.err"', text)
        self.assertIn('redact_tail 10 "$WORKDIR/verify.err"', text)
        self.assertIn('redact_tail 40 "$WORKDIR/restore.err"', text)
        self.assertNotRegex(text, r"tail -\d+ \"\$WORKDIR/(verify|restore)\.err\"")
        self.assertIn("psql -v ON_ERROR_STOP=1 -v VERBOSITY=terse -v SHOW_CONTEXT=never -q", text)


class OutboundCensus(unittest.TestCase):
    """G2/H3: the items are read from the restored queue and the readbacks from the provider, never handed in."""

    T = datetime(2026, 9, 24, 11, 0, 0, 123456, tzinfo=timezone.utc)

    def test_device_rows_map_pending_to_outcome_unknown_and_everything_final_to_settled(self):
        pending = rw.outbound_item("00000000-0000-4000-8000-000000000001", "n-1", "device", "pending", self.T)
        self.assertEqual(pending["state"], "outcome_unknown")
        self.assertEqual(pending["last_attempt_at"], "2026-09-24T11:00:00.123456Z")
        self.assertEqual(pending["envelope_digest"],
                         "sha256:" + hashlib.sha256(b'{"channel":"device","notification_id":"n-1"}').hexdigest())
        for final in ("delivered", "suppressed_quiet_hours", "failed"):
            self.assertEqual(rw.outbound_item("x", "n-1", "device", final, self.T)["state"], "settled")

    def judge(self, req):
        node = subprocess.run(["node", "--input-type=module", "-e",
                               "import {evaluateOutboundQueueRelease as e, v5OutboundCensusDigest as d} from "
                               f"'{REPO / 'mcp-server/src/recovery-matrix.v5.js'}';"
                               "let s='';process.stdin.on('data',c=>s+=c).on('end',()=>{const r=JSON.parse(s);"
                               "console.log(JSON.stringify({d:d(r.items),v:e(r,{now_ms:Date.parse('2026-09-24T12:00:00Z')})}))})"],
                              input=json.dumps(req), capture_output=True, text=True, check=True)
        return json.loads(node.stdout)

    def items(self):
        return [rw.outbound_item(f"0000000{i}-0000-4000-8000-000000000000", f"n-{i}", "device", s, self.T)
                for i, s in ((2, "pending"), (1, "delivered"))]

    def test_with_no_provider_reader_every_unsettled_item_stays_quarantined(self):
        self.assertEqual(rw.PROVIDER_READERS, {})  # no device push sender exists yet
        items = self.items()
        req = rw.outbound_census(items, "restore-1", datetime(2026, 9, 24, 12, 0, tzinfo=timezone.utc))
        self.assertEqual(req["readbacks"], [])
        self.assertEqual(req["census"], {"source": "ops.notification_delivery:device", "item_count": 2,
                                         "digest": rw.census_digest(items)})
        self.assertEqual(req["verification"]["verifier"], "tools/restore-watermark.py outbound-census")
        got = self.judge(req)
        self.assertEqual(got["d"], req["census"]["digest"])
        self.assertEqual(got["v"]["reason_id"], "outbound_items_quarantined")

    def test_a_registered_provider_reader_is_asked_about_unsettled_items_only_and_its_answer_carried(self):
        items = self.items()
        asked = []

        def reader(unsettled):
            asked.extend(unsettled)
            return [{"item_id": i["item_id"], "idempotency_key": i["envelope_digest"],
                     "read_at": "2026-09-24T11:30:00Z", "readback": "effect_absent"} for i in unsettled]

        with mock.patch.dict(rw.PROVIDER_READERS, {"device": reader}):
            req = rw.outbound_census(items, "restore-1", datetime(2026, 9, 24, 12, 0, tzinfo=timezone.utc))
        self.assertEqual([i["item_id"] for i in asked], [items[0]["item_id"]])
        got = self.judge(req)
        self.assertEqual(got["v"]["decision"], "reconciled")
        by = {d["item_id"]: d["disposition"] for d in got["v"]["dispositions"]}
        self.assertEqual(by, {items[0]["item_id"]: "release_for_governed_send", items[1]["item_id"]: "already_settled"})
        # a reader for another channel is never asked about device items
        with mock.patch.dict(rw.PROVIDER_READERS, {"email": reader}):
            self.assertEqual(rw.outbound_census(items, "restore-1")["readbacks"], [])

    def test_bind_never_restamps_and_digests_exactly_the_facts(self):
        facts = {"b": [1, "é"], "a": {"z": None, "y": True}}
        bound = recovery_evidence.bind(facts, "outbound_census", datetime(2026, 9, 24, 12, 0, 0, 999999, tzinfo=timezone.utc))
        self.assertEqual(bound["verification"]["verified_at"], "2026-09-24T12:00:00Z")  # floored, never rounded up
        self.assertEqual(bound["verification"]["facts_digest"],
                         "sha256:" + hashlib.sha256('{"a":{"y":true,"z":null},"b":[1,"é"]}'.encode()).hexdigest())
        with self.assertRaisesRegex(ValueError, "already carries"):
            recovery_evidence.bind(bound, "outbound_census")
        with self.assertRaises(KeyError):
            recovery_evidence.bind(facts, "made_up_kind")

    def test_the_cli_takes_no_readbacks_and_reads_the_dsn_from_the_environment_only(self):
        with tempfile.TemporaryDirectory() as tmp:
            rb = Path(tmp) / "rb.json"
            rb.write_text("[]")
            with self.assertRaises(SystemExit), mock.patch("sys.stderr", io.StringIO()):
                rw.main(["outbound-census", "--restore-id", "r", "--readbacks", str(rb)])
            with mock.patch.dict(os.environ, {"RESTORE_DSN": ""}), mock.patch("sys.stderr", io.StringIO()) as err:
                self.assertEqual(rw.main(["outbound-census", "--restore-id", "r"]), 2)
            self.assertIn("environment only", err.getvalue())


# ── end to end on a disposable local cluster ─────────────────────────────────

def _pg_bins():
    """One directory holding a real server beside its client tools (libpq alone ships no server)."""
    names = ("initdb", "pg_ctl", "pg_dump", "psql", "postgres")
    dirs = [Path(p).parent for p in filter(None, [shutil.which("postgres")])]
    dirs += [Path(d) for d in ("/opt/homebrew/opt/postgresql@17/bin", "/usr/local/opt/postgresql@17/bin",
                               "/usr/lib/postgresql/17/bin", "/usr/lib/postgresql/16/bin")]
    for d in dirs:
        cand = {n: str(d / n) for n in names}
        if all(Path(p).exists() for p in cand.values()):
            return cand
    return None


def _script_block(pattern: str) -> str:
    text = REHEARSE.read_text()
    m = re.search(pattern, text, re.S)
    if not m:
        raise AssertionError(f"could not find {pattern!r} in bin/restore-rehearse.sh")
    return m.group(1)


def _psycopg_available() -> bool:
    return importlib.util.find_spec("psycopg") is not None


@unittest.skipUnless(_pg_bins() and shutil.which("node") and _psycopg_available(),
                     "PostgreSQL binaries, node or psycopg not installed")
class EndToEndDisposableCluster(unittest.TestCase):
    """A real dump, a real restore into a disposable database, the rehearsal's own filter."""

    tmp: Path
    identity: Path

    @classmethod
    def setUpClass(cls):
        cls.bins = _pg_bins()
        # The postmaster requires a valid locale on macOS.
        env = {**os.environ, "LC_ALL": "C", "LANG": "C"}
        cls.fixture = DisposablePostgres("carr-f08-e2e-", cls.bins["pg_ctl"], env=env)
        cls.addClassCleanup(cls.fixture.close)
        cls.tmp = cls.fixture.root
        with socket.socket() as s:
            s.bind(("127.0.0.1", 0))
            cls.port = str(s.getsockname()[1])
        data = cls.tmp / "data"
        cls.fixture.run([cls.bins["initdb"], "-D", str(data), "-U", "carr_ci", "--auth=trust", "-E", "UTF8",
                        "--locale=C"], check=True, capture_output=True, env=env)
        cls.fixture.run([cls.bins["pg_ctl"], "-D", str(data), "-l", str(cls.tmp / "log"), "-w", "-o",
                        f"-p {cls.port} -k {cls.tmp} -c listen_addresses='' -c timezone=UTC", "start"],
                       check=True, capture_output=True, env=env)
        cls.data = data

    def dsn(self, db):
        return f"host={self.tmp} port={self.port} user=carr_ci dbname={db}"

    def psql(self, db, sql, *extra):
        return subprocess.run([self.bins["psql"], self.dsn(db), "-v", "ON_ERROR_STOP=1", "-At", *extra, "-c", sql],
                              check=True, capture_output=True, text=True).stdout

    def restored(self, artifact: Path, out: Path):
        got = run_tool("restored", "--artifact", str(artifact), env={**os.environ, "RESTORE_DSN": self.dsn("restored")})
        self.assertEqual(got.returncode, 0, got.stderr)
        out.write_text(got.stdout)

    def verify_and_evaluate(self, fake: "FakeProvider", receipt: Path, database: str):
        """verify-restore in process: provider and gh are fixtures; it CREATES and LOADS a real local database."""
        real = rw._status_module()
        real.api = fake.api
        with mock.patch.object(rw, "_status_module", return_value=real):
            bound = rw.verify_restore(repository=REPO_SLUG, run_id=RUN_ID, identity=self.identity,
                                      target_kind="disposable_local_cluster", database=database,
                                      admin_dsn=self.dsn("postgres"), work_dir=self.tmp / f"verify-{database}")
        receipt.write_text(json.dumps(bound))
        return bound, subprocess.run(["node", str(EVALUATOR), "restore", "-"], input=json.dumps(bound),
                                     capture_output=True, text=True)

    def test_exact_restore_passes_and_a_lost_or_changed_row_fails(self):
        self.psql("postgres", "create database src")
        self.psql("postgres", "create database restored")
        self.psql("src", '''
          create schema ops;
          create table public.party (id int primary key, name text, at timestamptz, f float8, j jsonb,
                                     g int generated always as (id * 2) stored);
          insert into public.party (id, name, at, f, j)
            select g, 'p' || g, '2026-09-24 10:00:00+00'::timestamptz + g * interval '1 minute', g / 3.0, '{"a": 1}'
              from generate_series(1, 250) g;
          insert into public.party (id, name) values (1001, E'multi\\nline'), (1002, E'back\\\\.slash');
          create table public."Odd ""Name""" (id int);
          insert into public."Odd ""Name""" values (1), (2);
          create table public.empty_t (id int);
          create table public.ev (id int, at date) partition by range (at);
          create table public.ev_2025 partition of public.ev for values from ('2025-01-01') to ('2026-01-01');
          create table public.ev_2026 partition of public.ev for values from ('2026-01-01') to ('2027-01-01');
          insert into public.ev values (1, '2025-05-01'), (2, '2026-05-01'), (3, '2026-06-01');
          create table ops.run (id int references public.party(id));
          insert into ops.run select g from generate_series(1, 40) g;
        ''')
        dump = self.tmp / "carr-fixture.sql"
        subprocess.run([self.bins["pg_dump"], "--no-owner", "--no-acl", "--schema=public", "--schema=ops",
                        "-f", str(dump), self.dsn("src")], check=True, capture_output=True)

        digest = run_tool("digest", str(dump)).stdout.strip()
        counted = run_tool("count", stdin=dump.read_text())
        self.assertEqual(counted.returncode, 0, counted.stderr)
        artifact = self.tmp / "artifact.json"
        artifact.write_text(counted.stdout)
        self.assertEqual({t: e["rows"] for t, e in json.loads(counted.stdout).items()}, {
            'public.Odd "Name"': 2, "public.empty_t": 0, "public.ev_2025": 1, "public.ev_2026": 2,
            "public.party": 252, "ops.run": 40})

        restore_filter = _script_block(r"RESTORE_FILTER='([^']*)'")
        filtered = subprocess.run(["sed", "-E", restore_filter], input=dump.read_text(), text=True,
                                  capture_output=True, check=True).stdout
        subprocess.run([self.bins["psql"], self.dsn("restored"), "-v", "ON_ERROR_STOP=1", "-q"], input=filtered,
                       text=True, capture_output=True, check=True)

        restored = self.tmp / "restored.json"
        self.restored(artifact, restored)
        cmp_ok = run_tool("compare", "--artifact", str(artifact), "--restored", str(restored))
        self.assertEqual(cmp_ok.returncode, 0, cmp_ok.stdout + cmp_ok.stderr)

        # verify-restore: the copy is served through fixtures of the provider, gh and
        # age; psql and the database are REAL, and verify-restore does the load.
        blob = _zip_of("carr-20260924.sql.age", dump.read_bytes())
        (self.tmp / "artifact.bin").write_bytes(blob)
        bindir = self.tmp / "bin"
        bindir.mkdir(exist_ok=True)
        (bindir / "gh").write_text(f"#!/bin/sh\ncat '{self.tmp / 'artifact.bin'}'\n")
        (bindir / "age").write_text("#!/bin/sh\ncat \"$4\"\n")
        for tool in ("gh", "age"):
            (bindir / tool).chmod(0o755)
        self.identity = self.tmp / "identity.txt"
        self.identity.write_text("stand-in\n")
        fake = FakeProvider()
        zdigest = "sha256:" + hashlib.sha256(blob).hexdigest()
        fake.artifact.update(digest=zdigest, size_in_bytes=len(blob))
        fake.summary.update(artifact_digest=zdigest, artifact_bytes=len(blob))
        receipt = self.tmp / "receipt.json"
        pinned = {"gh": str(bindir / "gh"), "age": str(bindir / "age"), "psql": self.bins["psql"]}
        real_stream = rw.restore_stream
        with mock.patch.object(rw, "_trusted_binary", lambda name: pinned[name]):
            bound, verdict = self.verify_and_evaluate(fake, receipt, "vr_exact")
            self.assertEqual(verdict.returncode, 0, verdict.stdout + verdict.stderr)
            self.assertEqual(json.loads(verdict.stdout)["reason_id"], "restore_exercise_exact")
            self.assertEqual(bound["artifact_watermark"], rw.strip_columns(json.loads(counted.stdout)))
            self.assertLessEqual(bound["started_at"], bound["finished_at"])
            # the database it created holds what it loaded
            self.assertEqual(self.psql("vr_exact", "select count(*) from ops.run").strip(), "40")
            self.assertEqual(self.psql("vr_exact", "select string_agg(extname, ',' order by extname) from pg_extension "
                                                   "where extname in ('pg_trgm', 'pgcrypto')").strip(), "pg_trgm,pgcrypto")

            # new by construction: a database that already exists is refused, not reused
            with self.assertRaises(Exception):
                self.verify_and_evaluate(fake, receipt, "vr_exact")
            with self.assertRaises(Exception):
                self.verify_and_evaluate(fake, receipt, "restored")

            # A restore that LOSES a row: the evaluator refuses on the piped receipt.
            def lossy(lines):
                dropped = False
                for chunk in real_stream(lines):
                    if chunk == b"40\n" and not dropped:
                        dropped = True
                        continue
                    yield chunk

            with mock.patch.object(rw, "restore_stream", lossy):
                _, verdict = self.verify_and_evaluate(fake, receipt, "vr_lossy")
            self.assertEqual(verdict.returncode, 1)
            self.assertEqual(json.loads(verdict.stdout)["reason_id"], "watermark_mismatch")

            # A restore that CHANGES a row, count intact: the content digest refuses it.
            def changing(lines):
                for chunk in real_stream(lines):
                    yield chunk.replace(b"7\tp7\t", b"7\tp7x\t", 1) if chunk.startswith(b"7\tp7\t") else chunk

            with mock.patch.object(rw, "restore_stream", changing):
                _, verdict = self.verify_and_evaluate(fake, receipt, "vr_changed")
            self.assertEqual(json.loads(verdict.stdout)["reason_id"], "watermark_mismatch")

            # A copy whose stored bytes differ from what the producer recorded fails on hash.
            fake.artifact["digest"] = fake.summary["artifact_digest"] = D("0")
            self.assertEqual(json.loads(self.verify_and_evaluate(fake, receipt, "vr_hash")[1].stdout)["reason_id"],
                             "artifact_hash_mismatch")

        # The rehearsal's local-dump path still compares with the standalone tools.
        self.psql("restored", "delete from ops.run where id = 40")
        self.restored(artifact, restored)
        cmp_bad = run_tool("compare", "--artifact", str(artifact), "--restored", str(restored))
        self.assertEqual(cmp_bad.returncode, 1)
        self.assertIn("MISMATCH ops.run: artifact=40 restored=39", cmp_bad.stdout)
        self.psql("restored", "insert into ops.run values (40)")
        self.psql("restored", "update public.party set name = 'p7x' where id = 7")
        self.restored(artifact, restored)
        cmp_changed = run_tool("compare", "--artifact", str(artifact), "--restored", str(restored))
        self.assertIn("MISMATCH public.party: artifact=252 restored=252 content_differs=true", cmp_changed.stdout)

    def test_outbound_census_reads_every_device_row_of_the_restored_queue(self):
        self.psql("postgres", "create database outbound")
        self.psql("outbound", '''
          create schema ops;
          create table ops.notification_delivery (id uuid primary key default gen_random_uuid(), notification_id uuid not null,
            channel text not null, state text not null, attempted_at timestamptz not null default now());
          insert into ops.notification_delivery (notification_id, channel, state, attempted_at)
            select gen_random_uuid(), c, s, '2026-09-24 11:00:00.5+00'
              from (values ('device','pending'), ('device','delivered'), ('device','failed'), ('in_app','pending')) v(c, s);
        ''')
        got = run_tool("outbound-census", "--restore-id", "restore-e2e",
                       env={**os.environ, "RESTORE_DSN": self.dsn("outbound")})
        self.assertEqual(got.returncode, 0, got.stderr)
        req = json.loads(got.stdout)
        self.assertEqual(req["census"]["item_count"], 3)  # in_app rows have no provider effect
        self.assertEqual(sorted(i["state"] for i in req["items"]), ["outcome_unknown", "settled", "settled"])
        self.assertEqual({i["last_attempt_at"] for i in req["items"]}, {"2026-09-24T11:00:00.500000Z"})
        verdict = subprocess.run(["node", str(EVALUATOR), "outbound", "-"], input=json.dumps(req),
                                 capture_output=True, text=True)
        self.assertEqual(req["readbacks"], [])  # no device provider reader exists, so nothing is read
        self.assertEqual(verdict.returncode, 1, verdict.stderr)  # and the pending row stays quarantined
        self.assertEqual(json.loads(verdict.stdout)["reason_id"], "outbound_items_quarantined")
        # Dropping the pending row from the census the reader emitted holds the whole queue.
        req["items"] = [i for i in req["items"] if i["state"] == "settled"]
        verdict = subprocess.run(["node", str(EVALUATOR), "outbound", "-"], input=json.dumps(req),
                                 capture_output=True, text=True)
        self.assertEqual(json.loads(verdict.stdout)["reason_id"], "outbound_evidence_not_reverified")


if __name__ == "__main__":
    unittest.main()
