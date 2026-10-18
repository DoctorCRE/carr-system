"""Structural partition for the Jev code review: every function, not seven regexes.

WHY THIS EXISTS. ops/jev_code_review.py finds candidate regions with seven
regex signatures and only ever judges what they flag. So a judgment that could
rule on an arbitrary function never sees one unless it happens to contain
`except Exception: pass` or `sleep(3)`. A catch block that logs nothing but is
written `except ValueError: continue`, a JS `catch {}`, a docstring that went
stale: none of them match a signature, so none of them are read.

WHAT THIS DOES INSTEAD. A deterministic pass cuts every tracked Python and JS
file into the units a reviewer reads:

  function        a def / function / method / arrow body, whole, when it fits
  function_part   a slice of a function too long for one judgment
  except_block    a Python except handler or a JS catch, with the lines above
                  it, emitted ONLY when no whole-function partition covers it
  comment_block   a run of comment lines outside any function, sent together
                  with the code it describes
  long_span       module-level code outside every function, in slices

Then it dedupes: a partition wholly inside a kept whole-function partition is
dropped (the judgment already read those lines), and a partition whose
exact text is identical to one already kept is judged once and
carries every location in `also_at`. Adjacent small partitions in one file are
packed into one region up to the size cap, because each region is one request.

NOTHING HERE CALLS A MODEL AND NOTHING HERE WRITES CODE. `review()` hands the
partitions to jev_code_review.review(), the existing one-request-per-region
batched judgment, unchanged. `verify_edit()` checks an edit SOMEONE ELSE
proposed, in a throwaway git worktree, with the normal checks, and reports.
It never applies the edit to the live tree; applying is a person's call.

LIBRARY ON PURPOSE: no shebang and no main guard, for the reason
ops/typesafe_client.py gives -- either construct makes this file a sealed
script entrypoint. The offline suite is ops/jev-code-partition-selftest.py.
"""

from __future__ import annotations

import ast
import hashlib
import importlib.util
import io
import json
import math
import os
import re
import shutil
import stat
import subprocess
import sys
import tempfile
import time
import tokenize

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# Same cap the regex scanner truncates at, so the two are compared on equal
# region sizes and neither wins by sending more code per request.
MAX_REGION_CHARS = 2600
EXCEPT_CONTEXT_BEFORE = 14
COMMENT_MIN_LINES = 4
COMMENT_FOLLOW_LINES = 20
PACK_BELOW_CHARS = 600          # a partition this small is packed with neighbours
SUFFIXES = (".py", ".mjs", ".js", ".cjs")
# Review noise (node_modules, vendored code, minified assets, lockfiles,
# generated registries) is decided by the one review-tier map
# (ops/config/review-tiers.v1.json, engineering-workflow-sop section 15), read
# through lib/review_tiers.py. Migrations are never noise.

KINDS = ("function", "function_part", "except_block", "comment_block", "long_span")


def _read(path):
    with open(path, encoding="utf-8", errors="ignore") as handle:
        return handle.read()


def _load(name, rel):
    spec = importlib.util.spec_from_file_location(name, os.path.join(REPO, rel))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def tracked_sources(repo=REPO):
    tiers = _load("review_tiers", "lib/review_tiers.py")
    out = subprocess.run(["git", "ls-files", "-z"], capture_output=True,
                         cwd=repo, timeout=120).stdout
    return [f for f in map(os.fsdecode, out.split(b"\0"))
            if f.endswith(SUFFIXES) and not tiers.is_review_noise(f)]


def digest(text):
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


# --- spans ---------------------------------------------------------------
#
# A span is (kind, start_line, end_line), 1-based and inclusive. The two
# parsers only produce spans; cutting, packing and dedupe are shared.

def python_spans(text, handler_headers=None):
    """Functions (including methods and nested defs) and except handlers."""
    try:
        tree = ast.parse(text)
    except (SyntaxError, ValueError):
        return None
    spans = []
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            start = min([node.lineno] + [d.lineno for d in node.decorator_list])
            spans.append(("function", start, node.end_lineno))
        elif isinstance(node, ast.ExceptHandler):
            spans.append(("except_block", node.lineno, node.end_lineno))
            if handler_headers is not None:
                # NL inside parentheses/explicit continuations is not NEWLINE.
                # The first logical NEWLINE terminates the complete header.
                tail = "\n".join(text.splitlines()[node.lineno - 1:])
                tokens = tokenize.generate_tokens(io.StringIO(tail).readline)
                header_end = next(t.start[0] for t in tokens if t.type == tokenize.NEWLINE)
                handler_headers[(node.lineno, node.end_lineno)] = node.lineno + header_end - 1
    return spans


