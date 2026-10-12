"""health_breach — the consumer of every health row's bound action.

Rule 590b11e1 says no metric ships without a bound action, and every red row
in `./run.sh health` prints one: "on breach: open/update one deduplicated loop
...; owner X; remediation ...; verify ...; auto-clear ...". Until this module
that action was prose nobody executed (2026-10-12: eleven rows red for days).

This module turns a health finding (tools/health-check.py's `_FINDINGS` rows,
the same objects `--findings-json` writes) into a Breach, then does one of two
things with the list:

  audit(...)  read-only: how many red rows have NO open loop, and how many have
              more than one. Printed by every `./run.sh health` run.
  act(...)    `./run.sh health --act`: for every red row that carries a bound
              action, file or update ONE deduplicated loop through the record
              layer; for every loop this consumer filed whose row is no longer
              red, apply the auto-clear (close it when the system owns it,
              otherwise note the clear and leave the close to the owner).

DEDUPLICATION LIVES IN THE RECORD LAYER, NOT IN A LOCAL STATE FILE. The readers
that already file their own loops keep a per-machine state file, and on
2026-10-12 the board held two open loops each for four seats and two for the
uptime monitor because those files were lost or not shared. Here the identity
is text the loop body carries, found with `loop-board` search:

  * the key the row's own action names ("loop seat-health:grok",
    "loop scheduled_jobs:<label>:<code>", "loop 'Tailscale down on this Mac'"),
    or an existing loop number it names ("update loop #665");
  * READER_LOOP_PHRASES, for readers whose loop body names no key;
  * this consumer's own tag, "[health-breach <row_id> <digest>]", which starts
    the first line of every loop it files.

A loop found under any of those is the row's one loop. If someone else filed it
the consumer leaves it alone ("covered"); if the consumer filed it, it updates
it only when the bound action changed (the digest), so a nightly run does not
churn versions.

A row without a bound action is counted and reported, never filed: the remedy
for it is an on-breach clause in tools/health-check.py, not a loop.

No shebang on purpose: this is a library, not a new entry point.
"""
from __future__ import annotations

import hashlib
import json
import re
import uuid
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

TAG = "[health-breach "
CLEARED = "cleared"
SEARCH_LIMIT = 20
OWN_LOOPS_LIMIT = 300
NAMESPACE = uuid.UUID("5c0f3a4e-8a51-4b8e-9d0e-6f1d2b7c4a90")

# Readers that open their own loop with a body that names no dedup key. Each
# phrase is copied from the reader's add-loop body; ops/health-act-selftest.py
# fails if a phrase stops appearing in that reader's source.
READER_LOOP_PHRASES = {
    "production_uptime": ("DoctorCRE uptime monitoring unavailable",),   # tools/uptime_health.py
    "jev_live_outage": ("Jev outage remains open", "Jev is offline"),      # tools/jev_outage_health.py
    "grok_session": ("Grok needs sign-in",),                               # ops/grok_session.py
}

_LEVELS = ("FAIL", "WARN", "UNKNOWN", "UNAVAILABLE", "STALE", "MISSING")
_BREACH = re.compile(r"\bon breach\b(?:\s*\([^)]*\))?\s*:?\s*", re.I)
_OWNER = re.compile(r"\bowner:?\s+([A-Za-z][\w-]*(?: (?!remediation\b|fix\b)[A-Za-z][\w-]*)?)")
_KEYED_LOOP = re.compile(r"\bloop\s+([a-z][\w.-]*(?::[\w.-]+)+)")
_NUMBERED_LOOP = re.compile(r"\bloop\s+#(\d+)")
_QUOTED_LOOP = re.compile(r"\bloop\s+'([^']{6,})'")
_VERIFY = re.compile(r"\bverify\b:?\s*([^;·]+)", re.I)
# The row's own text naming WHO files its loop. When it names another filer
# ("tools/job-watchdog.py scan files/updates loop ...", "this reader opens one
# ... loop", "nightly collector owns reconciliation") this consumer never files
# for it: a second writer is how duplicates happen. It only looks for that loop.
_EXECUTOR = re.compile(r"(?P<script>[\w./-]+\.(?:py|sh))(?: \w+)? files/updates loop"
                       r"|\b(?P<reader>this reader) opens\b"
                       r"|\b(?P<collector>[\w-]+ collector) owns\b", re.I)
