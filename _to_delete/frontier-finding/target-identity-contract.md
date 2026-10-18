# Target identity contract v1 — WR-000046 Artifact A cross-check

Both the OBSERVED manifest (gen-frontier-manifest.py, apply-diff on a disposable) and the INDEPENDENT parse-based extractor emit their target sets as a JSON array of identity strings using EXACTLY these forms, sorted with LC_ALL=C ordering, duplicates removed. The acceptance comparison is the symmetric difference of the two arrays; the terminal state must be EMPTY.

Identity string forms (lowercase kind prefix, schema-qualified names, no quoting unless the identifier itself demands it):

- `schema:<name>` — a created schema.
- `table:<schema>.<name>` — a created or altered table (partitioned or plain).
- `view:<schema>.<name>` / `matview:<schema>.<name>`
- `sequence:<schema>.<name>`
- `function:<schema>.<name>(<comma-separated argument type names, as pg_get_function_identity_arguments renders them, spaces after commas stripped>)`
- `type:<schema>.<name>` / `enum:<schema>.<name>` (an enum value addition targets its enum)
- `index:<schema>.<name>`
- `constraint:<schema>.<table>.<constraint_name>`
- `trigger:<schema>.<table>.<trigger_name>`
- `policy:<schema>.<table>.<policy_name>`
- `grant:<object identity as above>:<grantee role>:<PRIVILEGE>` — one entry per (object, grantee, privilege) granted; `revoke:` same shape for revokes. Default ACL changes: `defaultacl:<schema>.<for role>:<objtype>:<grantee>:<PRIVILEGE>`.
- `row:<schema>.<table>:<primary key values joined with '|' in key-column order>` — control-table row inserts/updates/deletes.
- `comment:<object identity as above>` — COMMENT ON statements.
- `owner:<object identity as above>` — ownership changes.

Rules, all required:
1. The extractor derives targets ONLY by reading migrations/0454…0471 at pinned commit 0985dcc70764d888d70004641e210f3730ef9d2a (git show). It must not read any observation code, snapshot SQL, or another seat's worktree.
2. The observed manifest derives targets ONLY from the disposable apply-diff, then renders them in these forms.
3. A `create or replace` of an existing object is still that object's identity (no separate marker).
4. Statements inside DO blocks and dynamic SQL: the extractor emits what it can prove from the text and marks each entry it could NOT statically resolve in a separate `unresolved` array with the file and line; unresolved entries are excluded from the symmetric difference but MUST be listed in the comparison report.
5. Both seats also emit a `by_file` map (migration filename → its identity strings) to make disagreements attributable.
