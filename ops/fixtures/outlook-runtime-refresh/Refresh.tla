----------------------- MODULE Refresh -----------------------
EXTENDS Naturals
VARIABLES phase, writer, swapped, registered
vars == <<phase, writer, swapped, registered>>
Init == /\ phase = "idle" /\ writer \in BOOLEAN /\ swapped = FALSE /\ registered = TRUE
Plan == /\ phase = "idle" /\ phase' \in {"planned", "current", "quiescing"} /\ UNCHANGED <<writer, swapped, registered>>
Disable == /\ phase = "quiescing" /\ phase' = "draining" /\ UNCHANGED <<writer, swapped, registered>>
Release == /\ phase = "draining" /\ writer /\ writer' = FALSE /\ UNCHANGED <<phase, swapped, registered>>
Timeout == /\ phase = "draining" /\ phase' = "restoring" /\ UNCHANGED <<writer, swapped, registered>>
Drain == /\ phase = "draining" /\ ~writer /\ phase' = "swapping" /\ registered' = FALSE /\ UNCHANGED <<writer, swapped>>
Swap == /\ phase = "swapping" /\ phase' = "verifying" /\ swapped' = TRUE /\ UNCHANGED <<writer, registered>>
Verify == /\ phase = "verifying" /\ phase' = "restoring" /\ UNCHANGED <<writer, swapped, registered>>
Restore == /\ phase = "restoring" /\ phase' = "done" /\ registered' = TRUE /\ UNCHANGED <<writer, swapped>>
Done == /\ phase \in {"done", "planned", "current"} /\ UNCHANGED vars
Next == Plan \/ Disable \/ Release \/ Timeout \/ Drain \/ Swap \/ Verify \/ Restore \/ Done
NoSendInterrupted == writer => registered
SwapDrained == swapped => ~writer
Spec == Init /\ [][Next]_vars
==============================================================
