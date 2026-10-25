#!/bin/zsh
# Nightly encrypted pg_dump -> R2 (A9: encrypted from the FIRST dump; a durable
# off-Mac copy is the permanent record). Scheduled at cutover; runnable
# any time: ./bin/backup-dump.sh
# Private key: ~/.config/carr/age-key.txt (local, 600) — Joe owes an OFFLINE
# copy (paper/sealed); tracked in secrets-inventory.md.
#
# CHANGED 2026-08-06 (ORDER 42b): dumps used to `git add backups/ && git commit
# && git push` — full encrypted production DB dumps, tracked in git history
# forever. ORDER 42 flagged that as PII exposure. The dump still writes to
# backups/ locally (restore-rehearse.sh and any manual `age -d` still find it
# there — the directory is now gitignored, not deleted), and now also uploads
# to the R2 archive via bin/backup-archive-r2.py, which reuses
# lib/r2_archive.py's quota-guarded uploader (ORDER 20) rather than a second
# implementation. ORDER 42b also purged dumps committed under the old scheme.
# pipefail ADDED 2026-08-07, and it is half of a fix for a night this script
# reported success on a 200-byte backup. See the guard block below for the whole
# story; the short version is that in `pg_dump | age > f` the pipeline's exit
# status is AGE's, age encrypts an empty stream without complaint, and so a
# pg_dump that died mid-transfer was invisible to `set -e`.
#
# CHANGED 2026-08-14 (PROGRAM 4, THE MAC-INDEPENDENT COPY): this script now
# serves BOTH the local nightly (Joe's Mac) and the GitHub Actions nightly
# workflow — rule a8c55a47, a manual path and an automated path doing the
# same job must be the same code, never a second implementation. Three env
# vars, all optional and all unset on Joe's Mac, so local behavior is
# byte-identical to before this change:
#   BACKUP_DATABASE_URL — if set, used AS-IS instead of resolving the
#     production owner DSN through neonctl. Actions has no neonctl login and
#     should never need one: it connects as the dedicated read-only
#     carr_backup role (migrations/0119_backup_role.sql) via a GitHub secret.
#   BACKUP_SKIP_R2=1 — skips the R2 archive step below. A GitHub runner's
#     disk dies with the job, so there is no local copy to keep and no
#     reason to spend the R2 quota a second time on the same night's dump;
#     the encrypted file goes to the workflow artifact (90-day retention)
#     instead. See .github/workflows/backup-nightly.yml.
#   BACKUP_OUTPUT_DIR — overrides where the dump (and the size-floor's
#     previous-dump lookup) lives. Defaults to $REPO/backups, exactly as
#     before.
#
# SCHEMA SCOPE (2026-08-14). The application owns public and ops. Neon Auth
# owns neon_auth, which is provider-managed identity data and is outside the
# CARR record-layer backup contract. The dedicated carr_backup role therefore
# has SELECT only in public+ops, and pg_dump is scoped to the same two schemas.
# This is both the least-privilege boundary and the restore boundary: Neon
# recreates its managed services; this artifact restores CARR's schema+data.
set -euo pipefail
REPO="$(cd "$(dirname "$0")/.." && pwd)"
export PATH="/usr/local/opt/node@22/bin:/opt/homebrew/opt/libpq/bin:/usr/local/opt/libpq/bin:$PATH"
PG_DUMP_BIN="${PG_DUMP_BIN:-pg_dump}"
PUBKEY="$(cat "$REPO/backups-public-key.txt")"
# A routine backup is read-only and must use the dedicated carr_backup login.
# Resolving an owner URL through neonctl turned a scheduled dump into an owner
# credential path.  Explicit manual recovery has a separate entrypoint.
URL="${CARR_DB_BACKUP_URL:-${BACKUP_DATABASE_URL:-}}"
[ -n "$URL" ] || { echo "backup-dump: CARR_DB_BACKUP_URL is required for routine backup" >&2; exit 78; }
# The guard resolves libpq connection parameters, checks the actual principal,
# and applies finite connection/operation deadlines to this same routine path.
# A URL substring is not authentication evidence (query parameters override it).
PYTHON="$REPO/.venv/bin/python"
[ -x "$PYTHON" ] || PYTHON=python3

