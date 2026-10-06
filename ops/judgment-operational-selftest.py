"""Every live judgment still ANSWERS, proven against answers known in advance.

WHY A SECOND CHECK, WHEN ONE ALREADY ASSERTS EVERY JUDGMENT HAS A CALLER.
Because having a caller is not the bar, and this session proved it twice in
one day. ops/jev_change_tolls.py was wired into the push hook with 2>/dev/null
on the invocation while writing its findings to stderr: called on every push,
printing to nowhere, and the wiring check was green the whole time. Earlier the
codebase scan read a response key that does not exist, scored 426 regions at
0.0, and reported a clean codebase -- again with every caller in place.

BOTH FAILURES LOOK EXACTLY LIKE SUCCESS, and that is the point. A judgment that
returns nothing is indistinguishable from a judgment that correctly found
nothing. You cannot tell them apart by watching. You can only tell them apart
by asking something whose answer you already know.

SO EVERY PROBE COMES IN A PAIR. A POSITIVE whose answer must be non-empty, and
a NEGATIVE whose answer must be empty. One alone is useless: a judgment stuck
at "nothing" passes every negative, and a judgment stuck at "everything" passes
every positive. Only the pair pins it.

WHAT THIS CATCHES that no other check in this repository does: a credential
that expired, a service that changed its response shape, output written to a
stream somebody silenced, a question edited until it stopped discriminating,
and a judgment quietly degrading while its tests -- which use recorded
fixtures, never the live service -- stay green.

LOCAL-ONLY, and for the same reason ops/config-as-code-selftest.py is. It needs
a live credential and a live service. A hosted runner has neither, so there it
would assert the absence of a thing that is absent by construction. The
credential is read from the environment and never printed.
"""

from __future__ import annotations

import importlib.util
import json
import os
import subprocess
import sys
import pathlib
import unittest
import uuid

OPS = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(OPS)
sys.path.insert(0, OPS)
from git_env import fixture_env                                # noqa: E402


def load(module_name):
    spec = importlib.util.spec_from_file_location(
        module_name, os.path.join(OPS, f"{module_name}.py"))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def recent_commits(limit=40):
    out = subprocess.run(
        ["git", "log", "--oneline", f"-{limit}", "--format=%h %s"],
        capture_output=True, text=True, cwd=REPO, timeout=60).stdout
    return [(line.split(" ", 1)[0], line.split(" ", 1)[1])
            for line in out.splitlines() if " " in line]


def has_credential():
    # Offline replay and CI verification must not spend live Jev calls even
    # when this workstation happens to hold an operational credential.
    if os.environ.get("CARR_JEV_OFFLINE_REPLAY") == "1":
        return False
    # ops/ci.sh exports CARR_JEV_OFFLINE, and the client refuses every paid
    # call under it; skip rather than report a refusal as a dead judgment.
    if os.environ.get("CARR_JEV_OFFLINE") or os.environ.get("CARR_HOOK_FIXTURE"):
        return False
    if os.environ.get("TYPESAFE_API_KEY"):
        return True
    path = os.path.expanduser("~/.config/carr/typesafe.env")
    return os.path.exists(path)


SKIP = "LOCAL-ONLY: needs the live credential and the live service"


