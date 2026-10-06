#!/usr/bin/env python3
"""
corpus-sync.py — the doctrine corpus mirror (ORDER 30, Wave 4 phase 1, 2026-07-31;
CORPUS FLIP 2026-08-06 — see below).

WHAT THIS IS. `corpus/` holds a byte-for-byte copy of the vault's DOCTRINE tier
so that rules and craft gain what records already have: version history, diffs,
and a way to answer "when did we decide that, and what did it say before?"

THE FLIP (2026-08-06). Both partners confirmed: the doctrine tier is now
**GIT-CANONICAL**. corpus/ under this repo is the source of truth; the Drive
(and drive:/home:-prefixed) copies are RENDERS, kept current by pushing git's
content out to them. This inverts the original phase-1 design, which ran the
other way (Drive canonical, mirror read-only). That original direction survives
only as `--sync`, now the CONFLICT-RESOLUTION path, not the normal flow.

So the two drift directions now mean opposite things from phase 1:
  * git (corpus/) differs from what was last synced = NORMAL. Doctrine changed
    in git; push it out with `--push`.
  * the source-side (Drive/vault/home) copy differs from what was last synced
    = A CONFLICT. Since git is canonical, a hand-edit on the Drive side is a
    violation — `--push` will not silently overwrite it. It prints a loud
    CONFLICT line naming the file and skips it; a human resolves by folding
    the edit into git (or, for the rare deliberate case, running `--sync` to
    pull it into git first).

Drift is detected by CONTENT HASH against the manifest, never by mtime alone:
Google Drive File Stream rewrites mtimes on its own schedule, so an mtime test
would cry wolf nightly and, worse, could miss a real edit that preserved mtime.

Usage:
  python3 tools/corpus-sync.py               # check only; exit 0 clean / 1 to-push / 2 conflict
  python3 tools/corpus-sync.py --push        # push git -> source-side (the normal flow, post-flip)
  python3 tools/corpus-sync.py --sync        # CONFLICT-RESOLUTION path: pull source-side -> git
                                              # (refuses to clobber a git-side edit; --force discards it)
  python3 tools/corpus-sync.py --sync --force  # ...and DISCARD git-side edits, restoring from the Drive
  python3 tools/corpus-sync.py --prune       # with --sync: delete mirror files no longer in the set
  python3 tools/corpus-sync.py --json        # machine-readable status

The set of files that ARE the corpus lives in corpus/corpus-set.tsv, one row per
file with the reason it counts as doctrine. Adding doctrine = adding a row.
No network, no credential, no database.
"""
import hashlib
import json
import os
import shutil
import sys
import time

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CORPUS = os.path.join(REPO, "corpus")
SET_FILE = os.path.join(CORPUS, "corpus-set.tsv")
MANIFEST = os.path.join(CORPUS, "manifest.json")

VAULT = os.environ.get("CARR_VAULT") or os.path.join(
    os.path.expanduser("~"), "Library/CloudStorage/GoogleDrive-joe.bookout.carr.us@gmail.com/My Drive/CARR AI")

# Stop rules from the order: this is a TEXT corpus.
MAX_BYTES = 5 * 1024 * 1024
TEXT_EXT = {".md", ".txt", ".tsv", ".csv", ".json", ".sql", ".py", ".js", ".html", ".yml", ".yaml"}

# CORPUS FLIP, 2026-08-06: git is canonical now. Stamped into every manifest this
# tool writes (from --push AND from --sync) so the file itself states which way
# the arrow points, no matter which command last touched it.
CANONICAL_NOTE = ("GIT. This repo's corpus/ is the doctrine tier's source of truth; the "
                   "Drive/vault/home copies are renders kept current by `--push`. Flipped "
                   "2026-08-06 (was Drive-canonical under ORDER 30 phase 1). `--sync` remains "
                   "only as the conflict-resolution path, pulling a source-side change INTO git.")


