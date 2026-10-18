#!/usr/bin/env python3
"""Compare numbered builder obligations with evidence at one PR head.

    bin/brief-check <owner/repo> <pr> [--brief PATH] [--post] [--jev] [--json]
    bin/brief-check --stamp <brief>

Builders put the helper's `Brief: <path> sha256:<hex>` line in the PR body.
Paths with spaces are supported. Relative paths resolve against --brief-root,
or this repository's canonical checkout. Hash conflicts invalidate grading.

Only complete mechanical asks can be met deterministically. Changed paths,
body mentions and CI checks are evidence for compound or semantic obligations,
which remain needs judgment. Missing base evidence stays unknown. Every row
preserves the complete obligation and evidence; this tool never approves.

--post requires the brief's bytes to match a file already present at the
inspected PR head. Untracked local input cannot be published. One orchestrator
host owns publication, serialized across its processes and worktrees by a
per-repository/per-PR lock. A persistent journal refuses repeat creates after
uncertain outcomes until the original comment is read back. GitHub's comment
API has no cross-host lock or head-conditional write; additional publishers
must use that same host. Head checks bracket writes and all source links pin
the inspected revision.

--jev is opt-in advisory judgment. Shared translation and observation policy
owns its result; vendor or loader failures preserve the deterministic report.
"""
from __future__ import annotations

import argparse
import base64
import contextlib
import fcntl
import hashlib
import importlib.util
import json
import math
import os
import re
import subprocess
import sys
import time
from pathlib import Path
from urllib.parse import quote

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MARKER = "<!-- brief-check -->"
VERDICTS = ("met", "not met", "needs judgment")
STAMP_RE = re.compile(r"^Brief: (.+?) sha256:([0-9a-f]{64})\s*$", re.M)

# A list header is a line that says the list is required. The job-conduct
# section ("RULES FOR THIS JOB (all required):") is not a build requirement.
HEADER_RE = re.compile(r"required", re.I)
NOT_REQUIREMENTS_RE = re.compile(r"^\W*(RULES|BOUNDARIES)\b", re.I)
ITEM_RE = re.compile(r"^\s*(\d+)\.(?:\s+(.*))?$")
# A caps label ("BOUNDARIES:", "RULES FOR THIS JOB (...):") or a markdown heading ends a list.
SECTION_RE = re.compile(r"^(#|[A-Z][A-Z /-]{2,}(\([^)]*\))?:)")

BODY_RE = re.compile(r"\bPR (body|description)\b|\bin the PR\b", re.I)
PATH_RE = re.compile(r"(?<![\w/~.:@-])(\.?[A-Za-z0-9_][A-Za-z0-9_.-]*/(?:[A-Za-z0-9_.-]+/)*(?:[A-Za-z0-9_-][A-Za-z0-9_.-]*\.[A-Za-z0-9]{1,6})?)(?![\w*/<-])")
TABLE_RE = re.compile(r"^\|.*\|", re.M)
TEST_FILE_RE =re.compile(r"(^|/)(tests?|__tests__|e2e)/|(^|/)test[-_][^/]*$|[-_.](selftest|test|spec)\.[A-Za-z0-9]+$")
STOP = set("""about above after again against also because been before being below between both
build builder change changes code does done each every file files first from have into just like
made make must name never only other over same should show some such than that their them then
there these they this those through under until very want when where which while will with
without would your body description section include report state evidence list item items put what""".split())
GREEN = {"SUCCESS"}
PENDING_STATES = {"PENDING", "EXPECTED", "QUEUED", "IN_PROGRESS", "WAITING", "REQUESTED"}
REVIEW_LINE_RE = re.compile(r"^(APPROVE|Reviewed-SHA:|REVIEW: BLOCKED|CHANGES REQUESTED)", re.M)
COMMENT_ENDPOINT_RE = re.compile(r"^repos/[\w.-]+/[\w.-]+/issues/(\d+/comments|comments/\d+)$")


class BriefError(Exception):
    pass


# ── brief ─────────────────────────────────────────────────────────────────────

