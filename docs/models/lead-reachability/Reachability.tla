---- MODULE Reachability ----
EXTENDS TLC
VARIABLES route, contact, eligible, conflict, classification
vars == <<route, contact, eligible, conflict, classification>>
Classify(r,c,e,f) == IF r /\ c /\ e /\ ~f THEN "Reachable" ELSE "Research"
Init == /\ route = FALSE /\ contact = FALSE /\ eligible = TRUE /\ conflict = FALSE /\ classification = "Research"
\* lead-follow-up.ts leadReachability evaluates one complete read snapshot.
Refresh == \E r,c,e,f \in BOOLEAN:
  /\ route' = r /\ contact' = c /\ eligible' = e /\ conflict' = f
  /\ classification' = Classify(r,c,e,f)
TypeOK == /\ route \in BOOLEAN /\ contact \in BOOLEAN /\ eligible \in BOOLEAN /\ conflict \in BOOLEAN
          /\ classification \in {"Reachable", "Research"}
NoUnsupportedReachable == classification = Classify(route,contact,eligible,conflict)
Spec == Init /\ [][Refresh]_vars
====
