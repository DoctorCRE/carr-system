# Production proof declarations

The PR body must contain exactly one `## Production proof` section. Its entire
content is `proofs/requirements/<change>.json`, optionally surrounded by backticks.
The JSON must satisfy `contracts/production-proof-plan.v1.json`.
Runner IDs resolve to exact source revisions in that contract. No PR command is
executed. The worker reader covers only worker assertions. The pinned Studio
reader covers run-receipt observations. App adapters require a reviewed contract
registration. Runner IDs come only from the contract registry.

For paths on the contract's explicit non-product allowlist, the section may instead
contain `n/a: <specific reason>`. Everything outside that allowlist is product,
including runtime Markdown and installed scripts. Mixed diffs require a declaration.
Product changes under `mcp-server/**` or `migrations/**` require `worker` and a
Worker assertion. Installed Outlook paths require `studio` and a Studio assertion.
Mixed Worker and Studio changes require both targets and their matching runners.
Rename sources and destinations are both classified. Review remains responsible
for the declared predicate's relevance; admission cannot establish that relevance
or prove a feature works.

CI reads the body from `GITHUB_EVENT_PATH`. Once the contract has landed on the PR
base's first-parent history, its introducing commit time is the rollout boundary.
One bounded GitHub GraphQL read checks the current body/head and reads
`createdAt` and `lastEditedAt`. Older PRs whose body has not been edited after
rollout remain grandfathered even when their source synchronizes. Newly opened
or body-edited PRs must declare proof. An `edited` event reruns CI on the same head.
Missing metadata fails closed; there is no retry loop. Before rollout the gate
reports admission as not required, including for this bootstrap PR.

Local admission uses a saved PR body without a network call:

```
python3 ops/production-proof-plan.py --body-file out/pr-body.md --base origin/main
```

Without a PR event or explicit body file, CI prints that PR admission was not
evaluated. The automatically discovered selftest always exercises invalid and
corrected synthetic declarations through the real CLI, including on this PR.
This slice provides admission only. Execution, trusted receipts, and completion
enforcement belong to later slices.

## Studio run observations

Declare `studio` and `studio-run-receipt.v1` for installed Outlook runtime changes.
The contract lists paths that require this target even if a declaration names
only worker. CI check implementations have explicit non-product entries;
`ops/ci-dispatch.py` and other installed operations remain product paths.

The Studio adapter reads one explicit service receipt file. It never starts a
job, refreshes the runtime, discovers personal files, sends, or writes records.
Its command interface is `python3 ops/studio-proof-observe.py --receipt-file FILE
--service SERVICE --runtime-sha FULL_SHA --count COUNT_PATH --minimum INTEGER`.
`COUNT_PATH` selects a dot-separated key under `counts`, such as `run.processed`.
Use the pinned source revision from the registry when executing a declaration.
The assertion predicate names the receipt source, count and minimum. Review
checks those choices against the changed behavior. A minimum of zero proves
only the declared idle predicate, never a nonempty processing path.

Admission can accept a runnable Studio declaration before its receipt exists.
Execution then reports BLOCKED for a missing receipt, a failed run, insufficient
counts, or a mismatched full runtime SHA. Current receipts with abbreviated
SHAs remain blocked until full identity is available. This adapter does not
supply P7 collection, trusted provenance, receipt ingestion or completion truth.
A successful local comparison alone cannot establish those facts.
