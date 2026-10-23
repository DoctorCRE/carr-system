#!/usr/bin/env python3
"""Calendar capture fires on workdays after template token substitution."""
import datetime
from pathlib import Path
import plistlib
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from lib.launchd_calendar import render_xml


class CalendarWeekdayTests(unittest.TestCase):
    def test_shared_renderer_preserves_weekday_constraints(self):
        entries = [{"Weekday": 1, "Hour": 7, "Minute": 20},
                   {"Weekday": 5, "Hour": 7, "Minute": 20}]
        rendered = "<plist><dict>" + render_xml(entries, "  ") + "</dict></plist>"
        self.assertEqual(plistlib.loads(rendered.encode())["StartCalendarInterval"], entries)

    def test_capture_fires_monday_through_friday_at_0720(self):
        template = (ROOT / "ops/launchd/com.carr.calendar-eventkit.plist").read_text()
        rendered = template.replace("{{REPO}}", "/synthetic/carr-system")
        job = plistlib.loads(rendered.encode())
        schedule = job["StartCalendarInterval"]
        week = [datetime.date(2026, 10, day) for day in range(19, 26)]
        fired = [day.strftime("%A") for day in week
                 if any(entry == {"Weekday": day.isoweekday() % 7,
                                  "Hour": 7, "Minute": 20} for entry in schedule)]
        self.assertEqual(fired, ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday"])
        self.assertEqual(len(schedule), 5)
        self.assertFalse(job["RunAtLoad"])
        self.assertEqual(job["ProgramArguments"], [
            "/bin/zsh", "/synthetic/carr-system/bin/run-scheduled.sh",
            "calendar-eventkit", "launchd.run", "/bin/sh",
            "/synthetic/carr-system/bin/calendar-eventkit-capture.sh", "--days", "7"])


if __name__ == "__main__":
    unittest.main()
