"""Synthetic integration cases for the public-source identity guard."""
import hashlib
import importlib.util
import io
import json
import pathlib
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest import mock
import os

REPO = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "ops"))
import pii_guard
from git_env import fixture_env


class PublicSourceGuardTests(unittest.TestCase):
    def test_repeated_snapshot_text_reuses_hash_work_without_losing_locations(self):
        corpus = self.synthetic_corpus()
        corpus["max_tokens"] = 16
        row = ('CREATE TABLE synthetic_table (synthetic_id int, synthetic_value text);\n'
               '-- Example Dental Group\n'
               '{"name": "\\u00c9xample Dental\\nGroup"}\n')
        source = row * 200
        with mock.patch.object(pii_guard.hashlib, "sha256", wraps=hashlib.sha256) as digest:
            spans = pii_guard.identity_spans(source, corpus)
        expected = []
        for index in range(200):
            begin = index * len(row) + row.index("Example")
            expected.append((begin, begin + len("Example Dental Group"), corpus["hashes"][0]))
            begin = index * len(row) + row.index("\\u00c9")
            end = index * len(row) + row.index('Group"') + len("Group")
            expected.append((begin, end, corpus["hashes"][0]))
        self.assertEqual(spans, expected)
        self.assertLess(digest.call_count, 1500,
                        "repeated snapshot vocabulary must not rehash every occurrence")

    def test_cache_eviction_long_tokens_and_changed_corpus_preserve_detection(self):
        corpus = self.synthetic_corpus()
        source = ("Example Dental Group\n" +
                  " ".join("synthetic%d" % index for index in range(9000)) +
                  "\nExample Dental Group")
        spans = pii_guard.identity_spans(source, corpus)
        self.assertEqual([(begin, end) for begin, end, _ in spans],
                         [(0, 20), (len(source) - 20, len(source))])
        changed = dict(corpus, hashes=[pii_guard.fingerprint("Synthetic Clinic", corpus["salt"])])
        self.assertEqual(pii_guard.identity_spans(source, changed), [])
        long_token = "synthetic" * 200
        long_corpus = dict(corpus, max_tokens=1,
                           hashes=[pii_guard.fingerprint(long_token, corpus["salt"])])
        self.assertEqual(pii_guard.identity_spans(long_token, long_corpus),
                         [(0, len(long_token), long_corpus["hashes"][0])])

    def test_ci_scanner_selftest_uses_its_own_repository_event(self):
        with tempfile.TemporaryDirectory() as tmp:
            event = pathlib.Path(tmp) / "event.json"
            event.write_text(json.dumps({"pull_request": {"base": {"sha": "a" * 40}}}))
            env = fixture_env()
            env["GITHUB_EVENT_PATH"] = str(event)
            program = ("import importlib.util, sys; "
                       "spec=importlib.util.spec_from_file_location('ci_selftest', sys.argv[1]); "
                       "module=importlib.util.module_from_spec(spec); spec.loader.exec_module(module); "
                       "module.test_secret_scanner_catches_and_respects_allow(); "
                       "sys.exit(any(not ok for _, ok, _ in module.RESULTS))")
            result = subprocess.run([sys.executable, "-c", program, str(REPO / "ops/ci-selftest.py")],
                                    cwd=REPO, env=env, capture_output=True, text=True, timeout=120)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            self.assertIn("a seeded credential is caught", result.stdout)
            self.assertIn("an inline allow marker on the same line suppresses it", result.stdout)

    def test_snapshot_export_sanitizes_prose_preserving_sql_and_row_identity(self):
        salt = "d" * 64
        corpus = {"schema": "public-source-identities/v1", "salt": salt,
                  "hashes": [hashlib.sha256((salt + "\0exampledentalgroup").encode()).hexdigest()],
                  "max_tokens": 3}
        source = ("select 1; -- Example Dental Group\n"
                  "COMMENT ON TABLE public.demo IS 'Example Dental Group example';\n"
                  "COPY public.retrieval_proposal (id, candidate, reason) FROM stdin;\n"
                  '00000000-0000-4000-8000-000000000001\t{"phrase":"Example Dental Group"}\tprose\n'
                  "\\.\n")
        result = pii_guard.sanitize_snapshot(source, corpus)
        self.assertNotIn("Example Dental Group", result)
        self.assertIn("select 1; -- Example Organization ", result)
        self.assertIn("00000000-0000-4000-8000-000000000001", result)
        self.assertIn('"phrase":"Example Organization ', result)
        self.assertEqual(pii_guard.sanitize_snapshot(result, corpus), result)
        with self.assertRaises(ValueError):
            pii_guard.sanitize_snapshot("select 'Example Dental Group';\n", corpus)
        with self.assertRaises(ValueError):
            pii_guard.sanitize_snapshot(
                "COPY public.party (name) FROM stdin;\nExample Dental Group\n\\.\n", corpus)
        multiline = "COMMENT ON TABLE public.demo IS 'Metadata\nExample Dental Group';\n"
        self.assertNotIn("Example Dental Group", pii_guard.sanitize_snapshot(multiline, corpus))
        self.assertEqual(pii_guard.sanitize_snapshot("select 'safe -- text';\n", corpus),
                         "select 'safe -- text';\n")

    def test_snapshot_sql_lexical_state_and_split_comments(self):
        corpus = self.synthetic_corpus()
        for source in [
            "SELECT '\n-- Example Dental Group\n';\n",
            'CREATE TABLE "prefix--Example Dental Group" (id int);\n',
            "COPY public.other (data) FROM stdin;\n-- Example Dental Group\n\\.\n",
            "SELECT $body$\n-- Example Dental Group\n$body$;\n",
            "SELECT E'escaped\\' -- Example Dental Group';\n",
        ]:
            with self.subTest(source=source), self.assertRaises(ValueError):
                pii_guard.sanitize_snapshot(source, corpus)
        for source in ["-- Example Dental\n-- Group\n",
                       "/* Example Dental Group */ SELECT 1;\n"]:
            self.assertNotIn("Example Dental", pii_guard.sanitize_snapshot(source, corpus))
        safe = "SELECT 'safe\rvalue';\nSELECT $$safe -- text$$;\n"
        self.assertEqual(pii_guard.sanitize_snapshot(safe, corpus), safe)

    def test_procedural_function_comments_are_prose_but_dynamic_sql_is_opaque(self):
        corpus = self.synthetic_corpus()
        source = ("CREATE FUNCTION demo() RETURNS int LANGUAGE plpgsql AS $$\n"
                  "BEGIN\nRETURN 1; -- Example Dental Group\nEND;\n$$;\n")
        result = pii_guard.sanitize_snapshot(source, corpus)
        self.assertIn("RETURN 1; -- Example Organization ", result)
        self.assertIn("END;\n$$;", result)
        dynamic = ("CREATE FUNCTION demo() RETURNS text LANGUAGE plpgsql AS $$\n"
                   "BEGIN\nRETURN $query$-- Example Dental Group$query$;\nEND;\n$$;\n")
        with self.assertRaises(ValueError):
            pii_guard.sanitize_snapshot(dynamic, corpus)

    @staticmethod
    def synthetic_corpus():
        salt = "e" * 64
        return {"schema": "public-source-identities/v1", "salt": salt,
                "hashes": [pii_guard.fingerprint("Example Dental Group", salt)],
                "max_tokens": 3}

    def test_snapshot_file_projection_preserves_cr_bytes_and_constraints(self):
        script = (REPO / "bin/schema-snapshot.sh").read_text()
        block = script.split("<<'PUBLIC_SNAPSHOT_PROJECTION'\n", 1)[1].split(
            "\nPUBLIC_SNAPSHOT_PROJECTION", 1)[0]
        with tempfile.TemporaryDirectory() as tmp:
            root = pathlib.Path(tmp)
            (root / "ops/config").mkdir(parents=True)
            shutil.copy(REPO / "ops/pii_guard.py", root / "ops/pii_guard.py")
            (root / "ops/config/public-source-identities.v1.json").write_text(
                json.dumps(self.synthetic_corpus()))
            path = root / "snapshot.sql"
            original = b"SELECT 'safe\rvalue';\n"
            path.write_bytes(original)
            result = subprocess.run([sys.executable, "-", str(root), str(path)],
                                    input=block, text=True, capture_output=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(path.read_bytes(), original)
        self.assertEqual((REPO / "db/schema.sql").read_bytes().count(b"\r"), 3)

    def test_snapshot_ingress_patterns_reject_cr_in_postgres(self):
        dsn = os.environ.get("CARR_CI_DATABASE_URL")
        if not dsn:
            self.skipTest("disposable database supplied by local-db-ci/hosted migration lane")
        from urllib.parse import urlsplit
        self.assertIn(urlsplit(dsn).hostname, {"localhost", "127.0.0.1", "::1"})
        import psycopg
        import re
        text = (REPO / "db/schema.sql").read_bytes().decode()
        patterns = [text[text.rfind("'", 0, m.start()) + 1:text.find("'", m.end())]
                    for m in re.finditer("\r", text)]
        self.assertEqual(len(patterns), 3)
        with psycopg.connect(dsn) as conn:
            for pattern in patterns:
                # Match PostgreSQL's exact pattern as loaded from the snapshot.
                with conn.cursor() as cur:
                    cur.execute("select %s !~ %s, %s !~ %s", (
                        "mcp-tool:x\ry", pattern, "mcp-tool:synthetic", pattern))
                    self.assertEqual(cur.fetchone(), (False, True))

    def test_serialized_values_share_identity_normalization(self):
        for value in ["Éxample Dental Group", "Example Dental\nGroup",
                      "Example Dental\tGroup", "Example Dental Group"]:
            raw = json.dumps({"name": value})
            output = io.StringIO()
            self.assertEqual(pii_guard._check_corpus(
                [("fixture.json", raw)], self.synthetic_corpus(), output=output), 1)
            self.assertEqual(output.getvalue(), "fixture.json:1\n")
            self.assertNotIn(value, output.getvalue())

    def test_nested_scanner_isolated_from_outer_hosted_event(self):
        with tempfile.TemporaryDirectory() as tmp:
            event = pathlib.Path(tmp) / "event.json"
            event.write_text(json.dumps({"pull_request": {"base": {"sha": "a" * 40}}}))
            with mock.patch.dict(os.environ, {"GITHUB_EVENT_PATH": str(event)}):
                self.test_ci_scanner_checks_index_bytes_and_changed_files()

    def test_editable_documents_pass_alias_corpus(self):
        paths = ["migrations/0059_org_identity_spec.md", "migrations/0061_national_account_spec.md",
                 "migrations/0064_counterparty_scorecard_spec.md"]
        output = io.StringIO()
        self.assertEqual(pii_guard.check(
            [(name, (REPO / name).read_text()) for name in paths],
            REPO / "ops/config/public-source-identities.v1.json", output=output), 0,
            output.getvalue())

    def test_nonprivate_trigger_cues_preserve_routing(self):
        triggers = json.loads((REPO / "ops/config/rule-jit-triggers.v1.json").read_text())["triggers"]
        import re
        for cue in ["NPPES", "already system"]:
            self.assertTrue(any(row.get("kind") == "prompt_regex" and "5d44d3f3" in row.get("rule_ids", []) and
                                re.search(row.get("pattern", "(?!)"), cue, re.I)
                                for row in triggers), cue)
        base = json.loads(subprocess.check_output([
            "git", "show", "0e1ca6525af547e829dba5286111f2aa21c3dcd6:ops/config/rule-jev-triggers.v1.json"], cwd=REPO))
        current = json.loads((REPO / "ops/config/rule-jev-triggers.v1.json").read_text())
        for key, row in base["rules"].items():
            candidate = current["rules"][key]
            for field in ["always_on_probability", "negatives", "no_cue_probability"]:
                self.assertEqual(row[field], candidate[field], (key, field))
            for cue, probability in row["triggers"]["keywords"].items():
                if not re.fullmatch(r"[clvp]-\d+", cue):
                    self.assertEqual(candidate["triggers"]["keywords"].get(cue), probability,
                                     (key, "non-private cue"))

    def test_ci_scanner_checks_index_bytes_and_changed_files(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = pathlib.Path(tmp)
            (root / "ops" / "config").mkdir(parents=True)
            for name in ["ci-secret-scan.py", "pii_guard.py", "git_env.py"]:
                shutil.copy(REPO / "ops" / name, root / "ops" / name)
            salt = "c" * 64
            corpus = {"schema": "public-source-identities/v1", "salt": salt,
                      "hashes": [hashlib.sha256((salt + "\0exampledentalgroup").encode()).hexdigest()],
                      "max_tokens": 3}
            (root / "ops/config/public-source-identities.v1.json").write_text(json.dumps(corpus))
            env = fixture_env()
            env.pop("GITHUB_EVENT_PATH", None)
            def git(*args):
                subprocess.run(["git", *args], cwd=root, env=env, check=True,
                               stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            def scan(*args):
                return subprocess.run([sys.executable, "ops/ci-secret-scan.py", *args],
                                      cwd=root, env=env, capture_output=True, text=True)
            git("init", "-b", "main")
            git("config", "user.name", "Synthetic Tester")
            git("config", "user.email", "test@example.invalid")
            (root / "fixture.txt").write_text("Synthetic Clinic\n")
            git("add", "ops/ci-secret-scan.py", "ops/pii_guard.py", "ops/git_env.py",
                "ops/config/public-source-identities.v1.json", "fixture.txt")
            git("commit", "--no-verify", "-m", "synthetic baseline")
            git("update-ref", "refs/remotes/origin/main", "HEAD")
            (root / "fixture.txt").write_text("Example Dental Group\n")
            git("add", "fixture.txt")
            (root / "fixture.txt").write_text("Synthetic Clinic\n")
            result = scan("--staged")
            self.assertEqual(result.returncode, 1, result.stderr)
            self.assertEqual(result.stderr, "fixture.txt:1\n")
            self.assertNotIn("Example Dental Group", result.stdout + result.stderr)
            git("add", "fixture.txt")
            self.assertEqual(scan("--staged").returncode, 0)
            (root / "fixture.txt").write_text("EXAMPLE-DENTAL GROUP\n")
            self.assertEqual(scan().returncode, 1)
            git("add", "fixture.txt")
            git("commit", "--no-verify", "-m", "synthetic planted name")
            self.assertEqual(scan("--range", "origin/main..HEAD").returncode, 1)
            self.assertEqual(scan().returncode, 1)
            for value in ["Éxample Dental Group", "Example Dental\nGroup"]:
                (root / "fixture.txt").write_text(json.dumps({"name": value}))
                self.assertEqual(scan().returncode, 1)
                git("add", "fixture.txt")
                self.assertEqual(scan("--staged").returncode, 1)
                git("commit", "--no-verify", "-m", "serialized synthetic identity")
                self.assertEqual(scan("--range", "origin/main..HEAD").returncode, 1)
            event = root / "event.json"
            base = subprocess.check_output(["git", "rev-parse", "origin/main"],
                                           cwd=root, env=env, text=True).strip()
            event.write_text(json.dumps({"pull_request": {"base": {"sha": base}}}))
            env["GITHUB_EVENT_PATH"] = str(event)
            self.assertEqual(scan().returncode, 1)
            event.write_text(json.dumps({"pull_request": {"base": {"sha": "a" * 40}}}))
            result = scan()
            self.assertEqual(result.returncode, 2)
            self.assertEqual(result.stderr, "ops/ci-secret-scan.py:1\n")

    def test_normalization_across_punctuation_accents_and_lines(self):
        salt = "b" * 64
        corpus = {"schema": "public-source-identities/v1", "salt": salt,
                  "hashes": [hashlib.sha256((salt + "\0exampledentalgroup").encode()).hexdigest()],
                  "max_tokens": 3}
        with tempfile.TemporaryDirectory() as tmp:
            path = pathlib.Path(tmp) / "corpus.json"
            path.write_text(json.dumps(corpus))
            output = io.StringIO()
            self.assertEqual(pii_guard.check(
                [("example.txt", "Éxample-Dental\nGROUP\nClean text")],
                path, output=output), 1)
            self.assertEqual(output.getvalue(), "example.txt:1\n")

    def test_decomposed_accents_and_lowercase_copy(self):
        output = io.StringIO()
        self.assertEqual(pii_guard._check_corpus(
            [("synthetic.txt", "E\u0301xample Dental Group")], self.synthetic_corpus(), output=output), 1)
        with self.assertRaises(ValueError):
            pii_guard.sanitize_snapshot(
                "copy public.other (data) from stdin;\n-- Example Dental Group\n\\.\n",
                self.synthetic_corpus())

    def test_invalid_or_missing_corpus_refuses_without_echoing_content(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = pathlib.Path(tmp) / "corpus.json"
            for raw in [None, "not-json", '{}', '{"hashes": []}']:
                if raw is not None:
                    path.write_text(raw)
                output = io.StringIO()
                self.assertEqual(pii_guard.check([], path, output=output), 2)
                self.assertEqual(output.getvalue(), "ops/config/public-source-identities.v1.json:1\n")

    def test_planted_name_fails_with_location_only_and_clean_file_passes(self):
        salt = "a" * 64
        # Independent worked vector: lowercase ASCII with separators removed.
        digest = hashlib.sha256((salt + "\0" + "exampledentalgroup").encode()).hexdigest()
        corpus = {"schema": "public-source-identities/v1", "salt": salt,
                  "hashes": [digest], "max_tokens": 3}
        with tempfile.TemporaryDirectory() as tmp:
            path = pathlib.Path(tmp) / "corpus.json"
            path.write_text(json.dumps(corpus))
            output = io.StringIO()
            self.assertEqual(pii_guard.check(
                [("fixture.json", '{\n  "name": "EXAMPLE Dental Group"\n}')],
                path, output=output), 1)
            self.assertEqual(output.getvalue(), "fixture.json:2\n")
            output = io.StringIO()
            self.assertEqual(pii_guard.check(
                [("fixture.json", '{"name": "Synthetic Clinic"}')],
                path, output=output), 0)
            self.assertEqual(output.getvalue(), "")


if __name__ == "__main__":
    unittest.main()
