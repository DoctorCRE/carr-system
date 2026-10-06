#!/usr/bin/env python3
"""restore-watermark.py — the EXACT half of a restore rehearsal (V5-F08 item 4).

bin/restore-rehearse.sh has always compared the restored database against LIVE
production and gated on ">= 90% of production's rows". That is the right
truncation alarm, and it is approximate by construction: production keeps
moving after the dump is taken. V5-F08 asks for more — "restore from an
independently controlled copy succeeds with exact watermark/hash" — and the
only thing a restore can be compared EXACTLY against is the artifact itself.

  count        the decrypted pg_dump plaintext on stdin -> per table, the rows
               the artifact CARRIES and a content digest over them (sha256 of
               the table's COPY text lines, sorted bytewise), read from its own
               COPY blocks. Prints JSON. The plaintext is streamed, never
               written anywhere.
  restored     the same watermark read from the RESTORED database: every
               public/ops base table (extension-owned tables excepted), COPY'd
               out in pg_dump's own text form and column list, through a
               read-only session. The DSN comes from the environment variable
               --dsn-env names, never from an argument.
  compare      artifact vs restored watermark. Exit 0 only on exact equality —
               rows AND content digest, table for table, none missing either side.
  digest       the sha256 of a stored file, as "sha256:<hex>".
  fetch-copy   the independently held copy AND the digest its producer
               recorded, both read from the store, never from an operator: the
               nightly workflow run's provider-authenticated "Backup artifact"
               Check (bin/backup-workflow-status.py writes it), cross-checked
               against the artifact API, then the artifact's own bytes. Writes
               copy.json and the extracted .sql.age into --out-dir. It feeds
               the RESTORE only; no receipt is ever built from its output.
  verify-restore
               THE ONLY WAY A RESTORE RECEIPT IS MADE (reviews H1, round-4
               item 3). It takes no receipt, watermark, digest or instant from
               anyone, and it performs the RESTORE ITSELF: it looks up the run's
               Check, downloads the artifact and hashes those bytes, decrypts
               and counts it, CREATES the target database (CREATE DATABASE
               fails if the name exists, so the database is new by
               construction), loads the decrypted artifact into it through
               psql, reads the watermark back read-only, and takes the finish
               instant from its own clock. For a branch target it is ARMED
               before the branch exists: it reads production's FLUSHED WAL
               position first, waits a bounded settle interval so the
               provider's storage can ingest it, then takes the branch id and
               admin DSN on stdin, and requires the branch's parent_lsn at or
               after that position (reviews M3, round 5). The head, the
               parent point and the gap are recorded in the receipt.
  outbound-census
               every device row of the RESTORED ops.notification_delivery as
               an outbound item, as the bound request evaluateOutboundQueueRelease
               reads. The items come from the database; readbacks come only
               from a registered per-channel provider reader that stamps its
               own read_at (review H3). No channel has one today, so every
               unsettled item stays quarantined.

WHAT IS AUTHORITY (review K3): only verify-restore's stdout PIPED into
mcp-server/bin/recovery-matrix-evaluate.mjs, as bin/restore-rehearse.sh does
and records. out/restore-exercise-receipt.json is a copy for the operator; an
evaluator verdict on that file is not a restore result.

BINARY RESOLUTION (reviews K2, M2): `age`, `gh` and `psql` are resolved from
TRUSTED_BIN_DIRS, never from PATH, and a resolved file inside this repository,
in a temporary directory, or group- or world-writable is refused. THIS STOPS AN
ACCIDENTAL PATH SHIM ONLY. /opt/homebrew and its Cellar are owned by the same
OS user that runs this verifier, so that user can repoint any of them; an
adversary running as that user is OUT OF SCOPE, because they could equally
edit this file. The GitHub reads made through ops/backup-workflow-status.py
use the same resolved `gh`.

CHILD ENVIRONMENTS (round-5 review): `age` and `psql` run with an
allow-listed environment (locale, TZ, HOME, TMPDIR and a fixed PATH, plus
psql's PG* connection variables) and never inherit the provider API key or
anything else in this process's environment.

ERROR OUTPUT (round-5 review): a failed COPY makes psql print the offending
row. psql runs with VERBOSITY=terse and SHOW_CONTEXT=never, and whatever it
still writes is passed through redact_errors (only ERROR/FATAL lines, quoted
and parenthesised values replaced) before it reaches load.err, an exception
message, verify.err or a terminal. `redact-errors` exposes the same filter to
bin/restore-rehearse.sh for every tail it prints.

CLOCK (review K4): the finish instant and the binding stamp are this
machine's clock; verify-restore compares it with the target server's
clock_timestamp() and refuses a skew over MAX_CLOCK_SKEW_SECONDS.

The only writes are verify-restore's own CREATE DATABASE, extensions and load
into the throwaway target; every read session is opened with
default_transaction_read_only=on. No credential value is printed: the age
identity is a file path handed to `age`, the DSN is read from the
environment, and the provider key is sent only as a header. fetch-copy and
verify-restore call the GitHub API through the logged-in, pinned `gh`.
"""
from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import os
import re
import stat
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Iterable

REPO = Path(__file__).resolve().parents[1]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))
from lib.recovery_evidence import CORE_TABLES, bind, canonical_json, check_clock_skew  # noqa: E402

COPY_RE = re.compile(
    rb'^COPY ((?:"(?:[^"]|"")+"|[A-Za-z_][A-Za-z0-9_$]*))\.((?:"(?:[^"]|"")+"|[A-Za-z_][A-Za-z0-9_$]*))'
    rb'( \((?:"(?:[^"]|"")+"|[A-Za-z_][A-Za-z0-9_$]*)(?:, (?:"(?:[^"]|"")+"|[A-Za-z_][A-Za-z0-9_$]*))*\))? FROM stdin;$'
)
TERMINATOR = b"\\."
RECEIPT_KIND = "restore-exercise-receipt.v1"
COPY_KEYS = ("copy_id", "custody_domain", "primary_domain", "produced_at", "producer_id",
             "recorded_artifact_digest", "recorded_digest_source", "store_readback_digest")
DUMP_MEMBER_RE = re.compile(r"^carr-[0-9]{8}\.sql\.age$")

# The facts about the nightly workflow copy that are true by construction of
# .github/workflows/backup-nightly.yml, not reported by anyone at run time.
CUSTODY_DOMAIN = "github-actions-artifacts"
PRIMARY_DOMAIN = "neon-primary"
PRODUCER_ID = "backup-nightly-workflow"
DIGEST_SOURCE_KIND = "github_actions_backup_check"

