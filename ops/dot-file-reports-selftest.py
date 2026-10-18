#!/usr/bin/env python3
"""Offline completeness regressions for bin/dot-file-reports."""

from __future__ import annotations

import importlib.util
from importlib.machinery import SourceFileLoader
import os
from pathlib import Path
import tempfile


REPO = Path(__file__).resolve().parent.parent
REAL_REPORT = Path(os.environ.get(
    "DOT_REAL_REPORT",
    REPO / "out/orch/dot/reports/10081500-E2E-work-hierarchy-design-export.txt",
))


def load_reporter():
    path = REPO / "bin/dot-file-reports"
    spec = importlib.util.spec_from_loader(
        "dot_file_reports", SourceFileLoader("dot_file_reports", str(path))
    )
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def representative_three_part_report() -> str:
    return """E2E-work-hierarchy-design-export — reconstruction, part 1 of 3
first segment

E2E-work-hierarchy-design-export — part 2 of 3
second segment

E2E-work-hierarchy-design-export — part 3 of 3
third segment
DOT-REPORT-END E2E-work-hierarchy-design-export
"""


def assert_passes(reporter, path: Path) -> None:
    text, snapshot = reporter.completed_snapshot(path)
    assert text
    assert snapshot.st_size == path.stat().st_size


def assert_incomplete(reporter, path: Path) -> None:
    try:
        reporter.completed_snapshot(path)
    except reporter.FilingError as error:
        assert str(error) == "report publication incomplete; final part required"
    else:
        raise AssertionError(f"incomplete multipart report passed: {path}")


def main() -> int:
    reporter = load_reporter()
    passed = 0
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)

        complete = REAL_REPORT
        if not complete.is_file():
            complete = root / "10081500-E2E-work-hierarchy-design-export.txt"
            complete.write_text(representative_three_part_report(), encoding="utf-8")
        assert_passes(reporter, complete)
        passed += 1

        missing_final = root / "missing-part-3.txt"
        missing_final.write_text(
            "Report — part 1 of 3\none\nReport — part 2 of 3\ntwo\nDOT-REPORT-END\n",
            encoding="utf-8",
        )
        assert_incomplete(reporter, missing_final)
        passed += 1

        missing_middle = root / "missing-part-2.txt"
        missing_middle.write_text(
            "Report — part 1 of 3\none\nReport — part 3 of 3\nthree\nDOT-REPORT-END\n",
            encoding="utf-8",
        )
        assert_incomplete(reporter, missing_middle)
        passed += 1

        single = root / "single-part.txt"
        single.write_text(
            "Report — part 1 of 1\ncomplete\nDOT-REPORT-END\n", encoding="utf-8"
        )
        assert_passes(reporter, single)
        passed += 1

    print(f"dot-file-reports-selftest: {passed}/4 passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
