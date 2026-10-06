#!/bin/zsh
# Mail contact capture — extract, then match. ONE nightly step, loop #169.
#
# WHY A WRAPPER AND NOT TWO STEPS. tools/mail-touch-matcher.py exits 1 when its
# input file is absent, and a night where Apple Mail was simply closed is not a
# broken night. The natural fix is to soften the matcher's exit, but that file
# is held uncommitted by another session and belongs to it (two-writer rule
# 308ef1de), so the guard lives here instead of being taken out of somebody
# else's hands. If that change ever lands there, this wrapper stays correct.
#
# EXIT 78 = EX_CONFIG, which bin/nightly.sh reads as SKIP rather than failure:
# the step ran, found what it needs is absent, wrote nothing and said so.
set -u
REPO="${0:A:h:h}"
cd "$REPO" || exit 1
PY="$REPO/.venv/bin/python"

"$PY" "$REPO/tools/mail-extract.py" "$@"
rc=$?
if [ "$rc" -eq 78 ]; then
  print -r -- "mail-capture: Apple Mail is not running — nothing extracted, nothing matched."
  exit 78
fi
[ "$rc" -ne 0 ] && exit "$rc"

# The extractor declines rather than writing an empty file, so its absence here
# means the extract genuinely did not happen.
EXTRACT="${CARR_MAIL_EXTRACT:-$REPO/out/mail-extract.json}"
if [ ! -f "$EXTRACT" ]; then
  print -r -- "mail-capture: no extract at $EXTRACT — skipping the matcher."
  exit 78
fi

exec "$PY" "$REPO/tools/mail-touch-matcher.py" --extract "$EXTRACT"