# pg_dump's own session settings for COPY text (pg_dump.c setup_connection), so
# the restored side renders every value exactly as the artifact carries it.
# TimeZone is pinned to UTC: the nightly dump runs on a UTC runner against a
# UTC server, so timestamptz values in the artifact carry +00.
SESSION_SETTINGS = (
    ("DateStyle", "ISO"), ("IntervalStyle", "postgres"), ("extra_float_digits", "3"),
    ("TimeZone", "UTC"), ("client_encoding", "UTF8"), ("standard_conforming_strings", "on"),
    ("default_transaction_read_only", "on"), ("search_path", "pg_catalog"),
)
# Review K5: every session this file opens is read-only from its first statement.
READ_ONLY_OPTIONS = "-c default_transaction_read_only=on"
TABLES_SQL = """
select n.nspname, c.relname
  from pg_class c join pg_namespace n on n.oid = c.relnamespace
 where n.nspname in ('public', 'ops') and c.relkind = 'r'
   and not exists (select 1 from pg_depend d
                    where d.classid = 'pg_class'::regclass and d.objid = c.oid and d.deptype = 'e')
 order by 1, 2
"""


def _ident(token: bytes) -> str:
    text = token.decode("utf-8")
    if text.startswith('"'):
        return text[1:-1].replace('""', '"')
    return text


def _as_bytes(raw: bytes | str) -> bytes:
    return raw if isinstance(raw, bytes) else raw.encode("utf-8")


class _Table:
    """One table's rows, hashed order-free: sha256 over the sorted row lines."""

    def __init__(self) -> None:
        self.lines: list[bytes] = []

    def add(self, line: bytes) -> None:
        self.lines.append(line)

    def entry(self) -> dict[str, Any]:
        h = hashlib.sha256()
        for line in sorted(self.lines):
            h.update(line)
            h.update(b"\n")
        return {"rows": len(self.lines), "content_digest": "sha256:" + h.hexdigest()}


def watermark_from_dump(lines: Iterable[bytes | str]) -> dict[str, dict[str, Any]]:
    """Per schema.table: rows, content digest and the COPY column list, from a plain pg_dump stream.

    A COPY block is the header line, one line per row, then a line that is
    exactly backslash-dot. pg_dump escapes embedded newlines and backslashes
    inside COPY text, so a data row can never be that terminator. A stream that
    ends inside a block is a truncated artifact and raises rather than guessing.
    """
    out: dict[str, dict[str, Any]] = {}
    table: str | None = None
    columns = ""
    current = _Table()
    for raw in lines:
        line = _as_bytes(raw).rstrip(b"\n")
        if table is None:
            m = COPY_RE.match(line)
            if m:
                table = f"{_ident(m.group(1))}.{_ident(m.group(2))}"
                if table in out:
                    raise ValueError(f"table {table} has two COPY blocks")
                columns = (m.group(3) or b"").decode("utf-8").strip()
                current = _Table()
            continue
        if line == TERMINATOR:
            out[table] = {**current.entry(), "copy_columns": columns}
            table = None
        else:
            current.add(line)
    if table is not None:
        raise ValueError(f"stream ended inside the COPY block for {table}: truncated artifact")
    if not out:
        raise ValueError("no COPY blocks found: not a plain-format data dump")
    return out


def count_copy_rows(lines: Iterable[bytes | str]) -> dict[str, int]:
    """Row counts only (the older, weaker view; kept for callers that want just counts)."""
    return {t: e["rows"] for t, e in watermark_from_dump(lines).items()}


def strip_columns(watermark: dict[str, dict[str, Any]]) -> dict[str, dict[str, Any]]:
    return {t: {"rows": e["rows"], "content_digest": e["content_digest"]} for t, e in watermark.items()}


def _quote(name: str) -> str:
    return '"' + name.replace('"', '""') + '"'


def restored_watermark(dsn: str, artifact: dict[str, dict[str, Any]]) -> dict[str, dict[str, Any]]:
    """The restored database's watermark, read-only, in the artifact's own COPY form."""
    import psycopg  # the repo venv's driver; imported here so the pure subcommands need none

    out: dict[str, dict[str, Any]] = {}
    with psycopg.connect(dsn, autocommit=True, options=READ_ONLY_OPTIONS) as conn:
        with conn.cursor() as cur:
            for name, value in SESSION_SETTINGS:
                cur.execute("select set_config(%s, %s, false)", (name, value))
            cur.execute(TABLES_SQL)
            tables = [(schema, rel) for schema, rel in cur.fetchall()]
            for schema, rel in tables:
                key = f"{schema}.{rel}"
                columns = str(artifact.get(key, {}).get("copy_columns", ""))
                # The artifact's column list, as pg_dump wrote it (generated columns
                # excluded, attnum order); validated by COPY_RE when it was read.
                statement = " ".join(p for p in ("COPY", f"{_quote(schema)}.{_quote(rel)}", columns, "TO STDOUT") if p)
                table = _Table()
                pending = b""
                with cur.copy(statement) as copy:
                    for chunk in copy:
                        pending += bytes(chunk)
                        *complete, pending = pending.split(b"\n")
                        for line in complete:
                            table.add(line)
                if pending:
                    raise ValueError(f"COPY output for {key} did not end with a newline")
                out[key] = table.entry()
    return out


def compare(artifact: dict[str, dict[str, Any]], restored: dict[str, dict[str, Any]]) -> list[dict]:
    tables = sorted(set(artifact) | set(restored))
    diffs = []
    for t in tables:
        a, r = artifact.get(t), restored.get(t)
        a_rows, r_rows = (a or {}).get("rows"), (r or {}).get("rows")
        a_dig, r_dig = (a or {}).get("content_digest"), (r or {}).get("content_digest")
        if a_rows != r_rows or a_dig != r_dig:
            diffs.append({"table": t, "artifact_rows": a_rows, "restored_rows": r_rows,
                          "content_differs": a_dig != r_dig})
    return diffs


