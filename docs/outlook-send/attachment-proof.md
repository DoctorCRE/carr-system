# Attachment send proof

## System design

`tools/outlook-first-send-proof.py` verifies one partner's first submitted send through the existing pure `prove` interface. SQL selects the frozen `submitted_email.attachments` manifest. No attachment file is opened by the verifier.

The submission version determines the existing UUID outbox key, including reconciliation's version adjustment. The selected key binds the ledger rows. Their fingerprints must agree. Duplicate presses with that fingerprint fail even across keys. Canonical correspondence must match the proposal's `send_id`, Sent item ID, timestamp, sender, and recipient roles.

The verifier compares sorted SHA256 lists, preserving multiplicity, across the submitted manifest, pressing rows, done rows, and correspondence. Ledger counts must match their digest lists. Submitted and correspondence manifests require valid SHA256 values and nonnegative integer sizes. This proves recorded manifest agreement. It does not prove delivered file bytes.

## Result contract

The redacted result uses `schema=outlook-first-send-proof.v2`. It retains the proposal ID, boolean dispatch checks, and exact-body check. It emits no subject, body, recipient, attachment name, path, digest, or Sent item ID.

`checks.submission_manifest_verified` reports agreement bound to the send identity. A missing ledger manifest is the historical zero-attachment representation. A nonempty submitted manifest therefore requires explicit matching ledger manifests.

`checks.sent_attachment_content_verified` is `null` for attachment sends. `sent_attachment_content_status` is `unavailable` because the current `SentRow` decoder has no attachment-byte evidence. No provider capability is inferred. For an empty manifest the check is vacuously true and its status is `not_required`.

The exact incomplete reason is selected in this order.

1. `no_partner_submission` means no selected partner submission exists.
2. `submission_manifest_mismatch` means manifest or send-identity evidence failed.
3. `first_send_invariants_failed` means dispatch or exact-body evidence failed.
4. `sent_attachment_content_unavailable` means the recorded manifest agrees but Sent attachment bytes remain unobserved.
5. `proven` means the attachment-free proof passed.

An input or observation exception in the CLI returns `proof_unavailable` with only the exception type. `ok` requires every check to be true. An attachment send cannot pass solely on recipient and body evidence. `dispatch_verified` retains its earlier meaning and does not attest attachment content.

## Verification and production limits

`python3 tools/test_outlook_attachment_proof.py` executes the real `prove` function with synthetic ledgers and snapshots. `python3 ops/outlook-send-selftest.py` preserves the existing first-send, exact-body, duplicate-send, and reconciliation checks.

Production proof remains pending. After delivery, the coordinator must inspect a naturally partner-submitted attachment send on Studio with the read-only verifier under separate authorized access. The first-send selector is unchanged. Do not send a new test email to obtain evidence. An absent Sent entry never authorizes a resend.

The declaration at `proofs/requirements/outlook-attachment-send-proof.json` records the required observation and its expected limitation. Admission does not establish execution. A Studio run receipt alone does not establish manifest agreement or Sent attachment bytes. Until the read-only evidence exists, attachment delivery remains unproven.
