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

Write a verdict file for the coordinator. Begin with these three lines:

```text
VERDICT: APPROVE or VERDICT: CHANGES
Reviewed-SHA: <full inspected head SHA>
Reviewer: <name>
```

Include the PR's Card line and findings with file:line after the header.
Use CHANGES only for real blockers. Label nits non-blocking.
If the head moves, inspect the new head before writing its SHA.

Reviewers never post comments or reviews. The coordinator submits the verdict
as a native App review through `ops/review-submit.py`, with
`--repository jbookout/doctorcre-app` for the app repository.
Never copy keys or publish VERDICT comments.
