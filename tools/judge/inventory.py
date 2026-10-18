"""Docs-free source inventory: executable calls plus every searched reference.

scan(root) uses the tracked repository corpus (plus new judge source) rather
than filesystem worktrees, dependencies or credential files. AST identifies
Python calls; JS source matching identifies the small set of typed/proxy calls.
Question constructors and test/reference hits are retained separately.
"""
import ast
import hashlib
import json
import re
import subprocess
from collections import Counter
from pathlib import Path

TERMS = re.compile(r"jev|typesafe_client|ask-jev|jev-1\.13|room-bridge", re.I)
RUNTIME = {"ops/jev_deal_read.py", "tools/dictation-rig/bin/post_call_jev.py",
           "tools/dictation-rig/bin/post_call.py", "mcp-server/src/jev-deal-reading.js",
           "mcp-server/src/dealroom-web.js", "generators/build-deal-room.py"}
KINDS = ("noul", "choice", "score")
INDIRECT = {
    "hooks/lint-gate.py": {"module.review_for_edit"},
    "hooks/completion-evidence-gate.py": {"module.check"},
    "hooks/blocker-decider-gate.py": {"module.advise"},
    "hooks/executor-tier-gate.py": {"module.recommend"},
    "hooks/rule-pack-preuse-reselection.py": {"module.advise"},
    "hooks/conduct-stop-gate.py": {"jev"},
    "hooks/escalation-gate.py": {"jev"},
    "tools/room-bridge/kanban_adapter.py": {"router.decide"},
    "tools/dictation-rig/bin/post_call.py": {"post_call_jev.check_distillation"},
}


def classify(path):
    runtime = path in RUNTIME
    return ("app_runtime" if runtime else "system_work"), [
        {"question": "Does the call interpret a production deal or meeting record for the human application?",
         "answer": runtime, "evidence": "production deal/post-call consumer" if runtime else "no production deal/post-call consumer"},
        {"question": "Otherwise does it govern or evaluate rules, hooks, reviews, orchestration, retrieval or build behavior?",
         "answer": not runtime, "evidence": "development/authority purpose or shared transport default" if not runtime else "not evaluated: runtime branch matched"},
    ]


def _test(path):
    return ("/test/" in path or "/tests/" in path or "/fixtures/" in path or
            "selftest" in path or Path(path).name.startswith(("test_", "test-")) or
            path.endswith((".test.mjs", ".test.js")))


def _shapes(node, file_shapes):
    shapes = set()
    for child in ast.walk(node):
        if isinstance(child, ast.Call) and isinstance(child.func, ast.Attribute) and child.func.attr in KINDS:
            shapes.add(child.func.attr)
        if isinstance(child, ast.Dict):
            for key, value in zip(child.keys, child.values):
                if isinstance(key, ast.Constant) and key.value == "type" and isinstance(value, ast.Constant) and value.value in KINDS:
                    shapes.add(value.value)
    return sorted(shapes or file_shapes or set(KINDS))


