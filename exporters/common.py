"""Exporter gate machinery (addendum A8) — 2026-07-30 build session.

Every exporter runs the same poison gate:
  build to TEMP -> validate (headers + row tolerance) -> keep generation ->
  atomic rename -> export_run row with a CANONICAL-DATA checksum.
A failed validation leaves the previous good file untouched and writes a
validation_failed run row: the alarm layer (digest dead-man at 26h) sees it.

DRAFT vs LIVE: exporters write to the draft dir (repo out/exports) unless
CARR_EXPORT_LIVE=1, which sends them to EXPORT_HOME — CARR's OneDrive since
Joe's ruling of 2026-08-22. It is no longer the Drive vault, which the
2026-08-19 cutoff retired.

Credential: CARR_DB_EXPORTER_URL from ~/.config/carr/db.env (carr_exporter
bundle: export views + export_run + system_config, nothing else).
"""

import errno
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from lib.credential_file import credential  # noqa: E402

import psycopg

REPO = Path(__file__).resolve().parent.parent
DRAFT = REPO / "out" / "exports"
VAULT = Path(os.environ.get("CARR_VAULT") or "/Users/booko/Library/CloudStorage/GoogleDrive-joe.bookout.carr.us@gmail.com/My Drive/CARR AI")

# WHERE THE LIVE FILES GO, ruled by Joe on 2026-08-22: CARR's own OneDrive.
#
# THE PROBLEM THIS SOLVES. The 2026-08-19 cutoff retired the Drive vault, and
# these exports were still addressed to it. They did not break — they build
# cleanly in seconds — they simply had nowhere to land, so thirteen nightly
# steps refused every night for want of a destination and the chain finished
# red. A chain that is always red is one nobody reads.
#
# WHY ONEDRIVE RATHER THAN A LOCAL FOLDER. These seven are the files a PERSON
# opens and hands to somebody: the client roster, the leads and vendor lists,
# the deal export. That is the one thing the store cannot do for them, and it
# is why they survived a cutoff that retired 38 generated documents. CARR's
# OneDrive is already where this system puts finished business documents —
# pipelines/backfill_document_attachments.py has written completed deal
# documents to Joe's Folder/Deals/Active Deals for months — so this puts the
# projections beside the documents rather than inventing a second home.
#
# READ THE FILE BACK BEFORE BELIEVING IT LANDED. OneDrive, like Google Drive
# File Stream before it, can serve a synced file as an online-only placeholder,
# so a path that exists is not proof that content does. The exporter's own
# checksum step reads the finished file, which is what makes a placeholder show
# up as a failure here rather than as a silently empty projection downstream.
EXPORT_HOME = Path(os.environ.get("CARR_EXPORT_HOME")
                   or "/Users/booko/Library/CloudStorage/OneDrive-CARR,Inc/Joe's Folder/CARR AI")
LIVE = os.environ.get("CARR_EXPORT_LIVE") == "1"
KEEP_GENERATIONS = 7
# The retry budget below is sized against a OneDrive hydration stall, not a
# scheduler hiccup.  It shipped on 2026-08-25 as 3 attempts over 0.05s + 0.10s,
# and on 2026-08-26 the curriculum export died the same EDEADLK death anyway:
# a sixth of a second is indistinguishable from no retry when the FileProvider
# is materialising a dehydrated cloud file.  That night the step began at
# 07:05:13Z and failed at 07:05:15Z, while the same file read by hand two
# minutes later succeeded on the first try.  Seconds, not milliseconds.
#
# Keep ATTEMPTS == len(BACKOFF_SECONDS) + 1: the retry loops index
# BACKOFF_SECONDS[attempt] on every attempt but the last, so a mismatch turns
# the retry into an IndexError raised from inside the error handler.
# ops/export-generation-retry-selftest.py pins both the total wall-clock budget
# and that relationship, because a budget is the part of a retry that silently
# regresses to a value which still looks like a retry.
#
# ETIMEDOUT JOINED THE SET ON 2026-09-15, AND IT IS THE ONE THAT WAS ACTUALLY
# FIRING. Measured on this Mac at 16:35Z against the six live export files:
# every one of them is a dehydrated OneDrive placeholder (st_blocks == 0), so
# the nightly's FIRST read of each is a cold cloud download. That read returned
# errno 60, ETIMEDOUT -- not EAGAIN and not EDEADLK -- which was NOT in this set,
# so it escaped on attempt one and the backoff below was never entered at all.
# Six straight nights (2026-09-10 through 09-15) lost all six targets that way,
# twelve nights running lost at least one, while every by-hand run in the same
# span succeeded.
#
# THE TIMEOUT IS ITSELF THE FIX'S EVIDENCE: the failed read is what asks the
# FileProvider to hydrate the file, so the very next read returns instantly.
# Measured in the same session -- attempt 1 ETIMEDOUT after 1.04s, attempts 2
# through 6 OK in 0.00s. One retry would have carried it. The earlier reading of
# this failure, that the budget was being exhausted, was wrong in the other
# direction and is corrected here: the budget was never entered.
#
# EACCES and friends stay non-retryable on purpose. A permission, path or
# storage failure is not a hydration stall and must reach the exporter.
GENERATION_COPY_ATTEMPTS = 6
GENERATION_COPY_RETRY_ERRNOS = frozenset({errno.EAGAIN, errno.EDEADLK, errno.ETIMEDOUT})
GENERATION_COPY_BACKOFF_SECONDS = (0.5, 1.0, 2.0, 5.0, 15.0)

