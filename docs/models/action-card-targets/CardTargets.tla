---- MODULE CardTargets ----
EXTENDS Naturals
VARIABLES target, detail, actions
vars == <<target, detail, actions>>
Init == /\ target = "Requested" /\ detail = FALSE /\ actions = FALSE
ReadCurrent == /\ target \in {"Requested", "Foreign", "Stale"}
               /\ target' = "Resolved" /\ detail' = TRUE /\ actions' = TRUE
ReadForeign == /\ target = "Requested" /\ target' = "Foreign" /\ detail' = FALSE /\ actions' = FALSE
ReadMissing == /\ target \in {"Requested", "Resolved"} /\ target' = "Missing" /\ detail' = FALSE /\ actions' = FALSE
ReadChanged == /\ target = "Resolved" /\ target' = "Stale" /\ detail' = TRUE /\ actions' = FALSE
ReadRetired == /\ target = "Resolved" /\ target' = "Retired" /\ detail' = FALSE /\ actions' = FALSE
ReadReviewed == /\ target = "Resolved" /\ target' = "Resolved" /\ detail' = TRUE /\ actions' = FALSE
ReadZeroDecisions == /\ target = "Requested" /\ target' = "Resolved" /\ detail' = TRUE /\ actions' = FALSE
Next == ReadCurrent \/ ReadForeign \/ ReadMissing \/ ReadChanged \/ ReadRetired \/ ReadReviewed \/ ReadZeroDecisions
Spec == Init /\ [][Next]_vars
TypeOK == /\ target \in {"Requested","Resolved","Foreign","Missing","Stale","Retired"} /\ detail \in BOOLEAN /\ actions \in BOOLEAN
PrivateDetail == target \in {"Foreign","Missing","Retired"} => ~detail
CurrentActions == actions => target = "Resolved"
====