def parse_requirements(text):
    """The numbered items under the first header that says they are required."""
    lines = text.splitlines()
    for start, line in enumerate(lines):
        if HEADER_RE.search(line) and not NOT_REQUIREMENTS_RE.match(line) and (
                line.lstrip().startswith("#") or line.rstrip().endswith(":") or SECTION_RE.match(line)):
            items = _numbered_list(lines[start + 1:])
            if items:
                return items
    raise BriefError("no numbered list under a 'required' header in the brief")


def _numbered_list(lines):
    items = []
    separated = False
    for line in lines:
        item = ITEM_RE.match(line)
        if item:
            items.append({"n": int(item.group(1)), "text": (item.group(2) or "").strip()})
        elif not line.strip():
            separated = True
            continue
        elif (SECTION_RE.match(line) or NOT_REQUIREMENTS_RE.match(line) or not items
              or separated and not line[0].isspace()):
            if items:
                break
        else:
            items[-1]["text"] = (items[-1]["text"] + " " + line.strip()).strip()
        separated = False
    if any(not item["text"] for item in items):
        raise BriefError("empty requirement; supply an obligation before checking")
    numbers = [item["n"] for item in items]
    if len(numbers) != len(set(numbers)):
        raise BriefError("duplicate requirement numbers; disambiguate the brief before checking")
    return items


def sentences(text):
    """Split on . ! ? followed by a capital, never inside parentheses."""
    out, depth, start = [], 0, 0
    for i, ch in enumerate(text):
        depth += (ch == "(") - (ch == ")")
        if ch in ".!?" and depth <= 0 and re.match(r"\s+[A-Z0-9\"“]", text[i + 1:]):
            out.append(text[start:i + 1].strip())
            start = i + 1
    tail = text[start:].strip()
    return [s for s in out + [tail] if s]


def stamp_line(path, sha):
    return f"Brief: {path} sha256:{sha}"


def stamp_preamble(brief_path, recorded_as=None):
    try:
        with open(brief_path, encoding="utf-8") as handle:
            sha = hashlib.sha256(handle.read().encode()).hexdigest()
    except (OSError, UnicodeError) as exc:
        raise BriefError(f"brief unavailable ({type(exc).__name__})") from None
    line = stamp_line(recorded_as or _recorded_path(brief_path), sha)
    return ("\nBRIEF RECORD (required): put this line, verbatim, on its own line in the PR body, "
            "so the reviewer's brief check reads the brief you ran:\n" + line + "\n")


def _recorded_path(brief_path):
    absolute = os.path.abspath(brief_path)
    root = _main_checkout(os.path.dirname(absolute))
    return os.path.relpath(absolute, root) if root and absolute.startswith(root + os.sep) else absolute


def _main_checkout(directory):
    """The main checkout owning `directory` (worktrees share its common dir)."""
    try:
        common = subprocess.run(["git", "-C", directory, "rev-parse", "--path-format=absolute", "--git-common-dir"],
                                capture_output=True, text=True, check=True, timeout=30).stdout.strip()
    except (OSError, subprocess.SubprocessError):
        return None
    return os.path.dirname(common)


def read_stamp(body):
    found = STAMP_RE.search(body or "")
    return (found.group(1), found.group(2)) if found else None


def stamp_state(recorded_sha, brief_text):
    if recorded_sha is None:
        return "not recorded in the PR body"
    current = hashlib.sha256(brief_text.encode()).hexdigest()
    return "matches" if current == recorded_sha else "changed since the builder ran"


# ── evidence ──────────────────────────────────────────────────────────────────

def _ci(checks):
    """(state, failed, pending, green) over a statusCheckRollup."""
    failed, pending, green = [], [], []
    for check in checks:
        name = check.get("name") or check.get("context") or "check"
        url = check.get("detailsUrl") or check.get("targetUrl") or ""
        if check.get("__typename") == "StatusContext" or "state" in check and "status" not in check:
            state = (check.get("state") or "").upper()
            bucket = green if state == "SUCCESS" else pending if state in PENDING_STATES else failed
            bucket.append((name, url, state))
            continue
        if (check.get("status") or "").upper() != "COMPLETED":
            pending.append((name, url, check.get("status")))
        elif (check.get("conclusion") or "").upper() in GREEN:
            green.append((name, url, check.get("conclusion")))
        else:
            failed.append((name, url, check.get("conclusion")))
    state = "failed" if failed else "pending" if pending or not checks else "green"
    return state, failed, pending, green


def _link(name, url):
    return f"[{name}]({url})" if url else name


