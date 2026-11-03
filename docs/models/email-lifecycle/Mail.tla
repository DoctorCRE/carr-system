---- MODULE Mail ----
EXTENDS Naturals, TLC
VARIABLES state, version, actions, noted
vars == <<state, version, actions, noted>>
Init == state = "live" /\ version = 0 /\ actions = 0 /\ noted = 0
Act == state = "live" /\ actions < 2 /\ actions' = actions + 1 /\ UNCHANGED <<state,version,noted>>
Observe == state = "live" /\ state' \in {"deleted","recalled","superseded"} /\ version' = 1 /\ noted' = actions /\ UNCHANGED actions
Replay == state # "live" /\ UNCHANGED vars
RefuseStaleOrForeign == UNCHANGED vars
Next == Act \/ Observe \/ Replay \/ RefuseStaleOrForeign
Spec == Init /\ [][Next]_vars
TerminalNotes == state # "live" => actions = noted
Versioned == (state = "live" /\ version = 0) \/ (state # "live" /\ version = 1)
====
