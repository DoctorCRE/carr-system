# Recorded conversion proof evidence

`github.json` is the unmodified GraphQL response read for merged PR 3114
using the query emitted by `conversion_query`. It includes merge-group run
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