def file_digest(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return "sha256:" + h.hexdigest()


# ── the independently held copy, read back from the store ────────────────────

# Where age, gh and psql are looked up (never PATH). A guard against an
# accidental PATH shim only; see BINARY RESOLUTION in the module docstring.
TRUSTED_BIN_DIRS = ("/opt/homebrew/bin", "/usr/local/bin", "/usr/bin",
                    "/opt/homebrew/opt/libpq/bin", "/usr/local/opt/libpq/bin")


def _untrusted_roots() -> tuple[Path, ...]:
    """This repository (any worktree of it) and every temporary-directory root."""
    return (REPO.resolve(), REPO.resolve().parents[2] if REPO.resolve().parent.name == "worktrees" else REPO.resolve(),
            Path(tempfile.gettempdir()).resolve(), Path("/tmp").resolve(), Path("/var/tmp").resolve(),
            Path("/private/var/folders"), Path("/var/folders"))


def _untrusted_location(real: Path, roots: Iterable[Path] | None = None) -> str | None:
    """Why this resolved binary may not be trusted, or None."""
    for root in (_untrusted_roots() if roots is None else roots):
        if real == root or root in real.parents:
            return f"it lives under {root}"
    mode = real.stat().st_mode
    if mode & (stat.S_IWGRP | stat.S_IWOTH):
        return "it is group- or world-writable"
    return None


def _trusted_binary(name: str) -> str:
    """The absolute path of `name`, from TRUSTED_BIN_DIRS only; PATH is never consulted.

    Review M2: this stops an accidental PATH shim. It is not a defence against
    an adversary running as the same OS user, who owns these directories.
    """
    for directory in TRUSTED_BIN_DIRS:
        candidate = Path(directory) / name
        if candidate.is_file() and os.access(candidate, os.X_OK):
            real = candidate.resolve()
            why = _untrusted_location(real)
            if why:
                raise ValueError(f"refusing {name} at {real}: {why}")
            return str(real)
    raise ValueError(f"{name} is not installed in any of {', '.join(TRUSTED_BIN_DIRS)}")


class _PinnedGh:
    """Stands in for `subprocess` inside ops/backup-workflow-status.py: its `gh` is the pinned one."""

    def __init__(self, gh: str):
        self.gh = gh

    def __getattr__(self, name: str) -> Any:
        return getattr(subprocess, name)

    def run(self, argv, *args, **kwargs):
        if argv and argv[0] == "gh":
            argv = [self.gh, *argv[1:]]
        return subprocess.run(argv, *args, **kwargs)


def _status_module():
    spec = importlib.util.spec_from_file_location("backup_workflow_status", REPO / "ops" / "backup-workflow-status.py")
    if spec is None or spec.loader is None:
        raise ImportError("cannot load ops/backup-workflow-status.py")
    module = importlib.util.module_from_spec(spec)
    # Registered before it runs: its dataclasses resolve annotations through sys.modules.
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    module.subprocess = _PinnedGh(_trusted_binary("gh"))
    return module


BACKUP_RUN_EVENTS = ("schedule", "workflow_dispatch")
BACKUP_RUN_BRANCH = "main"


def _instant(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def _check_bound_to_run(bws, repository: str, identity, run: dict[str, Any], item: dict[str, Any]) -> bool:
    """Is this Actions-app Check, whose external_id already equals the run identity, THIS run's?

    A Check a workflow creates with GITHUB_TOKEN does not land in its own run's
    check suite: read live on 2026-09-24, 0 of the last 5 nightly runs had it
    there; each sat in the main-canary github-actions suite on the same head.
    So the run is bound by: the Actions app stamp; the suite GitHub placed the
    Check in being a github-actions suite for this head on main (or the run's
    own suite); and its external_id (checked by matching_checks) naming this
    run id and attempt. Review H2: the Check's started_at/completed_at are set
    by whoever creates the Check, so they prove nothing and are NOT used.

    RESIDUAL (documented, not closed): the external_id is free text. Any
    workflow allowed checks:write that runs on the same main commit could
    write an identical envelope. _backup_run narrows who that can be: the run
    must be on main, scheduled or dispatched, on a commit in main's history,
    and its backup workflow file must be byte-identical to main's now, so a
    manually dispatched run of an EDITED backup-nightly is refused — and
    editing backup-nightly.yml therefore makes every earlier run unverifiable
    (fails closed) until a fresh nightly runs. What remains is another
    workflow already on main writing a forged Check; admitting workflows to
    main is the control there, and tools/test_workflow_checks_write_grant.py
    fails CI if any workflow but backup-nightly.yml is granted checks: write.
    """
    check_suite = item.get("check_suite")
    suite_id = check_suite.get("id") if isinstance(check_suite, dict) else None
    if suite_id is None:
        return False
    if suite_id != run.get("check_suite_id"):
        suite = bws.api(f"/repos/{repository}/check-suites/{int(suite_id)}")
        if not isinstance(suite, dict):
            return False
        raw_app = suite.get("app")
        app: dict[str, Any] = raw_app if isinstance(raw_app, dict) else {}
        if (app.get("id") != bws.ACTIONS_APP_ID or app.get("slug") != bws.ACTIONS_APP_SLUG
                or suite.get("head_branch") != BACKUP_RUN_BRANCH
                or str(suite.get("head_sha", "")).lower() != identity.head_sha):
            return False
    return True


def _authentic_backup_check(bws, identity, run: dict[str, Any]) -> dict[str, Any]:
    """The run's ONE "Backup artifact" Check, looked up here and never named by a caller.

    The name and the external_id envelope select; the Actions app stamp and
    the suite GitHub placed the Check in authenticate as far as they can; see
    _check_bound_to_run for what remains. Zero or several candidates fail closed.
    """
    repository = identity.repository
    stamped = [
        item for item in bws.matching_checks(identity)
        if isinstance(item.get("app"), dict)
        and item["app"].get("id") == bws.ACTIONS_APP_ID and item["app"].get("slug") == bws.ACTIONS_APP_SLUG
    ]
    candidates = [item for item in stamped if _check_bound_to_run(bws, repository, identity, run, item)]
    if len(candidates) != 1:
        raise ValueError(
            f"expected one provider-authenticated Backup artifact Check bound to run {identity.run_id}, "
            f"found {len(candidates)} (of {len(stamped)} Actions-app Checks carrying its envelope); refusing")
    item = candidates[0]
    if item.get("status") != "completed" or item.get("conclusion") != "success":
        raise ValueError("the Backup artifact Check did not complete successfully")
    return item


def _backup_run(bws, repository: str, run_id: int):
    """A nightly backup run whose copy may be trusted: the workflow's own, on main, started by the schedule or by hand."""
    run = bws.api(f"/repos/{repository}/actions/runs/{run_id}")
    if not isinstance(run, dict):
        raise ValueError("workflow run API returned no object")
    if run.get("path") != bws.BACKUP_WORKFLOW_PATH:
        raise ValueError(f"run {run_id} is not a run of {bws.BACKUP_WORKFLOW_PATH}")
    if run.get("conclusion") != "success":
        raise ValueError(f"run {run_id} did not conclude success")
    # G1: a run of the backup workflow file from any other branch or trigger
    # (a pull request, a push to a feature branch) runs code nobody reviewed
    # onto main, so its artifact is not the producer's copy.
    if run.get("head_branch") != BACKUP_RUN_BRANCH:
        raise ValueError(f"run {run_id} ran on {run.get('head_branch')!r}, not {BACKUP_RUN_BRANCH}")
    if run.get("event") not in BACKUP_RUN_EVENTS:
        raise ValueError(f"run {run_id} was triggered by {run.get('event')!r}, not {' or '.join(BACKUP_RUN_EVENTS)}")
    head_sha = str(run.get("head_sha", "")).lower()
    if not re.fullmatch(r"[0-9a-f]{40}", head_sha):
        raise ValueError(f"run {run_id} has no commit sha")
    # head_branch is a name; the commit must actually be in main's history.
    cmp = bws.api(f"/repos/{repository}/compare/{head_sha}...{BACKUP_RUN_BRANCH}")
    if not isinstance(cmp, dict) or cmp.get("status") not in ("ahead", "identical") or cmp.get("behind_by") != 0:
        raise ValueError(f"run {run_id}'s commit is not an ancestor of {BACKUP_RUN_BRANCH}")
    # H2: a manual dispatch runs the workflow file AT the run's commit. It must
    # be byte-identical (same git blob) to main's backup workflow now, so an
    # edited backup-nightly cannot produce a copy that counts.
    at_run = _workflow_blob(bws, repository, bws.BACKUP_WORKFLOW_PATH, head_sha)
    on_main = _workflow_blob(bws, repository, bws.BACKUP_WORKFLOW_PATH, BACKUP_RUN_BRANCH)
    if at_run is None or on_main is None or at_run != on_main:
        raise ValueError(f"run {run_id}'s {bws.BACKUP_WORKFLOW_PATH} is not the one on {BACKUP_RUN_BRANCH} now")
    identity = bws.Identity(repository, int(run["id"]), int(run["run_attempt"]), head_sha)
    return run, identity


def _workflow_blob(bws, repository: str, path: str, ref: str) -> str | None:
    got = bws.api(f"/repos/{repository}/contents/{path}", query={"ref": ref})
    sha = got.get("sha") if isinstance(got, dict) else None
    return sha if isinstance(sha, str) and re.fullmatch(r"[0-9a-f]{40}", sha) else None


def read_copy_record(repository: str, run_id: int) -> dict[str, Any]:
    """The copy's facts, every one read from the provider: the Check summary and the artifact API."""
    bws = _status_module()
    run, identity = _backup_run(bws, repository, run_id)
    check = _authentic_backup_check(bws, identity, run)
    summary = bws.summary_of(check)
    artifacts = bws.run_artifacts(identity)
    if not bws.summary_matches_artifact(identity, summary, artifacts):
        raise ValueError("the Backup artifact Check summary does not match the artifact the store holds")
    assert isinstance(summary, dict)
    stored = next(a for a in artifacts if a.get("id") == summary["artifact_id"])
    return {
        "copy_id": str(summary["artifact_name"]),
        "custody_domain": CUSTODY_DOMAIN,
        "primary_domain": PRIMARY_DOMAIN,
        "producer_id": PRODUCER_ID,
        "produced_at": str(summary["artifact_created_at"]),
        "recorded_artifact_digest": str(summary["artifact_digest"]),
        "recorded_digest_source": {"kind": DIGEST_SOURCE_KIND, "check_run_id": int(check["id"]),
                                   "workflow_run_id": identity.run_id},
        "store_readback_digest": str(stored["digest"]),
        "_artifact_id": int(summary["artifact_id"]),
    }


def download_copy(repository: str, artifact_id: int, out_dir: Path) -> tuple[Path, Path]:
    """Download one stored artifact ZIP and extract its one dump. Returns (zip, dump)."""
    out_dir.mkdir(parents=True, exist_ok=True)
    archive = out_dir / "artifact.zip"
    with archive.open("wb") as fh:
        got = subprocess.run([_trusted_binary("gh"), "api", f"/repos/{repository}/actions/artifacts/{artifact_id}/zip"],
                             stdout=fh, stderr=subprocess.PIPE, timeout=600, check=False)
    if got.returncode:
        raise ValueError(f"artifact download failed: {got.stderr.decode(errors='replace').strip()[-200:]}")
    with zipfile.ZipFile(archive) as zf:
        members = zf.namelist()
        if len(members) != 1 or not DUMP_MEMBER_RE.fullmatch(members[0]):
            raise ValueError(f"artifact must hold exactly one carr-YYYYMMDD.sql.age, found {members}")
        dump = out_dir / members[0]
        dump.write_bytes(zf.read(members[0]))
    return archive, dump


def fetch_copy(repository: str, run_id: int, out_dir: Path) -> tuple[Path, Path]:
    """The copy the rehearsal RESTORES. Writes copy.json for the operator's eyes only; nothing reads it back."""
    record = read_copy_record(repository, run_id)
    archive, dump = download_copy(repository, record.pop("_artifact_id"), out_dir)
    (out_dir / "copy.json").write_text(json.dumps(record, sort_keys=True))
    return archive, dump


# ── verify-restore: every decisive fact is read here, none handed in (H1) ────

AGE = "age"
NEON_API = "https://console.neon.tech/api/v2"
ORACLE_ID = "restore-rehearse"


def artifact_watermark_from(dump: Path, identity: Path) -> dict[str, dict[str, Any]]:
    """Decrypt the dump THIS process downloaded and count what it carries. The plaintext is a pipe only."""
    if not identity.is_file():
        raise ValueError("the age identity file does not exist")
    proc = subprocess.Popen([_trusted_binary(AGE), "--decrypt", "-i", str(identity), str(dump)],
                            stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=_base_env())
    assert proc.stdout is not None and proc.stderr is not None
    parse_error: ValueError | None = None
    watermark: dict[str, dict[str, Any]] = {}
    try:
        watermark = watermark_from_dump(proc.stdout)
    except ValueError as exc:  # judged after age's exit: a failed decrypt is named as such
        parse_error = exc
    finally:
        proc.stdout.close()
        err = proc.stderr.read().decode(errors="replace").strip()
        code = proc.wait()
    if code:
        raise ValueError(f"age could not decrypt the downloaded artifact: {err[-200:]}")
    if parse_error is not None:
        raise parse_error
    return watermark


def _neon(path: str) -> Any:
    key = os.environ.get("NEON_API_KEY", "")
    if not key:
        raise ValueError("NEON_API_KEY is not loaded; the branch target is read from the provider")
    req = urllib.request.Request(NEON_API + path, headers={
        "Authorization": f"Bearer {key}", "Accept": "application/json", "User-Agent": "carr-restore-verify"})
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            return json.loads(resp.read() or b"{}")
    except urllib.error.HTTPError as exc:
        raise ValueError(f"provider GET {path} answered {exc.code}") from None


def _dsn_host(dsn: str) -> str:
    from psycopg.conninfo import conninfo_to_dict

    return str(conninfo_to_dict(dsn).get("host") or "")


def _utc_seconds(value: datetime) -> str:
    """Whole seconds, FLOORED: a start read this way is never later than the truth."""
    return value.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _dsn_dbname(dsn: str) -> str:
    from psycopg.conninfo import conninfo_to_dict

    return str(conninfo_to_dict(dsn).get("dbname") or "")


INHERITED_DATABASE = "neondb"
PRODUCTION_ROLE = "neondb_owner"
DATABASE_NAME_RE = re.compile(r"^[a-z_][a-z0-9_]{0,62}$")
LSN_RE = re.compile(r"^([0-9A-Fa-f]{1,8})/([0-9A-Fa-f]{1,8})$")


def parse_lsn(value: Any) -> int | None:
    m = LSN_RE.fullmatch(value) if isinstance(value, str) else None
    return (int(m.group(1), 16) << 32) | int(m.group(2), 16) if m else None


def _default_branch(project_id: str) -> str:
    listed = _neon(f"/projects/{project_id}/branches").get("branches") or []
    defaults = [b.get("id") for b in listed if b.get("default")]
    if len(defaults) != 1 or not isinstance(defaults[0], str):
        raise ValueError("the provider did not report exactly one default branch")
    return defaults[0]


def format_lsn(value: int) -> str:
    return f"{value >> 32:X}/{value & 0xFFFFFFFF:X}"


# Round-5 review: the branch's parent_lsn is what the provider's STORAGE had
# ingested when the branch was made; pg_current_wal_flush_lsn() is what the
# compute had flushed to it. Ordinary ingest lag behind the flush point is
# well under a second to a few seconds, so the verifier waits this long after
# reading the flush point before it signals armed (and the rehearsal creates
# the branch). A point-in-time child taken at or before the artifact's dump,
# hours earlier, precedes the flush point by every write since and is still
# refused. The wait is bounded both ways; a lag longer than the wait is not
# ordinary and is refused with the gap named.
DEFAULT_SETTLE_SECONDS = 30
MIN_SETTLE_SECONDS = 5
MAX_SETTLE_SECONDS = 300


def production_head_lsn(project_id: str) -> int:
    """Production's FLUSHED WAL position, read by THIS process on a provider-issued read-only session."""
    default = _default_branch(project_id)
    uri = str(_neon(f"/projects/{project_id}/connection_uri?branch_id={default}"
                    f"&database_name={INHERITED_DATABASE}&role_name={PRODUCTION_ROLE}").get("uri") or "")
    if not uri:
        raise ValueError("the provider issued no production connection to read its head LSN")
    import psycopg

    with psycopg.connect(uri, autocommit=True, options=READ_ONLY_OPTIONS, connect_timeout=30) as conn:
        got = conn.execute("select pg_current_wal_flush_lsn()::text").fetchone()
    lsn = parse_lsn(got[0] if got else None)
    if lsn is None:
        raise ValueError("production reported no head LSN")
    return lsn


def branch_target_start(project_id: str, branch_id: str, admin_dsn: str, database: str,
                        head_lsn: int | None) -> tuple[str, dict[str, Any]]:
    """The throwaway branch as the PROVIDER reports it; its creation is the restore's earliest start.

    Refused unless the branch exists, is not the default (production) branch or
    a protected one, is a child of the default branch, and the admin DSN's host
    is one of THIS branch's endpoints. Review M3: the branch must come from
    production's HEAD as it stood when this verifier armed — its parent_lsn at
    or after `head_lsn`, production's flushed WAL position read before the
    branch existed and followed by the settle wait (round-5 review: storage
    ingest lag). A missing parent_lsn or head reading fails closed. (A
    point-in-time child, e.g. one taken at dump time, has an earlier
    parent_lsn and is refused; an idle production leaves the head unchanged,
    so a genuine head branch passes however long production has been quiet.)
    Returns the start instant and the recorded point {head_lsn, parent_lsn,
    gap_bytes}. Review K1, kept as defence in depth now that
    the verifier creates the database itself: the target database may not be
    neondb or any name that also exists on the parent branch.
    """
    default = _default_branch(project_id)
    branch = _neon(f"/projects/{project_id}/branches/{branch_id}").get("branch") or {}
    if branch.get("id") != branch_id:
        raise ValueError(f"the provider does not report branch {branch_id}")
    if branch.get("default") or branch.get("protected") or branch_id == default:
        raise ValueError("the restore target is the production (default) or a protected branch; refusing")
    if branch.get("parent_id") != default:
        raise ValueError("the restore target branch is not a child of the production branch")
    if head_lsn is None:
        raise ValueError("no production head LSN was read before the branch existed; refusing")
    parent_lsn = parse_lsn(branch.get("parent_lsn"))
    if parent_lsn is None:
        raise ValueError("the provider reported no parent_lsn for the restore target branch; refusing")
    if parent_lsn < head_lsn:
        raise ValueError(f"the restore target branch's parent_lsn {format_lsn(parent_lsn)} is "
                         f"{head_lsn - parent_lsn} bytes before production's flushed head {format_lsn(head_lsn)} "
                         "read at arming (a point-in-time branch, or storage lag longer than the settle wait); "
                         "refusing")
    if not database or database == INHERITED_DATABASE:
        raise ValueError(f"the target database {database or '(none)'!r} is one the branch inherits; "
                         "the restore must target a database it creates")
    inherited = {str(d.get("name")) for d in (_neon(f"/projects/{project_id}/branches/{default}/databases")
                                              .get("databases") or [])}
    if not inherited:
        raise ValueError("the provider reported no databases on the production branch; cannot rule out an inherited target")
    if database in inherited:
        raise ValueError(f"database {database!r} also exists on the production branch; the branch inherited it")
    hosts = {str(e.get("host")) for e in (_neon(f"/projects/{project_id}/branches/{branch_id}/endpoints")
                                          .get("endpoints") or [])}
    if _dsn_host(admin_dsn) not in hosts:
        raise ValueError("the admin DSN does not point at an endpoint of the named branch")
    created = _instant(branch.get("created_at"))
    if created is None:
        raise ValueError("the provider reported no creation instant for the branch")
    point = {"head_lsn": format_lsn(head_lsn), "parent_lsn": format_lsn(parent_lsn),
             "gap_bytes": parent_lsn - head_lsn}
    return _utc_seconds(created), point


# The statement classes a scoped plain pg_dump carries that a fresh throwaway
# database cannot apply (see bin/restore-rehearse.sh, THE PORTABILITY FILTER).
# Unlike the script's sed, this never touches COPY data lines.
RESTORE_STATEMENT_FILTER = re.compile(
    rb"^(CREATE SCHEMA public;|ALTER DEFAULT PRIVILEGES|GRANT |REVOKE |ALTER .* OWNER TO |COMMENT ON EXTENSION )")
RESTORE_EXTENSIONS = ("pg_trgm", "pgcrypto")


def restore_stream(lines: Iterable[bytes]) -> Iterable[bytes]:
    """The dump with the non-portable statements dropped; COPY blocks pass through untouched."""
    in_copy = False
    for raw in lines:
        line = raw.rstrip(b"\n")
        if in_copy:
            yield raw
            in_copy = line != TERMINATOR
            continue
        if COPY_RE.match(line):
            in_copy = True
            yield raw
            continue
        if RESTORE_STATEMENT_FILTER.match(line):
            continue
        yield raw


# Round-5 review: the ONLY variables age and psql inherit. Nothing else in this
# process's environment (the provider API key above all) reaches a child.
SAFE_ENV_KEYS = ("HOME", "LANG", "LC_ALL", "LC_CTYPE", "LC_MESSAGES", "TZ", "TMPDIR")
SAFE_PATH = "/usr/bin:/bin"


def _base_env() -> dict[str, str]:
    env = {k: os.environ[k] for k in SAFE_ENV_KEYS if k in os.environ}
    env["PATH"] = SAFE_PATH
    return env


PG_ENV_KEYS = {"host": "PGHOST", "port": "PGPORT", "user": "PGUSER", "password": "PGPASSWORD",
               "dbname": "PGDATABASE", "sslmode": "PGSSLMODE", "options": "PGOPTIONS",
               "channel_binding": "PGCHANNELBINDING", "connect_timeout": "PGCONNECT_TIMEOUT",
               "application_name": "PGAPPNAME", "sslrootcert": "PGSSLROOTCERT"}


def _pg_env(dsn: str) -> dict[str, str]:
    """psql's environment for `dsn`: every parameter as a PG* variable, none on an argument list."""
    from psycopg.conninfo import conninfo_to_dict

    params = conninfo_to_dict(dsn)
    unknown = sorted(set(params) - set(PG_ENV_KEYS))
    if unknown:
        raise ValueError(f"unrecognised connection parameter(s) {', '.join(unknown)}")
    env = _base_env()
    env.update({PG_ENV_KEYS[k]: str(v) for k, v in params.items() if v is not None})
    return env


def create_target_database(admin_dsn: str, database: str) -> str:
    """CREATE DATABASE (new by construction: it fails if the name exists), its extensions; returns its DSN."""
    if not DATABASE_NAME_RE.fullmatch(database or ""):
        raise ValueError(f"target database name {database!r} is not a plain lower-case identifier")
    import psycopg
    from psycopg import sql
    from psycopg.conninfo import make_conninfo

    with psycopg.connect(admin_dsn, autocommit=True, connect_timeout=30) as conn:
        conn.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(database)))
    target = make_conninfo(admin_dsn, dbname=database)
    with psycopg.connect(target, autocommit=True, connect_timeout=30) as conn:
        for ext in RESTORE_EXTENSIONS:
            conn.execute(sql.SQL("CREATE EXTENSION IF NOT EXISTS {}").format(sql.Identifier(ext)))
    return target


