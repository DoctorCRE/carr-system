#!/usr/bin/env python3
"""boot-budget-check.py — the permanent ceiling on what every session loads
before it does any work (WR-000019 slice S11, boot diet).

WHY. The standing-context boot payload used to recite ~205 rule gists on
every call, CLAUDE.md carries its own weight, and the MCP connector's
`initialize` response repeats a ~200-word instruction block once per
registration. Nothing capped the sum, so it only ever grew. This is that cap,
enforced the same way ops/enforcement-coverage-check.py and its neighbours
are: repository content only, no database, no network, no machine state --
so it runs the same under `env -i` on a bare checkout as it does on Joe's
Mac, and it is one of the "map checks" ops/ci.sh's inventory loop runs
directly against THIS repo (see ops/boot-budget-check-selftest.py for the one
that builds a synthetic fixture tree instead, per the established split).

NATIVE ENTRYPOINTS: Codex reads AGENTS.md; Claude reads CLAUDE.md plus the
AGENTS.md policy block hooks/worktree-self-plumb.py injects at SessionStart
(measured with that hook's own extractor). Charge the larger entrypoint plus
shared connector instructions and the default standing-context SUMMARY/pack-index
snapshot against the existing 10K ceiling.
AGENTS sections are printed on overage so consolidation has a named target.
The default scoped response is not the mandatory detail=boot read. Report that
boot's delivered full text and corpus index separately; its existing ceiling
and parity control remains ops/rule-boot-classes-check.py. Corpus counts never
mean rules loaded as full text. All store measurements are dated offline
snapshots, not claims about today's database. Client duplicate registration
costs are unobservable here and are not silently estimated.

THE FAILURE MESSAGE NEVER SUGGESTS RAISING THE BUDGET. An overage means
something is due for consolidation -- merge or retire a rule through the S7
triage (ops/config/rule-triage.v1.json) and the S10 amendment path
(amend-rule / retire-rule), trim the native standing file, or fix a known duplication. A
budget raise is Joe's decision alone; this check will not word its way
around that by suggesting one.

Usage:
    ./.venv/bin/python ops/boot-budget-check.py
"""
import importlib.util
import json
import os
import re
import sys

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CLAUDE_MD_PATH = os.path.join(REPO, "CLAUDE.md")
AGENTS_MD_PATH = os.path.join(REPO, "AGENTS.md")
MCP_JS_PATH = os.path.join(REPO, "mcp-server", "src", "mcp.js")
BUDGET_PATH = os.path.join(REPO, "ops", "config", "boot-budget.v1.json")
CORE_FIXTURE_PATH = os.path.join(REPO, "ops", "config", "boot-budget-core-fixture.v1.json")
SELF_PLUMB_PATH = os.path.join(REPO, "hooks", "worktree-self-plumb.py")

CONSOLIDATION_ADVICE = (
    "This is not a signal to raise the budget. The fix is consolidation: merge or\n"
    "retire redundant/stale rules through the S7 triage (ops/config/rule-triage.v1.json)\n"
    "and the S10 amendment path (amend-rule / retire-rule), trim the native standing file, or close a\n"
    "known duplication (see the WR-000019 slice S11 connector-dedup finding). Raising\n"
    "any number in ops/config/boot-budget.v1.json is Joe's decision alone, never a\n"
    "session's -- do not edit that file to make this check pass."
)


def file_bytes(path):
    with open(path, "rb") as fh:
        return len(fh.read())


def claude_policy_block_bytes(agents_md_path):
    """The AGENTS.md block Claude's SessionStart hook injects, via the hook itself."""
    spec = importlib.util.spec_from_file_location("worktree_self_plumb", SELF_PLUMB_PATH)
    hook = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(hook)
    return len(hook.delivery_policy_brief(os.path.dirname(agents_md_path)).encode("utf-8"))


def instruction_concat(expression, path, rail=""):
    """Decode the supported literal concatenation; unknown shapes fail closed."""
    literal = r'"(?:[^"\\]|\\.)*"'
    term = rf'(?:{literal}|RULE_DELIVERY_RAIL)'
    if not re.fullmatch(rf'\s*{term}(?:\s*\+\s*{term})*\s*', expression):
        raise ValueError(f"{path}: unsupported instructions expression")
    return "".join(rail if token == "RULE_DELIVERY_RAIL" else json.loads(token)
                   for token in re.findall(term, expression))


