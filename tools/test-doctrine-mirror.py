# Test the doctrine mirror: file hashing, excluded tables and atomic tree swaps.
import hashlib
import json
import tempfile
from datetime import date, datetime, timezone
from pathlib import Path
from uuid import UUID

import pytest

import sys, os; sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "pipelines"))
from doctrine_mirror import (
    EXCLUDED_TABLES,
    atomic_swap_tree,
    build_and_swap,
    compute_file_hashes,
    export_tables,
    render_document_markdown,
    render_output_manifest,
    render_rules_markdown,
    serialize_cell,
    write_output_manifest,
)


STAMP = "2026-08-08T12:34:56Z"


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (None, ""),
        ({"z": [2, {"b": False, "a": None}], "a": 1}, '{"a": 1, "z": [2, {"a": null, "b": false}]}'),
        (datetime(2026, 8, 8, 7, 6, 5, tzinfo=timezone.utc), "2026-08-08T07:06:05+00:00"),
        (date(2026, 8, 8), "2026-08-08"),
        (True, "true"),
        (False, "false"),
        (b"\x00\xaf\xff", "00afff"),
        (memoryview(b"\x01\x10"), "0110"),
        # A uuid[] column arrives as a list of UUID objects, which json.dumps
        # refuses by default. On 2026-08-17 that TypeError took the whole
        # portability mirror down — the readable escape hatch stopped
        # refreshing because three tables (experiment.piece_ids,
        # retrieval_proposal.resulting_row_ids, retrieval_query_log
        # .selected_row_ids) now carry uuid arrays. Every scalar branch above
        # already stringifies its type; the container branch must do the same
        # for whatever it holds.
        (
            [UUID("00000000-0000-4000-8000-000000000001")],
            '["00000000-0000-4000-8000-000000000001"]',
        ),
        (
            {"at": datetime(2026, 8, 8, 7, 6, 5, tzinfo=timezone.utc)},
            '{"at": "2026-08-08T07:06:05+00:00"}',
        ),
        (UUID("00000000-0000-4000-8000-000000000002"),
         "00000000-0000-4000-8000-000000000002"),
    ],
)
def test_serialize_cell(value, expected):
    assert serialize_cell(value) == expected


def test_document_markdown_has_banner_orders_and_filters_sections():
    document = (7, "portable-doctrine", "Portable Doctrine", "policy", "public")
    sections = [
        (7, "later", "Later", 20, "active", 102, "second body"),
        (7, "retired", "Old", 5, "retired", 100, "must not appear"),
        (7, "first", None, 10, "active", 101, "first body"),
    ]

    result = render_document_markdown(document, sections, STAMP)

    assert result.startswith("<!-- GENERATED SNAPSHOT — DO NOT EDIT\n")
    assert f"UTC timestamp: {STAMP}" in result
    assert "pipelines/doctrine_import.py" in result
    assert "# Portable Doctrine" in result
    assert result.index("## first") < result.index("## Later")
    assert "must not appear" not in result


def test_rules_markdown_groups_shared_and_personal_and_skips_intro():
    rows = [
        ("Shared statement", "Shared quote", "teacher", None, {"kind": "rule"}, 1),
        ("Bob rule", None, "teacher", "bob", {}, 2),
        ("Intro secret", "skip", "teacher", None, {"kind": "intro_politics"}, 3),
        ("Alice rule", "Alice quote", "teacher", "alice", {}, 4),
    ]

    result = render_rules_markdown(rows, STAMP)

    assert result.index("## SHARED") < result.index("## PERSONAL — alice")
    assert result.index("## PERSONAL — alice") < result.index("## PERSONAL — bob")
    assert "- Shared statement\n  > Shared quote" in result
    assert "- Alice rule\n  > Alice quote" in result
    assert "Intro secret" not in result


