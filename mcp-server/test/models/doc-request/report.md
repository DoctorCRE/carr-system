# Queued ask and assistant answer

TLC checked AtomicCommitted, AtMostOnce, ConsecutiveTurns and ExclusiveAllocation.
All four invariants and default deadlock checking pass. The summary reports 20 model
states, 33 model transitions, 14 chart states and 24 chart transitions.

Both writers acquire the same conversation lock before allocating the next sequence.
The PostgreSQL regression reproduced the sequence unique violation on c15575e8.
Both transaction orders pass with the shared lock. The README maps every action to a
named test and documents the model scope and regeneration command.
