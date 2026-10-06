#!/usr/bin/env python3
"""ops/timebomb-audit.py -- weekly scripted time-bomb audit (launchd, not a
scheduled model session).

WHAT A TIME BOMB IS, for this file. Code that is correct today and will
break, or silently change behaviour, with NO code change: purely because
time passes, or because the repo, a database, a log, or a counter grows.
Not every hardcoded number or date is one -- most are intentional and safe
(a page size, a display truncation, a schema column type nowhere near its
range) -- which is why this pipeline has three layers rather than one:

  1. CHEAP DETERMINISTIC SIGNATURES (scan_repo below) -- regex regions over
     tracked source, ported from the one-off scan that started this file
     (scratchpad/timebomb/find_candidates.py, 2026-09-24): git history
     windows, hardcoded dates compared against "now", expiry/deadline
     fields, exact counts pinned to growth, unbounded LIMITs, append-only
     logs, fixed-width migration numbers. Deliberately ambiguous -- each
     signature also matches the safe case, which is what leaves anything
     for deterministic headroom or a semantic review suggestion.

  2. DETERMINISTIC HEADROOM (compute_headroom) -- for the two signature
     families where "how close is this to tripping" is computable without
     asking a model: a git history window (git rev-list gives the real
     commit velocity) and a literal date (compared against today). A
     computed headroom under HEADROOM_COMMIT_THRESHOLD commits or
     HEADROOM_DAYS_THRESHOLD days is a finding REGARDLESS of what Jev says
     about it -- Jev only ranks where to look, it never overrides a
     deterministic clock or counter.

  3. JEV JUDGMENT (judge_regions) -- ops/typesafe_client.py's ask(), asking
     two semantic questions per residual region in ONE cached request
     (breaks_without_code_change, fails_silently), the same shape as the one-off scan
     (scratchpad/timebomb/scan.py) and reusing ops/jev_code_review.py's
     answer_value() reader UNCHANGED -- that reader shipped a real bug once
     (read body["probability"], which does not exist, and scored 426
     regions 0.0 -- see answer_value()'s own docstring) and the only reason
     it was caught was running it against a known-bad and a known-good
     snippet first. This file repeats that check -- reader_sanity_check()
     below -- at the START of every real run, and a run where it fails, or
     where Jev is unreachable, or where zero regions get judged despite
     there being some to judge, FAILS LOUDLY (nonzero exit, a line that
     says exactly that) rather than ever reporting "0 new findings" for a
     reason that has nothing to do with there being no findings.

THE TRIAGE LEDGER (ops/config/timebomb-triage.v1.json) is what keeps this
weekly and not a one-off: a region is keyed by its path plus a hash of its
own (whitespace-normalised) code text, so a REVIEWED region stays suppressed
across runs, and a region whose surrounding code CHANGES gets a fresh hash
and is treated as new. The ledger is seeded with the 15 false positives the
2026-09-24 one-off scan found (one-shot applied migrations, intentional
markers, synthetic test NOWs, expiries that are read from config and raise
loudly, and so on) -- see ops/config/timebomb-triage.v1.json's own entries
for the reasons. Already-applied migrations are excluded from the scan
STRUCTURALLY (MIGRATIONS_DIR below) rather than merely ledgered, because a
migrations/*.sql file that has already been applied to production never
re-runs -- there is no "grows and breaks" story to tell about it at all.

RECORD-LAYER FILING follows the SAME call path tools/cutover-watch.py (the
launchd-run cutover watch, invoked hourly-scale through
bin/run-scheduled.sh) already uses to reach a verb from an unattended
script: a subprocess call to `run.sh call <verb> '<json>'`, never the
generic MCP call-verb passthrough tool (see CLAUDE.md's fallback-door
doctrine). A clean run (nothing new since the ledger) stays silent and
writes nothing to the record; a run with new deterministic findings files exactly ONE
record-defect summarising them (never one per region -- the defect log
wants classes and counts, not a row flood) and, in the same run, posts ONE
`@queue enqueue` turn to the partner room's Hermes queue asking a session
with repo-write to work the findings (see queue_enqueue_turn() below for
why `./run.sh call add-room-turn` is the one scripted path that can produce
an accepted origin for that queue command).

USAGE:
  ops/timebomb-audit.py                  # real run: scan, judge, file, report
  ops/timebomb-audit.py --dry-run        # scan and judge, write the local
                                          # report, but file nothing to the
                                          # record layer or the room queue
  ops/timebomb-audit.py --no-jev         # deterministic-only pass (selftest
                                          # / offline use); still requires
                                          # the reader check to be skipped
                                          # explicitly, since there is no
                                          # reader to sanity-check

If the LaunchAgent is missing on a machine (a fresh clone, or after the job
has been unloaded), install it with the exact sed / plutil / launchctl
sequence in ops/launchd/com.carr.timebomb-audit.plist's own header comment.
There is deliberately no bin/install-timebomb-audit.sh: a bin/install-*.sh is
an external-admin ingress that would need its own SCAC registry successor
(see com.carr.release-pipeline.plist's header for the same reasoning), and
installing a weekly audit job is a small enough act that the three commands
belong inline rather than behind a script. This is deliberately not
`ops/config-as-code.py install --apply` either (the broad reconciler, which
has no per-job filter and would also rewrite every other hook and
LaunchAgent).

Env overrides (test isolation, same convention as CARR_REPLAY_REPO):
  CARR_TIMEBOMB_REPO           repo root to scan (default: this file's
                                grandparent directory)
  CARR_TIMEBOMB_OUT_DIR        where the local JSON report is written
                                (default: <repo>/out/timebomb-audit)
  CARR_TIMEBOMB_TRIAGE_PATH    the triage ledger path (default:
                                <repo>/ops/config/timebomb-triage.v1.json)

Fixtures: ops/timebomb-audit-selftest.py
"""
from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import os
import re
import subprocess
import sys
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

