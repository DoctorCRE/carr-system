# Triage Labels

The skills use five canonical triage roles. Work Requests carry no free-form
labels, so each role maps to a request state or a `review-and-triage`
classification.

| Label in mattpocock/skills | In CARR Work Requests                                  | Meaning                                  |
| -------------------------- | ------------------------------------------------------ | ---------------------------------------- |
| `needs-triage`             | state `captured`                                       | Maintainer needs to evaluate this issue  |
| `needs-info`               | state `needs_joe`                                      | Waiting on Joe's answer to scope         |
| `ready-for-agent`          | triaged, classification `operational`                  | Fully specified, ready for an AFK agent  |
| `ready-for-human`          | triaged, classification `needs_judgment` or `safety_review` | Needs human judgment or a safety review |
| `wontfix`                  | `decline-work-request` (state withdrawn)               | Will not be actioned                     |

`review-and-triage` is human-only. When a skill says to apply a triage label, it
states the proposed classification and the reason, and Joe records it.
`answer-work-request-for-joe` (direct human only) moves `needs_joe` back to triaged.