def connector_instructions_bytes(path):
    """Largest served full/Doc instruction string for ONE registration.

    Conditions and unselected profile notices are source code, not prompt text.
    The default full resource and the fixed Doc resource are measured separately,
    with the larger one charged against the existing ceiling.
    """
    with open(path, "r", encoding="utf-8") as fh:
        src = fh.read()
    rail = ""
    rail_match = re.search(r"const RULE_DELIVERY_RAIL = `(.*?)`;", src, re.S)
    if rail_match:
        rail = rail_match.group(1)
    if "instructions:" not in src:
        raise ValueError(f"{path}: no `instructions:` block found -- has the "
                          "initialize handler moved or been renamed?")
    start = src.index("instructions:")
    end_marker = "});"
    if end_marker not in src[start:]:
        raise ValueError(f"{path}: `instructions:` block never closes with `{end_marker}`")
    end = src.index(end_marker, start)
    chunk = src[start + len("instructions:"):end].strip().rstrip(",").strip()
    doc_prefix = 'profile === "doc" ? DOC_INSTRUCTIONS :'
    has_doc = chunk.startswith(doc_prefix)
    if has_doc:
        chunk = chunk[len(doc_prefix):].strip()
    # Full is the default registration. The optional narrow-profile notice
    # resolves to an empty string there; its comparison label is not served.
    chunk = re.sub(
        r'\+\s*\(profile === "full" \? "" : ` ACTIVE PROFILE: \$\{profile\}\.` '
        r'\+ \(PROFILE_NOTICE\[profile\] \|\| ""\)\)\s*$', "", chunk)
    joined = instruction_concat(chunk, path, rail)
    if not joined.strip():
        raise ValueError(f"{path}: extracted an empty instructions block -- "
                          "the string-literal shape probably changed")
    sizes = [len(joined.encode("utf-8"))]
    if has_doc:
        doc_path = os.path.join(os.path.dirname(path), "doc-profile.js")
        with open(doc_path, encoding="utf-8") as fh:
            doc_src = fh.read()
        literal = r'"(?:[^"\\]|\\.)*"'
        doc_match = re.search(
            rf'export const DOC_INSTRUCTIONS\s*=\s*({literal}(?:\s*\+\s*{literal})*)\s*;',
            doc_src)
        if not doc_match:
            raise ValueError(f"{doc_path}: DOC_INSTRUCTIONS literal not found")
        sizes.append(len(instruction_concat(doc_match.group(1), doc_path).encode("utf-8")))
    return max(sizes)


def core_payload_bytes(fixture_path):
    with open(fixture_path) as fh:
        fixture = json.load(fh)
    return fixture["delivered_summary_bytes"] + fixture["pack_index_bytes"]


def load_budget(path):
    with open(path) as fh:
        return json.load(fh)


def measure(claude_md_path=None, mcp_js_path=None,
            core_fixture_path=None, budget_path=None, agents_md_path=None):
    """Returns (budget_dict, surface_tokens_dict, total_tokens).

    Defaults resolve the module-level path constants AT CALL TIME (never
    baked into the signature) so a test can monkeypatch CLAUDE_MD_PATH et al.
    on this module and have main() actually pick it up -- a default bound at
    def-time would silently keep pointing at whatever the constant was when
    the module loaded, which is exactly the kind of untestable check this
    file exists to not be."""
    agents_md_path = agents_md_path or AGENTS_MD_PATH
    claude_md_path = claude_md_path or CLAUDE_MD_PATH
    mcp_js_path = mcp_js_path or MCP_JS_PATH
    core_fixture_path = core_fixture_path or CORE_FIXTURE_PATH
    budget_path = budget_path or BUDGET_PATH
    budget = load_budget(budget_path)
    bpt = float(budget.get("bytes_per_token", 3.5))
    surface_bytes = {
        "claude_md": file_bytes(claude_md_path),
        "claude_policy_block": claude_policy_block_bytes(agents_md_path),
        "agents_md": file_bytes(agents_md_path),
        "connector_instructions": connector_instructions_bytes(mcp_js_path),
        "core_payload": core_payload_bytes(core_fixture_path),
    }
    surface_tokens = {name: b / bpt for name, b in surface_bytes.items()}
    claude_entry = surface_tokens["claude_md"] + surface_tokens["claude_policy_block"]
    total_tokens = (max(claude_entry, surface_tokens["agents_md"])
                    + surface_tokens["connector_instructions"] + surface_tokens["core_payload"])
    return budget, surface_tokens, total_tokens, surface_bytes


