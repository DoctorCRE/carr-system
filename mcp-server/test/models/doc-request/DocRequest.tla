---- MODULE DocRequest ----
EXTENDS Naturals, Sequences, FiniteSets
VARIABLES phase, authorized, asks, receipts, answer, owner, turns, reserved
vars == <<phase, authorized, asks, receipts, answer, owner, turns, reserved>>
\* Start with an earlier human ask at sequence 0, as in the PostgreSQL regression.
Init == /\ phase = "absent" /\ authorized = TRUE /\ asks = 0 /\ receipts = 0
        /\ answer = "idle" /\ owner = "none" /\ turns = <<0>> /\ reserved = 0
\* migrations/1141_doc_request_submission.sql:44 locks the conversation before max(sequence).
Submit == /\ phase = "absent" /\ authorized /\ owner = "none"
          /\ phase' = "pending" /\ asks' = 1 /\ owner' = "ask" /\ reserved' = Len(turns)
          /\ UNCHANGED <<authorized, receipts, answer, turns>>
\* migrations/1141_doc_request_submission.sql:69-72 commits ask and receipt together.
Commit == /\ phase = "pending" /\ owner = "ask" /\ phase' = "accepted"
          /\ receipts' = 1 /\ turns' = Append(turns, reserved) /\ owner' = "none"
          /\ UNCHANGED <<authorized, asks, answer, reserved>>
\* migrations/1141_doc_request_submission.sql:69 raises on receipt failure; PostgreSQL rolls back the transaction.
Rollback == /\ phase = "pending" /\ owner = "ask" /\ phase' = "absent"
            /\ asks' = 0 /\ owner' = "none"
            /\ UNCHANGED <<authorized, receipts, answer, turns, reserved>>
\* migrations/1141_doc_request_submission.sql:106 locks the header; :121 allocates the sequence.
Answer == /\ answer = "idle" /\ owner = "none" /\ answer' = "pending"
          /\ owner' = "answer" /\ reserved' = Len(turns)
          /\ UNCHANGED <<phase, authorized, asks, receipts, turns>>
\* migrations/1141_doc_request_submission.sql:118 inserts the answer in the caller transaction.
\* mcp-server/test/doc-conversation-postgres.test.mjs:194 commits that transaction.
AnswerCommit == /\ answer = "pending" /\ owner = "answer" /\ answer' = "committed"
                /\ turns' = Append(turns, reserved) /\ owner' = "none"
                /\ UNCHANGED <<phase, authorized, asks, receipts, reserved>>
\* mcp-server/test/doc-conversation-postgres.test.mjs:228 rolls back the append transaction.
AnswerRollback == /\ answer = "pending" /\ owner = "answer" /\ answer' = "idle"
                  /\ owner' = "none" /\ UNCHANGED <<phase, authorized, asks, receipts, turns, reserved>>
\* migrations/1141_doc_request_submission.sql:44 and :106 wait for the same header lock.
Wait == /\ ((owner = "ask" /\ answer = "idle") \/ (owner = "answer" /\ phase = "absent" /\ authorized))
        /\ UNCHANGED vars
\* migrations/1141_doc_request_submission.sql:28 and :45 check current actor and grant access.
Revoke == /\ phase # "pending" /\ authorized /\ authorized' = FALSE
          /\ UNCHANGED <<phase, asks, receipts, answer, owner, turns, reserved>>
\* migrations/1141_doc_request_submission.sql:60 returns the stored receipt.
Replay == /\ phase = "accepted" /\ authorized /\ UNCHANGED vars
\* migrations/1141_doc_request_submission.sql:43 serializes the same actor/key before replay.
Duplicate == /\ phase = "accepted" /\ authorized /\ UNCHANGED vars
\* migrations/1141_doc_request_submission.sql:49 and :58 refuse unavailable access or key reuse.
Refuse == /\ (~authorized \/ phase = "accepted") /\ UNCHANGED vars
Next == Submit \/ Commit \/ Rollback \/ Answer \/ AnswerCommit \/ AnswerRollback \/ Wait \/ Revoke \/ Replay \/ Duplicate \/ Refuse
Spec == Init /\ [][Next]_vars
\* An accepted ask has one committed ask and one immutable receipt.
AtomicCommitted == phase # "pending" => asks = receipts
AtMostOnce == /\ asks <= 1 /\ receipts <= 1
\* Partners see every committed turn once, in consecutive sequence order.
ConsecutiveTurns == \A i \in 1..Len(turns): turns[i] = i - 1
\* The next writer cannot reserve a committed sequence or overwrite another writer's reservation.
ExclusiveAllocation == /\ (owner = "none" \/ reserved = Len(turns))
                       /\ ((phase = "pending") = (owner = "ask"))
                       /\ ((answer = "pending") = (owner = "answer"))
====