def _diff_link(pr, path):
    repo_url = pr['url'].split('/pull/')[0]
    return f"[`{path}`]({repo_url}/blob/{pr['headRefOid']}/{quote(path, safe='/')})"


def _terms(text):
    """{stem: word as written} for the words that carry the ask."""
    text = re.sub(r"\([^)]*\)", " ", text)
    words = {}
    for word in re.findall(r"[a-z0-9][a-z0-9-]*", text.lower()):
        if len(word) >= 4 and word not in STOP and _stem(word) not in STOP:
            words.setdefault(_stem(word), word)
    return words


def _stem(word):
    return word[:-1] if len(word) > 4 and word.endswith("s") else word


def _paragraphs(body):
    return [p.strip() for p in re.split(r"\n\s*\n", body or "") if p.strip()]


def _top_level_split(text, pattern):
    """Split on `pattern` only where no parenthesis is open."""
    parts, depth, start = [], 0, 0
    for at, ch in enumerate(text):
        depth += (ch == "(") - (ch == ")")
        found = re.match(pattern, text[at:]) if depth <= 0 else None
        if found and at >= start:
            parts.append(text[start:at])
            start = at + found.end()
    return parts + [text[start:]]


def _body_probe(sentence, body):
    """Each comma- or and-separated thing the sentence asks the body for must
    appear in some paragraph: 60% of its terms, at least one."""
    if not (body or "").strip():
        return "fail", "PR body is empty"
    paragraphs = [(p, {_stem(w) for w in re.findall(r"[a-z0-9][a-z0-9-]*", p.lower())}) for p in _paragraphs(body)]
    asks = [_terms(c) for c in _top_level_split(BODY_RE.sub(" ", sentence), r"[,;]\s*|\s+and\s+")]
    asks = [a for a in asks if a]
    if not asks:
        return "judge", "PR body: nothing in the requirement to match against"
    def hits(ask, paragraph):
        text, words = paragraph
        return sum(t in words or (t == "table" and bool(TABLE_RE.search(text))) for t in ask)

    found, missing = [], []
    for ask in asks:
        best = max(paragraphs, key=lambda p: hits(ask, p))
        enough = hits(ask, best) >= max(1, math.ceil(0.6 * len(ask)))
        (found if enough else missing).append((ask, best[0]))
    if not missing:
        excerpts = dict.fromkeys(re.sub(r"\s+", " ", p) for _, p in found)
        return "judge", "PR body: mentions require semantic review: " + " … ".join(f"“{e}”" for e in excerpts)
    return "judge", "PR body has nothing on: " + "; ".join(" ".join(a.values()) for a, _ in missing)


def _test_intent(sentence):
    if re.search(r"\b(?:without|no|not|never|do not|don't)\s+(?:adding?\s+|new\s+)?tests?\b", sentence, re.I):
        return None
    if re.search(r"\b(?:add|write|create)\s+(?:\w+\s+){0,3}(?:tests?|selftests?)\b|\btests? first\b", sentence, re.I):
        return "add"
    if re.search(r"\b(?:run|execute)\s+(?:existing\s+|the\s+)?(?:tests?|selftests?)\b", sentence, re.I):
        return "run"
    return None


def _execution_lanes(check):
    name = check.get("name") or check.get("context") or ""
    if name == "ops/ci.sh --strict":
        return {"unit", "gates"}
    prefix = "ops/ci.sh --strict --only "
    if name.startswith(prefix):
        return set(name[len(prefix):].replace(",", " ").split()) & {"unit", "gates"}
    return set()


def _test_lane(path):
    if re.fullmatch(r"ops/[^/]+-selftest\.py|tools/test-[^/]+\.py|tools/room-bridge/test_[^/]+_unit\.py|tools/room-bridge/test_activation_reliability\.py", path):
        return "gates"
    if re.match(r"(?:mcp-server|control-room|workspace|practice-plugin)/test/", path):
        return "unit"
    return None


