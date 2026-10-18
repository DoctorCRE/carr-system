"""Completion and review predicates. Semantic residuals explicitly need review.

No model or model-result cache participates in these checks. A path tier is a
contract, an artifact the verifier reads is evidence, and free-text acceptance
is unresolved.
"""
import ast
import importlib.util
import json
import os
import re
import subprocess
from types import ModuleType

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_LIB_MODULES: dict[str, ModuleType] = {}
DONE_CLAIM = re.compile(r"\b(done|fixed|pass(?:ed|es|ing)?|works|working|complete(?:d)?|resolved|finished|verified)\b",re.I)
TEST_MARKERS = re.compile(r"\bdef\s+test_|\bassert\b|\bexpect\(|\bit\(|\bdescribe\(")
FILE_HEADER = re.compile(r"^diff --git a/(?P<a>.+?) b/(?P<b>.+?)$",re.M)
MAX_HUNK_CHARS = 3000
ASSISTANT_TAIL_MESSAGES = 6
ASSISTANT_TEXT_CHARS = 1500
TRANSCRIPT_TAIL_BYTES = 2 * 1024 * 1024
FAILURE_CHARS = 3000
DIFF_CHARS_PER_FILE = 4000
MAX_HANDOFF_FILES = 20

def _sibling(name, folder="ops"):
    spec = importlib.util.spec_from_file_location(name, os.path.join(REPO, folder, f"{name}.py"))
    if spec is None or spec.loader is None:
        raise ImportError(name)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _sibling_lib(name):
    """A lib/ module, loaded once per process."""
    if name not in _LIB_MODULES:
        _LIB_MODULES[name] = _sibling(name, folder="lib")
    return _LIB_MODULES[name]


def _result(check_id, verdict, *, confidence=None, escalate=False, detail=None, advice=None):
    out = {"check": check_id, "verdict": verdict, "confidence": confidence,
           "escalate": bool(escalate), "detail": dict(detail or {})}
    if advice:
        out["detail"]["advice"] = advice
    return out


def deterministic_high_floor(path):
    """True when the review-tier map puts `path` at the triage floor tier.
    Raises when the map cannot be read; triage_review records that per file."""
    tiers = _sibling_lib("review_tiers")
    return tiers.tier_for_path(path) >= tiers.TRIAGE_HIGH_FLOOR_TIER


def split_diff_by_file(diff_text):
    """A unified diff, split into {path: hunk text}. One group per `diff --git`."""
    if not diff_text or not diff_text.strip():
        return {}
    matches = list(FILE_HEADER.finditer(diff_text))
    if not matches:
        return {"(change)": diff_text[:MAX_HUNK_CHARS]}
    out = {}
    for i, m in enumerate(matches):
        start = m.start()
        end = matches[i + 1].start() if i + 1 < len(matches) else len(diff_text)
        path = m.group("b") or m.group("a")
        out[path] = diff_text[start:end][:MAX_HUNK_CHARS]
    return out


def _content(rec):
    message = rec.get("message") if isinstance(rec.get("message"), dict) else {}
    return message.get("content", rec.get("content"))


def _assistant_text_blocks(rec):
    if rec.get("type") != "assistant":
        return []
    content = _content(rec)
    if not isinstance(content, list):
        return []
    return [b.get("text", "") for b in content
            if isinstance(b, dict) and b.get("type") == "text" and b.get("text")]


def tail_assistant_notes(transcript_path, *, tail_messages=ASSISTANT_TAIL_MESSAGES,
                         tail_bytes=TRANSCRIPT_TAIL_BYTES, per_note_chars=ASSISTANT_TEXT_CHARS):
    """The text of the last few assistant messages in a JSONL transcript, oldest first.

    Tail-reads the file, because a real transcript can be large and only the
    end of it is relevant to a mid-task handoff. Never raises: an unreadable
    or missing transcript yields an empty list.
    """
    if not transcript_path or not isinstance(transcript_path, str):
        return []
    try:
        with open(transcript_path, "rb") as handle:
            handle.seek(0, os.SEEK_END)
            size = handle.tell()
            handle.seek(max(0, size - tail_bytes))
            raw = handle.read().decode("utf-8", errors="replace")
    except OSError:
        return []
    notes = []
    for line in reversed(raw.splitlines()):
        line = line.strip()
        if not line:
            continue
        try:
            rec = json.loads(line)
        except ValueError:
            continue
        blocks = _assistant_text_blocks(rec)
        if blocks:
            notes.append("\n".join(blocks)[:per_note_chars])
        if len(notes) >= tail_messages:
            break
    notes.reverse()
    return notes


def _git_diff(path, *, timeout=5.0, git_env_module=None):
    """`git diff HEAD -- path`, run from path's own directory. "" on any failure."""
    folder = os.path.dirname(path) or "."
    if not os.path.isdir(folder):
        return ""
    try:
        env = (git_env_module or _sibling("git_env")).scrubbed_env()
    except Exception:
        env = None
    try:
        run = subprocess.run(["git", "-C", folder, "diff", "--no-color", "HEAD", "--", path],
                             capture_output=True, text=True, timeout=timeout, env=env)
        return run.stdout if run.returncode == 0 else ""
    except Exception:
        return ""


def _collect_items(task_text, transcript_path, changed_paths, failure_output):
    items = []
    if task_text and task_text.strip():
        items.append({"id": "task", "text": "## Task\n" + task_text.strip()})
    for path in (changed_paths or [])[:MAX_HANDOFF_FILES]:
        diff = _git_diff(path)
        if diff.strip():
            items.append({"id": f"diff:{path}",
                          "text": f"## Diff: {path}\n" + diff[:DIFF_CHARS_PER_FILE]})
    if failure_output and str(failure_output).strip():
        items.append({"id": "last_failure",
                      "text": "## Last failure\n" + str(failure_output)[:FAILURE_CHARS]})
    for i, note in enumerate(tail_assistant_notes(transcript_path)):
        items.append({"id": f"assistant_note_{i}", "text": f"## Assistant note {i}\n" + note})
    return items


