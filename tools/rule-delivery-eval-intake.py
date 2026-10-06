#!/usr/bin/env python3
"""rule-delivery-eval-intake.py — turn a live rule miss into a benchmark case.

ONE COMMAND, so live misses feed the benchmark instead of a to-do list:

    tools/rule-delivery-eval-intake.py --case-id <id> --missed-rule <rule id> \\
        [--prompt "<paraphrase>" --stratum <stratum>] [--tool-call '<json>' ...]

`--case-id` is either
  * a case already in the benchmark (a shape the tuned system still missed
    live): the new case copies its prompt and tool calls; or
  * a live reference: a drift-observer event id from
    out/rule-delivery-shadow.jsonl, or a transcript turn uuid. The command finds
    the partner's words for that turn in local session history to CHECK the
    paraphrase against — they are never written anywhere. Without --prompt it
    stops and says so.

CHECKS (ops/rule_gold_label.validate_intake), all run on the final prompt and
tool calls before the case is sent anywhere:
  * verbatim (live references only): the prompt, and every string inside the
    tool calls, may share at most five consecutive words with the live turn;
  * names: client, lead and practice names and the partners' names (the deal
    and lead owners), read from the record at intake time (deal-board and
    lead-board through ./run.sh call; or --names-file), turned into terms by
    ops/rule_gold_label.record_name_terms (full names plus distinctive
    surname-like tokens in any script; ordinary words dropped), and matched
    as whole words, case- and accent-insensitively, in the prompt and in every
    string value (and key) of the tool calls as parsed, never in their JSON
    text. An empty names source refuses like an unreadable one. A refusal
    says a name was caught but never which one;
  * patterns: emails, phone numbers, money and square-foot figures, URL hosts,
    bare hostnames (internal suffixes, a tailnet, and public TLDs other than
    the example domains), house-style machine names, IPs, credential, key and
    ssh paths, and token-shaped strings.

WHAT LEAVES THE MACHINE. Reading the names sends only the two read requests
above, which carry nothing from the case. Only after every check passes, and
unless --no-label is given, the labelling pass (ops/rule_gold_label.label_case)
sends Jev the case's prompt and one line per tool call, together with a fixed
description of the setting and the live rules' statements as the questions.
Nothing else from the case is sent.

EXITS. 1: a check refused the case (nothing written, nothing sent to Jev).
2: the command could not run a check or find the live turn — including when
the names cannot be read, since the name check fails closed rather than being
skipped (nothing written, nothing sent to Jev). 0: the case was built and
printed; with --dry-run nothing is written, otherwise it is appended to the
fixture. A refusal names the kind of problem only: never a caught name, never
the matched text, never the live turn's words.

The missed rule is gold by observation. Every other live rule is labelled by
one Jev first pass (ops/rule_gold_label.label_case, the same scheme as the
benchmark); a borderline rule goes into `disputed` — excluded from scoring —
until someone adjudicates it. --no-label skips Jev and marks the case partial.
The case lands in its split by seeded hash, so no existing case moves.
"""
import argparse
import glob
import importlib.util
import json
import os
import subprocess
import sys

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FIXTURE = os.path.join(REPO, "ops", "fixtures", "rule-delivery-eval", "cases.v2.json")
SHADOW = os.path.join(REPO, "out", "rule-delivery-shadow.jsonl")
PROJECTS = os.path.expanduser("~/.claude/projects")
DEFAULT_CALLS = os.path.join(REPO, "out", "rule-delivery-eval", "jev-label-calls.jsonl")


def _load(name):
    path = os.path.join(REPO, "ops", name + ".py")
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _human_text(record):
    if record.get("type") != "user" or record.get("isMeta"):
        return None
    content = (record.get("message") or {}).get("content")
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        if any(isinstance(b, dict) and b.get("type") == "tool_result" for b in content):
            return None
        return "\n".join(b.get("text", "") for b in content
                         if isinstance(b, dict) and b.get("type") == "text") or None
    return None


def find_live_turn(ref, shadow=SHADOW, projects=PROJECTS):
    """The partner's words for a live reference, or None. A drift event id
    resolves to its session and time, then to the last human message at or
    before that time; a transcript uuid resolves directly."""
    session, ts = None, None
    if os.path.exists(shadow):
        with open(shadow, "r", encoding="utf-8") as handle:
            for line in handle:
                if ref in line:
                    try:
                        row = json.loads(line)
                    except ValueError:
                        continue
                    if str(row.get("event_id", "")).startswith(ref):
                        session, ts = row.get("session"), row.get("ts")
                        break
    pattern = f"{projects}/*/{session}.jsonl" if session else f"{projects}/*/*.jsonl"
    best = None
    for path in glob.glob(pattern):
        with open(path, "r", encoding="utf-8", errors="replace") as handle:
            for line in handle:
                if not session and ref not in line:
                    continue
                try:
                    rec = json.loads(line)
                except ValueError:
                    continue
                if not isinstance(rec, dict):
                    continue
                text = _human_text(rec)
                if text is None:
                    continue
                if not session:
                    if rec.get("uuid") == ref:
                        return text
                    continue
                if ts is None or (rec.get("timestamp") or "") <= ts.replace("Z", ".999Z"):
                    best = text
    return best


def _verb(verb, args):
    proc = subprocess.run(["./run.sh", "call", verb, json.dumps(args)], cwd=REPO,
                          capture_output=True, text=True, stdin=subprocess.DEVNULL,
                          timeout=300)
    if proc.returncode != 0:
        raise OSError(f"{verb} exited {proc.returncode}")
    return json.loads(proc.stdout)


