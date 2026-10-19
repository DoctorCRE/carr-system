#!/usr/bin/env python3
"""
vault-drift-watch.py — vault-write door detection, v2 (Phase 1, 2026-08-13).

The vault ("/Users/booko/My Drive/CARR AI") accepts writes from more than one
actor, and the client-write door from Codex has been demonstrated open. v1 of
this watch was a plain manifest diff: sha256+mtime per tracked .md file,
compared against the previous run. That catches a hand-edit sitting on disk
between two runs, but it CANNOT catch a generated file being clobbered and then
immediately re-clobbered by the same night's export — the exact failure mode
this v2 build was commissioned to close: a nightly chain that rewrites every
generated file is a nightly chain that erases the evidence of any tamper that
happened to a generated file earlier that same day, before this watch ever saw
it. v2 adds a second, independent baseline that survives the rewrite.

THREE THINGS THIS SCRIPT NOW DOES, run as two separate modes:

  --rebaseline (run LAST, after the nightly export chain finishes)
      Snapshots the POST-EXPORT state of every file the live exporters/targets.py
      TARGETS registry knows about: sha256 always, plus a content copy (so a
      later diff has something to diff against) unless the total registered
      content exceeds a size cap, in which case only .md registry files get
      content-copied and everything else (xlsx, json) is hash-only. Written to
      out/vault-drift-baseline/.

  --check (run FIRST, before anything else in the nightly chain)
      Two independent comparisons:
        1. TAMPER — every file in the last --rebaseline's registry is re-hashed
           now. ANY hash difference (or the file having vanished) since that
           baseline is TAMPER: something touched a generated file outside the
           export that is supposed to be its only writer. This is the check v1
           could not do, because v1 only ever compared manifest-to-manifest and
           every manifest already reflected a post-export state.
        2. UNEXPECTED — same walk-and-classify structural diff v1 did, over
           tracked .md files only: any add/modify/delete of a .md file that is
           neither a registered export target nor one of the 54 files already
           content-hash-checked by the doctrine-mirror drift check (see
           tools/corpus-sync.py / corpus/corpus-set.tsv — that check owns those
           54 files; this watch excludes them so the same drift is never
           reported twice by two different scripts) is UNEXPECTED.
      Every TAMPER and UNEXPECTED finding gets salvaged before this run ends:
      quarantine the current file plus a diff against the last known-good
      content, append a well-formed intake payload to a manifest.jsonl, and
      (only if CARR_DRIFT_INGEST=1 is set — OFF by default) POST that payload
      to the Worker's /ingest socket, so a hand-edit that a nightly export is
      about to overwrite has a real chance of reaching the record layer instead
      of just disappearing. See "SALVAGE-THEN-HEAL" below.

A first --check run with no --rebaseline baseline yet skips the tamper
comparison (nothing to compare against) rather than reporting false tamper; a
first --check run with no previous check-manifest is a SEED for the structural
.md comparison, same semantics as v1. Neither counts as drift.

Exit codes (--check): 0 clean, 2 at least one TAMPER or UNEXPECTED finding,
1 FATAL (bad --root). Exit codes (--rebaseline): 0 on success, 1 FATAL.

SALVAGE-THEN-HEAL. The nightly chain's whole design is: overwrite every
generated file with a fresh export every night. If a human (or Codex) hand-
edited a registry file in between, the next export blows that edit away with
no record it ever existed — unless something captures it FIRST. That is what
--check's salvage step is for: quarantine + diff + a manifest line exist for
every finding whether or not live routing is turned on, so nothing is lost even
with CARR_DRIFT_INGEST unset. Turning that flag on is a separate, visible,
human decision — the capability ships disabled.

Run:
  python3 ops/vault-drift-watch.py --check
  python3 ops/vault-drift-watch.py --rebaseline
Test:
  python3 ops/vault-drift-watch.py --check --root <scratch> --manifest <scratch> \\
      --baseline-dir <scratch> --quarantine-dir <scratch> --salvage-manifest <scratch> \\
      --summary <scratch>
  (never point --root at the real vault from a test)
"""
import argparse
import pathlib as _PATHLIB
import difflib
import hashlib
import json
import os
import re
import shutil
import sys
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
# PHASE 4 GATE (2026-08-22). --root used to DEFAULT to a hardcoded Drive path,
# so the watch reached for Joe's real vault with no flag, no reason and no
# refusal -- the ambient Drive selection Phase 4 removes. There is no default
# now. An explicit --root (what every test passes, and what the tool's own help
# has always called the test override) runs normally, because naming a root is
# not ambient. Omitting it means the caller wants the REAL vault, and that is
# the case which must say so: it requires --recovery --reason WHY, and refuses
# with the seam named otherwise.
sys.path.insert(0, str(_PATHLIB.Path(__file__).resolve().parents[1]))
from lib.drive_recovery import (  # noqa: E402
    SEAM_MISSING_EXIT, add_recovery_arguments, require_recovery)

DEFAULT_VAULT = None
DEFAULT_MANIFEST = REPO_ROOT / "out" / "vault-md-manifest.json"
DEFAULT_BASELINE_DIR = REPO_ROOT / "out" / "vault-drift-baseline"
DEFAULT_QUARANTINE_DIR = REPO_ROOT / "out" / "vault-drift-quarantine"
DEFAULT_SALVAGE_MANIFEST = REPO_ROOT / "out" / "vault-drift-salvage-manifest.jsonl"
DEFAULT_SUMMARY = REPO_ROOT / "out" / "vault-drift-check-summary.json"
CORPUS_SET_FILE = REPO_ROOT / "corpus" / "corpus-set.tsv"

# ~50MB: above this, --rebaseline stops content-copying xlsx/binary registry
# files (hash-only for those) and content-copies .md registry files only.
CONTENT_COPY_CAP_BYTES = 50 * 1024 * 1024

TEXT_DIFF_EXTS = (".md", ".json", ".txt", ".tsv", ".csv")
DIFF_EXCERPT_CHARS = 8000

# Same skip-list convention as pipelines/build-section-index.py's SKIP_DIRS:
# matched by directory NAME anywhere in the tree, exact case, plus dotdirs.
SKIP_DIRS = {"Source Material", "Output", ".obsidian", ".claude", ".git",
             "source-exports", "photos", "Prospects", "_asset_staging", "_to_delete"}

# Also from build-section-index.py (THE ARCHIVE LEAK, 2026-08-03): the exporter
# keeps per-file version snapshots in SUFFIX-named directories
# ("CLAUDE.md.generations", "decision-history.md.generations", 46 of them in
# the vault today, 249 .md files inside them). Those are byte-copies of files
# already covered by their live path, not new content — walking them would
# make every exporter refresh look like a wall of UNEXPECTED adds.
GENERATIONS_SUFFIX = ".generations"

def load_target_paths_by_key_prefix(prefix):
    """Resolve the same paths as `run.sh export --only <prefix>`."""
    try:
        sys.path.insert(0, str(REPO_ROOT))
        from exporters import targets as _targets  # noqa: PLC0415 (deliberate late import)
        return {rel for key, (rel, _b) in _targets.TARGETS.items()
                if key.startswith(prefix)}, None
    except Exception as exc:  # pragma: no cover - exercised via a real import failure only
        return set(), f"{type(exc).__name__}: {exc}"


