---- MODULE Inbox ----
EXTENDS TLC
VARIABLES source, disposition, override, kind
vars == <<source, disposition, override, kind>>
Init == kind \in {"work", "fact_review", "tour_feedback"} /\ source = "open" /\ disposition = "inbox" /\ override = FALSE
Start == kind = "work" /\ source = "open" /\ source' = "working" /\ UNCHANGED <<disposition, override, kind>>
ResolveWork == kind = "work" /\ source \in {"open", "working"} /\ source' = "done" /\ UNCHANGED <<disposition, override, kind>>
Reopen == source = "done" /\ disposition = "inbox" /\ source' = "open" /\ disposition' = "inbox" /\ override' = FALSE /\ UNCHANGED kind
Snooze == disposition = "inbox" /\ disposition' = "snoozed" /\ override' = FALSE /\ UNCHANGED <<source, kind>>
Expire == disposition = "snoozed" /\ disposition' = "inbox" /\ UNCHANGED <<source, override, kind>>
Settle == disposition # "settled" /\ disposition' = "settled" /\ override' = FALSE /\ UNCHANGED <<source, kind>>
Undo == disposition # "inbox" \/ (source = "done" /\ ~override)
Unsettle == Undo /\ disposition' = "inbox" /\ override' = TRUE /\ UNCHANGED <<source, kind>>
Stay == UNCHANGED vars
ResolveFactReview == kind = "fact_review" /\ source = "open" /\ source' = "done" /\ UNCHANGED <<disposition, override, kind>>
ReceiveTourFeedback == kind = "tour_feedback" /\ source = "open" /\ source' = "done" /\ UNCHANGED <<disposition, override, kind>>
Next == Start \/ ResolveWork \/ ResolveFactReview \/ ReceiveTourFeedback \/ Reopen \/ Snooze \/ Expire \/ Settle \/ Unsettle \/ Stay
Automatic == source = "done" /\ disposition = "inbox" /\ ~override
Section == IF disposition # "inbox" THEN disposition ELSE IF Automatic THEN "auto-settled" ELSE IF override THEN "inbox" ELSE IF source = "working" THEN "working" ELSE "inbox"
ResolvedOnly == (Section = "auto-settled") => source = "done"
OverrideWins == override => Section # "auto-settled"
SnoozeWins == disposition = "snoozed" => Section = "snoozed"
Spec == Init /\ [][Next]_vars
====