def scan(root):
    root = Path(root)
    paths = subprocess.check_output(["git", "ls-files", "-z"], cwd=root).decode().split("\0")
    paths += [str(p.relative_to(root)) for p in (root / "tools/judge").glob("*.py")]
    calls, constructors, references, scanned = [], [], [], []
    for path in sorted(set(paths)):
        p = root / path
        if path.startswith("out/judge-"):
            continue  # Derived evidence is not source and must not hash itself.
        if not p.is_file() or p.suffix not in (".py", ".js", ".mjs", ".sh", ".json", ".md", ".toml"):
            continue
        text = p.read_text(errors="replace")
        if not TERMS.search(text):
            continue
        scanned.append({"path": path, "sha256": hashlib.sha256(p.read_bytes()).hexdigest()})
        matched = {i for i, line in enumerate(text.splitlines(), 1) if TERMS.search(line)}
        candidates = []
        if p.suffix == ".py" and not _test(path) and not path.startswith("tools/judge/"):
            tree = ast.parse(text)
            scopes = {}
            def walk(node, scope=None):
                if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    scope = node
                for child in ast.iter_child_nodes(node):
                    scopes[id(child)] = scope
                    walk(child, scope)
            walk(tree)
            file_shapes = set(_shapes(tree, set()))
            for node in ast.walk(tree):
                if not isinstance(node, ast.Call):
                    continue
                func = ast.unparse(node.func)
                attr = node.func.attr if isinstance(node.func, ast.Attribute) else ""
                scope = scopes.get(id(node))
                leaf = func.split(".")[-1]
                if attr in KINDS and any(a in func for a in ("tsc", "client", "ts.", "self._tsc")):
                    constructors.append({"location": f"{path}:{node.lineno}", "call_shape": attr})
                elif ((attr == "ask" and any(a in func for a in ("tsc", "client", "ts.", "self._tsc"))) or
                      (attr == "judge" and ("judge" in func or func.startswith(("jj.", "ranker.")))) or
                      (leaf in ("ask", "ask_fn", "live_ask") and ("typesafe_client" in text or "post_call_jev" in path))):
                    candidates.append((node.lineno, func, _shapes(scope or tree, file_shapes), scope.name if scope else "module", "request"))
                elif (func in INDIRECT.get(path, set()) or
                      (path == "hooks/jev-supervisor.py" and func == "run.do" and node.args and
                       isinstance(node.args[0], ast.Attribute) and ast.unparse(node.args[0]).startswith(("watch.", "done.")))):
                    candidates.append((node.lineno, ast.unparse(node.args[0]) if func == "run.do" else func,
                                       _shapes(scope or tree, file_shapes), scope.name if scope else "module", "indirect"))
                elif "jev" in func.lower() and leaf not in ("record", "read", "question", "questions") and not func.startswith("_load") and "outage" not in func:
                    # Indirect calls retain their source evidence for review;
                    # health/report/cache operations are not vendor calls.
                    if leaf in ("preflight", "review_for_edit", "check", "evaluate", "read_deals", "judge", "check_all"):
                        candidates.append((node.lineno, func, _shapes(scope or tree, file_shapes), scope.name if scope else "module", "indirect"))
        elif p.suffix in (".js", ".mjs") and not _test(path):
            for i, line in enumerate(text.splitlines(), 1):
                if re.search(r"(?:await\s+askJev\(|await\s+ask\(askJev|await\s+judgeBinding\(|prefetchJevAnswer\(jevArgs|callTool(?:Fn)?\(.*[\"']ask-jev[\"'])", line):
                    shapes = sorted(set(re.findall(r'type:\s*["\'](noul|choice|score)["\']', text))) or list(KINDS)
                    candidates.append((i, "Worker typed/proxy request", shapes, Path(path).stem, "request"))
                # Python embedded in the retrieval evaluation's JS runner.
                elif "typesafe_client.ask(" in line and path.startswith("evals/"):
                    candidates.append((i, "embedded Python typed request", list(KINDS), "retrieval evaluation", "request"))
        for line, call, shapes, purpose, kind in candidates:
            work_class, procedure = classify(path)
            calls.append({"path": path, "line": line, "location": f"{path}:{line}", "call": call,
                          "call_shape": shapes, "shape_evidence": "enclosing function constructors; otherwise file-level or generic typed contract",
                          "purpose": purpose, "class": work_class, "classification_test": procedure, "kind": kind,
                          "route": "ops/typesafe_client.py -> tools/judge/interface.py" if p.suffix == ".py" or path.startswith("evals/") else "prefetchJevAnswer -> judgeBinding",
                          "runtime_pin": work_class == "app_runtime"})
            matched.discard(line)
        for line in sorted(matched):
            references.append({"location": f"{path}:{line}", "kind": "test_or_fixture" if _test(path) else "reference_or_definition"})
    calls.sort(key=lambda c: (c["path"], c["line"]))
    counts = Counter(c["class"] for c in calls)
    grouped_references = {}
    for reference in references:
        path, line = reference["location"].rsplit(":", 1)
        key = (path, reference["kind"])
        grouped_references.setdefault(key, []).append(int(line))
    return {"schema": "carr-judge-inventory/v1", "scope": "tracked carr-system plus new judge libraries; excludes dependencies, untracked output, other worktrees and credentials",
            "search_terms": ["jev", "typesafe_client", "ask-jev", "jev-1.13", "room-bridge"],
            "source_files": scanned, "calls": calls, "question_constructors": constructors,
            "references": [{"path": path, "kind": kind, "lines": lines}
                           for (path, kind), lines in sorted(grouped_references.items())],
            "counts": {k: counts[k] for k in ("system_work", "app_runtime")}}


def write(root, output):
    inventory = scan(root)
    Path(output).write_text(json.dumps(inventory, indent=2) + "\n")
    return inventory
