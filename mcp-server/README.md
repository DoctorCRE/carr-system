
## Adding verbs and test suites

Add a new family descriptor in `src/tool-families/`. It exports a default object
with `source` (the implementation's repository path) and `createTools(helpers)`
(the existing tool factory). Census inventory and practice origination show
factories that use the shared envelope. Keep new registrations out of `tools.js`.
Its remaining inline handlers and older factories can move when their own family
changes; they share the same duplicate-name check, source binding and contract freeze.

Run `./run.sh regen registry` from the repository root. This discovers the family
files, emits static imports for Worker bundling and rebuilds the single
`src/scac-mutation-registry.current.generated.js` consumed by runtime and gates.
Both generated files are ignored by Git. Commit the family and implementation.
The reviewed database seal and grant inputs live in
`src/scac-mutation-registry.inputs.js`; do not import that build input at runtime.

`npm ci` prepares the registry. Tests, type generation, the gates class and
Wrangler builds regenerate it too. The merge-group workflow installs dependencies
on the combined Git tree, so its registry includes every merged family. Deploy
installs and builds that same source rather than reusing a branch's output.
A source conflict must be resolved before regeneration; duplicate verbs fail the
build. Failed generation restores the previous outputs and reviewed inputs.

Add suites as `test/*.test.js` or `test/*.test.mjs`; the runner discovers them.
Names ending in `-postgres.test.mjs` or `-postgres.test.js` run alone. Suites that
import `disposable-postgres.mjs` or `postgres-database.mjs`, or launch `initdb`,
also run alone even with other names. Browser isolation remains in place.
