# Outlook ledger binding and normal receipts

```mermaid
stateDiagram-v2
    [*] --> Unbound
    Unbound --> Bound: Empty ledger or matching legacy identity evidence
    Unbound --> Held: Legacy identity evidence missing or conflicting
    Bound --> Held: Settings owner or account changes
    Bound --> Ready: Settings match retained binding
    Ready --> Running: Reconcile or replay or scan
    Running --> Running: Settings replaced, keep admitted identity
    Running --> Refused: Decoded account does not verify admitted mailbox
    Refused --> Receipted: Write refusal for admitted identity without requests
    Running --> Receipted: Write private receipt atomically
    Running --> Failed: Receipt write fails
    Ready --> Receipted: Outside schedule window
    Held --> Receipted: Record refusal without replay
    Receipted --> Ready: Next matching run
```

The local lock covers binding, processing and receipt publication. An active-writer skip leaves its receipt untouched. Corrupt state or binding refuses processing. Folder keys can change while the canonical account stays fixed. The decoder re-verifies the new key.

Private binding.json retains schema outlook-ledger-binding/v1, mailbox_owner and mailbox_account. Existing state migrates only when both filing_account and account_binding match settings. Other legacy states require deployment reconciliation with original identity and retained request keys. Deleting state or binding is not recovery.

Private receipt-latest.json records runtime identity, timestamp, configured owner/account, folder key, binding digest, binding verification and decoder account verification. Local verification does not establish authenticated authority. Deployment preflight and server checks establish that separately. Monitoring retains only validated owner, digests, booleans, timestamp, runtime identity, counts and outcome.

Tests exercise admission and failure transitions through run and the collector CLI. This is a design chart, not a TLC proof. The frozen OutlookSettings value admitted under the lock is the transaction context for source selection, schedule checks, copy helper, census, decoder, processing, requests and receipts. Production transport and decoder closures capture that same value before run. No step reloads identity during the transaction. A settings replacement takes effect on the next run, where a different owner or canonical account refuses before replay.

The settings-change tests replace the synthetic file immediately after successful binding, in both partner directions. Foreign Sent evidence refuses before requests are persisted or sent. An owner-only replacement retains the admitted identity in requests and receipts. The next run refuses the changed owner. A separate test exhausts the settings loader after the first read, including a census request, and exercises processing without another read. Transport and decoder tests verify the captured settings at invocation. This chart does not model filesystem tampering or authenticated server authority.