_CLEAR = re.compile(r"\b(?:auto-clears?|auto-closes?|clears? (?:when|after|on))\b:?\s*([^;·]+)", re.I)


@dataclass(frozen=True)
class BoundAction:
    owner: str
    remediation: str
    verify: str
    auto_clear: str
    text: str


@dataclass(frozen=True)
class Breach:
    row_id: str
    level: str
    headline: str
    action: BoundAction | None
    declared_key: str | None = None
    executor: str | None = None
    loop_number: str | None = None
    phrases: tuple = ()

    @property
    def digest(self) -> str:
        source = self.action.text if self.action else self.headline
        return hashlib.sha256(source.encode()).hexdigest()[:8]

    @property
    def dedup_key(self) -> str:
        if self.loop_number:
            return "#" + self.loop_number
        return self.declared_key or f"health-breach:{self.row_id}"

    @property
    def tag(self) -> str:
        return f"{TAG}{self.row_id} "


def _clauses(text: str) -> list[str]:
    if " · " in text:
        parts = re.split(r"\s+·\s+", text)
    else:
        parts = re.split(r";\s+|(?<=\.)\s+(?=[A-Z])", text)
    return [p.strip(" ()").rstrip(".").strip() for p in parts if p.strip(" ()")]


def parse_action(text: str) -> BoundAction | None:
    """Split one row's bound-action text into owner/remediation/verify/auto-clear."""
    match = _BREACH.search(text or "")
    if not match:
        return None
    action = text[match.end():].strip()
    owner = verify = auto_clear = ""
    explicit, loose = [], []
    for clause in _clauses(action):
        low = clause.lower()
        if low.startswith("owner"):
            continue
        if low.startswith("verify"):
            verify = "; ".join(filter(None, (verify, re.sub(r"^verify\b:?\s*", "", clause, flags=re.I))))
        elif re.match(r"(auto-clears?|auto-closes?|clears? (when|after|on))\b", low):
            if not auto_clear:
                hit = _CLEAR.search(clause)
                auto_clear = hit.group(1).strip() if hit else clause
        elif re.match(r"(remediation|fix|remedy)\b", low):
            explicit.append(re.sub(r"^(remediation|fix|remedy)\b:?\s*", "", clause, flags=re.I))
        elif re.search(r"\bloops?\b", low) and not re.match(r"(run|repair|restore|inspect|read)\b", low):
            # "update loop #665 (owner ...) — read the log, fix the gate": the
            # loop declaration is not remediation, what follows the dash is.
            if " — " in clause:
                loose.append(clause.split(" — ", 1)[1].strip())
        else:
            loose.append(clause)
    owner_match = _OWNER.search(action)
    owner = owner_match.group(1).strip() if owner_match else ""
    verify_match, clear_match = _VERIFY.search(action), _CLEAR.search(action)
    if not verify and verify_match:
        verify = verify_match.group(1).strip()
    if not auto_clear and clear_match:
        auto_clear = clear_match.group(1).strip()
    remediation = "; ".join(explicit or loose)
    return BoundAction(owner=owner, remediation=remediation, verify=verify,
                       auto_clear=auto_clear, text=action)


def breach_from_finding(finding: dict) -> Breach:
    key = str(finding.get("key") or "unknown")
    subject = re.sub(r"\s+", "_", str(finding.get("subject") or "").strip())
    detail = " ".join(str(finding.get("detail") or "").split())
    row_id = f"{key}/{subject}" if subject else key
    head = _BREACH.split(detail, maxsplit=1)[0].rstrip(" ·;—-")
    first = head.split(" ", 1)[0].upper() if head else ""
    level = first if first in _LEVELS else ("FAIL" if finding.get("hard_error") else "WARN")
    action = parse_action(detail)
    declared = number = None
    named = _EXECUTOR.search(detail)
    executor = next((v for v in named.groupdict().values() if v), None) if named else None
    phrases = [f"{TAG}{row_id} "]
    if action:
        numbered = _NUMBERED_LOOP.search(action.text)
        keyed = _KEYED_LOOP.search(action.text)
        quoted = _QUOTED_LOOP.search(action.text)
        if numbered:
            number = numbered.group(1)
        elif keyed:
            declared = keyed.group(1)
            phrases.append(declared)
        elif quoted:
            declared = quoted.group(1)
            phrases.append(declared)
        phrases.extend(READER_LOOP_PHRASES.get(key, ()))
    return Breach(row_id=row_id, level=level,
                  headline=head, action=action, declared_key=declared, executor=executor,
                  loop_number=number, phrases=tuple(phrases))