def vault_reachable():
    """Cheap reachability probe for the SKIP-not-FAIL contract: if the Drive mount
    is down, VAULT (a path under it) won't resolve as a directory."""
    return os.path.isdir(VAULT)


# The Drive-rooted classes are RETIRED, not merely unmounted (cutoff 2026-08-19,
# and Joe's approval of Drive retirement across the dependency classes). The
# mount still resolves on this Mac, so a reachability probe says yes and would
# happily write doctrine renders back into a vault the cutoff turned off. Being
# able to write somewhere is not permission to.
RETIRED_ROOTS = ("vault", "drive")


def row_root_writable(rel):
    """(may this row be pushed now, why not) — asked per row, not once per run.

    WHY PER ROW (2026-08-23). --push refused the whole run unless the vault
    resolved. That was right when every row lived in the vault and wrong from the
    moment the set gained `home:` rows, which address ~/.claude/skills on this Mac
    and need no Drive at all. Twelve of the fifty-four rows are those. So a
    precondition written for one root switched off a dozen rows that never shared
    it — the same shape as the portability mirror the day before, where a step
    with a live half and a dead half failed whole and the live half went quiet
    without anyone deciding it should.

    WHY RETIREMENT AND NOT REACHABILITY DECIDES THE DRIVE ROWS. The vault is
    still mounted here. A reachability probe therefore returns True and the push
    would rewrite retired doctrine renders into it, which is precisely what the
    cutoff stopped and what the nightly acceptance requires stay stopped. They
    move only inside an explicitly opened recovery envelope — the same
    CARR_DRIVE_RECOVERY flag the routine boundary uses to decide whether a Drive
    root may cross into a child at all.
    """
    prefix = rel.partition(":")[0] if ":" in rel else "vault"
    if prefix not in ROOTS and prefix != "vault":
        prefix = "vault"
    if prefix in RETIRED_ROOTS and os.environ.get("CARR_DRIVE_RECOVERY") != "1":
        return False, f"{prefix} root retired 2026-08-19; open a recovery envelope to push it"
    root = VAULT if prefix == "vault" else ROOTS[prefix][0]
    if not os.path.isdir(root):
        return False, f"{prefix} root is not mounted on this machine"
    return True, ""


def sha256(path):
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def looks_binary(path):
    """A null byte in the first 8 KB. Cheap, and right for the file types here."""
    with open(path, "rb") as fh:
        return b"\0" in fh.read(8192)


# ── source roots (ORDER 30 phase 2 / ORDER 40 R-40d) ────────────────────────
# Phase 1 mirrored one root: the CARR AI vault. Phase 2 adds doctrine that lives
# OUTSIDE it — the skills, which sit in `.claude/skills` on the Drive (5) and on
# the Mac (12). A row may therefore carry a root prefix:
#
#   00_Context/model-tiering.md          -> the vault (no prefix; phase 1 shape,
#                                           so all 34 existing rows are untouched)
#   drive:.claude/skills/x/SKILL.md      -> "My Drive", the vault's parent
#   home:.claude/skills/x/SKILL.md       -> the Mac home directory
#
# Non-vault roots mirror under corpus/_drive/ and corpus/_home/ so the mirror
# tree cannot collide with a vault path and nothing escapes corpus/ via `..`.
#
# DELL'S TIER IS NEVER MIRRORED HERE (R-40d). ~/.claude and this Drive account
# are Joe's; Dell's equivalents live on his machine and mirror into his own repo.
DRIVE_ROOT = os.path.dirname(VAULT)
HOME_ROOT = os.path.expanduser("~")
ROOTS = {"drive": (DRIVE_ROOT, "_drive"), "home": (HOME_ROOT, "_home")}


def resolve(rel):
    """A corpus-set row -> (absolute source path, absolute mirror path)."""
    if ":" in rel:
        prefix, _, tail = rel.partition(":")
        if prefix in ROOTS:
            root, sub = ROOTS[prefix]
            return os.path.join(root, tail), os.path.join(CORPUS, sub, tail)
    return os.path.join(VAULT, rel), os.path.join(CORPUS, rel)


