# Deal Room post-call workflow

Status: Call Mode foundation implemented; extraction, report review, and Outlook
draft approval remain a separate release.

## Outcome

After Joe and Dell's weekly deal call, the Deal Room should show one review pack:

- deal-by-deal status and proposed record changes;
- Joe's actions and Dell's actions for the week;
- what each attached client, vendor, or other participant needs to know;
- one proposed email per allowed recipient, created in Outlook Drafts only after
  a human approves it;
- unresolved references that need a person to identify the deal or recipient.

Nothing sends automatically. Nothing changes the record layer until Joe or Dell
confirms it.

## Safe data flow

1. Call Mode starts the existing Quill recorder after the operator confirms that
   everyone has been told the call will be recorded.
2. Quill records separate microphone and system-audio channels, announces the
   recording, and transcribes locally.
3. The authenticated Deal Room supplies a short-lived exact index of open deal
   IDs and each deal's current participant IDs, roles, names, and allowed email
   addresses to the loopback Call Mode service.
4. A no-tools local model receives only the local transcript and that exact
   index. Its strict JSON output may reference only IDs in the index. Ambiguous
   references become review questions, never guessed writes. The default
   model is the resident Flash Next server (Qwen3.8 Flash Next, already
   running on loopback for the Model Room flash desk; this workflow only ever
   calls it and never starts, stops, or restarts it). When that server is
   unreachable, the session is marked blocked with a plain reason ("Flash
   Next ... is not running") and can be retried; there is no fallback to the
   bundled Qwen2.5 1.5B model, which looped and never produced a pack
   (2026-09-23).
5. A deterministic validator rejects invented IDs, recipients not attached to
   the deal, unsupported phase values, oversized evidence, and malformed tasks
   or drafts.
6. The validator writes a mode-0600 local review pack. Only sanitized phase,
   next-step, activity, and meeting proposals enter the existing capture queue.
   Raw transcript and full email bodies stay local.
7. The Deal Room renders the local report and email-draft cards. Approving an
   email card calls the loopback service, which invokes `bin/outlook-draft.py`.
   That helper has no send path.
8. Audio and transcript become purge-eligible only after every proposal is
   dispositioned and a session-level aggregate report is confirmed.

## Required contracts for the next release

### Exact call index

The browser builds this from the existing board and deal-detail reads. Each
entry contains an immutable deal ID, display name, and current participants.
The local validator treats this as the entire allowlist for attribution and
recipient selection.

### Local review pack

The pack has these top-level sections:

- `deal_updates`: exact deal ID, summary, proposed phase/next-step/activity, and
  confidence/evidence;
- `assigned_actions`: exact owner (`joe` or `dell`), exact deal ID, action, and
  due date when stated;
- `external_drafts`: exact deal and participant IDs, recipient email from the
  index, subject, body, and why the recipient needs the update;
- `unresolved`: the source reference and the question a human must answer;
- `call_summary`: an attributed aggregate narrative that replaces the raw call.

### Human gates

- Record changes: existing capture candidate confirmation.
- Joe/Dell assignment: explicit owner-aware action confirmation.
- External draft: explicit per-draft approval before Outlook Draft creation.
- Send: always performed by a human in Outlook.

### Retention gate

The current `ingested.json` marker is insufficient for a multi-deal call because
one confirmed meeting activity can create it while other candidates are still
pending. The next release must require both `pending == 0` and a confirmed
session-level aggregate report before writing the purge marker.

## Jev checks (added 2026-09-23, decision 008d682a)

Before Joe or Dell ever sees a draft item, `tools/dictation-rig/bin/post_call_jev.py`
runs it past Jev (TypeSafe) for three checks, right after `normalize_distillation`
and before the report is written:

- **RIGHT DEAL**: which recorded deal the item's words actually match, chosen
  from the item's own deal plus a handful of plausible alternatives (never
  the whole recorded-deal list);
- **RIGHT SPEAKER**: whether the item's attribution matches who actually said
  it — for `joe_tasks`/`dell_tasks` specifically, whether Joe or Dell said it;
- **RIGHT DETAILS**: whether the quoted evidence excerpt supports the item's
  specifics.

Each item in `joe_tasks`, `dell_tasks`, `deal_updates`, and `draft_proposals`
gets one `checks` object: `{deal, speaker, details, flagged, reasons}`. A
failed check **flags** the item with plain-English reasons in the review
pack; it is never silently dropped or held back from Joe or Dell. This is
purely a visible signal ahead of the human approval gates above — it does not
add, remove, or change any of them.

If Jev is unavailable (no local key, a network error, or any other failure),
the whole pack still ships: every affected item's `checks` becomes
`{"unavailable": true}` and one `review_questions` entry says the automatic
checks did not run. A Jev failure can never block or delay the review pack.

**Privacy change**: this is the first place post-call processing makes an
outbound network call. A short quoted evidence excerpt per item, the handful
of transcript segments nearest that excerpt (with their speaker labels), and
a short list of candidate deal names go to TypeSafe (`api.typesafe.ai`) for
this check. The full transcript, the full recorded-deal list, and email
bodies are never sent. This rides the same 2026-09-17 authority
`ops/typesafe_client.py` records for sending CARR records to a third-party
model API.

## Non-goals

- biometric voice recognition or retained third-party voiceprints;
- fuzzy deal or recipient assignment when an exact ID is unavailable;
- storing raw transcript or full draft bodies in the capture Worker;
- automatic external sending;
- a second audio recorder or transcription engine.
