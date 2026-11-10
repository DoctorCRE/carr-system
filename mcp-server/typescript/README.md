# Staged strict TypeScript gate

Run `npm --prefix mcp-server run check`. `check:staged` first runs
`python3 ops/strict-projects.py` from the repository root (npm supplies the
relative path). The generated strict configs, Knip graph and Fallow baseline
are ignored build products, never conversion PR edits.

Each conversion adds its own JSON file under `typescript/projects/`, named
for its card or PR. Fields `node`, `worker` and `entry` contain paths relative
to mcp-server. List each strict file in node or worker, and real executable
and test roots in entry. Libraries remain reachable through consumers.
The generator unions admissions deterministically, ignores retired paths,
and refuses uncovered entries or malformed admissions. Compiler flags remain
strict, Worker globals remain separate, and Knip checks all admitted files,
exports, types and duplicates. Biome and syntax refusals cover the same graph.

`migration.json` is a frozen historical ceiling. Do not prune it during a
conversion. Its lists may never grow against the tested candidate's base. JavaScript is
allowed only if both the ceiling and that base's actual tree contain its path.
A path deleted on that base cannot return. Legacy TypeScript must match the
base's bytes unless admitted to a strict project. New JavaScript fails
throughout the repository.

The baseline is resolved once to an immutable commit and printed with the
candidate SHA. Pull-request runs read `pull_request.base.sha` from
`GITHUB_EVENT_PATH` and require it to equal the checked-out synthetic merge's
first parent. Merge-group runs read `merge_group.base_sha`, require the event
head to match the checkout, and require the base on its first-parent history.
Both modes require `GITHUB_SHA` to match HEAD. Other runs, including local
checks, use HEAD's merge-base with `refs/remotes/origin/main`. Missing event
data, absent commit objects, unrelated bases and unreadable migration lists
refuse the check. The checker never fetches or substitutes a later branch tip.
Actions checkout already supplies full history with `fetch-depth: 0`.

A PR result covers its event base. The full `classes` job in
`.github/workflows/ci.yml` runs types on every merge group against the declared
queue base, covering newer main and preceding queue entries. The real-Git
regression in `gate.test.ts` proves an unchanged candidate survives an advancing
main while a queue candidate resurrecting a path deleted on its newer base is
refused. Frozen ceilings and generated project admissions remain unchanged.

Suite discovery remains authoritative. Duration measurements are advisory.
Extension-only TypeScript renames reuse the old JavaScript timing; genuinely
new suites receive the conservative serial 60-second default and are planned
separately from measured work. Budget and exact-once receipt checks still
refuse unsafe plans. Only a dedicated timing capture updates the table.

Fallow's historical baseline lives in `fallow-baselines/dead-code.legacy.json`.
The generator maps existing finding identities to a TypeScript replacement
only when the old file is absent and its replacement exists. It never grants
new export exemptions. Weekly Fallow generates its input before auditing.

## Updating an in-flight conversion

After merging main, preserve the pre-merge head in `conversion_head` and run
`python3 ops/strict-projects.py --admit-from "$conversion_head" --name pr3103`
(use that PR's unique number). This imports the old compiler and Knip admissions
into one independent fragment, then regenerates the ignored tool inputs.
Keep main's migration inventory, duration table and Fallow historical baseline.
Remove the old tracked generated configs from the index. Resolve actual source
conflicts individually, then rerun the normal checks before committing.

## Node-compatible libraries in Workers

Admit a library that imports Node builtins under `node_compat`. The generator
builds a composite declaration-only project with official `@types/node` and
ES globals. Both Worker compilers reference its emitted public interface,
with source redirects disabled. Export platform-neutral types such as `unknown`,
strings and structured values. A public Node type would import Node declarations
back into Worker consumers and must be removed from the public interface.

Worker admissions use Wrangler's runtime-only declarations, generated beside
PR 3100's full legacy Env declarations. They have no DOM or Node ambient types.
The full legacy compiler retains its generated Env and verb contracts. Neither
output is edited or patched. Run `npm run typecheck` before `check:staged` to
regenerate declarations. The empty declaration root keeps the composite project
valid before its first library is admitted.

For example, the artifact-trust conversion can admit `src/artifact-trust.ts`
under `node_compat`. Its public digest takes `unknown` and returns a string,
so Worker consumers do not load Node's Buffer or crypto globals. Deployment
still uses the existing `nodejs_compat` flag. This compiler split does not
promise that every Node builtin is implemented by Workers.

Miniflare's own declarations are a separate frontier. The pinned
`5.20261006.1-alpha` package references an unshipped `./shared` declaration and
missing dependency declarations. Platform isolation does not fix those imports.
Strict Miniflare-based test conversions still need a complete upstream package;
see [upstream issue 15377](https://github.com/cloudflare/workers-sdk/issues/15377).