_JS_CONTROL = re.compile(r"\b(?:if|for|while|switch|with|do|else|try|finally)\s*(?:\([^{}]*\))?\s*$")
_JS_CATCH = re.compile(r"\bcatch\s*(?:\([^{}]*\))?\s*$")
_JS_FUNCTION = re.compile(
    r"(?:\bfunction\b[^{};]*\)\s*$"                     # function f(a) {  /  function (a) {
    r"|=>\s*$"                                           # (a) => {
    r"|^\s*(?:async\s+|static\s+|get\s+|set\s+|\*\s*)*"   # method(a) {
    r"#?[\w$]+\s*\([^{};]*\)\s*$)")
_REGEX_PRECEDERS = set("(,=:[!&|?{};+-*%<>~^")


def js_spans(text, handler_headers=None):
    """Functions and catch blocks from a brace scan that skips strings,
    comments, template literals and regex literals.

    Not a parser. It is deliberately a scanner whose only job is to find the
    matching brace of every `{` and classify the header in front of it, which
    is what partitioning needs. An unbalanced file returns None and the caller
    falls back to long spans, so a scanner miss costs structure, not coverage.
    """
    spans = []
    stack = []                  # (kind or None, start_line, header_end_line)
    modes = []                      # template-literal nesting: brace depth at ${
    line = 1
    i, n = 0, len(text)
    header_start = 0                # index where the current statement header began
    last_sig = ""                   # last significant char, for regex detection
    last_word = ""

    def classify(end):
        header = text[header_start:end]
        header = re.sub(r"/\*.*?\*/|//[^\n]*", " ", header, flags=re.S)
        header = header.split("\n")
        tail = " ".join(h.strip() for h in header[-4:])
        if _JS_CATCH.search(tail):
            return "except_block"
        if _JS_CONTROL.search(tail):
            return None
        if _JS_FUNCTION.search(tail):
            return "function"
        return None

    def header_line(end):
        # first line of the header that is code, so a comment above
        # `async function f(\n a) {` is not counted as the function's start.
        seg = text[header_start:end]
        skip = re.match(r"(?:\s+|//[^\n]*|/\*.*?\*/)*", seg, re.S).end()
        return text.count("\n", 0, header_start + skip) + 1

    while i < n:
        c = text[i]
        if c == "\n":
            line += 1
            i += 1
            continue
        if c in " \t\r":
            i += 1
            continue
        nxt = text[i + 1] if i + 1 < n else ""
        if c == "/" and nxt == "/":
            j = text.find("\n", i)
            i = n if j < 0 else j
            continue
        if c == "/" and nxt == "*":
            j = text.find("*/", i + 2)
            j = n if j < 0 else j + 2
            line += text.count("\n", i, j)
            i = j
            continue
        if c in "'\"":
            j = i + 1
            while j < n and text[j] != c and text[j] != "\n":
                if text[j] == "\\":
                    j += 3 if text[j + 1:j + 3] == "\r\n" else 2
                else:
                    j += 1
            line += text.count("\n", i, j + 1)
            i = j + 1
            last_sig = c
            continue
        if c == "`" or (c == "}" and modes and modes[-1] == len(stack)):
            if c == "}":
                modes.pop()
            j = i + 1
            while j < n:
                ch = text[j]
                if ch == "\\":
                    j += 2
                    continue
                if ch == "`":
                    j += 1
                    break
                if ch == "$" and j + 1 < n and text[j + 1] == "{":
                    modes.append(len(stack))
                    j += 2
                    break
                j += 1
            line += text.count("\n", i, j)
            i = j
            last_sig = "`"
            continue
        if c == "/" and (last_sig in _REGEX_PRECEDERS or last_sig == ""
                         or last_word in ("return", "typeof", "case", "in", "of")):
            j = i + 1
            in_class = False
            while j < n and text[j] != "\n":
                ch = text[j]
                if ch == "\\":
                    j += 2
                    continue
                if ch == "[":
                    in_class = True
                elif ch == "]":
                    in_class = False
                elif ch == "/" and not in_class:
                    break
                j += 1
            i = j + 1
            last_sig = "/"
            last_word = ""
            continue
        if c == "{":
            kind = classify(i)
            stack.append((kind, header_line(i) if kind else line, line))
            header_start = i + 1
            last_sig = c
            last_word = ""
            i += 1
            continue
        if c == "}":
            if not stack:
                return None
            kind, start, header_end = stack.pop()
            if kind:
                spans.append((kind, start, line))
                if kind == "except_block" and handler_headers is not None:
                    handler_headers[(start, line)] = header_end
            header_start = i + 1
            last_sig = c
            last_word = ""
            i += 1
            continue
        if c == ";":
            header_start = i + 1
        if c.isalnum() or c in "_$":
            j = i
            while j < n and (text[j].isalnum() or text[j] in "_$"):
                j += 1
            last_word = text[i:j]
            last_sig = "a"
            i = j
            continue
        last_sig = c
        last_word = ""
        i += 1
    if stack or modes:
        return None
    return spans