# ── the verb door ───────────────────────────────────────────────────────────
# A door is `door(verb, args) -> result` where result has `.ok`, `.reply` and
# `.describe()` — exactly lib.record_call.VerbResult. The selftest passes a fake.

def record_door(verb: str, args: dict):
    from lib.record_call import call_verb
    return call_verb(verb, args, timeout=45)


def _search(door, phrase: str, limit: int = SEARCH_LIMIT):
    result = door("loop-board", {"kind": "open_loop", "status": "open",
                                 "search": phrase, "limit": limit})
    if not result.ok or not isinstance(result.reply, dict):
        return None, result.describe()
    return [row for row in result.reply.get("loops") or [] if isinstance(row, dict)], None


def _read_number(door, number: str):
    result = door("read-loop", {"number": number, "kind": "open_loop"})
    if not result.ok or not isinstance(result.reply, dict):
        if isinstance(result.reply, dict) and result.reply.get("error") == "not_found":
            return [], None
        return None, result.describe()
    row = result.reply.get("loop", result.reply)
    if row.get("status") != "open":
        return [], None
    label = (row.get("title") or (row.get("body") or "").split("\n", 1)[0])
    return [{"number": str(row.get("number")), "owner": row.get("owner"),
             "version": row.get("version"), "label": label}], None


def find_open_loops(breaches: list[Breach], door, workers: int = 8) -> dict:
    """row_id -> (open loops sorted by number, error or None). One search per
    distinct phrase, run in parallel; the record layer is the dedup authority."""
    def keys(b: Breach) -> list[tuple[str, str]]:
        return ([("#", b.loop_number)] if b.loop_number else []) + [("search", p) for p in b.phrases]
    wanted = list(dict.fromkeys(k for b in breaches for k in keys(b)))
    def run(job):
        kind, value = job
        return job, (_read_number(door, value) if kind == "#" else _search(door, value))
    with ThreadPoolExecutor(max_workers=max(1, workers)) as pool:
        answers = dict(pool.map(run, wanted))
    found = {}
    for b in breaches:
        loops, error = {}, None
        for job in keys(b):
            rows, err = answers[job]
            if err:
                error = err
                continue
            for row in rows:
                label = str(row.get("label") or "")
                if label.startswith(TAG) and not label.startswith(b.tag):
                    continue  # another row's tag that merely contains this phrase
                loops[str(row.get("number"))] = row
        ordered = sorted(loops.values(), key=lambda r: (int(re.sub(r"\D", "", str(r.get("number"))) or 0)))
        found[b.row_id] = (ordered, error)
    return found


def _own(loop: dict, b: Breach) -> bool:
    return str(loop.get("label") or "").startswith(b.tag)


def _loop_digest(loop: dict) -> str:
    label = str(loop.get("label") or "")
    parts = label[len(TAG):].split("]", 1)[0].split(" ")
    return parts[1] if len(parts) > 1 else ""


# ── audit: printed by every health run ──────────────────────────────────────

@dataclass
class Audit:
    red: int = 0
    unlooped: list = field(default_factory=list)
    unbound: list = field(default_factory=list)
    delegated: list = field(default_factory=list)
    duplicated: list = field(default_factory=list)
    error: str | None = None


def audit(findings: list[dict], door) -> Audit:
    breaches = [breach_from_finding(f) for f in findings]
    result = Audit(red=len(breaches))
    if not breaches:
        return result
    found = find_open_loops(breaches, door)
    for b in breaches:
        loops, error = found.get(b.row_id, ([], "not searched"))
        if error and not loops:
            result.error = error
            continue
        if not loops:
            result.unlooped.append(b.row_id)
            if b.action is None:
                result.unbound.append(b.row_id)
            elif b.executor:
                result.delegated.append((b.row_id, b.executor))
        elif len(loops) > 1:
            result.duplicated.append((b.row_id, [str(l.get("number")) for l in loops]))
    return result


AUDIT_ACTION = ("on breach: owner orchestrator · remediation ./run.sh health --act files or updates "
                "one deduplicated loop per red row (bin/nightly.sh runs it); a row with no bound action "
                "needs an on-breach clause in tools/health-check.py; for a duplicated row the owner "
                "keeps the lowest-numbered loop and closes the others naming it as successor · verify the next "
                "./run.sh health prints 0 red rows with no open loop and 0 duplicated · auto-clear when "
                "every red row has exactly one open loop")


