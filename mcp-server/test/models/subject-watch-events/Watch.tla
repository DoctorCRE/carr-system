------------------------------ MODULE Watch ------------------------------
EXTENDS Naturals, FiniteSets
VARIABLES subscribed, version, baseline, delivered, ack, cursor, visited
vars == <<subscribed, version, baseline, delivered, ack, cursor, visited>>
Init == /\ subscribed = TRUE /\ version = 0 /\ baseline = 0
        /\ delivered = {} /\ ack = FALSE /\ cursor = 0 /\ visited = {}
Change == /\ version < 2 /\ version' = version + 1
          /\ UNCHANGED <<subscribed, baseline, delivered, ack, cursor, visited>>
Consume == /\ subscribed /\ version > baseline
           /\ delivered' = delivered \cup {version}
           /\ UNCHANGED <<subscribed, version, baseline, ack, cursor, visited>>
Acknowledge == /\ ack' = TRUE
               /\ UNCHANGED <<subscribed, version, baseline, delivered, cursor, visited>>
Unsubscribe == /\ subscribed' = FALSE
               /\ UNCHANGED <<version, baseline, delivered, ack, cursor, visited>>
Scan == /\ cursor < 3 /\ cursor' = cursor + 1 /\ visited' = visited \cup {cursor + 1}
        /\ UNCHANGED <<subscribed, version, baseline, delivered, ack>>
Done == UNCHANGED vars
Next == Change \/ Consume \/ Acknowledge \/ Unsubscribe \/ Scan \/ Done
NoBaselineWake == 0 \notin delivered
UniqueWake == Cardinality(delivered) <= version
NoSkippedRecord == visited = 1..cursor
=============================================================================
