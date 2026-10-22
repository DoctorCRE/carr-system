#!/usr/bin/env python3
"""Fixture tests for the evidence-only table usage report."""
import importlib.util
from pathlib import Path
import unittest
import tempfile

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("table_usage_census", ROOT / "ops/table-usage-census.py")
assert spec is not None and spec.loader is not None
census = importlib.util.module_from_spec(spec)
spec.loader.exec_module(census)


def table(name, rows=0, inserts=0, size=8192):
    return dict(schema="public", name=name, estimated_rows=rows, inserts=inserts,
                updates=0, deletes=0, total_bytes=size, stats_reset="2026-01-01T00:00:00Z")


class ReportTests(unittest.TestCase):
    def test_classification_preserves_unknowns_and_reset_caveat(self):
        report = census.build_report(
            [table("busy", inserts=2), table("populated", rows=4),
             table("empty"), table("unknown", rows=-1)],
            references={}, unresolved=[], backup_tables={"public.busy"})
        rows = {row["table"]: row for row in report["tables"]}
        self.assertEqual(report["counts"], {"active": 1, "quiet": 2, "empty-and-unreferenced": 1})
        self.assertTrue(rows["public.busy"]["in_backup_census"])
        self.assertFalse(rows["public.empty"]["in_backup_census"])
        self.assertEqual(rows["public.unknown"]["estimated_rows"], -1)
        self.assertIn("zero", report["stats_reset_caveat"].lower())
        self.assertIn("reset", report["stats_reset_caveat"].lower())
        self.assertTrue(all(row["classification_reason"] for row in rows.values()))

    def test_uncertainty_and_references_prevent_empty_classification(self):
        for refs, unresolved in [({"public.empty": [{"kind": "static"}]}, []),
                                 ({}, [{"kind": "dynamic_sql", "source": "ops/run.py"}])]:
            report = census.build_report([table("empty")], references=refs,
                                         unresolved=unresolved, backup_tables=set())
            self.assertEqual(report["counts"]["quiet"], 1)
        missing = table("missing")
        missing["inserts"] = None
        self.assertEqual(census.build_report([missing], references={}, unresolved=[],
                                            backup_tables=set())["counts"]["quiet"], 1)

    def test_size_summary_is_bounded_and_descending(self):
        report = census.build_report([table(f"t{i}", size=i * 100) for i in range(30)],
                                     references={}, unresolved=[], backup_tables=set())
        self.assertEqual(len(report["top_25_by_size"]), 25)
        self.assertEqual(report["top_25_by_size"][0]["table"], "public.t29")
        self.assertIn("Top 25", census.render_summary(report))


class ReferenceTests(unittest.TestCase):
    def test_inventory_and_static_references_have_locations_and_symbols(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "ops").mkdir()
            path = root / "ops/read.py"
            path.write_text('def read():\n    return "select * from public.widget"\n')
            refs, unresolved = census.scan_references(
                root, [table("widget"), table("widget_extra")], ["ops/read.py"],
                [{"operation": "save-widget", "source_locator": "mcp-server/src/widget.js",
                  "tables_written": ["public.widget"]}], [])
            self.assertEqual(unresolved, [])
            self.assertEqual(refs["public.widget"][0]["operation"], "save-widget")
            self.assertEqual(refs["public.widget"][1]["symbol"], "read")
            self.assertEqual(refs["public.widget"][1]["line"], 2)
            self.assertNotIn("public.widget_extra", refs)

    def test_dynamic_sql_is_unresolved_and_secret_paths_are_never_read(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "ops").mkdir()
            (root / "ops/run.py").write_text('def run():\n    sql = f"select * from {table}"\n')
            refs, unresolved = census.scan_references(
                root, [table("widget")], ["ops/run.py", "ops/.env", "ops/credentials.py",
                                         "ops/database-dump.sql", "ops/private-key.py"], [],
                [{"signature": "ops.run_dynamic()", "definition": "begin EXECUTE query; end"}])
            self.assertEqual(refs, {})
            self.assertEqual({item["kind"] for item in unresolved}, {"dynamic_sql", "excluded_sensitive_path"})
            report = census.build_report([table("widget")], references=refs,
                                         unresolved=unresolved, backup_tables=set())
            self.assertEqual(report["tables"][0]["classification"], "quiet")

    def test_quoted_and_multiline_execute_prevent_false_empty_classification(self):
        for expression in ("EXECUTE 'SELECT * FROM public.' || quote_ident(table_name);",
                           "EXECUTE\n    query;"):
            for installed in (False, True):
                with self.subTest(expression=expression, installed=installed):
                    with tempfile.TemporaryDirectory() as tmp:
                        root = Path(tmp)
                        source = "ops/run.sql"
                        (root / "ops").mkdir()
                        (root / source).write_text("begin\n" + expression + "\nend")
                        functions = [{"signature": "ops.run_dynamic()",
                                      "definition": "begin\n" + expression + "\nend"}]
                        refs, unresolved = census.scan_references(
                            root, [table("widget")], [] if installed else [source], [],
                            functions if installed else [])
                        self.assertEqual(unresolved, [{
                            "kind": "dynamic_sql", "source": "pg_proc" if installed else source,
                            "line": 2, **({"function": "ops.run_dynamic()"} if installed else {})}])
                        report = census.build_report([table("widget")], references=refs,
                                                     unresolved=unresolved, backup_tables=set())
                        self.assertEqual(report["counts"]["empty-and-unreferenced"], 0)

    def test_installed_functions_and_same_file_verbs_are_labeled_evidence(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "ops").mkdir()
            (root / "ops/read.js").write_text('async function read() { return "select * from widget"; }\n')
            refs, _ = census.scan_references(
                root, [table("widget")], ["ops/read.js"],
                [{"operation": "read-widget", "source_locator": "ops/read.js"}],
                [{"signature": "public.get_widget()", "definition": "select * from widget"}])
            self.assertTrue(any(item.get("function") == "public.get_widget()" for item in refs["public.widget"]))
            self.assertTrue(any(item.get("candidate_verbs") == ["read-widget"] for item in refs["public.widget"]))

    def test_malformed_inventory_and_interpolated_identifiers_are_unresolved(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "ops").mkdir()
            (root / "ops/run.sh").write_text('query="select * from $TABLE"\n')
            _, unresolved = census.scan_references(
                root, [table("widget")], ["ops/run.sh"],
                [{"operation": "unknown", "tables_written": "public.widget"}], [])
            self.assertEqual({item["kind"] for item in unresolved},
                             {"inventory_table_unresolved", "dynamic_sql"})


if __name__ == "__main__":
    unittest.main()
