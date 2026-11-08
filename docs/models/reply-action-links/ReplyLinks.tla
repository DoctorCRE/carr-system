---- MODULE ReplyLinks ----

VARIABLES session, card
vars == <<session, card>>

sessionStates == {"SignedIn", "SignedOut"}
cardStates == {"Consumed", "Expired", "Issued", "Refused", "Review", "Unissued"}

TypeOK ==
    /\ session \in sessionStates
    /\ card \in cardStates

Init ==
    /\ session = "SignedIn"
    /\ card = "Unissued"

SignOut ==
    /\ (session \in {"SignedIn"} \/ card \in {"Review"})
    /\ \/ (session = "SignedIn" /\ session' = "SignedOut")
       \/ (session \notin {"SignedIn"} /\ UNCHANGED session)
    /\ \/ (card = "Review" /\ card' = "Refused")
       \/ (card \notin {"Review"} /\ UNCHANGED card)

SignIn ==
    /\ \/ (session = "SignedOut" /\ session' = "SignedIn")
    /\ UNCHANGED <<card>>

IssueCurrentProposal ==
    /\ \/ (card = "Unissued" /\ card' = "Issued")
    /\ UNCHANGED <<session>>

ResolveOwningSession ==
    /\ session = "SignedIn" \* mcp-server/src/action-links.js:54 and tools.js:9267
    /\ \/ (card = "Issued" /\ card' = "Review")
    /\ UNCHANGED <<session>>

ResolveForeignOrSignedOut ==
    /\ \/ (card = "Issued" /\ card' = "Refused")
    /\ UNCHANGED <<session>>

ResolveAgainWithoutMutation ==
    /\ session = "SignedIn" \* mcp-server/src/action-links.js:54 and tools.js:9267
    /\ \/ (card = "Review" /\ card' = "Review")
    /\ UNCHANGED <<session>>

EditOrRetireSource ==
    /\ \/ (card = "Issued" /\ card' = "Expired")
       \/ (card = "Review" /\ card' = "Expired")
    /\ UNCHANGED <<session>>

ExplicitDomainApproval ==
    /\ session = "SignedIn" \* mcp-server/src/action-links.js:54 and tools.js:9267
    /\ \/ (card = "Review" /\ card' = "Consumed")
    /\ UNCHANGED <<session>>

ResolveOrActAgain ==
    /\ \/ (card = "Consumed" /\ card' = "Refused")
    /\ UNCHANGED <<session>>

ResolveOrActStaleReference ==
    /\ \/ (card = "Expired" /\ card' = "Refused")
    /\ UNCHANGED <<session>>

ResolveFreshEligibleReference ==
    /\ session = "SignedIn" \* mcp-server/src/action-links.js:54 and tools.js:9267
    /\ \/ (card = "Refused" /\ card' = "Review")
    /\ UNCHANGED <<session>>

Next == SignOut \/ SignIn \/ IssueCurrentProposal \/ ResolveOwningSession \/ ResolveForeignOrSignedOut \/ ResolveAgainWithoutMutation \/ EditOrRetireSource \/ ExplicitDomainApproval \/ ResolveOrActAgain \/ ResolveOrActStaleReference \/ ResolveFreshEligibleReference

Spec == Init /\ [][Next]_vars

PrivateAfterSignOut == ~(session = "SignedOut" /\ card = "Review")

====