_COMMENT_LINE = {".py": re.compile(r"^\s*#"), "js": re.compile(r"^\s*(?://|/\*|\*)")}


def comment_runs(lines, suffix):
    pattern = _COMMENT_LINE[".py" if suffix == ".py" else "js"]
    runs, start = [], None
    for idx, raw in enumerate(lines, 1):
        if pattern.match(raw):
            start = start or idx
            continue
        if start and idx - start >= COMMENT_MIN_LINES:
            runs.append((start, idx - 1))
        start = None
    if start and len(lines) + 1 - start >= COMMENT_MIN_LINES:
        runs.append((start, len(lines)))
    return runs


# --- cutting -------------------------------------------------------------

def _slices(lines, start, end, cap=MAX_REGION_CHARS):
    """Cut lines[start..end] (1-based inclusive) into consecutive slices that
    each fit the cap. A single line over the cap is its own truncated slice."""
    out, lo, size = [], start, 0
    for idx in range(start, end + 1):
        width = len(lines[idx - 1]) + 1
        if size and size + width > cap:
            out.append((lo, idx - 1))
            lo, size = idx, 0
        size += width
    if lo <= end:
        out.append((lo, end))
    return out


def _text(lines, start, end):
    return "\n".join(lines[start - 1:end])


def trivial(code):
    """Nothing a judgment could rule on: blank, or only braces and punctuation.
    Anything with a word in it is kept; packing folds short ones into their
    neighbours rather than dropping lines the judge should see."""
    return not re.search(r"\w", code)


def _part(path, kind, start, end, lines):
    code = _text(lines, start, end)
    sent = code[:MAX_REGION_CHARS]
    sent_end_line = (end if len(sent) == len(code) else
                     start + sent.count("\n") - 1)
    return {"path": path, "line": start, "end_line": end, "kind": kind,
            "code": sent, "digest": digest(code),
            "chars": len(sent), "sent_end_line": sent_end_line}


def _handler_parts(path, start, end, lines, header_end):
    """Slice a long handler, repeating its header as context on body slices.

    Later slices retain their body line range; context_line identifies the
    repeated header without claiming the intervening body was sent again.
    """
    if header_end == end:
        return [_part(path, "except_block", start, end, lines)]
    full_header = _text(lines, start, header_end)
    # Oversized context gets its own truthful source region. Repeating only a
    # bounded prefix leaves room for body text on every subsequent slice.
    body_room = min(MAX_REGION_CHARS // 2,
                    max(len(line) + 1 for line in lines[header_end:end]))
    header = full_header[:MAX_REGION_CHARS - body_room - 1]
    cap = MAX_REGION_CHARS - len(header) - 1
    out = ([] if header == full_header else
           [_part(path, "except_block", start, header_end, lines)])
    for a, b in _slices(lines, header_end + 1, end, cap=cap):
        if a == header_end + 1 and header == full_header:
            first = _part(path, "except_block", start, b, lines)
            first["context_line"] = start
            out.append(first)
            continue
        body = _text(lines, a, b)
        code = header + "\n" + body
        sent = code[:MAX_REGION_CHARS]
        sent_body = sent[len(header) + 1:]
        out.append({"path": path, "line": a, "end_line": b,
                    "kind": "except_block", "code": sent, "digest": digest(code),
                    "chars": len(sent), "context_line": start,
                    "sent_end_line": b if len(sent) == len(code) else
                                     a + sent_body.count("\n") - 1})
    return out


