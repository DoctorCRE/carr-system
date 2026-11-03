---- MODULE Review ----
EXTENDS Naturals
VARIABLES phase, findings, writes, frozen
vars == <<phase, findings, writes, frozen>>
Init == /\ phase = "Unread" /\ findings = 0 /\ writes = 0 /\ frozen = FALSE
Resolve == /\ phase = "Unread" /\ phase' \in {"Held", "Prepared"}
           /\ frozen' = (phase' = "Prepared") /\ UNCHANGED <<findings, writes>>
Submit == /\ phase = "Prepared" /\ phase' \in {"Recorded", "Retry", "Refused"}
          /\ findings' \in {0, 1} /\ UNCHANGED <<writes, frozen>>
Retry == /\ phase = "Retry" /\ phase' \in {"Recorded", "Changed", "Refused"}
         /\ findings' \in {findings, 1} /\ UNCHANGED <<writes, frozen>>
Replay == /\ phase = "Recorded" /\ UNCHANGED vars
Done == /\ phase \in {"Held", "Changed", "Refused"} /\ UNCHANGED vars
RecoverHeld == /\ phase = "Held" /\ phase' = "Unread"
               /\ UNCHANGED <<findings, writes, frozen>>
RestoreSource == /\ phase = "Changed" /\ phase' = "Retry"
                 /\ UNCHANGED <<findings, writes, frozen>>
Next == Resolve \/ Submit \/ Retry \/ Replay \/ Done \/ RecoverHeld \/ RestoreSource
NoOverwrite == writes = 0
AtMostOne == findings <= 1
FrozenRetry == phase \in {"Retry", "Recorded", "Refused", "Changed"} => frozen
====