# psql's ERROR/FATAL/PANIC lines, age's own lines, and this tool's refusal line
# (so a rehearsal's tail still says WHY verify-restore stopped).
ERROR_LINE_RE = re.compile(r"^(?:psql:[^ ]*: )?(?:ERROR|FATAL|PANIC):|^age: |^restore-watermark: ")
QUOTED_RE = re.compile(r'"[^"]*"|\'[^\']*\'|\([^()]*\)')


def redact_errors(text: str) -> list[str]:
    """psql/age stderr with no row data: only ERROR/FATAL/PANIC and age lines, every quoted
    or parenthesised value replaced. CONTEXT, DETAIL, HINT, LINE and bare data lines are dropped."""
    out = []
    for line in text.splitlines():
        line = line.rstrip()
        if ERROR_LINE_RE.match(line):
            out.append(QUOTED_RE.sub("<redacted>", line)[:300])
    return out


def load_artifact(dump: Path, identity: Path, target_dsn: str, work_dir: Path) -> None:
    """Decrypt the downloaded artifact and load it into the target through psql, in pipes only."""
    err_path = work_dir / "load.err"
    with err_path.open("wb") as err, open(os.devnull, "wb") as out:
        age = subprocess.Popen([_trusted_binary(AGE), "--decrypt", "-i", str(identity), str(dump)],
                               stdout=subprocess.PIPE, stderr=err, env=_base_env())
        psql = subprocess.Popen([_trusted_binary("psql"), "-X", "-q", "-v", "ON_ERROR_STOP=1",
                                 "-v", "VERBOSITY=terse", "-v", "SHOW_CONTEXT=never"],
                                stdin=subprocess.PIPE, stdout=out, stderr=err, env=_pg_env(target_dsn))
        assert age.stdout is not None and psql.stdin is not None
        try:
            for chunk in restore_stream(age.stdout):
                psql.stdin.write(chunk)
        except BrokenPipeError:
            pass  # psql stopped on an error; its exit status says so below
        finally:
            age.stdout.close()
            try:
                psql.stdin.close()
            except BrokenPipeError:
                pass
            age_code, psql_code = age.wait(), psql.wait()
    # The raw stderr never survives: it is rewritten redacted before anything reads it.
    redacted = redact_errors(err_path.read_text(errors="replace"))
    err_path.write_text("".join(f"{line}\n" for line in redacted))
    if age_code or psql_code:
        tail = redacted[-5:]
        raise ValueError(f"the artifact did not decrypt and load (age {age_code}, psql {psql_code}): {' | '.join(tail)}")


