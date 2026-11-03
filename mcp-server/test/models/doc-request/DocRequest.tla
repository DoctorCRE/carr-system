---- MODULE DocRequest ----
EXTENDS Naturals
VARIABLES phase, authorized, asks, receipts
vars == <<phase, authorized, asks, receipts>>
Init == /\ phase = "absent" /\ authorized = TRUE /\ asks = 0 /\ receipts = 0
Submit == /\ phase = "absent" /\ authorized /\ phase' = "pending" /\ asks' = 1 /\ UNCHANGED <<authorized, receipts>>
Commit == /\ phase = "pending" /\ phase' = "accepted" /\ receipts' = 1 /\ UNCHANGED <<authorized, asks>>
Rollback == /\ phase = "pending" /\ phase' = "absent" /\ asks' = 0 /\ UNCHANGED <<authorized, receipts>>
Revoke == /\ phase # "pending" /\ authorized /\ authorized' = FALSE /\ UNCHANGED <<phase, asks, receipts>>
Replay == /\ phase = "accepted" /\ authorized /\ UNCHANGED vars
Duplicate == /\ phase = "accepted" /\ authorized /\ UNCHANGED vars
Refuse == /\ (~authorized \/ phase = "accepted") /\ UNCHANGED vars
Next == Submit \/ Commit \/ Rollback \/ Revoke \/ Replay \/ Duplicate \/ Refuse
Spec == Init /\ [][Next]_vars
AtomicCommitted == phase # "pending" => asks = receipts
AtMostOnce == /\ asks <= 1 /\ receipts <= 1
====