def evaluate(budget, surface_tokens, total_tokens):
    """Returns a list of (surface_or_'TOTAL', measured, cap) tuples that are
    over budget. Empty list means pass."""
    over = []
    sub_budgets = budget.get("sub_budgets_tokens", {})
    for name, measured in surface_tokens.items():
        cap = sub_budgets.get(name)
        if cap is not None and measured > cap:
            over.append((name, measured, cap))
    total_cap = budget.get("total_budget_tokens")
    if total_cap is not None and total_tokens > total_cap:
        over.append(("TOTAL", total_tokens, total_cap))
    return over


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    budget, surface_tokens, total_tokens, surface_bytes = measure()

    print("BOOT BUDGET (WR-000019 slice S11)")
    for name in ("claude_md", "claude_policy_block", "agents_md",
                 "connector_instructions", "core_payload"):
        cap = budget.get("sub_budgets_tokens", {}).get(name)
        cap_s = f"(budget {cap})" if cap is not None else "(no sub-budget set)"
        print(f"  {name:24s} {surface_bytes[name]:7d} bytes  "
              f"~{surface_tokens[name]:8.1f} tokens  {cap_s}")
    total_cap = budget.get("total_budget_tokens")
    print(f"  {'TOTAL':24s} {'':7s}         ~{total_tokens:8.1f} tokens  "
          f"(budget {total_cap})")
    print("  TOTAL charges the larger native entrypoint (Claude: claude_md + "
          "claude_policy_block; Codex: agents_md) plus shared connector and summaries.")
    with open(CORE_FIXTURE_PATH) as fh:
        fixture = json.load(fh)
    print(f"  delivered summaries: {fixture['delivered_summary_bytes']} bytes; "
          f"pack/trigger index: {fixture['pack_index_bytes']} bytes (snapshot).")
    if "rule_boot_full_text_bytes" in fixture:
        boot_bytes = fixture["rule_boot_full_text_bytes"] + fixture["rule_boot_corpus_index_bytes"]
        print(f"  mandatory boot full text: {fixture['rule_boot_full_text_bytes']} bytes; "
              f"corpus index: {fixture['rule_boot_corpus_index_bytes']} bytes (snapshot). "
              "Their separate ceiling/parity check is ops/rule-boot-classes-check.py; "
              "corpus entries are not rules delivered as full text.")
        print(f"  combined snapshot estimate including mandatory boot: "
              f"~{total_tokens + boot_bytes / float(budget.get('bytes_per_token', 3.5)):.1f} tokens; "
              "the existing component ceilings apply separately.")
    print("  NOTE: connector_instructions is ONE registration's cost. This "
          "session's live tool list may carry it twice if the CARR connector "
          "is registered under two MCP prefixes -- see the WR-000019 slice "
          "S11 dedup finding; this check cannot see a client-side duplicate "
          "registration and does not estimate one.")

    over = evaluate(budget, surface_tokens, total_tokens)
    if over:
        print("\nBOOT BUDGET EXCEEDED:")
        for name, measured, cap in over:
            print(f"  {name}: ~{measured:.1f} tokens > budget {cap}")
        for name, path in (("agents_md", AGENTS_MD_PATH), ("claude_md", CLAUDE_MD_PATH)):
            if any(item[0] in (name, "TOTAL") for item in over):
                print(f"  {name} contributing sections:")
                for title, size in steering_sections(path):
                    print(f"    {title}: {size} bytes")
        print()
        print(CONSOLIDATION_ADVICE)
        return 1

    print("\nOK: within budget.")
    return 0


def steering_sections(path):
    """Count UTF-8 bytes under each heading, including preamble and heading."""
    sections = []
    title, size = "preamble", 0
    with open(path, encoding="utf-8") as fh:
        for line in fh:
            if re.match(r"^#{1,6} ", line):
                if size:
                    sections.append((title, size))
                title, size = line.strip(), 0
            size += len(line.encode("utf-8"))
    if size:
        sections.append((title, size))
    return sections


if __name__ == "__main__":
    sys.exit(main())