REPO = Path(os.environ.get("CARR_TIMEBOMB_REPO") or Path(__file__).resolve().parent.parent)
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from lib import record_call  # noqa: E402
OUT_DIR = Path(os.environ.get("CARR_TIMEBOMB_OUT_DIR") or (REPO / "out" / "timebomb-audit"))
TRIAGE_PATH = Path(os.environ.get("CARR_TIMEBOMB_TRIAGE_PATH")
                    or (REPO / "ops" / "config" / "timebomb-triage.v1.json"))

# ── SCAN ─────────────────────────────────────────────────────────────────
# Ported from scratchpad/timebomb/find_candidates.py's tightened pass
# (8381 -> 568 regions on 2026-09-24: dropped comment-only date lines,
# required a comparison/clock call or a growth-suggesting word nearby).

CONTEXT_BEFORE = 14
CONTEXT_AFTER = 10
MAX_REGION_CHARS = 2600

SUFFIXES = (".py", ".mjs", ".js", ".sh", ".sql", ".yml", ".yaml")
EXCLUDE_DIRS = ("node_modules/", "vendor/", ".claude/worktrees/")

# Already-applied migrations never re-run, so there is no growth story to
# tell about a bound inside one: excluded STRUCTURALLY, not merely
# ledgered (Joe's instruction, 2026-09-24 scan follow-up). A migration
# still pending review lives on a branch, not main, so this audit -- which
# only ever scans the checked-out tree it runs from -- never needed to see
# it here anyway.
MIGRATIONS_DIR = "migrations/"

_NEARBY_TIME_CMP = re.compile(
    r"(now\(\)|today\(\)|datetime\.now|Date\.now|utcnow|time\.time\(\)|"
    r"<=|>=|(?<![=!<>])[<>](?!=)|\bcompare\(|\belapsed\b)", re.I)
_NEARBY_GROWTH = re.compile(r"\b(git|log|commit|history)\b", re.I)
_ORDER_BY = re.compile(r"order\s+by", re.I)
_ROTATION_WORD = re.compile(r"\b(rotate|rotation|truncate|gzip|logrotate|"
                             r"maxBytes|max_bytes|RotatingFileHandler)\b", re.I)
_LOG_LIKE_PATH = re.compile(r"[\"'][^\"'\n]*(?:out/|\.log|\.jsonl)[^\"'\n]*[\"']")
_COMMENT_LINE = re.compile(r"^\s*(#|//|\*|<!--)")


def _window_text(text: str, start: int, span: int = 200) -> str:
    lo = max(0, start - span)
    hi = min(len(text), start + span)
    return text[lo:hi]


def _match_line(text: str, start: int) -> str:
    lo = text.rfind("\n", 0, start) + 1
    hi = text.find("\n", start)
    if hi == -1:
        hi = len(text)
    return text[lo:hi]


