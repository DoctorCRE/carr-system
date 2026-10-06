#!/usr/bin/env python3
"""Fail a build bundle that cites evidence it never opened.

WHY THIS EXISTS. On 2026-09-02 all three A03 r2 capability-map builds failed
independent review on ONE shape: rationales citing artifacts the build's own
trace shows it never opened. One build opened 2 of 11 required prior-revision
artifacts; another never opened 8 of 12 and listed two sibling findings files
without reading them; a third attached the same basis to 35 rows, each naming
capture files it had only listed the directory of. All three self-audits
reported that check green, because each checked for expected PHRASES in a
basis string rather than whether the cited file was ever read.

Three independent builds, one shape, three green self-audits: that is a design
problem, not three lapses (rule 1c21263c). A check is better than a rule
because it fires without anyone remembering. Defect 0b07e57f-56cf-4cf9-a483-
72663dfb651f.

WHAT IT DOES. Extracts every evidence path cited in a bundle's artifacts,
extracts every path the trace shows the build actually opening, and fails on
the difference.

    ops/citation-trace-check.py <bundle-dir> <trace.jsonl>

Exit 0 when every cited evidence path was opened, 1 when any was not, 2 on a
usage or input error.

WHAT IT DOES NOT CATCH, stated because a check whose limits are unknown gets
trusted past them. It matches LITERAL paths under the evidence root. It does
not see a prose claim ("the r1 graph and the prose were read"), a citation
written as a bare filename rather than a path, or a glob. Run against the
three bundles that failed review on 2026-09-02 it catches two of the citation
failures the reviewers found and misses the prose-shaped ones. It is a FLOOR,
not a substitute for reading the trace — the trace is still the verification
surface, and this only makes the cheapest slice of that reading automatic.
"""
import json
import os
import re
import sys

# Only paths under these roots are treated as EVIDENCE citations. A prose
# mention of a doctrine name or a URL is not a file this build had to open.
# Scoped to the program's own evidence tree ON PURPOSE. A first version also
# covered mcp-server/ and reported 21, 59 and 20 unopened source files across
# the three bundles — all false. Those builds DID read that source, through
# `git show "$ANCHOR:$path"` inside loops where the path is constructed at
# runtime and never appears literally in the command. A check that cries wolf
# on every bulk source extraction is a check nobody runs. Every citation
# failure the three reviews actually found was an evidence artifact under this
# one root, cited by its literal path.
EVIDENCE_ROOTS = ("out/doctorcre-program/",)

PATH_RE = re.compile(r'(?<![\w/.-])((?:' + '|'.join(
    r.replace('/', r'/') for r in EVIDENCE_ROOTS
) + r')[A-Za-z0-9._/-]+\.[A-Za-z0-9]{1,6})')

# A trace entry opens a path if a read-shaped tool names it, or a shell
# command reads it. Anything ambiguous counts as OPENED: this check must not
# manufacture a failure, only catch one nobody looked for.
READ_TOOLS = {"Read", "NotebookRead"}
# Commands that put a file's CONTENT in front of the build. Deliberately
# excludes ls, find, stat, shasum and wc: hashing or listing a file is not
# reading it, and a first version that counted them certified all three
# known-bad bundles as clean. That false green is the reason this list is
# explicit rather than a catch-all.
SHELL_READ_RE = re.compile(
    r'\b(?:cat|head|tail|sed|awk|less|nl|grep|rg|jq|'
    r'python3?|node|git\s+show|git\s+cat-file)\b')
NON_READ_RE = re.compile(r'\b(?:ls|find|stat|shasum|sha256sum|wc|du)\b')


def cited_paths(bundle):
    """Every evidence path named in the bundle's own artifacts."""
    out = {}
    for root, dirs, files in os.walk(bundle):
        dirs[:] = [d for d in dirs if d != "__pycache__"]
        for name in files:
            if not name.endswith((".md", ".json", ".txt", ".csv")):
                continue
            fp = os.path.join(root, name)
            try:
                text = open(fp, encoding="utf-8", errors="replace").read()
            except OSError:
                continue
            for m in PATH_RE.finditer(text):
                out.setdefault(m.group(1), set()).add(
                    os.path.relpath(fp, bundle))
    return out


