# P02 legacy TypeScript authority rethink

The failed premise was that a PR-owned digest could establish unchanged legacy
TypeScript. J418's attack changed action-closure.ts and refreshed its digest.
The original full staged command exited 0 with that edit.

Two executable candidates used the same fixture matrix and real-file attack.

| Observation | A, base bytes and path sets | B, base-owned hashes |
| --- | --- | --- |
| Unchanged listed source | Pass | Pass |
| J418 fixture edit plus rehash | Refused | Refused |
| J418 action-closure.ts edit plus rehash, full staged command | Exit 1 | Exit 1 |
| Append listed path | Refused | Refused |
| Rename listed path | Refused | Refused |
| Shrink lists | Pass | Pass |
| First introduction without base list | Pass | Pass |
| Main-only file added after initial snapshot, listed | Pass | Pass |
| Depth-one fixture clone | Pass | Pass |
| Bootstrap source edit plus rehash | Refused | Refused |

B ignores PR hash authority, refuses changed hash entries and compares source
digests against the base list. Without a base list it derives authority from base
bytes. Its formatted prototype was 167 implementation lines.

A removes stored hashes and compares Buffers from disk and git show at the same
path. The final implementation is 148 lines. The TypeScript snapshot shrinks from
an object of digests to an array of paths. Both lists are shrink-only once the
gate exists. At introduction, membership is limited to paths already on base.
This also admits pre-existing main files such as PR 3075's additions without
admitting newly created PR files. JavaScript bytes are never frozen.

A wins because it stores less state, removes digest maintenance, has no
bootstrap hash fallback and uses Git's source as the single content authority.
Every per-file refusal states the conversion remedy; unlisted pre-existing main
files also receive the listing remedy.

The existing staged command remains the public interface. The baseline reader
owns Git authority and returns validated path sets. No new module interface or
runtime dependency is introduced. The codebase-design pass compared these two
internal representations. The zero-tech-debt pass deleted the hash schema and
digest implementation rather than retaining compatibility with an unmerged
format. Tests still use a hash to reproduce the rejected historical attack.

The scan is linear in repository paths plus one bounded git show per ungated
TypeScript file. At ten times the files, subprocess work and the existing CI
deadline are the first capacity limits. There is no cache, queue, database or
persistent runtime state. Git lookup failure refuses the file; base lookup
failure refuses the command. A missing base permits one depth-one fetch with a
30-second timeout. Other tools retain their existing 120-second timeouts.
No repeated external calls, external writes or write idempotency apply.

This scope belongs to carr-system. doctorcre-app and software-factory have
independent gates and are unchanged under this task's explicit file scope.