@unittest.skipUnless(has_credential(), SKIP)
class MessageBoundaryJevTests(unittest.TestCase):
    """The prompt hook delivers matching rules and defers build judgment."""

    @classmethod
    def setUpClass(cls):
        path = os.path.join(REPO, "hooks", "rule-pack-preuse-reselection.py")
        spec = importlib.util.spec_from_file_location("jev_message_hook_live", path)
        cls.hook = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(cls.hook)

    def receipt(self, prompt, turn):
        payload = {
            "hook_event_name": "UserPromptSubmit",
            "cwd": REPO,
            # A fresh session per run: the compiled-trigger matcher does not
            # resend a rule a session already has in context, so a fixed id
            # would see the rule deduped on the second run inside the window.
            "session_id": f"jev-operational-selftest-{uuid.uuid4().hex}",
            "turn_id": turn,
            "prompt": prompt,
        }
        output = self.hook.process(payload)
        self.assertIsNotNone(output, "the prompt hook returned no context")
        return json.loads(output["hookSpecificOutput"]["additionalContext"])

    def test_binding_pack_rule_is_delivered_without_build_annotation(self):
        row = self.receipt(
            "My working tree is dirty. I am about to tell Joe another session "
            "is blocking my change. I only compared against HEAD and have not "
            "fetched origin/main. Can I say that?", "jev-positive")
        self.assertEqual(row["schema"], "rule-jev-message-delivery/v3")
        self.assertIn("173119a8", row["rule_ids"])
        self.assertIn("engineering-git", row["packs"])
        self.assertTrue(row["model_provenance"]["173119a8"]["binding_model"])
        self.assertNotIn("build_receipt", row)


class ShellPreCheckTests(unittest.TestCase):
    """Fires on every Bash call, from the delegation gate. Deterministic since
    the 2026-10-04 Jev audit (no paid call), so it runs without a credential."""

    def setUp(self):
        self.module = load("command_precheck")

    def test_it_still_flags_a_command_that_cannot_work(self):
        probability, _facts, _reasons = self.module.check(
            "./.venv/bin/python ops/there-is-no-such-file-as-this.py --nope",
            repo=REPO)
        self.assertGreater(probability, 0.5,
                           "a command naming a path that does not exist should "
                           "read as likely to fail; near-zero here means the "
                           "judgment stopped answering, not that the command is fine")

    def test_it_still_leaves_an_ordinary_command_alone(self):
        probability, _facts, _reasons = self.module.check("git status", repo=REPO)
        # None is the quiet answer, not a missing one: nothing was flagged, so
        # there is no maximum to report. Treating it as a number was this
        # test's own bug on the first run.
        self.assertTrue(probability is None or probability < 0.5,
                        f"a plain command must not be flagged (got {probability}); "
                        "a high score here means the check now warns about "
                        "everything, which is the same as warning about nothing")


@unittest.skipUnless(has_credential(), SKIP)
class DefectClassAdvisoryTests(unittest.TestCase):
    """Fires when a session is about to file a defect."""

    def setUp(self):
        self.module = load("jev_defect_class")

    def test_it_still_returns_a_ranked_shortlist(self):
        # The corpus is read from the live store. When the store cannot be
        # reached the advisor returns nothing by design, and that is an
        # environment fact, not a judgment regression: skip, never fail.
        if not self.module.load_classes():
            self.skipTest("defect-class corpus unreachable from this checkout")
        note = self.module.advise({
            "claimed": "the library was finished and working",
            "actual": "nothing in the repository ever called it, so it never ran"})
        self.assertTrue(note, "no shortlist came back at all")
        self.assertIn("0.", note, "a shortlist with no probabilities in it means "
                                  "the scores are being read from the wrong key "
                                  "-- the failure that scored 426 regions at zero")