# 20 minutes, against a measured 8-10 minute provider outage after a scheduled
# dark wake. Long enough to cover roughly double the worst observed wait, short
# enough that a genuinely dead provider is reported the same night rather than
# hanging until morning. Polling faster than 20s buys nothing from something
# that takes minutes, and only multiplies the log.
PROVIDER_WAIT_BUDGET_SECONDS = float(os.environ.get("CARR_PROVIDER_WAIT_SECONDS", 1200))
PROVIDER_WAIT_POLL_SECONDS = float(os.environ.get("CARR_PROVIDER_POLL_SECONDS", 20))
# Materialization is per FILE, not per byte: the provider must fetch the content
# before it can answer the first read at all, so one chunk proves what a full
# read proves at a fraction of the cost on a 9320-row workbook.
PROVIDER_PROBE_BYTES = 1 << 16

# WAITING FOR A PROVIDER THAT IS NOT RUNNING, measured 2026-09-22. The 09-22
# nightly is the first that reached the end of the budget above instead of being
# killed by the step timeout, and what it proved is that the wait itself was
# never the binding constraint: 60 poll lines over the full 1200s, no file ever
# warming, then EDEADLK on all six targets. The cause was not a slow provider.
# `ps -Ao lstart` that afternoon put OneDrive.app's start at 08:55:58 local,
# three seconds after the session's "Display is turned on" event and nearly
# SEVEN HOURS after the exports step ran at 02:05:29 local. OneDrive is a GUI
# login item, so nothing had it running in the 02:05 window; every read of a
# dehydrated placeholder answers EDEADLK forever when no provider exists to
# service it. The same six files read in ~2s each once the client was up.
#
# After a bounded file read finds a hydration failure, the wait asks whether
# anything is there to wait for and records the observation. When absent it makes ONE
# attempt to start it (`open -gj -a OneDrive`, background and hidden: a no-op
# when it is already running) and keeps waiting, so a night that would have lost
# six targets can recover on its own. When the launch does not take, the budget
# is not burned in silence: the message records the census and launch result
# alongside the read errors, without asserting the cause of those errors.
#
# The check is a process probe, not an API call, because there is no supported
# way to ask a File Provider extension whether it is up, and because a probe
# that needs the provider in order to test the provider cannot report its
# absence. Both names are matched: the appex services the reads, the app is
# what `open` starts.
# Match executable basenames, including the end delimiter: OneDriveUpdater is
# an updater, not a process that can service file reads.
PROVIDER_PROCESS_PATTERNS = (
    r"^/Applications/OneDrive\.app/Contents/PlugIns/OneDrive File Provider\.appex/Contents/MacOS/OneDrive File Provider($| )",
    r"^/Applications/OneDrive\.app/Contents/MacOS/OneDrive($| )",
)
PROVIDER_LAUNCH_COMMAND = ("open", "-gj", "-a", "OneDrive")
PROVIDER_LAUNCH_TIMEOUT_SECONDS = 30.0
PROVIDER_PROCESS_TIMEOUT_SECONDS = 2.0
PROVIDER_FILE_TIMEOUT_SECONDS = 5.0


