#!/usr/bin/env python3
"""Enumerate the exact sibling paths a build seat may read, for its brief.

WHY THIS EXISTS. Every A03 packet forbids scoping a command at the shared
program directory. Every builder nonetheless has to find its sibling
artifacts, and the packet offers no permitted way to do that — so on
2026-09-02 the prohibition was broken three times in one round, by two
builders and by the controller setting the round up (defect b7936d5d). Three
occurrences of one shape is the instruction failing, not three careless
seats, and the outgoing controller's handoff had already named the remedy:
"the fix is a positive instruction, not another prohibition."

This is that positive instruction. THE CONTROLLER runs it before dispatch and
pastes the output into the brief, so the seat is handed the exact paths and
never needs to look. One sanctioned listing by the party who owns the
directory replaces one ad-hoc listing per seat.

    ops/doctorcre-sibling-manifest.py <seed>        e.g. a03a

Prints a brief-ready block: the seed's own prior-revision bundle file by file,
the upstream walkthrough bundles it is entitled to, and its packet. It does
NOT decide what the packet admits — it lists what exists, by exact path, and
the packet's Field 7 still governs which of them must be opened.
"""
import os
import sys

ROOT = os.environ.get("CARR_ROOT", os.path.join(os.path.expanduser("~"), "carr-system"))
PROG = os.path.join(ROOT, "out", "doctorcre-program")

# Upstream bundles every capability-map seed is entitled to read, and the one
# line about each that a seat needs before opening it.
UPSTREAM = [
    ("a02a-r5", "route/entrypoint registry — the primary source for the row "
                "inventory. Its Unknown U-01 is QUARANTINED: it says a storage "
                "bucket is referenced nowhere at the anchor, and five "
                "references exist. Re-derive, never inherit."),
    ("a02b-r6-build", "desktop walkthrough baseline. NOT usable for any "
                      "completeness, total, rate, coverage or absence claim "
                      "from its request log."),
    ("a02c-v1", "operations walkthrough — primary evidence for the Calls "
                "domain, no stated limits."),
    ("a02d-v1", "PARTIAL baseline. Five captures are excluded by name and are "
                "not evidence of what they name. Read the governing decision "
                "before the directory is touched."),
]


def listing(rel):
    d = os.path.join(PROG, rel)
    if not os.path.isdir(d):
        return None
    out = []
    for root, dirs, files in os.walk(d):
        dirs[:] = [x for x in dirs if x not in {"__pycache__", "node_modules"}]
        for f in sorted(files):
            p = os.path.relpath(os.path.join(root, f), ROOT)
            out.append(p)
    return sorted(out)


def main(argv):
    if len(argv) != 2:
        print("usage: doctorcre-sibling-manifest.py <seed>", file=sys.stderr)
        return 2
    seed = argv[1]
    print("## THE EXACT PATHS YOU MAY READ — you never need to list a directory")
    print()
    print("This manifest is the positive instruction that replaces the "
          "prohibition on")
    print("scoping a command at the shared program directory. Every path below "
          "is")
    print("named in full. If something you need is not here, say so and STOP "
          "rather")
    print("than going to look for it — a missing path is the controller's "
          "error to fix.")
    print()

    prior = "%s-v1" % seed
    files = listing(prior)
    print("### Your own prior revision — %s" % prior)
    if files is None:
        print("  (none on disk)")
    else:
        print("  %d file(s). What Field 7 requires of these, the packet says; "
              "this is the list." % len(files))
        for p in files:
            print("    " + p)
    print()

    for rel, note in UPSTREAM:
        files = listing(rel)
        print("### %s — %s" % (rel, note))
        if files is None:
            print("  (not on disk)")
        else:
            for p in files:
                print("    " + p)
        print()

    for rev in ("r2", "r3"):
        p = os.path.join("out/doctorcre-program/packet-drafts",
                         "packet-%s-%s-draft.md" % (seed.upper().replace("A03", "A03"), rev))
        if os.path.isfile(os.path.join(ROOT, p)):
            print("### Your packet")
            print("    " + p)
            print()
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
