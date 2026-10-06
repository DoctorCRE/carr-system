#!/usr/bin/env python3
"""sync-rule-boot-classes.py — regenerate mcp-server/src/rule-boot-classes.js
from ops/config/rule-classes.v1.json, and guard the rule boot budget.

WHY A GENERATED MODULE. standing-context's `detail: "boot"` mode (the gated
rule boot, see mcp-server/src/rule-boot.js) renders an index of every active
rule plus the full text of the always-on set. The class, the <=20-word
summary and the "when it applies" line for each rule are committed data in
ops/config/rule-classes.v1.json; rule STATEMENTS never enter class metadata and
are read from the store at request time. Validation reads the existing committed
rule-selection corpus to reject action/topic classes for supported standing
identity facts. A Cloudflare Worker has no
filesystem at request time, so the class data ships as this checked-in
module, the same pattern as ops/sync-core-rule-ids.py.

THIS SCRIPT IS THE ONLY WAY THE MODULE IS MEANT TO BE PRODUCED, and --check is
the same code path, so the write and the parity check cannot drift apart
(rule a8c55a47).

THE BUDGET GUARD. The boot text is delivered to every session and every
subagent before its first ordinary tool call, so its size is paid on every
context. --check also estimates the rendered size from the committed `chars`
(statement length at classification; a length, never text) and FAILS when the
index plus the always-on text exceeds `budget_tokens` (chars / chars_per_token),
naming the largest always-on rules. It never suggests truncating: an overage
is resolved by consolidating rules or by a budget decision that is Joe's.

EVERY SCOPE IS MEASURED: unsponsored, every partner named in `sponsors`
(Dell and Joe today) and any partner a classified rule is personal to. And
EVERY ACTIVE RULE MUST BE CLASSIFIED: an active id in
ops/config/rule-enforcement-map.json that is not in the class file would
render as class U in full text, outside this estimate, so --check fails on it.
The one exclusion is the rule_surface "intro_politics" rules, which the verb's
query leaves out of the boot.

Usage:
    ./.venv/bin/python ops/sync-rule-boot-classes.py            # regenerate
    ./.venv/bin/python ops/sync-rule-boot-classes.py --check    # parity + budget; exit 1 on either
"""
import argparse
import hashlib
import json
import os
import re
import sys

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CLASSES_PATH = os.path.join(REPO, "ops", "config", "rule-classes.v1.json")
MAP_PATH = os.path.join(REPO, "ops", "config", "rule-enforcement-map.json")
CORPUS_PATH = os.path.join(REPO, "ops", "config", "rule-selection-corpus.v1.json")
EXCLUDED_SURFACES = {"intro_politics"}
OUT_PATH = os.path.join(REPO, "mcp-server", "src", "rule-boot-classes.js")
CLASSES = {"a", "b", "c", "d", "e"}
MAX_SUMMARY_WORDS = 20
# Must match mcp-server/src/rule-boot.js's layout: one always-on entry is
# "### <id>[ (personal)]\n<statement>\n\n", one index line is
# "<id> | <CLASS> | <summary> | <when>\n". PREAMBLE_CHARS is a fixed allowance
# for the headers and instructions the renderer prints once.
PREAMBLE_CHARS = 2400


def load(path=CLASSES_PATH):
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)