def load_set():
    """corpus-set.tsv -> [(path_with_optional_root_prefix, class, why)]. '#' comments ok."""
    rows = []
    with open(SET_FILE, encoding="utf-8") as fh:
        for line in fh:
            line = line.rstrip("\n")
            if not line.strip() or line.lstrip().startswith("#"):
                continue
            parts = line.split("\t")
            while len(parts) < 3:
                parts.append("")
            rows.append((parts[0].strip(), parts[1].strip(), parts[2].strip()))

    # NON-OVERLAP GUARD (2026-08-10, the two-owners ruling, loop #312). Anything
    # under `My Drive/.claude/skills` or `.../agents` is owned by claude-tree/ and
    # written by bin/sync-skills.sh. Two tools projecting to one destination is
    # how the council's panel-verdict rule was silently deleted 27 minutes after
    # it was reconciled: each writer saw the other's write as drift and neither
    # was wrong. Refusing at load time is what stops the overlap coming back by
    # someone re-adding a row that looks perfectly reasonable on its own.
    clash = [r[0] for r in rows
             if r[0].startswith("drive:.claude/skills/")
             or r[0].startswith("drive:.claude/agents/")]
    if clash:
        raise SystemExit(
            "corpus-set.tsv claims %d file(s) that bin/sync-skills.sh already owns:\n  %s\n"
            "Those live in claude-tree/ and project to the same Drive folder this tool "
            "writes. Remove the row(s), or move ownership deliberately and update BOTH "
            "tools plus hooks/corpus_renders.py." % (len(clash), "\n  ".join(clash)))
    return rows


def load_manifest():
    try:
        with open(MANIFEST, encoding="utf-8") as fh:
            return json.load(fh)
    except FileNotFoundError:
        return None


# Keys that change on every run whether or not anything happened. Everything
# else in the manifest is substance: the file list, their hashes, their sizes.
VOLATILE_KEYS = ("generated_at", "generator")


def write_manifest(out) -> bool:
    """Write the manifest only when its SUBSTANCE changed. Returns True if written.

    ADDED 2026-08-07. Both --sync and --push rewrote this file unconditionally,
    stamping a fresh generated_at every night. corpus/manifest.json is tracked in
    git, so the nightly chain left the repo permanently dirty: `git status` in
    ~/carr-system showed a modified file every single morning, on a night where
    nothing had been pushed at all.

    That is not a cosmetic annoyance, it is a broken instrument. A working tree
    that is never clean cannot tell you that something real is uncommitted — which
    is exactly what happened on 2026-08-07, when a genuine fix sat unnoticed in
    the same `git status` as this permanent noise. Same failure family as the
    nightly alarm the ORDER 2 addendum killed: a signal that fires every day
    stops being read.

    The timestamp is not lost, it is made truthful. generated_at now means "when
    the corpus last actually moved", which is the question anyone reading it is
    asking, rather than "when this script last ran", which out/nightly.log
    already answers.
    """
    old = load_manifest()
    if old is not None:
        a = {k: v for k, v in old.items() if k not in VOLATILE_KEYS}
        b = {k: v for k, v in out.items() if k not in VOLATILE_KEYS}
        if a == b:
            return False
    with open(MANIFEST, "w", encoding="utf-8") as fh:
        json.dump(out, fh, indent=2, ensure_ascii=False)
        fh.write("\n")
    return True


