# Issue tracker: CARR Work Requests

Issues and specs for this repo are CARR Work Requests in the record layer. They are
not GitHub Issues: this repo has never used GitHub Issues, and the write law keeps
work in the database instead of `.md` files. Reach the verbs through the CARR
connector, or through `./run.sh call <verb> '<json>'` when the connector is not loaded.

Every write takes a fresh UUID `idempotency_key`. Every change to an existing
request takes `base_version` from a fresh `work-request-card` read.

## Conventions

- **Create an issue**: `report-problem` with `situation` (a short description of
  the work, matched against shared doctrine to source the request), `title`,
  `desired_outcome`, and `acceptance_criteria`, given as `[{id, text}]` with ids
  like `AC-1`. The request lands in state `captured` and gets a ref like `WR-123`.
- **Read an issue**: `work-request-card` with the `WR-` ref.
- **List issues**: `current-work-requests`. It returns at most 20 shared requests
  that still need a bounded human action, and it takes no filters.
- **Triage**: `review-and-triage`, which is **human-only**. An agent proposes the
  classification and Joe records it. See `triage-labels.md`.
- **Withdraw**: `decline-work-request` with an `exit_reason`, or
  `supersede-work-request` naming the replacement request. Both work only from
  `captured`.
- **Plan**: `propose-ready-plan` on a triaged request. Heavy work goes through the
  full research, review and acceptance path.

There is no comment verb. Discussion belongs in the PR that delivers the work.

## Before filing

Follow the Work Request priority policy at the top of [AGENTS.md](../../AGENTS.md).
Work this session can finish now is done, not filed.

## Pull requests as a triage surface

**PRs as a request surface: no.**

## When a skill says "publish to the issue tracker"

Call `report-problem`.

## When a skill says "fetch the relevant ticket"

Call `work-request-card` with the `WR-` ref.
