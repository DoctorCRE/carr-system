#!/usr/bin/env python3
"""untracked-anomaly-report.py — what is sitting untracked in the canonical
checkout that nobody has ruled on. A WEEKLY-REVIEW ARTIFACT. It pages nobody.

R02 PROPOSED (Repo Hygiene Program, WR-000040). NOT INSTALLED.

WHY IT EXISTS. canonical-edit-gate.py's allowance 2 lets any session write a
brand-new UNTRACKED file anywhere in the canonical checkout, and that allowance
is kept, deliberately: receipts, run records and out/ are load-bearing, and a
gate that refuses them makes its own remedy unusable. But "allowed" was doing
double duty as "unwatched". On 2026-09-01 the canonical checkout held 40
untracked-nonignored paths — 30 of them temp roots from a single selftest run
on 2026-08-24 that nobody noticed for eight days, plus two contract fixtures,
three ops scripts, a proposal text file and four directories. None of that is an
emergency. All of it is invisible.

The answer to an allowance that hides things is VISIBILITY, NOT REFUSAL. This
report is the compensating control for keeping allowance 2, and its whole design
follows from that:

NON-PAGING, AND THAT IS A CONTRACT, NOT A DEFAULT.
  - it always exits 0, including when it finds anomalies. A non-zero exit is
    how a scheduled job turns into an alarm, so it never returns one.
  - it opens no incident, calls no record-layer verb, sends no notification,
    writes to no alarm channel and prints nothing to stderr on the normal path.
  - it writes an artifact and stops. A human reads it during weekly review.
  - ops/untracked-anomaly-report-selftest.py asserts each of those directly,
    because "non-paging" is exactly the property that erodes the first time
    somebody adds "just one" alert.
  This is deliberately WEAKER than R04's canonical-dirt alarm, which is PLANNED
  to page. R04 watches TRACKED dirt — an edit to shared history, which is the
  hazard. This watches UNTRACKED accumulation — mess, which is a review item.
  Wiring this one to page would train people to ignore the one that matters.
  NOTE R04 IS NOT BUILT: the accepted dependency graph puts it after R08's
  worktree-versus-clone ruling, which blocks R04's packet compilation. So the
  comparison above is to a planned control, and this report is currently the
  only thing watching untracked accumulation at all.

WHAT COUNTS AS AN ANOMALY: a path git reports as untracked-and-not-ignored in
the canonical checkout, that does not sit under an approved root in
ops/config/untracked-approved-roots.json. Approved roots come in two lists —
structural plumbing (out/, .venv/, worktree roots) and roots a settlement has
actually RULED. The ruled list starts empty on purpose: an advisory disposition
in the R01 worksheet is not a ruling, and quietly promoting one here would
delete a debris family from review without anyone deciding to.

Usage:
    python3 ops/untracked-anomaly-report.py            # write the artifact
    python3 ops/untracked-anomaly-report.py --json     # artifact + JSON to stdout
    python3 ops/untracked-anomaly-report.py --print    # artifact + markdown to stdout
"""
import argparse
import json
import os
import subprocess
import sys
from collections import defaultdict
from datetime import datetime, timezone

