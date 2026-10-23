# Outlook dispatch approval

Send and Save require an unexpired, one-use receipt from a separate administrative
authority. Draft remains the default. This implements audit PR #2196, slice E1,
finding F1. Installation and real Outlook behavior are separate deployment work;
this change does not install the authority or send a message.

## Terms

An **approval receipt** authorizes one exact dispatch on Joe's authority until its
expiry. A **standing instruction** is a named instruction Joe gave, recorded with
its decision reference, exact allowed account and content, and expiry. A
**dispatch permit** is the remaining lifetime of a receipt already consumed for
the current press attempt. It cannot authorize a later operation.

## Decisions from the autonomous design interview

1. **Who mints receipts?** A root-owned, standalone copy of
   `lib/outlook_approval.py`. The sending process receives only its consume command.
   A sender-owned file, hash, boolean, or pluggable approval verifier would let
   the sender approve itself. The helper uses only the isolated system Python
   standard library, with no imports from a writable checkout.
2. **Where are receipts stored?** `/private/var/db/carr-outlook-approval`, owned
   by root. Each receipt contains a digest, issue time, expiry, and authority
   basis. It stores no account, recipients, subject, or body. Consumption takes
   an exclusive lock, renames the receipt to a spent receipt, and fsyncs the
   directory before returning. Sender state resets cannot restore a receipt.
3. **What establishes Joe's yes?** The root-only `approve` command displays the
   complete binding and expiry to Joe's administrative terminal and requires
   `yes` from `/dev/tty`. Piped input cannot answer the confirmation prompt.
   Receipt lifetime is at most fifteen minutes.
4. **How is a standing instruction named?** The root-only
   `register-instruction` command displays the complete scope, a descriptive
   name, an expiry, and `decision:<full UUID>` for Joe's actual instruction.
   Joe confirms that instruction in the administrative terminal. Its returned
   opaque instruction ID identifies that trusted registration.
   Decision `41906a30` permits this authority basis; it is not itself a blanket
   sending instruction. No live record-layer lookup or write occurs here.
5. **How are standing receipts issued?** A trusted administrative workflow runs
   `issue-standing` with the registered instruction ID and exact message. It
   cannot expand that instruction's scope or expiry. The sender cannot invoke
   this command. Each resulting receipt binds a distinct operation key. The
   standing scope includes every message field but permits new operation keys.
   This narrow implementation supports exact repeatable content, not templates,
   recipient patterns, or caller-interpreted natural-language instructions.
6. **Where are approvals checked?** After suppression, initial field readback,
   mailbox and idle checks, the sender consumes the receipt. Immediately before
   the background action and any permitted foreground fallback, it checks the
   current account, exact fields, idle time and receipt expiry again. Field
   readback and the button token share one accessibility snapshot. Only the
   existing explicit `background_unavailable` result allows foreground fallback.
7. **What happens on uncertainty?** Refuse dispatch. Consumption is never undone
   after timeout, crash, expiry, or a failed UI action. Existing delivery latches,
   suppression, daily cap and mailbox confirmation remain in force. Recovery of
   known-unsent work and logging belongs to audit slice E2.

## Administrative provisioning contract

The sender must run without root authority. It must not have unrestricted sudo,
permission to issue receipts, a usable administrative authentication context, or
write access to the installed helper, interpreter, store, or their ancestors.
Root administrators remain trusted. This does not contain arbitrary code that
bypasses this module and drives Outlook directly.

The fixed helper path is
`/Library/PrivilegedHelperTools/com.carr.outlook-approval`. Install the reviewed
standalone source there as a root-owned regular executable, mode 0755, with
root-owned ancestors that are not writable by group or others. Its shebang uses
`/usr/bin/python3 -I`. Root must create the store's parent if needed; the helper
creates its own 0700 store. The store must never be removed to recover sender
state, since that would discard replay protection.

The sender's sudoers permission must match only the exact command below, with
no argument wildcard, no SETENV, and no permission for any issuance command:

```text
/Library/PrivilegedHelperTools/com.carr.outlook-approval consume
```

No installer or sudo policy is run by this PR. An absent, unsafe, inaccessible,
or unresponsive helper refuses dispatch. Drafts continue to work without it.

## Request format and use

All helper requests arrive on stdin, never as command arguments. These are
synthetic fields illustrating the complete binding:

```json
{
  "binding": {
    "account": "sender@example.test",
    "kind": "send",
    "operation_key": "synthetic-operation-1",
    "fields": {
      "to": "recipient@example.test",
      "cc": "",
      "bcc": "",
      "subject": "Synthetic subject",
      "body": "Synthetic body"
    }
  },
  "expires_at": 1800000060
}
```

`expires_at` is an absolute Unix timestamp, strictly in the future. The example
is illustrative, not an active approval. Account and addresses use the same
lowercase representation as Outlook readback. Recipient fields join addresses
with `; ` in order and keep To, Cc, and Bcc separate. No whitespace normalization
is applied to subject or body. Event bindings use `kind: "event"` and exactly
`subject`, `start`, `end`, `location`, `body`, `attendees`. Start and end use the
timezone-aware ISO strings read by `lib.outlook_send.fields`.

The `approve` command returns `approval_id`. For standing instructions,
`register-instruction` additionally takes `reference` and `name` and returns
`instruction_id`. `issue-standing` takes the binding, expiry and instruction ID
and returns `approval_id`. Root can revoke an instruction by removing its
registration; consumption rechecks registration and expiry.

All three steps are required for an approved send:

1. Obtain a receipt through Joe's administrative approval or a registered standing instruction workflow.
2. Add its `approval_id` to the owned 0600 JSON input and request `--send`.
3. Check for `done confirmed_in_mailbox`; any refusal requires a fresh administrative receipt before retry.

The Python `send` and `create_event` interfaces accept the same opaque
`approval_id`. They accept no issuer, store path, approval flag or verifier.
Providing a receipt without `send_requested=True` still prepares only a draft.

## Verification limits

The selftests exercise synthetic forms, a temporary durable authority store,
clocks, concurrent consumers, and OS privilege failures. Store tests replace
root ownership checks only for the temporary fixtures; separate tests retain
the real privilege checks. No privileged helper installation, sudo policy,
native Outlook Accessibility labels, real instruction record, or real message
is verified by these tests.
