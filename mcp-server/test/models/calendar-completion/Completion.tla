---------------- MODULE Completion ----------------
EXTENDS Naturals
VARIABLES state, touches, confirmed
vars == <<state,touches,confirmed>>
Init == /\ state = "Review" /\ touches = 0 /\ confirmed = FALSE
Confirm == /\ state = "Review" /\ state' = "Applying" /\ UNCHANGED <<touches,confirmed>>
Commit == /\ state = "Applying" /\ state' = "Complete" /\ touches' = 1 /\ confirmed' = TRUE
Crash == /\ state = "Applying" /\ state' = "Review" /\ UNCHANGED <<touches,confirmed>>
Revise == /\ state = "Complete" /\ state' = "Review" /\ UNCHANGED <<touches,confirmed>>
Retry == /\ state = "Complete" /\ UNCHANGED vars
Next == Confirm \/ Commit \/ Crash \/ Revise \/ Retry
NoDuplicate == touches <= 1
NoUnconfirmedTouch == touches > 0 => confirmed
Spec == Init /\ [][Next]_vars
====================================================