STAMP="$(date -u +%Y%m%d)"
OUTDIR="${BACKUP_OUTPUT_DIR:-$REPO/backups}"
mkdir -p "$OUTDIR"
OUT="$OUTDIR/carr-$STAMP.sql.age"
if [ -z "${BACKUP_SKIP_R2:-}" ]; then
  OUT="$OUTDIR/carr-$STAMP-$("$PYTHON" -c 'import uuid; print(uuid.uuid4().hex)').sql.age"
fi

# --no-owner --no-acl ADDED 2026-08-02, and the reason is a real failure, not style.
# The first genuine restore rehearsal ever run against these dumps died with
#   ERROR: permission denied to change default privileges
# A plain pg_dump embeds ALTER DEFAULT PRIVILEGES / GRANT / REVOKE / OWNER TO
# naming the source database's owning role. Restoring into a fresh database —
# which is what any real recovery does — those roles are not the restoring
# session's to act for, the first statement errors, and the load aborts. Nine
# months of nightly backups would have been discovered unrestorable at the worst
# possible moment.
# We restore SCHEMA AND DATA, never the source cluster's permission model: roles
# and grants are rebuilt by the migrations, which are in git. Dropping them from
# the dump costs nothing and is what makes it portable.
#
# --enable-row-security ADDED 2026-09-01 (WR-000044, decision 11376c54). 0324
# enabled RLS on ops.work_request; pg_dump's default row_security=off makes a
# non-BYPASSRLS role's read of an RLS table a hard ERROR ("query would be
# affected by row-level security policy"), which is what broke the nightly dump.
# Turning row_security ON lets the dump read under RLS, where migration 0475's
# permissive carr_backup SELECT policy (USING (true)) returns every row. This
# was chosen over ALTER ROLE carr_backup BYPASSRLS specifically to avoid moving
# the pinned SCAC v10 role-attribute census (see 0475's header). The cost of
# row_security=on is that a future RLS-enabled table WITHOUT a carr_backup
# read-all policy would dump short and silent; ops/backup-role-rls-coverage-*
# make that a loud failure instead. carr_backup stays SELECT-only.
# The original guard transaction survives pg_dump, encryption, stream/OID
# verification, the encrypted size floor and the final acknowledgement. Only
# the helper promotes its private temporary ciphertext; a failure preserves
# every previous acknowledged artifact and never reaches the archive step.
RESULT="$("$PYTHON" "$REPO/bin/backup-guard.py" \
  --output "$OUT" --recipient "$PUBKEY" --pg-dump "$PG_DUMP_BIN")" || {
  echo "DUMP FAILED — guarded dump refused; previous backups untouched" >&2
  exit 1
}
SIZE="$(print -r -- "$RESULT" | "$PYTHON" -c 'import json,sys; print(json.load(sys.stdin)["bytes"])')"
FLOOR="$(print -r -- "$RESULT" | "$PYTHON" -c 'import json,sys; print(json.load(sys.stdin)["floor"])')"
if [ -n "${BACKUP_SKIP_R2:-}" ]; then
  echo "backup ok -> $OUT ($SIZE bytes, floor was $FLOOR) -> R2 archive: skipped (BACKUP_SKIP_R2)"
else
  ARCHIVE_RC=0
  ARCHIVE_JSON="$(cd "$REPO" && DATABASE_URL="$URL" "$PYTHON" bin/backup-archive-r2.py "$OUT")" || ARCHIVE_RC=$?
  R2_KEY="$(print -r -- "$ARCHIVE_JSON" | "$PYTHON" -c '
import json,sys
try:
    result=json.load(sys.stdin)
    if result["archived"] is not True:
        raise ValueError("archive owed")
    key=result["key"]
    if not isinstance(key,str) or not key.startswith("backups/"):
        raise ValueError("invalid archive key")
    print(key)
except (ValueError, KeyError, TypeError):
    sys.exit(1)
')" || ARCHIVE_RC=1
  if [ "$ARCHIVE_RC" -eq 0 ]; then
    echo "backup ok -> $OUT ($SIZE bytes, floor was $FLOOR) -> R2 archive: $R2_KEY"
  else
    echo "backup ok -> $OUT ($SIZE bytes, floor was $FLOOR) -> R2 archive: OWED; local ciphertext retained" >&2
    exit 1
  fi
fi