def provider_running(run=subprocess.run, *, timeout=PROVIDER_PROCESS_TIMEOUT_SECONDS):
    """Return observed presence, absence, or None for an unsuccessful census.

    Both pgrep invocations share one timeout. Only status 1 proves no match;
    missing tools, errors and timeouts cannot establish absence.
    """
    if timeout <= 0 or shutil.which("pgrep") is None:
        return None
    deadline = time.monotonic() + min(timeout, PROVIDER_PROCESS_TIMEOUT_SECONDS)
    unknown = False
    for pattern in PROVIDER_PROCESS_PATTERNS:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            return None
        try:
            done = run(["pgrep", "-f", pattern], capture_output=True, text=True,
                       timeout=remaining)
        except (OSError, subprocess.SubprocessError):
            return None
        if done.returncode == 0 and done.stdout.strip():
            return True
        if done.returncode != 1:
            unknown = True
    return None if unknown else False


def start_provider(run=subprocess.run, *, timeout=PROVIDER_LAUNCH_TIMEOUT_SECONDS):
    """Request a background launch within available time; never raise."""
    if timeout <= 0:
        return False
    try:
        done = run(list(PROVIDER_LAUNCH_COMMAND), capture_output=True, text=True,
                   timeout=min(timeout, PROVIDER_LAUNCH_TIMEOUT_SECONDS))
    except (OSError, subprocess.SubprocessError):
        return False
    return done.returncode == 0


# Isolate existence checks as well as open/read: any of them can wedge inside
# FileProvider. subprocess.run kills and reaps the child on timeout, leaving no
# blocked threads or file handles in the exporter process.
_PROVIDER_FILE_PROBE = """
import json, sys
from pathlib import Path
cold = []
for index, name in enumerate(json.loads(sys.argv[1])):
    try:
        with Path(name).open('rb') as stream:
            stream.read(int(sys.argv[2]))
    except FileNotFoundError:
        pass
    except OSError as error:
        cold.append([index, error.errno, str(error)])
print(json.dumps(cold))
"""


def probe_provider_files(paths, *, timeout):
    """Return unreadable files from a bounded batch; missing files need no warm-up."""
    try:
        done = subprocess.run(
            [sys.executable, "-c", _PROVIDER_FILE_PROBE,
             json.dumps([str(p) for p in paths]), str(PROVIDER_PROBE_BYTES)],
            capture_output=True, text=True, timeout=timeout)
        if done.returncode != 0:
            raise OSError(errno.EIO, "file probe failed")
        return [(paths[i], OSError(code, message))
                for i, code, message in json.loads(done.stdout)]
    except subprocess.TimeoutExpired:
        return [(p, OSError(errno.ETIMEDOUT, "file probe exceeded its deadline")) for p in paths]
    except (OSError, subprocess.SubprocessError, ValueError, IndexError, TypeError) as error:
        return [(p, OSError(errno.EIO, f"file probe failed: {error}")) for p in paths]


@dataclass
class ProviderWaitResult:
    """Errors and observations from one wait, without a later process census."""
    cold: list[tuple[Path, OSError]]
    provider_state: bool | None = None
    launch_succeeded: bool | None = None

    def diagnostic(self):
        errors = ", ".join(sorted({errno.errorcode.get(e.errno, str(e.errno)) for _, e in self.cold}))
        state = {True: "last observed up", False: "last observed absent", None: "state unknown"}[self.provider_state]
        launch = {True: "launch attempt issued", False: "launch attempt failed",
                  None: "no launch attempted"}[self.launch_succeeded]
        return f"read errors: {errors}; provider {state}; {launch}"


def connect():
    # db.env is read through lib/credential_file, the one Python reader of it.
    # Its values are shell-quoted so `set -a; . db.env` survives an `&` in the
    # DSN; on 2026-08-02 a hand-rolled parser here fed psycopg the quotes and
    # blinded the export register. The shared reader unquotes as the shell does.
    url = credential("CARR_DB_EXPORTER_URL")
    if not url:
        sys.exit("no CARR_DB_EXPORTER_URL (see ~/.config/carr/db.env)")
    return psycopg.connect(url)


