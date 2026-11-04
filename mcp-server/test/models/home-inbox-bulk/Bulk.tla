---- MODULE Bulk ----
EXTENDS TLC, FiniteSets, Naturals
VARIABLES prior, current, applied, undone, phase
vars == <<prior,current,applied,undone,phase>>
States == {"working","snoozed","set-aside","auto-settled","done","override"}
Init == /\ prior \in [1..2 -> States] /\ prior[2] = "working" /\ current = prior
        /\ applied = {} /\ undone = {} /\ phase = "ready"
Apply == /\ phase = "ready"
         /\ \E target \in {"snoozed","set-aside"}, allowed \in SUBSET (1..2):
              /\ applied' = {i \in allowed : prior[i] # target /\ ~(prior[i] = "auto-settled" /\ target = "set-aside")}
              /\ current' = [i \in 1..2 |-> IF i \in applied' THEN target ELSE prior[i]]
         /\ phase' = "applied" /\ UNCHANGED <<prior,undone>>
UndoOne == /\ phase = "applied"
           /\ \E i \in applied \ undone:
                 /\ current' = [current EXCEPT ![i] = prior[i]]
                 /\ undone' = undone \cup {i}
           /\ UNCHANGED <<prior,applied,phase>>
UndoAll == /\ phase = "applied" /\ current' = prior /\ undone' = applied
           /\ phase' = "undone" /\ UNCHANGED <<prior,applied>>
Stay == UNCHANGED vars
Next == Apply \/ UndoOne \/ UndoAll \/ Stay
NoopUntouched == \A i \in (1..2) \ applied: current[i] = prior[i]
ExactUndo == \A i \in undone: current[i] = prior[i]
AutomaticNotManual == \A i \in 1..2: (prior[i]="auto-settled" /\ current[i]="set-aside") => FALSE
Spec == Init /\ [][Next]_vars
====