def status():
    """Classify every file in the set. Pure read; safe for the health check."""
    man = load_manifest()
    if man is None:
        return {"manifest": False, "files": [], "skipped": [], "orphans": [],
                "counts": {}, "mirrored": 0, "manifest_count": 0}

    recorded = {f["source"]: f for f in man.get("files", [])}
    skipped_rec = {s["source"]: s for s in man.get("skipped", [])}
    out = []
    in_set = set()

    for rel, klass, why in load_set():
        in_set.add(rel)
        src, dst = resolve(rel)
        rec = recorded.get(rel)

        if not os.path.exists(src):
            # Not "the mirror is wrong" — the vault file moved, was renamed, or the
            # Drive mount is not up. Named separately so nobody re-mirrors a ghost.
            out.append({"path": rel, "state": "SOURCE-GONE", "class": klass})
            continue

        try:
            if os.path.getsize(src) > MAX_BYTES or looks_binary(src):
                out.append({"path": rel, "state": "SKIP", "class": klass,
                            "bytes": os.path.getsize(src)})
                continue
        except OSError as e:
            out.append({"path": rel, "state": "UNREADABLE", "class": klass, "err": str(e)})
            continue

        src_h = sha256(src)
        if rec is None or not os.path.exists(dst):
            out.append({"path": rel, "state": "NEW" if rec is None else "MISSING-MIRROR",
                        "class": klass, "src_sha": src_h})
            continue

        mir_h = sha256(dst)
        was = rec.get("sha256")
        if mir_h != was and src_h != was:
            out.append({"path": rel, "state": "BOTH-CHANGED", "class": klass,
                        "src_sha": src_h, "mirror_sha": mir_h, "manifest_sha": was})
        elif mir_h != was:
            out.append({"path": rel, "state": "MIRROR-EDITED", "class": klass,
                        "src_sha": src_h, "mirror_sha": mir_h, "manifest_sha": was})
        elif src_h != was:
            out.append({"path": rel, "state": "DRIFT", "class": klass, "src_sha": src_h})
        else:
            out.append({"path": rel, "state": "OK", "class": klass})

    orphans = [p for p in recorded if p not in in_set]
    counts = {}
    for f in out:
        counts[f["state"]] = counts.get(f["state"], 0) + 1
    return {"manifest": True, "files": out, "orphans": orphans, "counts": counts,
            "skipped": list(skipped_rec.values()),
            "manifest_count": len(recorded), "generated_at": man.get("generated_at")}


def sync(force=False, prune=False):
    """Copy Drive -> mirror for everything that changed, then rewrite the manifest."""
    man = load_manifest() or {"files": [], "skipped": []}
    recorded = {f["source"]: f for f in man.get("files", [])}
    now = time.strftime("%Y-%m-%dT%H:%M:%S%z")

    files, skipped, refused, copied = [], [], [], []
    st_by_path = {f["path"]: f for f in status()["files"]} if os.path.exists(MANIFEST) else {}

    for rel, klass, why in load_set():
        src, dst = resolve(rel)
        if not os.path.exists(src):
            refused.append((rel, "source missing on the Drive"))
            if rel in recorded:            # keep the old row; do not lose the history pointer
                files.append(recorded[rel])
            continue
        size = os.path.getsize(src)
        if size > MAX_BYTES:
            skipped.append({"source": rel, "reason": f"over {MAX_BYTES // (1024*1024)} MB", "bytes": size})
            continue
        if looks_binary(src):
            skipped.append({"source": rel, "reason": "binary (this is a text corpus)", "bytes": size})
            continue

        state = st_by_path.get(rel, {}).get("state")
        if state in ("MIRROR-EDITED", "BOTH-CHANGED") and not force:
            refused.append((rel, f"{state}: the mirror was edited; the Drive is canonical. "
                                 "Move the edit to the Drive, or re-run with --force to discard it."))
            files.append(recorded[rel])
            continue

        os.makedirs(os.path.dirname(dst), exist_ok=True)
        need = (not os.path.exists(dst)) or sha256(dst) != sha256(src)
        if need:
            shutil.copy2(src, dst)
            copied.append(rel)
        mt = os.path.getmtime(src)
        files.append({
            "source": rel,
            "mirror": os.path.relpath(dst, REPO),
            "sha256": sha256(src),
            "bytes": size,
            "mtime": round(mt, 3),
            "mtime_iso": time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime(mt)),
            "mirrored_at": now if need else recorded.get(rel, {}).get("mirrored_at", now),
            "class": klass,
            "why": why,
        })

    in_set = {f["source"] for f in files} | {s["source"] for s in skipped}
    orphans = [p for p in recorded if p not in in_set]
    pruned = []
    if prune:
        for p in orphans:
            fp = os.path.join(CORPUS, p)
            if os.path.exists(fp):
                os.remove(fp)
            pruned.append(p)

    out = {
        "generated_at": now,
        "generator": "tools/corpus-sync.py --sync (conflict-resolution path, post-flip)",
        "vault_root": VAULT,
        "canonical": CANONICAL_NOTE,
        "max_bytes": MAX_BYTES,
        "count": len(files),
        "files": sorted(files, key=lambda f: f["source"]),
        "skipped": sorted(skipped, key=lambda s: s["source"]),
    }
    wrote = write_manifest(out)
    return {"copied": copied, "refused": refused, "skipped": skipped,
            "orphans": orphans, "pruned": pruned, "count": len(files),
            "manifest_written": wrote}


