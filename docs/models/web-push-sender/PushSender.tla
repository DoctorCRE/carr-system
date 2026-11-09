---- MODULE PushSender ----
EXTENDS TLC
VARIABLES delivery, attemptAuthority, subscription, submission
vars == <<delivery, attemptAuthority, subscription, submission>>
Init == /\ delivery = "Pending"
        /\ attemptAuthority = "RetryAllowed"
        /\ subscription = "Active"
        /\ submission = "Idle"
TypeOK == /\ delivery \in {"Pending","Held","Submitting","Accepted","Failed","Unknown","Cancelled","Expired"}
          /\ attemptAuthority \in {"RetryAllowed","Ambiguous"}
          /\ subscription \in {"Active","Revoked"}
          /\ submission \in {"Idle","Sending"}
PolicyHolds == /\ delivery = "Pending" \* migrations/1170_web_push_outbox.sql:154
               /\ delivery' = "Held"
               /\ UNCHANGED <<attemptAuthority,subscription,submission>>
HoldEnds == /\ delivery = "Held" \* migrations/1170_web_push_outbox.sql:77
            /\ delivery' = "Pending"
            /\ UNCHANGED <<attemptAuthority,subscription,submission>>
ClaimPersisted == /\ delivery = "Pending" \* migrations/1170_web_push_outbox.sql:77
                  /\ attemptAuthority = "RetryAllowed" \* migrations/1170_web_push_outbox.sql:82
                  /\ delivery' = "Submitting"
                  /\ UNCHANGED <<attemptAuthority,subscription,submission>>
BeginHTTP == /\ delivery = "Submitting" \* migrations/1170_web_push_outbox.sql:88
             /\ subscription = "Active" \* migrations/1170_web_push_outbox.sql:125
             /\ submission = "Idle" \* mcp-server/src/web-push-outbox.ts:39
             /\ submission' = "Sending"
             /\ UNCHANGED <<delivery,attemptAuthority,subscription>>
ProviderAccepts == /\ submission = "Sending" \* mcp-server/src/web-push-sender.ts:60
                   /\ delivery' = "Accepted" /\ submission' = "Idle"
                   /\ UNCHANGED <<attemptAuthority,subscription>>
PermanentRejection == /\ submission = "Sending" \* mcp-server/src/web-push-sender.ts:60
                      /\ delivery' = "Failed" /\ submission' = "Idle"
                      /\ UNCHANGED <<attemptAuthority,subscription>>
ExplicitRetryableRejection == /\ submission = "Sending" \* mcp-server/src/web-push-sender.ts:60
                             /\ delivery' = "Pending" /\ submission' = "Idle"
                             /\ UNCHANGED <<attemptAuthority,subscription>>
ConnectionLostOrWorkerCloses == /\ delivery = "Submitting" \* migrations/1170_web_push_outbox.sql:82
                               /\ delivery' = "Unknown" /\ submission' = "Idle"
                               /\ attemptAuthority' = "Ambiguous"
                               /\ UNCHANGED subscription
SourceOrSubscriptionUnavailable == /\ delivery \in {"Pending","Held"} \* migrations/1170_web_push_outbox.sql:125
                                   /\ delivery' = "Cancelled"
                                   /\ UNCHANGED <<attemptAuthority,subscription,submission>>
LifetimeEnds == /\ delivery \in {"Pending","Held"} \* migrations/1170_web_push_outbox.sql:128
                /\ delivery' = "Expired"
                /\ UNCHANGED <<attemptAuthority,subscription,submission>>
PreflightRefuses == /\ delivery = "Submitting" \* migrations/1170_web_push_outbox.sql:125
                   /\ submission = "Idle" \* mcp-server/src/web-push-outbox.ts:39
                   /\ delivery' = "Cancelled"
                   /\ UNCHANGED <<attemptAuthority,subscription,submission>>
Revoke == /\ subscription = "Active" \* migrations/1163_web_push_registry.sql:100
          /\ submission = "Idle" \* migrations/1170_web_push_outbox.sql:75
          /\ subscription' = "Revoked"
          /\ UNCHANGED <<delivery,attemptAuthority,submission>>
Next == PolicyHolds \/ HoldEnds \/ ClaimPersisted \/ BeginHTTP \/ ProviderAccepts \/ PermanentRejection \/ ExplicitRetryableRejection \/ ConnectionLostOrWorkerCloses \/ SourceOrSubscriptionUnavailable \/ LifetimeEnds \/ PreflightRefuses \/ Revoke
Spec == Init /\ [][Next]_vars
\* Unknown attempts never automatically retry.
NoAmbiguousRetry == attemptAuthority = "Ambiguous" => delivery = "Unknown"
\* Completed revocation prevents submission.
NoRevokedSubmission == ~(submission = "Sending" /\ subscription = "Revoked")
====