def test_atomic_swap_replaces_entire_prior_tree(tmp_path: Path):
    target = tmp_path / "mirror"
    target.mkdir()
    (target / "obsolete.txt").write_text("old", encoding="utf-8")
    fresh = tmp_path / "fresh"
    (fresh / "nested").mkdir(parents=True)
    (fresh / "nested" / "current.txt").write_text("new", encoding="utf-8")

    atomic_swap_tree(fresh, target)

    assert (target / "nested" / "current.txt").read_text(encoding="utf-8") == "new"
    assert not (target / "obsolete.txt").exists()
    assert not list(tmp_path.glob("mirror.prev*"))


@pytest.mark.parametrize("stale_previous", [False, True])
def test_provider_cleanup_refusal_does_not_block_publication(
    tmp_path: Path, monkeypatch, capsys, stale_previous
):
    import errno
    import shutil

    target = tmp_path / "mirror"
    target.mkdir()
    (target / "snapshot.txt").write_text("old")
    if stale_previous:
        previous = tmp_path / "mirror.prev"
        previous.mkdir()
        (previous / "snapshot.txt").write_text("older")
    remove = shutil.rmtree

    def provider_refuses(path, *args, **kwargs):
        if Path(path).name.startswith("mirror.prev"):
            raise OSError(errno.EDEADLK, "Resource deadlock avoided", str(path))
        return remove(path, *args, **kwargs)

    with monkeypatch.context() as provider:
        provider.setattr(shutil, "rmtree", provider_refuses)
        build_and_swap(target, lambda fresh: (fresh / "snapshot.txt").write_text("new"))

    assert (target / "snapshot.txt").read_text() == "new"
    assert "cleanup deferred" in capsys.readouterr().err
    build_and_swap(target, lambda fresh: (fresh / "snapshot.txt").write_text("newest"))
    assert (target / "snapshot.txt").read_text() == "newest"
    assert not list(tmp_path.glob("mirror.prev*"))


def test_publication_rename_failure_restores_original(tmp_path: Path, monkeypatch):
    import errno

    target = tmp_path / "mirror"
    target.mkdir()
    (target / "snapshot.txt").write_text("safe")
    rename = Path.rename

    def refuse_publication(path, destination):
        if path.name.startswith(".mirror.tmp-"):
            raise OSError(errno.EDEADLK, "publication refused")
        return rename(path, destination)

    monkeypatch.setattr(Path, "rename", refuse_publication)
    with pytest.raises(OSError, match="publication refused"):
        build_and_swap(target, lambda fresh: (fresh / "snapshot.txt").write_text("new"))
    assert (target / "snapshot.txt").read_text() == "safe"


def test_builder_crash_leaves_original_target_intact(tmp_path: Path):
    target = tmp_path / "mirror"
    target.mkdir()
    (target / "original.txt").write_text("safe", encoding="utf-8")

    def crashing_builder(fresh: Path):
        (fresh / "partial.txt").write_text("partial", encoding="utf-8")
        raise RuntimeError("simulated crash")

    with pytest.raises(RuntimeError, match="simulated crash"):
        build_and_swap(target, crashing_builder)

    assert list(target.iterdir()) == [target / "original.txt"]
    assert (target / "original.txt").read_text(encoding="utf-8") == "safe"


def test_excluded_tables_never_reach_writer(tmp_path: Path):
    seen = []

    def fake_writer(connection, table_name, output):
        seen.append((table_name, output.name))
        return 3

    table_count, row_count = export_tables(
        object(),
        tmp_path / "csv",
        ["z_table", "sensitive_blob", "tool_call", "a_table"],
        fake_writer,
    )

    assert EXCLUDED_TABLES == {"sensitive_blob", "tool_call"}
    assert seen == [("a_table", "a_table.csv"), ("z_table", "z_table.csv")]
    assert (table_count, row_count) == (2, 6)


# ---------------------------------------------------------------------------
# manifest.json — the machine-readable sibling to MANIFEST.md that
# ops/vault-drift-watch.py verifies mirror regeneration against, so its
# nightly UNEXPECTED alarm can tell a sanctioned re-export (hash matches what
# the pipeline itself just wrote) from a foreign writer parking content in the
# mirror (hash diverges, or the file carries no manifest entry at all).