def render_audit(result: Audit) -> str:
    if result.error and not (result.unlooped or result.duplicated):
        return (f"  UNKNOWN breach audit — record layer unreadable ({result.error[:160]}); "
                f"{result.red} red row(s) unchecked · {AUDIT_ACTION}")
    count = len(result.unlooped)
    mark = "OK" if not count and not result.duplicated and not result.error else "⚠︎"
    parts = [f"{count} of {result.red} red row(s) have no open loop"]
    if result.unbound:
        parts.append(f"{len(result.unbound)} of them carry no bound action ({', '.join(result.unbound[:6])}"
                     f"{', …' if len(result.unbound) > 6 else ''})")
    if result.delegated:
        filers = sorted({who for _, who in result.delegated})
        parts.append(f"{len(result.delegated)} of them are filed by a filer the row names ("
                     + ", ".join(filers) + "), whose loop body carries no key this audit can search")
    delegated = {r for r, _ in result.delegated}
    bound_unlooped = [r for r in result.unlooped if r not in result.unbound and r not in delegated]
    if bound_unlooped:
        parts.append("bound but unlooped: " + ", ".join(bound_unlooped[:6])
                     + (", …" if len(bound_unlooped) > 6 else ""))
    if result.duplicated:
        parts.append(f"{len(result.duplicated)} row(s) with duplicate open loops ("
                     + "; ".join(f"{r} #{'/#'.join(n)}" for r, n in result.duplicated[:4]) + ")")
    if result.error:
        parts.append(f"some searches failed ({result.error[:80]})")
    return f"  {mark} {'breach audit':<18} " + "; ".join(parts) + " · " + AUDIT_ACTION


# ── act: `./run.sh health --act` ────────────────────────────────────────────

def _loop_owner(text: str) -> str:
    low = (text or "").strip().lower()
    return low if low in ("joe", "dell") else "claude"


def _body(b: Breach, observed: str) -> str:
    a = b.action
    assert a is not None, "only a row with a bound action is filed"
    return "\n".join([
        f"{b.tag.rstrip()} {b.digest}] "
        f"{b.headline if b.headline.upper().startswith(b.level) else b.level + ' ' + b.headline}"[:600],
        f"Owner (as the health row names it): {a.owner or 'not named'}",
        f"Remediation: {a.remediation or 'not named'}",
        f"Verify: {a.verify or 'not named'}",
        f"Auto-clear: {a.auto_clear or 'not named'}",
        f"Dedup key: {b.dedup_key}. Filed by ./run.sh health --act (tools/health_breach.py), "
        f"one loop per health row; observed {observed}.",
        f"Bound action as printed: {a.text}",
    ])


def _key(*parts) -> str:
    return str(uuid.uuid5(NAMESPACE, ":".join(str(p) for p in parts)))


@dataclass
class ActReport:
    receipts: list = field(default_factory=list)

    def count(self, decision: str) -> int:
        return sum(1 for r in self.receipts if r["decision"] == decision)

    @property
    def errors(self) -> int:
        return self.count("error")

    def summary(self) -> str:
        names = ("filed", "updated", "unchanged", "covered", "delegated", "cleared", "clear_noted",
                 "unbound", "error")
        return ", ".join(f"{n} {self.count(n)}" for n in names if self.count(n))