# Round-5 review: loopback is not enough — a tunnel to the hosted production
# server is also loopback. A server that reports any of the provider's own
# settings is refused before anything is created or loaded.
PROVIDER_SETTINGS_SQL = ("select (select count(*) from pg_settings where name like 'neon.%'), "
                         "current_setting('shared_preload_libraries', true)")


def local_target_start(dsn: str) -> str:
    """A disposable LOCAL cluster: socket path or loopback, no provider settings; its postmaster start is the earliest start."""
    host = _dsn_host(dsn)
    if not (host.startswith("/") or host in ("localhost", "127.0.0.1", "::1")):
        raise ValueError("a local-cluster target must be reached over a socket path or loopback")
    import psycopg

    with psycopg.connect(dsn, autocommit=True, options=READ_ONLY_OPTIONS) as conn:
        provider = conn.execute(PROVIDER_SETTINGS_SQL).fetchone()
        if not provider or provider[0] or "neon" in str(provider[1] or "").lower():
            raise ValueError("the local-cluster target reports provider settings (a tunnel to a hosted "
                             "server, not a disposable local cluster); refusing before CREATE DATABASE")
        started = conn.execute("select pg_postmaster_start_time()").fetchone()
    if not started or not isinstance(started[0], datetime):
        raise ValueError("the local cluster reported no start instant")
    return _utc_seconds(started[0])