def is_standing_fact(statement):
    """Recognize bounded assertions of standing business/partner identity.

    Decision order: remove quoted/code examples; reject conditional, hypothetical
    or prohibited assertions; then match a supported fact shape. Merely naming a
    partner, territory or Doc in an action instruction is insufficient. Mixed
    rules qualify when they also declare an independent standing fact. This is
    deliberately not a general natural-language classifier: unmatched shapes
    still require review. No rule ids or summaries participate in the judgment.
    """
    if not isinstance(statement, str):
        return False
    text = re.sub(r"```[\s\S]*?```|`[^`]*`|\"[^\"]*\"|“[^”]*”|‘[^’]*’|(?<!\w)'[^'\n]+'(?!\w)",
                  " ", statement)
    partner = r"(?:joe|dell|(?:the|both|our) partners?)"
    pair = r"(?:joe\s*(?:and|&)\s*dell|dell\s*(?:and|&)\s*joe|both partners|our partners)"
    shapes = (
        rf"\b{pair}\s+are\s+(?:both\s+)?(?:business partners|visual thinkers|early[- ]stage)\b",
        r"\b(?:the\s+)?team\s+(?:is|means|consists of)\s+joe\s*(?:and|&)\s*dell\b",
        rf"\b{partner}\s+is\s+(?:the|our|an?)\s+(?:ai/system[- ]design|system[- ]design|business|brokerage)\s+partner\b",
        rf"\b{partner}\s+is\s+(?:an?\s+)?(?:licensed\s+)?(?:broker|realtor)\b",
        r"\b(?:our|the team'?s|the shared)\s+territory\s+(?:is|covers|extends|runs|spans)\s+\S",
        r"\bthe territory\s+is\s+(?:the\s+)?team'?s\b",
        rf"\b{partner}\s+holds\s+(?:an?\s+)?(?:[a-z]+\s+){{0,3}}licen[cs]e\b",
        rf"\b{partner}'s\s+licensure\s+is\s+(?:the\s+)?team'?s\b",
        r"\b(?:the\s+)?(?:vendor network|team network)\s+(?:is\s+(?:the\s+)?team'?s|belongs to\s+(?:the|our)\s+team|is\s+owned by\s+(?:the|our)\s+team)\b",
        r"\b(?:the\s+)?(?:persona|assistant)\s+(?:is named|is called|goes by)\s+\S",
        r"\bcarr\s+(?:represents\s+(?:buyers and tenants|tenants and buyers)\s+only|never represents\s+(?:landlords|sellers))\b",
        r"\bno[- ]conflict\s+(?:tenant/buyer|buyer/tenant)[- ]only\s+model\b",
        rf"\b{partner}\s+(?:will never be able to|cannot|can't)\s+(?:hand[- ]feed\s+the system|manually\s+(?:report|log|feed)\s+(?:every|all)\s+(?:activity|activities|touches))\b",
        rf"\b{partner}\s+values\s+being able to\s+[^.!?]{{0,90}}\b(?:phone|mobility)\b",
        r"\bconcept coherence\s+is\s+(?:his|her|joe'?s|dell'?s|the partner'?s)\s+(?:edge|strength)\b",
        r"\b(?:the|our)\s+(?:practice|business|team)\s+operates from\s+an?\s+[a-z-]+\s+mindset\b",
        rf"\bcalm\s+is defined by\s+{partner}\b",
        rf"\b{partner}\s+has granted standing permission\s+[^.!?]{{0,90}}\b(?:motion|interactive|effects)\b",
        r"\bprospects\s+are\s+healthcare experts unfamiliar with\s+cre\b",
    )
    for sentence in re.split(r"[.!?\n]+", text.lower()):
        sentence = sentence.strip()
        if re.search(r"\b(?:if|unless|suppose|assuming|imagine|hypothetical|example|wrong|banned)\b", sentence):
            continue
        if re.match(r"(?:when|before|after|while)\b", sentence):
            continue
        if re.search(r"\b(?:(?:never|do not|don't)\s+(?:say|assume|assert|claim|state|write|infer)|"
                     r"(?:false|untrue) that|not true that)\b", sentence):
            continue
        if any(re.search(shape, sentence) for shape in shapes):
            return True
    return False


def corpus_statements(path=CORPUS_PATH):
    """Use the existing selection corpus; class metadata must not contain text."""
    with open(path, encoding="utf-8") as fh:
        corpus = json.load(fh)
    return {row["id"]: row["statement"] for row in corpus["rules"]}