ALWAYS_EXPECTED = {
    "DNA/compiled-rules-shared.md",
    "00_Context/compiled-rules-joe.md",
    "DNA/compiled-rules-dell.md",
}
# 00_Context/today.md was listed here while bin/local-briefs.sh still copied it
# into the vault. The cutoff retired that write on 2026-08-20 once the claim-card
# verb shipped and served the one section no verb could reach, so nothing writes
# the path any more and it does not belong in an expected-WRITERS set.
# The two Automation/Learning reports were briefly listed here as expected
# writers. They are not listed any more because they no longer exist in the
# vault: the same 2026-08-19 cutoff retired them, and bin/learning-weekly.sh and
# bin/learning-monthly.sh now write to the repo's out/Learning instead. A path
# that nothing writes does not belong in an expected-WRITERS set.

# ---------------------------------------------------------------- ingest wiring
# Same house pattern as bin/notes-sweep-post.sh and bin/pull-gmail-calendar.py:
# one shared credential file, one bearer token per source, never composed into
# a command line or printed. Reused here rather than reinvented.
INGEST_ENVFILE = os.path.expanduser("~/.config/carr/ingest.env")
DEFAULT_INGEST_URL = "https://api.practicecre.com/ingest"
INGEST_TOKEN_VAR = "CARR_INGEST_TOKEN_VAULT_DRIFT"


def load_expected_writers():
    """Registered export target relpaths, parsed from the LIVE exporters/targets.py
    registry (its TARGETS dict) rather than hand-copied, so this can't drift from
    the real registry the way a pasted list would. Returns (expected_set, error);
    error is None on a clean import. On failure this falls back to
    ALWAYS_EXPECTED only — conservative in the safe direction: more UNEXPECTED
    false positives, never a false "expected" on an unverified path. Note this
    registry is NOT .md-only (it also carries the xlsx/json registry targets) —
    both --check's tamper pass and --rebaseline hash every path in it regardless
    of extension.
    """
    try:
        sys.path.insert(0, str(REPO_ROOT))
        from exporters import targets as _targets  # noqa: PLC0415 (deliberate late import)
        paths = {rel for rel, _builder in _targets.TARGETS.values()}
        # THE CUTOFF (fired 2026-08-19). A retired .md render was MOVED to
        # _to_delete staging on purpose and the exporter will never write it
        # again, so keeping it registered here means 37 paths permanently
        # "not present" and, on the tamper pass, 37 findings that describe a
        # deliberate act as drift. Reads the SAME flag the exporter gates on;
        # any read failure leaves the path registered, which is the safe
        # direction this function already documents.
        paths |= ALWAYS_EXPECTED
        # Subtracted AFTER the union on purpose: ALWAYS_EXPECTED re-lists the
        # three compiled-rules renders as a belt against a broken registry
        # import, and they are retired too. 00_Context/today.md is NOT — it is
        # written daily by bin/local-briefs.sh, not by the exporter, so the
        # cutoff does not touch it and it stays tracked here.
        try:
            from exporters.run_exports import md_renders_retired  # noqa: PLC0415
            if md_renders_retired():
                paths -= {rel for rel, _b in _targets.TARGETS.values()
                          if rel.lower().endswith(".md")}
        except Exception:
            pass
        return paths, None
    except Exception as exc:  # pragma: no cover - exercised via a real import failure only
        return set(ALWAYS_EXPECTED), f"{type(exc).__name__}: {exc}"


def export_target_paths():
    """target key -> vault-relative path, from the LIVE registry. The ledger
    stores hashes per TARGET; the tamper pass works in PATHS. Hand-copying the
    mapping between them is the drift this whole file exists to catch, so it is
    read from exporters/targets.py or not used at all."""
    try:
        sys.path.insert(0, str(REPO_ROOT))
        from exporters import targets as _targets  # noqa: PLC0415 (deliberate late import)
        return {key: rel for key, (rel, _builder) in _targets.TARGETS.items()}
    except Exception:
        return {}


def load_export_ledger():
    """vault-relative path -> {sha256 the exporter recorded writing there}.

    exporters/common.py stores sha256 of the written file in export_run.file_sha
    on every LIVE export and never on a draft one, so this is an exact record of
    what the legitimate writer last put on disk. Returns (ledger, note); note is
    a human-readable reason when the ledger could not be read, and an unreadable
    ledger degrades to baseline-only comparison rather than to silence.
    """
    env_path = os.path.expanduser("~/.config/carr/db.env")
    url = os.environ.get("CARR_DB_EXPORTER_URL") or os.environ.get("CARR_DB_JOBS_URL")
    if not url and os.path.exists(env_path):
        for key in ("CARR_DB_EXPORTER_URL=", "CARR_DB_JOBS_URL="):
            for line in open(env_path):
                if line.startswith(key):
                    url = line.split("=", 1)[1].strip().strip('"').strip("'")
                    break
            if url:
                break
    if not url:
        return {}, "no exporter credential — tamper falls back to the baseline alone"
    paths = export_target_paths()
    if not paths:
        return {}, "exporters/targets.py did not import — tamper falls back to the baseline alone"
    try:
        import psycopg  # noqa: PLC0415 (deliberate late import; absent in some venvs)
        with psycopg.connect(url, connect_timeout=15) as conn:
            cur = conn.cursor()
            cur.execute("select 1 from information_schema.columns "
                        "where table_name='export_run' and column_name='file_sha'")
            if not cur.fetchone():
                return {}, "export_run.file_sha not present on this database (migration 0073)"
            cur.execute("""
                select distinct on (target) target, file_sha
                  from export_run
                 where status='ok' and file_sha is not null
                 order by target, ran_at desc""")
            ledger = {}
            for target, sha in cur.fetchall():
                rel = paths.get(target)
                if rel:
                    ledger.setdefault(rel, set()).add(sha)
            return ledger, None
    except Exception as exc:
        return {}, f"{type(exc).__name__}: {exc} — tamper falls back to the baseline alone"


def tamper_verdict(relpath, baseline_sha, current_sha, ledger):
    """None when the file is accounted for; otherwise the reason it is TAMPER.

    Tamper if and only if the bytes on disk match NEITHER the baseline snapshot
    NOR the last hash the exporter recorded for that path. The second clause is
    what stops a legitimate re-export between two baselines — the hourly rule
    refresh, a session's export, a second chain — from reading as tampering. The
    first clause is what still catches a hand-edit, and it is not negotiable:
    bytes that no legitimate writer claims are a finding.
    """
    if current_sha is None:
        return "generated file deleted since last rebaseline"
    if baseline_sha is not None and current_sha == baseline_sha:
        return None
    known = ledger.get(relpath)
    if known and current_sha in known:
        return None
    if not known:
        return ("hash changed since last rebaseline and there is no exporter-recorded "
                "hash for this target to check it against")
    return ("hash changed since last rebaseline and does not match what the exporter "
            "recorded writing (rewritten by something other than the export)")


def load_corpus_mirror_set():
    """Vault-relative paths already covered by the EXISTING doctrine-mirror
    drift check (tools/corpus-sync.py, driven off corpus/corpus-set.tsv) — that
    script content-hashes these 54 files against its own manifest and prints a
    loud CONFLICT by name on a hand-edit. Excluded here so this watch never
    double-reports the same file two different scripts already watch.
    """
    if not CORPUS_SET_FILE.exists():
        return set()
    paths = set()
    try:
        for line in CORPUS_SET_FILE.read_text().splitlines():
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            parts = line.split("\t")
            if parts and parts[0]:
                paths.add(parts[0])
    except Exception:
        return set()
    return paths