def server_clock(dsn: str) -> datetime:
    """The target server's clock_timestamp(), read-only (review K4)."""
    import psycopg

    with psycopg.connect(dsn, autocommit=True, options=READ_ONLY_OPTIONS) as conn:
        got = conn.execute("select clock_timestamp()").fetchone()
    if not got or not isinstance(got[0], datetime):
        raise ValueError("the restore target reported no clock")
    return got[0]


def verify_restore(*, repository: str, run_id: int, identity: Path, target_kind: str, database: str,
                   work_dir: Path, project_id: str | None = None, admin_dsn: str | None = None,
                   handoff: Callable[[], tuple[str, str]] | None = None, armed: Callable[[], None] | None = None,
                   settle_seconds: int = DEFAULT_SETTLE_SECONDS, sleep: Callable[[float], None] = time.sleep,
                   now: Callable[[], datetime] = lambda: datetime.now(timezone.utc)) -> dict[str, Any]:
    """Restore the artifact into a database this process creates, and receipt it from its own reads."""
    head_lsn: int | None = None
    point: dict[str, Any] | None = None
    if target_kind == "disposable_branch":
        if not project_id or handoff is None:
            raise ValueError("a branch target needs --project-id and the branch handed over on stdin")
        if not MIN_SETTLE_SECONDS <= settle_seconds <= MAX_SETTLE_SECONDS:
            raise ValueError(f"the settle wait must be {MIN_SETTLE_SECONDS}..{MAX_SETTLE_SECONDS} s, not {settle_seconds}")
        head_lsn = production_head_lsn(project_id)  # BEFORE the branch exists (review M3)
    elif target_kind == "disposable_local_cluster":
        if not admin_dsn:
            raise ValueError("a local-cluster target needs its admin DSN in the environment")
    else:
        raise ValueError(f"target kind must be disposable_branch or disposable_local_cluster, not {target_kind!r}")
    if not DATABASE_NAME_RE.fullmatch(database or ""):
        raise ValueError(f"target database name {database!r} is not a plain lower-case identifier")
    if head_lsn is not None:
        sleep(settle_seconds)  # storage ingests the flushed head before the branch can exist (round-5 review)
        point = {"settle_seconds": settle_seconds}
    if armed is not None:
        armed()
    work_dir.mkdir(parents=True, exist_ok=True)
    record = read_copy_record(repository, run_id)
    archive, dump = download_copy(repository, record.pop("_artifact_id"), work_dir)
    observed = file_digest(archive)
    artifact = artifact_watermark_from(dump, identity)
    missing = [t for t in CORE_TABLES if int(artifact.get(t, {}).get("rows", 0)) <= 0]
    if missing:
        raise ValueError(f"the artifact carries no rows for core table(s) {', '.join(missing)}; not a record-layer dump")
    if target_kind == "disposable_branch":
        assert handoff is not None and project_id
        branch_id, admin_dsn = handoff()
        started_at, read_point = branch_target_start(project_id, branch_id, admin_dsn, database, head_lsn)
        assert point is not None
        point.update(read_point)
    else:
        assert admin_dsn
        started_at = local_target_start(admin_dsn)
    target = create_target_database(admin_dsn, database)
    load_artifact(dump, identity, target, work_dir)
    restored = restored_watermark(target, artifact)
    finished = now()  # this process's own clock, after its last read; it also stamps the binding
    check_clock_skew(finished, server_clock(target), "the restore target server")
    finished_at = _utc_seconds(finished)
    receipt = {
        "receipt_kind": RECEIPT_KIND,
        "target_kind": target_kind,
        "copy": {k: record[k] for k in COPY_KEYS},
        "oracle_id": ORACLE_ID,
        "observed_artifact_digest": observed,
        "artifact_watermark": strip_columns(artifact),
        "restored_watermark": strip_columns(restored),
        "started_at": started_at,
        "finished_at": finished_at,
    }
    if point is not None:
        receipt["target_point"] = point
    return bind(receipt, "restore_exercise", finished)


