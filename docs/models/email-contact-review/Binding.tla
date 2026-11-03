---- MODULE Binding ----
EXTENDS Naturals, FiniteSets
VARIABLES phase, observed, current, findings
vars == <<phase, observed, current, findings>>
Init == /\ phase = "Prepared" /\ observed = 0 /\ current = 0 /\ findings = {}
Change == /\ phase \in {"Prepared", "Held"} /\ current' = 1 - current
          /\ UNCHANGED <<phase, observed, findings>>
Submit == /\ phase = "Prepared"
          /\ phase' = IF observed = current THEN "Recorded" ELSE "Held"
          /\ findings' = IF observed = current THEN {observed} ELSE findings
          /\ UNCHANGED <<observed, current>>
Refresh == /\ phase = "Held" /\ observed' = current /\ phase' = "Prepared"
           /\ UNCHANGED <<current, findings>>
Replay == /\ phase = "Recorded" /\ UNCHANGED vars
Next == Change \/ Submit \/ Refresh \/ Replay
NoStaleBinding == phase = "Recorded" => findings = {current}
HeldWritesNothing == phase = "Held" => findings = {}
AtMostOne == Cardinality(findings) <= 1
====
