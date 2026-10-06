#!/usr/bin/env python3
"""Proof for lib/launchd_calendar.py, the one StartInterval -> StartCalendarInterval converter.

WHY THIS EXISTS. On the Mac Studio (macOS 27.0) every LaunchAgent scheduled
with StartInterval sat at `runs = 0` with `pended nondemand spawn =
speculative|interval`, and even RunAtLoad never fired; StartCalendarInterval
agents fired normally. Fourteen CARR jobs were silently dead there. The fix is
to express every interval as a calendar schedule, and this file holds the three
things that fix depends on:

  1. THE CONVERTER. Every cadence CARR uses (60, 120, 300, 600, 900, 1800, 3600
     seconds) converts to the exact minute set, and an interval that does not
     divide the hour is rounded to the nearest divisor with a note saying so.
  2. THE TREE. No CARR LaunchAgent template carries StartInterval, and every
     template the converter rendered still matches what the converter would
     render now, so a hand edit to one minute array cannot drift silently.
  3. THE REFUSAL. A planted StartInterval is caught, and a StartInterval that
     only appears in a comment is not (the templates explain themselves in
     prose, and prose is not a key launchd reads).
"""
from __future__ import annotations

import plistlib
import re
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))
from lib import launchd_calendar as lc  # noqa: E402

RESULTS: list[bool] = []


def check(label: str, condition: bool, detail: object = "") -> None:
    RESULTS.append(bool(condition))
    print(f"{'PASS' if condition else 'FAIL'}  {label}"
          + ("" if condition or detail == "" else f": {detail!r}"))


def minutes(entries):
    return [entry["Minute"] for entry in entries]


def plist_text(label: str, schedule_xml: str) -> str:
    return (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" '
        '"http://www.apple.com/DTDs/PropertyList-1.0.dtd">\n'
        '<plist version="1.0">\n<dict>\n'
        f'  <key>Label</key><string>{label}</string>\n'
        '  <key>ProgramArguments</key>\n  <array>\n'
        '    <string>/usr/bin/true</string>\n  </array>\n'
        '  <!-- prose may say StartInterval; launchd never reads a comment -->\n'
        '  <!-- retired: <key>StartInterval</key><integer>60</integer> -->\n'
        f'{schedule_xml}'
        '  <key>RunAtLoad</key><true/>\n'
        '</dict>\n</plist>\n'
    )


# --------------------------------------------------------------------------
# 1. The converter, one cadence at a time.
# --------------------------------------------------------------------------
for seconds, expected in (
    (60, list(range(0, 60))),
    (120, list(range(0, 60, 2))),
    (300, list(range(0, 60, 5))),
    (600, [0, 10, 20, 30, 40, 50]),
    (900, [0, 15, 30, 45]),
    (1800, [0, 30]),
):
    entries, note = lc.calendar_for_interval(seconds, "com.carr.example")
    check(f"{seconds}s converts to minutes {expected[:4]}{'...' if len(expected) > 4 else ''}",
          minutes(entries) == expected and all(set(e) == {"Minute"} for e in entries)
          and note is None,
          (minutes(entries), note))
    check(f"{seconds}s reads back as {seconds}s", lc.cadence_seconds(
        {"StartCalendarInterval": entries}) == seconds)

# Hourly: one entry, at the label's fixed minute, never :00 for the table's jobs.
for label, minute in lc.HOURLY_MINUTE.items():
    entries, note = lc.calendar_for_interval(3600, label)
    check(f"3600s for {label} is one entry at :{minute:02d}",
          entries == [{"Minute": minute}] and note is None, (entries, note))
table_minutes = list(lc.HOURLY_MINUTE.values())
check("the hourly table spreads its jobs (distinct minutes, none at :00)",
      len(set(table_minutes)) == len(table_minutes) and 0 not in table_minutes,
      table_minutes)
check("the hourly table avoids the 2- and 5-minute grids the sub-hour jobs fire on",
      all(m % 2 and m % 5 for m in table_minutes), table_minutes)
unknown_a, _ = lc.calendar_for_interval(3600, "com.carr.not-in-table")
unknown_b, _ = lc.calendar_for_interval(3600, "com.carr.not-in-table")
check("an hourly label outside the table gets a stable hash-derived minute",
      unknown_a == unknown_b and len(unknown_a) == 1
      and 0 <= unknown_a[0]["Minute"] < 60, unknown_a)
check("an hourly schedule reads back as 3600s",
      lc.cadence_seconds({"StartCalendarInterval": [{"Minute": 43}]}) == 3600)
check("a single-dict (non-array) hourly schedule reads back as 3600s",
      lc.cadence_seconds({"StartCalendarInterval": {"Minute": 43}}) == 3600)

# Non-divisors: rounded to the nearest divisor of the hour, with a note.
entries, note = lc.calendar_for_interval(420, "com.carr.example")
check("420s (7 min) does not divide the hour; rounds to 6 min with a note",
      minutes(entries) == list(range(0, 60, 6)) and note is not None
      and "420" in note and "360" in note, (minutes(entries), note))
entries, note = lc.calendar_for_interval(480, "com.carr.example")
check("480s (8 min) is equidistant from 6 and 10; the tie goes to the more frequent 6",
      minutes(entries) == list(range(0, 60, 6)) and note is not None, (minutes(entries), note))
entries, note = lc.calendar_for_interval(90, "com.carr.example")
check("90s (not a whole minute) rounds to a divisor and says so",
      note is not None and lc.cadence_seconds({"StartCalendarInterval": entries}) in (60, 120),
      (minutes(entries), note))
