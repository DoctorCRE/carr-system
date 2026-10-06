"""Export a plain-text portability mirror of a Doctrine Postgres database."""

from __future__ import annotations

import argparse
import csv
import errno
import hashlib
import json
import os
import shutil
import sys
import tempfile
import time
import traceback
from collections.abc import Callable, Iterable, Mapping, Sequence
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, TextIO


EXCLUDED_TABLES = frozenset({"sensitive_blob", "tool_call"})
MANIFEST_WARNING = (
    "SNAPSHOT — never edit, never read in normal operation. Restore path: "
    "markdown re-imports via pipelines/doctrine_import.py; CSV reloads via COPY."
)


def utc_timestamp(now: datetime | None = None) -> str:
    """Return a stable ISO-8601 UTC timestamp."""
    value = now or datetime.now(timezone.utc)
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def snapshot_banner(timestamp: str) -> str:
    """Build the three-line warning banner used by generated markdown files."""
    return (
        "<!-- GENERATED SNAPSHOT — DO NOT EDIT\n"
        f"UTC timestamp: {timestamp}\n"
        "Re-import via pipelines/doctrine_import.py -->"
    )


def _json_default(value: Any) -> str:
    """Render a value json.dumps refuses, the way a scalar cell is rendered.

    A container cell is only as portable as the values inside it. Until
    2026-08-17 the container branch below called json.dumps with no default,
    so every type the scalar branches handle by hand — UUID, datetime, bytes —
    raised TypeError the moment it appeared INSIDE a list or dict rather than
    on its own. A uuid[] column did exactly that and took the whole mirror
    down: no CSV, no markdown, nothing written to either location. Routing
    back through serialize_cell keeps one rendering for each type wherever it
    sits, so a nested value can never be less serializable than a bare one.
    """
    return serialize_cell(value)


def serialize_cell(value: Any) -> str:
    """Serialize one database value for the dependency-free CSV mirror."""
    if value is None:
        return ""
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (dict, list)):
        return json.dumps(value, sort_keys=True, default=_json_default)
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, memoryview):
        return value.tobytes().hex()
    if isinstance(value, bytes):
        return value.hex()
    return str(value)


def _field(row: Any, index: int, name: str) -> Any:
    if isinstance(row, Mapping):
        return row[name]
    return row[index]


def render_document_markdown(
    document: Sequence[Any] | Mapping[str, Any],
    sections: Iterable[Sequence[Any] | Mapping[str, Any]],
    timestamp: str,
) -> str:
    """Render a document and its active sections from tuple- or dict-like rows."""
    title = _field(document, 2, "title")
    active = [row for row in sections if _field(row, 4, "status") == "active"]
    active.sort(key=lambda row: _field(row, 3, "ordinal"))

    parts = [snapshot_banner(timestamp), "", f"# {title}"]
    for row in active:
        section_key = _field(row, 1, "section_key")
        section_title = _field(row, 2, "title") or section_key
        plain_text = _field(row, 6, "plain_text") or ""
        parts.extend(("", f"## {section_title}", "", str(plain_text)))
    return "\n".join(parts).rstrip() + "\n"


def _scope_kind(scope: Any) -> Any:
    if isinstance(scope, str):
        try:
            scope = json.loads(scope)
        except json.JSONDecodeError:
            return None
    return scope.get("kind") if isinstance(scope, Mapping) else None


def render_rules_markdown(
    rows: Iterable[Sequence[Any] | Mapping[str, Any]], timestamp: str
) -> str:
    """Render compiled rules grouped into shared and per-person sections."""
    shared: list[Any] = []
    personal: dict[str, list[Any]] = {}
    for row in rows:
        if _scope_kind(_field(row, 4, "scope")) == "intro_politics":
            continue
        personal_to = _field(row, 3, "personal_to")
        if personal_to is None:
            shared.append(row)
        else:
            personal.setdefault(str(personal_to), []).append(row)

    parts = [snapshot_banner(timestamp), "", "# Compiled Rules", "", "## SHARED"]
    _append_rule_bullets(parts, shared)
    for slug in sorted(personal):
        parts.extend(("", f"## PERSONAL — {slug}"))
        _append_rule_bullets(parts, personal[slug])
    return "\n".join(parts).rstrip() + "\n"