class TollsHoldThePushTests(unittest.TestCase):
    """The advisory stopped being advice on 2026-09-18.

    It had scored inventory_reseal at 0.94 on a push, named the exact remedy,
    and been pushed past; hosted CI failed 25 minutes later on that same toll.
    These hold the shape of the fix rather than the score: the model picks
    which deterministic check to run, the check decides, and anything that
    cannot run lets the push through.
    """

    def setUp(self):
        self.module = load("jev_change_tolls")

    def test_a_probability_never_refuses_a_push_on_its_own(self):
        """Every toll that can hold a push names a real command to run."""
        for name, entry in self.module.VERIFIERS.items():
            argv, reason = entry
            self.assertIsInstance(argv, list)
            self.assertTrue(argv and all(isinstance(a, str) for a in argv),
                            f"{name} has no runnable command, so its score "
                            f"would be the thing refusing the push")
            self.assertIn(name, self.module.TOLLS,
                          f"{name} verifies a toll that does not exist")
            self.assertTrue(reason.strip(), f"{name} fails without saying why")

    def test_a_verifier_that_cannot_run_does_not_hold_the_push(self):
        """The fail-closed trap this must never become.

        A missing interpreter, an unreachable service, a renamed script: none
        of those are evidence the change owes anything. Blocking on them would
        turn an advisory into an outage.
        """
        module = self.module
        original = dict(module.VERIFIERS)
        try:
            module.VERIFIERS.clear()
            module.VERIFIERS["inventory_reseal"] = (
                ["/nonexistent/interpreter/that/is/not/here"], "cannot run")
            state = {"files": {"added": [], "edited": ["hooks/delegation-gate.py"]},
                     "added_files_with_a_shebang_or_main_guard": [],
                     "edited_files_that_are_script_entrypoints": ["hooks/delegation-gate.py"],
                     "this_branch_merged_another_branch": False}
            failures = module.verify(state)
            for name, _p, reason, _o in failures:
                self.assertIn("could not run", reason,
                              "a verifier that failed to LAUNCH was reported as "
                              "a failed check, which would block a correct push")
        finally:
            module.VERIFIERS.clear()
            module.VERIFIERS.update(original)

    def test_the_push_hook_actually_stops_on_a_failed_check(self):
        """Wiring, not behaviour. The function can be perfect and the hook can
        still `|| true` it into silence -- which is exactly what happened to
        this same advisory's stderr in the commit that added it."""
        hook = (pathlib.Path(self.module.REPO) / "ops" / "githooks" / "pre-push"
                ).read_text(encoding="utf-8")
        self.assertIn("module.verify(", hook,
                      "the push hook does not run the verifiers at all")
        self.assertIn("sys.exit(3)", hook,
                      "the hook prints failures without exiting nonzero")
        block = hook[hook.index("module.verify("):]
        self.assertNotIn("|| true", block.split("TOLLS")[0],
                         "`|| true` on the toll block swallows the refusal, "
                         "which is how this check got wired to nothing once already")
        self.assertIn('if [ "$status" -eq 3 ]', hook,
                      "the shell never translates the refusal into a failed push")

    def test_the_skip_door_still_exists(self):
        """A door with no handle gets taken off its hinges."""
        hook = (pathlib.Path(self.module.REPO) / "ops" / "githooks" / "pre-push"
                ).read_text(encoding="utf-8")
        self.assertIn("CARR_SKIP_TOLL_ADVISORY", hook)