SIGNATURES = (
    ("git_log_window", re.compile(r"\bgit\s+log\b[^\n]*(?:-\d+|-n\s*\d+)")),
    ("git_head_tilde", re.compile(r"HEAD~\d+")),
    ("git_rev_list_count", re.compile(r"rev-list\b[^\n]*--count")),
    ("git_max_count", re.compile(r"--max-count(?:=|\s+)\d+")),
    ("git_depth", re.compile(r"(?:--depth(?:=|\s+)\d+|fetch-depth:\s*\d+)")),
    # the wrapper-call / arg-list spelling of the same window (the seed bug:
    # git("log", "-160", ...) / ["log", "-160"]), which literal "git log -N"
    # text does not match.
    ("git_log_call_arg", re.compile(r"[\"']log[\"']\s*,\s*[\"']-\d+[\"']")),

    ("hardcoded_date_literal", re.compile(r"\b20[0-9]{2}-[01][0-9]-[0-3][0-9]\b")),
    ("datetime_year_literal", re.compile(r"(?:datetime|date)\(\s*20\d{2}\b")),

    ("expiry_field", re.compile(r"\b(expires?|expiry|not_after|valid_until|deadline|sunset)\b", re.I)),
    ("after_date_remove", re.compile(r"after\s+20[0-9]{2}-[01][0-9]-[0-3][0-9][^\n]*remove", re.I)),

    ("magic_count_eq", re.compile(
        r"\b\w*(?:count|total|len|length|rows|records|expected|verb)\w*\s*==\s*\d{3,}\b", re.I)),
    ("expected_total_const", re.compile(r"\b(EXPECTED_\w+|TOTAL_\w+|VERB_COUNT|verb_count)\s*=\s*\d+")),
    ("n_of_n_total", re.compile(r"\d+\s*/\s*\d+\s+passed", re.I)),

    ("sql_limit_no_order", re.compile(r"LIMIT\s+\d+", re.I)),
    ("python_slice_bound", re.compile(r"\[:\s*\d{2,}\s*\]")),
    ("shell_head_tail", re.compile(
        r"(?:git\s+log|--oneline|commit|history)[^\n|]{0,60}\|\s*(?:head|tail)\s+-n?\s*\d+", re.I)),

    ("append_open_out", re.compile(r"open\([^)\n]*[\"']a[\"'][^)\n]*\)")),
    ("append_redirect_out", re.compile(r">>\s*\S*(?:out/|\.log|\.jsonl)")),

    ("zero_padded_migration", re.compile(r"(?:^|/)0\d{3}[_-]\w", re.M)),
)

CONTEXT_FILTERS = {
    "expiry_field": lambda w, l: bool(_NEARBY_TIME_CMP.search(w)) and not _COMMENT_LINE.match(l),
    "sql_limit_no_order": lambda w, l: not _ORDER_BY.search(w),
    "python_slice_bound": lambda w, l: bool(_NEARBY_GROWTH.search(w)) and not _COMMENT_LINE.match(l),
    "zero_padded_migration": lambda w, l: True,
    "hardcoded_date_literal": lambda w, l: bool(_NEARBY_TIME_CMP.search(w)) and not _COMMENT_LINE.match(l),
    "datetime_year_literal": lambda w, l: not _COMMENT_LINE.match(l),
    "append_open_out": lambda w, l: bool(_LOG_LIKE_PATH.search(w)) and not _ROTATION_WORD.search(w),
    "append_redirect_out": lambda w, l: not _ROTATION_WORD.search(w),
}

GIT_WINDOW_KINDS = frozenset({
    "git_log_window", "git_head_tilde", "git_rev_list_count", "git_max_count",
    "git_depth", "git_log_call_arg", "shell_head_tail",
})
# The subset of GIT_WINDOW_KINDS whose own matched text carries a literal
# window size worth turning into deterministic headroom. git_depth
# (checkout fetch-depth) and git_rev_list_count (rev-list --count with no
# literal argument) are excluded: a CI checkout's --depth/fetch-depth is an
# ordinary shallow-clone convention, not a sliding search window, and
# treating it as one was the loudest false signal in an early pass of this
# script (18 "findings" that were really fetch-depth: 0/1/2 on unrelated
# GitHub Actions steps).
GIT_WINDOW_HEADROOM_KINDS = frozenset({
    "git_log_window", "git_head_tilde", "git_max_count", "git_log_call_arg", "shell_head_tail",
})
DATE_KINDS = frozenset({"hardcoded_date_literal", "datetime_year_literal"})


def tracked_sources(repo: Path, suffixes=SUFFIXES) -> list[str]:
    out = subprocess.run(["git", "ls-files"], capture_output=True, text=True,
                          cwd=str(repo), timeout=120).stdout
    result = []
    for f in out.splitlines():
        if f.startswith(MIGRATIONS_DIR):
            continue
        if not f.endswith(suffixes):
            if not (f.startswith(".github/workflows/") and f.endswith((".yml", ".yaml"))):
                continue
        if any(f.startswith(d) or f"/{d}" in f for d in EXCLUDE_DIRS):
            continue
        result.append(f)
    return result


def regions_for_repo(repo: Path) -> list[dict]:
    found = []
    for rel in tracked_sources(repo):
        full = repo / rel
        try:
            text = full.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        lines = text.splitlines()
        for kind, pattern in SIGNATURES:
            if kind == "zero_padded_migration":
                continue  # migrations/ is excluded structurally above
            filt = CONTEXT_FILTERS.get(kind)
            for match in pattern.finditer(text):
                if filt and not filt(_window_text(text, match.start()), _match_line(text, match.start())):
                    continue
                line_no = text[:match.start()].count("\n") + 1
                lo = max(0, line_no - 1 - CONTEXT_BEFORE)
                hi = min(len(lines), line_no + CONTEXT_AFTER)
                snippet = "\n".join(lines[lo:hi])[:MAX_REGION_CHARS]
                # match_text is the EXACT matched substring (not the wider
                # context snippet) -- deterministic headroom extraction reads
                # only this, precisely, rather than scanning the whole region
                # for "the nearest number", which grabs unrelated literals
                # (a port, an exit code, a CI fetch-depth) sitting nearby.
                found.append({"path": rel, "line": line_no, "kind": kind, "code": snippet,
                              "match_text": match.group(0)})
    return found


