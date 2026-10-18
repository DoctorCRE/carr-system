---
name: create-skill
description: Draft, validate, test, and revise a SKILL.md for a bounded workflow. Use when pstack calls for skill authoring, including a project verification skill or a user's working conventions.
---

# Create a skill

Read [the port contract](../../PORT.md) and [the model routes](../../pstack-models.md) first.
This local dependency replaces pstack's reference to Cursor's built-in `create-skill`.

## Define the workflow

Inspect existing skills before creating another home for the same behavior.
Use the requested location and scope. If placement is unspecified, follow the target repository's skill convention.
Identify the requests the skill handles, its inputs, permitted effects, and observable completion condition.
Ask only for a missing preference or fact that the repository cannot answer.

## Draft

Write a skill folder with `SKILL.md`. Include valid YAML frontmatter with `name` and `description`.
Use a lowercase name with hyphens. Write the description as one YAML scalar.
The description names the workflow and the requests that should trigger it.
Keep automatic discovery unless the user requests explicit invocation or the existing skill already specifies it.

Write the instructions for an agent that reaches them mid-task.
Keep the reusable workflow in the body. Put branch-specific detail in linked references only when the detail warrants another file.
Resolve paths relative to the skill file. Link each required reference where the agent needs it.
Use existing repository scripts and contracts instead of copying their contents.
Preserve the user's product, task boundaries, and authorization. Skill creation grants no additional effects.

## Validate, test, and iterate

1. Parse the frontmatter and check that `name` and `description` are nonempty strings.
2. Verify referenced files and cross-skill links against the installed layout.
3. For a structural workflow, run a representative request in an isolated test workspace.
4. Compare its actions and artifacts against the workflow's intended result and permitted effects.
5. Revise the instruction that caused a demonstrated failure, then rerun the affected case.

A subjective working-style skill can use the user's draft feedback instead of an artificial benchmark.
Apply [unslop](../unslop/SKILL.md) to the draft.
Report what you tested and what remains unverified. A workflow that has not run remains a draft.

If selection misses intended requests or captures unrelated requests, test its description with both examples and near misses.
Adjust only the trigger wording, rerun those cases, and preserve the skill's content scope.
