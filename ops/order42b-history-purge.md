# ORDER 42b — history purge: `baselines/*.html` + `backups/*.sql.age`

*Written 2026-08-06 by the ORDER 42b session, the parked second half of ORDER 42
(PII in the carr-system repo). Joe ruled the same night to do all of it. This
document PREPARES the rewrite. It does not execute it — no `git filter-repo`,
no force-push, no `git commit` ran from this session. The parent session and
Joe execute the steps below.*

## What this closes out

ORDER 42 (2026-08-01) purged `frozen-sources/` and `exporters/templates/` from
git history and force-pushed. Its own execution log parked four items for later,
two of which are a second history purge:

> `baselines/lead-board.html` holds 60,687 emails / 218 phones and
> `baselines/deal-room-panhandle.html` 91/49 — a larger exposure than
> frozen-sources ever was... `backups/carr-2026073{0,1}.sql.age` (full
> encrypted DB dumps) are tracked in git — encrypted-vs-PII is genuinely
> ambiguous.

This session's Phase 1–2 work changed the go-forward MECHANISM so neither path
writes PII into git again: `baselines/lead-board.html` and
`baselines/deal-room-panhandle.html` are now hash-only tracked
(`baselines/SHA256SUMS`, full HTML local + gitignored — see `tools/check.sh`
and the README Boundaries section); `backups/*.sql.age` now archives to R2 via
`bin/backup-archive-r2.py` instead of being git-committed (see
`bin/backup-dump.sh` and `lib/r2_archive.py`). **Both paths still exist in
every commit made before this session's changes are committed and pushed.**
This document is how that history gets removed too, matching what ORDER 42
already did for `frozen-sources/` and `exporters/templates/`.

## What is currently tracked (state as of 2026-08-06, this session — HEAD `16c0963`)

```
$ git ls-files baselines/
baselines/renewal-radar.json         (NOT PII — stays tracked, do not purge)
baselines/writing-lint.txt           (NOT PII — stays tracked, do not purge)
(lead-board.html and deal-room-panhandle.html are GONE from HEAD already —
 see "concurrent-session note" immediately below for how)

$ git ls-files backups/
backups/carr-20260730.sql.age
backups/carr-20260731.sql.age
backups/carr-20260802.sql.age
backups/carr-20260803.sql.age
backups/carr-20260804.sql.age
backups/carr-20260805.sql.age
(these 6 are STILL tracked at HEAD — see "known gap" below)
```

**Concurrent-session note, important for whoever reads this next.** This
session staged `baselines/lead-board.html` and `baselines/deal-room-
panhandle.html` for removal via `git rm --cached` (Phase 1) and left that
staged, uncommitted, per its "do not commit" instruction. While this session
was still working, a DIFFERENT, concurrent session — same repo, same working
directory, commits authored `Joe Bookout` / co-authored `Claude Fable 5` —
committed unrelated work (`49b5000 ledger-sweep: skip harness turns...`,
about a hook fix, nothing to do with PII) and its commit's file list swept up
this session's staged baselines deletion along with its own two files. Net
effect: `baselines/lead-board.html` and `baselines/deal-room-panhandle.html`
are **already gone from `HEAD`** (verified: `git ls-tree HEAD baselines/`
shows only `renewal-radar.json` and `writing-lint.txt`) — which is Phase 1's
intended end state, just reached through a commit this session did not make
and was not consulted on. Whoever reviews this: (1) the removal is correct
and matches this document's purpose, nothing to undo; (2) it is evidence that
**another live session is working in `~/carr-system` concurrently** —
anything left uncommitted in this working tree (this session's `.gitignore`,
`tools/check.sh`, `baselines/SHA256SUMS`, `bin/backup-*`, the 9 sanitized
source files, `README.md`, `migrations/0001_init.sql`, this file) is at the
same risk of being swept into an unrelated commit by that other session
before anyone reviews it — worth checking `git status`/`git diff` again,
fresh, before treating the list above as still accurate, and worth raising
the two-writer collision with Joe directly rather than assuming it will sort
itself out.

**Known gap, honestly stated:** this session could not remove
`backups/*.sql.age` from the git index the same way (`git rm --cached`):
this repo's own `hooks/guard-unattended.py` PreToolUse guard blocks any Bash
command whose text matches `\.age\b` (its private-key-material rule — intended
for `age-key.txt`/`identity.txt`, but it also catches the `.sql.age` dump
extension as a false positive). Rather than crafting a command to dodge that
pattern match, this session left the 6 files tracked in the index and is
flagging it here instead. **This has no practical effect on the purge below**:
`git filter-repo --invert-paths --path backups/` removes the path from every
commit including HEAD regardless of whether it was `rm --cached`'d first, so
step 3's invocation covers it either way. Whoever runs the pre-flight (below)
should run `git rm --cached backups/*.sql.age` first if a clean `git status`
before the rewrite is wanted — optional, not required for the purge itself.

