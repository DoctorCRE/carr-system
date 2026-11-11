--------------------------- MODULE Acceptance ---------------------------
EXTENDS Naturals
VARIABLES state, source, reviewed, cart, base, writes, others, notified
vars == <<state, source, reviewed, cart, base, writes, others, notified>>
Init == /\ state = "pending" /\ source = 1 /\ reviewed = 0 /\ cart = 0 /\ base = 0 /\ writes = 0 /\ others = 0 /\ notified = 0
Review == /\ state \in {"pending", "source-changed"} /\ state' = "reviewed" /\ reviewed' = source /\ base' = cart /\ UNCHANGED <<source,cart,writes,others,notified>>
Change == /\ source < 2 /\ state \in {"reviewed", "accepting", "unknown"} /\ source' = source + 1 /\ UNCHANGED <<state,reviewed,cart,base,writes,others,notified>>
Accept == /\ state = "reviewed" /\ state' = "accepting" /\ UNCHANGED <<source,reviewed,cart,base,writes,others,notified>>
Unknown == /\ state = "accepting" /\ state' = "unknown" /\ UNCHANGED <<source,reviewed,cart,base,writes,others,notified>>
Commit == /\ state \in {"accepting","unknown"} /\ source = reviewed /\ cart = base /\ writes = 0 /\ state' = "accepted" /\ cart' = cart + 1 /\ writes' = 1 /\ UNCHANGED <<source,reviewed,base,others,notified>>
Conflict == /\ state \in {"accepting","unknown"} /\ (source # reviewed \/ cart # base) /\ state' = "source-changed" /\ UNCHANGED <<source,reviewed,cart,base,writes,others,notified>>
Keep == /\ state = "reviewed" /\ source = reviewed /\ cart = base /\ state' = "kept" /\ UNCHANGED <<source,reviewed,cart,base,writes,others,notified>>
Retry == /\ state = "accepted" /\ UNCHANGED vars
Notify == /\ notified < source /\ notified' = source /\ UNCHANGED <<state,source,reviewed,cart,base,writes,others>>
Retire == /\ state # "retired" /\ state' = "retired" /\ UNCHANGED <<source,reviewed,cart,base,writes,others,notified>>
OtherPartner == /\ state \in {"reviewed","accepting","unknown"} /\ others = 0 /\ cart' = cart + 1 /\ others' = 1 /\ UNCHANGED <<state,source,reviewed,base,writes,notified>>
Next == OtherPartner \/ Review \/ Change \/ Accept \/ Unknown \/ Commit \/ Conflict \/ Keep \/ Retry \/ Notify \/ Retire
Spec == Init /\ [][Next]_vars
Once == writes <= 1
Guarded == state = "accepted" => source = reviewed
NoClientWrites == cart = writes + others
Dedup == notified <= source
=============================================================================