def validate(doc, statements=None):
    problems = []
    rules = doc.get("rules")
    if not isinstance(rules, dict) or not rules:
        return ["rules must be a non-empty object"]
    if statements is None:
        try:
            statements = corpus_statements()
        except (OSError, ValueError, KeyError, TypeError) as exc:
            return [f"standing fact validation: cannot read selection corpus: {exc}"]
    for rid, row in sorted(rules.items()):
        if len(rid) != 8 or any(ch not in "0123456789abcdef" for ch in rid):
            problems.append(f"{rid}: id must be the 8-char lowercase hex short form")
        if row.get("class") not in CLASSES:
            problems.append(f"{rid}: class must be one of {sorted(CLASSES)}")
        summary = row.get("summary")
        if not isinstance(summary, str) or not summary.strip():
            problems.append(f"{rid}: summary missing")
        elif len(summary.split()) > MAX_SUMMARY_WORDS:
            problems.append(f"{rid}: summary is {len(summary.split())} words (max {MAX_SUMMARY_WORDS})")
        if not isinstance(row.get("when"), str) or not row["when"].strip():
            problems.append(f"{rid}: when missing")
        if not isinstance(row.get("always_on"), bool):
            problems.append(f"{rid}: always_on must be a boolean")
        if row.get("class") == "a" and not row.get("always_on"):
            problems.append(f"{rid}: class a is always on by definition")
        if not isinstance(row.get("chars"), int) or row["chars"] <= 0:
            problems.append(f"{rid}: chars must be a positive integer")
        if "statement" in row or "human_quote" in row:
            problems.append(f"{rid}: rule text must never be committed here")
        statement = statements.get(rid)
        if row.get("class") in {"b", "c"} and (not isinstance(statement, str) or not statement.strip()):
            problems.append(f"{rid}: class {row['class']} requires a non-empty corpus statement "
                            "to validate that no standing fact is deferred")
        if is_standing_fact(statement) and (row.get("class") != "a" or row.get("always_on") is not True):
            problems.append(f"{rid}: standing fact requires class a and always_on=true; "
                            "action/topic metadata cannot defer standing identity")
    return problems


