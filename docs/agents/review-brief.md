# Review brief for cloud reviewer routines

A cloud review routine's prompt names only the repository, the PR number, the
head SHA and anything specific to check; it points here for the rest.

You run headless in a cloud sandbox; nobody answers questions.

- GitHub GraphQL is blocked here. Use `gh api` REST (`repos/O/R/pulls/N`,
  `pulls/N/files`, `issues/N/comments`, `commits/<sha>/check-runs`) and plain
  git (`git fetch origin pull/N/head:prN`).
- Read CI check-runs for the head SHA instead of running whole suites; run
  only the PR's own test files when that helps.
- Never run env, printenv or `ps eww`, and never open .env, credential, token,
  key, keychain or dump files.
- Do not push, merge or edit code.
- If an earlier App review requests changes, confirm each blocker is fixed.

For DoctorCRE/carr-system, submit a native App review through
`ops/review-submit.py N approve|request-changes --body-file FILE --reviewed-sha SHA`.
The body includes the PR's Card line, reviewer identity, and findings with file:line.
Use request-changes only for real blockers; label nits non-blocking.
Never publish VERDICT comments. The orchestrator provides the authorized local
App submission path when the cloud routine cannot access it. Do not copy keys.
If the head moved, inspect the new head before submitting its SHA.