def _append_rule_bullets(parts: list[str], rows: Iterable[Any]) -> None:
    for row in rows:
        parts.append(f"- {_field(row, 0, 'statement')}")
        quote = _field(row, 1, "human_quote")
        if quote is not None:
            parts.append(f"  > {quote}")


def filter_table_names(table_names: Iterable[str]) -> list[str]:
    """Return exportable table names in deterministic order."""
    return sorted(name for name in table_names if name not in EXCLUDED_TABLES)


def atomic_swap_tree(fresh_tree: str | os.PathLike[str], target: str | os.PathLike[str]) -> None:
    """Swap a complete directory tree into place, restoring the old one on error."""
    fresh = Path(fresh_tree)
    destination = Path(target)
    destination.parent.mkdir(parents=True, exist_ok=True)
    previous = destination.with_name(destination.name + ".prev")

    if previous.exists():
        _remove_tree(previous)
    moved_old = False
    try:
        if destination.exists():
            destination.rename(previous)
            moved_old = True
        fresh.rename(destination)
    except BaseException:
        if moved_old and not destination.exists() and previous.exists():
            previous.rename(destination)
        raise
    else:
        if moved_old:
            _remove_tree(previous)


def build_and_swap(
    target: str | os.PathLike[str], builder: Callable[[Path], Any]
) -> Any:
    """Build in a sibling temporary directory and publish only after success."""
    destination = Path(target)
    destination.parent.mkdir(parents=True, exist_ok=True)
    fresh = Path(tempfile.mkdtemp(prefix=f".{destination.name}.tmp-", dir=destination.parent))
    try:
        result = builder(fresh)
        atomic_swap_tree(fresh, destination)
        return result
    except BaseException:
        if fresh.exists():
            _remove_tree(fresh)
        raise


def _remove_tree(path: Path) -> None:
    if path.is_dir() and not path.is_symlink():
        shutil.rmtree(path)
    else:
        path.unlink()


def copy_and_swap(source: str | os.PathLike[str], target: str | os.PathLike[str]) -> None:
    """Atomically publish a copy of an already-complete mirror tree."""
    source_path = Path(source)

    def copy_builder(fresh: Path) -> None:
        shutil.copytree(source_path, fresh, dirs_exist_ok=True)

    build_and_swap(target, copy_builder)


def enumerate_base_tables(connection: Any) -> list[str]:
    with connection.cursor() as cursor:
        cursor.execute(
            """
            SELECT table_name
              FROM information_schema.tables
             WHERE table_schema = 'public'
               AND table_type = 'BASE TABLE'
             ORDER BY table_name
            """
        )
        return filter_table_names(row[0] for row in cursor.fetchall())


def write_table_csv(connection: Any, table_name: str, output: Path) -> int:
    """Write one public table to CSV and return its row count."""
    from psycopg import sql

    with connection.cursor() as cursor:
        cursor.execute(
            """
            SELECT column_name
              FROM information_schema.columns
             WHERE table_schema = 'public' AND table_name = %s
             ORDER BY ordinal_position
            """,
            (table_name,),
        )
        columns = [row[0] for row in cursor.fetchall()]
        if not columns:
            raise RuntimeError(f"table metadata unavailable for {table_name}")

        query = sql.SQL("SELECT {} FROM {}.{} ORDER BY {}").format(
            sql.SQL(", ").join(map(sql.Identifier, columns)),
            sql.Identifier("public"),
            sql.Identifier(table_name),
            sql.Identifier(columns[0]),
        )
        cursor.execute(query)
        with output.open("w", encoding="utf-8", newline="") as stream:
            writer = csv.writer(stream, lineterminator="\n")
            writer.writerow(columns)
            count = 0
            while batch := cursor.fetchmany(1000):
                for row in batch:
                    writer.writerow(serialize_cell(value) for value in row)
                    count += 1
    return count


def export_tables(
    connection: Any,
    output_dir: Path,
    table_names: Iterable[str] | None = None,
    writer: Callable[[Any, str, Path], int] = write_table_csv,
) -> tuple[int, int]:
    """Export allowed tables, returning (table count, total row count)."""
    names = filter_table_names(
        enumerate_base_tables(connection) if table_names is None else table_names
    )
    output_dir.mkdir(parents=True, exist_ok=True)
    rows = sum(writer(connection, name, output_dir / f"{name}.csv") for name in names)
    return len(names), rows