def _stdin_handoff() -> tuple[str, str]:
    """The branch id and its admin DSN, one per line, from the rehearsal after it creates the branch."""
    branch_id = sys.stdin.readline().strip()
    admin_dsn = sys.stdin.readline().strip()
    if not branch_id or not admin_dsn:
        raise ValueError("no branch was handed over on stdin (the rehearsal stopped before creating it)")
    return branch_id, admin_dsn


def _signal_armed(path: str | None) -> Callable[[], None] | None:
    if not path:
        return None

    def armed() -> None:
        Path(path).write_text("armed\n")
    return armed


# ── the outbound census, read from the RESTORED database (review G2, H3) ─────

OUTBOUND_CENSUS_SOURCE = "ops.notification_delivery:device"
OUTBOUND_SQL = """
select id::text, notification_id::text, channel, state, attempted_at
  from ops.notification_delivery
 where channel = 'device'
 order by id
"""

# H3: a readback is the PROVIDER's answer about one effect, read by code in
# this file with its own clock, never JSON an operator typed. A reader takes
# the items of its channel and returns readbacks shaped
# {item_id, idempotency_key, read_at, readback}. No device push sender exists
# in this repository yet, so there is no provider to ask and no reader: every
# unsettled device item stays quarantined until one is added HERE.
PROVIDER_READERS: dict[str, Callable[[list[dict[str, Any]]], list[dict[str, Any]]]] = {}


