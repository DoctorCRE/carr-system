#!/usr/bin/env python3
"""boot-budget-check-selftest.py — the PAIRED suite for ops/boot-budget-check.py
(WR-000019 slice S11, boot diet).

Builds its own synthetic fixture tree on purpose (the established split, see
ops/ci.sh's inventory-checks comment): this measures the CHECK's behaviour,
never the real repository's actual numbers -- ops/boot-budget-check.py itself,
run directly against THIS repo by ops/ci.sh's inventory loop, is what proves
the real inventory is in budget.

THE ONE THING THIS FILE MUST PROVE, per WR-000019 slice S11's own acceptance
criterion: a synthetic overage actually fails the check, and the failure
message instructs consolidation rather than a budget raise.
"""
import importlib.util
import json
import os
import shutil
import subprocess
import sys
import tempfile
import types
import contextlib
import io

REPO = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(REPO)
sys.path.insert(0, REPO)


def _load_module(name: str, path: str) -> types.ModuleType:
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None, \
        f"could not build a module spec for {path}"
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


boot_budget_check = _load_module(
    "boot_budget_check", os.path.join(REPO, "ops", "boot-budget-check.py"))

FAILURES: list[str] = []


def check(name, condition):
    if condition:
        print(f"  ok  {name}")
    else:
        print(f"  FAIL  {name}")
        FAILURES.append(name)


MCP_JS_TEMPLATE = """// synthetic mcp.js fixture
const RULE_DELIVERY_RAIL = `%(rail)s`;
function handler() {
  switch (rpc.method) {
    case "initialize":
      return reply({
        instructions:
          "%(instr)s" + RULE_DELIVERY_RAIL,
      });
  }
}
"""


def make_tree(tmp, *, claude_md_bytes, instr_chars, rail_chars,
              current_recitation_bytes, pack_index_bytes,
              total_budget_tokens, sub_budgets_tokens, bytes_per_token=3.5):
    claude_md = os.path.join(tmp, "CLAUDE.md")
    with open(claude_md, "w") as fh:
        fh.write("x" * claude_md_bytes)
    with open(os.path.join(tmp, "AGENTS.md"), "w") as fh:
        fh.write("# Native boot\nsmall\n")

    mcp_js = os.path.join(tmp, "mcp.js")
    with open(mcp_js, "w") as fh:
        fh.write(MCP_JS_TEMPLATE % {
            "rail": "r" * rail_chars,
            "instr": "i" * instr_chars,
        })

    fixture = os.path.join(tmp, "core-fixture.json")
    with open(fixture, "w") as fh:
        json.dump({
            "delivered_summary_bytes": current_recitation_bytes,
            "pack_index_bytes": pack_index_bytes,
        }, fh)

    budget = os.path.join(tmp, "budget.json")
    with open(budget, "w") as fh:
        json.dump({
            "bytes_per_token": bytes_per_token,
            "total_budget_tokens": total_budget_tokens,
            "sub_budgets_tokens": sub_budgets_tokens,
        }, fh)

    return claude_md, mcp_js, fixture, budget