def sha256_of(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def scan(root):
    """Return {relpath: {"sha256":..., "mtime":...}} for every tracked .md file."""
    root = Path(root)
    files = {}
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = sorted(d for d in dirnames
                              if d not in SKIP_DIRS
                              and not d.startswith(".")
                              and not d.endswith(GENERATIONS_SUFFIX))
        for name in sorted(filenames):
            if not name.endswith(".md"):
                continue
            abspath = Path(dirpath) / name
            relpath = str(abspath.relative_to(root))
            try:
                st = abspath.stat()
                digest = sha256_of(abspath)
            except OSError as exc:
                print(f"  ⚠︎ could not read {relpath}: {exc}", file=sys.stderr)
                continue
            files[relpath] = {"sha256": digest, "mtime": st.st_mtime}
    return files


# A THIRD legitimate writer class the first two missed. `expected` covers the
# exporters/targets.py TARGETS registry and `corpus_mirror` covers
# tools/corpus-sync.py, but the vault has a third machine that writes .md into
# it every night: `./run.sh graph-system` (bin/nightly.sh:233) regenerates the
# whole derived Graph-System/ tree. Nothing registers those files, because they
# are not export targets and not corpus mirrors — so every one of them landed in
# the UNEXPECTED pile. Measured cost, run 20260813T201608Z: 97 of that run's 99
# UNEXPECTED findings were Graph-System/ paths. A drift report that is 98% one
# known generator is a report nobody reads, which is how the two REAL findings
# in that same run (two deliberately retired renders) nearly went unnoticed.
# ops/vault-duplicate-sweep.py already skips this directory by name for exactly
# this reason; this is that same knowledge, applied to the second scanner.
# Prefix-matched rather than set-matched on purpose: the tree is regenerated
# wholesale, so its members change nightly and enumerating them would drift.
#
# Keyed by writer label (added 2026-08-14, PR #57) so classify()'s
# "expected-writer: derived (see ...)" message names the actual generator
# instead of hardcoding Graph-System's.
#
# Backups/portability-mirror/ was proposed for this same blanket-prefix
# treatment the same day, in the same PR — same "wholesale regeneration,
# don't enumerate" reasoning that fits Graph-System/. It does NOT belong
# here: unlike Graph-System/, the mirror is verified against its own
# manifest.json instead (see MIRROR_PREFIX / classify_mirror_file below). A
# blanket exclusion here would trust the whole mirror tree wholesale,
# blinding this watch to exactly the foreign writer parking content in a
# Backups/ path that the alarm exists to catch; hash verification silences
# the same nightly false alarm without giving up that coverage. Resolved
# this way merging PR #57's DERIVED_DIRS change against the manifest-
# verification work below — verification supersedes blanket exclusion
# wherever the two approaches would otherwise collide on this one path.
DERIVED_DIRS = {
    "Graph-System/": "./run.sh graph-system",
    # Graph/ belongs here for exactly the same reason, and its absence failed
    # the nightly chain on 2026-08-14: 0 TAMPER + 4 UNEXPECTED, two of them
    # lead notes for two leads added that day. pipelines/build-graph-notes.py
    # WIPES AND REBUILDS this whole directory on every run — run.sh's own
    # comment beside the graph target says so, and warns that the hub pass must
    # follow it for that reason. So every new lead, client or vendor produced a
    # file no writer claimed, and ordinary business activity read as drift.
    #
    # Left unfixed this is the alarm-that-fires-every-day problem the chain has
    # already been burned by twice (see the ORDER 2 addendum note in
    # bin/nightly.sh): a check that goes red on normal work teaches people to
    # stop reading it, and then it is worth nothing on the night it matters.
    "Graph/": "./run.sh graph",
}

# The same problem one level down, where a whole directory is the wrong unit.
# `social-batch-weekly` (ops/config/services.json, cron 0 8 * * 5) writes next
# week's drafts into the vault every Friday morning. That is the other half of
# the 4 UNEXPECTED findings that failed the chain on 2026-08-14 — the two above
# were lead notes, these two were that morning's batch — and six weekly folders
# had accumulated by then without a writer between them, so the chain went red
# every Friday on the marketing lane's most ordinary scheduled work.
#
# WHY A PATTERN AND NOT A PREFIX. "Marketing/Social Media/" cannot go in
# DERIVED_DIRS: that folder also holds the playbooks, strategies, SOPs and
# handoffs, every one of them hand-authored and exactly what this watch is for.
# A blanket prefix would trust the whole tree wholesale and blind the alarm to a
# foreign writer parking content beside them — the same trade the
# Backups/portability-mirror note below talks itself out of, for the same
# reason. What the Friday run owns is narrower than the folder: a dated run
# directory, and a dated batch file at the folder root.
#
# The date is load-bearing, not decoration. Without \d{4}-\d{2}-\d{2} in both
# shapes, "week-" and "x-batch-" degrade into prefixes anything could hide
# behind; the selftest asserts an undated path in the same namespace still
# reads UNEXPECTED.
#
# Coverage knowingly given up: an edit to an ALREADY-WRITTEN batch stops being
# reported. That is not a hazard this watch exists for — nothing in the nightly
# chain regenerates these files, so there is no export about to overwrite an
# edit, which is the loss SALVAGE-THEN-HEAL is built around.
DERIVED_PATTERNS = {
    re.compile(r"^Marketing/Social Media/week-\d{4}-\d{2}-\d{2}/"):
        "the Friday social-batch-weekly run",
    re.compile(r"^Marketing/Social Media/x-batch-\d{4}-\d{2}-\d{2}-week\.md$"):
        "the Friday social-batch-weekly run",
}

# A FOURTH legitimate writer. Backups/portability-mirror/ is also a
# wholesale nightly regeneration (pipelines/doctrine_mirror.py, invoked by
# bin/nightly.sh's "portability mirror" step) — 224 files (222 doc snapshots
# + rules.md + MANIFEST.md) that legitimately change bytes every single
# night, because every snapshot carries a fresh UTC timestamp banner even
# when the underlying content did not move.
#
# THE VERIFICATION: doctrine_mirror.py's build_mirror() writes root/
# manifest.json at the end of every run — {relpath: sha256} for every file it
# produced that run, computed by walking its own output tree, MANIFEST.md
# included (see write_output_manifest in pipelines/doctrine_mirror.py). A
# mirror file whose current sha256 matches that manifest's entry for the same
# relpath is a sanctioned regeneration, full stop — not counted as UNEXPECTED,
# reported instead in a distinct "mirror-verified" line so the fix stays
# visible in the report. Everything else under the prefix still lands in
# UNEXPECTED, because it is still the real signal the alarm exists for:
#   - hash present in the manifest but does not match current bytes (tamper)
#   - file exists under the mirror with no entry in manifest.json at all
#     (a foreign writer parked it there — the manifest never claimed it)
#   - manifest.json entry for a path with no file on disk (something deleted
#     it after the pipeline's own atomic swap published the tree)
#   - manifest.json itself missing or unreadable (fail visible, never silent)
# A path that vanishes AND has no manifest entry either (the manifest's own
# latest run also stopped producing it — a doctrine document retired from the
# database) is a legitimate structural removal, not tamper; see
# classify_mirror_file below.
MIRROR_PREFIX = "Backups/portability-mirror/"


def load_mirror_manifest(root):
    """{sub-relpath: sha256} from Backups/portability-mirror/manifest.json, or
    (None, note) when it cannot be read. sub-relpath is relative to the mirror
    root itself (e.g. "md/policy/foo.md"), matching manifest.json's own
    schema — it does NOT carry the MIRROR_PREFIX every other path in this
    watch is relative to. A missing or unreadable manifest degrades to "every
    mirror path is UNEXPECTED this run" (see classify_mirror_file) rather than
    silently trusting the tree — the same fail-visible posture as
    load_expected_writers' FATAL-on-import-failure, just non-fatal here
    because an isolated mirror manifest problem should not block the rest of
    the check.
    """
    path = os.path.join(root, MIRROR_PREFIX, "manifest.json")
    if not os.path.isfile(path):
        return None, f"no {MIRROR_PREFIX}manifest.json on disk — nothing to verify mirror files against"
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, ValueError) as exc:
        return None, f"{MIRROR_PREFIX}manifest.json unreadable: {type(exc).__name__}: {exc}"
    files = data.get("files")
    if not isinstance(files, dict):
        return None, f"{MIRROR_PREFIX}manifest.json has no 'files' mapping"
    return files, None


