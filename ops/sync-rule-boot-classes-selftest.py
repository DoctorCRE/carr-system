#!/usr/bin/env python3
"""Bounded semantic regression for the rule boot class generator."""
import copy
import importlib.util
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

REPO = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("rule_boot_classes", REPO / "ops/sync-rule-boot-classes.py")
assert SPEC is not None and SPEC.loader is not None
boot = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(boot)
CORPUS = {row["id"]: row["statement"] for row in
          json.loads((REPO / "ops/config/rule-selection-corpus.v1.json").read_text())["rules"]}
# Regression fixtures only; production recognition must depend on statements, not these ids.
STANDING_IDS = ("3d185f2b", "424ba0cc", "51d9f05f", "5697071b", "614c1209",
                "67580c28", "725dff46", "9293d609", "a8f159ad", "b7ec8f3b",
                "c8ebeb5b", "ce12c11e", "d3774a28", "f0f9156e", "f2d58a30", "f5bac101")


class StandingFactsTests(unittest.TestCase):
    def test_live_vendor_network_cannot_be_deferred_to_action_or_topic(self):
        for cls in ("b", "c"):
            with self.subTest(cls=cls):
                doc = copy.deepcopy(boot.load())
                doc["rules"]["725dff46"].update({"class": cls, "always_on": False})
                self.assertTrue(any("725dff46" in p and "standing fact" in p for p in boot.validate(doc)),
                                "team ownership must be validated from its full corpus statement")

    def test_supported_corpus_facts_are_always_on(self):
        doc = boot.load()
        for rid in STANDING_IDS:
            with self.subTest(rid=rid):
                self.assertTrue(boot.is_standing_fact(CORPUS[rid]))
                self.assertEqual(doc["rules"][rid]["class"], "a")
                self.assertIs(doc["rules"][rid]["always_on"], True)
                for cls in ("b", "c"):
                    deferred = dict(doc["rules"][rid], **{"class": cls, "always_on": False})
                    self.assertTrue(any("standing fact" in p for p in
                        boot.validate({"rules": {rid: deferred}}, statements=CORPUS)))

    def test_missing_or_empty_statements_cannot_authorize_deferred_classes(self):
        for cls in ("b", "c"):
            row = dict(boot.load()["rules"]["725dff46"], **{"class": cls, "always_on": False})
            for statements in ({}, {"12345678": ""}, {"12345678": "  "}, {"12345678": None}):
                with self.subTest(cls=cls, statements=statements):
                    self.assertTrue(any("corpus statement" in p for p in boot.validate(
                        {"rules": {"12345678": row}}, statements=statements)))

    def test_neutral_assertions_use_semantics_not_ids(self):
        positives = (
            "Joe and Dell are business partners.",
            "Team is Joe and Dell by definition.",
            "Our territory is South Alabama and the Florida Panhandle.",
            "The persona is named Doc.",
            "Joe is the system-design partner.",
            "Dell holds an Alabama licence.",
            "The vendor network belongs to the team.",
            "CARR represents buyers and tenants only.",
            "CARR never represents landlords or sellers.",
            "Joe and Dell are visual thinkers.",
            "Joe and Dell are early-stage.",
            "The partner will never be able to hand-feed the system.",
            "Joe cannot manually report every activity to the system.",
            "Joe values being able to work from his phone.",
            "Concept coherence is his edge.",
            "The practice operates from an abundance mindset.",
            "Calm is defined by Joe.",
            "Joe has granted standing permission to go big on motion.",
            "Prospects are healthcare experts unfamiliar with CRE.",
        )
        template = copy.deepcopy(boot.load()["rules"]["725dff46"])
        for statement in positives:
            with self.subTest(statement=statement):
                self.assertTrue(boot.is_standing_fact(statement))
                for cls in ("b", "c"):
                    row = dict(template, **{"class": cls, "always_on": False})
                    doc = {"rules": {"12345678": row}}
                    self.assertTrue(boot.validate(doc, statements={"12345678": statement}))
                self.assertFalse(boot.validate({"rules": {"12345678": dict(template, **{
                    "class": "a", "always_on": True})}}, statements={"12345678": statement}))

    def test_conditional_quoted_and_action_only_statements_are_not_facts(self):
        negatives = (
            "When writing prospect-visible content, credit the vendor network to the team.",
            "Before creating a client, ask Joe for review.",
            "If Joe and Dell are business partners, show their shared work.",
            "When our territory is South Alabama, route the lead to Dell.",
            "Suppose the persona is named Doc.",
            'Never say "Joe and Dell are business partners" without evidence.',
            "Never assert Joe and Dell are business partners without evidence.",
            "It is false that Joe is a broker.",
            "Imagine Joe is the system-design partner.",
            "'Joe and Dell are business partners' is an unverified quote.",
            "‘The persona is named Doc’ is an unverified quote.",
            'WRONG: Joe and Dell are business partners.',
            'Hypothetical: CARR represents buyers and tenants only.',
            'Banned example: Our territory is South Alabama.',
            'Example: The vendor network belongs to the team.',
            'Do not assume Joe holds an Alabama licence.',
            'CARR may represent buyers and tenants only.',
            'If Joe has granted standing permission to go big on motion, use it.',
            'When Doc is missing a capability, find real tooling for it.',
            'Doc must have good judgment and be proactive.',
            'Route Outlook tasks to Copilot; use Claude for the rest.',
            'Joe asked Dell to review the rendered artifact.',
            'A territory map should name Joe and Dell.',
            '```\nJoe and Dell are business partners.\n```',
        )
        for statement in negatives:
            with self.subTest(statement=statement):
                self.assertFalse(boot.is_standing_fact(statement))
        for rid in ("49533583", "6901cc3b", "8aefcdce", "57d13061"):
            with self.subTest(rid=rid):
                self.assertFalse(boot.is_standing_fact(CORPUS[rid]))

    def test_generator_refuses_wrong_class_even_when_output_has_parity(self):
        for cls in ("b", "c"):
            with self.subTest(cls=cls), tempfile.TemporaryDirectory() as tmp:
                doc = copy.deepcopy(boot.load())
                doc["rules"]["725dff46"].update({"class": cls, "always_on": False})
                classes = Path(tmp) / "classes.json"
                out = Path(tmp) / "classes.js"
                classes.write_text(json.dumps(doc))
                out.write_text(boot.render(doc))
                result = subprocess.run([sys.executable, str(REPO / "ops/sync-rule-boot-classes.py"),
                    "--check", "--classes", str(classes), "--out", str(out)], capture_output=True, text=True)
                self.assertEqual(result.returncode, 1)
                self.assertIn("725dff46", result.stdout)
                self.assertIn("standing fact", result.stdout)

    def test_digest_and_render_derive_from_the_class_source(self):
        doc = boot.load()
        self.assertEqual(boot.validate(doc), [])
        self.assertEqual(boot.render(doc), Path(boot.OUT_PATH).read_text())
        changed = copy.deepcopy(doc)
        changed["rules"]["725dff46"]["summary"] += " amended"
        self.assertNotEqual(boot.classes_digest(doc), boot.classes_digest(changed))
        self.assertIn(boot.classes_digest(doc), boot.render(doc))


if __name__ == "__main__":
    unittest.main()