## Pre-flight (before touching anything)

1. **Confirm the working tree is clean or intentionally staged.** `git status`.
   Commit or stash anything not meant to ride into the rewrite. Phase 1–3 of
   this session's work (the `.gitignore` entries, `tools/check.sh`,
   `baselines/SHA256SUMS`, `bin/backup-archive-r2.py`, `bin/backup-dump.sh`,
   the 9 sanitized source files, `README.md`, `migrations/0001_init.sql`) is
   uncommitted on purpose — this session was told not to commit. Review it,
   commit it (a normal commit, not part of the rewrite), THEN start the
   pre-flight below on a repo that already has the new gitignore/hash-tracking
   in place — otherwise the very next `backup-dump.sh` run or `check.sh`
   invocation re-introduces what step 3 just purged.

2. **Push or account for the 20 local-only commits.** As of this writing, local
   `main` is 20 commits ahead of `origin/main` (`git rev-list --left-right
   --count origin/main...HEAD` → `0  20`) — origin's tip is still the
   2026-08-05 nightly backup commit; this session's local clone has 20 newer
   commits (including this session's own commits, once made) that were never
   pushed. **Decide before rewriting:** rewrite the local clone (which has all
   235 commits including the 20 unpushed ones) and force-push the result — the
   20 commits ride along and origin catches up in the same push — OR push the
   20 commits normally FIRST (a plain `git push`, non-destructive), confirm
   origin match, THEN rewrite. Either works; rewriting a clone that is BEHIND
   origin does not (the rewrite would silently drop commits origin has that
   local doesn't). Check `git rev-list --left-right --count origin/main...HEAD`
   again immediately before step 2 below and confirm the second number is 0
   or matches what you expect to carry.

3. **Backup ref bundle FIRST, before any rewrite.** This is the undo button.
   ```
   cd ~/carr-system
   git bundle create ~/carr-system-pre-order42b-purge-$(date -u +%Y%m%dT%H%M%SZ).bundle --all
   git bundle verify   ~/carr-system-pre-order42b-purge-*.bundle
   ```
   Also keep (or make) a full mirror clone, the same pattern ORDER 42 used:
   ```
   git clone --mirror ~/carr-system ~/carr-system-prepurge-backup-$(date -u +%Y%m%d)
   ```
   Store both outside `~/carr-system` (they must not become part of what gets
   rewritten). Do not delete either until step 6's verification passes AND
   Joe has confirmed the force-push landed correctly.

4. **Fresh clone vs. in-place fetch-all — recommendation: fresh clone.**
   `git filter-repo` refuses to run against a repo with a remote configured by
   default (it wants a clean, disposable checkout) — ORDER 42 hit this
   implicitly by working from the existing local clone; the flag needed is
   `--force` on the invocation itself if working in-place, OR clone fresh:
   ```
   git clone ~/carr-system ~/carr-system-purge-work
   cd ~/carr-system-purge-work
   git fetch --all
   ```
   A fresh clone from the LOCAL repo (not from `origin`) is important given
   the pre-flight #2 decision above: cloning from `origin` would only carry
   origin's 215 commits, not local's 235. Clone from `~/carr-system` itself
   (or from wherever step 2 landed all the commits you intend to keep) so the
   rewrite operates on the complete, intended history.

5. **CRITICAL — back up the local baseline/backup files before rewriting,
   not after.** `git filter-repo` checks out the rewritten HEAD after it
   finishes, which removes from the working tree any file that no longer
   appears in ANY commit — this is exactly what happened to
   `exporters/templates/` during ORDER 42 ("must be restored locally from its
   Drive zip and gitignored or the exporter's `_template()` path breaks").
   The same will happen here: `baselines/lead-board.html`,
   `baselines/deal-room-panhandle.html`, and every `backups/*.sql.age` file
   will DISAPPEAR from the working directory of whichever checkout gets
   rewritten, even though they are gitignored now — gitignore prevents
   RE-adding a file, it does not protect a currently-tracked file from being
   removed when history stops containing it. Before running step 6:
   - Confirm current local copies exist and are good:
     `ls -la ~/carr-system/baselines/*.html ~/carr-system/backups/*.sql.age`
   - They are NOT at risk in `~/carr-system` itself if the rewrite runs in the
     separate `~/carr-system-purge-work` clone from step 4 (recommended) —
     `~/carr-system`'s own working copy is untouched by a rewrite that happens
     in a different directory. The only actual restore-after-rewrite need is
     for `~/carr-system-purge-work` itself (which nobody uses after this), and
     for `~/carr-system` AFTER it is reset to the rewritten history (step 7).
   - Verify the hashes match `baselines/SHA256SUMS` before and after:
     `shasum -a 256 baselines/lead-board.html baselines/deal-room-panhandle.html`
   - `backups/*.sql.age`: confirm each has an R2 archive copy (once
     `bin/backup-dump.sh`'s new archive step has actually run for it) or keep
     the local files — they are the only copy for any dump taken before this
     session's Phase 2 change landed.

## Step 6 — the purge

Run inside `~/carr-system-purge-work` (the fresh clone from step 4), never
inside `~/carr-system` directly, so `~/carr-system`'s own working copy is
never mid-rewrite:

```
cd ~/carr-system-purge-work
git filter-repo --invert-paths \
  --path baselines/lead-board.html \
  --path baselines/deal-room-panhandle.html \
  --path backups/ \
  --force
```

Notes on the invocation:
- `--path backups/` (the whole directory, not a glob) because `git
  filter-repo` does not expand shell globs the way `--path backups/*.sql.age`
  implies — `--path` matches literal paths/prefixes. `backups/` currently
  holds only `*.sql.age` files (confirmed via `git ls-files backups/` above),
  so purging the directory is equivalent to purging the dumps and does not
  remove anything else. If a non-PII file is ever added to `backups/` before
  this runs, re-check and switch to explicit `--path` lines per file.
  `baselines/renewal-radar.json` and `baselines/writing-lint.txt` are named
  individually and are NOT in the `--path` list — they must survive the purge
  untouched; verify this in step 8.
- `--force` is required because the source is not a fresh `git clone` in
  filter-repo's own sense (it still has refs/remotes metadata from being
  cloned off a local path) — same flag ORDER 42 used.
- Expect the commit count to drop, the same way ORDER 42's run went from 116
  to 114 (two now-empty commits pruned). Record the before/after count.

## Step 7 — bring `~/carr-system` up to the rewritten history

```
cd ~/carr-system
git remote add purge-work ~/carr-system-purge-work   # or: git fetch ~/carr-system-purge-work main
git fetch purge-work
git reset --hard purge-work/main
git remote remove purge-work
```
This is the point where `baselines/lead-board.html`, `deal-room-panhandle.html`,
and `backups/*.sql.age` disappear from `~/carr-system`'s working tree (per
step 5's warning) — restore them immediately after from the pre-purge copies
you confirmed exist:
```
# baselines (confirm hash matches baselines/SHA256SUMS after copying back):
cp <your pre-purge copy of lead-board.html>          baselines/lead-board.html
cp <your pre-purge copy of deal-room-panhandle.html>  baselines/deal-room-panhandle.html
# backups (or fetch from R2 for any dump uploaded there):
cp <your pre-purge copies of backups/carr-*.sql.age>  backups/
```

## Step 8 — force-push (**JOE ONLY — explicit per-action authorization required**)

Per the standing stop rule (the same one ORDER 42 followed): **do not force-push
without Joe's explicit go, given in chat, for this specific action.** Record his
exact words the way ORDER 42's log did ("Go — you push it").

```
git ls-remote origin HEAD    # record remote HEAD BEFORE, for the done-test
git push --force origin main
git ls-remote origin HEAD    # record remote HEAD AFTER
```

## Step 9 — post-purge verification (every commit, not sampled)

```
cd ~/carr-system
# 1. Full-history content scan — zero matches expected:
git log --all -p -- baselines/lead-board.html baselines/deal-room-panhandle.html | head -1
git log --all -p -- 'backups/*.sql.age' | head -1
# (both should print nothing — no commit in the rewritten history touches these paths)

# 2. Confirm the paths are gone from every commit's tree, not just HEAD:
git rev-list --all | xargs -I{} git ls-tree -r {} --name-only 2>/dev/null \
  | grep -E '^baselines/(lead-board|deal-room-panhandle)\.html$|^backups/.*\.sql\.age$' \
  | sort -u
# (expect zero output)

# 3. Confirm the survivors are UNCHANGED (these must still be there):
git log --all --oneline -- baselines/renewal-radar.json | head -3
git log --all --oneline -- baselines/writing-lint.txt    | head -3

# 4. Confirm remote HEAD moved and matches local:
git ls-remote origin HEAD
git rev-parse HEAD

# 5. Confirm the working tree is intact and check.sh still passes/fails the
#    same way it did before the purge (same pass/fail behavior, not a new break):
./tools/check.sh
```

Record all five results in the execution log entry for this order, the same
way ORDER 42's completion entry did.

## Standing caveat (carried forward from ORDER 42, still true)

GitHub can retain unreachable objects server-side for a time after a
force-push; the purged commits/blobs may stay fetchable by direct SHA until
GitHub's own housekeeping (garbage collection) runs. The repo is private
either way, which bounds the exposure to GitHub itself rather than the public
internet. **Optional hardening (Joe's call):** file a GitHub Support request
asking them to run a GC on the repo now rather than waiting for it to happen
on its own schedule. Not required to close this order; note it as still open
if Joe doesn't want to pursue it.

## Also completed this session (not part of the git-history rewrite, but related)

- **README.md posture line** (Boundaries section + the `baselines/` bullet)
  updated to describe the NEW mechanism (hash-only tracking, R2 archive) —
  ORDER 42 deliberately left this line unedited on 2026-08-01 because editing
  it then would have been false (baselines/ still held full PII in git at the
  time). It is true now that Phase 1–2 landed, so it was safe to update.
- **Two historical execution records ORDER 42 flagged but did not amend:**
  - `DNA/Deal Management/record-layer/opus-work-orders-2026-07-31.md`, above
    line 1226 — a dated superseded-note was added directly above the
    "**Freeze:** `frozen-sources/2026-07-31-loops/`..." sentence. The
    original sentence is untouched.
  - `00_Context/handoffs/handoff-2026-07-30-freeze-cutover.md`, lines 31-32 —
    the equivalent in-file note was **blocked** by
    `hooks/record-home-gate.py` (a handoff file is governed content; the gate
    requires a record-layer verb instead of a hand-edit). Logged instead via
    `log-decision` (decision_id `c92d8bc7-621c-4adc-b431-24a57d7add02`,
    renders into `00_Context/decision-history.md`) recording the same
    supersession fact. The handoff file's original text remains completely
    unedited — neither the old text nor a new note.

## EXECUTED 2026-08-06 night — Joe's hands on the rewrite and the push, verified by the Fable seat

Pre-flight: tree clean, 239 commits, origin 24 behind with nothing local lacked.
Bundle created and verified complete; mirror clone made; both HTML baselines and
all six dump files copied outside the repo; baseline hashes matched SHA256SUMS
before and after. Rewrite ran in the disposable work clone: 239 -> 217 commits
(22 emptied nightly-backup commits pruned). ~/carr-system reset to the rewritten
history; local copies restored, hashes re-verified. Two stale July branches
(graph-orphan-fix, graph-usability-rework) carried purged blobs and only
superseded graph work — deleted locally, preserved in the bundle and mirror.
Force-push by Joe: remote HEAD 43b3934 -> 3968612, recorded before and after.
Post-push verification, all five: (1+2) all-refs log and tree scans EMPTY for
both HTML baselines and the backups path; (3) renewal-radar.json and
writing-lint.txt histories intact; (4) remote HEAD == local HEAD at 3968612;
(5) check.sh pass/fail behavior unchanged. Local reflogs expired and gc'd:
0 loose objects, 1,737 in-pack — the purged blobs are gone from the machine
too, surviving only in the two backup artifacts, which stay until Joe deletes
them deliberately. STILL OPEN, Joe's option: the GitHub GC support ticket
(server-side unreachable objects until their housekeeping runs; private repo
bounds the exposure).

## ADDENDUM 2026-09-26 — `baselines/renewal-radar.json` reclassified as PII-bearing

This document's original assessment above, in both "What is currently
tracked" and step 6's purge notes, called `baselines/renewal-radar.json`
"NOT PII — stays tracked, do not purge" and required it to survive the purge
untouched. That assessment was wrong: on inspection the file carries real
tenant/landlord names, street addresses and phone numbers (about 150 rows,
same shape of exposure as the two files this document did purge). The
original sentences above are left unedited, per this document's own practice
of dated addenda rather than silent rewrites of historical assertions.

Go-forward mechanism only, done in PR #1334: `baselines/renewal-radar.json`
moved to the same hash-only tracking as `lead-board.html` and
`deal-room-panhandle.html` (gitignored, `git rm --cached`'d, full file stays
local, sha256 added to `baselines/SHA256SUMS`). This does **not** purge the
file from history — no `git filter-repo` ran, no force-push happened. The
file's git history is exactly the 3 commits `git log --all --oneline --
baselines/renewal-radar.json` shows (paths, not values, recorded here);
whether to run a second history purge for it, on this document's same
mechanism (steps 1-9 above, updated to target `baselines/renewal-radar.json`
instead of the two HTML files), is Joe's call — flagged for him, not decided
here. This repo is also now PUBLIC (not private, contrary to this document's
and the README's private-repo assumption at the time each was written), which
raises the stakes of leaving this file's history unpurged.
