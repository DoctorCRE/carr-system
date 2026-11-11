----------------------------- MODULE Proposals -----------------------------
EXTENDS Naturals, TLC
VARIABLES active, version, receipts, reply, phase, before, authorized
vars == <<active, version, receipts, reply, phase, before, authorized>>
Init == /\ active = TRUE /\ version = 0 /\ receipts = [k \in 1..2 |-> 0]
 /\ reply = 0 /\ phase = "Ready" /\ before = 0 /\ authorized = TRUE
Save(k) == /\ active /\ version < 2 /\ receipts[k] = 0
 /\ version' = version+1 /\ receipts' = [receipts EXCEPT ![k] = version+1]
 /\ reply' = version+1 /\ phase' = "Saved" /\ before' = version /\ authorized' = active
 /\ UNCHANGED active
Retry(k) == /\ active /\ receipts[k] > 0
 /\ reply' = receipts[k] /\ phase' = "Saved" /\ before' = version /\ authorized' = active
 /\ UNCHANGED <<active,version,receipts>>
Conflict == /\ active /\ version > 0
 /\ phase' = "Conflict" /\ before' = version /\ authorized' = active
 /\ UNCHANGED <<active,version,receipts,reply>>
Reload == /\ active /\ reply' = version /\ phase' = "Ready" /\ before' = version /\ authorized' = active
 /\ UNCHANGED <<active,version,receipts>>
Expire == /\ active /\ active' = FALSE /\ phase' = "Expired" /\ before' = version /\ authorized' = FALSE
 /\ UNCHANGED <<version,receipts,reply>>
Denied == /\ ~active /\ before' = version /\ authorized' = FALSE
 /\ UNCHANGED <<active,version,receipts,reply,phase>>
Next == (\E k \in 1..2: Save(k) \/ Retry(k)) \/ Conflict \/ Reload \/ Expire \/ Denied
Spec == Init /\ [][Next]_vars
NoUnauthorizedWrite == ~authorized => version = before
ConfirmedVersionExists == reply <= version
ReceiptVersionsUnique == receipts[1] = 0 \/ receipts[2] = 0 \/ receipts[1] # receipts[2]
=============================================================================