class ChangeCollectorTests(unittest.TestCase):
    """What the advisory SEES, which needs no credential and so runs in CI too.

    Separated from the judgment tests on purpose: the questions need the model,
    but the file list handed to them is ordinary git plumbing, and both bugs
    found on 2026-09-18 were in the plumbing rather than in the judgment. A
    perfect answer about the wrong files is still the wrong answer.
    """

    def setUp(self):
        self.module = load("jev_change_tolls")

    def test_every_path_the_advisory_names_is_a_file_that_exists(self):
        """A path that does not resolve means the list was mis-parsed.

        `git status --porcelain` puts the status in two COLUMNS, so an
        unstaged edit reads " M path" with a leading space. Stripping the
        whole blob before splitting it ate that space on the FIRST line only
        and shifted that one path by a character -- the advisory reported an
        edit to "cp-server/src/tools.js", which does not exist, while every
        other row came through clean. One silently wrong row per run is the
        hardest kind to notice, so this asserts the property rather than the
        parse.
        """
        state = self.module.change()
        named = state["files"]["added"] + state["files"]["edited"]
        missing = [rel for rel in named
                   if not (pathlib.Path(self.module.REPO) / rel).exists()]
        self.assertEqual(
            missing, [],
            "the change advisory named paths that do not exist, so its file "
            "list is being mis-parsed and every judgment it makes is about "
            "the wrong change: " + ", ".join(missing))

    def test_a_deleted_path_is_reported_as_deleted_not_edited(self):
        """A deletion is not an edit to a file that no longer exists.

        Found 2026-09-23: a branch that untracked one binary made the property
        above fail, because the "D" row fell through to "edited". Built on a
        throwaway repository with no remote, so nothing is fetched. Every git
        call, including change()'s own, runs under fixture_env(): git exports
        GIT_DIR into the push hook, and inherited it would aim these calls at
        the live repository instead of the fixture.
        """
        import tempfile
        from unittest import mock
        env = fixture_env()
        with tempfile.TemporaryDirectory() as tmp:
            def git(*args):
                subprocess.run(["git", *args], cwd=tmp, check=True, env=env,
                               capture_output=True, text=True, timeout=60)
            git("init", "-q", "-b", "base")
            git("config", "user.email", "selftest@example.invalid")
            git("config", "user.name", "selftest")
            for name in ("gone.txt", "kept.txt"):
                pathlib.Path(tmp, name).write_text(name + "\n")
            git("add", ".")
            git("commit", "-q", "-m", "base")
            git("switch", "-q", "-c", "work")
            git("rm", "-q", "gone.txt")
            pathlib.Path(tmp, "kept.txt").write_text("changed\n")
            git("commit", "-q", "-am", "work")
            with mock.patch.dict(os.environ, env, clear=True):
                state = self.module.change(base="base", repo=tmp)
        self.assertEqual(state["files"]["deleted"], ["gone.txt"])
        self.assertEqual(state["files"]["edited"], ["kept.txt"])
        self.assertEqual(state["files"]["added"], [])

    def test_it_sees_uncommitted_work(self):
        """The advisory is most useful mid-edit, which is when the first
        version was blind: it diffed origin/main...HEAD only."""
        source = (pathlib.Path(self.module.__file__).read_text(encoding="utf-8")
                  if getattr(self.module, "__file__", None) else "")
        self.assertIn(
            "status", source,
            "jev_change_tolls.change() no longer consults git status, so it "
            "cannot see uncommitted work -- the exact moment a session most "
            "needs to be told what its change owes")


@unittest.skipUnless(has_credential(), SKIP)
class ChangeTollAdvisoryTests(unittest.TestCase):
    """Fires on every push, from ops/githooks/pre-push."""

    def setUp(self):
        self.module = load("jev_change_tolls")

    def test_it_still_names_the_tolls_an_obvious_change_owes(self):
        owed = self.module.owed({
            "files": {"added": ["hooks/new-gate.py"],
                      "edited": ["hooks/delegation-gate.py", "ops/ci.sh"]},
            "added_files_with_a_shebang_or_main_guard": ["hooks/new-gate.py"],
            "edited_files_that_are_script_entrypoints":
                ["hooks/delegation-gate.py", "ops/ci.sh"],
            "this_branch_merged_another_branch": False})
        named = {name for _probability, name, _fix in owed}
        self.assertIn("gate_rebless", named,
                      "an edited gate under hooks/ plainly owes a re-bless; "
                      "missing it means the judgment stopped discriminating")
        # Decision 05e144eb (2026-09-24): scripts are no longer sealed, so a new
        # hook and two edited scripts owe no registry successor. Naming one
        # would send every session back to hand-sealing.
        for absent in ("new_ingress_admitted", "inventory_reseal"):
            self.assertNotIn(absent, named,
                             f"a script-only change no longer owes {absent}")

    def test_it_still_names_a_reseal_for_a_new_verb(self):
        owed = self.module.owed({
            "files": {"added": [], "edited": ["mcp-server/src/tools.js"]},
            "added_files_with_a_shebang_or_main_guard": [],
            "edited_files_that_are_script_entrypoints": [],
            "summary": "adds a new MCP write verb register-widget to tools.js",
            "this_branch_merged_another_branch": False})
        named = {name for _probability, name, _fix in owed}
        self.assertTrue(named & {"new_ingress_admitted", "inventory_reseal"},
                        "a new MCP verb is still a sealed row and owes a successor")

    def test_it_still_stays_quiet_on_a_change_that_owes_nothing(self):
        owed = self.module.owed({
            "files": {"added": [], "edited": ["README.md"]},
            "added_files_with_a_shebang_or_main_guard": [],
            "edited_files_that_are_script_entrypoints": [],
            "this_branch_merged_another_branch": False})
        self.assertEqual(owed, [], "editing one markdown file owes no toll; "
                                   "naming any means it now warns on everything")


