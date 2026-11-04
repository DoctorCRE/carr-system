# Production proof declarations

The PR body must contain exactly one `## Production proof` section. Its entire
content is `proofs/requirements/<change>.json`, optionally surrounded by backticks.
The JSON must satisfy `contracts/production-proof-plan.v1.json`.
Runner IDs resolve to exact source revisions in that contract. No PR command is
executed. The currently registered worker reader cannot prove app or Studio work;
those adapters must be registered through reviewed contract changes.

For paths on the contract's explicit non-product allowlist, the section may instead
contain `n/a: <specific reason>`. Everything outside that allowlist is product,
including runtime Markdown and installed scripts. Mixed diffs require a declaration.
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