def config(cur, key, default):
    cur.execute("select value from system_config where key = %s", (key,))
    row = cur.fetchone()
    return row[0] if row else default


def canonical_checksum(rows):
    """sha256 of extracted DATA (never file bytes — openpyxl restamps metadata)."""
    blob = json.dumps(rows, default=str, sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(blob.encode()).hexdigest()


def last_ok_count(cur, target):
    cur.execute("select row_count from export_run where target=%s and status='ok' "
                "order by ran_at desc limit 1", (target,))
    row = cur.fetchone()
    return row[0] if row else None


def record_run(cur, target, row_count, checksum, status, file_sha=None):
    # file_sha (0073, wave 1): sha256 of the WRITTEN FILE's bytes. The data
    # checksum proves what the DB said; this proves what the file was, so
    # ops/renders-verify.py can detect a render edited by anything other than
    # the exporter (the V-BNK-050 clobber class). Nullable: failure paths and
    # pre-0073 rows carry none. Written via a column-tolerant INSERT so this
    # code deploys before the migration lands without breaking exports.
    try:
        cur.execute("insert into export_run (target,row_count,checksum,status,file_sha) "
                    "values (%s,%s,%s,%s,%s)", (target, row_count, checksum, status, file_sha))
    except Exception:
        cur.connection.rollback()
        cur.execute("insert into export_run (target,row_count,checksum,status) "
                    "values (%s,%s,%s,%s)", (target, row_count, checksum, status))


# ---------------- the coverage note (absence is not absence, at the render) ----------------
#
# This system has read an empty cell as a fact five separate times: a lookup verb
# that invented an absence, an open-deals-only JSON read as the whole book,
# `stale-records` needing its own docstring to warn its caller that empty may mean
# nothing-captured, email silence treated as a cold prospect, and a grep too narrow
# to support what it was denying. Every one of those was caught after the fact.
#
# The renders are where the misreading actually starts, because a blank cell in a
# vault file looks exactly like a recorded zero to whoever opens it, and one of the
# two people opening these is newer to the system than the other. So each render
# carrying an absence-ambiguous column states its own coverage, in plain language,
# in the file, next to the column.
#
# WHICH columns are absence-ambiguous is a judgment and is authored per target: most
# blanks are ordinary (a vendor with no Notes is a vendor with no notes), and a note
# on every sparse column would be noise nobody reads. THE NUMBERS ARE NEVER AUTHORED.
# They are counted from the rows being written, on every run. That is not a style
# preference: the audit of 2026-08-02 measured vendor Last Touch at 0 of 290, and by
# the time this shipped the export view was already deriving 2. A number typed into a
# file would have shipped stale on day one. A column at full coverage drops out of
# the note entirely, so the note empties itself as the book fills in, and nobody has
# to remember to go delete it.


def _has_value(v):
    """Blank is blank: None, empty string and whitespace-only all count as unrecorded.

    NOT `is not None`. A view that renders '' and a view that renders NULL look the
    same to the reader, and the reader is who this number is for.
    """
    return v is not None and str(v).strip() != ""


def coverage_findings(rows, cols, watched):
    """[(column, filled, total, blurb)] for each watched column short of full coverage.

    `watched` maps a column name to the misreading it invites, phrased as what a
    blank does NOT tell you. `rows`/`cols` are the rows and header being written, so
    the count describes the file the partners open rather than some wider query
    sitting behind it. A watched column the render does not carry is skipped, which
    is what lets two targets share one watch list.
    """
    total = len(rows)
    if not total:
        return []
    found = []
    for col, blurb in watched.items():
        if col not in cols:
            continue
        i = cols.index(col)
        filled = sum(1 for r in rows if _has_value(r[i]))
        if filled < total:
            found.append((col, filled, total, blurb))
    return found


def _pct(filled, total):
    return round(filled / total * 100)


def coverage_note_md(rows, cols, watched, noun):
    """The markdown block for a rendered table, or [] when every watched column is full."""
    findings = coverage_findings(rows, cols, watched)
    if not findings:
        return []
    bullets = [f"- **{col}**: recorded on {filled} of {total} {noun} ({_pct(filled, total)}%). "
               f"A blank tells you nothing about {blurb}."
               for col, filled, total, blurb in findings]
    return [
        "",
        "### Coverage note",
        "",
        "Blanks in the columns listed here mean nobody has recorded a value yet. Read them",
        "as unknown.",
        "",
        *bullets,
        "",
        "*Counted from the rows above on every export. A column leaves this list once every",
        "row has a value.*",
    ]


def coverage_note_cell(finding, noun):
    """The same finding as one flat sentence, sized for a spreadsheet cell comment."""
    col, filled, total, blurb = finding
    return (f"Coverage note (generated). {col} is recorded on {filled} of {total} {noun} "
            f"({_pct(filled, total)}%). A blank tells you nothing about {blurb}. "
            f"Recounted on every export.")


def _generation_destinations(gen_dir: Path, final_path: Path, stamp: str):
    """Yield the bounded same-second name space, oldest-compatible name first."""
    yield gen_dir / f"{stamp}-{final_path.name}"
    for sequence in range(1, 100):
        yield gen_dir / f"{stamp}-{sequence:02d}-{final_path.name}"


def wait_for_provider(paths, budget_seconds=None, poll_seconds=None, sleep=time.sleep,
                      running=provider_running, launch=start_provider,
                      probe=probe_provider_files, monotonic=time.monotonic):
    """Warm existing files within one deadline, returning errors and observations.

    Warm or missing files never trigger a launch. Non-transient read errors
    return immediately. Cold reads, process census, launch and sleep all spend
    the same budget; failures remain observations so every export target runs.
    """
    budget = PROVIDER_WAIT_BUDGET_SECONDS if budget_seconds is None else budget_seconds
    poll = max(PROVIDER_WAIT_POLL_SECONDS if poll_seconds is None else poll_seconds, 0.01)
    paths = list(paths)
    result = ProviderWaitResult([])
    if not paths:
        return result
    deadline = monotonic() + max(budget, 0)
    # Reserve time for recovery even when the first file probe consumes its
    # entire allowance. This preserves the warm-file no-launch path.
    while True:
        remaining = deadline - monotonic()
        if remaining <= 0:
            if not result.cold:
                result.cold = [(p, OSError(errno.ETIMEDOUT, "warm-up budget exhausted")) for p in paths]
            return result
        result.cold = probe(paths, timeout=min(PROVIDER_FILE_TIMEOUT_SECONDS, remaining / 2))
        if not result.cold or any(e.errno not in GENERATION_COPY_RETRY_ERRNOS for _, e in result.cold):
            return result
        remaining = deadline - monotonic()
        if remaining <= 0:
            return result
        try:
            result.provider_state = running(timeout=min(PROVIDER_PROCESS_TIMEOUT_SECONDS, remaining))
        except (OSError, subprocess.SubprocessError):
            result.provider_state = None
        remaining = deadline - monotonic()
        if result.provider_state is False and result.launch_succeeded is None and remaining > 0:
            try:
                result.launch_succeeded = launch(timeout=min(PROVIDER_LAUNCH_TIMEOUT_SECONDS, remaining))
            except (OSError, subprocess.SubprocessError):
                result.launch_succeeded = False
            print(f"[provider] {result.diagnostic()}", file=sys.stderr, flush=True)
        remaining = deadline - monotonic()
        if remaining <= 0:
            return result
        names = ", ".join(sorted(path.name for path, _e in result.cold))
        print(f"[provider] {len(result.cold)} unreadable file(s) ({names}); "
              f"{result.diagnostic()}; {remaining:.0f}s left before exports start anyway",
              file=sys.stderr, flush=True)
        sleep(min(poll, remaining))


class _PublishedGenerationCleanupError(RuntimeError):
    """The generation is durable; cleanup failed and must not trigger a recopy."""


def _remove_generation_temporary(temporary: Path) -> None:
    """Remove a staged file, retrying only the two FileProvider transient errors."""
    for attempt in range(GENERATION_COPY_ATTEMPTS):
        try:
            temporary.unlink(missing_ok=True)
            return
        except OSError as error:
            if error.errno not in GENERATION_COPY_RETRY_ERRNOS:
                raise
            if attempt + 1 == GENERATION_COPY_ATTEMPTS:
                raise
            time.sleep(GENERATION_COPY_BACKOFF_SECONDS[attempt])


def _write_generation_attempt(source: Path, gen_dir: Path, stamp: str) -> Path:
    """Stage one byte-for-byte generation, then atomically publish without clobber."""
    fd, temporary_name = tempfile.mkstemp(prefix=f".{stamp}-{source.name}.", dir=gen_dir)
    temporary = Path(temporary_name)
    published = None
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(source.read_bytes())
        # link(2) is the no-clobber publication primitive: EEXIST means another
        # run won this name after we began staging, so allocate the next name.
        for destination in _generation_destinations(gen_dir, source, stamp):
            try:
                os.link(temporary, destination)
                published = destination
                return destination
            except FileExistsError:
                continue
        first = gen_dir / f"{stamp}-{source.name}"
        raise FileExistsError(errno.EEXIST, "generation-name space exhausted", first)
    finally:
        try:
            _remove_generation_temporary(temporary)
        except OSError as error:
            if published is not None:
                raise _PublishedGenerationCleanupError(
                    f"generation published at {published}, but staging cleanup failed"
                ) from error
            raise


def keep_generation(final_path: Path):
    """Keep an atomic dated copy of the file about to be replaced.

    OneDrive's FileProvider can transiently return EAGAIN/EDEADLK while reading
    or writing a cloud-backed file.  Retry only those two documented errors,
    with a short bounded backoff.  All other errors (and an exhausted transient
    retry budget) escape: a permission, path, or storage failure must be seen by
    the exporter rather than silently discarding the rollback guarantee.

    The copy deliberately avoids shutil.copy2/copyfile, whose macOS fcopyfile
    fast path was the source of the 2026-08-25 EDEADLK.  The timestamp in the
    generation name is its provenance, so copying source metadata is not part of
    this contract.
    """
    if not final_path.exists():
        return
    gen_dir = final_path.parent / (final_path.name + ".generations")
    gen_dir.mkdir(exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")

    for attempt in range(GENERATION_COPY_ATTEMPTS):
        try:
            _write_generation_attempt(final_path, gen_dir, stamp)
            break
        except OSError as error:
            if error.errno not in GENERATION_COPY_RETRY_ERRNOS:
                raise
            if attempt + 1 == GENERATION_COPY_ATTEMPTS:
                raise
            time.sleep(GENERATION_COPY_BACKOFF_SECONDS[attempt])

    gens = sorted(gen_dir.iterdir())
    for old in gens[:-KEEP_GENERATIONS]:
        old.unlink()


def publish_export(tmp_path: Path, final_path: Path, live: bool | None = None):
    """Publish one built export atomically. Returns (published, file_sha, error).

    `error` carries the publication failure when published is False, and the
    tamper-hash failure (a landed file whose hash could not be read) when it is
    True. It is None on a clean publication.

    SEPARATED FROM run_export SO IT CAN FAIL AS ONE TARGET AND BE PROVED WITHOUT A
    DATABASE. On 2026-09-01 the nightly's first target raised EDEADLK out of
    keep_generation while reading the previous OneDrive copy; the OSError went
    through run_export, through run_exports.main, and killed the process. The
    caller now catches per target, but two things were still missing when a
    publication fails: an `export_run` row saying so (the receipt `run.sh health`
    reads), and removal of the staged .tmp left sitting in the vault.

    THE ORDER IS THE SAFETY PROPERTY. keep_generation runs BEFORE os.replace, so
    a failure to keep the dated copy means the previous good file was never
    overwritten — the rollback guarantee is preserved by refusing to publish, not
    by publishing and hoping.

    The tamper hash is deliberately NOT allowed to fail the target: it is a second
    cloud read of a file that has ALREADY landed, so losing a good export over the
    detector would be the wrong trade. It comes back as sha_error instead.
    """
    if live is None:
        live = LIVE
    try:
        keep_generation(final_path)
        os.replace(tmp_path, final_path)
    except Exception as error:
        if tmp_path.exists():
            tmp_path.unlink()
        return False, None, error
    if not live:
        return True, None, None
    try:
        return True, hashlib.sha256(final_path.read_bytes()).hexdigest(), None
    except OSError as error:
        return True, None, error


def run_export(target_key, live_rel_path, build_fn, bootstrap=False):
    """build_fn(tmp_path, cur) -> (row_count, canonical_rows). Returns True on ok."""
    dest_dir = (EXPORT_HOME / live_rel_path).parent if LIVE else DRAFT
    dest_dir.mkdir(parents=True, exist_ok=True)
    final_path = (EXPORT_HOME / live_rel_path) if LIVE else DRAFT / Path(live_rel_path).name
    tmp_path = final_path.with_name(final_path.name + ".tmp")

    with connect() as conn, conn.cursor() as cur:
        try:
            row_count, canonical = build_fn(tmp_path, cur)
        except Exception as e:
            record_run(cur, target_key, 0, "build_error", "failed")
            conn.commit()
            print(f"[{target_key}] BUILD FAILED: {e}", file=sys.stderr)
            if tmp_path.exists():
                tmp_path.unlink()
            return False

        checksum = canonical_checksum(canonical)
        prev = last_ok_count(cur, target_key)
        tol = float(config(cur, "export.row_tolerance_pct", 5))
        # Small files break percent gates: 2 rules -> 3 is 50% "drift" but normal
        # growth. An absolute floor lets small deltas through regardless of percent.
        abs_floor = float(config(cur, "export.row_tolerance_abs", 3))
        # ASYMMETRIC BY DIRECTION, 2026-08-03. This guard exists to stop a
        # truncated or half-built export from overwriting a good file. That is
        # DATA LOSS, and data loss always shows up as rows going DOWN. Growth is
        # the opposite signal: on an accumulator (open-loops-backlog, the
        # record-layer dictionary) it is just the week's work landing. One
        # symmetric 5% gate treated both the same and FROZE open-loops-backlog.md
        # for two days across three nightly runs (51->59, then 65->72), so loop
        # #146 was filed into a render nobody could read on the morning it was
        # needed. A guard that fires on normal use is one people learn to
        # --bootstrap past without reading, which is exactly how the real failure
        # gets waved through. So: shrink stays tight, growth gets room. Growth is
        # still bounded, because a runaway duplication balloons rather than
        # truncates. Both limits are DB-tunable; the growth key falls back to its
        # default when absent, so no migration is owed.
        tol_grow = float(config(cur, "export.row_tolerance_growth_pct", 30))
        if prev is not None and prev > 0 and not bootstrap:
            delta = row_count - prev
            drift = abs(delta) / prev * 100
            limit = tol_grow if delta > 0 else tol
            if drift > limit and abs(delta) > abs_floor:
                record_run(cur, target_key, row_count, checksum, "validation_failed")
                conn.commit()
                tmp_path.unlink()
                way = "GREW to" if delta > 0 else "SHRANK to"
                kind = "growth" if delta > 0 else "shrink"
                print(f"[{target_key}] VALIDATION FAILED: {way} {row_count} rows from last ok {prev} "
                      f"({drift:.1f}% > {limit}% {kind} limit). Previous good file untouched. "
                      f"If the change is real, rerun this target with --bootstrap.",
                      file=sys.stderr)
                return False
        elif prev is None and not bootstrap:
            record_run(cur, target_key, row_count, checksum, "validation_failed")
            conn.commit()
            tmp_path.unlink()
            print(f"[{target_key}] no prior ok run — rerun with --bootstrap to accept first output",
                  file=sys.stderr)
            return False

        # file_sha is a LIVE-file tamper detector; a draft run's hash describes
        # out/exports/, not the vault, and recording it poisoned renders-verify
        # with false "tampered" flags (caught by the doctrine health pass,
        # 2026-08-08: three draft 03:43Z rows outranked the live 02:51Z ones).
        published, file_sha, publish_error = publish_export(tmp_path, final_path)
        if not published:
            record_run(cur, target_key, row_count, checksum, "failed")
            conn.commit()
            print(f"[{target_key}] PUBLISH FAILED: "
                  f"{type(publish_error).__name__}: {publish_error}. "
                  f"Previous good file untouched, staged copy removed.", file=sys.stderr)
            return False
        if publish_error is not None:
            print(f"[{target_key}] file landed; tamper hash not recorded "
                  f"({type(publish_error).__name__}: {publish_error})", file=sys.stderr)
        record_run(cur, target_key, row_count, checksum, "ok", file_sha)
        conn.commit()
        mode = "LIVE" if LIVE else "draft"
        print(f"[{target_key}] ok — {row_count} rows -> {final_path} ({mode})")
        return True