def _tests_probe(pr, changed, intent):
    tests = [p for p, file in changed.items() if TEST_FILE_RE.search(p)
             and file.get("changeType") != "DELETED"
             and re.search(r"\.(?:py|mjs|cjs|js|ts|tsx|swift|sh)$", p)]
    if intent == "add" and not tests:
        removed = any(TEST_FILE_RE.search(p) and f.get("changeType") == "DELETED" for p, f in changed.items())
        state = "fail" if removed or not any(TEST_FILE_RE.search(p) for p in changed) else "judge"
        return [(state, "no executable test file delivered in the diff")]
    evidence = [("pass", "tests in diff: " + ", ".join(_diff_link(pr, p) for p in tests))] if tests else []
    known_repo = (pr.get("url") or "").startswith("https://github.com/jbookout/carr-system/pull/")
    execution = [c for c in pr.get("statusCheckRollup") or [] if known_repo and _execution_lanes(c)]
    state, failed, pending, green = _ci(execution)
    if not execution:
        return evidence + [("judge", "test execution unavailable: no recognized test check")]
    if any((c or "").upper() in {"SKIPPED", "NEUTRAL"} for _, _, c in failed):
        return evidence + [("judge", "test execution skipped or neutral")]
    if state == "failed":
        return evidence + [("fail", "test execution: " + "; ".join(f"{c} {_link(n, u)}" for n, u, c in failed))]
    if state == "pending":
        return evidence + [("judge", "CI pending: test execution has not completed")]
    if intent == "add":
        covered = set().union(*(_execution_lanes(c) for c in execution))
        unresolved = [p for p in tests if _test_lane(p) not in covered]
        if unresolved:
            return evidence + [("judge", "test collection not covered by successful execution: " + ", ".join(unresolved))]
    return evidence + [("pass", "test execution: " + "; ".join(_link(n, u) for n, u, _ in green))]


def _path_probe(pr, changed, path, exists_in_base, local_reference, *, command=None):
    is_dir = path.endswith("/")
    files = [f for p, f in changed.items() if p == path or (is_dir and p.startswith(path))]
    if files and command is None:
        if all(f.get("changeType") == "DELETED" for f in files):
            return "fail", f"`{path}` removed from the inspected head"
        return "pass", "diff: " + (_diff_link(pr, path) if not is_dir else f"`{path}` ({len(files)} files)")
    for check in (pr.get("statusCheckRollup") or []) if command else []:
        name = check.get("name") or check.get("context") or ""
        if name == command:
            state, _, _, _ = _ci([check])
            url = check.get("detailsUrl") or check.get("targetUrl") or ""
            conclusion = (check.get("conclusion") or "").upper()
            verdict = "judge" if conclusion in {"SKIPPED", "NEUTRAL"} else {"green": "pass", "failed": "fail", "pending": "judge"}[state]
            return verdict, f"check {_link(f'`{name}`', url)} {state}"
    if command:
        return "judge", f"execution unavailable for `{command}` at the inspected head"
    present = exists_in_base(path) if exists_in_base else None
    if present:
        return "neutral", f"`{path}` exists at the bound base revision, unchanged"
    if local_reference is not None and local_reference(path):
        return "neutral", f"`{path}` names a local untracked reference"
    if present is None:
        return "judge", f"`{path}` not in the diff (base revision unavailable)"
    return "fail", f"`{path}` is in neither the diff nor the bound base revision"


def _path_groups(sentence):
    matches = list(PATH_RE.finditer(sentence))
    groups = []
    for i, match in enumerate(matches):
        if i and re.fullmatch(r"\s+or\s+", sentence[matches[i-1].end():match.start()], re.I):
            groups[-1].append(match.group())
        else:
            groups.append([match.group()])
    return groups


def _sentence(pr, changed, sentence, exists_in_base, local_reference):
    probes = []
    intent = _test_intent(sentence)
    if intent:
        probes += _tests_probe(pr, changed, intent)
    if BODY_RE.search(sentence):
        probes.append(_body_probe(sentence, pr.get("body")))
    for group in _path_groups(sentence):
        results = [_path_probe(pr, changed, p, exists_in_base, local_reference,
                               command=sentence.strip()[4:].rstrip(".") if re.match(r"Run\b", sentence, re.I) else None) for p in group]
        probes.append(min(results, key=lambda r: ("pass", "neutral", "judge", "fail").index(r[0])))
    # Only these complete, mechanical asks can be certified. Every other
    # sentence may contain obligations that a path or word match cannot prove.
    paths = PATH_RE.sub("PATH", sentence.strip().rstrip("."))
    complete = bool(re.fullmatch(r"(?:Add|Create|Change|Update|Modify) PATH(?: or PATH)*", paths, re.I)
                    or re.fullmatch(r"(?:Add|Write|Create|Run|Execute) (?:existing )?(?:tests|selftests)", paths, re.I)
                    or re.fullmatch(r"Run PATH --strict", paths, re.I))
    states = {s for s, _ in probes}
    verdict = "fail" if "fail" in states else "pass" if complete and states == {"pass"} else "judge"
    evidence = [e for _, e in probes]
    if verdict == "judge":
        evidence.append("requires review of the complete obligation: “" + sentence + "”")
    return verdict, evidence


