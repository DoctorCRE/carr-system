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

### Pending follow-ups for Home

`read-post-call-followups` accepts `{}` and returns `post-call-followups.v1`
with `candidates` and `send: false`. Each candidate has `id`, `session_id`,
`deal_id`, `deal_name`, `kind`, `payload`, `evidence_quote`, and `created_at`.
Task payloads contain `deal_id`, `assignee`, `action`, and nullable `due_on`.
Email payloads contain `deal_id`, `recipient_party_id`, `recipient_ref`,
`subject`, and `body_sha256`. Email bodies and session tokens are excluded.

The read includes pending proposals for current active board deals from
unexpired post-call sessions at the distilling stage. It covers both partners
and both workspaces, orders by creation time and ID, and refuses above 1,000
candidates with `post_call_followups_limit_exceeded`. It uses the existing
writer grants inside a read-only transaction. No migration is required.

Use the existing `resolve-post-call-candidate` for a human decision. Email
confirmation records metadata only. Outlook draft creation still needs the
local report and companion. The aggregate filing report cannot gate this read
because filing requires all candidates to have a disposition. Home controls
remain a separate app change after this server contract is live.

### Human gates

- Record changes: existing capture candidate confirmation, except the
  automatic changes below.
- Joe/Dell assignment: explicit owner-aware action confirmation.

### Automatic changes from the weekly call (WR-000433 AC4, added 2026-10-26)

Joe's ruling, 2026-10-26: "the approval cards are probably good for
non-obvious decisions ... But a summary of the changes it applied
automatically would still need to be recorded and presented just in case they
were wrong".

- A deal's next step (with a stated date) and phase move (with a stated start
  date, when given) apply without a confirm when the distiller leaves
  `needs_review` null, the evidence quote appears word for word in the
  transcript, the call stated only one version of that change, and Jev's
  RIGHT DEAL check did not pick another deal. Everything else stays a card
  with its reason.
- The capture bridge applies each one right after filing, through
  `resolve-candidate` with `automatic: true`, as the recording partner's own
  local identity (`tools/call-verb.py`, joe-local or dell-local). It is
  base-guarded and audited like a Deal Room confirm; a refusal leaves a card.
- `read-call-outcomes` returns each unreviewed call: the applied changes with
  their evidence quote and the deal's current version, and the cards still
  open. `undo-call-change` reverts one applied change (base_version-guarded;
  refused once the card changed again). `review-call-outcome` takes the call
  off Home.
- Tasks, email drafts and anything outbound never apply automatically.
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
bodies are never sent. The classifier calls the official TypeSafe SDK directly,
with all item questions in one request. The local process supplies
`TYPESAFE_API_KEY` through its environment.

## Non-goals

- biometric voice recognition or retained third-party voiceprints;
- fuzzy deal or recipient assignment when an exact ID is unavailable;
- storing raw transcript or full draft bodies in the capture Worker;
- automatic external sending;
- a second audio recorder or transcription engine.