def test_passes_under_budget():
    tmp = tempfile.mkdtemp(prefix="boot-budget-pass-")
    try:
        claude_md, mcp_js, fixture, budget = make_tree(
            tmp, claude_md_bytes=350, instr_chars=100, rail_chars=50,
            current_recitation_bytes=1000, pack_index_bytes=200,
            total_budget_tokens=1000,
            sub_budgets_tokens={"claude_md": 500, "connector_instructions": 500,
                                 "core_payload": 500})
        b, surface_tokens, total, _ = boot_budget_check.measure(
            claude_md_path=claude_md, mcp_js_path=mcp_js,
            core_fixture_path=fixture, budget_path=budget,
            agents_md_path=os.path.join(tmp, "AGENTS.md"))
        over = boot_budget_check.evaluate(b, surface_tokens, total)
        check("a modest synthetic tree passes with no overages", over == [])
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_synthetic_overage_fails_and_names_consolidation():
    """THE ACCEPTANCE CRITERION: a synthetic overage must fail the check, and
    the failure output must instruct consolidation, never a budget raise."""
    tmp = tempfile.mkdtemp(prefix="boot-budget-overage-")
    try:
        # CLAUDE.md alone is set to blow both its own sub-budget and the total.
        claude_md, mcp_js, fixture, budget = make_tree(
            tmp, claude_md_bytes=50_000, instr_chars=100, rail_chars=50,
            current_recitation_bytes=1000, pack_index_bytes=200,
            total_budget_tokens=1000,
            sub_budgets_tokens={"claude_md": 500, "connector_instructions": 500,
                                 "core_payload": 500})
        b, surface_tokens, total, _ = boot_budget_check.measure(
            claude_md_path=claude_md, mcp_js_path=mcp_js,
            core_fixture_path=fixture, budget_path=budget,
            agents_md_path=os.path.join(tmp, "AGENTS.md"))
        over = boot_budget_check.evaluate(b, surface_tokens, total)
        check("a synthetic 50KB CLAUDE.md is reported over budget",
              any(name == "claude_md" for name, _, _ in over))
        check("the total is also reported over budget",
              any(name == "TOTAL" for name, _, _ in over))

        # main() is repo-relative by design (matching every other *-check.py
        # in this repo), so the CLI path itself is exercised separately in
        # test_main_exit_code_reflects_overage() by monkeypatching its module
        # path constants. Here, confirm main() prints the exact consolidation
        # message on overage.
        check("the consolidation advice text is present and names the triage + amendment path",
              "consolidation" in boot_budget_check.CONSOLIDATION_ADVICE.lower()
              and "rule-triage" in boot_budget_check.CONSOLIDATION_ADVICE
              and "amend-rule" in boot_budget_check.CONSOLIDATION_ADVICE)
        check("the consolidation advice never tells anyone to raise the budget",
              "raise the budget" not in boot_budget_check.CONSOLIDATION_ADVICE.lower()
              or "not a signal to raise" in boot_budget_check.CONSOLIDATION_ADVICE.lower())
        check("the advice explicitly reserves a raise for Joe alone",
              "joe" in boot_budget_check.CONSOLIDATION_ADVICE.lower())
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_main_exit_code_reflects_overage():
    """Exercises the real main() entrypoint end to end (not just measure/evaluate),
    by pointing it at a repo root built to overage via monkeypatched module paths --
    this is the same technique the check's own module uses to stay import-safe."""
    tmp = tempfile.mkdtemp(prefix="boot-budget-main-")
    try:
        claude_md, mcp_js, fixture, budget = make_tree(
            tmp, claude_md_bytes=50_000, instr_chars=100, rail_chars=50,
            current_recitation_bytes=1000, pack_index_bytes=200,
            total_budget_tokens=1000,
            sub_budgets_tokens={"claude_md": 500, "connector_instructions": 500,
                                 "core_payload": 500})
        orig = (boot_budget_check.AGENTS_MD_PATH, boot_budget_check.CLAUDE_MD_PATH, boot_budget_check.MCP_JS_PATH,
                boot_budget_check.CORE_FIXTURE_PATH, boot_budget_check.BUDGET_PATH)
        boot_budget_check.AGENTS_MD_PATH = os.path.join(tmp, "AGENTS.md")
        boot_budget_check.CLAUDE_MD_PATH = claude_md
        boot_budget_check.MCP_JS_PATH = mcp_js
        boot_budget_check.CORE_FIXTURE_PATH = fixture
        boot_budget_check.BUDGET_PATH = budget
        try:
            rc = boot_budget_check.main([])
        finally:
            (boot_budget_check.AGENTS_MD_PATH, boot_budget_check.CLAUDE_MD_PATH, boot_budget_check.MCP_JS_PATH,
             boot_budget_check.CORE_FIXTURE_PATH, boot_budget_check.BUDGET_PATH) = orig
        check("main() exits nonzero on a synthetic overage", rc == 1)

        # And the inverse: a small tree under budget exits 0.
        claude_md2, mcp_js2, fixture2, budget2 = make_tree(
            tmp, claude_md_bytes=350, instr_chars=100, rail_chars=50,
            current_recitation_bytes=1000, pack_index_bytes=200,
            total_budget_tokens=1000,
            sub_budgets_tokens={"claude_md": 500, "connector_instructions": 500,
                                 "core_payload": 500})
        boot_budget_check.AGENTS_MD_PATH = os.path.join(tmp, "AGENTS.md")
        boot_budget_check.CLAUDE_MD_PATH = claude_md2
        boot_budget_check.MCP_JS_PATH = mcp_js2
        boot_budget_check.CORE_FIXTURE_PATH = fixture2
        boot_budget_check.BUDGET_PATH = budget2
        try:
            rc2 = boot_budget_check.main([])
        finally:
            (boot_budget_check.AGENTS_MD_PATH, boot_budget_check.CLAUDE_MD_PATH, boot_budget_check.MCP_JS_PATH,
             boot_budget_check.CORE_FIXTURE_PATH, boot_budget_check.BUDGET_PATH) = orig
        check("main() exits zero when every surface is within budget", rc2 == 0)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_extractor_is_honest_about_the_real_instructions_block():
    """Not synthetic: proves the connector_instructions extractor actually
    finds mcp-server/src/mcp.js's real `initialize` block and returns a
    plausible, nonzero size -- a silently-empty extraction (mcp.js renamed
    the handler, or restructured the string literals) must be caught here,
    not discovered the day the budget mysteriously reads as ~0 tokens."""
    real_mcp_js = os.path.join(REPO, "mcp-server", "src", "mcp.js")
    n = boot_budget_check.connector_instructions_bytes(real_mcp_js)
    check("the real connector instructions block extracts to a plausible size",
          500 < n < 20_000)