def check_test_quality(test_source, code_under_test, task_text):
    if not test_source or not TEST_MARKERS.search(test_source):
        return _result("test_quality","not_triggered")
    try:
        tree = ast.parse(test_source)
        assertions = [n for n in ast.walk(tree) if isinstance(n,ast.Assert)]
        tautologies = [n for n in assertions if isinstance(n.test,ast.Constant) and bool(n.test.value)
                      or isinstance(n.test,ast.Compare) and len(n.test.comparators) == 1
                      and isinstance(n.test.ops[0],ast.Eq)
                      and isinstance(n.test.left,(ast.Name,ast.Constant))
                      and ast.dump(n.test.left) == ast.dump(n.test.comparators[0])]
        if tautologies:
            return _result("test_quality","weak",detail={"red_flags":["tautological_assertion"]},
                           advice="A test assertion compares a value to itself or asserts a true literal.")
    except SyntaxError:
        pass  # Another language or partial edit still requires review.
    return _result("test_quality","needs_review",escalate=True,
                   advice="Behavioral coverage and stated edge cases need review.")


def check_done_claim(final_message, evidence):
    if not final_message or not DONE_CLAIM.search(final_message):
        return _result("done_claim","no_claim")
    evidence = evidence or {}
    scope = evidence.get("claim_scope")
    if scope == "other":
        return _result("done_claim","no_claim")
    # Natural language alone cannot distinguish quotes, history and assertions.
    if scope not in {"tests","current_completion"}:
        return _result("done_claim","needs_review",escalate=True,
                       advice="Bind the completion claim to explicit criteria and current evidence; needs review.")
    latest = {}
    for row in evidence.get("test_runs", evidence.get("test_history", [])):
        if isinstance(row,dict) and row.get("command"):
            latest[row["command"]] = row
    failed = [command for command,row in latest.items() if row.get("exit_code") not in (0,"0")]
    if failed or evidence.get("test_exit_code") not in (None,0,"0") or evidence.get("test_failed"):
        return _result("done_claim","unsupported",detail={"unresolved_checks":failed},
                       advice="Completion conflicts with unresolved check failures.")
    if evidence.get("test_history_truncated"):
        return _result("done_claim","needs_review",escalate=True,advice="Omitted check runs need review.")
    receipt = _sibling_lib("acceptance_checks").evaluate(evidence.get("criteria"),root=evidence.get("root",REPO))
    verdict = {"passed":"supported","failed":"unsupported","needs_review":"needs_review"}[receipt["status"]]
    return _result("done_claim",verdict,escalate=verdict == "needs_review",detail=receipt,
                   advice="Completion criteria need review." if verdict == "needs_review" else
                          "Completion criteria are not met." if verdict == "unsupported" else None)


def triage_review(diff_text, task_text):
    files = split_diff_by_file(diff_text)
    results = {}
    for path in files:
        try:
            tier = _sibling_lib("review_tiers").tier_for_path(path)
            floor = deterministic_high_floor(path)
            results[path] = {"risk":"high" if floor else "medium",
                             "tier":tier,"source":"deterministic_floor" if floor else "path_contract"}
        except Exception:
            results[path] = {"risk":"unknown","source":"unreadable_path_contract"}
    return _result("review_triage","needs_review" if files else "ok",escalate=bool(files),
                   detail={"files":results},advice="Review the diff at its path-contract tiers." if files else None)


def fact_check(claim_text, doctrine_passages):
    passages = [p for p in doctrine_passages or [] if isinstance(p,dict) and p.get("ref") and p.get("text")]
    if not claim_text or not passages:
        return _result("fact_check","unsupported",advice="Claim or source passages missing.")
    return _result("fact_check","needs_review",escalate=True,
                   detail={"passage_refs":[p["ref"] for p in passages]},
                   advice="Semantic support or contradiction needs review.")


def build_handoff(task_text, transcript_path, changed_paths, failure_output=None, *, max_chars=12000):
    items = _collect_items(task_text,transcript_path,changed_paths,failure_output)
    # Task and current failure come first; then diffs and newest notes. Never
    # drop the task because a later small item fits the remaining space.
    priority = sorted(enumerate(items),key=lambda pair:(
        0 if pair[1]["id"] == "task" else 1 if pair[1]["id"] == "last_failure" else
        2 if pair[1]["id"].startswith("diff:") else 3,
        -pair[0] if pair[1]["id"].startswith("assistant_note") else pair[0]))
    kept,dropped,parts,used = [],[],[],0
    for _,item in priority:
        remaining = max(0,max_chars-used-(2 if parts else 0))
        if remaining:
            part = item["text"][:remaining]
            parts.append(part); used += len(part)+(2 if len(parts)>1 else 0)
            kept.append(item["id"])
            if len(part) != len(item["text"]):
                dropped.append(item["id"] + ":truncated")
        else:
            dropped.append(item["id"])
    return {"pack":"\n\n".join(parts),"kept":kept,"dropped":dropped}


def inspect_stop_boundary(final_message, evidence, diff_text, task_text, session_id):
    # Predicates are cheap and read current evidence on every call. No stale
    # model-result cache can suppress a newly failed check.
    results = [check_done_claim(final_message,evidence),triage_review(diff_text,task_text)]
    return [r for r in results if r["verdict"] not in {"no_claim","ok"}]