def export_documents(connection: Any, root: Path, timestamp: str) -> int:
    with connection.cursor() as cursor:
        cursor.execute(
            """
            SELECT id, slug, title, content_class, visibility
              FROM doctrine_document
             ORDER BY id
            """
        )
        documents = cursor.fetchall()
        for document in documents:
            cursor.execute(
                """
                SELECT s.document_id, s.section_key, s.title, s.ordinal,
                       s.status, s.current_revision_id, r.plain_text
                  FROM doctrine_section AS s
                  JOIN doctrine_revision AS r ON r.id = s.current_revision_id
                 WHERE s.document_id = %s AND s.status = 'active'
                 ORDER BY s.ordinal
                """,
                (document[0],),
            )
            content = render_document_markdown(document, cursor.fetchall(), timestamp)
            path = root / "md" / str(document[3]) / f"{document[1]}.md"
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(content, encoding="utf-8")
    return len(documents)


def export_rules(connection: Any, root: Path, timestamp: str) -> None:
    with connection.cursor() as cursor:
        cursor.execute(
            """
            SELECT statement, human_quote, taught_by, personal_to, scope, id
              FROM v_compiled_rules
             ORDER BY id
            """
        )
        content = render_rules_markdown(cursor.fetchall(), timestamp)
    path = root / "md" / "rules.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def verify_written_mirror(root: Path) -> list[str]:
    """Re-read the mirror FROM DISK and check it against its own manifest.

    WHY THE PRODUCER NOW CHECKS ITS OWN WORK (2026-08-22). Until the Drive vault
    was retired, this verification lived in ops/vault-drift-watch.py: the watch
    loaded Backups/portability-mirror/manifest.json and hashed every mirror file
    against it, so a divergent hash, an extra file or a missing one all read as
    drift rather than being trusted wholesale (that was the point of #64 — verify
    the mirror instead of blanket-trusting it).

    That check was keyed to the vault path. When the mirror stopped writing there
    it had nothing left to read, and when the mirror was repointed to OneDrive on
    2026-08-22 the producer came back without its checker. Restoring the check
    HERE rather than in a path-scanning watch makes it independent of where the
    mirror lands, which is the property the last two months kept breaking.

    IT IS NOT SELF-CONGRATULATION. The manifest records hashes computed from the
    bytes this process wrote; this reads the files BACK OFF THE DESTINATION and
    hashes them again. That difference is the whole value at the new location: a
    cloud-synced folder can dehydrate a file to a placeholder, truncate a partial
    upload, or drop one, and every one of those still leaves a path that exists.
    The one moment this mirror matters is when the database cannot be reached to
    compare it against, so it has to be checked when it is written or not at all.
    """
    manifest_path = root / "manifest.json"
    if not manifest_path.is_file():
        return ["manifest.json is missing from the written mirror"]
    recorded = json.loads(manifest_path.read_text(encoding="utf-8"))
    claimed = recorded.get("files") if isinstance(recorded, dict) else None
    if not isinstance(claimed, dict) or not claimed:
        return ["manifest.json records no files"]

    actual = compute_file_hashes(root)
    # manifest.json cannot vouch for its own hash; it is written after the
    # others and is excluded on both sides rather than special-cased on one.
    actual.pop("manifest.json", None)
    claimed = {k: v for k, v in claimed.items() if k != "manifest.json"}

    problems = []
    for rel, digest in sorted(claimed.items()):
        if rel not in actual:
            problems.append(f"missing from the destination: {rel}")
        elif actual[rel] != digest:
            problems.append(f"content does not match the manifest: {rel}")
    for rel in sorted(set(actual) - set(claimed)):
        problems.append(f"present but unrecorded: {rel}")
    return problems



def mirror_role(connection: Any) -> str:
    """The database role this mirror was taken as.

    WHY A BACKUP HAS TO NAME ITS READER (2026-08-22). export_tables discovers
    what to copy from information_schema.tables, and that view is ROLE-FILTERED:
    a role that cannot see a table is not told the table exists. So the mirror's
    coverage is a property of the credential, not of the database, and until now
    the manifest recorded only a count. Measured on the live database the same
    day: the exporter credential sees 36 public base tables and the jobs
    credential sees 24. A mirror taken with the narrower one would have written
    "Table count: 24" and looked exactly as finished as a complete mirror.

    That is the worst place for a silent shrink. This file is the copy a person
    reaches for when Postgres is unreachable and the record layer cannot answer,
    so the one moment its completeness matters is the moment nobody can check it
    against the source. Naming the reader does not make the mirror complete; it
    makes an incomplete one legible, and it lets two mirrors be compared without
    the database being up.
    """
    with connection.cursor() as cursor:
        cursor.execute("select current_user")
        row = cursor.fetchone()
    return str(row[0]) if row else "unknown"


