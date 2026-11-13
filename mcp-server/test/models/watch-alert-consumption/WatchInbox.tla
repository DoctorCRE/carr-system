---- MODULE WatchInbox ----

VARIABLES notificationVisibility, homePlacement, canonicalWatchSubject
vars == <<notificationVisibility, homePlacement, canonicalWatchSubject>>

notificationVisibilityStates == {"Read", "Unread"}
homePlacementStates == {"Inbox", "Settled", "Snoozed"}
canonicalWatchSubjectStates == {"Completed", "NeedsAttention"}

TypeOK ==
    /\ notificationVisibility \in notificationVisibilityStates
    /\ homePlacement \in homePlacementStates
    /\ canonicalWatchSubject \in canonicalWatchSubjectStates

Init ==
    /\ notificationVisibility = "Unread"
    /\ homePlacement = "Inbox"
    /\ canonicalWatchSubject = "NeedsAttention"

Acknowledge ==
    /\ \/ (notificationVisibility = "Unread" /\ notificationVisibility' = "Read")
       \/ (notificationVisibility = "Read" /\ notificationVisibility' = "Read")
    /\ UNCHANGED <<homePlacement, canonicalWatchSubject>>

Settle ==
    /\ \/ (homePlacement = "Inbox" /\ homePlacement' = "Settled")
    /\ UNCHANGED <<notificationVisibility, canonicalWatchSubject>>

Unsettle ==
    /\ \/ (homePlacement = "Settled" /\ homePlacement' = "Inbox")
       \/ (homePlacement = "Snoozed" /\ homePlacement' = "Inbox")
    /\ UNCHANGED <<notificationVisibility, canonicalWatchSubject>>

Snooze ==
    /\ \/ (homePlacement = "Inbox" /\ homePlacement' = "Snoozed")
    /\ UNCHANGED <<notificationVisibility, canonicalWatchSubject>>

Next == Acknowledge \/ Settle \/ Unsettle \/ Snooze

Spec == Init /\ [][Next]_vars

\* Reading a notification never completes its subject.
ReadCompletesWork == ~(notificationVisibility = "Read" /\ canonicalWatchSubject = "Completed")

\* Settling a Home card never completes its subject.
SettlementCompletesWork == ~(homePlacement = "Settled" /\ canonicalWatchSubject = "Completed")

\* Snoozing a Home card never completes its subject.
SnoozeCompletesWork == ~(homePlacement = "Snoozed" /\ canonicalWatchSubject = "Completed")

====