def push():
    """Copy repo (git, canonical since the 2026-08-06 flip) -> source-side (Drive/vault/home)
    for every row whose git copy changed. SAFETY (flag-not-clobber): before overwriting a
    source-side file, its hash is checked against the manifest's recorded hash. If the source
    changed since the last sync, that is a hand-edit made after git became canonical — a
    CONFLICT. It is never overwritten silently; the row is skipped and reported so a human can
    resolve it. Every row actually pushed gets its manifest entry rewritten so both hashes read
    equal afterward (the same guarantee --sync gives in the other direction).
    """
    man = load_manifest() or {"files": [], "skipped": []}
    recorded = {f["source"]: f for f in man.get("files", [])}
    now = time.strftime("%Y-%m-%dT%H:%M:%S%z")

    pushed, conflicts, refused, unchanged = [], [], [], []
    files_by_source = dict(recorded)   # start from the manifest; only pushed rows get rewritten

    unreachable = []
    for rel, klass, why in load_set():
        # A row whose ROOT is not mounted is not a refusal and not a conflict: it
        # is a destination that does not exist on this machine right now. Counted
        # and named so the skip is visible, kept out of the exit code so a
        # retired root cannot make every run look broken.
        ok_root, root_why = row_root_writable(rel)
        if not ok_root:
            unreachable.append((rel, root_why))
            continue
        src, dst = resolve(rel)   # src = source-side (Drive/vault/home); dst = git copy under corpus/

        if not os.path.exists(dst):
            refused.append((rel, "no git copy under corpus/ to push from — this row was never synced in"))
            continue
        try:
            if os.path.getsize(dst) > MAX_BYTES or looks_binary(dst):
                refused.append((rel, "git copy is over the size/binary limit for this text corpus"))
                continue
        except OSError as e:
            refused.append((rel, f"git copy unreadable: {e}"))
            continue

        repo_h = sha256(dst)
        rec = recorded.get(rel)
        was = rec.get("sha256") if rec else None
        source_exists = os.path.exists(src)
        src_h = sha256(src) if source_exists else None

        if source_exists and repo_h == src_h:
            unchanged.append(rel)
            if rec is not None and was != repo_h:
                # Both sides already agree with each other, just not with the stale manifest
                # entry (e.g. converged independently). Realign the record; nothing to copy.
                files_by_source[rel] = {**rec, "sha256": repo_h, "mirrored_at": now}
            continue

        if source_exists and was is not None and src_h != was:
            # The source-side file moved since the last sync. Post-flip that is a hand-edit on
            # what is now a render — flag it, do not clobber it.
            conflicts.append({"path": rel, "class": klass, "repo_sha": repo_h,
                               "source_sha": src_h, "manifest_sha": was})
            continue

        # Safe to push: the source either never existed at this path (a brand-new doctrine
        # file, or the mount recreating a deleted render) or still matches what was last
        # recorded — nobody touched the Drive/vault/home side out of band.
        os.makedirs(os.path.dirname(src), exist_ok=True)
        shutil.copy2(dst, src)
        pushed.append(rel)
        mt = os.path.getmtime(dst)
        files_by_source[rel] = {
            "source": rel,
            "mirror": os.path.relpath(dst, REPO),
            "sha256": repo_h,
            "bytes": os.path.getsize(dst),
            "mtime": round(mt, 3),
            "mtime_iso": time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime(mt)),
            "mirrored_at": now,
            "class": klass,
            "why": why,
        }

    out = {
        "generated_at": now,
        "generator": "tools/corpus-sync.py --push (CORPUS FLIP, 2026-08-06)",
        "vault_root": VAULT,
        "canonical": CANONICAL_NOTE,
        "max_bytes": MAX_BYTES,
        "count": len(files_by_source),
        "files": sorted(files_by_source.values(), key=lambda f: f["source"]),
        "skipped": man.get("skipped", []),
    }
    wrote = write_manifest(out)
    return {"pushed": pushed, "conflicts": conflicts, "refused": refused,
            "unchanged": unchanged, "unreachable": unreachable, "manifest_written": wrote}


