# Production proof declarations

The PR body must contain exactly one `## Production proof` section. Its entire
content is `proofs/requirements/<change>.json`, optionally surrounded by backticks.
The JSON must satisfy `contracts/production-proof-plan.v1.json`.

Each assertion must contain exactly one of `execution` or `deferred`.
An executable assertion declares a registered command, the live receipt source,
structured checks, and passing and failing synthetic JSON receipts under
`proofs/fixtures/`. Admission runs those checks against both receipts. It refuses
a rejected passing fixture or an accepted failing fixture. Fixture execution
proves that the declared predicate runs, not that a producer exists or that the
product works. Review checks the predicate's relevance and receipt provenance.

`receipt-predicate.v1` resolves through `x-runners` to the source-bound entry point
`ops/production-proof-plan.py`. Its fixed command is
`["python3", "ops/production-proof-plan.py", "--observe-receipt"]`.
Admission calls this runner's receipt evaluator directly. It never launches a PR
shell command, reads credentials, or contacts production. Older registered runners
without this fixture interface require explicit deferral until an evaluator of
their complete predicate is registered. Registration alone does not pass admission.

A check names a dot-separated JSON `path`, an `operator`, and a `value`.
`equals` requires the same type and value. `at_least` requires a nonnegative
integer. Release fields `source_sha`, `source_commit`, `release_sha`, and `sha`
require `contains_commit` rather than equality. That operator verifies real Git
commit ancestry using the supplied repository. Descendant releases pass.
Replacement objects, grafts, and inherited Git routing cannot supply ancestry.
Exact installed runtime identity can still use `runtime_sha` equality.
Held-gate receipts can explicitly check `exit_code` equal to 78.

Each fixture file is bounded to 1 MiB. The declaration's artifact-byte and runtime
budgets also bound the complete fixture run. Each assertion has its own deadline.
There are at most 100 assertions and 20 checks per assertion. Git calls have a
15-second cap within the remaining deadline. No retries or external calls occur.

Live observation uses the same evaluator, with one explicitly supplied receipt.
For example, run the admission check's own observation with its emitted JSON log
line saved to `/tmp/product-admission-receipt.json`.

```
python3 ops/production-proof-plan.py --observe-receipt \
  --requirement proofs/requirements/wr622-s1-j299-proof-fixtures.json \
  --assertion next-product-pr-fixture-admission \
  --receipt-file /tmp/product-admission-receipt.json
```

For ancestry checks, `--repo` selects the repository containing the release and
merge commits. Live observation refuses a deferred assertion. A matching receipt
predicate does not establish trusted receipt provenance or production acceptance.

A deferral must name `blocking_unit` and a concrete `reason`. Use it when a
producer or evaluator is not built, such as the waiting Studio delivery job.
Admission labels these assertions `DEFERRED` and emits state `deferred` in its
`carr-proof-admission-receipt.v1` JSON line. It never labels them fixture-verified.
Existing prose declarations are migrated to this state without asserting success.
The JSON line includes the requirement ID, fixture-run count and deferred count.
An entirely executable declaration emits state `fixtures-verified`.

For paths on the contract's explicit non-product allowlist, the section may instead
contain `n/a: <specific reason>`. Everything outside that allowlist is product.
Product changes under `mcp-server/**` or `migrations/**` require `worker` and a
Worker assertion. Installed Outlook paths require `studio` and a Studio assertion.
Mixed changes require both targets. Rename sources and destinations are classified.

CI runs admission through the gates class in `ops/ci.sh`. It reads the body from
`GITHUB_EVENT_PATH`. Once the contract has landed on the PR base's first-parent
history, its introducing commit time is the rollout boundary. One bounded GitHub
GraphQL read checks the current body and head and reads creation and edit times.
Older PRs whose body has not been edited after rollout remain grandfathered.
Newly opened or body-edited PRs must declare proof. Missing metadata fails closed.

Local admission uses a saved PR body without a network call.

```
python3 ops/production-proof-plan.py --body-file out/pr-body.md --base origin/main
```

Without a PR event or explicit body file, CI prints that PR admission was not
evaluated. The discovered selftests exercise the real admission and live receipt
interfaces with synthetic inputs. Fixture success never authorizes completion.