def check(brief_text, pr, *, exists_in_base=None, local_reference=None, brief_path=None):
    """The report: one row per numbered requirement, verdict plus evidence.

    exists_in_base(path) and local_reference(path) answer whether a named path
    is on the base branch, or is a local file the repository does not track
    (present on disk or gitignored, like the out/orch briefs). None means
    unknown, which turns a missing path into needs judgment rather than not met.
    """
    changed = {f["path"]: f for f in pr.get("files") or []}
    stamp = read_stamp(pr.get("body"))
    rows = []
    for item in parse_requirements(brief_text):
        results = [_sentence(pr, changed, s, exists_in_base, local_reference) for s in sentences(item["text"])]
        found = [r for r, _ in results]
        verdict = "not met" if "fail" in found else "met" if all(r == "pass" for r in found) else "needs judgment"
        evidence = list(dict.fromkeys(e for _, ev in results for e in ev))
        rows.append({"n": item["n"], "text": item["text"], "verdict": verdict, "evidence": evidence})
    provenance = stamp_state(stamp[1] if stamp else None, brief_text)
    if provenance == "changed since the builder ran":
        for row in rows:
            row["verdict"] = "needs judgment"
            row["evidence"].append("brief hash conflict: resolve the builder's recorded source before grading")
    state, _, _, _ = _ci(pr.get("statusCheckRollup") or [])
    return {
        "pr": pr.get("number"), "url": pr.get("url"), "head": pr.get("headRefOid"),
        "brief": brief_path or (stamp[0] if stamp else None),
        "brief_state": provenance, "brief_sha256": hashlib.sha256(brief_text.encode()).hexdigest(),
        "ci": state, "requirements": rows,
    }


# ── Jev on the residue ────────────────────────────────────────────────────────

def add_jev(report, *, judge=None, noul=None):
    residue = [r for r in report["requirements"] if r["verdict"] == "needs judgment"]
    if not residue:
        return report
    if report_status(report) == 2:
        for row in residue:
            row["jev"] = "Jev unavailable (brief provenance conflict)"
        return report
    policy = None
    try:
        policy = _jev_policy()
        if judge is None or noul is None:
            judge, noul = _jev()
        questions = {f"r{r['n']}": noul(
            f"Requirement {r['n']}: {r['text']}\n"
            "Using only the PR evidence in the state, was this requirement met?") for r in residue}
        subject = {"pr_body": report.get("body") or "", "head": report["head"],
                   "evidence": {f"r{r['n']}": r["evidence"] for r in residue}}
        answer = judge(subject, questions, caller="brief_check", timeout=20, retries=0,
                       deadline=time.monotonic() + 20)
        for r in residue:
            decision = policy.read(answer, f"r{r['n']}")
            word = "unsure" if decision["escalate"] else "likely met" if decision["outcome"] == "yes" else "likely not met"
            r["jev"] = f"Jev (advisory, needs-judgment residue): {word}, p={decision['value']:.2f}"
        policy.record("brief_check", f"{report['url']}@{report['head']}", answer,
                      {f"r{r['n']}": r["verdict"] for r in residue},
                      family="brief_requirement", consequence_class="review_advisory",
                      downstream_action="advisory_only")
    except Exception as exc:
        for r in residue:
            r["jev"] = f"Jev unavailable ({type(exc).__name__})"
        if policy:
            policy.record("brief_check", f"{report['url']}@{report['head']}", None,
                          error=type(exc).__name__, family="brief_requirement",
                          consequence_class="review_advisory", downstream_action="deterministic_only")
    return report