def _utc_micros(value: datetime) -> str:
    return value.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def outbound_item(row_id: str, notification_id: str, channel: str, state: str, attempted_at: datetime) -> dict[str, Any]:
    """One device delivery row as an outbound item (mapping decided with Jev, 0.65).

    in_app rows are rendered in the record layer and have no provider effect,
    so only device rows are outbound. A pending row may have been handed to the
    push provider before the outage, so its outcome is unknown and it carries
    its attempt instant into the settle window; delivered, suppressed and
    failed rows are final. The envelope key is the delivery's natural key
    (unique (notification_id, channel)), digested canonically.
    """
    envelope = "sha256:" + hashlib.sha256(canonical_json({"channel": channel, "notification_id": notification_id}).encode()).hexdigest()
    return {"item_id": row_id, "envelope_digest": envelope,
            "state": "outcome_unknown" if state == "pending" else "settled",
            "last_attempt_at": _utc_micros(attempted_at)}


def census_digest(items: list[dict[str, Any]]) -> str:
    """Byte-for-byte recovery-matrix.v5.js v5OutboundCensusDigest."""
    keyed = [{k: i[k] for k in ("envelope_digest", "item_id", "last_attempt_at", "state")} for i in items]
    keyed.sort(key=lambda i: i["item_id"])
    return "sha256:" + hashlib.sha256(canonical_json(keyed).encode("utf-8")).hexdigest()


def restored_outbound_items(dsn: str) -> list[dict[str, Any]]:
    import psycopg

    with psycopg.connect(dsn, autocommit=True, options=READ_ONLY_OPTIONS) as conn:
        with conn.cursor() as cur:
            cur.execute(OUTBOUND_SQL)
            return [outbound_item(*row) for row in cur.fetchall()]


def provider_readbacks(items: list[dict[str, Any]], channel: str = "device") -> list[dict[str, Any]]:
    """Ask the channel's registered provider reader about every unsettled item; none registered, none read."""
    reader = PROVIDER_READERS.get(channel)
    unsettled = [i for i in items if i["state"] != "settled"]
    return reader(unsettled) if reader and unsettled else []


def outbound_census(items: list[dict[str, Any]], restore_id: str, now: datetime | None = None) -> dict[str, Any]:
    """The evaluator's request: every restored device row, the provider's own readbacks, the census, bound."""
    request = {
        "restore_id": restore_id,
        "census": {"source": OUTBOUND_CENSUS_SOURCE, "digest": census_digest(items), "item_count": len(items)},
        "items": items,
        "readbacks": provider_readbacks(items),
    }
    return bind(request, "outbound_census", now)


def _load_json(path: str) -> Any:
    return json.loads(Path(path).read_text())


def _dsn_from(env_name: str) -> str:
    dsn = os.environ.get(env_name, "")
    if not dsn:
        raise ValueError(f"${env_name} is empty; the restored database DSN is read from the environment only")
    return dsn


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("count")
    rs = sub.add_parser("restored")
    rs.add_argument("--artifact", required=True)
    rs.add_argument("--dsn-env", default="RESTORE_DSN")
    c = sub.add_parser("compare")
    c.add_argument("--artifact", required=True)
    c.add_argument("--restored", required=True)
    d = sub.add_parser("digest")
    d.add_argument("path")
    f = sub.add_parser("fetch-copy")
    f.add_argument("--repository", required=True)
    f.add_argument("--run-id", required=True, type=int)
    f.add_argument("--out-dir", required=True)
    v = sub.add_parser("verify-restore")
    v.add_argument("--repository", required=True)
    v.add_argument("--run-id", required=True, type=int)
    v.add_argument("--identity", required=True, help="path to the age identity file (read by age, never printed)")
    v.add_argument("--target-kind", required=True, choices=("disposable_branch", "disposable_local_cluster"))
    v.add_argument("--project-id")
    v.add_argument("--database", required=True, help="the target database this process creates and loads")
    v.add_argument("--work-dir", required=True)
    v.add_argument("--admin-dsn-env", default="RESTORE_ADMIN_DSN",
                   help="local-cluster target: the env var holding the admin DSN (a branch's comes on stdin)")
    v.add_argument("--armed-file", help="written once production's head LSN has been read (liveness only)")
    v.add_argument("--settle-seconds", type=int, default=DEFAULT_SETTLE_SECONDS,
                   help=f"branch target: wait after reading the head, {MIN_SETTLE_SECONDS}..{MAX_SETTLE_SECONDS} s")
    sub.add_parser("redact-errors", help="stdin psql/age stderr -> stdout with no row data")
    o = sub.add_parser("outbound-census")
    o.add_argument("--restore-id", required=True)
    o.add_argument("--dsn-env", default="RESTORE_DSN")
    a = p.parse_args(argv)
    try:
        if a.cmd == "count":
            print(json.dumps(watermark_from_dump(sys.stdin.buffer), sort_keys=True))
            return 0
        if a.cmd == "redact-errors":
            for line in redact_errors(sys.stdin.read()):
                print(line)
            return 0
        if a.cmd == "digest":
            print(file_digest(Path(a.path)))
            return 0
        if a.cmd == "restored":
            print(json.dumps(restored_watermark(_dsn_from(a.dsn_env), _load_json(a.artifact)), sort_keys=True))
            return 0
        if a.cmd == "fetch-copy":
            archive, dump = fetch_copy(a.repository, a.run_id, Path(a.out_dir))
            print(json.dumps({"archive": str(archive), "dump": str(dump)}))
            return 0
        if a.cmd == "verify-restore":
            branch = a.target_kind == "disposable_branch"
            receipt = verify_restore(repository=a.repository, run_id=a.run_id, identity=Path(a.identity),
                                     target_kind=a.target_kind, database=a.database, work_dir=Path(a.work_dir),
                                     project_id=a.project_id,
                                     admin_dsn=None if branch else _dsn_from(a.admin_dsn_env),
                                     handoff=_stdin_handoff if branch else None,
                                     armed=_signal_armed(a.armed_file), settle_seconds=a.settle_seconds)
            print(json.dumps(receipt, sort_keys=True))
            return 0
        if a.cmd == "outbound-census":
            print(json.dumps(outbound_census(restored_outbound_items(_dsn_from(a.dsn_env)), a.restore_id), sort_keys=True))
            return 0
        artifact = _load_json(a.artifact)
        restored = _load_json(a.restored)
        diffs = compare(artifact, restored)
        print(f"WATERMARK tables={len(artifact)} mismatches={len(diffs)}")
        for d_ in diffs[:40]:
            print(f"  MISMATCH {d_['table']}: artifact={d_['artifact_rows']} restored={d_['restored_rows']}"
                  f" content_differs={str(d_['content_differs']).lower()}")
        return 0 if not diffs else 1
    except (ValueError, OSError, json.JSONDecodeError, KeyError, zipfile.BadZipFile) as exc:
        print(f"restore-watermark: {exc}", file=sys.stderr)
        return 2
    except Exception as exc:  # a status-module StatusError or a driver error: still a contract failure
        print(f"restore-watermark: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
