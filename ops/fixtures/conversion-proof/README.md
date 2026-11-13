# Recorded conversion proof evidence

`github.json` selects the non-Actions and merge-group suites from the GraphQL response for merged PR 3114
without changing their fields, using the query emitted by `conversion_query`. It includes merge-group run
42736888524 and its actual required job names, statuses and conclusions.

`release.json` selects the unmodified `ok`, `env` and `git_sha` fields from
`/tmp/J696/release.json`. `health.json` selects `ok` from that capture's health
response. J696 observed these public endpoints at 2026-11-09 20:47:12 CST.
The release SHA and GraphQL merge SHA are both
5db975d80815e396b1186379c324603353bf9c41.

Tests replay these responses through the production parser. Negative cases
mutate one recorded field. An additional disposable repository supplies real
equal, descendant, older, unrelated and unknown commit ancestry without
requiring the checkout's historical Git objects.

`workflow.yml` selects the unmodified coverage job and class matrix through its
job-name metadata from `.github/workflows/ci.yml` at the recorded merge SHA.
The proof must evaluate recorded jobs against their own workflow contract.
