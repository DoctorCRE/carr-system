"""launchd_calendar — the one place a CARR interval becomes a StartCalendarInterval.

WHY (2026-09-26, Mac Studio, macOS 27.0). Every LaunchAgent scheduled with
StartInterval sat at `runs = 0` with `pended nondemand spawn =
speculative|interval`; even RunAtLoad did not fire. `launchctl kickstart` ran
them, and every StartCalendarInterval agent on the same machine fired on time.
The MacBook on macOS 26.6.2 does not show it. Fourteen CARR jobs were dead on
the hub, and release-pipeline and fleet-sync only ever ran because a session
kickstarted them. Replacing room-bridge's `StartInterval 60` with sixty
`{Minute: n}` entries and re-bootstrapping made it fire on schedule within two
minutes. So StartInterval is not an option for a CARR template any more, on any
Mac: the templates are shared, and a job that works on one machine and is
silently dead on the other is the worst failure a scheduler can have.

THE CONVERSION. An interval of N seconds becomes the calendar entries that fire
every N seconds, aligned to the clock:

  * N below an hour that divides it (60, 120, 300, 600, 900, 1800 ...): one
    `{Minute: m}` per m where m % (N/60) == 0. 60s is all sixty minutes.
  * N of exactly an hour: ONE `{Minute: M}`, where M is the job's fixed minute
    from HOURLY_MINUTE below, or, for a label not in that table, sha256(label)
    mod 60. Hourly jobs are deliberately NOT all at :00.
  * N of whole hours dividing a day (2h, 3h, 4h, 6h, 8h, 12h, 24h): one
    `{Hour: h, Minute: M}` per h, with M chosen as for hourly.
  * ANY OTHER N (7 minutes, 90 seconds, 5 hours, a week) is rounded to the
    nearest supported step, ties to the MORE frequent step because firing a
    little early is recoverable and a job that is late by design is not, and
    the caller gets a note saying so. Every template records the interval it
    asked for, so the rounding stays visible in the file.

WHY A TABLE FOR THE HOURLY MINUTE, not a hash alone and not :00 for everyone.
Judged with ops/jev_judge.py when this was written (explicit table 0.84, pure
hash 0.16, all at :00 0.00): a hand-picked table is readable in review and
guarantees the five hourly jobs never collide, and the hash fallback still gives
a new hourly job a stable spread-out minute without anyone remembering to add
it. The table's minutes are odd and not multiples of five, so an hourly job
never lands on the same minute as the 120s and 300s jobs' grid, and none of them
coincides with the fixed daily minutes the calendar jobs already use (:00, :05,
:10, :15, :20, :30, :45).

HOW TEMPLATES USE IT. The plists in ops/launchd are static XML that
ops/config-as-code.py installs by token substitution and compares byte for byte,
so the conversion happens once, into the template, not at install time. Each
converted template carries a marker comment naming the interval it asked for:

    <!-- carr-launchd-interval-seconds: 300 | ... -->

and audit_template() re-derives the array from that marker, so a hand edit to
one minute is drift a test catches rather than a schedule that quietly changed.
To convert a new template, write the StartInterval you mean and run

    python3 -m lib.launchd_calendar rewrite ops/launchd/<file>.plist

A template that still carries StartInterval is REFUSED by ops/config-as-code.py
(check and install) and by ops/launchd-calendar-selftest.py.
"""
from __future__ import annotations

import hashlib
import plistlib
import re
from pathlib import Path

MARKER = "carr-launchd-interval-seconds"

# The hourly jobs, each at its own minute. See the module docstring for why.
HOURLY_MINUTE: dict[str, int] = {
    "com.carr.canonical-dirty-watchdog": 7,
    "com.carr.cc-version-sentinel": 19,
    "com.carr.fleet-sync": 31,
    "com.carr.gate-zero-canary": 43,
    "com.carr.notes-sweep": 53,
}