def _load_module(name):
    spec = importlib.util.spec_from_file_location(name, os.path.join(REPO, "ops", f"{name}.py"))
    if spec is None or spec.loader is None:
        raise BriefError(f"cannot load ops/{name}.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _jev_policy():
    return _load_module("jev_judge")


def _jev():
    return _jev_policy().judge, _load_module("typesafe_client").noul


# ── comment ───────────────────────────────────────────────────────────────────

def _cell(text):
    return re.sub(r"\s+", " ", text).replace("|", "\\|")


def render_comment(report):
    counts = {v: sum(r["verdict"] == v for r in report["requirements"]) for v in VERDICTS}
    lines = [
        MARKER,
        "### Brief check (evidence only; this comment never approves)",
        "",
        f"Checked head: `{report['head']}` · CI: {report['ci']} · brief: `{report['brief'] or 'unknown'}` "
        f"({report['brief_state']})",
        "",
        f"**{counts['met']} met · {counts['not met']} not met · {counts['needs judgment']} need judgment**",
        "",
        "| # | Requirement | Verdict | Evidence |",
        "|---|---|---|---|",
    ]
    for r in report["requirements"]:
        evidence = "<br>".join(_cell(e) for e in r["evidence"] + ([r["jev"]] if r.get("jev") else []))
        text = r["text"]
        lines.append(f"| {r['n']} | {_cell(text)} | **{r['verdict']}** | {evidence} |")
    lines += ["", "<sub>ops/brief-check.py: met = every sentence of the requirement has deterministic evidence; "
              "needs judgment = the reviewer decides. Re-runs edit this comment in place.</sub>"]
    text = "\n".join(lines) + "\n"
    assert not REVIEW_LINE_RE.search(text), "a brief-check comment must never read as a review verdict"
    return text


# ── gh, comment endpoints only ────────────────────────────────────────────────

def guarded(args):
    args = list(args)
    if args[:2] == ["pr", "view"]:
        return args
    if args and args[0] == "api":
        methods = [args[i + 1].upper() for i, a in enumerate(args[:-1]) if a in ("-X", "--method")]
        methods += [a.split("=", 1)[1].upper() for a in args if a.startswith("--method=")]
        method = methods[0] if len(methods) == 1 else "GET" if not methods else "INVALID"
        endpoint = next((a for a in args[1:] if a.startswith("repos/") or a == "user"), "")
        read = endpoint == "user" or bool(re.fullmatch(r"repos/[\w.-]+/[\w.-]+/contents/[^?]+\?ref=[0-9a-f]{40}", endpoint))
        if (method in ("GET", "POST", "PATCH") and COMMENT_ENDPOINT_RE.fullmatch(endpoint)) or (method == "GET" and read):
            return args
    raise BriefError("refused gh call outside the read/publication interface")


def run_gh(args, stdin=None):
    try:
        return subprocess.run(["gh", *guarded(args)], input=stdin, capture_output=True,
                              text=True, check=True, timeout=30).stdout
    except subprocess.CalledProcessError as exc:
        raise BriefError(f"gh {' '.join(args[:3])} failed: {((exc.stderr or '').strip().splitlines() or [''])[-1][:300]}") from None
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise BriefError(f"gh unavailable ({type(exc).__name__})") from None


def _json_stream(text):
    decoder, at, items = json.JSONDecoder(), 0, []
    try:
        while at < len(text):
            while at < len(text) and text[at].isspace():
                at += 1
            if at == len(text):
                break
            value, at = decoder.raw_decode(text, at)
            items += value if isinstance(value, list) else [value]
    except (ValueError, TypeError):
        raise BriefError("invalid JSON from gh") from None
    return items


@contextlib.contextmanager
def _publication_lock(repo, number):
    root = Path(os.environ.get("CARR_BRIEF_LOCK_ROOT", os.path.expanduser("~/.cache/carr/brief-check")))
    root.mkdir(parents=True, exist_ok=True)
    key = hashlib.sha256(f"{repo}/{number}".encode()).hexdigest()
    with (root / (key + ".lock")).open("a+") as handle:
        deadline = time.monotonic() + 30
        while True:
            try:
                fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except BlockingIOError:
                if time.monotonic() >= deadline:
                    raise BriefError("publication lock unavailable") from None
                time.sleep(0.05)
        try:
            yield root / (key + ".pending")
        finally:
            fcntl.flock(handle, fcntl.LOCK_UN)


def post_comment(repo, number, report, *, gh=run_gh):
    if not isinstance(report, dict):
        raise BriefError("publication requires a checked report and a cleared source")
    with _publication_lock(repo, number) as journal:
        def verify_head():
            current = json.loads(gh(guarded(["pr", "view", str(number), "-R", repo, "--json", "headRefOid"])))
            if current.get("headRefOid") != report["head"]:
                raise BriefError("PR head changed; obsolete report refused")
        verify_head()
        path = report.get("brief") or ""
        if os.path.isabs(path) or ".." in path.split("/") or not path:
            raise BriefError("local brief is not cleared for publication; use a brief tracked at the inspected head")
        try:
            source = json.loads(gh(guarded(["api", f"repos/{repo}/contents/{quote(path, safe='/')}?ref={report['head']}"])))
            content = base64.b64decode(source["content"])
            if source.get("type") != "file" or hashlib.sha256(content).hexdigest() != report["brief_sha256"]:
                raise BriefError("local brief does not match the source published at the inspected head")
        except (KeyError, ValueError, TypeError):
            raise BriefError("brief source cannot be cleared for publication") from None
        body = render_comment(report)
        if len(body) > 65000:
            raise BriefError("complete checklist exceeds the comment limit; publication refused without truncation")
        login = json.loads(gh(guarded(["api", "user"]))).get("login")
        if not login:
            raise BriefError("authenticated comment author unavailable")
        def mine():
            comments = _json_stream(gh(guarded(["api", "--paginate", f"repos/{repo}/issues/{number}/comments"])))
            return [c for c in comments if (c.get("body") or "").startswith(MARKER)
                    and (c.get("user") or {}).get("login") == login]
        comments = mine()
        verify_head()
        if comments:
            chosen = min(comments, key=lambda c: c["id"])
            # Converge prior duplicates without deleting someone else's record.
            for extra in comments:
                if extra["id"] != chosen["id"]:
                    gh(guarded(["api", "-X", "PATCH", f"repos/{repo}/issues/comments/{extra['id']}", "-F", "body=@-"]),
                       stdin="Superseded brief checklist. See the canonical checklist comment.")
            args = ["api", "-X", "PATCH", f"repos/{repo}/issues/comments/{chosen['id']}", "-F", "body=@-"]
        else:
            if journal.exists() and journal.read_text():
                raise BriefError("earlier POST outcome unresolved; read back the comment before another create")
            journal.write_text(json.dumps({"head": report["head"], "body_sha256": hashlib.sha256(body.encode()).hexdigest()}))
            args = ["api", "-X", "POST", f"repos/{repo}/issues/{number}/comments", "-F", "body=@-"]
        verify_head()
        try:
            result = json.loads(gh(guarded(args), stdin=body))
        except BriefError:
            if "POST" not in args:
                raise
            found = mine()
            matching = [c for c in found if c.get("body") == body]
            if not matching:
                raise BriefError("POST outcome uncertain; journal retained, no automatic retry") from None
            result = matching[0]
        journal.write_text("")
        verify_head()
        return result.get("html_url")


# ── command line ──────────────────────────────────────────────────────────────

def _git(checkout, *args):
    try:
        return subprocess.run(["git", "-C", checkout, *args], capture_output=True, text=True, timeout=30)
    except (OSError, subprocess.TimeoutExpired):
        return None


def _bound_base(checkout, repo, revision):
    origin = _git(checkout, "remote", "get-url", "origin")
    if origin is None or origin.returncode or not re.fullmatch(
            r"(?:https://github.com/|git@github.com:|ssh://git@github.com/)" + re.escape(repo) + r"(?:\.git)?", origin.stdout.strip()):
        raise BriefError("base checkout repository identity does not match the PR")
    if not re.fullmatch(r"[0-9a-f]{40}", revision or ""):
        raise BriefError("exact PR base revision unavailable")
    result = _git(checkout, "cat-file", "-t", revision)
    if result is None or result.returncode:
        fetched = _git(checkout, "fetch", "origin", revision)
        if fetched is None or fetched.returncode:
            raise BriefError("exact PR base revision could not be fetched")
        result = _git(checkout, "cat-file", "-t", revision)
    if result is None or result.returncode or result.stdout.strip() != "commit":
        raise BriefError("exact PR base revision could not be verified")
    return _git_exists(checkout, revision)


def _git_exists(checkout, ref):
    def exists(path):
        revision = _git(checkout, "rev-parse", "--verify", f"{ref}^{{commit}}")
        if revision is None or revision.returncode:
            return None
        result = _git(checkout, "ls-tree", "-z", revision.stdout.strip(), "--", path.rstrip('/'))
        return bool(result.stdout) if result is not None and result.returncode == 0 else None
    return exists


def report_status(report):
    if report["brief_state"] == "changed since the builder ran":
        return 2
    return 1 if any(r["verdict"] == "not met" for r in report["requirements"]) else 0


def _local_reference(checkout):
    def local(path):
        ignored = _git(checkout, "check-ignore", "-q", "--no-index", path)
        return os.path.exists(os.path.join(checkout, path)) or bool(ignored and ignored.returncode == 0)
    return local


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("repo", nargs="?")
    parser.add_argument("pr", nargs="?", type=int)
    parser.add_argument("--stamp", metavar="BRIEF", help="print the brief-record instruction for a builder prompt")
    parser.add_argument("--brief", help="brief file (default: the Brief: line in the PR body)")
    parser.add_argument("--brief-root", help="where a relative Brief: path resolves (default: this repo's main checkout)")
    parser.add_argument("--checkout", help="clone of <repo> for exact PR base revision lookups (default: ~/<repo name>)")
    parser.add_argument("--post", action="store_true", help="create or update the one brief-check comment on the PR")
    parser.add_argument("--jev", action="store_true", help="ask Jev about the needs-judgment residue (paid, advisory)")
    parser.add_argument("--json", action="store_true", help="print the report as JSON instead of the comment")
    args = parser.parse_args(argv)

    if args.stamp:
        sys.stdout.write(stamp_preamble(args.stamp))
        return 0
    if not args.repo or not args.pr:
        parser.error("<owner/repo> <pr> are required unless --stamp is given")

    pr = json.loads(run_gh(["pr", "view", str(args.pr), "-R", args.repo, "--json",
                            "number,url,headRefOid,baseRefOid,body,files,statusCheckRollup"]))
    brief_path = args.brief
    if not brief_path:
        stamp = read_stamp(pr.get("body"))
        if not stamp:
            print(f"brief-check: PR {args.pr} carries no 'Brief: <path> sha256:<hex>' line; pass --brief", file=sys.stderr)
            return 2
        root = args.brief_root or _main_checkout(REPO) or REPO
        brief_path = stamp[0] if os.path.isabs(stamp[0]) else os.path.join(root, stamp[0])
    try:
        with open(brief_path, encoding="utf-8") as handle:
            brief_text = handle.read()
    except (OSError, UnicodeError) as exc:
        raise BriefError(f"brief unavailable ({type(exc).__name__})") from None

    checkout = args.checkout or os.path.expanduser(f"~/{args.repo.split('/')[-1]}")
    has_checkout = os.path.isdir(os.path.join(checkout, ".git")) or os.path.isfile(os.path.join(checkout, ".git"))
    base_lookup = None
    if has_checkout:
        try:
            base_lookup = _bound_base(checkout, args.repo, pr.get("baseRefOid"))
        except BriefError as exc:
            print(f"brief-check: {exc}; base lookup remains unknown", file=sys.stderr)
    report = check(
        brief_text, pr,
        exists_in_base=base_lookup,
        local_reference=_local_reference(checkout) if base_lookup else None,
        brief_path=args.brief or read_stamp(pr.get("body"))[0])
    if args.jev:
        report["body"] = pr.get("body")
        add_jev(report)
        report.pop("body", None)

    comment = render_comment(report)
    print(json.dumps(report, indent=2) if args.json else comment)
    if args.post:
        if report_status(report) == 2:
            raise BriefError("brief hash conflict must be resolved before publication")
        print("posted:", post_comment(args.repo, args.pr, report), file=sys.stderr)
    return report_status(report)


if __name__ == "__main__":
    try:
        sys.exit(main())
    except BriefError as exc:
        print(f"brief-check: {exc}", file=sys.stderr)
        sys.exit(2)