def collapse(found: list[dict], window: int = CONTEXT_BEFORE + CONTEXT_AFTER) -> list[dict]:
    by_file: dict[str, list[dict]] = {}
    for item in found:
        by_file.setdefault(item["path"], []).append(item)
    out = []
    for items in by_file.values():
        items.sort(key=lambda i: i["line"])
        current = None
        for item in items:
            if current and item["line"] - current["line"] <= window:
                if item["kind"] not in current["kind"].split("+"):
                    current["kind"] += "+" + item["kind"]
                    current["match_texts"][item["kind"]] = item["match_text"]
                continue
            current = dict(item)
            current["match_texts"] = {item["kind"]: item["match_text"]}
            out.append(current)
    for item in out:
        item.pop("match_text", None)
    return out


def scan_repo(repo: Path) -> list[dict]:
    return collapse(regions_for_repo(repo))


# ── TRIAGE LEDGER ───────────────────────────────────────────────────────

def normalize_code(code: str) -> str:
    lines = [ln.rstrip() for ln in code.splitlines()]
    while lines and not lines[0].strip():
        lines.pop(0)
    while lines and not lines[-1].strip():
        lines.pop()
    return "\n".join(lines)


def region_hash(code: str) -> str:
    return hashlib.sha256(normalize_code(code).encode("utf-8")).hexdigest()[:16]


def ledger_key(path: str, code: str) -> str:
    return f"{path}#{region_hash(code)}"


def load_ledger(path: Path) -> dict:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {"schema": "timebomb-triage/v1", "entries": {}}
    if not isinstance(data, dict) or not isinstance(data.get("entries"), dict):
        return {"schema": "timebomb-triage/v1", "entries": {}}
    return data


def suppressed_by_ledger(region: dict, ledger: dict) -> bool:
    entry = ledger.get("entries", {}).get(region["_ledger_key"])
    return bool(entry) and entry.get("verdict") in ("false_positive", "fixed")


# ── DETERMINISTIC HEADROOM ───────────────────────────────────────────────
# "How much runway is left" computed from the repo's own clock/history,
# never guessed by a model. A finding here stands on its own regardless of
# what Jev scores the same region -- Jev ranks WHERE to look; it does not
# get a veto over a computed number.

HEADROOM_COMMIT_THRESHOLD = 20
HEADROOM_DAYS_THRESHOLD = 30

_WINDOW_N_RE = re.compile(
    r"(?:-n\s*(\d+)|--max-count(?:=|\s+)(\d+)|--depth(?:=|\s+)(\d+)|"
    r"fetch-depth:\s*(\d+)|HEAD~(\d+)|-(\d+)\b|[\"']log[\"']\s*,\s*[\"']-(\d+)[\"'])")
_DATE_RE = re.compile(r"\b(20[0-9]{2})-([01][0-9])-([0-3][0-9])\b")
_MAX_AGE_RE = re.compile(r"max_age(?:_days)?\s*[:=]\s*(\d+)", re.I)


def _commit_velocity(repo: Path) -> tuple[int, float] | None:
    """(total_commits, commits_per_day over the trailing 90 days), or None
    if git is unavailable/unreadable -- callers must treat that as
    "cannot compute", never as zero velocity (zero velocity would make
    every window look infinitely safe)."""
    try:
        total = subprocess.run(["git", "rev-list", "--count", "HEAD"],
                                cwd=str(repo), capture_output=True, text=True,
                                timeout=30, check=True).stdout.strip()
        recent = subprocess.run(
            ["git", "rev-list", "--count", "--since=90 days ago", "HEAD"],
            cwd=str(repo), capture_output=True, text=True, timeout=30, check=True).stdout.strip()
    except (OSError, subprocess.CalledProcessError, subprocess.TimeoutExpired):
        return None
    try:
        total_i = int(total)
        recent_i = int(recent)
    except ValueError:
        return None
    return total_i, recent_i / 90.0


def _extract_window_n(match_text: str) -> int | None:
    """Read the window size out of the SIGNATURE'S OWN matched text (e.g.
    "git log -160", "HEAD~40", "--max-count=25", '"log", "-160"',
    "| head -n 20") -- never out of the wider region, which routinely
    contains unrelated small integers (a port, an exit code, an array
    index) that are not the window at all."""
    numbers = [int(g) for g in re.findall(r"\d+", match_text)]
    return min(numbers) if numbers else None