# Every step (in minutes) a calendar can express as an evenly spaced schedule:
# the divisors of an hour, the hour itself, and the divisors of a day in hours.
_MINUTE_STEPS = (1, 2, 3, 4, 5, 6, 10, 12, 15, 20, 30)
_HOUR_STEPS = (1, 2, 3, 4, 6, 8, 12, 24)
STEPS_MINUTES = _MINUTE_STEPS + tuple(h * 60 for h in _HOUR_STEPS)

_COMMENT = re.compile(r"<!--.*?-->", re.S)
_START_INTERVAL = re.compile(
    r"(?P<indent>[ \t]*)<key>StartInterval</key>\s*<integer>\s*(?P<n>\d+)\s*</integer>[ \t]*\n?")
_MARKER_RE = re.compile(re.escape(MARKER) + r":\s*(\d+)")

TEMPLATE_DIRS = ("ops/launchd", "tools/dictation-rig/launchd")


def hourly_minute(label: str) -> int:
    """The fixed minute an hourly (or multi-hour) job fires at."""
    if label in HOURLY_MINUTE:
        return HOURLY_MINUTE[label]
    return int(hashlib.sha256(label.encode("utf-8")).hexdigest(), 16) % 60


def _step_minutes(seconds: int) -> tuple[int, str | None]:
    if seconds % 60 == 0 and seconds // 60 in STEPS_MINUTES:
        return seconds // 60, None
    # Nearest supported step; on a tie the smaller (more frequent) one wins.
    step = min(STEPS_MINUTES, key=lambda s: (abs(s * 60 - seconds), s))
    return step, (f"interval {seconds}s does not divide the hour or the day evenly; "
                  f"rounded to the nearest supported step, {step * 60}s")