def opened_paths(trace):
    """Every path the trace shows the build actually reading."""
    seen = set()

    def note(s):
        for m in PATH_RE.finditer(s or ""):
            seen.add(m.group(1))

    def walk(x):
        if isinstance(x, dict):
            name, inp = x.get("name"), x.get("input")
            if isinstance(inp, dict):
                if name in READ_TOOLS:
                    note(str(inp.get("file_path", "")))
                cmd = inp.get("command")
                if isinstance(cmd, str) and SHELL_READ_RE.search(cmd):
                    # A command that both lists and reads gets its listing
                    # segments dropped, so `ls dir && cat dir/one.md` credits
                    # only the file actually read.
                    for seg in re.split(r'[;&|]{1,2}|\n', cmd):
                        if SHELL_READ_RE.search(seg) and not NON_READ_RE.search(seg):
                            note(seg)
            for v in x.values():
                walk(v)
        elif isinstance(x, list):
            for v in x:
                walk(v)

    with open(trace, encoding="utf-8", errors="replace") as fh:
        for line in fh:
            try:
                walk(json.loads(line))
            except ValueError:
                continue
    return seen


def main(argv):
    if len(argv) != 3:
        print(__doc__.strip().splitlines()[-6], file=sys.stderr)
        print("usage: citation-trace-check.py <bundle-dir> <trace.jsonl>",
              file=sys.stderr)
        return 2
    bundle, trace = argv[1], argv[2]
    if not os.path.isdir(bundle):
        print("not a directory: " + bundle, file=sys.stderr)
        return 2
    if not os.path.isfile(trace):
        print("not a file: " + trace, file=sys.stderr)
        return 2

    cited = cited_paths(bundle)
    opened = opened_paths(trace)
    # A path the bundle produced is its own output, never a citation it owed
    # a read for.
    # The bundle's own outputs are not citations it owed a read for. Matched
    # on the FULL relative path — an earlier version matched on BASENAME,
    # which silently excused every citation of a sibling bundle's
    # findings.md, unknowns.md or self-audit.md because this bundle happens
    # to contain files by those names. That hole hid every real failure.
    # Normalise the bundle to its evidence-root-relative form. An earlier
    # version used relpath against the CURRENT DIRECTORY, so the same bundle
    # reported 0 unopened when addressed by a relative path and 58 when
    # addressed by an absolute one — its own outputs stopped being recognised
    # as its own. Path form must not change a verdict.
    norm = os.path.normpath(os.path.abspath(bundle))
    bundle_rel = None
    for r in EVIDENCE_ROOTS:
        i = norm.find("/" + r)
        if i != -1:
            bundle_rel = norm[i + 1:].rstrip("/") + "/"
            break
    if bundle_rel is None:
        bundle_rel = os.path.relpath(bundle, ".").rstrip("/") + "/"

    unopened = {p: srcs for p, srcs in cited.items()
                if p not in opened and not p.startswith(bundle_rel)}

    print("bundle:      %s" % bundle)
    print("trace:       %s" % trace)
    print("cited:       %d evidence path(s)" % len(cited))
    print("opened:      %d path(s) in the trace" % len(opened))
    print("UNOPENED:    %d cited path(s) the trace does not show being read"
          % len(unopened))
    for p in sorted(unopened):
        srcs = sorted(unopened[p])
        print("  %s" % p)
        print("      cited in: %s%s" % (", ".join(srcs[:3]),
                                        "" if len(srcs) <= 3 else
                                        " (+%d more)" % (len(srcs) - 3)))
    if unopened:
        print("\nFAIL — the bundle cites evidence its trace does not show it "
              "opening.")
        return 1
    print("\nOK — every cited evidence path appears as a read in the trace.")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