def classes_digest(doc):
    body = json.dumps(doc.get("rules"), sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return "sha256:" + hashlib.sha256(body.encode("utf-8")).hexdigest()


def render(doc):
    rules = doc["rules"]
    entries = []
    for rid in sorted(rules):
        row = rules[rid]
        value = {"cls": row["class"], "on": True, "summary": row["summary"], "when": row["when"]}
        if row.get("personal_to"):
            value["personal_to"] = row["personal_to"]
        entries.append(f"  {json.dumps(rid)}: Object.freeze({json.dumps(value, ensure_ascii=False, sort_keys=True)}),")
    lines = [
        "// GENERATED -- do not hand-edit.",
        "// Source: ops/config/rule-classes.v1.json",
        "// Regenerate with: ./.venv/bin/python ops/sync-rule-boot-classes.py",
        "// Drift + budget check (CI): ./.venv/bin/python ops/sync-rule-boot-classes.py --check",
        "//",
        "// The gated rule boot (standing-context detail \"boot\", mcp-server/src/rule-boot.js)",
        "// reads this for each rule's class, always-on flag, <=20-word summary and",
        "// when-it-applies line. Rule statements are NOT here: they come from the store",
        "// at request time. A Worker has no filesystem, so this is a checked-in module.",
        "",
        "export const RULE_BOOT_CLASSES_SOURCE = \"ops/config/rule-classes.v1.json\";",
        f"export const RULE_BOOT_CLASSES_DIGEST = {json.dumps(classes_digest(doc))};",
        f"export const RULE_BOOT_BUDGET_TOKENS = {int(doc.get('budget_tokens', 40000))};",
        f"export const RULE_BOOT_CHARS_PER_TOKEN = {float(doc.get('chars_per_token', 3.6))};",
        "",
        "export const RULE_BOOT_CLASSES = Object.freeze({",
        *entries,
        "});",
        "",
    ]
    return "\n".join(lines)


def estimate(doc, sponsor=None):
    """(total_chars, tokens, always_on rows sorted largest first) for one sponsor's view."""
    rules = doc["rules"]
    total = PREAMBLE_CHARS
    big = []
    for rid in sorted(rules):
        row = rules[rid]
        owner = row.get("personal_to")
        if owner and owner != sponsor:
            continue
        total += len(f"{rid} | {row['class'].upper()} | {row['summary']} | {row['when']}\n")
        header = f"### {rid}{' (personal)' if owner else ''}\n"
        total += len(header) + row["chars"] + 2
        big.append((row["chars"], rid))
    big.sort(reverse=True)
    per_token = float(doc.get("chars_per_token", 3.6))
    return total, total / per_token, big


def budget_findings(doc):
    limit = int(doc.get("budget_tokens", 40000))
    sponsors = sorted({r.get("personal_to") for r in doc["rules"].values() if r.get("personal_to")}
                      | set(doc.get("sponsors") or []))
    findings = []
    for sponsor in [None, *sponsors]:
        chars, tokens, big = estimate(doc, sponsor)
        label = sponsor or "unsponsored"
        if tokens > limit:
            largest = ", ".join(f"{rid} ({n} chars)" for n, rid in big[:10])
            findings.append(
                f"RULE BOOT OVER BUDGET for {label}: ~{tokens:,.0f} tokens ({chars:,} chars) "
                f"> {limit:,}. Largest always-on rules: {largest}. Consolidate or retire rules "
                "(amend-rule / retire-rule); never truncate the boot text.")
        else:
            findings.append(f"ok  {label}: ~{tokens:,.0f} of {limit:,} tokens ({chars:,} chars)")
    return findings


def coverage_findings(doc, map_path=MAP_PATH):
    """Every active rule in the enforcement map is classified, in its scope."""
    try:
        with open(map_path, encoding="utf-8") as fh:
            mapping = json.load(fh)
    except (OSError, ValueError) as exc:
        return [f"RULE BOOT COVERAGE: cannot read {os.path.relpath(map_path, REPO)}: {exc}"]
    controls = mapping.get("rule_controls") or {}
    excluded = {k[:8] for k, v in controls.items()
                if isinstance(v, dict) and v.get("rule_surface") in EXCLUDED_SURFACES}
    rules = doc["rules"]
    known = set(doc.get("sponsors") or [])
    findings = []
    for scope, ids in sorted((mapping.get("active_rule_ids") or {}).items()):
        owner = None if scope == "shared" else scope
        if owner and owner not in known:
            findings.append(f"RULE BOOT COVERAGE: partner {owner!r} has active rules but is not in "
                            "`sponsors`, so its scope is not measured against the budget")
        loose = sorted(i[:8] for i in ids if i[:8] not in excluded
                       and (i[:8] not in rules or (rules[i[:8]].get("personal_to") or None) != owner))
        if loose:
            findings.append(f"RULE BOOT COVERAGE: {len(loose)} active {scope} rule(s) are not classified "
                            f"for that scope and would render as class U in full text, outside the budget: "
                            f"{', '.join(loose[:20])}. Classify them in ops/config/rule-classes.v1.json.")
    return findings


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--check", action="store_true")
    ap.add_argument("--classes", default=CLASSES_PATH)
    ap.add_argument("--out", default=OUT_PATH)
    args = ap.parse_args(argv)
    doc = load(args.classes)
    problems = validate(doc)
    if problems:
        print("rule-classes.v1.json INVALID:\n  " + "\n  ".join(problems))
        return 1
    rendered = render(doc)
    if not args.check:
        with open(args.out, "w", encoding="utf-8") as fh:
            fh.write(rendered)
        print(f"wrote {os.path.relpath(args.out, REPO)} ({len(doc['rules'])} rules)")
        return 0
    rc = 0
    try:
        with open(args.out, encoding="utf-8") as fh:
            current = fh.read()
    except OSError:
        current = None
    if current != rendered:
        print(f"STALE: {os.path.relpath(args.out, REPO)} does not match "
              "ops/config/rule-classes.v1.json. Regenerate: "
              "./.venv/bin/python ops/sync-rule-boot-classes.py")
        rc = 1
    for line in budget_findings(doc) + coverage_findings(doc):
        print(line)
        if line.startswith(("RULE BOOT OVER BUDGET", "RULE BOOT COVERAGE")):
            rc = 1
    if rc == 0:
        print(f"rule-boot classes: module in parity with {len(doc['rules'])} classified rules")
    return rc


if __name__ == "__main__":
    sys.exit(main())