entries, note = lc.calendar_for_interval(30, "com.carr.example")
check("30s is below launchd's calendar resolution; every minute, with a note",
      minutes(entries) == list(range(60)) and note is not None, note)
entries, note = lc.calendar_for_interval(7200, "com.carr.fleet-sync")
check("7200s is every other hour at the label's minute",
      entries == [{"Hour": h, "Minute": lc.HOURLY_MINUTE["com.carr.fleet-sync"]}
                  for h in range(0, 24, 2)] and note is None, (entries, note))
check("7200s reads back as 7200s",
      lc.cadence_seconds({"StartCalendarInterval": entries}) == 7200)
entries, note = lc.calendar_for_interval(5 * 3600, "com.carr.example")
check("5h does not divide the day; rounds to 4h with a note",
      len(entries) == 6 and note is not None, (entries, note))
for bad in (0, -60, 1.5, "60", True):
    try:
        lc.calendar_for_interval(bad, "com.carr.example")  # type: ignore[arg-type]
        check(f"{bad!r} is refused", False)
    except ValueError:
        check(f"{bad!r} is refused", True)
check("an irregular calendar has no single cadence",
      lc.cadence_seconds({"StartCalendarInterval": [{"Minute": 0}, {"Minute": 7}]}) is None)
check("a plist with no calendar has no cadence", lc.cadence_seconds({}) is None)

# --------------------------------------------------------------------------
# 2. Rewrite and audit on a synthetic template.
# --------------------------------------------------------------------------
planted = plist_text("com.carr.planted", "  <key>StartInterval</key><integer>300</integer>\n")
problems = lc.audit_template(planted)
check("a planted StartInterval is REFUSED by the audit",
      any("StartInterval" in p for p in problems), problems)
only_prose = plist_text("com.carr.prose", "")
check("StartInterval named only in a comment is not a finding",
      lc.audit_template(only_prose) == [], lc.audit_template(only_prose))

rewritten, changes = lc.rewrite_template(planted)
check("rewrite converts the planted key and reports it",
      changes == [(300, None)]
      and "<key>StartInterval</key>" not in re.sub(r"<!--.*?-->", "", rewritten, flags=re.S), changes)
parsed = plistlib.loads(rewritten.encode("utf-8"))
check("the rewritten template is a valid plist with the 300s calendar and RunAtLoad kept",
      "StartInterval" not in parsed and parsed.get("RunAtLoad") is True
      and minutes(parsed["StartCalendarInterval"]) == list(range(0, 60, 5)), parsed)
check("the rewritten template passes the audit", lc.audit_template(rewritten) == [],
      lc.audit_template(rewritten))
again, again_changes = lc.rewrite_template(rewritten)
check("rewrite is idempotent", again == rewritten and again_changes == [])

split_lines = plist_text("com.carr.split",
                         "  <key>StartInterval</key>\n  <integer>3600</integer>\n")
split_out, split_changes = lc.rewrite_template(split_lines)
check("rewrite also handles the key and integer on separate lines",
      split_changes == [(3600, None)]
      and plistlib.loads(split_out.encode())["StartCalendarInterval"]
      == lc.calendar_for_interval(3600, "com.carr.split")[0], split_changes)

tampered = rewritten.replace("<integer>55</integer>", "<integer>54</integer>", 1)
check("a hand-edited minute in a rendered array is caught as drift from the converter",
      any("does not match" in p for p in lc.audit_template(tampered)),
      lc.audit_template(tampered))

check("a key-shaped StartInterval inside a comment survives rewrite untouched",
      "<!-- retired: <key>StartInterval</key><integer>60</integer> -->" in rewritten
      and rewritten.count("<key>StartCalendarInterval</key>") == 1)
check("a commented-out StartInterval key alone is not refused",
      lc.audit_template(plist_text("com.carr.commented", "")) == [])
doubled = rewritten.replace(
    "  <key>RunAtLoad</key>",
    f"  <!-- {lc.MARKER}: 300 | a second, stray marker -->\n  <key>RunAtLoad</key>", 1)
check("two interval markers in one template are refused",
      any("markers" in p for p in lc.audit_template(doubled)), lc.audit_template(doubled))

# --------------------------------------------------------------------------
# 3. The real tree.
# --------------------------------------------------------------------------
templates = lc.carr_templates(REPO)
check("the template roster covers ops/launchd and the dictation-rig copies",
      any(p.parent.name == "launchd" and p.parent.parent.name == "ops" for p in templates)
      and any("dictation-rig" in str(p) for p in templates), len(templates))
for path in templates:
    rel = path.relative_to(REPO)
    found = lc.audit_template(path.read_text(encoding="utf-8"))
    check(f"{rel} carries no StartInterval and matches the converter", found == [], found)

# The dictation-rig keeps its own copies of three agents. Their bodies differ
# on purpose (different prose), but a schedule that differs between the twins
# would mean one installer path brings back a cadence the other retired.
by_name: dict[str, list[Path]] = {}
for path in templates:
    by_name.setdefault(path.name, []).append(path)
for name, paths in sorted(by_name.items()):
    if len(paths) < 2:
        continue
    schedules = [plistlib.loads(p.read_bytes()).get("StartCalendarInterval") for p in paths]
    check(f"{name}: every copy carries the same calendar schedule",
          all(s == schedules[0] for s in schedules), schedules)

print(f"launchd-calendar-selftest: {sum(RESULTS)}/{len(RESULTS)} passed")
sys.exit(0 if all(RESULTS) else 1)