def partition_text(path, text, stats=None):
    """Every partition of one file, before cross-file dedupe. `stats`, when
    given, counts what was left out or folded and why, so nothing is dropped
    silently.

    The partitions do not overlap except where they must. A comment run that
    sits directly on a function is read WITH that function; any other comment
    run starts its own module-level slice, together with the code under it.
    An except handler is not cut out separately when a partition already
    carries all of it; that partition's kind gains "+except_block" instead,
    so the judgment is told what is inside. Only a handler that straddles a
    slice boundary gets its own region. Long handlers are sliced separately,
    with their header repeated so every body slice keeps the failure context.
    """
    stats = stats if stats is not None else {}
    lines = text.splitlines()
    if not lines:
        return []
    suffix = os.path.splitext(path)[1]
    handler_headers = {}
    spans = (python_spans(text, handler_headers) if suffix == ".py" else
             js_spans(text, handler_headers))
    if spans is None:
        stats["unparsed_files"] = stats.get("unparsed_files", 0) + 1
    spans = spans or []
    functions = sorted((s, e) for k, s, e in spans if k == "function")
    excepts = sorted((s, e) for k, s, e in spans if k == "except_block")

    def fits(s, e):
        return len(_text(lines, s, e)) <= MAX_REGION_CHARS

    long_except = set()
    for s, e in excepts:
        if not fits(s, e):
            long_except.update(range(s, e + 1))

    whole = [(s, e) for s, e in functions if fits(s, e)]
    outer = [(s, e) for s, e in whole
             if not any((ws, we) != (s, e) and ws <= s and e <= we for ws, we in whole)]
    in_function = [False] * (len(lines) + 2)
    for s, e in functions:
        for k in range(s, e + 1):
            in_function[k] = True

    # Comment runs outside functions: attach to the function directly under
    # them when the pair still fits, otherwise mark a slice break.
    starts = {s: i for i, (s, _e) in enumerate(outer)}
    attached, breaks = {}, set()
    for s, e in comment_runs(lines, suffix):
        if in_function[s]:
            continue
        nxt = e + 1
        while nxt <= len(lines) and not lines[nxt - 1].strip():
            nxt += 1
        if nxt in starts and fits(s, outer[starts[nxt]][1]):
            attached[nxt] = s
            for k in range(s, nxt):
                in_function[k] = True
        else:
            breaks.add(s)

    parts = []
    for s, e in outer:
        if s in attached:
            parts.append(_part(path, "comment_block+function", attached[s], e, lines))
        else:
            parts.append(_part(path, "function", s, e, lines))
    # Functions too long for one request: slice the lines no fitting inner
    # function already covers.
    covered_by_whole = [False] * (len(lines) + 2)
    for s, e in outer:
        for k in range(s, e + 1):
            covered_by_whole[k] = True
    for s, e in functions:
        if fits(s, e) or any((os_, oe) != (s, e) and os_ <= s and e <= oe
                             and not fits(os_, oe) for os_, oe in functions):
            continue       # fits, or an enclosing long function slices it
        for lo, hi in _uncovered_runs(s, e, lambda a, _b: covered_by_whole[a] or a in long_except):
            for a, b in _slices(lines, lo, hi):
                parts.append(_part(path, "function_part", a, b, lines))
    # Module-level code outside every function, a new slice at each comment run.
    for lo, hi in _uncovered_runs(1, len(lines), lambda a, _b: in_function[a] or a in long_except):
        cuts = sorted(c for c in breaks if lo < c <= hi)
        for run_lo, run_hi in zip([lo] + cuts, [c - 1 for c in cuts] + [hi]):
            for a, b in _slices(lines, run_lo, run_hi):
                if _text(lines, a, b).strip():
                    kind = "comment_block" if a in breaks else "long_span"
                    parts.append(_part(path, kind, a, b, lines))
    # Except handlers: fold into the partition that carries them whole.
    for s, e in excepts:
        if not fits(s, e):
            parts.extend(_handler_parts(path, s, e, lines, handler_headers[(s, e)]))
            continue
        home = next((p for p in parts if p["line"] <= s and e <= p["end_line"]), None)
        if home is not None:
            if "except_block" not in home["kind"].split("+"):
                home["kind"] += "+except_block"
            stats["except_folded"] = stats.get("except_folded", 0) + 1
            continue
        lo = max(1, s - EXCEPT_CONTEXT_BEFORE)
        a, b = (lo, e) if fits(lo, e) else (s, e)
        parts.append(_part(path, "except_block", a, b, lines))
    kept = [p for p in parts if not trivial(p["code"])]
    stats["trivial"] = stats.get("trivial", 0) + len(parts) - len(kept)
    kept.sort(key=lambda p: (p["line"], p["end_line"]))
    return kept


