#!/usr/bin/env python3
"""Pins the post-merge BLOCK finding on PR #1342 at main (fix-forward carrier).

Fixed by PR #1346 (0bd7a9aa) in mcp-server/src/salesforce-browser-read-rw02.v5.js.
The BLOCK (issuecomment-5856087394, upheld by issuecomment-5856272215):

P1  the `log_in_permitted` and `code_autofill_permitted` branches clicked
    before the page ladder ran, so a wrong origin, org, signed-in account,
    layout or policy on a sign-in page was clicked through and discarded,
    and a later normal page recorded the run as clean with no page-stop
    receipt. The same held for a contradictory challenge on that page.

These tests replay the reviewer's reproduction through the exported
runSalesforceBrowserReadReconciliation (via the committed synthetic fixtures)
for both the password and the code step. The expected login and MFA
challenges must still sign in, so the fix is not "refuse every sign-in".
Synthetic data only: no browser, no Salesforce, no network.
"""
import json
import shutil
import subprocess
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "mcp-server" / "src" / "salesforce-browser-read-rw02.v5.js"
FIXTURES = ROOT / "mcp-server" / "test" / "salesforce-browser-read-rw02.fixtures.mjs"

MISMATCHES = {
    "origin_mismatch": {"observation": {"origin": "https://wrong-origin.invalid"}},
    "org_mismatch": {"observation": {"org_id": "00Dwrong"}},
    "signed_in_account_mismatch": {"observation": {"signed_in_account_ref": "wrong-seat"}},
    "ui_drift": {"ui": {"layout_ref": "changed-layout"}},
    "policy_conflict": {"observation": {"policy_conflicts": ["read_forbidden"]}},
}
CHALLENGES = {"captcha": "authentication_challenge", "unstated": "challenge_state_unobservable",
              "session_expired": "authentication_challenge"}

# One run per case: a password page, a code page, then a normal page. The
# case's defect is placed on the step under test. Prints, per case, the clicks
# made, the outcome, the stop reason and whether anything recorded "clean".
NODE_PROBE = r"""
const mod = await import(%s);
const { FakeReaderDriver, FakeCodeDriver, FakeClockClient, SpyRecorder, page, runWith } = await import(%s);
const cases = JSON.parse(process.argv[1]);
const out = {};
for (const [name, c] of Object.entries(cases)) {
  const log = [];
  const recorder = new SpyRecorder();
  const on = step => c.step === step ? c.defect : {};
  const password = page({ ...on("password"),
    sign_in: { state: "password_prompt", credentials_autofilled: true },
    observation: { ...(c.ok_challenges ? { challenge: "login_required" } : {}), ...(on("password").observation || {}) } });
  const code = page({ ...on("code"), sign_in: { state: "code_prompt" },
    observation: { ...(c.ok_challenges ? { challenge: "mfa_challenge" } : {}), ...(on("code").observation || {}) } });
  const run = await runWith(mod, [], { reader: new FakeReaderDriver([[password, code, page()]]), recorder,
    signInOperator: new FakeCodeDriver({ log, fillOn: [1] }),
    serverClock: mod.createServerClock(new FakeClockClient()) });
  out[name] = { clicks: log.filter(m => m.startsWith("click")), decision: run.decision,
    run_outcome: run.run_outcome ?? null, reason_id: run.reason_id ?? null,
    page_stops: recorder.calls.filter(x => x.verb === "record-salesforce-page-stop").length,
    clean: recorder.calls.some(x => x.args && x.args.outcome === "clean") };
}
console.log(JSON.stringify(out));
"""


@unittest.skipUnless(shutil.which("node"), "node is not installed")
class Rw02SignInGuard(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cases = {}
        for step in ("password", "code"):
            for reason, defect in MISMATCHES.items():
                cases[f"{step}:{reason}"] = {"step": step, "defect": defect}
            for challenge in CHALLENGES:
                cases[f"{step}:challenge:{challenge}"] = {
                    "step": step, "defect": {"observation": {"challenge": challenge}}}
        cases["expected-challenges"] = {"step": None, "defect": {}, "ok_challenges": True}
        script = NODE_PROBE % (json.dumps(SRC.as_uri()), json.dumps(FIXTURES.as_uri()))
        p = subprocess.run(["node", "--input-type=module", "-e", script, json.dumps(cases)],
                           capture_output=True, text=True, timeout=60)
        if p.returncode != 0:
            raise AssertionError(p.stderr)
        cls.got = json.loads(p.stdout)

    def assertStoppedBeforeClick(self, name, reason_id):
        got = self.got[name]
        # A defect on the password page allows no click at all; on the code
        # page only the earlier, safe Log in press.
        allowed = [] if name.startswith("password:") else ["clickLogIn"]
        self.assertEqual(got["clicks"], allowed, f"{name}: clicked on the unsafe page: {got}")
        self.assertEqual(got["run_outcome"], "stopped", f"{name}: {got}")
        self.assertEqual(got["reason_id"], reason_id, f"{name}: {got}")
        self.assertEqual(got["page_stops"], 1, f"{name}: the unsafe observation is not receipted: {got}")
        self.assertFalse(got["clean"], f"{name}: recorded clean: {got}")

    def test_p1_page_mismatch_stops_before_sign_in_click(self):
        for step in ("password", "code"):
            for reason in MISMATCHES:
                with self.subTest(step=step, reason=reason):
                    self.assertStoppedBeforeClick(f"{step}:{reason}", reason)

    def test_p1_contradictory_challenge_stops_before_sign_in_click(self):
        for step in ("password", "code"):
            for challenge, reason in CHALLENGES.items():
                with self.subTest(step=step, challenge=challenge):
                    self.assertStoppedBeforeClick(f"{step}:challenge:{challenge}", reason)

    def test_expected_login_and_mfa_challenges_still_sign_in(self):
        got = self.got["expected-challenges"]
        self.assertEqual(got["decision"], "reconciled", got)
        self.assertIn("clickLogIn", got["clicks"])
        self.assertIn("clickCodeField", got["clicks"])


if __name__ == "__main__":
    unittest.main()
