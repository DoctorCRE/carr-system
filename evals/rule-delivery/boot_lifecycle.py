#!/usr/bin/env python3
"""Measure rendered rule text and the real boot hook across context epochs.

No model is called. Character-derived token estimates measure rendered payload,
not deployed model consumption or behavioral compliance. Corpus text and page
answers remain in temporary files; the report contains IDs, hashes and counts.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))
from lib.rule_boot_gate import boot_delivery, _utf16_len


def digest(data):
    return hashlib.sha256(data).hexdigest()


def render(tree, rows, sponsor):
    script = """
const { ruleBootPage } = await import(process.argv[1]);
let input = ''; for await (const chunk of process.stdin) input += chunk;
const {rows, sponsor} = JSON.parse(input);
const first = await ruleBootPage(rows, sponsor, 1);
const pages = [first];
for (let page = 2; page <= first.pages_total; page++) pages.push(await ruleBootPage(rows, sponsor, page));
process.stdout.write(JSON.stringify(pages));
"""
    proc = subprocess.run(["node", "--input-type=module", "-e", script,
                           (tree / "mcp-server/src/rule-boot.js").as_uri()],
                          input=json.dumps({"rows": rows, "sponsor": sponsor}),
                          capture_output=True, text=True, check=True, cwd=tree)
    return json.loads(proc.stdout)


def lifecycle(tree, pages, source, state, stub, session):
    env = {**os.environ, "CARR_RULE_BOOT_STATE_DIR": str(state),
           "CARR_RULE_BOOT_FETCH_STUB": str(stub),
           "CARR_HOOK_GUARD_LOG": str(state.parent / "guard.log")}
    arm_code = "from lib.rule_boot_gate import arm_session; arm_session('" + session + "','" + source + "')"
    subprocess.run([sys.executable, "-c", arm_code], cwd=tree, env=env, check=True)
    payload = {"session_id": session, "cwd": str(tree)}

    def hook(event, tool, tool_input, response=None):
        call = dict(payload, hook_event_name=event, tool_name=tool, tool_input=tool_input)
        if response is not None:
            call["tool_response"] = response
        proc = subprocess.run([sys.executable, str(tree / "hooks/rule-boot-gate.py")],
                              input=json.dumps(call), capture_output=True, text=True,
                              env=env, cwd=tree, check=True)
        return json.loads(proc.stdout)["hookSpecificOutput"] if proc.stdout.strip() else {}

    actions = [("Read", {"file_path": str(tree / "AGENTS.md")}),
               ("Bash", {"command": "git push origin HEAD"}),
               ("Agent", {"prompt": "Inspect the diagnosis"})]
    held_before = all(hook("PreToolUse", tool, args).get("permissionDecision") == "deny"
                      for tool, args in actions)
    for page in pages:
        command = "./run.sh call standing-context '" + json.dumps({"detail": "boot", "page": page["page"]}) + "' | jq -r '.rule_boot.digest'"
        hook("PreToolUse", "Bash", {"command": command})
        hook("PostToolUse", "Bash", {"command": command}, {"stdout": page["digest"], "stderr": ""})
    digest_only_held = all(hook("PreToolUse", tool, args).get("permissionDecision") == "deny"
                           for tool, args in actions)
    incomplete_held = True
    received = {}
    for page in pages:
        args = {"detail": "boot", "page": page["page"]}
        hook("PreToolUse", "mcp__carr__standing_context", args)
        hook("PostToolUse", "mcp__carr__standing_context", args, {"ok": True, "rule_boot": page})
        received[page["page"]] = page
        if page["page"] < len(pages):
            incomplete_held &= all(hook("PreToolUse", tool, args).get("permissionDecision") == "deny"
                                   for tool, args in actions)
    allowed_after = all(hook("PreToolUse", tool, args).get("permissionDecision") != "deny"
                        for tool, args in actions)
    delivered = boot_delivery(received)
    assert held_before and digest_only_held and incomplete_held and allowed_after and delivered is not None
    return {"source": source, "held_before": held_before, "digest_only_held": digest_only_held,
            "incomplete_held": incomplete_held, "allowed_after": allowed_after,
            "delivered_rules": len(delivered),
            "delivered_ids_sha256": digest(json.dumps(sorted(delivered)).encode())}


def measure(baseline_ref, corpus, sponsor):
    raw = corpus.read_bytes()
    doc = json.loads(raw)
    classification = json.loads((REPO / "ops/config/rule-classes.v1.json").read_text())
    classes = classification["rules"]
    rows = [{"id": row["id"], "statement": row["statement"],
             "personal_to": row.get("personal_to") or (row.get("scope") or {}).get("personal_to")
             or (classes.get(row["id"][:8]) or {}).get("personal_to")}
            for row in doc["rules"] if (row.get("scope") or {}).get("kind") != "intro_politics"]
    expected = {r["id"][:8]: (r["statement"].strip(), bool(r["personal_to"]))
                for r in rows if r["personal_to"] in (None, sponsor)}
    cases_path = REPO / "ops/fixtures/rule-delivery-eval/cases.v2.json"
    cases = [c for c in json.loads(cases_path.read_text())["cases"] if c["split"] == "test"]
    ref = subprocess.check_output(["git", "rev-parse", baseline_ref + "^{commit}"], cwd=REPO, text=True).strip()
    report = {"schema": "rule-boot-lifecycle-measurement/v1", "baseline_ref": ref,
              "corpus_sha256": digest(raw), "corpus_rules": len(rows), "sponsor": sponsor,
              "frozen_cases_sha256": digest(cases_path.read_bytes()), "test_cases": len(cases),
              "model": "deterministic-no-model", "token_measure": "rendered UTF-16 chars / 3.6; not deployed consumption",
              "source_sha256": {rel: digest((REPO / rel).read_bytes()) for rel in
                                ("mcp-server/src/rule-boot.js", "mcp-server/src/rule-boot-classes.js",
                                 "lib/rule_boot_gate.py", "hooks/rule-boot-gate.py",
                                 "ops/config/rule-classes.v1.json",
                                 "evals/rule-delivery/boot_lifecycle.py")}, "arms": {}}
    with tempfile.TemporaryDirectory(prefix="rule-boot-lifecycle-") as folder:
        work = Path(folder)
        base = work / "baseline"
        (base / "mcp-server/src").mkdir(parents=True)
        (base / "package.json").write_text('{"type":"module"}')
        for name in ("rule-boot.js", "rule-boot-classes.js"):
            rel = "mcp-server/src/" + name
            (base / rel).write_bytes(subprocess.check_output(["git", "show", ref + ":" + rel], cwd=REPO))
        for arm, tree in (("baseline", base), ("candidate", REPO)):
            pages = render(tree, rows, sponsor)
            text = "".join(p["text"] for p in pages)
            delivered = boot_delivery({p["page"]: p for p in pages})
            assert delivered is not None
            retained = {rid for rid, (statement, personal) in expected.items() if rid in delivered
                        and f"### {rid}{' (personal)' if personal else ''}\n{statement}\n" in text}
            discoverable = {rid for rid in expected if f"{rid} | " in text}
            required = sum(len(c["gold"]) for c in cases)
            hits = sum(len(set(c["gold"]) & retained) for c in cases)
            index_hits = sum(len(set(c["gold"]) & discoverable) for c in cases)
            stub = work / (arm + "-page.json")
            stub.write_text(json.dumps({"ok": True, "rule_boot": pages[0]}))
            state = work / (arm + "-state")
            report["arms"][arm] = {
                "digest": pages[0]["digest"], "total_chars": pages[0]["total_chars"],
                "approx_tokens": pages[0]["approx_tokens"], "pages": len(pages),
                "max_json_page_chars": max(_utf16_len(json.dumps(p, ensure_ascii=False, indent=2)) for p in pages),
                "full_text_rules": len(retained), "in_scope_rules": len(expected),
                "required_occurrences": required, "full_text_hits": hits,
                "discoverable_hits": index_hits, "full_text_availability": hits / required,
                "missing_ids": sorted(set().union(*(set(c["gold"]) for c in cases)) - retained),
                "lifecycles": [lifecycle(REPO, pages, source, state, stub, arm)
                               for source in ("startup", "compact")],
            }
            expected_full = set(expected) if arm == "baseline" else {
                rid for rid in expected if rid not in classes or classes[rid]["always_on"]
                or rid in classification.get("keep_full_text", {})}
            assert retained == expected_full, "a retained corpus statement was omitted or truncated"
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--baseline-ref", required=True)
    parser.add_argument("--corpus", type=Path, default=REPO / "ops/config/rule-selection-corpus.v1.json")
    parser.add_argument("--sponsor", default="joe")
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    report = measure(args.baseline_ref, args.corpus, args.sponsor)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n")
    for arm, row in report["arms"].items():
        print(f"{arm}: ~{row['approx_tokens']} tokens, {row['pages']} pages, actual full text {row['full_text_hits']}/{row['required_occurrences']}; startup/compact held then allowed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