def staged_basenames(root):
    """Every .md filename currently sitting under the vault's _to_delete/.

    WHY THIS EXISTS. Joe's rule faae6748 forbids deleting a vault file: a
    candidate deletion is MOVED into _to_delete/ instead, in a dated folder
    naming the reason. The monthly sweep obeys that rule, so every retirement
    it makes looks, from this checker's side of the fence, exactly like a
    deletion — the file is gone from its home and _to_delete/ is in SKIP_DIRS,
    so nothing here ever saw where it went.

    On 2026-08-16 that cost a wrong conclusion rather than just a noisy line:
    the nightly reported sweep-sop.md, review-task.md and a July network brief
    as UNEXPECTED deletions, and the session on duty told Joe his monthly sweep
    procedure might have been deleted by something unknown. All three were in
    _to_delete/ the whole time, each in a folder naming its reason.

    Matched on BASENAME, not full path, because staging moves the file into a
    differently-named folder by design — the path cannot match and requiring it
    to would defeat the check. That is deliberately loose, which is why the
    caller applies it to DELETED paths only.
    """
    names = set()
    staged_root = os.path.join(root, "_to_delete")
    if not os.path.isdir(staged_root):
        return names
    for dirpath, _dirnames, filenames in os.walk(staged_root):
        for fn in filenames:
            if fn.endswith(".md"):
                names.add(fn)
    return names


def classify_mirror_file(sub, current_sha, mirror_manifest):
    """Classify one path under MIRROR_PREFIX against the mirror's own
    manifest.json. Returns (tag, reason). current_sha is None when the file
    is not currently on disk (the DELETED case); mirror_manifest is None when
    load_mirror_manifest could not read one.
    """
    if mirror_manifest is None:
        return ("UNEXPECTED", "the mirror's own manifest.json could not be read — cannot verify")
    entry_sha = mirror_manifest.get(sub)
    if current_sha is None:
        if entry_sha is not None:
            return ("UNEXPECTED",
                    "the mirror's manifest.json lists this file but it is missing on disk")
        return ("expected-writer: mirror-regenerated (see pipelines/doctrine_mirror.py)",
                "no longer produced by the mirror's latest regeneration — a structural "
                "removal, not tamper")
    if entry_sha is None:
        return ("UNEXPECTED",
                "this file exists under the mirror with no entry in its own manifest.json")
    if current_sha != entry_sha:
        return ("UNEXPECTED",
                "this file's sha256 does not match the mirror's own manifest.json entry")
    return ("expected-writer: mirror-verified (see pipelines/doctrine_mirror.py)",
            "sha256 matches the mirror's own manifest.json — sanctioned regeneration")


def classify(relpath, expected, corpus_mirror, current_sha=None, mirror_manifest=None):
    if relpath in expected:
        return "expected-writer: exporter"
    if relpath in corpus_mirror:
        return "expected-writer: corpus-mirror (see tools/corpus-sync.py)"
    if relpath.startswith(MIRROR_PREFIX):
        tag, _reason = classify_mirror_file(relpath[len(MIRROR_PREFIX):], current_sha, mirror_manifest)
        return tag
    for prefix, writer in DERIVED_DIRS.items():
        if relpath.startswith(prefix):
            return f"expected-writer: derived (see {writer})"
    for pattern, writer in DERIVED_PATTERNS.items():
        if pattern.match(relpath):
            return f"expected-writer: derived (see {writer})"
    return "UNEXPECTED"


def mirror_finding_reason(relpath, current_sha, mirror_manifest):
    """The detailed reason behind an UNEXPECTED classify() call under
    MIRROR_PREFIX, for the salvage payload's "reason" field."""
    _tag, reason = classify_mirror_file(relpath[len(MIRROR_PREFIX):], current_sha, mirror_manifest)
    return reason


def write_manifest(manifest_path, root, files, unexpected_count, seed):
    manifest_path = Path(manifest_path)
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    data = {
        "schema": 1,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "root": str(root),
        "seed": seed,
        "unexpected_count": unexpected_count,
        "file_count": len(files),
        "files": files,
    }
    tmp = manifest_path.with_suffix(manifest_path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, indent=2, sort_keys=True))
    tmp.replace(manifest_path)


def load_baseline(baseline_dir):
    manifest_path = Path(baseline_dir) / "manifest.json"
    if not manifest_path.exists():
        return None
    try:
        return json.loads(manifest_path.read_text())
    except Exception:
        return None


def load_ingest_env():
    if not os.path.exists(INGEST_ENVFILE):
        return {}
    out = {}
    with open(INGEST_ENVFILE) as fh:
        for line in fh:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            out[k.strip()] = v.strip().strip('"').strip("'")
    return out


def read_text_or_none(path):
    if not path or not os.path.exists(path):
        return None
    try:
        with open(path, encoding="utf-8") as f:
            return f.read()
    except (UnicodeDecodeError, OSError):
        return None


def build_diff(relpath, baseline_path, current_path):
    """Unified diff of baseline content copy vs current file, when both are
    diffable text. Registry binaries (xlsx) and any file this watch has no
    content copy for (non-registry .md — only rebaselined registry files get
    content-copied) fall back to an honest note plus sizes rather than a fake
    diff.
    """
    if not relpath.endswith(TEXT_DIFF_EXTS):
        b_size = os.path.getsize(baseline_path) if baseline_path and os.path.exists(baseline_path) else None
        c_size = os.path.getsize(current_path) if current_path and os.path.exists(current_path) else None
        return (f"# {relpath} is not a diffable text type for this watch (binary/spreadsheet).\n"
                f"# baseline size: {b_size if b_size is not None else 'n/a'} bytes\n"
                f"# current size:  {c_size if c_size is not None else 'n/a (file missing)'} bytes\n")
    baseline_text = read_text_or_none(baseline_path)
    current_text = read_text_or_none(current_path)
    if baseline_text is None and current_text is None:
        return (f"# no content available for {relpath} — no baseline content copy on file "
                f"(only files rebaselined after the size-cap check get one) and the current "
                f"file is also missing or unreadable.\n")
    if baseline_text is None:
        return (f"# no baseline content copy on file for {relpath} (new registry path, or "
                f"baselined as hash-only past the size cap). Showing current content only:\n\n"
                + current_text)
    b_lines = baseline_text.splitlines(keepends=True)
    c_lines = (current_text or "").splitlines(keepends=True)
    diff_lines = difflib.unified_diff(b_lines, c_lines,
                                       fromfile=f"baseline/{relpath}", tofile=f"current/{relpath}")
    text = "".join(diff_lines)
    if not text:
        text = (f"# hash differs but no line-level diff found for {relpath} — likely a "
                f"whitespace/encoding-only change, or the file is gone (see quarantine_file).\n")
    return text


