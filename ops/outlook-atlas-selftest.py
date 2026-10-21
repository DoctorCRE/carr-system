#!/usr/bin/env python3
"""Synthetic bundle tests. No installed Outlook or private store is consulted."""

import importlib.util
import copy
import hashlib
import json
from pathlib import Path
import plistlib
import subprocess
import sys
import tempfile
import unittest


SPEC = importlib.util.spec_from_file_location(
    "outlook_atlas", Path(__file__).with_name("outlook-atlas.py")
)
assert SPEC is not None and SPEC.loader is not None
atlas = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(atlas)


class SurfaceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.app = Path(self.temp.name) / "Outlook.app"
        self.info = self.app / "Contents/Info.plist"
        self.info.parent.mkdir(parents=True)
        self.metadata = {
            "CFBundleIdentifier": "com.microsoft.Outlook",
            "CFBundleVersion": "16.114.123",
            "CFBundleShortVersionString": "16.114",
            "CFBundleURLTypes": [{"CFBundleURLSchemes": ["mailto", "ms-outlook"]}],
            "CFBundleDocumentTypes": [{
                "CFBundleTypeName": "Example event", "CFBundleTypeExtensions": ["ics"],
                "CFBundleTypeRole": "Editor", "LSHandlerRank": "Default",
            }],
            "NSUserActivityTypes": ["com.example.compose"],
            "INIntentsSupported": ["INSendMessageIntent"],
        }
        self.write_info()

    def write_info(self):
        self.info.write_bytes(plistlib.dumps(self.metadata, fmt=plistlib.FMT_BINARY))

    def binary(self, data):
        path = self.app / "Contents/MacOS/Microsoft Outlook"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"\xcf\xfa\xed\xfe" + b"\0" * 28 + data)
        return path

    def test_binary_literals_recover_only_grammar_with_every_evidence_offset(self):
        literal = b"ms-outlook://emails/message/open?immutableID=opaque&account=example.test\0"
        self.binary(literal + literal + b"ms-outlook://events/new?title=Example\0")
        result = self.extract()
        routes = [e for e in result["surfaces"].values() if e["kind"] == "url_literal"]
        self.assertEqual(len(routes), 2)
        route = next(e for e in routes if e["value"]["host"] == "emails")
        self.assertEqual(route["value"], {
            "scheme": "ms-outlook", "host": "emails", "path": "/message/open",
            "query_keys": ["account", "immutableID"],
        })
        self.assertEqual([e["offset"] for e in route["evidence"]], [32, 32 + len(literal)])
        self.assertEqual(route["claim"], "literal grammar only; runtime acceptance unknown")
        self.assertNotIn("opaque", json.dumps(result))

    def grammar_audits(self):
        path = self.binary(b'separate synthetic instruction constants, no complete URL')
        source = 'Contents/MacOS/Microsoft Outlook'
        raw = path.read_bytes()
        grammar = {
            'schema': 'wr443-deeplink-grammar/v1',
            'subject': {'binary': source, 'build': '16.114.123', 'bytes': len(raw),
                        'sha256': hashlib.sha256(raw).hexdigest(), 'architecture': 'arm64'},
            'emails': {
                'recognized_query_keys': ['from', 'body', 'account', 'immutableID'],
                'new': {'route': 'ms-outlook://emails/new', 'reads': ['body', 'account'],
                        'evidence': ['email_keys']},
                'send': {'route': 'ms-outlook://emails/send', 'result': 'false, unhandled',
                         'evidence': ['email_send_false']},
                'existing_message': {'route': 'ms-outlook://emails/open/message?immutableID=SECRET',
                                     'evidence': ['email_keys']},
            },
            'events': {
                'route': 'ms-outlook://events/new', 'recognized_query_keys': ['title', 'location',
                    'isallday', 'start', 'end', 'attendees'],
                'location': {'key': 'location'}, 'isallday': {'key': 'isallday'},
                'dates': {'keys': ['start', 'end']}, 'attendees': {'key': 'attendees'},
                'evidence': ['event_keys'],
            },
            'evidence': [{'id': key, 'file_offset': '0x20', 'byte_length': 4,
                          'vm_start': '0x100000020', 'sha256': hashlib.sha256(raw[32:36]).hexdigest()}
                         for key in ['email_keys', 'email_send_false', 'event_keys']],
        }
        send = {
            'schema': 'wr443-outlook-static-send-audit/v1',
            'sources': [{'path': 'MacOS/Microsoft Outlook', 'size_bytes': len(raw),
                         'sha256': hashlib.sha256(raw).hexdigest()}],
            'questions': {
                '1_emails_send': {'any_query_keys_consumed': False},
                '2_immutableID': {'applicable_path': 'ms-outlook://emails/open/message',
                                  'required_message_query_key': 'immutableID', 'evidence': ['E07']},
                '3_accountID': {'actual_query_key': 'account', 'fallback_query_key': 'from',
                                'evidence': ['E15']},
            },
            'evidence': [{'id': key, 'binary': 'MacOS/Microsoft Outlook',
                          'universal_file_offset': '0x20', 'bytes_hex': raw[32:36].hex(),
                          'arm64_vm_address': '0x100000020', 'symbol': 'Synthetic.rawValue'}
                         for key in ['E07', 'E15']],
        }
        return grammar, send

    def test_reconstruction_extracts_audited_constants_without_runtime_claims(self):
        grammar, send = self.grammar_audits()
        before = self.extract()
        result = atlas.reconcile_surface(before, grammar, send, {'sources': []})
        self.assertFalse(any(e['kind'] == 'url_literal' for e in result['surfaces'].values()))
        routes = {e['value']['path']: e for e in result['surfaces'].values()
                  if e['kind'] == 'url_reconstructed' and e['value']['host'] == 'emails'}
        self.assertEqual(routes['/new']['value']['query_keys'], ['account', 'body', 'from'])
        self.assertEqual(routes['/open/message']['value']['query_keys'],
                         ['account', 'from', 'immutableID'])
        self.assertEqual(routes['/send']['value']['query_keys'], [])
        self.assertEqual(routes['/send']['value']['static_dispatch'], 'unhandled_false')
        self.assertEqual(routes['/new']['confidence'], 'inferred')
        self.assertEqual(routes['/new']['value']['runtime_acceptance'], 'untested')
        self.assertTrue(all(e['offset'] == 32 for e in routes['/new']['evidence']))
        self.assertNotIn('SECRET', json.dumps(result))
        self.assertEqual(before, self.extract(), 'reconciliation must not mutate its input')
        self.assertEqual(result, self.extract(result), 'bound reconstruction must regenerate')

    def test_reconcile_cli_checks_pinned_audit_bytes_and_omits_values(self):
        grammar, send = self.grammar_audits()
        root = Path(self.temp.name)
        subprocess.run(['git', 'init', '-q', str(root)], check=True)
        audits = root / 'audits'
        audits.mkdir()
        inputs = {'survey': {'schema': 'wr443-outlook-route-survey/v1'},
                  'grammar': grammar, 'send': send}
        paths = {'survey': 'wr443-outlook-route-survey.json',
                 'grammar': 'wr443-deeplink-grammar.json', 'send': 'wr443-send-by-id.json'}
        for role, value in inputs.items():
            (audits / paths[role]).write_text(json.dumps(value))
        subprocess.run(['git', '-C', str(root), 'add', *['audits/' + p for p in paths.values()]], check=True)
        subprocess.run(['git', '-C', str(root), '-c', 'user.name=Synthetic',
                        '-c', 'user.email=synthetic@example.invalid', 'commit', '-qm', 'fixture'], check=True)
        revision = subprocess.check_output(['git', '-C', str(root), 'rev-parse', 'HEAD'], text=True).strip()
        bindings = {'schema': 'outlook-grammar-sources/v1', 'sources': [
            {'role': role, 'path': 'audits/' + paths[role], 'revision': revision,
             'sha256': hashlib.sha256((audits / paths[role]).read_bytes()).hexdigest(),
             'pr': 2040 + i, 'state': 'OPEN', 'merge_commit': None, 'merged_at': None}
            for i, role in enumerate(inputs)]}
        pinned = root / 'bindings.json'
        baseline = root / 'baseline.json'
        baseline.write_text(json.dumps(self.extract()))
        script = str(Path(__file__).with_name('outlook-atlas.py'))
        def run():
            pinned.write_text(json.dumps(bindings))
            return subprocess.run([sys.executable, script, 'reconcile', str(baseline), str(pinned)],
                                  cwd=root, capture_output=True, text=True)
        result = run()
        self.assertEqual(result.returncode, 0, result.stderr)
        reconciled = json.loads(result.stdout)
        self.assertEqual(len(reconciled['reconciliation']['provenance']['sources']), 3)
        self.assertNotIn('SECRET', result.stdout)
        bindings['sources'][0]['sha256'] = '0' * 64
        invalid = run()
        self.assertEqual(invalid.returncode, 2)
        self.assertEqual(invalid.stdout, '')
        self.assertEqual(json.loads(invalid.stderr)['error'], 'atlas_input_invalid_or_changed')

    def test_audited_source_or_build_changes_require_new_reconstruction(self):
        grammar, send = self.grammar_audits()
        before = self.extract()
        reconciled = atlas.reconcile_surface(before, grammar, send, {'sources': []})
        self.binary(b'changed synthetic instructions')
        with self.assertRaisesRegex(ValueError, 'binary or build'):
            self.extract(reconciled)
        self.grammar_audits()
        self.metadata['CFBundleVersion'] = '16.115.456'
        self.write_info()
        with self.assertRaisesRegex(ValueError, 'binary or build'):
            atlas.extract_surface(self.app, '16.115.456', reconciled)
        for audit in (grammar, send):
            bad = copy.deepcopy(audit)
            bad['schema'] = 'unsupported'
            with self.assertRaisesRegex(ValueError, 'schema'):
                atlas.reconcile_surface(before, bad if audit is grammar else grammar,
                                         bad if audit is send else send, {'sources': []})
        contradictory = copy.deepcopy(send)
        contradictory['questions']['1_emails_send']['any_query_keys_consumed'] = True
        with self.assertRaisesRegex(ValueError, 'disagree'):
            atlas.reconcile_surface(before, grammar, contradictory, {'sources': []})

    def test_reconstructed_drift_separates_query_vocabulary_and_audit_provenance(self):
        grammar, send = self.grammar_audits()
        before = atlas.reconcile_surface(self.extract(), grammar, send, {'sources': []})
        grammar['emails']['recognized_query_keys'].append('signature')
        after = atlas.reconcile_surface(before, grammar, send, {'sources': []})
        diff = atlas.diff_surface(before, after)
        self.assertEqual([e['kind'] for e in diff['changed'].values()], ['url_query_vocabulary'])
        evidence = copy.deepcopy(after)
        evidence['reconciliation']['provenance']['sources'] = [{'state': 'MERGED'}]
        diff = atlas.diff_surface(after, evidence)
        self.assertEqual(diff['changed'], {})
        self.assertTrue(diff['reconciliation_changes'])
        reference = atlas.render_surface(before)
        self.assertIn('Synthetic.rawValue', reference)
        self.assertIn('byte 32, length 4', reference)
        self.assertIn('unhandled_false', reference)

    def test_non_macho_resources_are_not_url_evidence(self):
        resource = self.app / "Contents/Resources/example.txt"
        resource.parent.mkdir(parents=True)
        resource.write_text("ms-outlook://not-a-binary/new?invented=yes")
        self.assertFalse(any(e["kind"] == "url_literal" for e in self.extract()["surfaces"].values()))

    def test_drift_separates_role_and_query_changes_from_hash_only_changes(self):
        self.binary(b"ms-outlook://emails/open?immutableID=one\0")
        before = self.extract()
        self.metadata["CFBundleDocumentTypes"][0]["CFBundleTypeRole"] = "Viewer"
        self.metadata["INIntentsSupported"] = ["INExampleIntent"]
        self.write_info()
        self.binary(b"ms-outlook://emails/open?immutableID=two&account=example.test\0")
        after = self.extract(before)
        diff = atlas.diff_surface(before, after)
        self.assertEqual(len(diff["new"]), 1)
        self.assertEqual(len(diff["removed"]), 1)
        self.assertEqual({e["kind"] for e in diff["changed"].values()}, {"document_type", "url_literal"})
        self.assertEqual(len(diff["source_changes"]), 2)
        self.assertEqual(diff["changed"][next(k for k in diff["changed"] if k.startswith("document_type:"))]
                         ["fields"]["/value/CFBundleTypeRole"], {"before": "Editor", "after": "Viewer"})
        self.binary(b"ms-outlook://emails/open?immutableID=three&account=example.test\0")
        evidence_only = atlas.diff_surface(after, self.extract(after))
        self.assertEqual(evidence_only["changed"], {})
        self.assertEqual(len(evidence_only["source_changes"]), 1)
        self.assertEqual(atlas.diff_surface(before, before)["source_changes"], {})

    def test_new_version_preserves_first_seen_for_existing_declarations(self):
        before = self.extract()
        self.metadata["CFBundleVersion"] = "16.115.456"
        self.metadata["INIntentsSupported"].append("INExampleIntent")
        self.write_info()
        after = atlas.extract_surface(self.app, "16.115.456", before)
        intents = {e["value"]: e for e in after["surfaces"].values() if e["kind"] == "supported_intent"}
        self.assertEqual(intents["INSendMessageIntent"]["first_seen_version"], "16.114.123")
        self.assertEqual(intents["INExampleIntent"]["first_seen_version"], "16.115.456")
        self.assertEqual(atlas.diff_surface(before, after)["version"],
                         {"before": "16.114.123", "after": "16.115.456"})

    def test_symlinks_are_reported_and_never_followed(self):
        outside = Path(self.temp.name) / "outside"
        outside.mkdir()
        (outside / "Info.plist").write_text("unreadable as plist")
        (self.app / "Contents/External").symlink_to(outside, target_is_directory=True)
        (self.app / "Contents/linked.dylib").symlink_to(outside / "private")
        result = self.extract()
        self.assertEqual(result["skipped_symlinks"], ["Contents/External", "Contents/linked.dylib"])
        self.info.unlink()
        self.info.symlink_to(outside / "Info.plist")
        with self.assertRaisesRegex(ValueError, "symlink"):
            self.extract()

    def test_version_mismatch_and_unsupported_baseline_are_refused(self):
        with self.assertRaisesRegex(ValueError, "CFBundleVersion"):
            atlas.extract_surface(self.app, "16.999")
        with self.assertRaisesRegex(ValueError, "schema"):
            self.extract({"schema": "unknown"})

    def test_conflicting_declarations_are_not_silently_discarded(self):
        self.metadata["CFBundleURLTypes"].append({
            "CFBundleURLSchemes": ["mailto"], "CFBundleTypeRole": "Viewer",
        })
        self.write_info()
        with self.assertRaisesRegex(ValueError, "conflicting"):
            self.extract()

    def test_malformed_metadata_emits_no_partial_atlas_or_payload(self):
        data = self.app / "Contents/Resources/Metadata.appintents/extract.actionsdata"
        data.parent.mkdir(parents=True)
        data.write_text("synthetic-private@example.test this is not JSON")
        process = subprocess.run([sys.executable, str(Path(__file__).with_name("outlook-atlas.py")),
                                  "extract", "--app", str(self.app), "--version", "16.114.123"],
                                 capture_output=True, text=True)
        self.assertEqual(process.returncode, 2)
        self.assertEqual(process.stdout, "")
        self.assertEqual(json.loads(process.stderr)["error"], "atlas_input_invalid_or_changed")
        self.assertNotIn("synthetic-private", process.stderr)

    def test_query_keys_from_multiple_literals_are_unioned_without_values(self):
        self.binary(b"ms-outlook://events/new?title=Example\0ms-outlook://events/new?start=42\0")
        routes = [e for e in self.extract()["surfaces"].values() if e["kind"] == "url_literal"]
        self.assertEqual(len(routes), 1)
        self.assertEqual(routes[0]["value"]["query_keys"], ["start", "title"])
        self.assertEqual(len(routes[0]["evidence"]), 2)

    def test_unsupported_plist_and_actions_shapes_fail_instead_of_inventing_entries(self):
        self.metadata["INIntentsSupported"] = "not-an-array"
        self.write_info()
        with self.assertRaisesRegex(ValueError, "declaration"):
            self.extract()
        self.metadata["INIntentsSupported"] = []
        self.write_info()
        data = self.app / "Contents/Resources/Metadata.appintents/extract.actionsdata"
        data.parent.mkdir(parents=True)
        data.write_text('{"actions": []}')
        with self.assertRaisesRegex(ValueError, "actions"):
            self.extract()

    def test_drift_reports_added_null_metadata_keys(self):
        data = self.app / "Contents/Resources/Metadata.appintents/extract.actionsdata"
        data.parent.mkdir(parents=True)
        data.write_text('{"actions": {"ExampleIntent": {"identifier": "ExampleIntent"}}}')
        before = self.extract()
        data.write_text('{"actions": {"ExampleIntent": {"identifier": "ExampleIntent", "result": null}}}')
        diff = atlas.diff_surface(before, self.extract(before))
        self.assertEqual(len(diff["changed"]), 1)
        change = next(iter(diff["changed"].values()))["fields"]["/value/result"]
        self.assertEqual(change, {"before": None, "after": None, "before_present": False, "after_present": True})

    def test_cli_extract_drift_and_render_use_the_same_contract(self):
        script = str(Path(__file__).with_name("outlook-atlas.py"))
        extracted = subprocess.run([sys.executable, script, "extract", "--app", str(self.app),
                                    "--version", "16.114.123"], capture_output=True, text=True)
        self.assertEqual(extracted.returncode, 0, extracted.stderr)
        result = json.loads(extracted.stdout)
        baseline = Path(self.temp.name) / "atlas.json"
        baseline.write_text(extracted.stdout)
        drift = subprocess.run([sys.executable, script, "drift", "--app", str(self.app),
                                "--version", "16.114.123", "--baseline", str(baseline)],
                               capture_output=True, text=True)
        self.assertEqual(drift.returncode, 0, drift.stderr)
        self.assertEqual(json.loads(drift.stdout)["changed"], {})
        rendered = subprocess.run([sys.executable, script, "render", str(baseline)],
                                  capture_output=True, text=True)
        self.assertEqual(rendered.returncode, 0, rendered.stderr)
        self.assertEqual(rendered.stdout, atlas.render_surface(result))
        self.assertIn("Editor", rendered.stdout)
        self.assertIn("runtime", rendered.stdout)
        self.assertEqual(sorted(p.name for p in Path(self.temp.name).iterdir()), ["Outlook.app", "atlas.json"])

    def extract(self, baseline=None):
        return atlas.extract_surface(self.app, "16.114.123", baseline)

    def test_plugin_and_app_intents_preserve_action_parameters(self):
        plugin = self.app / "Contents/PlugIns/Example.appex/Contents"
        plugin.mkdir(parents=True)
        (plugin / "Info.plist").write_bytes(plistlib.dumps({
            "CFBundleIdentifier": "com.example.widget", "CFBundleVersion": "123",
            "NSExtension": {"NSExtensionPointIdentifier": "com.apple.widgetkit-extension"},
        }))
        data = plugin / "Resources/Metadata.appintents/extract.actionsdata"
        data.parent.mkdir(parents=True)
        data.write_text(json.dumps({"actions": {"ExampleIntent": {
            "identifier": "ExampleIntent", "parameters": [{"name": "direction", "isOptional": False}],
        }}}))
        result = self.extract()
        action = next(e for e in result["surfaces"].values() if e["kind"] == "app_intent")
        self.assertEqual(action["value"]["parameters"], [{"name": "direction", "isOptional": False}])
        self.assertEqual(action["evidence"][0]["pointer"], "/actions/ExampleIntent")
        extension = next(e for e in result["surfaces"].values() if e["kind"] == "plugin")
        self.assertEqual(extension["value"]["NSExtension"]["NSExtensionPointIdentifier"],
                         "com.apple.widgetkit-extension")

    def test_declared_surfaces_keep_roles_and_source_evidence(self):
        result = self.extract()
        by_kind = {}
        for entry in result["surfaces"].values():
            by_kind.setdefault(entry["kind"], []).append(entry)
        self.assertEqual([x["value"]["scheme"] for x in by_kind["url_scheme"]],
                         ["mailto", "ms-outlook"])
        document = by_kind["document_type"][0]
        self.assertEqual(document["value"]["CFBundleTypeRole"], "Editor")
        self.assertEqual(document["first_seen_version"], "16.114.123")
        self.assertEqual(document["confidence"], "proven")
        self.assertEqual(document["evidence"][0]["pointer"], "/CFBundleDocumentTypes/0")
        self.assertEqual(len(result["sources"]["Contents/Info.plist"]["sha256"]), 64)
        self.assertEqual(by_kind["user_activity"][0]["value"], "com.example.compose")
        self.assertEqual(by_kind["supported_intent"][0]["value"], "INSendMessageIntent")
        self.assertEqual(result, self.extract(), "extraction must be deterministic")


if __name__ == "__main__":
    unittest.main()