def act(findings: list[dict], door, *, receipts_path: Path | str, complete: bool,
        now: Callable[[], datetime] = lambda: datetime.now(timezone.utc)) -> ActReport:
    """File/update one loop per red row; auto-clear this consumer's loops whose
    row went green. `complete` must be True only for a full, finished run —
    a partial run cannot prove a row green, so it clears nothing."""
    report = ActReport()
    stamp = now().strftime("%Y-%m-%dT%H:%M:%SZ")
    breaches = [breach_from_finding(f) for f in findings]
    found = find_open_loops(breaches, door) if breaches else {}

    def receipt(b_row, decision, **extra):
        report.receipts.append({"at": stamp, "row_id": b_row, "decision": decision, **extra})

    for b in breaches:
        base = {"level": b.level, "dedup_key": b.dedup_key}
        if b.action is None:
            receipt(b.row_id, "unbound", **base, detail="row carries no bound action; nothing to execute")
            continue
        loops, error = found.get(b.row_id, ([], "not searched"))
        if error:
            receipt(b.row_id, "error", **base, detail="dedup search failed, filing refused: " + error)
            continue
        dupes = [str(l.get("number")) for l in loops] if len(loops) > 1 else []
        mine = [l for l in loops if _own(l, b)]
        if not loops and b.executor:
            receipt(b.row_id, "delegated", **base, executor=b.executor,
                    detail="the row names its own filer; a second writer would duplicate its loop")
        elif not loops:
            owner = _loop_owner(b.action.owner)
            payload = {"idempotency_key": _key("add", b.row_id, b.digest, stamp[:10]),
                       "kind": "open_loop", "owner": owner, "domain": "system", "marker": "none",
                       "body": _body(b, stamp), "source_note": f"health-breach:{b.row_id}",
                       "blocker": "other_lane" if owner == "claude" else "human_only",
                       "blocker_detail": (f"Orchestrator remediation of health row {b.row_id}"
                                          if owner == "claude" else
                                          f"{owner.capitalize()} performs the remediation health row {b.row_id} names")}
            result = door("add-loop", payload)
            number = result.reply.get("number") if result.ok and isinstance(result.reply, dict) else None
            receipt(b.row_id, "filed" if result.ok else "error", **base, verb="add-loop",
                    loop=number, loop_id=(result.reply or {}).get("loop_id") if result.ok else None,
                    owner=owner, detail=None if result.ok else result.describe())
        elif mine:
            target = mine[0]
            if _loop_digest(target) == b.digest:
                receipt(b.row_id, "unchanged", **base, loop=str(target.get("number")), duplicates=dupes)
                continue
            version = target.get("version")
            result = door("update-loop", {"idempotency_key": _key("update", target.get("number"), version, b.digest),
                                          "number": str(target.get("number")), "kind": "open_loop",
                                          "base_version": version, "body": _body(b, stamp)})
            receipt(b.row_id, "updated" if result.ok else "error", **base, verb="update-loop",
                    loop=str(target.get("number")), duplicates=dupes,
                    detail=None if result.ok else result.describe())
        else:
            receipt(b.row_id, "covered", **base, loop=str(loops[0].get("number")), duplicates=dupes,
                    detail="an open loop filed by this row's own reader already carries the action")

    if complete:
        red = {b.row_id for b in breaches}
        own, error = _search(door, TAG, OWN_LOOPS_LIMIT)
        if error:
            receipt("*", "error", detail="own-loop search failed, auto-clear skipped: " + error)
            own = []
        for loop in own:
            label = str(loop.get("label") or "")
            if not label.startswith(TAG):
                continue
            row_id = label[len(TAG):].split(" ", 1)[0].rstrip("]")
            if row_id in red or _loop_digest(loop) == CLEARED:
                continue
            number, version = str(loop.get("number")), loop.get("version")
            if _loop_owner(str(loop.get("owner"))) == "claude":
                result = door("close-loop", {
                    "idempotency_key": _key("close", number, version), "number": number,
                    "kind": "open_loop", "base_version": version, "resolution": "done",
                    "outcome": (f"Auto-cleared: the complete health run at {stamp} no longer reports "
                                f"row {row_id} red, which is the row's own auto-clear condition.")})
                receipt(row_id, "cleared" if result.ok else "error", verb="close-loop", loop=number,
                        detail=None if result.ok else result.describe())
            else:
                read = door("read-loop", {"number": number, "kind": "open_loop"})
                row = read.reply.get("loop", read.reply) if read.ok and isinstance(read.reply, dict) else {}
                body = row.get("body") if isinstance(row, dict) else None
                if not isinstance(body, str):
                    receipt(row_id, "error", verb="read-loop", loop=number, detail=read.describe())
                    continue
                rest = body.split("\n", 1)[1] if "\n" in body else ""
                note = (f"{TAG}{row_id} {CLEARED}] CLEARED {stamp}: the health row is no longer red. "
                        f"The close is the owner's call ({loop.get('owner')}).")
                result = door("update-loop", {"idempotency_key": _key("clear-note", number, version),
                                              "number": number, "kind": "open_loop",
                                              "base_version": version, "body": note + "\n" + rest})
                receipt(row_id, "clear_noted" if result.ok else "error", verb="update-loop",
                        loop=number, detail=None if result.ok else result.describe())

    path = Path(receipts_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as sink:
        for row in report.receipts:
            sink.write(json.dumps(row, sort_keys=True) + "\n")
    return report
