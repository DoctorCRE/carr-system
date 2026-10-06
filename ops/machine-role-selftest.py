#!/usr/bin/env python3
"""Precedence proof for lib/machine_role.py: marker first, git email fallback."""
import os
import sys
import tempfile

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO)
from lib import machine_role  # noqa: E402

OWNER = machine_role.owner_email(REPO)


def run(label, home_role, email, expected):
    with tempfile.TemporaryDirectory(prefix="machine-role-") as home:
        if home_role is not None:
            d = os.path.join(home, ".config", "carr")
            os.makedirs(d)
            with open(os.path.join(d, "machine-role.json"), "w", encoding="utf-8") as fh:
                fh.write(home_role)
        got = machine_role.is_primary(REPO, git_email=email, home=home)
    ok = got is expected
    print(f"{'PASS' if ok else 'FAIL'}  {label}" + ("" if ok else f": got {got}"))
    return ok


def main():
    if not OWNER:
        print("FAIL  OWNER_EMAIL unreadable from ops/githooks/pre-push")
        return 1
    cases = [
        run("no marker, owner email -> primary (old behaviour)", None, OWNER, True),
        run("no marker, other email -> secondary", None, "x@example.com", False),
        run("marked secondary outranks owner email", '{"role": "secondary"}', OWNER, False),
        run("marked primary outranks non-owner email", '{"role": "primary"}', "x@example.com", True),
        run("unknown role fails closed", '{"role": "boss"}', OWNER, False),
        run("unparseable marker fails closed", "not json", OWNER, False),
    ]
    with tempfile.TemporaryDirectory(prefix="machine-role-") as home:
        path = machine_role.write_marker("secondary", home=home)
        ok = (machine_role.read_marker(home) == "secondary"
              and oct(os.stat(path).st_mode & 0o777) == "0o600")
        print(f"{'PASS' if ok else 'FAIL'}  write_marker round-trips at 0600")
        cases.append(ok)
    print(f"machine-role-selftest: {sum(cases)}/{len(cases)} passed")
    return 0 if all(cases) else 1


if __name__ == "__main__":
    raise SystemExit(main())