def calendar_for_interval(seconds: int, label: str) -> tuple[list[dict], str | None]:
    """(StartCalendarInterval entries, rounding note or None) for an interval."""
    if isinstance(seconds, bool) or not isinstance(seconds, int) or seconds <= 0:
        raise ValueError(f"interval must be a positive integer number of seconds, got {seconds!r}")
    step, note = _step_minutes(seconds)
    if step < 60:
        return [{"Minute": m} for m in range(0, 60, step)], note
    minute = hourly_minute(label)
    if step == 60:
        return [{"Minute": minute}], note
    return [{"Hour": h, "Minute": minute} for h in range(0, 24, step // 60)], note


def cadence_seconds(plist: dict) -> int | None:
    """The period of an evenly spaced StartCalendarInterval, or None.

    The inverse of calendar_for_interval for everything it produces, so a
    selftest can assert "this job runs every 30 minutes" without caring which
    key launchd is given."""
    cal = plist.get("StartCalendarInterval")
    if cal is None:
        return None
    entries = [cal] if isinstance(cal, dict) else list(cal)
    if not entries or not all(isinstance(e, dict) for e in entries):
        return None
    shapes = {frozenset(e) for e in entries}
    if shapes == {frozenset()}:
        return 60
    if shapes == {frozenset({"Minute"})}:
        return _even_period(sorted(e["Minute"] for e in entries), 60, 60)
    if shapes == {frozenset({"Hour", "Minute"})}:
        if len({e["Minute"] for e in entries}) != 1:
            return None
        return _even_period(sorted(e["Hour"] for e in entries), 24, 3600)
    return None


def _even_period(values: list[int], cycle: int, unit: int) -> int | None:
    if len(values) != len(set(values)):
        return None
    if len(values) == 1:
        return cycle * unit
    step = values[1] - values[0]
    if step <= 0 or step * len(values) != cycle:
        return None
    if any(b - a != step for a, b in zip(values, values[1:])):
        return None
    return step * unit


def render_xml(entries: list[dict], indent: str) -> str:
    """The StartCalendarInterval key and array, one entry per line."""
    inner = indent + (indent if indent else "  ")
    lines = [f"{indent}<key>StartCalendarInterval</key>", f"{indent}<array>"]
    for entry in entries:
        body = "".join(f"<key>{k}</key><integer>{entry[k]}</integer>"
                       for k in ("Hour", "Minute") if k in entry)
        lines.append(f"{inner}<dict>{body}</dict>")
    lines.append(f"{indent}</array>")
    return "\n".join(lines) + "\n"


def plist_label(text: str) -> str:
    try:
        return str(plistlib.loads(text.encode("utf-8")).get("Label", ""))
    except Exception:
        match = re.search(r"<key>Label</key>\s*<string>([^<]+)</string>", text)
        return match.group(1) if match else ""


def _uncommented_spans(text: str) -> list[tuple[int, int]]:
    spans, pos = [], 0
    for match in _COMMENT.finditer(text):
        spans.append((pos, match.start()))
        pos = match.end()
    spans.append((pos, len(text)))
    return spans


def rewrite_template(text: str) -> tuple[str, list[tuple[int, str | None]]]:
    """Replace every live StartInterval with its calendar form.

    Returns (new_text, [(seconds, note), ...]). Text inside comments is left
    alone, and a template with nothing to convert comes back unchanged."""
    label = plist_label(text)
    spans = _uncommented_spans(text)
    out, changes, pos = [], [], 0
    for match in _START_INTERVAL.finditer(text):
        if not any(a <= match.start() and match.end() <= b + 1 for a, b in spans):
            continue
        seconds = int(match.group("n"))
        entries, note = calendar_for_interval(seconds, label)
        indent = match.group("indent")
        marker = (f"{indent}<!-- {MARKER}: {seconds} | rendered by lib/launchd_calendar.py;"
                  f" macOS 27 launchd never fires StartInterval"
                  + (f" | {note}" if note else "") + " -->\n")
        out.append(text[pos:match.start()])
        out.append(marker + render_xml(entries, indent))
        pos = match.end()
        changes.append((seconds, note))
    out.append(text[pos:])
    return "".join(out), changes


def audit_template(text: str) -> list[str]:
    """Problems with one CARR LaunchAgent template; empty means it is sound.

    A live StartInterval is refused outright. A template carrying the marker
    must hold exactly the calendar the converter renders for that interval."""
    problems = []
    if "<key>StartInterval</key>" in _COMMENT.sub("", text):
        problems.append("StartInterval is REFUSED: launchd on macOS 27 never fires it "
                        "(runs=0, even RunAtLoad); convert with "
                        "`python3 -m lib.launchd_calendar rewrite <template>`")
    markers = _MARKER_RE.findall(text)
    if len(markers) > 1:
        problems.append(f"{len(markers)} {MARKER} markers; a template has one schedule")
    elif markers:
        try:
            parsed = plistlib.loads(text.encode("utf-8"))
        except Exception as exc:
            return problems + [f"not a parseable plist: {exc}"]
        expected, _ = calendar_for_interval(int(markers[0]), str(parsed.get("Label", "")))
        actual = parsed.get("StartCalendarInterval")
        if isinstance(actual, dict):
            actual = [actual]
        if actual != expected:
            problems.append(f"StartCalendarInterval does not match what lib/launchd_calendar.py "
                            f"renders for {markers[0]}s; re-render instead of hand-editing")
    return problems


def carr_templates(repo) -> list[Path]:
    """Every tracked-location CARR LaunchAgent template under the repo."""
    root = Path(repo)
    found = []
    for rel in TEMPLATE_DIRS:
        folder = root / rel
        if folder.is_dir():
            found.extend(sorted(p for p in folder.glob("com.carr.*.plist") if p.is_file()))
    return found


def _main(argv: list[str]) -> int:
    if len(argv) < 2 or argv[0] not in ("rewrite", "audit"):
        print("usage: python3 -m lib.launchd_calendar rewrite|audit <template.plist> ...")
        return 64
    status = 0
    for name in argv[1:]:
        path = Path(name)
        text = path.read_text(encoding="utf-8")
        if argv[0] == "rewrite":
            new, changes = rewrite_template(text)
            if changes:
                path.write_text(new, encoding="utf-8")
            for seconds, note in changes:
                print(f"{path}: StartInterval {seconds}s -> StartCalendarInterval"
                      + (f" ({note})" if note else ""))
            text = new
        for problem in audit_template(text):
            print(f"{path}: {problem}")
            status = 1
    return status


if __name__ == "__main__":
    import sys
    raise SystemExit(_main(sys.argv[1:]))