def _extract_date(match_text: str) -> date | None:
    match = _DATE_RE.search(match_text)
    if not match:
        return None
    try:
        return date(int(match.group(1)), int(match.group(2)), int(match.group(3)))
    except ValueError:
        return None


def compute_headroom(region: dict, commit_velocity: tuple[int, float] | None,
                      today: date) -> dict | None:
    """Returns a headroom finding dict, or None when nothing computable was
    present (e.g. the signature fired on a bare year with no usable window
    or date -- Jev alone judges those)."""
    kinds = set(region["kind"].split("+"))
    match_texts = region.get("match_texts") or {}

    headroom_kinds = kinds & GIT_WINDOW_HEADROOM_KINDS
    if headroom_kinds:
        # Smallest window across every headroom-eligible signature that fired
        # in this (possibly merged) region -- the tightest bound is the one
        # that trips first.
        n = None
        for k in headroom_kinds:
            candidate = _extract_window_n(match_texts.get(k, ""))
            if candidate is not None and (n is None or candidate < n):
                n = candidate
        # A window of "-1"/"-2"/"-3" is almost always "get the N most recent
        # commits" (git log -1 --format=%an is "who authored HEAD"), an
        # idiom that stays correct forever regardless of repo growth -- not
        # a sliding search window that can be outgrown. MIN_PLAUSIBLE_WINDOW
        # excludes exactly that idiom from deterministic headroom; Jev still
        # judges the region on its actual semantics either way.
        MIN_PLAUSIBLE_WINDOW = 5
        if n is not None and n < MIN_PLAUSIBLE_WINDOW:
            n = None
        if n is not None and commit_velocity is not None:
            _total, per_day = commit_velocity
            headroom_days = (n / per_day) if per_day > 0 else None
            finding = (n < HEADROOM_COMMIT_THRESHOLD or
                       (headroom_days is not None and headroom_days < HEADROOM_DAYS_THRESHOLD))
            return {
                "kind": "git_window", "window_commits": n,
                "commits_per_day_90d": round(per_day, 3),
                "headroom_days_est": round(headroom_days, 1) if headroom_days is not None else None,
                "deterministic_finding": finding,
            }

    date_kinds = kinds & DATE_KINDS
    if date_kinds:
        literal = None
        for k in date_kinds:
            literal = _extract_date(match_texts.get(k, ""))
            if literal is not None:
                break
        if literal is not None:
            delta_days = (literal - today).days
            max_age_match = _MAX_AGE_RE.search(region["code"])
            max_age = int(max_age_match.group(1)) if max_age_match else None
            # A date already in the past is overwhelmingly a NARRATIVE citation
            # in this codebase (a decision date, a docstring's "as of") rather
            # than a live bound -- the one-off scan's false-positive list is
            # almost entirely exactly this shape. Only count "already broken"
            # deterministically when an explicit max_age sits beside it (that
            # is the signal this is a real, computed bound rather than prose);
            # an UPCOMING date within the threshold is a finding either way --
            # that is a live deadline approaching regardless of any max_age.
            if 0 <= delta_days <= HEADROOM_DAYS_THRESHOLD:
                finding = True
            elif delta_days < 0 and max_age is not None:
                finding = True
            else:
                finding = False
            result = {"kind": "date", "literal_date": literal.isoformat(),
                      "headroom_days": delta_days, "deterministic_finding": finding}
            if max_age is not None:
                result["max_age_days"] = max_age
            return result

    return None


# ── JEV JUDGMENT ─────────────────────────────────────────────────────────
# Same shape as scratchpad/timebomb/scan.py: import ops/jev_code_review.py
# and ops/typesafe_client.py by path (they are libraries and stay that
# way -- see either file's own docstring on why they carry no shebang or
# main guard), ask three questions per region in ONE request, read the
# answer with answer_value() UNCHANGED.

REPORT_AT = 0.55
JUDGE_WORKERS = 8
JUDGE_TIMEOUT_SECONDS = 90.0