def post_ingest(payload, url, token, dry_run):
    external_id = f"vault-drift:{payload['path']}:{payload.get('current_sha256') or 'deleted'}:{payload['run_id']}"
    body = dict(payload)
    body["external_id"] = external_id
    if dry_run:
        print(f"  [DRY-RUN] would POST {payload['path']} -> {url} "
              f"(external_id={external_id}, token read from {INGEST_ENVFILE}::{INGEST_TOKEN_VAR}, "
              f"never printed)")
        return
    if not token:
        print(f"  ⚠︎ ingest: CARR_DRIFT_INGEST=1 but {INGEST_TOKEN_VAR} is not set in "
              f"{INGEST_ENVFILE} — {payload['path']} stays in the manifest + quarantine only. "
              f"That token is Joe's to create; see DNA/Deal Management/record-layer/"
              f"ingest-tokens-setup.md")
        return
    req = urllib.request.Request(
        url,
        data=json.dumps(body).encode("utf-8"),
        headers={
            "authorization": f"Bearer {token}",
            "content-type": "application/json",
            # NOT cosmetic — see bin/pull-gmail-calendar.py: Cloudflare's edge
            # refuses urllib's default UA with error code 1010 before the
            # Worker ever sees the request.
            "user-agent": "carr-capture/1 (+carr-system ops/vault-drift-watch.py)",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            data = json.loads(resp.read().decode() or "{}")
            print(f"  ingest POST {payload['path']} -> {resp.status} "
                  f"(id={data.get('id')}, duplicate={data.get('duplicate')})")
    except urllib.error.HTTPError as e:
        print(f"  ⚠︎ ingest POST {payload['path']} -> HTTP {e.code}: "
              f"{e.read().decode(errors='replace')[:200]}")
    except Exception as e:
        print(f"  ⚠︎ ingest POST {payload['path']} -> FAILED: {type(e).__name__}: {e}")


def salvage_finding(finding, root, baseline_content_dir, qroot, run_id, salvage_manifest_path,
                     ingest_enabled, dry_run_ingest, ingest_url, ingest_token):
    """Quarantine the current file + a diff, append the intake payload, and
    (only if enabled) route it live. Runs on EVERY finding — TAMPER and
    UNEXPECTED alike — because both are about to be silently lost: a TAMPERed
    registry file is about to be overwritten by tonight's export, and an
    UNEXPECTED .md file is exactly the kind of hand-edit nobody logged.
    """
    relpath = finding["path"]
    current_path = os.path.join(root, relpath)
    current_exists = os.path.isfile(current_path)

    qfile_rel = None
    if current_exists:
        dest = os.path.join(qroot, relpath)
        os.makedirs(os.path.dirname(dest), exist_ok=True)
        shutil.copy2(current_path, dest)
        qfile_rel = relpath

    baseline_path = (os.path.join(baseline_content_dir, relpath)
                      if baseline_content_dir else None)
    diff_text = build_diff(relpath, baseline_path, current_path if current_exists else None)

    diff_dest = os.path.join(qroot, relpath + ".diff")
    os.makedirs(os.path.dirname(diff_dest), exist_ok=True)
    with open(diff_dest, "w", encoding="utf-8") as f:
        f.write(diff_text)

    truncated = len(diff_text) > DIFF_EXCERPT_CHARS
    excerpt = diff_text[:DIFF_EXCERPT_CHARS]

    payload = {
        "kind": "vault-drift-salvage",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "run_id": run_id,
        "path": relpath,
        "finding_type": finding["type"],
        "reason": finding["reason"],
        "baseline_sha256": finding.get("baseline_sha256"),
        "current_sha256": finding.get("current_sha256"),
        "quarantine_dir": os.path.relpath(qroot, REPO_ROOT),
        "quarantine_file": qfile_rel,
        "diff_excerpt": excerpt,
        "diff_truncated": truncated,
    }

    with open(salvage_manifest_path, "a", encoding="utf-8") as f:
        f.write(json.dumps(payload, sort_keys=True) + "\n")

    print(f"  SALVAGED  {finding['type']:<10} {relpath}  (quarantine: "
          f"{qfile_rel or 'file gone, diff only'})")

    if ingest_enabled:
        post_ingest(payload, ingest_url, ingest_token, dry_run=dry_run_ingest)

    return payload


# ---------------------------------------------------------------- --rebaseline

def run_rebaseline(args):
    root = args.root
    baseline_dir = Path(args.baseline_dir)
    content_dir = baseline_dir / "content"

    if not os.path.isdir(root):
        print(f"FATAL: vault root not found or not a directory: {root}", file=sys.stderr)
        return 1

    expected, expected_err = load_expected_writers()
    if expected_err:
        # FAIL CLOSED on a full rebaseline. This used to warn and carry on,
        # writing a baseline of just the 3 hardcoded compiled-rules paths —
        # which REPLACES the full one, silently deleting the tamper baseline for
        # every registered export target. It happened for real: the baseline
        # found on disk today was stamped 2026-08-13T18:21:01Z with file_count
        # 3, because someone ran `python3 ops/vault-drift-watch.py --rebaseline`
        # instead of nightly.sh's `./.venv/bin/python`, psycopg was missing, and
        # the tool degraded quietly. From then until the next nightly, tamper
        # detection covered 3 files instead of the whole registry, and nothing
        # in the artifact said so — the warning went to stderr and the run still
        # exited 0. A baseline that silently covers less than it claims is worse
        # than a missing one, because --check reports "OK" off it.
        #
        # --only is exempt: it MERGES, so it can only ever add or refresh the
        # paths it names, and ALWAYS_EXPECTED is hardcoded rather than imported.
        # --only-target falls through to its own resolver below, which fails
        # closed with a message naming the selector the caller actually passed.
        if not args.only and not args.only_target:
            print(f"FATAL: could not load exporters/targets.py's TARGETS registry "
                  f"({expected_err}). Refusing to write a full baseline covering only the "
                  f"{len(ALWAYS_EXPECTED)} hardcoded compiled-rules paths — that would drop "
                  f"tamper coverage for every export target. Run it the way bin/nightly.sh "
                  f"does: ./.venv/bin/python ops/vault-drift-watch.py --rebaseline",
                  file=sys.stderr)
            return 1
        print(f"⚠︎ could not load the TARGETS registry ({expected_err}) — proceeding "
              f"because --only merges and its paths are hardcoded.", file=sys.stderr)

    # A partial baseline preserves tamper evidence for every unselected path.
    only_prefixes = None
    if args.only_target:
        resolved, resolve_err = load_target_paths_by_key_prefix(args.only_target)
        if resolve_err:
            print(f"FATAL: --only-target needs exporters/targets.py's TARGETS registry to "
                  f"resolve '{args.only_target}', and it could not be loaded "
                  f"({resolve_err}). Run it the way bin/nightly.sh does: "
                  f"./.venv/bin/python ops/vault-drift-watch.py --rebaseline …",
                  file=sys.stderr)
            return 1
        if not resolved:
            print(f"FATAL: --only-target {args.only_target} matched no export target key. "
                  f"Nothing to rebaseline — refusing rather than writing an empty slice.",
                  file=sys.stderr)
            return 1
        only_prefixes = tuple(sorted(resolved))
        expected = {p for p in expected if p in resolved} | resolved
    elif args.only:
        only_prefixes = tuple(p.strip() for p in args.only.split(",") if p.strip())
        if not only_prefixes:
            print("FATAL: --only was given but parsed to no prefixes", file=sys.stderr)
            return 1
        expected = {p for p in expected if p.startswith(only_prefixes)}
        if not expected:
            print(f"FATAL: --only {args.only} matched no registered path — refusing to "
                  f"write a baseline that would drop every entry.", file=sys.stderr)
            return 1

    print(f"vault drift watch --rebaseline — {datetime.now(timezone.utc).isoformat()}")
    print(f"root: {root}")
    if only_prefixes:
        print(f"--only {', '.join(only_prefixes)} — partial rebaseline, merging into "
              f"the existing baseline")
    print(f"registered file(s): {len(expected)}")

    sizes = {}
    total_bytes = 0
    missing = []
    for relpath in sorted(expected):
        full = os.path.join(root, relpath)
        if os.path.isfile(full):
            sz = os.path.getsize(full)
            sizes[relpath] = sz
            total_bytes += sz
        else:
            missing.append(relpath)

    print(f"total registered content: {total_bytes:,} bytes (~{total_bytes / 1024 / 1024:.2f} MB) "
          f"across {len(sizes)} file(s) on disk; {len(missing)} registered path(s) not present")

    content_copy_all = total_bytes <= CONTENT_COPY_CAP_BYTES
    cap_mb = CONTENT_COPY_CAP_BYTES / 1024 / 1024
    if content_copy_all:
        print(f"under the ~{cap_mb:.0f}MB cap — content-copying every registered file "
              f"(enables a real diff for all of them, not just .md)")
    else:
        print(f"over the ~{cap_mb:.0f}MB cap — content-copying .md registry files only; "
              f"xlsx/binary registry files get a hash-only baseline")

    # A partial run inherits the prior manifest's copy decision instead of
    # recomputing it from its own small subset — otherwise rebaselining three
    # .md files would see "well under the cap" and start content-copying every
    # xlsx on the next full run's terms, quietly changing an unrelated policy.
    prior = {}
    manifest_path = baseline_dir / "manifest.json"
    if only_prefixes:
        if manifest_path.exists():
            try:
                prior = json.loads(manifest_path.read_text())
            except (OSError, ValueError) as exc:
                print(f"FATAL: --only needs a readable existing baseline to merge into, "
                      f"but {manifest_path} could not be parsed ({exc}). Run a full "
                      f"--rebaseline first.", file=sys.stderr)
                return 1
            content_copy_all = bool(prior.get("content_copy_all", content_copy_all))
        else:
            print(f"FATAL: --only needs an existing baseline to merge into, but "
                  f"{manifest_path} does not exist. Run a full --rebaseline first.",
                  file=sys.stderr)
            return 1
    elif content_dir.exists():
        # Full rebaseline still starts clean; a partial one must NOT, or it
        # would delete the content copies of every path it is not touching.
        shutil.rmtree(content_dir)

    files = {}
    copied = 0
    hash_only = 0
    for relpath in sorted(expected):
        full = os.path.join(root, relpath)
        if not os.path.isfile(full):
            files[relpath] = {"sha256": None, "size": None, "content_copied": False}
            continue
        sha = sha256_of(full)
        do_copy = content_copy_all or relpath.endswith(".md")
        if do_copy:
            dest = content_dir / relpath
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(full, dest)
            copied += 1
        else:
            hash_only += 1
        files[relpath] = {"sha256": sha, "size": sizes[relpath], "content_copied": do_copy}

    baseline_dir.mkdir(parents=True, exist_ok=True)
    touched = len(files)
    if only_prefixes:
        # Merge: every path this run did not name keeps the entry it already
        # had. The counts below are recomputed over the MERGED set so the
        # manifest keeps describing the whole baseline, not just this slice.
        merged = dict(prior.get("files") or {})
        merged.update(files)
        files = merged
        copied = sum(1 for e in files.values() if e.get("content_copied"))
        hash_only = sum(1 for e in files.values()
                        if e.get("sha256") is not None and not e.get("content_copied"))
        missing = [p for p, e in files.items() if e.get("sha256") is None]
        total_bytes = sum(e.get("size") or 0 for e in files.values())

    manifest = {
        "schema": 1,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "root": str(root),
        "total_bytes": total_bytes,
        "content_copy_cap_bytes": CONTENT_COPY_CAP_BYTES,
        "content_copy_all": content_copy_all,
        "file_count": len(files),
        "content_copied_count": copied,
        "hash_only_count": hash_only,
        "missing_count": len(missing),
        "files": files,
    }
    if only_prefixes:
        # Provenance, so a later reader can tell a merged baseline from a full
        # one without diffing timestamps.
        manifest["partial_rebaseline"] = {
            "prefixes": list(only_prefixes),
            "paths_touched": touched,
            "prior_generated_at": prior.get("generated_at"),
        }
    tmp = manifest_path.with_suffix(manifest_path.suffix + ".tmp")
    tmp.write_text(json.dumps(manifest, indent=2, sort_keys=True))
    tmp.replace(manifest_path)

    print(f"\nbaseline written: {manifest_path}")
    if only_prefixes:
        print(f"  {touched} path(s) re-snapshotted, "
              f"{len(files) - touched} carried forward untouched")
    print(f"  {copied} content-copied, {hash_only} hash-only, {len(missing)} missing")
    return 0


# ---------------------------------------------------------------- --check

def run_check(args):
    root = args.root
    manifest_path = Path(args.manifest)
    baseline_dir = Path(args.baseline_dir)
    baseline_content_dir = baseline_dir / "content"
    quarantine_root = Path(args.quarantine_dir)
    salvage_manifest_path = Path(args.salvage_manifest)

    if not os.path.isdir(root):
        print(f"FATAL: vault root not found or not a directory: {root}", file=sys.stderr)
        return 1

    run_id = args.run_id or (datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S.%f")[:-3] + "Z")

    expected, expected_err = load_expected_writers()
    if expected_err:
        # FAIL CLOSED, for the same reason --rebaseline does. This used to warn
        # and continue, and its own warning stated the consequence: "Everything
        # else will read UNEXPECTED until this import is fixed." That is not a
        # degraded check, it is a wrong one. `expected` IS the classifier, so a
        # check without the registry reclassifies every legitimate export target
        # as UNEXPECTED and emits dozens of false findings -- each of which
        # quarantines a copy, writes a salvage row, and, because bin/nightly.sh
        # runs this step with CARR_DRIFT_INGEST=1, POSTs to the ingest endpoint.
        # The run then exits 2 (findings), which reads as "drift detected"
        # rather than "the scanner was blind", and the only thing distinguishing
        # them is one stderr line nobody reads in a nightly log.
        #
        # This is the same defect the --rebaseline side already had and the same
        # one that produced the 3-path baseline on 2026-08-13T18:21Z. Fixing one
        # caller and leaving the other is how a class survives its own fix.
        print(f"FATAL: could not load exporters/targets.py's TARGETS registry "
              f"({expected_err}). Refusing to run a check whose classifier knows only "
              f"{len(ALWAYS_EXPECTED)} of the registered paths — every export target "
              f"would be reported as UNEXPECTED, quarantined and ingested. Run it the "
              f"way bin/nightly.sh does: ./.venv/bin/python ops/vault-drift-watch.py "
              f"--check", file=sys.stderr)
        return 1

    corpus_mirror = load_corpus_mirror_set()
    mirror_manifest, mirror_manifest_note = load_mirror_manifest(root)
    # Counts populated while classify() runs below (seed loop and
    # report_group alike) — never hardcoded, so "N mirror-verified" always
    # reflects what this run actually saw.
    mirror_stats = {"verified": 0, "regenerated": 0, "unexpected": 0}

    print(f"vault drift watch --check — {datetime.now(timezone.utc).isoformat()}")
    print(f"root: {root}")
    print(f"run id: {run_id}")

    # ---- Pillar 1: registry tamper check against the last --rebaseline ----
    baseline = load_baseline(baseline_dir)
    tamper_findings = []
    if baseline is None:
        print("\nregistry tamper check: NO BASELINE — run --rebaseline once (nightly's LAST "
              "step, after exports) to seed post-export hashes. Skipping the tamper comparison "
              "this run; there is nothing to compare against yet.")
    else:
        bfiles = baseline.get("files", {})
        baseline_age_h = (datetime.now(timezone.utc)
                           - datetime.fromisoformat(baseline["generated_at"])).total_seconds() / 3600
        print(f"\nregistry tamper check: baseline {baseline_age_h:.1f}h old, "
              f"{len(bfiles)} registered file(s)")
        # THE SECOND ORACLE (2026-08-14). The baseline alone cannot tell a
        # legitimate re-export from a tamper, because the nightly export is not
        # the only legitimate writer: the hourly rule refresh writes five of
        # these files, any session running a live export writes them, and every
        # render carries an `Exported:` timestamp so its bytes change on every
        # pass regardless. On 2026-08-14 that reported 30 TAMPERs whose entire
        # diff was that one timestamp line. export_run.file_sha records what the
        # exporter actually wrote, so it answers the question exactly; see
        # tamper_verdict for the rule and tools/test-drift-export-ledger.py for
        # the cases it must satisfy.
        ledger, ledger_note = load_export_ledger()
        if ledger_note:
            print(f"  export ledger UNAVAILABLE ({ledger_note}); a legitimate re-export "
                  f"since the baseline will read as TAMPER this run")
        else:
            print(f"  export ledger: {len(ledger)} target(s) carry a recorded write hash")
        reexported = []
        for relpath in sorted(bfiles):
            binfo = bfiles[relpath]
            if binfo.get("sha256") is None:
                continue  # wasn't on disk at rebaseline time either — nothing to compare
            full = os.path.join(root, relpath)
            cur_sha = sha256_of(full) if os.path.isfile(full) else None
            if cur_sha == binfo["sha256"]:
                continue
            reason = tamper_verdict(relpath, binfo["sha256"], cur_sha, ledger)
            if reason is None:
                reexported.append(relpath)
                continue
            tamper_findings.append({
                "path": relpath, "type": "tamper", "reason": reason,
                "baseline_sha256": binfo["sha256"], "current_sha256": cur_sha,
            })
        if reexported:
            print(f"  {len(reexported)} registered file(s) re-exported since the baseline "
                  f"(bytes match the exporter's own record — not tamper): "
                  + ", ".join(reexported[:6])
                  + (f", +{len(reexported) - 6} more" if len(reexported) > 6 else ""))
        if tamper_findings:
            print(f"  {len(tamper_findings)} TAMPERED file(s):")
            for tf in tamper_findings:
                print(f"    TAMPER  {tf['path']}  ({tf['reason']})")
        else:
            print("  no tamper detected — every registered file matches either its "
                  "post-export baseline hash or the exporter's own record of writing it")
        new_since_baseline = sorted(p for p in expected if p not in bfiles
                                     and os.path.isfile(os.path.join(root, p)))
        if new_since_baseline:
            print(f"  {len(new_since_baseline)} registered file(s) not yet in any baseline "
                  f"(will be captured at the next --rebaseline, not tamper): "
                  + ", ".join(new_since_baseline))

    # ---- structural .md pass: add/modify/delete vs the previous --check manifest ----
    new = scan(root)
    seeding = not manifest_path.exists()
    old = {}
    if not seeding:
        try:
            old_data = json.loads(manifest_path.read_text())
            old = old_data.get("files", {})
        except Exception as exc:
            print(f"⚠︎ could not read previous check manifest at {manifest_path} "
                  f"({exc}) — treating this run as a reseed rather than guessing at "
                  f"drift against unreadable state.", file=sys.stderr)
            seeding = True
            old = {}

    print(f"\ntracked .md files: {len(new)}")
    if mirror_manifest_note:
        print(f"\nportability mirror verification: {mirror_manifest_note} — every path under "
              f"{MIRROR_PREFIX} will read UNEXPECTED until a mirror run writes one")
    else:
        print(f"\nportability mirror verification: {MIRROR_PREFIX}manifest.json loaded, "
              f"{len(mirror_manifest)} file(s) registered by the mirror's own last run")

    def classify_tracking(p, current_sha):
        """classify() plus mirror_stats bookkeeping — one call site for both
        the seed loop and report_group so the counts can never drift apart
        from what was actually printed."""
        tag = classify(p, expected, corpus_mirror, current_sha=current_sha,
                        mirror_manifest=mirror_manifest)
        if p.startswith(MIRROR_PREFIX):
            if tag.startswith("expected-writer: mirror-verified"):
                mirror_stats["verified"] += 1
            elif tag.startswith("expected-writer: mirror-regenerated"):
                mirror_stats["regenerated"] += 1
            elif tag == "UNEXPECTED":
                mirror_stats["unexpected"] += 1
        return tag

    unexpected_entries = []
    if seeding:
        print(f"\nSEED RUN for the .md structural manifest — no previous check manifest at "
              f"{manifest_path}. All {len(new)} file(s) report ADDED; this is expected seed "
              f"behavior, not drift.")
        for p in sorted(new):
            print(f"  ADDED  {classify_tracking(p, new[p]['sha256']):<55} {p}")
    else:
        added = sorted(p for p in new if p not in old)
        deleted = sorted(p for p in old if p not in new)
        modified = sorted(p for p in new if p in old and new[p]["sha256"] != old[p]["sha256"])

        staged_for_deletion = staged_basenames(root)

        def report_group(label, paths):
            if not paths:
                print(f"\n{label}: none")
                return
            print(f"\n{label}: {len(paths)}")
            for p in paths:
                cur_sha = new.get(p, {}).get("sha256")
                tag = classify_tracking(p, cur_sha)
                # A file that left its home and now sits under _to_delete/ was
                # RETIRED by a sanctioned process, not deleted. DELETED only:
                # an added or modified file is untouched by this, and a file
                # that vanished with no staged copy stays UNEXPECTED, which is
                # the real signal this alarm exists for.
                if (label == "DELETED" and tag == "UNEXPECTED"
                        and os.path.basename(p) in staged_for_deletion):
                    tag = "RETIRED: staged in _to_delete"
                print(f"  {tag:<55} {p}")
                if tag == "UNEXPECTED":
                    if p.startswith(MIRROR_PREFIX):
                        reason = mirror_finding_reason(p, cur_sha, mirror_manifest)
                    else:
                        reason = f"{label.lower()} — non-registry, non-corpus-mirror .md file"
                    unexpected_entries.append((label, p, reason))

        report_group("ADDED", added)
        report_group("MODIFIED", modified)
        report_group("DELETED", deleted)

    if mirror_stats["verified"] or mirror_stats["regenerated"] or mirror_stats["unexpected"]:
        print(f"\nportability mirror: {mirror_stats['verified']} mirror-verified (sha256 matches "
              f"the mirror's own manifest.json — sanctioned regeneration, not counted as "
              f"UNEXPECTED), {mirror_stats['regenerated']} mirror-regenerated (retired between "
              f"runs, not tamper), {mirror_stats['unexpected']} unexpected (still flagged)")

    write_manifest(manifest_path, root, new, unexpected_count=len(unexpected_entries), seed=seeding)

    unexpected_findings = []
    for label, p, reason in unexpected_entries:
        unexpected_findings.append({
            "path": p, "type": "unexpected",
            "reason": reason,
            "baseline_sha256": old.get(p, {}).get("sha256"),
            "current_sha256": new.get(p, {}).get("sha256"),
        })

    if unexpected_findings:
        print(f"\n{'=' * 78}\nUNEXPECTED VAULT MARKDOWN CHANGE(S) DETECTED".center(78))
        print("=" * 78)
    findings = tamper_findings + unexpected_findings

    # ---- Pillar 3: salvage-then-heal ----
    ingest_enabled = os.environ.get("CARR_DRIFT_INGEST") == "1"
    ingest_env = load_ingest_env()
    ingest_url = ingest_env.get("CARR_INGEST_URL", DEFAULT_INGEST_URL)
    ingest_token = ingest_env.get(INGEST_TOKEN_VAR, "")

    if findings:
        qroot = os.path.join(str(quarantine_root), run_id)
        os.makedirs(qroot, exist_ok=True)
        print(f"\n{'=' * 78}")
        print(f"SALVAGE-THEN-HEAL — {len(findings)} finding(s), quarantining to {qroot}")
        print("=" * 78)
        for f_ in findings:
            salvage_finding(f_, root,
                             str(baseline_content_dir) if baseline is not None else None,
                             qroot, run_id, str(salvage_manifest_path),
                             ingest_enabled, args.dry_run_ingest, ingest_url, ingest_token)
        print(f"\nsalvage manifest: {salvage_manifest_path}")
        print(f"quarantine dir:   {qroot}")
    else:
        print("\nno findings — nothing to salvage")

    if not ingest_enabled:
        print(f"\ningest: disabled by default (set CARR_DRIFT_INGEST=1 to enable live routing "
              f"to {ingest_url}) — manifest + quarantine + loud health flag only")
    elif args.dry_run_ingest:
        print("\ningest: CARR_DRIFT_INGEST=1 and --dry-run-ingest set — payloads built and "
              "printed, no network call made")

    # ---- summary for tools/health-check.py ----
    summary = {
        "schema": 1,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "run_id": run_id,
        "tamper_count": len(tamper_findings),
        "unexpected_count": len(unexpected_findings),
        "total_findings": len(findings),
        "ingest_enabled": ingest_enabled,
        "baseline_present": baseline is not None,
        "mirror_verified_count": mirror_stats["verified"],
        "mirror_regenerated_count": mirror_stats["regenerated"],
        "mirror_unexpected_count": mirror_stats["unexpected"],
    }
    summary_path = Path(args.summary)
    summary_path.parent.mkdir(parents=True, exist_ok=True)
    tmp = summary_path.with_suffix(summary_path.suffix + ".tmp")
    tmp.write_text(json.dumps(summary, indent=2, sort_keys=True))
    tmp.replace(summary_path)

    if findings:
        print(f"\n{len(tamper_findings)} TAMPER + {len(unexpected_findings)} UNEXPECTED — exit 2")
        return 2
    print("\nclean — 0 tamper, 0 unexpected")
    return 0


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__,
                                  formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--root", default=DEFAULT_VAULT,
                     help="Vault root to scan. No default: naming a root runs normally, "
                          "omitting it means the REAL vault and requires --recovery --reason.")
    add_recovery_arguments(ap)
    ap.add_argument("--manifest", default=str(DEFAULT_MANIFEST),
                     help="Structural .md check-to-check manifest "
                          "(default: out/vault-md-manifest.json)")
    ap.add_argument("--baseline-dir", default=str(DEFAULT_BASELINE_DIR),
                     help="--rebaseline snapshot dir (default: out/vault-drift-baseline)")
    ap.add_argument("--quarantine-dir", default=str(DEFAULT_QUARANTINE_DIR),
                     help="Per-run quarantine root (default: out/vault-drift-quarantine)")
    ap.add_argument("--salvage-manifest", default=str(DEFAULT_SALVAGE_MANIFEST),
                     help="Append-only salvage intake log "
                          "(default: out/vault-drift-salvage-manifest.jsonl)")
    ap.add_argument("--summary", default=str(DEFAULT_SUMMARY),
                     help="Machine-readable last-check summary for health-check.py "
                          "(default: out/vault-drift-check-summary.json)")
    ap.add_argument("--check", action="store_true",
                     help="Tamper + structural drift check (run FIRST, before exports)")
    ap.add_argument("--rebaseline", action="store_true",
                     help="Snapshot the post-export registry state (run LAST, after exports)")
    ap.add_argument("--only-target", default=None, metavar="EXPORT_KEY_PREFIX",
                     help="With --rebaseline: re-snapshot exactly the paths written by "
                          "`run.sh export --only <EXPORT_KEY_PREFIX>`, resolved through "
                          "exporters/targets.py so the two selections cannot drift apart. "
                          "Preferred over --only for anything that shadows an export.")
    ap.add_argument("--only", default=None, metavar="PREFIX[,PREFIX...]",
                     help="With --rebaseline: re-snapshot ONLY registered paths starting "
                          "with one of these comma-separated prefixes, MERGING them into "
                          "the existing baseline and leaving every other path's baseline "
                          "untouched. Without it, --rebaseline re-snapshots the whole registry.")
    ap.add_argument("--dry-run-ingest", action="store_true",
                     help="With --check and CARR_DRIFT_INGEST=1: build and print payloads, "
                          "never actually POST. No effect when CARR_DRIFT_INGEST is unset.")
    ap.add_argument("--run-id", default=None,
                     help="Override the run id used for the quarantine folder name and "
                          "salvage payloads (default: current UTC timestamp). Tests only.")
    args = ap.parse_args(argv)
    if not args.root:
        try:
            args.root = str(require_recovery(
                args,
                "record-native drift detection (this watches the vault's own file tree)"))
        except ValueError as exc:
            print(f"MISSING_CANONICAL_SEAM: {exc}", file=sys.stderr)
            return SEAM_MISSING_EXIT

    if (args.only or args.only_target) and args.check:
        print("FATAL: --only/--only-target apply to --rebaseline only; --check always "
              "scans the whole registry.", file=sys.stderr)
        return 1
    if args.only and args.only_target:
        print("FATAL: --only and --only-target are two ways to name the same slice — "
              "pass one. Prefer --only-target when the slice shadows an export.",
              file=sys.stderr)
        return 1
    if args.check and args.rebaseline:
        print("FATAL: --check and --rebaseline are mutually exclusive — run them as two "
              "separate steps (--check first, --rebaseline last).", file=sys.stderr)
        return 1
    if not args.check and not args.rebaseline:
        args.check = True
        print("(no mode flag given — defaulting to --check; pass --rebaseline explicitly "
              "to snapshot the post-export baseline)")

    if args.rebaseline:
        return run_rebaseline(args)
    return run_check(args)


if __name__ == "__main__":
    sys.exit(main())