@unittest.skipUnless(has_credential(), SKIP)
class PostWriteCodeReviewTests(unittest.TestCase):
    """Fires after every Write or Edit, from hooks/lint-gate.py."""

    def setUp(self):
        self.module = load("jev_code_review")

    def test_it_still_reads_a_swallowed_write_as_wrong(self):
        scores = self.module.review_one({
            "path": "probe.py", "line": 1, "kind": "probe",
            "code": "def save_invoice(row):\n"
                    "    try:\n"
                    "        db.execute('insert into invoice values (%s)', row)\n"
                    "        db.commit()\n"
                    "    except Exception:\n"
                    "        pass\n"
                    "    return {'ok': True, 'saved': True}"})
        self.assertGreater(scores["swallow_is_wrong_here"], 0.6,
                           "a database write swallowed and then reported as "
                           "succeeded is the clearest case there is; a low score "
                           "means the judgment stopped discriminating")

    def test_it_still_leaves_correct_code_alone(self):
        scores = self.module.review_one({
            "path": "probe.py", "line": 1, "kind": "probe",
            "code": "def normalize_rate(amount, basis):\n"
                    "    if basis == 'usd_sf_mo':\n"
                    "        return amount * 12\n"
                    "    if basis == 'usd_sf_yr':\n"
                    "        return amount\n"
                    "    raise ValueError(f'unknown rate basis: {basis}')"})
        loud = [name for name, value in scores.items()
                if not name.startswith("_") and value >= 0.70]
        self.assertEqual(loud, [], "plain correct code must draw nothing; "
                                   "flagging it means the review now flags "
                                   "everything, which reads as noise and gets "
                                   "ignored")


@unittest.skipUnless(has_credential(), SKIP)
class EveryPathIsOutputNotJustReturnValue(unittest.TestCase):
    """The hook must SHOW what the judgment said.

    A judgment can be correct, called, and still silent, because the thing that
    invokes it discards its output. That is not hypothetical: the push hook
    carried 2>/dev/null over a block writing to stderr, and every other check
    in this repository was green.
    """

    def test_the_push_hook_does_not_silence_the_advisory(self):
        with open(os.path.join(REPO, "ops", "githooks", "pre-push"),
                  encoding="utf-8") as handle:
            body = handle.read()
        block = body[body.find("WHAT THIS CHANGE OWES"):]
        self.assertIn("jev_change_tolls", block, "the advisory is not in pre-push")
        invocation = [line for line in block.splitlines()
                      if ".venv/bin/python" in line and "TOLLS" in line]
        self.assertTrue(invocation, "could not find the advisory's invocation line")
        self.assertNotIn("2>/dev/null", invocation[0],
                         "the advisory writes to stderr; silencing stderr makes it "
                         "run on every push and print to nowhere")

    def test_the_post_write_review_prints_its_context(self):
        with open(os.path.join(REPO, "hooks", "lint-gate.py"),
                  encoding="utf-8") as handle:
            body = handle.read()
        self.assertIn("jev_code_review", body,
                      "the post-write review is not wired into the write door")
        self.assertIn("additionalContext", body,
                      "the review runs and its answer is never handed to the "
                      "session, which is indistinguishable from not running")

    def test_the_defect_advisory_prints_its_context(self):
        with open(os.path.join(REPO, "hooks", "blocker-decider-gate.py"),
                  encoding="utf-8") as handle:
            body = handle.read()
        self.assertIn("additionalContext", body,
                      "the shortlist is computed and never handed to the session")