def _load_module(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise ImportError(f"cannot load {name} from {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def load_jev_modules(repo: Path):
    jcr = _load_module("jev_code_review_for_timebomb", repo / "ops" / "jev_code_review.py")
    tsc = _load_module("typesafe_client_for_timebomb", repo / "ops" / "typesafe_client.py")
    return jcr, tsc


def build_questions(tsc):
    return {
        "breaks_without_code_change": tsc.noul(
            "The code in `region.code` was flagged as `region.why_it_was_flagged` by a "
            "cheap pattern pass looking for time bombs -- code that is correct today but "
            "will break, or silently change behaviour, with NO code change: purely "
            "because time passes, or because the repo, a database, a log, or a counter "
            "grows. Will this code fail, or produce a wrong result, for that reason -- "
            "unedited? Answer no for bounds that are intentional and safe, such as a "
            "page size, a display truncation, or a schema column type that is nowhere "
            "near its range.",
            true="The code will fail, or silently produce a wrong result, purely "
                 "because time passed or something (repo history, a table, a log, a "
                 "counter) grew, with nobody having edited it.",
            false="Any bound or literal here is intentional and safe for the data it "
                  "handles, or nothing here can actually break this way."),
        "fails_silently": tsc.noul(
            "Assume the code in `region.code` DOES eventually trip for the reason above "
            "(time passing, or history/data/counters growing). When it trips, would it "
            "trip SILENTLY -- producing a wrong result that looks like success, with no "
            "error, no loud failure, no obviously wrong output a person would "
            "immediately notice -- rather than failing loudly?",
            true="It would misbehave quietly: wrong output, a partial result, or a "
                 "stale value that looks like a normal successful run.",
            false="It would fail loudly and visibly (an exception, a clear error "
                  "message, a CI failure) or it does not trip at all."),

    }


class JevUnreachable(RuntimeError):
    """Jev could not be reached at all -- the run must fail loudly, never
    silently report zero findings."""


def reader_sanity_check(jcr, tsc, ask=None) -> tuple[bool, str]:
    """Sanity-check answer_value() against a known-bad and a known-good
    snippet BEFORE trusting any real judgment this run makes -- the same
    check scratchpad/timebomb/verify_reader.py ran before the one-off scan,
    now run at the start of EVERY weekly run. A reader that returns 0.0 for
    everything looks exactly like a clean codebase, and that bug shipped
    once already (see jev_code_review.answer_value's own docstring).

    `ask` is an injection seam for the selftest (a fake client's .ask, or a
    bound tsc.ask); production callers leave it default.
    """
    # Offline typed fixtures check the reader, not the provider's semantics.
    bad_answer = {"answers": {"breaks_without_code_change": {"type":"noul", "noul":0.9}}}
    good_answer = {"answers": {"breaks_without_code_change": {"type":"noul", "noul":0.1}}}

    bad = {qid: jcr.answer_value(body) for qid, body in (bad_answer.get("answers") or {}).items()}
    good = {qid: jcr.answer_value(body) for qid, body in (good_answer.get("answers") or {}).items()}
    bad_score = bad.get("breaks_without_code_change")
    good_score = good.get("breaks_without_code_change")
    if not isinstance(bad_score, (int, float)):
        return False, f"known-bad breaks_without_code_change is not numeric: {bad}"
    if not isinstance(good_score, (int, float)):
        return False, f"known-good breaks_without_code_change is not numeric: {good}"
    # Checked BEFORE either threshold: a reader stuck returning the same
    # value for everything (the 0.0-for-everything bug answer_value's own
    # docstring describes) would otherwise always trip the LOW check first
    # and never get named for what it actually is.
    if good_score == bad_score:
        return False, ("known-bad and known-good scored IDENTICALLY -- the reader is "
                        "probably broken (the 0.0-for-everything bug)")
    if bad_score < 0.55:
        return False, f"known-bad scored LOW on breaks_without_code_change: {bad}"
    if good_score >= 0.55:
        return False, f"known-good scored HIGH on breaks_without_code_change: {good}"
    return True, f"reader check PASS (bad={bad_score}, good={good_score})"


def judge_regions(regions: list[dict], jcr, tsc, ask=None) -> tuple[list[dict], int]:
    """Returns (regions with a "scores" key added, error_count)."""
    ask = ask or tsc.ask
    questions = build_questions(tsc)
    semantic = _load_module("jev_semantic", REPO / "ops" / "jev_semantic.py")

    def one(region):
        state = {"region": {"path": region["path"], "line": region["line"],
                             "why_it_was_flagged": region["kind"], "code": region["code"][:6000]}}
        try:
            answer = semantic.ask(state, questions, transport=ask, caller="timebomb-audit",
                                  version="region-v2", timeout=JUDGE_TIMEOUT_SECONDS)
        except Exception as exc:  # noqa: BLE001 -- a per-region failure, not a crash
            return region, None, f"{type(exc).__name__}: {exc}"[:200]
        scores = {qid: jcr.answer_value(body) for qid, body in (answer.get("answers") or {}).items()}
        model = answer.get("model")
        if isinstance(model, str) and model.strip():
            scores["_model"] = model
        return region, scores, None

    results = []
    errors = 0
    with ThreadPoolExecutor(max_workers=JUDGE_WORKERS) as pool:
        for region, scores, error in pool.map(one, regions):
            row = dict(region)
            if error:
                row["scores"] = {"_error": error}
                errors += 1
            else:
                row["scores"] = scores
            results.append(row)
    return results, errors


# ── RECORD-LAYER FILING ──────────────────────────────────────────────────
# lib/record_call: `run.sh call <verb> '<json>'`, never the generic MCP
# call-verb passthrough.

def call_verb(verb: str, args: dict) -> tuple[bool, object]:
    """Never raises -- a verb call that fails is a finding, not a crash. Only
    an ok outcome is success: a refused write is reported, never accepted."""
    child_env = {"HOME": os.environ.get("HOME", ""), "PATH": os.environ.get("PATH", ""),
                 "LANG": os.environ.get("LANG", "C")}
    result = record_call.call_verb(verb, args, env=child_env, timeout=120)
    return (True, result.reply) if result.ok else (False, result.describe())


def summarize_findings(findings: list[dict]) -> str:
    lines = []
    for f in findings[:25]:
        score = (f.get("scores") or {}).get("breaks_without_code_change")
        headroom = f.get("headroom")
        bits = [f["path"] + ":" + str(f["line"]), f["kind"]]
        if isinstance(score, (int, float)):
            bits.append(f"jev={score:.2f}")
        if headroom and headroom.get("deterministic_finding"):
            bits.append(f"headroom={headroom.get('headroom_days_est', headroom.get('headroom_days'))}d")
        lines.append(" ".join(bits))
    more = len(findings) - len(lines)
    if more > 0:
        lines.append(f"... and {more} more")
    return "\n".join(lines)


def file_defect(findings: list[dict], run_date: str) -> tuple[bool, object]:
    """ONE record-defect per run, summarising every new finding -- never one
    row per region (record-defect's own doctrine wants classes and counts
    to accumulate, not a flood)."""
    claimed = ("the tracked codebase contains no new time-bomb regions -- code that "
               "breaks, or silently changes behaviour, purely because time passes or "
               "something (history, a table, a log, a counter) grows -- beyond what "
               f"ops/config/timebomb-triage.v1.json already reviewed as of {run_date}")
    actual = (f"the weekly timebomb-audit run on {run_date} found {len(findings)} new/changed "
              f"region(s) not in the triage ledger:\n{summarize_findings(findings)}")
    args = {
        "idempotency_key": str(uuid.uuid4()),
        "defect_class": "timebomb-region-unreviewed",
        "claimed": claimed,
        "actual": actual,
        "source_unread": "ops/config/timebomb-triage.v1.json",
        "detected_by": "check",
        "cost_note": f"{len(findings)} region(s) awaiting triage or a fix",
    }
    return call_verb("record-defect", args)


# ── ROOM QUEUE: WORK THE FINDINGS ────────────────────────────────────────
# Joe's 2026-09-24 scope addition: a clean run stays silent; a run with new
# findings also enqueues a repo-write Claude Desktop session through the
# partner room's Hermes queue, so the findings get worked without anyone
# having to notice the defect row and start a session by hand.
#
# `add-room-turn`'s own handler (mcp-server/src/partner-room.js) ALWAYS
# stamps `origin_channel: "mcp", origin_actor: actor.slug` server-side for
# every caller whose credential resolves to a sponsored partner (any
# credential add-room-turn already accepts at all) -- it is not something a
# caller can spoof or omit, and it is not conditional on the call coming
# from an interactive session. `run.sh call add-room-turn` (the exact door
# tools/cutover-watch.py already uses from an unattended launchd script for
# update-loop) authenticates as this Mac's local machine token, which
# resolves to a sponsored partner the same way that existing call does --
# so it produces a turn with origin_channel="mcp" and a non-empty
# origin_actor, which is exactly what tools/room-bridge/queue_grammar.py's
# _origin() requires to accept an `@queue enqueue` command. No forged
# origin is needed or attempted here: the server derives it, this script
# only supplies the room turn's body text.
ROOM_UUID_NAMESPACE = uuid.UUID("6f2b6b0a-0a1e-4f0e-9c1d-6a0c7c9c7a10")


def queue_enqueue_turn(run_date: str, report_path: str, defect_ref: str) -> dict:
    key = f"timebomb-{run_date}"
    body = (
        f"@queue enqueue target=claude-desktop cap=repo-write priority=P1 runtime=3h "
        f"key={key} :: Work the time-bomb audit findings for {run_date}\n"
        f"Report: {report_path}\n"
        f"Defect/loop ref: {defect_ref}\n"
        "Verify each finding's headroom deterministically (do not take the report's "
        "numbers on faith); fix the real ones through an ordinary PR each, and do not "
        "merge them; add any false positives to ops/config/timebomb-triage.v1.json "
        "with reasons, in a PR; reply in the room with the PR URLs.")
    msg_id = str(uuid.uuid5(ROOM_UUID_NAMESPACE, key))
    return {
        "idempotency_key": str(uuid.uuid4()),
        "room": "model-room",
        "seat": "claude",
        "kind": "turn",
        "body": body,
        "msg_id": msg_id,
    }


def file_room_queue_turn(run_date: str, report_path: str, defect_ref: str) -> tuple[bool, object]:
    return call_verb("add-room-turn", queue_enqueue_turn(run_date, report_path, defect_ref))


# ── MAIN ──────────────────────────────────────────────────────────────────

def build_report(run_date: str, new_regions: list[dict], errors: int,
                  deterministic_only: list[dict]) -> dict:
    return {
        "schema": "timebomb-audit/v1",
        "run_date": run_date,
        "new_region_count": len(new_regions),
        "judge_errors": errors,
        "deterministic_findings": len(deterministic_only),
        "regions": new_regions,
    }


def write_report(report: dict, out_dir: Path, run_date: str) -> Path:
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / f"{run_date}.json"
    path.write_text(json.dumps(report, indent=1, sort_keys=True) + "\n", encoding="utf-8")
    return path


def run(*, dry_run: bool = False, use_jev: bool = True, now: datetime | None = None) -> int:
    now = now or datetime.now(timezone.utc)
    run_date = now.strftime("%Y-%m-%d")
    today = now.date()

    ledger = load_ledger(TRIAGE_PATH)
    all_regions = scan_repo(REPO)
    for r in all_regions:
        r["_ledger_key"] = ledger_key(r["path"], r["code"])
    new_regions = [r for r in all_regions if not suppressed_by_ledger(r, ledger)]

    commit_velocity = _commit_velocity(REPO)
    for r in new_regions:
        r["headroom"] = compute_headroom(r, commit_velocity, today)

    jcr = tsc = None
    errors = 0
    semantic_regions = [r for r in new_regions if r["headroom"] is None]
    if use_jev and semantic_regions:
        try:
            jcr, tsc = load_jev_modules(REPO)
        except Exception as exc:  # noqa: BLE001
            print(f"timebomb-audit: FAIL -- cannot load the Jev client modules: {exc}",
                  file=sys.stderr)
            return 1

        ok, detail = reader_sanity_check(jcr, tsc)
        print(f"timebomb-audit: reader check -- {detail}")
        if not ok:
            print("timebomb-audit: FAIL -- reader sanity check failed; refusing to "
                  "report a result that could be a silently-broken reader wearing a "
                  "clean run's clothes", file=sys.stderr)
            return 1

        if semantic_regions:
            judged, errors = judge_regions(semantic_regions, jcr, tsc)
            by_key = {r["_ledger_key"]: r for r in judged}
            new_regions = [by_key.get(r["_ledger_key"], r) for r in new_regions]
            judged_ok = len(semantic_regions) - errors
            if judged_ok == 0:
                print(f"timebomb-audit: FAIL -- {len(semantic_regions)} region(s) needed "
                      f"judgment and ZERO were judged successfully; this must never "
                      f"look like a clean run", file=sys.stderr)
                return 1

    # Numeric/date headroom is the only automatic filing authority. Semantic
    # suggestions remain in the report for review until a labeled threshold exists.
    findings = [r for r in new_regions
                if (r.get("headroom") or {}).get("deterministic_finding")]
    semantic_advice = [dict(r, review_required=True) for r in new_regions
                       if r.get("scores") and not r["scores"].get("_error")]
    report = build_report(run_date, findings, errors, findings)
    report["semantic_advice"] = semantic_advice
    report["review_required"] = bool(semantic_advice)
    report_path = write_report(report, OUT_DIR, run_date)
    print(f"timebomb-audit: {len(all_regions)} region(s) scanned, "
          f"{len(new_regions)} new/changed vs. the ledger, {len(findings)} finding(s). "
          f"Report: {report_path}")

    if not findings:
        print("timebomb-audit: clean run -- nothing new since triage. Staying silent "
              "on the record layer, per the scheduled-routine rule.")
        return 0

    if dry_run:
        print("timebomb-audit: --dry-run -- not filing to the record layer or the room queue.")
        return 0

    ok, res = file_defect(findings, run_date)
    if not ok:
        print(f"timebomb-audit: FAIL -- could not file record-defect: {res}", file=sys.stderr)
        return 1
    defect_id = res.get("defect_id") if isinstance(res, dict) else None
    defect_ref = f"defect #{defect_id}" if defect_id is not None else "record-defect (see run output)"
    print(f"timebomb-audit: filed {defect_ref}")

    ok, res = file_room_queue_turn(run_date, str(report_path), defect_ref)
    if not ok:
        print(f"timebomb-audit: FAIL -- findings were filed as {defect_ref} but the room "
              f"queue turn could not be posted: {res}", file=sys.stderr)
        return 1
    print("timebomb-audit: posted @queue enqueue turn to model-room")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--dry-run", action="store_true",
                         help="scan, judge, and write the local report, but file nothing")
    parser.add_argument("--no-jev", action="store_true",
                         help="deterministic-only pass; skips the reader check and Jev "
                              "judgment entirely (selftest / offline use)")
    args = parser.parse_args(argv)
    return run(dry_run=args.dry_run, use_jev=not args.no_jev)


if __name__ == "__main__":
    sys.exit(main())
