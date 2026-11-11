----------------------------- MODULE Proposals -----------------------------
EXTENDS Naturals, TLC
VARIABLES active, version, receipts, reply, phase, before, authorized, base, requestedBase
vars == <<active, version, receipts, reply, phase, before, authorized, base, requestedBase>>
Init == /\ active = TRUE /\ version = 0 /\ receipts = [k \in 1..2 |-> 0]
 /\ reply = 0 /\ phase = "Ready" /\ before = 0 /\ authorized = TRUE /\ base = [k \in 1..2 |-> 0] /\ requestedBase = 0
Save(k) == /\ active /\ version < 2 /\ receipts[k] = 0 /\ base[k] = version
 /\ version' = version+1 /\ receipts' = [receipts EXCEPT ![k] = version+1]
 /\ reply' = version+1 /\ phase' = "Saved" /\ before' = version /\ authorized' = active
 /\ requestedBase' = base[k] /\ UNCHANGED <<active,base>>
Retry(k) == /\ active /\ receipts[k] > 0
 /\ reply' = receipts[k] /\ phase' = "Saved" /\ before' = version /\ authorized' = active
 /\ requestedBase' = base[k] /\ UNCHANGED <<active,version,receipts,base>>
Conflict == /\ active /\ (\E k \in 1..2: base[k] # version)
 /\ phase' = "Conflict" /\ before' = version /\ authorized' = active
 /\ UNCHANGED <<active,version,receipts,reply,base,requestedBase>>
Reload(k) == /\ active /\ reply' = version /\ phase' = "Ready" /\ before' = version /\ authorized' = active
 /\ base' = [base EXCEPT ![k] = version] /\ UNCHANGED <<active,version,receipts,requestedBase>>
Expire == /\ active /\ active' = FALSE /\ phase' = "Expired" /\ before' = version /\ authorized' = FALSE
 /\ UNCHANGED <<version,receipts,reply,base,requestedBase>>
Denied == /\ ~active /\ before' = version /\ authorized' = FALSE
 /\ UNCHANGED <<active,version,receipts,reply,phase,base,requestedBase>>
Next == (\E k \in 1..2: Save(k) \/ Retry(k) \/ Reload(k)) \/ Conflict \/ Expire \/ Denied
Spec == Init /\ [][Next]_vars
NoUnauthorizedWrite == ~authorized => version = before
NoStaleWrite == version # before => requestedBase = before
ConfirmedVersionExists == reply <= version
ReceiptVersionsUnique == receipts[1] = 0 \/ receipts[2] = 0 \/ receipts[1] # receipts[2]
=============================================================================
