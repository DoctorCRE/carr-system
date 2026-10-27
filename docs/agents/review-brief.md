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
- If an earlier `VERDICT: CHANGES` exists, confirm each blocker is fixed, then
  review the full diff for new correctness bugs.

Post one comment per PR with
`gh api repos/O/R/issues/N/comments -f body=...`. Its first three lines are
exactly:

```
VERDICT: APPROVE        (or VERDICT: CHANGES)
Reviewed-SHA: <the 40-character head you reviewed>
Reviewer: <model> cloud routine
```

Findings follow with file:line. Use CHANGES only for real blockers; nits go
under a "Non-blocking" heading. If the head moved past the SHA you were given,
review the new head and use its SHA.