# States that mean "the source-side copy no longer matches what git last pushed" — post-flip
# these are the loud ones, because git is canonical and the source side should only ever move
# because --push moved it.
CONFLICT_STATES = ("DRIFT", "BOTH-CHANGED")


def report(st):
    """Status/check mode, in git-canonical language (CORPUS FLIP, 2026-08-06):
      * git (corpus/) differing from the manifest = NORMAL, "to push" — remedy is `--push`.
      * the source-side (Drive/vault/home) copy differing from the manifest = a CONFLICT —
        a hand-edit landed on what is now a render. Named loudly per file; never auto-resolved.
    """
    if not st["manifest"]:
        print("corpus: no manifest yet — run: python3 tools/corpus-sync.py --sync")
        return 1
    c = st["counts"]
    ok = c.get("OK", 0)
    to_push = c.get("MIRROR-EDITED", 0)
    conflict = sum(c.get(k, 0) for k in CONFLICT_STATES)
    missing = c.get("NEW", 0) + c.get("MISSING-MIRROR", 0)
    other = c.get("SOURCE-GONE", 0) + c.get("UNREADABLE", 0)
    print(f"corpus mirror (git-canonical) — {ok} clean, {to_push} to push, {conflict} CONFLICT "
          f"(source-side changed), {missing} missing git copy, {other} unreadable/gone, "
          f"{len(st['orphans'])} orphan(s)")
    for f in st["files"]:
        s = f["state"]
        if s == "OK":
            continue
        if s == "MIRROR-EDITED":
            print(f"  ++ {'TO-PUSH':<14} {f['path']}  (git changed since the last sync; run --push)")
        elif s in CONFLICT_STATES:
            print(f"  !! {'CONFLICT':<14} {f['path']}")
            print("     THE SOURCE-SIDE COPY DIFFERS FROM GIT. Git is canonical since 2026-08-06, so a")
            print("     Drive/vault-side edit here is unexpected. Fold it into git by hand, then --push")
            print("     will clear it — or run --push now to see the same conflict named again; it")
            print("     refuses to overwrite the source side automatically.")
        elif s in ("NEW", "MISSING-MIRROR"):
            print(f"  -- {s:<14} {f['path']}  (no git copy yet — seed once with --sync, then --push going forward)")
        else:
            print(f"  -- {s:<14} {f['path']}")
    for p in st["orphans"]:
        print(f"  -- ORPHAN         {p}  (in the manifest, no longer in corpus-set.tsv; --sync --prune removes it)")
    for s in st["skipped"]:
        print(f"  -- SKIPPED        {s['source']}  ({s['reason']}, {s['bytes']:,} bytes)")
    return 2 if conflict else (1 if to_push or missing or other else 0)


