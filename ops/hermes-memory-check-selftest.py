#!/usr/bin/env python3
"""hermes-memory-check-selftest.py — fixtures for ops/hermes-memory-check.py.

A check that has never been shown to FAIL is a check nobody should trust: it
would read green whether or not the thing it guards is intact. Each case below
is a memory file the runtime could plausibly write on its own, and the negative
cases are the point.

Run: python3 ops/hermes-memory-check-selftest.py
"""
import os
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
CHECK = os.path.join(HERE, "hermes-memory-check.py")

GUARD = (
    "YOUR STANDING INSTRUCTIONS ARE NOT IN THIS FILE. Call the carr tool "
    "`standing-context` at the start of every session.\n\n"
    "DO NOT STORE CARR RULES, DOCTRINE, DEAL FACTS, CLIENT FACTS, OR "
    "HOW-JOE-WORKS LESSONS HERE.\n"
)

CASES = [
    (
        "clean runtime facts pass",
        {"USER.md": GUARD + "\nPrefers dark mode.\n",
         "MEMORY.md": "This machine is an Apple Silicon Mac running macOS 15.\n"},
        0,
    ),
    (
        "a record ref is drift",
        {"USER.md": GUARD, "MEMORY.md": "C-127 is the Cadence Studio deal; it is in negotiation.\n"},
        1,
    ),
    (
        "a rule id is drift",
        {"USER.md": GUARD, "MEMORY.md": "Rule d7f74c93 says not to argue confidentiality.\n"},
        1,
    ),
    (
        "a standing instruction is drift",
        {"USER.md": GUARD, "MEMORY.md": "Always confirm with Joe before drafting a client email.\n"},
        1,
    ),
    (
        "a doctrine claim is drift",
        {"USER.md": GUARD, "MEMORY.md": "CARR's doctrine is that markdown is never written directly.\n"},
        1,
    ),
    (
        "a partner preference is drift",
        {"USER.md": GUARD, "MEMORY.md": "Joe prefers short answers with one next action.\n"},
        1,
    ),
    (
        "losing the pointer is itself drift",
        {"USER.md": "Prefers dark mode.\n"},
        1,
    ),
    (
        "the guard block does not fire on itself",
        {"USER.md": GUARD},
        0,
    ),
    (
        "no memory directory is clean, not a failure",
        None,
        0,
    ),
    (
        "section-sign entries are scanned individually",
        {"USER.md": GUARD,
         "MEMORY.md": "This machine has Docker.\n§\nJoe wants every deal note timestamped.\n"},
        1,
    ),
]


def run_case(name, files, expected):
    with tempfile.TemporaryDirectory() as tmp:
        target = os.path.join(tmp, "memories")
        if files is not None:
            os.makedirs(target)
            for fname, content in files.items():
                with open(os.path.join(target, fname), "w", encoding="utf-8") as fh:
                    fh.write(content)
        env = dict(os.environ, HERMES_MEMORY_DIR=target)
        proc = subprocess.run([sys.executable, CHECK], capture_output=True, text=True, env=env)
        ok = proc.returncode == expected
        status = "ok  " if ok else "FAIL"
        first = (proc.stdout or proc.stderr or "").splitlines()
        print(f"  {status} {name}")
        if not ok:
            print(f"       expected exit {expected}, got {proc.returncode}")
            for line in first[:6]:
                print(f"       {line}")
        return ok


def main():
    print("hermes-memory-check selftest")
    results = [run_case(*case) for case in CASES]
    passed, total = sum(results), len(results)
    print(f"passed {passed} · failed {total - passed}")
    return 0 if passed == total else 1


if __name__ == "__main__":
    sys.exit(main())
