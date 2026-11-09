---- MODULE Receipts ----
VARIABLES transaction, receipt
vars == <<transaction, receipt>>
TypeOK == /\ transaction \in {"Quiet", "Open", "Committed"}
          /\ receipt \in {"Hidden", "Pending"}
Init == /\ transaction = "Quiet" /\ receipt = "Hidden"
\* migrations/1166_subject_watch_changes.sql:105 capture_subject_watch_changes queues current transaction.
RegisteredWrite == /\ transaction = "Quiet" /\ transaction' = "Open" /\ UNCHANGED receipt
KeyedRawIngress == /\ transaction = "Quiet" /\ transaction' = "Open" /\ UNCHANGED receipt
UnkeyedRawIngress == /\ transaction = "Quiet" /\ transaction' = "Open" /\ UNCHANGED receipt
\* migrations/1166_subject_watch_changes.sql:193 Canonical touch and early flush retain capture_before and are invisible outside the transaction.
AnotherWrite == /\ transaction = "Open" /\ UNCHANGED vars
\* PostgreSQL ROLLBACK restores source, baseline and receipt together at rollback.
Rollback == /\ transaction = "Open" /\ transaction' = "Quiet" /\ UNCHANGED receipt
\* migrations/1166_subject_watch_changes.sql:188 Deferred subject_watch_commit compares final material and inserts one generation/transaction key.
Commit == /\ transaction = "Open" /\ transaction' = "Committed" /\ receipt' = "Pending"
\* Registered idempotency replay emits no source event.
Replay == /\ transaction = "Committed" /\ UNCHANGED vars
EquivalentCommit == /\ transaction = "Quiet" /\ UNCHANGED vars
Next == EquivalentCommit \/ RegisteredWrite \/ KeyedRawIngress \/ UnkeyedRawIngress \/ AnotherWrite \/ Rollback \/ Commit \/ Replay
Spec == Init /\ [][Next]_vars
\* Never publish a receipt for a rolled back change.
AtomicCapture == ~(receipt = "Pending" /\ transaction = "Quiet")
\* Never lose a committed material change, including raw keyed ingress.
NoLostCapture == ~(receipt = "Hidden" /\ transaction = "Committed")
\* Never expose intermediate writes as committed receipts.
NoIntermediateReceipt == ~(receipt = "Pending" /\ transaction = "Open")
====
