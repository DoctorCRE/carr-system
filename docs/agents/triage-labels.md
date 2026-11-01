# Native triage labels

**Status:** Work Requests still live in the record layer: use `report-problem` and `work-request-card` until the cutover slice of WR-000675 lands. This file describes the GitHub flow that will replace it. Current triage remains human-only.

The vocabulary is defined in [.github/labels.json](../../.github/labels.json).
The [issue tracking contract](issue-tracker.md) defines ownership, public-data
restrictions, issue bodies, Project fields and PR closure. Label application
and migration are separate jobs; this slice only defines the contract.

## Readiness and outcomes

Use one current readiness label from `needs-triage`, `needs-info`,
`ready-for-agent`, or `ready-for-human`. Remove the previous readiness label
when changing readiness. `safety-review` and `blocked` are additional holds;
neither can be bypassed by applying a ready label. Remove each hold only when
its review or named dependency is resolved and the resolution is recorded.
Terminal labels replace readiness labels and holds when closing unsuccessful
or replaced work. Use one terminal label and record its reason.

| Label | Apply when | Required next information or action |
| --- | --- | --- |
| `needs-triage` | New work or materially changed scope needs evaluation | Classify ownership, scope and readiness |
| `needs-info` | Required information is missing | Name the question and who can answer |
| `ready-for-agent` | Scope, acceptance checks and dependencies permit agent execution | Execute within the authorized scope; honor any holds |
| `ready-for-human` | Human judgment is needed | Joe or Dell supplies the judgment or needs-Joe answer |
| `safety-review` | A safety review is needed before execution | Record the review and disposition before removing the hold |
| `blocked` | A concrete dependency prevents the next action | Name the dependency, owner and next action |
| `wontfix` | Work is deliberately withdrawn or declined | Record why; close without claiming delivery |
| `superseded` | Another issue replaces this work | Link the replacement; close without claiming delivery |
| `failed` | Execution ends unsuccessfully | Record the failure and opaque evidence references |

`ready-for-human` items and needs-Joe answers come from Joe or Dell. Agents may
prepare the question and engineering evidence. The broader direct-human-only
triage authority question is pending Joe's decision. This document neither
expands nor settles that authority.

The forms also apply one kind label: `type:task`, `type:bug`, or `type:slice`.
Kind labels describe the issue shape and do not indicate readiness.

## Legacy WR mapping

Use this mapping during the later migration, preserving the legacy WR ID in
the issue body. Classifications and holds remain distinct from execution
progress. No records are migrated by this documentation change.

| Legacy WR state or classification | Native readiness or outcome | Project Status / Delivery |
| --- | --- | --- |
| `captured` | `needs-triage` | Backlog / Not started |
| `needs_joe` | `needs-info`; use `ready-for-human` when judgment is the next action | Backlog / Not started |
| `triaged`, `operational` | `ready-for-agent` once acceptance and dependencies are sufficient; otherwise `needs-info` | Backlog, then Ready / Not started |
| `triaged`, `needs_judgment` | `ready-for-human` | Backlog / Not started |
| `safety_review` classification | `safety-review` plus the current readiness label | Backlog / Not started |
| `ready` | `ready-for-agent` or `ready-for-human`, according to the next executor | Ready / Not started |
| Execution started | Keep readiness; add `blocked` if necessary | In progress / Not started |
| Implementation awaiting review | Keep readiness and any holds | In review / Not started |
| Source accepted and final implementation PR merged | Clear readiness and resolved holds | Done / Awaiting verification |
| Production acceptance verified | No success label required | Done / Verified |
| Accepted work without a production effect | No success label required; record the reason | Done / Not applicable |
| Withdrawn or declined | `wontfix` | Done / Not applicable |
| Replaced | `superseded` with replacement link | Done / Not applicable |
| Execution failed | `failed` | Done / Not applicable; record any unresolved production effects separately |

Do not treat a legacy completion receipt as proof of a production release.
Verify its evidence before selecting Delivery. If production effects remain
unverified after failure or closure, retain Awaiting verification and name the
remaining check. Closed issues can still need delivery verification.

The legacy `review-and-triage` and `answer-work-request-for-joe` verbs are being
retired with the other Work Request verbs. They remain implemented until the
separate cutover; this contract does not remove or relax their current runtime
checks.