def _uncovered_runs(start, end, is_covered):
    runs, lo = [], None
    for k in range(start, end + 1):
        if is_covered(k, k):
            if lo is not None:
                runs.append((lo, k - 1))
                lo = None
        elif lo is None:
            lo = k
    if lo is not None:
        runs.append((lo, end))
    return runs


# --- dedupe and packing --------------------------------------------------

def dedupe(parts):
    """Drop exact repeats (anywhere in the tree) and partitions a kept
    partition in the same file already contains in full. Returns
    (kept, stats); a dropped repeat is recorded on its twin as `also_at`."""
    kept, by_digest = [], {}
    stats = {"raw": len(parts), "exact_duplicates": 0, "contained": 0}
    whole_by_path = {}
    for p in parts:
        if p["kind"] == "function":
            whole_by_path.setdefault(p["path"], []).append((p["line"], p["end_line"]))
    for p in parts:
        if p["kind"] != "function" and any(
                s <= p["line"] and p["end_line"] <= e
                for s, e in whole_by_path.get(p["path"], ())):
            stats["contained"] += 1
            continue
        fully_sent = p["sent_end_line"] == p["end_line"]
        twin = by_digest.get(p["digest"]) if fully_sent else None
        if twin is not None:
            twin.setdefault("also_at", []).append(f'{p["path"]}:{p["line"]}')
            twin.setdefault("also_at_spans", []).append({
                "path": p["path"], "line": p["line"],
                "end_line": p["end_line"],
                "sent_end_line": p["sent_end_line"]})
            stats["exact_duplicates"] += 1
            continue
        p = dict(p)
        if fully_sent:
            by_digest[p["digest"]] = p
        kept.append(p)
    stats["kept"] = len(kept)
    return kept, stats


def pack(parts, cap=MAX_REGION_CHARS, small=PACK_BELOW_CHARS,
         source_lines=None):
    """Merge runs of small adjacent partitions in one file into one region.

    Each region is one request, so twenty three-line helpers in a row cost
    twenty requests unpacked. Only neighbours whose combined text still fits
    the cap are merged, and the region keeps every constituent kind.
    """
    out = []
    for p in parts:
        prev = out[-1] if out else None
        candidate = None
        if prev and prev["path"] == p["path"] and p["line"] > prev["end_line"]:
            if source_lines is not None:
                candidate = _text(source_lines[p["path"]], prev["line"], p["end_line"])
            elif p["line"] == prev["end_line"] + 1:
                candidate = prev["code"] + "\n" + p["code"]
        if (prev and prev["path"] == p["path"] and p["chars"] < small
                and prev["chars"] < cap and p["line"] > prev["end_line"]
                and candidate is not None and len(candidate) <= cap
                and not prev.get("also_at") and not p.get("also_at")
                and "context_line" not in prev and "context_line" not in p
                and prev["sent_end_line"] == prev["end_line"]
                and p["sent_end_line"] == p["end_line"]):
            prev["code"] = candidate
            prev["chars"] = len(candidate)
            prev["end_line"] = p["end_line"]
            prev["sent_end_line"] = p["sent_end_line"]
            if p["kind"] not in prev["kind"].split("+"):
                prev["kind"] += "+" + p["kind"]
            prev["digest"] = digest(prev["code"])
            prev["packed"] = prev.get("packed", 1) + 1
            continue
        out.append(dict(p))
    return out