REPO = os.path.realpath(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
CONFIG = os.path.join(REPO, "ops", "config", "untracked-approved-roots.json")
OUT_MD = os.path.join(REPO, "out", "untracked-anomaly-report.md")
OUT_JSON = os.path.join(REPO, "out", "untracked-anomaly-report.json")


def now():
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def approved_roots(config_path):
    """(roots, detail). Missing or malformed config means NO roots are approved,
    so everything untracked is reported. That is the safe direction for a report
    that only ever reports: the failure mode is a noisier artifact, never a
    silently emptied one."""
    try:
        with open(config_path) as fh:
            cfg = json.load(fh)
    except Exception as exc:
        return [], {"config_error": str(exc)}
    roots, detail = [], {"structural": [], "ruled": []}
    for kind in ("structural", "ruled"):
        for entry in cfg.get(kind) or []:
            root = entry.get("root") if isinstance(entry, dict) else entry
            if not root:
                continue
            roots.append(root.rstrip("/") + "/")
            detail[kind].append(entry)
    return roots, detail


def untracked_paths(repo):
    """Untracked-and-not-ignored paths, straight from git.

    GIT_OPTIONAL_LOCKS=0 so a read-only report never takes the index lock out
    from under a session that is actually working.
    """
    env = dict(os.environ, GIT_OPTIONAL_LOCKS="0")
    proc = subprocess.run(
        ["git", "-C", repo, "status", "--porcelain=v1", "--untracked-files=normal"],
        capture_output=True, text=True, timeout=120, env=env)
    if proc.returncode != 0:
        return None, proc.stderr.strip()
    paths = []
    for line in proc.stdout.splitlines():
        if line.startswith("?? "):
            paths.append(line[3:].strip().strip('"'))
    return sorted(paths), None


def classify(paths, roots):
    approved, anomalies = [], []
    for p in paths:
        hit = next((r for r in roots if p == r.rstrip("/") or p.startswith(r)), None)
        (approved if hit else anomalies).append(
            {"path": p, "approved_under": hit} if hit else {"path": p})
    return approved, anomalies


def describe(repo, path):
    full = os.path.join(repo, path)
    try:
        st = os.stat(full)
        mtime = datetime.fromtimestamp(st.st_mtime, timezone.utc).strftime(
            "%Y-%m-%dT%H:%M:%SZ")
    except OSError:
        mtime = None
    return {"path": path,
            "kind": "directory" if path.endswith("/") or os.path.isdir(full) else "file",
            "mtime_utc": mtime,
            "top_level_root": path.split("/", 1)[0] + ("/" if "/" in path else "")}


def render(doc):
    lines = [
        "# Untracked anomaly report",
        "",
        f"Canonical checkout `{doc['repo']}`, generated {doc['generated_utc']}.",
        "",
        "Untracked-but-not-ignored paths that sit outside the approved roots in "
        "`ops/config/untracked-approved-roots.json`. **This is a weekly-review "
        "artifact. It pages nobody, opens no incident and blocks nothing.** "
        "Tracked dirt is a different and more serious thing; the R04 "
        "canonical-dirt alarm is PLANNED to watch for that and is NOT BUILT "
        "YET, so nothing watches tracked dirt today.",
        "",
        f"- untracked-nonignored paths seen: **{doc['counts']['untracked_total']}**",
        f"- under an approved root: **{doc['counts']['approved']}**",
        f"- anomalies for review: **{doc['counts']['anomalies']}**",
        "",
    ]
    if doc.get("error"):
        lines += [f"> Report could not read git: `{doc['error']}`. No conclusion "
                  f"is drawn from an empty list.", ""]
    if not doc["anomalies"]:
        lines += ["Nothing outside the approved roots. Nothing to do.", ""]
    else:
        groups = defaultdict(list)
        for row in doc["anomalies"]:
            groups[row["top_level_root"]].append(row)
        lines += ["| Top-level root | Paths | Oldest mtime (UTC) | Kind |",
                  "|---|---:|---|---|"]
        for root in sorted(groups):
            rows = groups[root]
            mtimes = [r["mtime_utc"] for r in rows if r["mtime_utc"]]
            kinds = sorted({r["kind"] for r in rows})
            lines.append(f"| `{root}` | {len(rows)} | "
                         f"{min(mtimes) if mtimes else '—'} | {', '.join(kinds)} |")
        lines += ["", "## Every anomaly path", ""]
        for row in doc["anomalies"]:
            lines.append(f"- `{row['path']}` — {row['kind']}, "
                         f"mtime {row['mtime_utc'] or '—'}")
        lines.append("")
    lines += ["## Approved roots applied", ""]
    for kind in ("structural", "ruled"):
        entries = doc["approved_roots_detail"].get(kind) or []
        lines.append(f"**{kind}** ({len(entries)})")
        if not entries:
            lines += ["", "- none", ""]
            continue
        lines.append("")
        for e in entries:
            root = e.get("root") if isinstance(e, dict) else e
            why = e.get("why", "") if isinstance(e, dict) else ""
            lines.append(f"- `{root}` — {why}")
        lines.append("")
    return "\n".join(lines)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--repo", default=REPO)
    ap.add_argument("--config", default=None)
    ap.add_argument("--out-md", default=None)
    ap.add_argument("--out-json", default=None)
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--print", dest="do_print", action="store_true")
    args = ap.parse_args()

    repo = os.path.realpath(args.repo)
    config = args.config or os.path.join(repo, "ops", "config",
                                         "untracked-approved-roots.json")
    out_md = args.out_md or os.path.join(repo, "out", "untracked-anomaly-report.md")
    out_json = args.out_json or os.path.join(repo, "out",
                                             "untracked-anomaly-report.json")

    roots, detail = approved_roots(config)
    paths, error = untracked_paths(repo)
    paths = paths or []
    approved, anomalies = classify(paths, roots)
    doc = {
        "generated_utc": now(),
        "repo": repo,
        "paging": "none — this report never alarms, never opens an incident and "
                  "always exits 0",
        "error": error,
        "approved_roots": roots,
        "approved_roots_detail": detail,
        "counts": {"untracked_total": len(paths), "approved": len(approved),
                   "anomalies": len(anomalies)},
        "approved_paths": approved,
        "anomalies": [describe(repo, a["path"]) for a in anomalies],
    }
    markdown = render(doc)

    for dest, body in ((out_json, json.dumps(doc, indent=2)), (out_md, markdown)):
        try:
            os.makedirs(os.path.dirname(dest), exist_ok=True)
            with open(dest, "w") as fh:
                fh.write(body + ("\n" if not body.endswith("\n") else ""))
        except Exception:
            # Even failing to write the artifact is not worth an alarm.
            pass

    if args.json:
        print(json.dumps(doc, indent=2))
    elif args.do_print:
        print(markdown)
    else:
        print(f"untracked-anomaly-report: {doc['counts']['anomalies']} anomalies "
              f"of {doc['counts']['untracked_total']} untracked paths -> {out_md}")

    # ALWAYS 0. See the module docstring: a non-zero exit is how a scheduled
    # job becomes an alarm, and this one is not an alarm.
    return 0


if __name__ == "__main__":
    sys.exit(main())