def main(argv):
    do_sync = "--sync" in argv or "--apply" in argv
    do_push = "--push" in argv
    force = "--force" in argv
    prune = "--prune" in argv
    if "--json" in argv:
        print(json.dumps(status(), indent=2))
        return 0
    if do_push:
        # THE WHOLE-RUN VAULT GATE IS GONE, and this is the point of the change.
        # It returned 78 for every row unless the vault resolved, which was right
        # when every row lived in the vault and wrong from the moment the set
        # gained `home:` rows addressing this Mac. After the 2026-08-19 cutoff
        # retired the vault it switched off twelve rows that never needed it.
        # push() now asks each root for itself; the run only skips when NOTHING
        # is reachable, which is the original contract with the original meaning.
        r = push()
        reachable_any = bool(r["pushed"] or r["unchanged"] or r["conflicts"] or r["refused"])
        if not reachable_any and r["unreachable"]:
            print(f"corpus push — SKIP: no configured root is reachable "
                  f"({len(r['unreachable'])} row(s) waiting on one)")
            print("  Nothing pushed; safe to retry once a root is mounted.")
            return 78
        print(f"corpus push — {len(r['pushed'])} pushed, {len(r['unchanged'])} already in sync, "
              f"{len(r['conflicts'])} CONFLICT, {len(r['refused'])} refused, "
              f"{len(r['unreachable'])} root unreachable")
        print(f"  manifest {'rewritten' if r['manifest_written'] else 'unchanged (nothing moved)'}")
        for p in r["pushed"]:
            print(f"  >> PUSHED    {p}")
        for cf in r["conflicts"]:
            print(f"  !! CONFLICT     {cf['path']}")
            print(f"     source-side hash {cf['manifest_sha'][:12]}.. -> {cf['source_sha'][:12]}.. since the")
            print(f"     last sync (git is at {cf['repo_sha'][:12]}..). NOT overwritten — this is a")
            print("     hand-edit on what is now a render. Fold it into git, or run --sync to pull it")
            print("     into git deliberately, then re-run --push.")
        for rel, why in r["refused"]:
            print(f"  -- REFUSED   {rel}\n     {why}")
        if r["unreachable"]:
            # Named rather than counted alone: a silent skip of a retired root is
            # how a live row would disappear next time the set changes.
            reasons = sorted({why for _rel, why in r["unreachable"]})
            print(f"  ~~ ROOT NOT WRITABLE  {len(r['unreachable'])} row(s)")
            for why in reasons:
                print(f"     {why}")
            print("     Not a conflict and not a refusal; excluded from the exit code on "
                  "purpose, because a retired root must not make every run look broken.")
        return 2 if (r["conflicts"] or r["refused"]) else 0
    if do_sync:
        print("NOTE: git is canonical since 2026-08-06 — pulling source into git is the "
              "conflict-resolution path, not the normal flow.")
        r = sync(force=force, prune=prune)
        print(f"corpus sync — {r['count']} file(s) in the manifest, {len(r['copied'])} (re)mirrored")
        print(f"  manifest {'rewritten' if r['manifest_written'] else 'unchanged (nothing moved)'}")
        for p in r["copied"]:
            print(f"  ++ {p}")
        for p, why in r["refused"]:
            print(f"  !! REFUSED {p}\n     {why}")
        for s in r["skipped"]:
            print(f"  -- SKIPPED {s['source']} ({s['reason']}, {s['bytes']:,} bytes)")
        for p in r["pruned"]:
            print(f"  xx PRUNED  {p}")
        for p in r["orphans"]:
            if p not in r["pruned"]:
                print(f"  -- ORPHAN  {p} (left in place; --prune deletes it)")
        return 2 if r["refused"] else 0
    return report(status())


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
