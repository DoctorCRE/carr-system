# Native issue tracking contract

**Status:** Work Requests still live in the record layer: use `report-problem` and `work-request-card` until the cutover slice of WR-000675 lands. This file describes the GitHub flow that will replace it. Current triage remains human-only.

WR675.S5.J2 defines the GitHub Issues contract from the WR675.S5.J1 plan.
This slice installs templates and documentation only. It migrates no Work
Requests, creates no issues or Project, applies no labels to GitHub, and changes
no repository settings. Existing Work Request records remain in place until a
separate migration and cutover.

## Ownership and intake

File work in the repository that owns its implementation. CARR runtime and
record-layer work belongs in `DoctorCRE/carr-system`; DoctorCRE application
work belongs in `jbookout/doctorcre-app`. One GitHub Project spans both
repositories. A cross-repository outcome uses linked issues in the owning
repositories, with dependencies and exact contract revisions recorded.

Use the engineering task, bug, or slice form in
[.github/ISSUE_TEMPLATE](../../.github/ISSUE_TEMPLATE/). Blank issues are disabled.
Each form starts with `needs-triage` and a `type:*` label. Those labels must exist
before intake is activated; this slice provides their definitions without
applying them. A label or Project field is tracking metadata, not execution
authority or a replacement for required checks.

## Public content rule

**carr-system is PUBLIC.** Include no client names, contact details, deal identifiers, deal facts (property addresses, rents, prices, terms),
document extracts, screenshots or identifying links. Write engineering
descriptions only, with opaque evidence references. This applies to issue titles,
bodies, comments, attachments, PRs and Project fields. Reproduce bugs with
synthetic data. Keep private evidence in its authorized record store and put
only its opaque reference in GitHub. An opaque reference must not encode a
client identity or expose an identifying URL.

## Issue body

Every issue records the following information in its form fields.

- **Problem.** The engineering failure or unmet need and its impact.
- **Desired outcome.** The observable result.
- **Scope and exclusions.** The work included and deliberately excluded.
- **Acceptance checklist.** Stable IDs, such as `- [ ] AC-1: ...`, with an
  observable verification method. Preserve IDs as criteria evolve; record why
  scope changes rather than silently rewriting completed checks.
- **Dependencies.** Public engineering issue references and versioned contracts,
  or `None`. A blocker names the dependency and next action.
- **Opaque evidence references.** Record IDs only, or `None`; never evidence
  content or identifying links.
- **Legacy WR ID.** The original `WR-...` reference when applicable, otherwise
  `None`. This preserves provenance without copying private WR text.

Bugs also record synthetic reproduction steps, expected and actual behavior,
and the affected version or commit. Slices name their parent issue. After
filing, attach each slice as a native GitHub sub-issue; a body link alone does
not create the parent/sub-issue relationship. Each slice has its own bounded
scope and acceptance checklist. Cross-repository dependencies use qualified
references so issue numbers cannot be confused.

## Tracking fields

The shared Project uses these single-select fields. This is the contract for
later Project setup, not a claim that those fields already exist.

| Field | Values | Purpose |
| --- | --- | --- |
| Status | Backlog, Ready, In progress, In review, Done | Execution progress |
| Delivery | Not started, Awaiting verification, Verified, Not applicable | Delivery verification independent of source merge |

Readiness and exceptional outcomes use the labels in
[triage-labels.md](triage-labels.md). Set Status to Done when the slice's source
acceptance checks pass and its final PR merges. Set Delivery to Awaiting
verification until the relevant production release is read back and the
acceptance behavior is verified. Record the release identity and opaque
verification references before setting Delivery to Verified. For work with no
production effect, record the reason and use Not applicable. Done or a closed
issue alone never means production is verified. Terminal labels explain why
unimplemented work closed; they do not claim successful delivery.

## PR linkage and closure

Use ordinary references on planning, preparatory and intermediate PRs.
`Closes #n` belongs only on the final implementation PR for that slice. For an
issue in another repository, use the fully qualified GitHub closing reference.
Do not close a parent outcome when only one sub-issue is delivered. GitHub's
closure on merge records source completion; the separate Delivery field keeps
production verification visible after closure. Keep the issue in the Project
until delivery is resolved.

## Triage and transition

`ready-for-human` items and needs-Joe answers come from Joe or Dell. The broader
direct-human-only triage authority question remains pending Joe's decision;
this contract does not resolve it. See [triage-labels.md](triage-labels.md) for
readiness transitions and the legacy state mapping.

Work Request verbs are **being retired**, not removed or disabled by this
slice. This includes `report-problem`, `work-request-card`,
`current-work-requests`, `review-and-triage`, `answer-work-request-for-joe`,
`propose-ready-plan`, `decline-work-request`, `supersede-work-request`, and
`complete-work-request`, along with the associated planning and dispatch verbs.
Until cutover, existing WR work continues through its current record-layer
workflow. Do not dual-write or infer that documentation alone migrated it.
After cutover, a skill's "publish to the issue tracker" means the owning repo's
GitHub Issue form, and "fetch the relevant ticket" means that GitHub Issue.

## Label definitions and application

[.github/labels.json](../../.github/labels.json) is the label vocabulary source.
[tools/sync-issue-labels.py](../../tools/sync-issue-labels.py) applies it using
only `gh label create --force`, serially, with a two-second wait before every
call, including the first. It stops at the first failed call. Rerunning
converges defined labels without deleting unrelated labels. No label sync runs
as part of this slice or CI.

The sync is ready for a separately authorized setup job to run with
`python3 tools/sync-issue-labels.py --repo DoctorCRE/carr-system`.
The script requires an authenticated `gh` with label-write access. It creates
or updates labels only; it does not create issues, configure a Project, or
change repository settings.