def test_compute_file_hashes_walks_every_file_with_posix_relpaths(tmp_path: Path):
    (tmp_path / "md" / "policy").mkdir(parents=True)
    (tmp_path / "md" / "policy" / "a.md").write_text("alpha", encoding="utf-8")
    (tmp_path / "csv").mkdir()
    (tmp_path / "csv" / "table.csv").write_text("h1,h2\n1,2\n", encoding="utf-8")
    (tmp_path / "MANIFEST.md").write_text("# manifest\n", encoding="utf-8")

    hashes = compute_file_hashes(tmp_path)

    assert set(hashes) == {"md/policy/a.md", "csv/table.csv", "MANIFEST.md"}
    assert hashes["md/policy/a.md"] == hashlib.sha256(b"alpha").hexdigest()
    assert hashes["csv/table.csv"] == hashlib.sha256(b"h1,h2\n1,2\n").hexdigest()
    assert hashes["MANIFEST.md"] == hashlib.sha256(b"# manifest\n").hexdigest()


def test_render_output_manifest_is_stable_json_with_schema_and_count():
    files = {"z.md": "deadbeef", "a.md": "beadfeed"}

    text = render_output_manifest(STAMP, files)
    data = json.loads(text)

    assert data == {
        "schema": 1,
        "generated_at": STAMP,
        "file_count": 2,
        "files": {"z.md": "deadbeef", "a.md": "beadfeed"},
    }
    # sort_keys=True in the writer -> deterministic byte-for-byte output,
    # which matters because this file is itself hashed and diffed by callers.
    assert text.index('"a.md"') < text.index('"z.md"')


def test_write_output_manifest_covers_every_prior_output_including_manifest_md(tmp_path: Path):
    (tmp_path / "md" / "policy").mkdir(parents=True)
    (tmp_path / "md" / "policy" / "a.md").write_text("alpha", encoding="utf-8")
    (tmp_path / "MANIFEST.md").write_text("# manifest\n", encoding="utf-8")

    out_path = write_output_manifest(tmp_path, STAMP)

    assert out_path == tmp_path / "manifest.json"
    data = json.loads(out_path.read_text(encoding="utf-8"))
    assert data["generated_at"] == STAMP
    assert data["file_count"] == 2
    assert data["files"]["md/policy/a.md"] == hashlib.sha256(b"alpha").hexdigest()
    assert data["files"]["MANIFEST.md"] == hashlib.sha256(b"# manifest\n").hexdigest()
    # manifest.json cannot list its own hash (it does not exist yet at the
    # moment its own contents are computed) — that must never regress into a
    # self-referential entry.
    assert "manifest.json" not in data["files"]


def test_cleanup_permission_error_remains_a_failure(tmp_path: Path, monkeypatch):
    import errno
    import shutil

    target = tmp_path / "mirror"
    target.mkdir()
    (target / "snapshot.txt").write_text("old")

    def denied(path, *args, **kwargs):
        raise PermissionError(errno.EACCES, "cleanup permission denied", str(path))

    monkeypatch.setattr(shutil, "rmtree", denied)
    with pytest.raises(PermissionError, match="cleanup permission denied"):
        build_and_swap(target, lambda fresh: (fresh / "snapshot.txt").write_text("new"))
    assert (target / "snapshot.txt").read_text() == "new"


def test_cleanup_does_not_remove_unrelated_previous_paths(tmp_path: Path):
    unrelated = tmp_path / "mirror.prev-not-owned"
    unrelated.mkdir()
    (unrelated / "evidence.txt").write_text("keep")
    target = tmp_path / "mirror"

    build_and_swap(target, lambda fresh: (fresh / "snapshot.txt").write_text("new"))

    assert (unrelated / "evidence.txt").read_text() == "keep"
    assert (target / "snapshot.txt").read_text() == "new"


if __name__ == "__main__":
    with tempfile.TemporaryDirectory() as directory:
        result = pytest.main([__file__, f"--basetemp={directory}/pytest"])
    raise SystemExit(result)
