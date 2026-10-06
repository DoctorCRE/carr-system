"""Append-only mistake notebook with exact error/artifact lookup. No model."""
import json
import os
import re
from datetime import datetime, timezone

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
NOTEBOOK_PATH = os.path.join(REPO,"out","jev-mistake-notebook.jsonl")
DEFAULT_LIMIT = 3

def _read_all(path=NOTEBOOK_PATH):
    """Every row in the notebook, oldest first. Missing file is an empty log."""
    rows = []
    try:
        with open(path, "r", encoding="utf-8") as handle:
            for line in handle:
                line = line.strip()
                if not line:
                    continue
                try:
                    rows.append(json.loads(line))
                except ValueError:
                    continue
    except OSError:
        pass
    return rows


def record_mistake(kind, task_text, what_went_wrong, fix, *, source,
                    notebook_path=NOTEBOOK_PATH):
    """Append one mistake to the notebook. Deterministic — no Jev is asked.

    `kind` is a short caller-chosen label (see classify_kind() for picking an
    existing one). `source` names where this was noticed (a check id, a
    session, a reviewer) so a later reader can trace it back. Never raises: a
    notebook write that fails is logged nowhere useful anyway, since the
    notebook IS the log, so the failure is swallowed the same way every
    shadow-log writer in this project swallows one.

    Returns the row written, including its `id` (an index into the notebook at
    write time — stable for the life of the file, not across a rewrite).
    """
    row = {
        "id": None,  # filled in below once the current length is known
        "at": datetime.now(timezone.utc).isoformat(),
        "kind": kind,
        "task_text": (task_text or "")[:2000],
        "what_went_wrong": (what_went_wrong or "")[:2000],
        "fix": (fix or "")[:2000],
        "source": source,
    }
    try:
        os.makedirs(os.path.dirname(notebook_path), exist_ok=True)
        existing = 0
        if os.path.exists(notebook_path):
            with open(notebook_path, "r", encoding="utf-8") as handle:
                existing = sum(1 for line in handle if line.strip())
        row["id"] = existing
        with open(notebook_path, "a", encoding="utf-8") as handle:
            handle.write(json.dumps(row, sort_keys=True) + "\n")
    except OSError:
        pass
    return row


def _format_line(row):
    return (f"[past mistake, kind={row.get('kind')}] {row.get('what_went_wrong', '')} "
            f"— fix: {row.get('fix', '')}")


def _signatures(text):
    text = text or ""
    errors = set(re.findall(r"\b(?:[A-Z]\w*(?:Error|Exception)|ENOENT|ECONNREFUSED)(?::[^\n]*)?",text))
    paths = set(re.findall(r"(?<![\w/])(?:[\w.\-]+/)+[\w.\-]+",text))
    return errors | paths


def recall_mistakes(task_text, *, limit=DEFAULT_LIMIT, notebook_path=NOTEBOOK_PATH):
    query = _signatures(task_text)
    rows = _read_all(notebook_path)
    scored = [(len(query & _signatures(row.get("what_went_wrong","")+"\n"+row.get("task_text",""))),i,row)
              for i,row in enumerate(rows) if isinstance(row,dict)]
    chosen = [row for score,i,row in sorted(scored,key=lambda t:(-t[0],-t[1])) if score][:max(0,limit)]
    return {"check":"notebook_recall","verdict":[row.get("id") for row in chosen],
            "confidence":None,"escalate":False,
            "detail":{"lines":[_format_line(row) for row in chosen],"source":"exact_error_artifact",
                      "reason":"exact error/artifact match" if chosen else "no exact error/artifact match"}}


def classify_kind(what_went_wrong, task_text, *, notebook_path=NOTEBOOK_PATH):
    query = _signatures(what_went_wrong)
    kinds = {row.get("kind") for row in _read_all(notebook_path) if isinstance(row,dict) and row.get("kind")
             and query & _signatures(row.get("what_went_wrong",""))}
    verdict = next(iter(kinds)) if len(kinds) == 1 else "needs_review"
    return {"check":"notebook_classify","verdict":verdict,"confidence":None,
            "escalate":verdict == "needs_review","detail":{"matching_kinds":sorted(kinds)}}