def record_name_rows():
    """[{name, kind}] from the record: deal clients, deal names and deal owners
    (deal-board), and leads and lead owners (lead-board). The owners are the
    partners. Read at intake time, held in memory, never written."""
    rows = []
    for deal in _verb("deal-board", {}).get("deals") or []:
        rows.append({"name": deal.get("client_name"), "kind": "practice"})
        rows.append({"name": deal.get("name"), "kind": "deal"})
        rows.append({"name": deal.get("lead_owner"), "kind": "partner"})
    for lead in _verb("lead-board", {}).get("leads") or []:
        rows.append({"name": lead.get("name"), "kind": "practice"})
        rows.append({"name": lead.get("owner_label"), "kind": "partner"})
    if not any(row["name"] for row in rows):
        raise ValueError("the record returned no names")
    return rows


def load_names(names_file=None):
    """The name terms to refuse. Fails closed, whatever the source: when the
    record cannot be read, or either source yields no name terms at all (an
    empty or malformed --names-file included), the intake refuses rather than
    running with an empty name check."""
    gl = _load("rule_gold_label")
    if names_file:
        with open(names_file, "r", encoding="utf-8") as handle:
            rows = json.load(handle)
        if not isinstance(rows, list) or not all(isinstance(r, dict) for r in rows):
            raise ValueError("--names-file must be a JSON list of {name, kind}")
    else:
        rows = record_name_rows()
    terms = gl.record_name_terms(rows)
    if not terms:
        raise ValueError("the names source yielded no name terms")
    return terms


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--case-id", required=True)
    parser.add_argument("--missed-rule", required=True)
    parser.add_argument("--prompt", help="a PARAPHRASE of the live turn (never its words)")
    parser.add_argument("--stratum")
    parser.add_argument("--tool-call", action="append", default=None,
                        help='one {"tool_name": ..., "tool_input": {...}}, sanitised; repeatable')
    parser.add_argument("--fixture", default=FIXTURE)
    parser.add_argument("--corpus", required=True,
                        help="live corpus {rules:[{id, statement}]} (standing-context detail=full)")
    parser.add_argument("--no-label", action="store_true", help="skip the Jev pass")
    parser.add_argument("--calls-log", default=DEFAULT_CALLS)
    parser.add_argument("--dry-run", action="store_true", help="print the case, write nothing")
    parser.add_argument("--names-file",
                        help="JSON [{name, kind}] to check names against instead of reading "
                             "the record (offline use and tests)")
    args = parser.parse_args(argv)

    gl = _load("rule_gold_label")
    with open(args.fixture, "r", encoding="utf-8") as handle:
        doc = json.load(handle)
    with open(args.corpus, "r", encoding="utf-8") as handle:
        rules = json.load(handle)["rules"]
    live = {r["id"] for r in rules}
    known = {c["id"] for c in doc["cases"]}
    source = None
    if args.case_id not in known:
        source = find_live_turn(args.case_id)
        if source is None:
            print(f"no live turn found for {args.case_id}; pass a benchmark case id, a drift "
                  "event id or a transcript uuid", file=sys.stderr)
            return 2
        if not args.prompt:
            print("found the live turn (not shown or saved). Re-run with --prompt carrying a "
                  "paraphrase of it and --stratum.", file=sys.stderr)
            return 2
    calls = [json.loads(c) for c in args.tool_call] if args.tool_call else None
    try:
        names = load_names(args.names_file)
    except (OSError, ValueError, subprocess.SubprocessError) as exc:
        print(f"refused: the person and practice name check could not run ({exc}), so "
              "the case was not checked, written or sent to Jev. Retry when the record "
              "is reachable, or pass --names-file.", file=sys.stderr)
        return 2
    # Every check runs here, BEFORE the Jev pass: a refused case never leaves
    # the machine.
    try:
        prompt, _stratum, calls, _new_id = gl.validate_intake(
            doc, args.case_id, args.missed_rule, live_rules=live, prompt=args.prompt,
            stratum=args.stratum, tool_calls=calls, source_text=source, extra_names=names)
    except ValueError as exc:
        print(f"refused: {exc}", file=sys.stderr)
        return 1
    probs = None
    if not args.no_label:
        tsc = _load("typesafe_client")
        probs, usage = gl.label_case({"id": "intake", "prompt": prompt, "tool_calls": calls},
                                     rules, tsc, calls_log=args.calls_log)
        print(json.dumps({"jev": usage}), file=sys.stderr)
    try:
        case = gl.intake_case(doc, args.case_id, args.missed_rule, live_rules=live,
                              prompt=args.prompt, stratum=args.stratum, tool_calls=calls,
                              source_text=source, probs=probs, extra_names=names,
                              seed=(doc.get("split") or {}).get("seed"))
    except ValueError as exc:
        print(f"refused: {exc}", file=sys.stderr)
        return 1
    print(json.dumps(case, indent=1))
    if args.dry_run:
        return 0
    doc["cases"].append(case)
    counts = doc.setdefault("split", {}).setdefault("counts", {})
    counts[case["split"]] = counts.get(case["split"], 0) + 1
    tmp = args.fixture + ".tmp"
    with open(tmp, "w", encoding="utf-8") as handle:
        json.dump(doc, handle, indent=1, sort_keys=True)
        handle.write("\n")
    os.replace(tmp, args.fixture)
    print(f"added {case['id']} to {args.fixture} ({case['split']})", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
