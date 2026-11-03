---- MODULE Inbox ----
EXTENDS TLC
VARIABLES source, disposition, override
vars == <<source, disposition, override>>
Init == source = "open" /\ disposition = "inbox" /\ override = FALSE
Start == source = "open" /\ source' = "working" /\ UNCHANGED <<disposition, override>>
Resolve == source \in {"open", "working"} /\ source' = "done" /\ UNCHANGED <<disposition, override>>
Reopen == source = "done" /\ disposition = "inbox" /\ source' = "open" /\ disposition' = "inbox" /\ override' = FALSE
Snooze == disposition = "inbox" /\ disposition' = "snoozed" /\ override' = FALSE /\ UNCHANGED source
Expire == disposition = "snoozed" /\ disposition' = "inbox" /\ UNCHANGED <<source, override>>
Settle == disposition # "settled" /\ disposition' = "settled" /\ override' = FALSE /\ UNCHANGED source
Undo == disposition # "inbox" \/ (source = "done" /\ ~override)
Unsettle == Undo /\ disposition' = "inbox" /\ override' = TRUE /\ UNCHANGED source
Stay == UNCHANGED vars
Next == Start \/ Resolve \/ Reopen \/ Snooze \/ Expire \/ Settle \/ Unsettle \/ Stay
Automatic == source = "done" /\ disposition = "inbox" /\ ~override
Section == IF disposition # "inbox" THEN disposition ELSE IF Automatic THEN "auto-settled" ELSE IF override THEN "inbox" ELSE IF source = "working" THEN "working" ELSE "inbox"
ResolvedOnly == (Section = "auto-settled") => source = "done"
OverrideWins == override => Section # "auto-settled"
SnoozeWins == disposition = "snoozed" => Section = "snoozed"
Spec == Init /\ [][Next]_vars
====
