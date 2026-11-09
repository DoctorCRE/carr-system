# Staged strict TypeScript gate

Run `npm --prefix mcp-server run check` for the existing Worker contract checks,
strict gate, typed gate tests and bounded legacy test runner. CI's existing
`types` class runs `check:staged`, the Worker contract checks and Python types.
CI's `unit` class owns the legacy runner, so it does not run twice in CI.

`tsconfig.base.json` owns the standard flags. Node-run files join the explicit
list in `tsconfig.node.json`. Worker files join `tsconfig.worker.json`, which
retains strict flags and removes Node globals. Its list starts empty because
P02 converts no product modules. The existing `tsconfig.json` still checks all
current Worker contracts and generated Cloudflare declarations. Worker
conversions must include the generated declarations in their strict project.

`migration.json` lists legacy JavaScript and TypeScript paths across the entire repository. Both lists may only shrink against the authoritative base branch. On first
introduction, only paths already on the base can be listed. Listed TypeScript
bytes must equal the same path on that base, read directly with Git. No stored
hash grants an exemption. Legacy JavaScript stays editable.
A changed or new TypeScript file must
join a strict project. A new JavaScript file fails, including outside packages.
There is no tool-exception allowlist. New first-party files must join a strict
TypeScript project. Config, migration-schema and baseline refusals end with a
`Remedy:` line and exit 1 without a stack trace. Remove migration
entries as their files are converted. Delete the temporary migration gate when
these lists empty.

Add every strict file to Knip's explicit project list. List real executable or
test roots as entries; libraries must remain reachable through their consumers.
Knip checks files, exports, types and duplicates in this admitted graph.
Dependency analysis remains with the existing package checks while legacy
modules own those dependencies. Biome and prohibited-syntax checks run over
every admitted file, with warnings treated as errors.

The typed tests copy the real gate and configuration into a disposable Git
repository and run the installed compiler, Biome and Knip against clean code
and negative seeds. They need only the checked-out files and installed packages.