def partition(paths, repo=REPO, reader=None):
    """Partition, dedupe and pack `paths`. Returns (regions, stats)."""
    read = reader or (lambda rel: _read(os.path.join(repo, rel)))
    raw, timings, dropped, source_lines = [], [], {}, {}
    for rel in paths:
        try:
            text = read(rel)
        except OSError:
            continue
        source_lines[rel] = text.splitlines()
        t0 = time.perf_counter()
        raw.extend(partition_text(rel, text, dropped))
        timings.append(time.perf_counter() - t0)
    kept, stats = dedupe(raw)
    regions = pack(kept, source_lines=source_lines)
    stats.update(dropped)
    stats.update({"files": len(paths), "regions": len(regions),
                  "partition_seconds_p95": percentile(timings, 95),
                  "partition_seconds_total": sum(timings)})
    return regions, stats


def percentile(values, pct):
    if not values:
        return None
    ordered = sorted(values)
    k = max(0, math.ceil(pct / 100.0 * len(ordered)) - 1)
    return ordered[k]


# --- judging -------------------------------------------------------------

def review(regions, *, client=None, api_key=None, workers=8):
    """The existing batched typed judgment, one request per region.

    Each region goes through jev_code_review._review, the same request the
    regex scanner's regions go through: every question in ONE ask. Returns
    one row per region with `scores`, plus `seconds` and the vendor's `usage`
    so p95 and token cost are measured rather than estimated. A failed request
    is an `_error` row; a scan never aborts.
    """
    from concurrent.futures import ThreadPoolExecutor
    jcr = _load("jev_code_review_for_partition", "ops/jev_code_review.py")
    tsc = client or jcr._client()

    def one(region):
        t0 = time.perf_counter()
        try:
            scores, answer = jcr._review(region, None, client=tsc, api_key=api_key)
            usage = answer.get("usage") if isinstance(answer, dict) else None
        except Exception as exc:                       # a scan never aborts
            scores, usage = {"_error": f"{type(exc).__name__}: {exc}"[:160]}, None
        return dict(region, scores=scores, usage=usage,
                    seconds=time.perf_counter() - t0)

    with ThreadPoolExecutor(max_workers=workers) as pool:
        return list(pool.map(one, regions))


def request_chars(region, questions=None):
    """Characters one request sends: the region state plus every question.
    Divided by CHARS_PER_TOKEN this is the offline token estimate; the live
    run replaces it with the vendor's own `usage` when the response has one."""
    if questions is None:
        questions = _load("jev_code_review_for_cost", "ops/jev_code_review.py").QUESTIONS
    state = {"region": {"path": region["path"], "line": region["line"],
                        "why_it_was_flagged": region["kind"],
                        "code": region["code"]}}
    return len(json.dumps(state)) + sum(len(q) for q in questions.values())


CHARS_PER_TOKEN = 4.0     # an estimate; the report labels it as one


# --- verifying a proposed edit -------------------------------------------
#
# Jev answers typed questions; it never writes code. An edit comes from a
# session or a person as {"path": ..., "new_text": ...}. The code below checks
# it the way CI would check that file, inside a detached worktree of HEAD, and
# returns a verdict. It has no code path that writes under `repo`.

def _git_env():
    return _load("git_env_for_partition", "ops/git_env.py").scrubbed_env()


def paired_selftests(rel, repo=REPO):
    """ops/foo_bar.py -> ops/foo-bar-selftest.py / ops/foo_bar-selftest.py,
    plus any ops selftest that names the file's path."""
    base = os.path.splitext(os.path.basename(rel))[0]
    names = {f"ops/{base}-selftest.py", f"ops/{base.replace('_', '-')}-selftest.py"}
    found = sorted(n for n in names if os.path.isfile(os.path.join(repo, n)))
    if not found:
        ops = os.path.join(repo, "ops")
        for name in sorted(os.listdir(ops)) if os.path.isdir(ops) else ():
            if name.endswith("-selftest.py"):
                try:
                    body = _read(os.path.join(ops, name))
                except OSError:
                    continue
                if rel in body:
                    found.append(f"ops/{name}")
    return found[:3]