def render_manifest(timestamp: str, doc_count: int, table_count: int, row_count: int,
                    role: str = "unknown") -> str:
    return "\n".join(
        (
            snapshot_banner(timestamp),
            "",
            "# Portability Mirror Manifest",
            "",
            f"UTC timestamp: {timestamp}",
            f"Taken as database role: {role}",
            f"Document count: {doc_count}",
            f"Table count: {table_count}",
            f"Total row count: {row_count}",
            "",
            "Table coverage follows the reading role: information_schema.tables",
            "hides what the role cannot see, so a mirror taken as a narrower role",
            "is smaller and says so only here. Compare this line before trusting",
            "a table count against an older mirror.",
            "",
            MANIFEST_WARNING,
            "",
        )
    )


def compute_file_hashes(root: Path) -> dict[str, str]:
    """Return {relpath: sha256} for every regular file currently under root.

    relpath is POSIX-style (forward slashes) and relative to root, matching
    the schema ops/vault-drift-watch.py expects when it strips the vault's
    Backups/portability-mirror/ prefix and looks up what remains.
    """
    hashes: dict[str, str] = {}
    for path in sorted(root.rglob("*")):
        if not path.is_file():
            continue
        relpath = path.relative_to(root).as_posix()
        hashes[relpath] = _hash_one_file(path)
    return hashes


# The same three errnos exporters/common.py retries, and for the same reason:
# one definition of "the cloud provider cannot serve this right now", two places
# that must agree about it.
_TRANSIENT_ERRNOS = frozenset({errno.EAGAIN, errno.EDEADLK, errno.ETIMEDOUT})
_HASH_RETRY_BACKOFF_SECONDS = (0.5, 1.0, 2.0, 5.0, 15.0)


def _hash_one_file(path: Path) -> str:
    """sha256 one mirror file, retrying only a transient cloud-provider refusal.

    THE MIRROR WRITES INTO ONEDRIVE AND THEN READS ITSELF BACK, which is the
    step that failed on 2026-09-17: `OSError: [Errno 11] Resource deadlock
    avoided` out of this loop, thirty seconds into the manifest. The files are
    freshly written, so this is not Files On-Demand evicting them — it is the
    File Provider still coming up ten minutes into a scheduled 01:55 dark wake,
    refusing reads it will happily serve minutes later. Re-running the identical
    mirror by hand at 06:08 the same morning wrote all 313 files clean.

    A manifest that cannot vouch for every file it claims is worthless, so this
    retries rather than skipping the file or writing a partial manifest. Only
    the transient errnos: a permission or path failure escapes on the first
    attempt, where it belongs.
    """
    for attempt in range(len(_HASH_RETRY_BACKOFF_SECONDS) + 1):
        digest = hashlib.sha256()
        try:
            with path.open("rb") as stream:
                for chunk in iter(lambda: stream.read(1 << 20), b""):
                    digest.update(chunk)
            return digest.hexdigest()
        except OSError as error:
            if error.errno not in _TRANSIENT_ERRNOS:
                raise
            if attempt == len(_HASH_RETRY_BACKOFF_SECONDS):
                raise
            time.sleep(_HASH_RETRY_BACKOFF_SECONDS[attempt])
    raise AssertionError("unreachable: the loop returns or raises")


def render_output_manifest(timestamp: str, files: Mapping[str, str]) -> str:
    """Render manifest.json: the machine-readable sibling to MANIFEST.md.

    This is what ops/vault-drift-watch.py's nightly UNEXPECTED alarm verifies
    the mirror against, so a sanctioned regeneration (bytes match what this
    pipeline itself just wrote) stops tripping the alarm while a foreign
    writer parking content in the mirror — divergent bytes, or a path this
    manifest never claimed — still does. sort_keys=True makes the output
    byte-stable so a real content change is the only thing that moves it.
    """
    payload = {
        "schema": 1,
        "generated_at": timestamp,
        "file_count": len(files),
        "files": dict(files),
    }
    return json.dumps(payload, indent=2, sort_keys=True) + "\n"