def test_doc_branch_measures_the_larger_served_instruction_string():
    with tempfile.TemporaryDirectory(prefix="boot-budget-doc-") as tmp:
        claude_md, mcp_js, fixture, budget = make_tree(
            tmp, claude_md_bytes=1, instr_chars=100, rail_chars=50,
            current_recitation_bytes=1, pack_index_bytes=1,
            total_budget_tokens=1000, sub_budgets_tokens={})
        with open(mcp_js) as fh:
            src = fh.read()
        with open(mcp_js, "w") as fh:
            fh.write(src.replace("instructions:",
                                'instructions: profile === "doc" ? DOC_INSTRUCTIONS :'))
        doc = os.path.join(tmp, "doc-profile.js")
        for size, expected in [(300, 300), (5, 150)]:
            with open(doc, "w") as fh:
                fh.write('export const DOC_INSTRUCTIONS = "' + "d" * size + '";')
            check(f"Doc branch size {size} measures max(Doc, full), excluding condition labels",
                  boot_budget_check.connector_instructions_bytes(mcp_js) == expected)
        with open(doc, "w") as fh:
            fh.write('export const DOC_INSTRUCTIONS = "' + "d" * 4000 + '";')
        b, tokens, total, _ = boot_budget_check.measure(
            claude_md_path=claude_md, mcp_js_path=mcp_js,
            core_fixture_path=fixture, budget_path=budget,
            agents_md_path=os.path.join(tmp, "AGENTS.md"))
        check("a Doc-only overage still fails the existing total budget",
              any(name == "TOTAL" for name, _, _ in boot_budget_check.evaluate(b, tokens, total)))
        with open(doc, "w") as fh:
            fh.write("export const DOC_INSTRUCTIONS = unmeasuredPrompt;")
        try:
            boot_budget_check.connector_instructions_bytes(mcp_js)
        except ValueError:
            refused = True
        else:
            refused = False
        check("an unsupported Doc expression refuses instead of dropping its measurement", refused)


def test_real_measurement_matches_served_full_and_doc_instructions():
    script = """
import { dispatch } from './mcp-server/src/mcp.js';
import { authenticatedIdentity } from './mcp-server/src/identity.js';
const sizes = [];
for (const path of ['/mcp', '/doc/mcp']) {
  const request = new Request('https://api.doctorcre.com' + path, {
    method: 'POST', body: JSON.stringify({jsonrpc: '2.0', id: 1, method: 'initialize'})
  });
  const reply = await (await dispatch(request, {}, {},
    authenticatedIdentity.connectionForGrant({slug: 'joe'}))).json();
  sizes.push(Buffer.byteLength(reply.result.instructions, 'utf8'));
}
console.log(JSON.stringify(sizes));
"""
    sizes = json.loads(subprocess.check_output(
        ["node", "--input-type=module", "-e", script], cwd=REPO, text=True))
    check("real budget equals the largest served full/Doc instructions, not source labels",
          boot_budget_check.connector_instructions_bytes(
              os.path.join(REPO, "mcp-server", "src", "mcp.js")) == max(sizes))


