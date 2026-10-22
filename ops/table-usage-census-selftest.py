#!/usr/bin/env python3
"""Fixture tests for the evidence-only table usage report."""
import importlib.util
from pathlib import Path
import unittest
import tempfile
import json
import subprocess
import sys
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("table_usage_census", ROOT / "ops/table-usage-census.py")
assert spec is not None and spec.loader is not None
census = importlib.util.module_from_spec(spec)
spec.loader.exec_module(census)


def table(name, rows=0, inserts=0, size=8192):
    return dict(schema="public", name=name, estimated_rows=rows, inserts=inserts,
                updates=0, deletes=0, total_bytes=size, stats_reset="2026-01-01T00:00:00Z")


class ReportTests(unittest.TestCase):
    def test_per_table_uncertainty_and_generated_deletion_proposal(self):
        report = census.build_report(
            [table('used'), table('empty'), table('uncertain'), table('populated', rows=3)],
            references={'public.used': [{'kind': 'dynamic_sql_bounded', 'source': 'ops/read.py', 'line': 2}]},
            unresolved=[{'kind': 'dynamic_sql', 'source': 'ops/other.py', 'line': 5,
                         'possible_tables': ['public.uncertain'], 'reason': 'Unresolved identifier'}],
            backup_tables={'public.empty'})
        rows = {row['table']: row for row in report['tables']}
        self.assertEqual(report['counts'], {'referenced': 1, 'unreferenced-and-empty': 1, 'unknown': 2})
        self.assertEqual(rows['public.empty']['uncertainty'], [])
        self.assertEqual(report['unresolved'][rows['public.uncertain']['uncertainty'][0]]['source'], 'ops/other.py')
        self.assertEqual([row['table'] for row in report['proposed_deletions']], ['public.empty'])
        self.assertEqual(report['proposed_deletions'][0]['approval'], 'required_from_joe')
        self.assertIn('Joe', report['next_step'])
        self.assertNotIn('DROP TABLE', census.render_summary(report))

    def test_source_only_cli_never_imports_a_database_client_or_reads_credentials(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root/'migrations').mkdir()
            (root/'ops').mkdir()
            (root/'migrations/0001_fixture.sql').write_text(
                'CREATE TABLE public.used(id int); CREATE TABLE ops.unproven(id int);')
            (root/'ops/read.py').write_text('conn.execute("select * from public.used")')
            def git(command, **kwargs):
                if command[1] == 'ls-files':
                    return subprocess.CompletedProcess(command, 0, b'migrations/0001_fixture.sql\0ops/read.py\0')
                return subprocess.CompletedProcess(command, 0, 'fixture-head\n')
            with patch.object(census, 'ROOT', root), patch.object(census, 'source_inventory', return_value=[]), \
                    patch.object(census.subprocess, 'run', side_effect=git), \
                    patch.dict('sys.modules', {'psycopg': None, 'lib.credential_file': None}):
                self.assertEqual(census.main(['--source-only']), 0)
            report = json.loads((root/'out/table-usage-census.json').read_text())
            self.assertEqual(report['counts'], {'referenced': 1, 'unreferenced-and-empty': 0, 'unknown': 1})
            self.assertIsNone(report['tables'][0]['estimated_rows'])
            self.assertEqual(report['proposed_deletions'], [])
            proposal = json.loads((root/'out/table-proposed-deletions.json').read_text())
            self.assertEqual(proposal['approval'], 'required_from_joe')
            self.assertEqual(proposal['proposed_deletions'], [])
            self.assertIn('Joe', proposal['next_step'])
            self.assertEqual(report['provenance']['catalog_source'], 'migration_declarations_not_live_database')

    def test_real_source_only_with_fake_empty_statistics_has_review_candidates(self):
        run = subprocess.run([sys.executable, ROOT/'ops/table-usage-census.py', '--source-only'],
                             cwd=ROOT, capture_output=True, text=True, timeout=300)
        self.assertEqual(run.returncode, 0, run.stderr)
        source = json.loads((ROOT/'out/table-usage-census.json').read_text())
        tables = [dict(row, estimated_rows=0, inserts=0, updates=0, deletes=0) for row in source['tables']]
        report = census.build_report(tables,
            references={row['table']: row['references'] for row in source['tables']},
            unresolved=list(source['unresolved'].values()), backup_tables=None)
        self.assertGreater(report['counts']['unreferenced-and-empty'], 0, report['counts'])
        rows = {row['table']: row for row in report['tables']}
        protected = {'ops.execution_environment_' + suffix: 'bin/schema-snapshot.sh'
                     for suffix in ('conformance', 'provider', 'provider_event')}
        protected['public.' + 'retrieval_' + 'phrase'] = 'bin/schema-snapshot.sh'
        protected['ops.' + 'scac_reference_monitor_' + 'mode_event'] = 'docs/frontier-finding/gen_breakglass_run.py'
        for name, source_path in protected.items():
            with self.subTest(table=name):
                self.assertEqual(rows[name]['classification'], 'referenced')
                self.assertTrue(any(item['source'] == source_path for item in rows[name]['references']))
        print('Real source with FAKE empty statistics:', report['counts'])

    def test_cli_scans_tracked_text_outside_former_roots_and_skips_generated_files(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            sources = ['bin/read.sh', 'hooks/check.swift', 'control-room/read.mjs',
                       'evals/check.py', 'docs/reference.md', 'shared/reader']
            paths = ['migrations/0001_fixture.sql', 'db/schema.sql', 'vendor/unread.py', 'generated/unread.sql']
            (root/'migrations').mkdir()
            (root/paths[0]).write_text('CREATE TABLE public.used(id int);')
            for source in sources:
                (root/source).parent.mkdir(parents=True, exist_ok=True)
                (root/source).write_text('public.used')
            def git(command, **kwargs):
                if command[1] == 'ls-files':
                    return subprocess.CompletedProcess(command, 0, ('\0'.join(paths + sources) + '\0').encode())
                return subprocess.CompletedProcess(command, 0, 'fixture-head\n')
            with patch.object(census, 'ROOT', root), patch.object(census, 'source_inventory', return_value=[]), \
                    patch.object(census.subprocess, 'run', side_effect=git):
                self.assertEqual(census.main(['--source-only']), 0)
            report = json.loads((root/'out/table-usage-census.json').read_text())
            self.assertEqual({item['source'] for item in report['tables'][0]['references']}, set(sources))

    def test_shared_unknown_evidence_does_not_expand_quadratically(self):
        tables = [table(f'candidate_{i}') for i in range(100)]
        names = [f'public.candidate_{i}' for i in range(100)]
        report = census.build_report(tables, references={},
            unresolved=[{'kind': 'dynamic_sql', 'source': 'ops/generic.py', 'line': 3, 'possible_tables': names}],
            backup_tables=set())
        self.assertEqual(report['counts']['unknown'], 100)
        self.assertLess(len(json.dumps(report)), 100000)
        for row in report['tables']:
            self.assertEqual(report['unresolved'][row['uncertainty'][0]]['source'], 'ops/generic.py')

    def test_classification_preserves_unknowns_and_reset_caveat(self):
        report = census.build_report(
            [table("busy", inserts=2), table("populated", rows=4),
             table("empty"), table("unknown", rows=-1)],
            references={}, unresolved=[], backup_tables={"public.busy"})
        rows = {row["table"]: row for row in report["tables"]}
        self.assertEqual(report["counts"], {"referenced": 1, "unreferenced-and-empty": 1, "unknown": 2})
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
            self.assertEqual(report["tables"][0]["classification"], "referenced" if refs else "unknown")
        missing = table("missing")
        missing["inserts"] = None
        self.assertEqual(census.build_report([missing], references={}, unresolved=[],
                                            backup_tables=set())["counts"]["unknown"], 1)

    def test_size_summary_is_bounded_and_descending(self):
        report = census.build_report([table(f"t{i}", size=i * 100) for i in range(30)],
                                     references={}, unresolved=[], backup_tables=set())
        self.assertEqual(len(report["top_25_by_size"]), 25)
        self.assertEqual(report["top_25_by_size"][0]["table"], "public.t29")
        self.assertIn("Top 25", census.render_summary(report))


class ReferenceTests(unittest.TestCase):
    def test_literal_placeholder_prefix_does_not_scope_uncertainty_to_every_table(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root/'read.js').write_text('db.query("select * from widget_%"); db.query("update }");')
            tables = [table('widget_one'), table('unrelated')]
            refs, unknown = census.scan_references(root, tables, ['read.js'], [], [])
            report = census.build_report(tables, references=refs, unresolved=unknown, backup_tables=set())
            self.assertEqual(report['counts']['unreferenced-and-empty'], 1)
            self.assertTrue(all(item['possible_tables'] == ['public.widget_one'] for item in unknown))

    def test_migration_dependencies_count_but_table_declarations_do_not(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root/'migrations').mkdir()
            source = 'migrations/0001_fixture.sql'
            (root/source).write_text(
                'CREATE TABLE public.unused(id int);\n'
                'CREATE TABLE public.child(id int REFERENCES public.parent(id));\n'
                'CREATE VIEW v AS SELECT * FROM public.viewed;\n'
                'CREATE TRIGGER t BEFORE UPDATE ON public.triggered FOR EACH ROW EXECUTE FUNCTION f();\n'
                'CREATE POLICY p ON public.secured USING (true);\n')
            tables = [table(name) for name in ('unused', 'child', 'parent', 'viewed', 'triggered', 'secured')]
            refs, unknown = census.scan_references(root, tables, [source], [], [])
            report = census.build_report(tables, references=refs, unresolved=unknown, backup_tables=set())
            self.assertEqual({name for name in refs},
                             {'public.parent', 'public.viewed', 'public.triggered', 'public.secured'})
            self.assertEqual(report['counts']['unreferenced-and-empty'], 2)

    def test_opaque_calls_and_unread_sensitive_paths_are_notes_without_table_scope(self):
        fixtures = {
            'lib/read.py': 'self.query(repo, "query { repository { name } }")\nconn.execute(external_sql)',
            'lib/read.js': 'api.query(graphql); db.query(externalSql);',
        }
        for source, text in fixtures.items():
            with self.subTest(source=source), tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                (root/'lib').mkdir()
                (root/source).write_text(text)
                tables = [table('unused')]
                paths = [source, 'lib/credentials.py', 'lib/database-dump.sql']
                refs, notes = census.scan_references(root, tables, paths, [], [])
                report = census.build_report(tables, references=refs, unresolved=notes, backup_tables=set())
                self.assertEqual(report['counts']['unreferenced-and-empty'], 1)
                self.assertTrue(notes)
                self.assertTrue(all(item['possible_tables'] == [] for item in notes))
                self.assertEqual(report['tables'][0]['uncertainty'], [])

    def test_finite_python_table_sources_do_not_taint_unrelated_empty_tables(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root/'ops').mkdir()
            (root/'ops/read.py').write_text(
                'TABLES = ("public.alpha", "ops.beta")\n'
                'for table_name in TABLES:\n'
                '    conn.execute(f"select * from {table_name} where id=%s", (row_id,))\n'
                'conn.execute("select * from public.gamma where id=%s", (row_id,))\n')
            tables = [table('alpha'), dict(table('beta'), schema='ops'), table('gamma'), table('unused')]
            refs, unknown = census.scan_references(root, tables, ['ops/read.py'], [], [])
            report = census.build_report(tables, references=refs, unresolved=unknown, backup_tables=set())
            self.assertEqual(unknown, [])
            self.assertEqual({name for name, evidence in refs.items()
                              if any(item['kind'] == 'dynamic_sql_bounded' for item in evidence)},
                             {'public.alpha', 'ops.beta', 'public.gamma'})
            self.assertEqual([row['table'] for row in report['proposed_deletions']], ['public.unused'])

    def test_javascript_and_sql_bounded_templates_only_reference_their_targets(self):
        fixtures = {
            'ops/read.js': 'const TABLES = ["public.alpha", "ops.beta"];\n'
                           'for (const tableName of TABLES) {\n'
                           '  await c.query(`select * from ${tableName}`);\n}\n',
            'ops/read.sql': "BEGIN EXECUTE format('select * from %I.%I', 'public', 'alpha'); END;",
        }
        for source, text in fixtures.items():
            with self.subTest(source=source), tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                (root/'ops').mkdir()
                (root/source).write_text(text)
                tables = [table('alpha'), dict(table('beta'), schema='ops'), table('unused')]
                refs, unknown = census.scan_references(root, tables, [source], [], [])
                self.assertEqual(unknown, [])
                self.assertTrue(any(item['kind'] == 'dynamic_sql_bounded' for item in refs['public.alpha']))
                report = census.build_report(tables, references=refs, unresolved=unknown, backup_tables=set())
                self.assertIn('public.unused', [row['table'] for row in report['proposed_deletions']])

    def test_unbounded_mutated_or_parameter_sources_are_notes(self):
        fixtures = [
            'TABLES = ["public.alpha"]\nTABLES.append(external_table)\n'
            'for name in TABLES:\n    conn.execute(f"select * from {name}")\n',
            'name = "public.alpha"\ndef read(name):\n    conn.execute(f"select * from {name}")\n',
        ]
        for text in fixtures:
            with self.subTest(text=text), tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                (root/'ops').mkdir()
                (root/'ops/read.py').write_text(text)
                refs, unknown = census.scan_references(root, [table('alpha'), table('other')], ['ops/read.py'], [], [])
                report = census.build_report([table('other')], references=refs, unresolved=unknown, backup_tables=set())
                self.assertEqual(report['tables'][0]['classification'], 'unreferenced-and-empty')
                self.assertNotIn('public.other', refs)
                self.assertEqual([row['table'] for row in report['proposed_deletions']], ['public.other'])
                self.assertTrue(unknown)
                self.assertTrue(all(item['possible_tables'] == [] for item in unknown))

    def test_table_prefix_uncertainty_and_sql_loop_sources(self):
        fixtures = {
            'ops/read.sql': "BEGIN FOREACH target IN ARRAY ARRAY['public.alpha', 'ops.beta_one'] LOOP "
                            "EXECUTE format('SELECT * FROM %s', target); END LOOP; END;",
            'ops/read.py': 'conn.execute(f"select * from ops.beta_{external_suffix}")\n',
        }
        for source, text in fixtures.items():
            with self.subTest(source=source), tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                (root/'ops').mkdir()
                (root/source).write_text(text)
                tables = [table('alpha'), dict(table('beta_one'), schema='ops'), table('unused')]
                refs, unknown = census.scan_references(root, tables, [source], [], [])
                report = census.build_report(tables, references=refs, unresolved=unknown, backup_tables=set())
                if source.endswith('.sql'):
                    self.assertEqual(unknown, [])
                    self.assertTrue(any(item['kind'] == 'dynamic_sql_bounded' for item in refs['ops.beta_one']))
                else:
                    self.assertEqual(unknown[0]['possible_tables'], ['ops.beta_one'])
                    self.assertNotIn('ops.beta_one', refs)
                self.assertIn('public.unused', [row['table'] for row in report['proposed_deletions']])

    def test_unknown_sql_fragment_does_not_infer_unrelated_table_dependencies(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root/'ops').mkdir()
            (root/'ops/read.py').write_text('conn.execute(f"select * from public.alpha {extra_sql}")')
            tables = [table('alpha'), table('other')]
            refs, unknown = census.scan_references(root, tables, ['ops/read.py'], [], [])
            report = census.build_report(tables, references=refs, unresolved=unknown, backup_tables=set())
            self.assertEqual({row['table']: row['classification'] for row in report['tables']},
                             {'public.alpha': 'referenced', 'public.other': 'unreferenced-and-empty'})
            self.assertNotIn('public.other', refs)

    def test_javascript_reassignment_and_postgres_positional_format(self):
        fixtures = {
            'ops/read.js': 'let target = "public.alpha"; target = input; c.query(`select * from ${target}`);',
            'ops/read.sql': "BEGIN EXECUTE format('select * from %2$I.%1$I', 'alpha', 'public'); END;",
        }
        for source, text in fixtures.items():
            with self.subTest(source=source), tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                (root/'ops').mkdir()
                (root/source).write_text(text)
                tables = [table('alpha'), table('other')]
                refs, unknown = census.scan_references(root, tables, [source], [], [])
                if source.endswith('.js'):
                    self.assertTrue(unknown)
                    self.assertTrue(all(item['possible_tables'] == [] for item in unknown))
                    self.assertNotIn('public.other', refs)
                else:
                    self.assertEqual(unknown, [])
                    self.assertTrue(any(item['kind'] == 'dynamic_sql_bounded' for item in refs['public.alpha']))

    def test_collection_mutations_resolve_literals_but_keep_external_values_unknown(self):
        for suffix in ('py', 'js'):
            for external in (False, True):
                with self.subTest(suffix=suffix, external=external), tempfile.TemporaryDirectory() as tmp:
                    root = Path(tmp)
                    (root/'ops').mkdir()
                    value = 'external_table' if external else '"public.beta"'
                    text = ('TABLES = ["public.alpha"]\nTABLES.append(' + value + ')\n'
                            'for target in TABLES:\n    conn.execute(f"select * from {target}")') if suffix == 'py' else (
                            'const TABLES = ["public.alpha"]; TABLES.push(' + value + ');\n'
                            'for (const target of TABLES) { c.query(`select * from ${target}`); }')
                    source = 'ops/read.' + suffix
                    (root/source).write_text(text)
                    tables = [table('alpha'), table('beta'), table('other')]
                    refs, unknown = census.scan_references(root, tables, [source], [], [])
                    report = census.build_report(tables, references=refs, unresolved=unknown, backup_tables=set())
                    rows = {row['table']: row for row in report['tables']}
                    self.assertEqual(rows['public.other']['classification'],
                                     'unreferenced-and-empty')
                    if not external:
                        self.assertTrue(any(item['kind'] == 'dynamic_sql_bounded' for item in refs['public.beta']))

    def test_javascript_opaque_queries_remain_visible_as_notes(self):
        fixtures = [
            'const target = "public.alpha"; const read = target => c.query(`select * from ${target}`);',
            'let i = 2; i--; c.query(query);',
        ]
        for text in fixtures:
            with self.subTest(text=text), tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                (root/'ops').mkdir()
                (root/'ops/read.js').write_text(text)
                refs, unknown = census.scan_references(root, [table('alpha'), table('other')], ['ops/read.js'], [], [])
                report = census.build_report([table('other')], references=refs, unresolved=unknown, backup_tables=set())
                self.assertEqual(report['tables'][0]['classification'], 'unreferenced-and-empty')

    def test_returned_sql_builders_resolve_targets_without_executing_source(self):
        for target in ('"public.alpha"', 'external_table'):
            with self.subTest(target=target), tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                (root/'ops').mkdir()
                (root/'ops/build.py').write_text('def build():\n    return sql.SQL("select * from {}").format(sql.Identifier(' + target + '))')
                tables = [table('alpha'), table('other')]
                refs, unknown = census.scan_references(root, tables, ['ops/build.py'], [], [])
                report = census.build_report(tables, references=refs, unresolved=unknown, backup_tables=set())
                rows = {row['table']: row for row in report['tables']}
                self.assertEqual(rows['public.other']['classification'],
                                 'unreferenced-and-empty')
                if target != 'external_table':
                    self.assertEqual(unknown, [])
                    self.assertTrue(any(item['kind'] == 'dynamic_sql_bounded' for item in refs['public.alpha']))

    def test_parenthesized_unresolvable_execute_is_a_note(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root/'ops').mkdir()
            (root/'ops/run.sql').write_text('BEGIN EXECUTE(query); END;')
            refs, unknown = census.scan_references(root, [table('other')], ['ops/run.sql'], [], [])
            report = census.build_report([table('other')], references=refs, unresolved=unknown, backup_tables=set())
            self.assertEqual(report['tables'][0]['classification'], 'unreferenced-and-empty')

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
            self.assertEqual({item["kind"] for item in unresolved}, {"unresolved_call_note", "excluded_sensitive_path"})
            report = census.build_report([table("widget")], references=refs,
                                         unresolved=unresolved, backup_tables=set())
            self.assertEqual(report["tables"][0]["classification"], "unreferenced-and-empty")

    def test_schema_only_and_multiline_opaque_execute_are_notes(self):
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
                        self.assertEqual(len(unresolved), 1)
                        self.assertEqual(unresolved[0]['kind'], 'unresolved_call_note')
                        self.assertEqual(unresolved[0]['possible_tables'], [])
                        self.assertEqual(unresolved[0]['line'], 2)
                        report = census.build_report([table("widget")], references=refs,
                                                     unresolved=unresolved, backup_tables=set())
                        self.assertEqual(report["counts"]["unreferenced-and-empty"], 1)

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
                             {"inventory_table_unresolved", "unresolved_call_note"})


if __name__ == "__main__":
    unittest.main()