class DealReadTests(unittest.TestCase):
    """ops/jev_deal_read.py -- the reading the Deal Room shows.

    These are the three things that were actually WRONG in the first version,
    each caught by running the thing rather than by reading it, plus the floor
    that is the whole reason the module is trustworthy.
    """

    @classmethod
    def setUpClass(cls):
        cls.mod = load("jev_deal_read")

    @staticmethod
    def bundle(**over):
        """A complete bundle. Complete on purpose: read_deal builds its state
        before the guard, so a half-built fixture raises here rather than
        being reported back as a service failure."""
        base = {
            "deal_id": "11111111-2222-3333-4444-555555555555",
            "name": "Test Deal", "client": "A Client", "phase": "research",
            "deal_type": "startup", "lane": "territory", "segment": "Dental",
            "city": "Pensacola", "owner": "joe", "operating_state": "active",
            "parking_reason": None, "parking_note": None,
            "next_step": "Send the LOI.", "next_step_due": "2026-10-01",
            "next_date": None, "status_narrative": "Terms discussed.",
            "history": ["2026-09-01 [note] toured two buildings"],
            "last_recorded_event": "2026-09-01",
            "days_since_record_touched": 17, "evidence_chars": 900,
            "has_evidence": True,
        }
        base.update(over)
        return base

    def test_the_score_levels_are_numbered_from_zero(self):
        """The off-by-one that reported every deal one rung too early.

        A five-rung score returns 0.0 through 4.0 and keys its probabilities
        "0".."4" -- verified against a live legend, not assumed. The first
        version subtracted one before indexing, which read four correctly
        judged deals as if the model had got them wrong.
        """
        mod = self.mod
        top = len(mod.MOVEMENT_LEVELS) - 1
        bundle = self.bundle()

        class Fake:
            @staticmethod
            def ask(state, questions, **kw):
                return {"answers": {
                    "movement": {"score": float(top), "confidence": 0.9},
                    "waiting_on": {"choice": "carr", "confidence": 0.8},
                    "silence_is_bad": {"noul": 0.1}}}

        real = mod.ts.ask
        mod.ts.ask = Fake.ask
        try:
            out = mod.read_deal(bundle)
        finally:
            mod.ts.ask = real
        self.assertEqual(out["movement_level"], mod.MOVEMENT_LEVELS[top],
                         "a top score must report the TOP rung")
        self.assertEqual(out["movement_rung"], len(mod.MOVEMENT_LEVELS))

    def test_a_thin_record_is_refused_rather_than_guessed(self):
        """The floor is the honest half. Below it nothing is asked at all."""
        mod = self.mod
        called = []

        def boom(*a, **k):
            called.append(1)
            raise AssertionError("asked a question about a deal with no evidence")

        real = mod.ts.ask
        mod.ts.ask = boom
        try:
            out = mod.read_deal(self.bundle(has_evidence=False,
                                            evidence_chars=12))
        finally:
            mod.ts.ask = real
        self.assertFalse(called, "no request may go out below the floor")
        self.assertFalse(out["judged"])
        self.assertIn("12", out["reason"])
        self.assertIn(str(mod.EVIDENCE_FLOOR_CHARS), out["reason"])

    def test_a_failed_reading_never_breaks_the_room(self):
        """Every failure path lands on judged=False, never on an exception."""
        mod = self.mod
        real = mod.ts.ask

        def explode(*a, **k):
            raise RuntimeError("no credential on this machine")

        mod.ts.ask = explode
        try:
            out = mod.read_deal(self.bundle())
        finally:
            mod.ts.ask = real
        self.assertFalse(out["judged"])
        self.assertIn("no credential", out["reason"])

    def test_a_malformed_bundle_raises_instead_of_reporting_an_outage(self):
        """The guard must cover the SERVICE, not this file's own faults.

        The first version wrapped state_for in the same try, so a missing key
        came back as "the judgment did not run: 'client'" and a bug in this
        module was indistinguishable from the network being down. Mutation
        found that the earlier test could not tell the two shapes apart: with
        a complete bundle they behave identically.
        """
        mod = self.mod
        real = mod.ts.ask
        mod.ts.ask = lambda *a, **k: self.fail("a broken bundle reached the service")
        try:
            with self.assertRaises(KeyError):
                mod.read_deal({"has_evidence": True, "evidence_chars": 900})
        finally:
            mod.ts.ask = real

    def test_the_state_carries_the_evidence_and_not_the_bookkeeping(self):
        """Accuracy falls as a state fills with detail unrelated to the
        decision, so the identifiers and counters stay out of the request."""
        mod = self.mod
        blob = json.dumps(mod.state_for(self.bundle()))
        self.assertIn("toured two buildings", blob)
        self.assertIn("Terms discussed", blob)
        self.assertNotIn("11111111", blob)
        self.assertNotIn("evidence_chars", blob)
        self.assertNotIn("has_evidence", blob)

    def test_the_deal_room_still_builds_when_the_reading_cannot_run(self):
        """The generator's guard, read from the generator itself."""
        with open(os.path.join(REPO, "generators", "build-deal-room.py"),
                  encoding="utf-8") as handle:
            body = handle.read()
        self.assertIn("CARR_DEAL_ROOM_NO_READ", body,
                      "the reading needs a deliberate kill switch")
        self.assertIn("READ, READ_SUMMARY = {}, {}", body,
                      "a failed reading must leave the room's payload empty")
        # "readHtml(d)" alone also matches `function readHtml(d){`, so
        # deleting the CALL left this assertion passing. Mutation found it.
        self.assertIn("h+=readHtml(d);", body,
                      "the reading is computed and never rendered")
        self.assertIn("readBanner()", body,
                      "the counts that qualify every number are not shown")