def write_output_manifest(root: Path, timestamp: str) -> Path:
    """Write root/manifest.json, hashing every file this run has produced so far.

    Must run LAST, after MANIFEST.md (and everything else), so manifest.json
    can vouch for MANIFEST.md too — otherwise the human-readable manifest
    would itself land in the mirror with no verifiable entry. manifest.json
    necessarily cannot vouch for its own bytes (they do not exist yet at the
    moment they are computed); callers that need to tell "no entry because
    this file is metadata" from "no entry because something foreign added
    this file" already know manifest.json's own path and can skip it by name.
    """
    files = compute_file_hashes(root)
    path = root / "manifest.json"
    path.write_text(render_output_manifest(timestamp, files), encoding="utf-8")
    return path


def build_mirror(connection: Any, root: Path, timestamp: str) -> tuple[int, int, int]:
    doc_count = export_documents(connection, root, timestamp)
    export_rules(connection, root, timestamp)
    table_count, row_count = export_tables(connection, root / "csv")
    manifest = render_manifest(timestamp, doc_count, table_count, row_count,
                               mirror_role(connection))
    (root / "MANIFEST.md").write_text(manifest, encoding="utf-8")
    write_output_manifest(root, timestamp)
    return doc_count, table_count, row_count


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", required=True, type=Path, help="target mirror directory")
    parser.add_argument("--also", type=Path, help="optional second mirror directory")
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    database_url = os.environ.get("DATABASE_URL")
    if not database_url:
        # 78 = EX_CONFIG, THE HOUSE SKIP CONTRACT, and the same one
        # bin/backup-dump.sh line 57 already implements for this exact
        # credential. bin/nightly.sh lines 143-151 spell out why it matters:
        # exit 78 means "the step ran, found a setting it needs is absent,
        # wrote nothing and said so", which is NOT a failed night, while any
        # other nonzero code records a FAIL.
        #
        # THIS WAS EXIT 2 UNTIL 2026-08-20 AND NOBODY SAW IT, because the
        # chain used to die at the backup step a few lines earlier and never
        # reached this one. Fixing that abort made this step run for the
        # first time in days — and it would have recorded a hard failure
        # every single night that CARR_DB_BACKUP_URL stays absent from
        # ~/.config/carr/db.env. An alarm that fires nightly over a known
        # missing credential trains people to stop reading alarms, which is
        # the failure the SKIP convention exists to prevent.
        #
        # The step still prints its reason and still shows in the log, so a
        # missing mirror stays visible without being counted as a breakage.
        print("mirror skipped: DATABASE_URL is not configured", file=sys.stderr)
        return 78

    try:
        import psycopg

        timestamp = utc_timestamp()

        def builder(fresh: Path) -> tuple[int, int, int]:
            with psycopg.connect(database_url) as connection:
                with connection.transaction():
                    return build_mirror(connection, fresh, timestamp)

        doc_count, table_count, row_count = build_and_swap(args.out, builder)
        if args.also is not None:
            copy_and_swap(args.out, args.also)
        # Both copies are checked, because the second one is the one nobody
        # looks at until the first is gone.
        for written in [args.out] + ([args.also] if args.also is not None else []):
            problems = verify_written_mirror(written)
            if problems:
                print(f"mirror VERIFY FAILED at {written}", file=sys.stderr)
                for problem in problems[:20]:
                    print(f"  {problem}", file=sys.stderr)
                if len(problems) > 20:
                    print(f"  ...and {len(problems) - 20} more", file=sys.stderr)
                return 1
    except Exception as error:
        # The type name alone is not a diagnosis. "mirror failed: TypeError"
        # is what the 2026-08-17 nightly chain printed, and finding the actual
        # cause meant re-running the pipeline by hand under a traceback
        # wrapper. The message names the value; the traceback names the row.
        print(f"mirror failed: {type(error).__name__}: {error}", file=sys.stderr)
        traceback.print_exc(file=sys.stderr)
        return 1

    print(
        f"mirror OK (verified on disk): {doc_count} md docs, {table_count} tables, "
        f"{row_count} rows -> {args.out}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
