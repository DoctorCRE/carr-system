# vendor-patches

Local changes to third-party tools that live OUTSIDE this repo. The tools are
installed under `~/carr-local/`; only the diffs live here, so a rebuild is
reproducible and the changes are not stranded on one machine.

## hxprobe (no patch)

The hxprobe decoder is not patched here. Its CARR source lives in
[vendor/hxprobe](../vendor/hxprobe/UPSTREAM.md), the tree CI tests, and
`bin/install-outlook-intake.sh` builds the installed decoder from that tree.
The former `hxprobe-calendar-and-bodies.patch` was retired because it had
drifted from the tree.

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