def default_checks(rel, worktree, repo=REPO):
    """The normal per-file checks: syntax, then the file's paired selftests."""
    checks = []
    if rel.endswith(".py"):
        checks.append(("syntax", [sys.executable, "-m", "py_compile", rel]))
    elif rel.endswith((".js", ".mjs", ".cjs")):
        node = shutil.which("node")
        checks.append(("syntax", [node, "--check", rel] if node else None))
    for test in paired_selftests(rel, repo):
        checks.append((f"selftest:{test}", [sys.executable, test]))
    return checks


def _write_edit(worktree, rel, new_text):
    """Write within the disposable tree without following any path symlink."""
    parts = rel.split("/")
    directory = os.open(worktree, os.O_RDONLY | os.O_DIRECTORY)
    try:
        for name in parts[:-1]:
            try:
                os.mkdir(name, dir_fd=directory)
            except FileExistsError:
                pass
            child = os.open(name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW,
                            dir_fd=directory)
            os.close(directory)
            directory = child
        try:
            target = os.open(parts[-1], os.O_WRONLY | os.O_CREAT | os.O_EXCL,
                             0o644, dir_fd=directory)
        except FileExistsError:
            target = os.open(parts[-1], os.O_WRONLY | os.O_NOFOLLOW | os.O_NONBLOCK,
                             dir_fd=directory)
            if not stat.S_ISREG(os.fstat(target).st_mode):
                os.close(target)
                raise OSError("edit target is not a regular file")
            os.ftruncate(target, 0)
        with os.fdopen(target, "w", encoding="utf-8") as handle:
            handle.write(new_text)
    finally:
        os.close(directory)


def verify_edit(edit, *, repo=REPO, checks=None, env=None, timeout=600):
    """Run the normal checks against a proposed edit. Never applies it.

    Returns {"path", "verdict", "checks": [{name, status, tail}],
    "applied_to_live_tree": False}. verdict is "passes_checks" only when
    every check ran and passed; a check that could not run makes it
    "not_verified", because a skipped check is not a pass.
    """
    rel = edit.get("path") if isinstance(edit, dict) else None
    new_text = edit.get("new_text") if isinstance(edit, dict) else None
    result = {"path": rel, "verdict": "not_verified", "checks": [],
              "applied_to_live_tree": False}
    if not isinstance(rel, str) or not isinstance(new_text, str) or \
            os.path.isabs(rel) or any(part in ("", ".", "..")
                                      for part in rel.split("/")):
        result["error"] = "edit needs a repo-relative path and new_text"
        return result
    env = env or _git_env()
    tmp = tempfile.mkdtemp(prefix="jev-edit-verify-")
    worktree = os.path.join(tmp, "wt")
    try:
        add = subprocess.run(["git", "worktree", "add", "--detach", "--quiet",
                              worktree, "HEAD"], cwd=repo, env=env,
                             capture_output=True, text=True, timeout=120)
        if add.returncode != 0:
            result["error"] = "worktree: " + (add.stderr or "")[-300:]
            return result
        try:
            _write_edit(worktree, rel, new_text)
        except OSError as exc:
            result["error"] = f"edit path is unsafe or unwritable: {type(exc).__name__}"
            return result
        plan = checks(rel, worktree) if callable(checks) else \
            default_checks(rel, worktree, repo)
        all_ran, all_passed = True, True
        for name, argv in plan:
            if not argv:
                result["checks"].append({"name": name, "status": "not_run",
                                         "tail": "tool unavailable"})
                all_ran = False
                continue
            try:
                proc = subprocess.run(argv, cwd=worktree, env=env, text=True,
                                      capture_output=True, timeout=timeout)
                status = "pass" if proc.returncode == 0 else "fail"
                tail = ((proc.stdout or "") + (proc.stderr or ""))[-600:]
            except (OSError, subprocess.TimeoutExpired) as exc:
                status, tail = "not_run", f"{type(exc).__name__}: {exc}"[:300]
                all_ran = False
            if status == "fail":
                all_passed = False
            result["checks"].append({"name": name, "status": status, "tail": tail})
        if not plan:
            all_ran = False
        result["verdict"] = ("fails_checks" if not all_passed else
                             "passes_checks" if all_ran else "not_verified")
        return result
    finally:
        subprocess.run(["git", "worktree", "remove", "--force", worktree],
                       cwd=repo, env=env, capture_output=True, timeout=120)
        shutil.rmtree(tmp, ignore_errors=True)
