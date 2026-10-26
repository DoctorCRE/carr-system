# vendor-patches

Local changes to third-party tools that live OUTSIDE this repo. The tools are
installed under `~/carr-local/`; only the diffs live here, so a rebuild is
reproducible and the changes are not stranded on one machine.

## hxprobe-calendar-and-bodies.patch

Applies to `ukd1/hxstore-reverse-engineering` @
191b54e42757687806202f8f696e9066f0514e11 (MIT), the `hxprobe` Rust crate,
installed at `~/carr-local/hxstore-reverse-engineering`. The Outlook intake job
(`lib/outlook_intake.py`, com.carr.outlook-intake) runs its `db` command on a
private copy of `HxStore.hxd`.

SECURITY REVIEW (2026-10-20): Rust, MIT. Dependencies are memmap2, memchr and
rusqlite with bundled SQLite; the patch adds flate2 on its pure-Rust backend.
No networking and no subprocesses anywhere in `src`. Apart from tests, the only
file it writes is the SQLite database named on its command line. Its one
`unsafe` block is the read-only mmap of the input file, which is why the intake
job decodes a copy and never the live store Outlook rewrites. Blocks are
CRC-checked before they are decompressed. Build with `cargo build --release
--locked` so the patched Cargo.lock pins every crate.

WHAT THE PATCH ADDS to `hxprobe db <file> [out.db] [files-dir]`:
- Calendar events (`src/calendar.rs`) in `events` and `event_attendees`: GOID,
  start and end, subject, location, organizer and attendees. Byte-distinct
  candidates remain separate; only exact serialized copies collapse. GOID and
  start are observed grouping fields. Every row exports unsupported lifecycle
  status. Current occurrence state and attendance remain unverified. See
  [calendar lifecycle limits](../vendor/hxprobe/UPSTREAM.md#calendar-lifecycle-limits).
- Full bodies from EFMData sidecars (`src/body.rs`). These are gzip files at
  `Files/S0/<N>/EFMData/<id>.dat`. `<N>` is 845 on Joe's machine, so every
  `S0/*` folder is read rather than one hard-coded number. A sidecar is
  accepted only when its text contains the record's own preview opening.
- Copy collapse. Revisions of one message are joined by sender, normalized
  subject and preview opening within a day. A preview-only copy then joins the
  one full copy with the same sender and opening. The full body always wins,
  and `copy_key` is stored as the identity the copies share.
- Store-local message keys (WR-000502). `messages.local_keys` lists, comma-separated,
  the key of every message object merged into the row; more than one means the
  copy join merged distinct store items. `messages_with_key` and
  `local_key_conflicts` count them. See
  [the identity study](../spec/outlook/stable-message-id-research.md).
- Folders for the 2026-10 Outlook build (WR-000469). Its folder, compact-message
  and metadata envelopes use new tags (`04d8`, `074e`, `02b8`); the old tags stay
  accepted. `folders.parent_status` and `parent_id` come from the parent reference
  at folder object +792, so paths such as Inbox/Active Deals/<deal> resolve. On the
  2026-10-21 TEST-campaign copies: 0 folders before, 236 after (208 with a parent,
  28 top-level), and 2,104 of 2,121 messages linked to a folder.
- Attachment metadata (WR-000469 AC-ATTACH, `src/attachment.rs`). Class `016a`
  objects (tag `0318`) give `attachments`: message key (typed reference at +360),
  name (the cache file name without Outlook's `[<digits>]` suffix), declared MIME
  type, size (+558) and the profile-relative cache path
  `~/Files/S0/<n>/Attachments/<k>/<file>`. `message_keys` maps message rows to the
  local keys of their message objects. No bytes are read; `lib/outlook_attachments.py`
  reads, type-checks by magic bytes, caps and hashes cached files from a private
  copy. On the 2026-10-21 copies: 15,130 objects, 7,017 attachments, 566 linked to
  exactly one message row.
- A `meta` table with block verification, message, collapse, sidecar and event
  counts, which the intake job checks before it trusts a run (at least 95% of
  blocks verified).

Measured on a fresh copy, email from the last seven days:
- Before: 253 unique emails, 79 with a full body (31%).
- After: 164 emails, 80 with a full body (49%), 9 of them from EFMData.
- The other emails are preview-only because Outlook never downloaded their
  body. The evening triage job opens those, so Outlook downloads them.

REBUILD:
  curl -sL https://codeload.github.com/ukd1/hxstore-reverse-engineering/tar.gz/191b54e42757687806202f8f696e9066f0514e11 | tar -xz -C ~/carr-local
  mv ~/carr-local/hxstore-reverse-engineering-191b54e42757687806202f8f696e9066f0514e11 ~/carr-local/hxstore-reverse-engineering
  cd ~/carr-local/hxstore-reverse-engineering
  patch -p1 < ~/carr-system/vendor-patches/hxprobe-calendar-and-bodies.patch
  cd hxprobe && cargo test --release --locked && cargo build --release --locked

`bin/install-outlook-intake.sh` runs these steps when the binary is missing.

## hxstore-decode-field-alignment.patch

Applies to `mitchell-johnson/hxstore-decode` @ 494723b (MIT), installed at
`~/carr-local/hxstore-decode` with its own venv.

WHAT THE TOOL IS FOR: reading Joe's carr.us mail out of New Outlook's local
store, `HxStore.hxd`. It is the only route that works — every API is closed
(Graph consent blocked tenant-wide, IMAP basic auth refused, AppleScript sees
no mailbox). See decision `db40facd`.

SECURITY REVIEW, done before installing and worth not repeating from scratch:
pure Python, MIT, deps are `click` and `lz4` only. No network calls, no
exec/eval/subprocess, no base64/pickle/marshal, and NO file writes anywhere in
`src`. The single file-open builds its path from the profile directory plus a
regex-constrained `EFMData/(\d+)\.dat` — digits only, so no traversal. The
hand-written LZ4 decoder guards every classic decoder bug: bounded output,
rejects `match_offset > len(output)`, rejects zero offsets, and clamps literal
copies to the available input. Clone hashes were verified against the reviewed
copy before install.

KNOWN NON-SECURITY CAVEAT: `parser.py` reads the WHOLE store into RAM via
`read_bytes()`. Fine at 72 MB. Joe enabled full folder sync WITH attachments on
2026-08-03, so if that store reaches multiple GB this will try to allocate all
of it at once.

WHAT THE PATCH FIXES: upstream assigns sender/subject BY POSITION in the
extracted string sequence and never validates the result, so on a real mailbox
it emitted MIME types, GUIDs, base64 SafeLinks tokens and bare addresses as
`subject`, and subject lines as `sender_name`. The patch adds type-aware
rejection (`_is_noise`), un-doubles names that HxStore stores twice, and
detects the swap case where the display name landed in `subject` and the
subject in `sender_name`. An empty field is preferable to a confidently wrong
one: a blank sends you to look, a wrong value gets believed.

STILL IMPERFECT after the patch, deliberately recorded: `sender_name` can hold
a document name on DocuSign/automated mail, some subjects are body fragments,
and occasional addresses come out malformed at string boundaries. Good enough
to FIND a message; not a faithful archive.

Also removed from `pyproject.toml`: the `License :: OSI Approved :: MIT License`
classifier, which modern setuptools rejects under PEP 639 alongside an SPDX
`license` field. Metadata only, no code impact.

REBUILD:
  git clone --depth 1 https://github.com/mitchell-johnson/hxstore-decode.git ~/carr-local/hxstore-decode
  cd ~/carr-local/hxstore-decode
  git apply ~/carr-system/vendor-patches/hxstore-decode-field-alignment.patch
  python3 -m venv .venv && ./.venv/bin/pip install .
