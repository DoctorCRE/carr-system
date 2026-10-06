#!/usr/bin/env bash
# round.sh N "one-line change" GOAL DECISION  — record one hillclimb round.
# Writes runs/vN/{change.md,change.patch,summary.json} (patch = working-tree diff
# of everything outside evals/, against the last committed state) and appends
# runs/ledger.jsonl. Run after `run_eval.py --variant vN --split train`.
set -euo pipefail
cd "$(dirname "$0")/../.."
n="$1"; title="$2"; goal="$3"; decision="$4"; base="${5:-baseline}"
d="evals/rule-delivery/runs/v$n"
mkdir -p "$d"
git diff -- . ':(exclude)evals' > "$d/change.patch"
python3 evals/rule-delivery/run_eval.py --compare "$base" "v$n" --split train > "$d/compare.txt"
verdict=$(python3 evals/rule-delivery/run_eval.py --verdict "$base" "v$n" "$goal")
printf '# v%s: %s\n\nGoal: %s. Decision: %s.\n\nGate: %s\n\n```\n%s\n```\n' "$n" "$title" "$goal" "$decision" "$verdict" "$(cat "$d/compare.txt")" > "$d/change.md"
python3 - "$n" "$title" "$goal" "$decision" "$verdict" <<'P'
import json, sys
n, title, goal, decision, verdict = sys.argv[1:6]
d = f"evals/rule-delivery/runs/v{n}"
json.dump({"description": title, "label": f"v{n}", "target": "code", "goal": goal,
           "decision": decision, "gate": verdict}, open(d + "/summary.json", "w"), indent=1)
open("evals/rule-delivery/runs/ledger.jsonl", "a").write(json.dumps(
    {"round": int(n), "change": title, "goal": goal, "decision": decision, "gate": verdict}) + "\n")
P
echo "recorded v$n ($decision): $verdict"