class DealEvidenceFloorTests(unittest.TestCase):
    """The floor and the last-touch date, measured against the live record.

    Needs the database. Skipped rather than failed where it is unreachable,
    because this file already runs on machines that have no credential.
    """

    def setUp(self):
        self.mod = load("jev_deal_read")
        try:
            self.found = self.mod.bundles()
        except Exception as exc:  # pragma: no cover - environment, not logic
            self.skipTest(f"the record layer is not reachable here: {exc}")

    def test_no_deal_is_ever_reported_quiet_for_a_negative_number_of_days(self):
        """A due date is in the FUTURE and is not a last touch.

        Feeding critical-date and action due dates into the last-touch marker
        produced a deal reported as quiet for minus nine hundred and
        twenty-five days, which is the kind of number a reader stops trusting
        the whole panel over.
        """
        bad = [b for b in self.found
               if b["days_since_record_touched"] is not None
               and b["days_since_record_touched"] < 0]
        self.assertEqual(bad, [], "a future date set the last-touch marker")

    def test_structured_fields_do_not_count_toward_the_evidence_floor(self):
        """Every live deal has a phase and a city. If those counted, every
        deal would clear any floor while carrying nothing to read."""
        nothing = [b for b in self.found if not b["history"]
                   and not b["status_narrative"] and not b["next_step"]]
        self.assertTrue(nothing, "expected deals with no free text at all")
        for b in nothing:
            self.assertEqual(b["evidence_chars"], 0, b["name"])
            self.assertFalse(b["has_evidence"], b["name"])


if __name__ == "__main__":
    if not has_credential():
        print("judgment-operational-selftest: " + SKIP)
        sys.exit(0)
    unittest.main(verbosity=2)
