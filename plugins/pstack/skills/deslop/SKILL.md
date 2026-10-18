---
name: deslop
description: Clean the current code diff before commit. Remove redundant comments, defensive code, awkward abstractions, and style drift while preserving the requested behavior.
---

# Deslop

Read [the port contract](../../PORT.md) and [the model routes](../../pstack-models.md) first.
This local dependency replaces pstack's reference to `cursor-team-kit`'s `deslop`.

## Clean the scoped diff

1. Establish the assignment's paths and comparison base. Read the diff and surrounding code before changing anything.
2. Identify additions that duplicate existing behavior, explain obvious code, or introduce an abstraction without a caller that needs it.
3. Remove those additions. Match the repository's names, control flow, error handling, and formatting.
4. Check defensive branches against the caller's contract. Keep checks at untrusted boundaries and checks that handle a demonstrated failure.
5. Replace casts or type escapes with the existing types when the replacement preserves behavior.
6. Read the resulting diff against the requested behavior. Run the relevant repository checks after code changes.

Keep the cleanup within the assigned diff. Preserve unrelated edits and required audit or contract checks.
Use evidence from the code and caller contracts. Do not invent line limits, complexity scores, or coverage thresholds.
Report what changed and what you verified. If nothing needs cleanup, say that after reading the diff.