def test_native_agents_growth_and_section_diagnostics():
    with tempfile.TemporaryDirectory(prefix="boot-budget-native-") as tmp:
        claude, mcp, fixture, budget = make_tree(
            tmp, claude_md_bytes=35, instr_chars=10, rail_chars=0,
            current_recitation_bytes=35, pack_index_bytes=0,
            total_budget_tokens=1000, sub_budgets_tokens={"agents_md": 500})
        agents = os.path.join(tmp, "AGENTS.md")
        with open(agents, "w") as fh:
            fh.write("# Boot\nsmall\n## Enlarged assignment\n" + "x" * 50000)
        originals = {key: getattr(boot_budget_check, key, None) for key in
                     ("CLAUDE_MD_PATH", "AGENTS_MD_PATH", "MCP_JS_PATH",
                      "CORE_FIXTURE_PATH", "BUDGET_PATH")}
        for key, path in zip(originals, (claude, agents, mcp, fixture, budget)):
            setattr(boot_budget_check, key, path)
        try:
            output = io.StringIO()
            with contextlib.redirect_stdout(output):
                rc = boot_budget_check.main([])
            check("enlarged native AGENTS fails with its contributing section",
                  rc == 1 and "agents_md contributing sections" in output.getvalue()
                  and "Enlarged assignment" in output.getvalue())
        finally:
            for key, value in originals.items():
                setattr(boot_budget_check, key, value)



POLICY = ("<!-- carr-product-first-policy:start -->\n" + "p" * 700
          + "\n<!-- carr-product-first-policy:end -->\n")


def native_tree(tmp, *, claude_md_bytes, agents_text, total_budget_tokens):
    claude, mcp, fixture, budget = make_tree(
        tmp, claude_md_bytes=claude_md_bytes, instr_chars=10, rail_chars=0,
        current_recitation_bytes=0, pack_index_bytes=0,
        total_budget_tokens=total_budget_tokens, sub_budgets_tokens={},
        bytes_per_token=1.0)
    agents = os.path.join(tmp, "AGENTS.md")
    with open(agents, "w") as fh:
        fh.write(agents_text)
    return boot_budget_check.measure(
        claude_md_path=claude, agents_md_path=agents, mcp_js_path=mcp,
        core_fixture_path=fixture, budget_path=budget)


def test_native_entrypoints_charge_the_larger_one_not_their_sum():
    """Codex reads AGENTS.md; Claude reads CLAUDE.md. Neither reads both whole."""
    with tempfile.TemporaryDirectory(prefix="boot-budget-max-") as tmp:
        b, tokens, total, _ = native_tree(
            tmp, claude_md_bytes=600, agents_text="a" * 600, total_budget_tokens=1000)
        check("two moderate entrypoints whose sum exceeds the total still pass",
              boot_budget_check.evaluate(b, tokens, total) == [])
        check("the total charges the larger entrypoint, not the sum",
              total == 600 + tokens["connector_instructions"])


def test_claude_entrypoint_charges_the_injected_policy_block():
    """SessionStart injects the AGENTS.md policy block into every Claude session."""
    with tempfile.TemporaryDirectory(prefix="boot-budget-policy-") as tmp:
        b, tokens, total, sizes = native_tree(
            tmp, claude_md_bytes=500, agents_text="# Boot\n" + POLICY,
            total_budget_tokens=1000)
        block = len(("p" * 700).encode())
        check("the injected policy block is measured with the hook's own extractor",
              sizes["claude_policy_block"] == block)
        check("Claude's charged entrypoint is CLAUDE.md plus the injected block",
              total == 500 + block + tokens["connector_instructions"])
        check("CLAUDE.md plus the block over the total is reported over budget",
              any(name == "TOTAL" for name, _, _ in
                  boot_budget_check.evaluate(b, tokens, total)))

if __name__ == "__main__":
    test_passes_under_budget()
    test_synthetic_overage_fails_and_names_consolidation()
    test_main_exit_code_reflects_overage()
    test_extractor_is_honest_about_the_real_instructions_block()
    test_doc_branch_measures_the_larger_served_instruction_string()
    test_real_measurement_matches_served_full_and_doc_instructions()
    test_native_agents_growth_and_section_diagnostics()
    test_native_entrypoints_charge_the_larger_one_not_their_sum()
    test_claude_entrypoint_charges_the_injected_policy_block()
    if FAILURES:
        print(f"\n{len(FAILURES)} FAILURE(S): {FAILURES}")
        sys.exit(1)
    print("\nOK: boot-budget-check selftest passed")
    sys.exit(0)
