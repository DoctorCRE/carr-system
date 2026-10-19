#!/usr/bin/env python3
"""Render a completed restore-rehearse transcript; read no backup or credential."""

import re
import sys


def report(transcript: str) -> tuple[bool, str]:
    def field(pattern: str) -> str | None:
        matches = re.findall(pattern, transcript, re.M)
        return matches[0] if len(matches) == 1 else None

    verdicts = re.findall(r"^\s*RESTORE REHEARSAL: (.*)$", transcript, re.M)
    dump = field(r"^\s*ok\s+(?:newest|selected) dump: (\S+)")
    pct = field(r"^\s*restored total is (\d+(?:\.\d+)?)% of production")
    branch = field(r"^\s*ok\s+created restore-rehearse-\S+ \(([^\s()]+)\)$")
    deleted = re.findall(r"^\s*teardown: branch (\S+) deleted$", transcript, re.M)
    failures = [line for line in transcript.splitlines()
                if re.search(r"\b(?:FAIL|MISMATCH)\b|^REHEARSAL FAILED:|teardown: (?:KEEPING|COULD NOT DELETE)", line)]
    warnings = [line for line in transcript.splitlines()
                if re.search(r"\bWARN\b|EVIDENCE WARNING:|^\s+(?:was NOT recorded to ops.run|skipped whenever)", line)]
    problems = []
    if verdicts != ["PASS"]:
        problems.append("expected exactly one terminal PASS verdict")
    if not (dump and pct and branch):
        problems.append("dump, percentage or created branch missing or ambiguous")
    if not branch or deleted != [branch]:
        problems.append("branch deletion not confirmed")
    if not problems and not failures and dump:
        name = dump.rsplit("/", 1)[-1]
        return True, "\n".join([f"RESTORE REHEARSAL: PASS | {name} | restored {pct}% "
                                f"of production | branch {branch} deleted"] + warnings)
    return False, ("RESTORE REHEARSAL: FAIL\nRestore rehearsal is not currently provable.\n"
                   + "\n".join(failures + problems + warnings))


if __name__ == "__main__":
    passed, output = report(sys.stdin.read())
    print(output)
    sys.exit(0 if passed else 1)
