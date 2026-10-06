# CS/xhigh cross-family review trail — 'The Frontier Finding' plan, WR-000046

Reviewer seat: OpenAI Codex, model gpt-5.6-sol, reasoning effort xhigh, sandbox read-only against the real repository, prompts piped from files. NINE reviewer runs, 2026-09-01 → 2026-09-02: the initial review of rev 3, then eight same-reviewer rechecks of revs 4 through 11, each revision fixing the previous round's findings. Joe capped the loop by explicit order ('cap it', 2026-09-02) after the ninth run; the final same-reviewer verdict was FAIL, and rev 12 of the plan folds in that round's five findings (a load-bearing miscount, a double-apply/expected-pre conflict resolved by a companion post-to-post manifest, two missing acceptance fixtures, an identity-tuple field the client wrapper cannot pass). Finding-by-finding dispositions are in each round's PER-FINDING RECHECK block below and in the plan's revision headers (revs 4 through 12). Each round's full prompt is digest-pinned below; the prompts and complete raw outputs remain in the session's task records.

## Round 1 — plan under review: rev 3 — prompt sha256 bc471c328dc7c556c81bfac295b329d16a374500654a53dca0e599ab9cd72d44

VERDICT: FAIL

FINDINGS:
1. [BLOCKER] The negative finding ignores a live rebase/re-pin family. `origin/main:ops/scac-mutation-inventory.mjs:24-27` explicitly says v1–v9 are source-only and may be regenerated through `--write-rebased-*`; lines 1988–2059 expose writers for the registry migrations through v10. A production-clone rebase, reseal, snapshot regeneration, and clean staging replacement was not tested by either prior review. Rev 3 must execute and reject that route or narrow its claim; “sealed history only moves by forward successor” contradicts the repository.
2. [BLOCKER] “No live probe can distinguish production from a disposable” contradicts review-one B3: raw `pg_catalog` is precisely what differs—production reports 600 while disposables match the pinned sequence—and B3 proposed a per-file observed-side probe. That probe did not rescue rev 1 because of other defects, but its existence invalidates this argument leg.
3. [BLOCKER] The fourteen blockers are not an exhaustive proof against every workaround family. A production-only executor using a separate digest-pinned receipt ledger, leaving `schema_migrations` untouched and applying only dependency-independent, replay-safe files, avoids rev-1’s ledger spoof/replay defects and rev-2’s numbering defects; it is effectively an automated form of F01’s sanctioned manual apply. It may still prove undesirable, but rev 3 never tests it. Holding merges is containment rather than an exit, but the plan also fails to compare it with indefinitely accumulating queued migrations.
4. [BLOCKER] The stated “only exit” is not executable as written. The runner selects an uninterrupted filename prefix and stops at 0454; a successor numbered after 0471 cannot reconcile or re-pin anything before 0454 succeeds. Activation therefore requires an unstated bootstrap—production catalog reconciliation, regeneration of the source-only frontier, or out-of-band application. F02 cannot hand authors “forward-successor migrations” as the solution without resolving this ordering circle.
5. [BLOCKER] F01’s three proofs do not make break-glass safe. Exact idempotent SQL could create a security-definer function and grant EXECUTE, pass readback and replay-no-op checks, yet change the census; it could similarly create a frontier-owned object and make 0454 fail later on duplicate creation. The protocol lacks dependency/order proof, frontier-identifier collision checks, catalog-census impact proof, transactional effect verification, and a required forward-fix/rollback receipt.
6. [BLOCKER] F01’s end condition is undefined. “Activation” could mean Joe’s approval, production application through 0471, or enforcement cutover; the frontier migrations themselves require `production_enforcement_active=false`, so those events are demonstrably distinct. “Dissolves automatically” needs an exact observable condition and readback.
7. [MAJOR] F02 omits a load-bearing activation artifact: a commit- and digest-bound v1–v10 reconciliation matrix containing every expected and production count/digest, row-level catalog deltas, generator commands, migration hashes, environment rebuild/reconciliation steps, and the named activation program/owner. “600 versus the pins” plus transcripts is insufficient to choose between catalog normalization and source-tail rebasing.
8. [MAJOR] The acceptance criteria mostly prove that prose and events exist. “Resolves,” “states the law,” “packet-grade,” and “full dead-end catalog” are undefined; no criterion tests an unsafe break-glass rejection, the dissolution predicate, evidence hashes, reproduction commands, or disposition of every workaround family. The plan’s central negative finding has no falsifiable acceptance test.
9. [MAJOR] Rollback and failure handling are largely theater. Reverting doctrine cannot undo a repo pointer, append-only coordination events, or production break-glass effects; a growing countdown only observes activation stall; and manual approvals do not prevent normalization. There is no recovery procedure for partial activation, an incorrect readback, a later-discovered non-no-op replay, or a break-glass change that further alters the frontier census.

## Round 2 — plan under review: rev 4 — prompt sha256 21abde04aa72f0cb2046e23c6e75e089a74210b3218cd066a7fd087a021ea7fe

PER-FINDING RECHECK:

1. FIXED — Rev 4 expressly recognizes the live rebase/re-pin family, correctly limits immutability to applied history, and identifies the `--write-rebased-*` writers. It narrows the negative claim to workarounds within WR-000046 and hands rebase/re-pin to the integrity activation program as its leading candidate, satisfying the prior finding’s “execute/reject or narrow” requirement.

2. FIXED — Section 1 now acknowledges that an observed-side `pg_catalog` probe distinguishes production’s 600 entries from disposable pins. It limits the criticism to the prior probe’s within-run instability and missing expected-value authority instead of claiming that no distinguishing probe exists.

3. NOT FIXED — Section 1’s separate-executor disposition remains unsupported. B2 says “almost no” migration is independent, not none; census neutrality still leaves a nonempty class. More importantly, automation need not remove Joe’s per-use approval—a digest-bound executor can require an approved manifest and receipt before execution. Calling that “F01 with the safety removed” attacks only an unnecessarily unsafe implementation and does not test the family.

4. FIXED — Rev 4 now states the ordering circle explicitly: an ordinary successor after 0471 cannot run while 0454 remains first pending. It names source-tail regeneration/re-pin or receipted out-of-band bootstrap as the required bootstrap mechanisms instead of presenting forward successors alone as executable.

5. NOT FIXED — F01 adds census neutrality, collision language, and a forward-fix commitment, but still lacks an executable dependency/order proof, transactional pre/post-effect verification, and a bound collision manifest. The referenced “SIEP object list” is unnamed and absent from `origin/main`; moreover, the frontier alters and replaces existing objects—not merely objects it creates—such as `ops.propose_sourced_work_request_plan`, constraints, ACLs, and control rows. The acceptance test only demonstrates rejection when condition (d) is omitted, not rejection of unsafe SQL falsely presented as satisfying it.

6. FIXED — Rev 4 now distinguishes approval, partial application, and actual cutover. It supplies an observable conjunction: receipted production ledger readback of the activation set plus ordinary pending selection showing a post-frontier next migration or none. The predicate’s new safety defect is recorded below.

7. NOT FIXED — F02 now names most required matrix fields, but weakens row-level deltas to “where obtainable” and requests per-environment “consequences,” not executable rebuild/reconciliation steps. Its acceptance criterion verifies only migration-file digests; it does not verify complete production counts/digests, row deltas, production-clone reproduction, clean staging replacement, or a runnable reconciliation sequence.

8. NOT FIXED — The new criteria remain artifact-presence tests around the central claims. A missing-(d) rejection tests an omitted checklist item rather than a request that claims all five conditions but is actually unsafe. Nothing exercises both sides of the dissolution predicate, validates the production observations and reproduction commands, or proves that the family disposition is exhaustive. The negative finding therefore remains unfalsifiable.

9. NOT FIXED — Rev 4 is more honest about append-only effects, but it still lacks recovery for partial activation or a materially wrong live state. “Keep the protocol in force” is containment, not partial-activation recovery; rereading an incorrect readback does not repair its underlying effect. Its new forward-fix rule is also internally impossible for census drift, as detailed below.

NEW FINDINGS:

1. [BLOCKER] The dissolution predicate can be satisfied by the exact ledger-stamp failure already rejected. Both conjuncts depend on the filename-keyed `schema_migrations` ledger: `tools/migrate.py:329-333` defines pending solely as filenames absent from that ledger. Inserting uninterrupted 0454–0471 rows with their real file digests—without executing their SQL—makes the ledger “complete” and the next pending migration post-frontier while no registry objects exist. The predicate requires no catalog, object, or seal readback, so it can dissolve F01 on a spoofed activation.

2. [BLOCKER] F01’s collision control depends on a nonexistent and insufficient artifact. No digest-bound “SIEP object list” exists in `origin/main`, and neither the F01 write set nor acceptance criteria creates or validates one. Even a generated list of newly created identifiers would miss the frontier’s replacements, alterations, grants, revokes, constraints, triggers, and data assumptions.

3. [BLOCKER] The recovery rule forbids its own repair. Condition (d) requires every break-glass operation to be census-neutral, while conditions (e) and Section 3 require a census-perturbing operation to be corrected by a forward fix “under the same five-condition protocol.” Restoring a changed census necessarily adds, removes, or alters a censused entry and therefore fails condition (d). No incident can authorize the promised correction.

4. [MAJOR] F02 describes the existing writers as a production-repin mechanism without supplying their missing measurement stage. `ops/scac-mutation-inventory.mjs:64-177` embeds fixed catalog constants—its comments explicitly say they come from disposable readback, never a running production catalog—and the writer block at lines 1988–2059 accepts only a mode and output target. The handoff lacks commands for deriving sequential production-clone baselines, updating those constants, resealing them, and replacing staging after the migration hashes change.

VERDICT: FAIL

## Round 3 — plan under review: rev 5 — prompt sha256 93508980059d26f1d89d2485333a9b01a8753c54b418dd2cb43dbc867a6b742c

PER-FINDING RECHECK:
O3. FIXED — Section 1 now addresses the strongest executor: approval, digest-bound manifest, and receipts are preserved, while the executor is correctly classified as F01 mechanization that cannot unpark the frontier. Activation-grade out-of-band execution is separately assigned to the integrity program.

O5. NOT FIXED — F01(d) still provides no exact census command and no single transaction that performs pre-readback, SQL, post-readback, and rollback on mismatch. Its unspecified disposable rehearsal does not prove the required production-clone → frontier activation → ordinary replay order. The manifest also omits dependency references and DML/control-row collisions.

O7. FIXED — F02 makes every matrix field mandatory, requires row-level deltas and per-database command sequences, verifies production observations through recorded taps, and explicitly distinguishes runnable commands from the genuinely missing measurement stage.

O8. FIXED — Rev 5 adds unsafe SQL that falsely claims condition (d), exercises both dissolution outcomes, requires reproducible observations and commands, and replaces the open-universe completeness claim with an explicit boundedness rule.

O9. NOT FIXED — The census-restoration contradiction is repaired, but partial activation still receives containment plus a requirement for a future plan, not an executable recovery accepted by F02. Non-census wrong state has no recorded pre-effect baseline, and a repair touching a frontier-collision object remains forbidden by condition (d)(ii).

N1. FIXED — The exact ledger-stamp spoof now fails conjunct (iii), because stamping 0454–0471 without executing them leaves the registry surface and validity function absent.

N2. NOT FIXED — The artifact is now planned and includes structural effects, but its domain still excludes inserted/updated/deleted control rows and dependency references. Spot-checking created/altered/replaced/granted examples cannot establish complete coverage of revokes, constraints, triggers, dynamic SQL, or data assumptions.

N3. FIXED — The restorative exception expressly permits a census-changing correction when its post-state equals the recorded pre-incident baseline.

N4. FIXED — Rev 5 accurately identifies the embedded disposable-derived constants, names the missing production-clone measurement stage, enumerates its required steps, and distinguishes existing writer commands from work not yet built.

NEW FINDINGS:
1. [BLOCKER] Dissolution conjunct (iii) is still not an executable, definition-bound predicate. It names neither the exact functions/queries nor expected results and definition digests. The acceptance test rejects a pure ledger stamp but cannot reject a partial or counterfeit surface whose stub “current” function returns valid.

2. [MAJOR] Both F01 and F02 repeatedly enumerate four census categories, but frontier v9 and v10 also pin `runtime_dml_grants` at count 297 with its own digest. The promised “every pinned category” matrix and activation validity proof are therefore internally inconsistent with the actual frontier catalog projection.

VERDICT: FAIL

## Round 4 — plan under review: rev 6 — prompt sha256 7d9ce85421eeecc23188fd731a717e94b5e63bff0e63aa97c772e66eb9754d4e

PER-FINDING RECHECK:
O5. NOT FIXED — Rev 6 acknowledges the unrehearsable post-activation replay risk and adds dependencies/control-row collisions, but F01(d) still supplies neither the exact census command nor an executable transaction containing `BEGIN → pre-readback → candidate SQL → post-readback → asserted comparison → COMMIT/ROLLBACK`. Referring to SQL that will later be placed in a manifest does not make the promised guard or negative test executable.

O9. FIXED — Conditions (b) and (e) now record non-census baselines and expressly permit restorative census and protected-object changes. The pre-activation baseline duty plus F02’s refusal of any future activation plan lacking a rehearsed mid-flight abort/repair procedure is an appropriate boundary for this zero-code plan.

N2. NOT FIXED — The manifest’s stated domain now includes control-row DML and dependencies, but acceptance still only spot-checks examples of each class. An artifact containing one example per class while omitting other revokes, rows, triggers, constraints, or dependencies would pass. The namespace backstop does not cover frontier dependencies outside `ops.scac_*`, so completeness remains load-bearing.

R1. NOT FIXED — Definition digests improve the predicate, but rev 6 still defers the exact function set, queries, digests, and results to the dissolution receipt. It also fails to bind the transitive dependency closure: an exact `ops.scac_reference_monitor_state()` definition can call counterfeit dependency functions and return `current`. No acceptance case tests that counterfeit shape.

R2. RELOCATED — Current-state references now include `runtime_dml_grants`, but F02 requires non-optional values for all five categories for every version v1–v10. The actual generator constants pin three categories in v1, four in v2–v8, and five only in v9–v10. The promised matrix therefore remains inconsistent with the frontier unless absent categories are explicitly represented as not-applicable rather than invented.

NEW FINDINGS:
1. [BLOCKER] F01 deadlocks its own dissolution. It says “No other production write path exists until dissolution,” while dissolution requires the integrity program to write the frontier to production first. The frontier cannot satisfy F01(d), and no activation exception is defined, so the doctrine forbids the very activation that ends it.

2. [MAJOR] Neither generated deliverable has a concrete path, schema, pinned source commit, or actual generation/verification command. “Generation command recorded” is only a promise. Consequently, F01/F02 acceptance cannot locate, regenerate, or independently verify either artifact from the plan.

VERDICT: FAIL

## Round 5 — plan under review: rev 7 — prompt sha256 f974a6b9fdec725320c18edfac65b429d58c5f73c95becf2846139c6e22aa6da

PER-FINDING RECHECK:
O5. NOT FIXED — F01(d)’s purported “verbatim guarded transaction” remains pseudocode: PRE, CANDIDATE, POST, ASSERT, COMMIT, and ROLLBACK are comments or placeholders. Section 0’s `git show | sed` commands merely extract function-definition fragments; they are not executable census commands. The 0471 fragment invokes pre-activation-absent `ops.scac_canonical_json`, while the 0467 fragment returns an aggregate rather than the promised raw row set. No exact client command holds one connection through the asserted transaction.

N2. NOT FIXED — `unclassified = []` proves only that every statement received a classification, not that every target, key, or dependency inside a classified statement was captured. Statement counts cannot establish object completeness: single frontier statements revoke privileges from many functions or insert hundreds of control rows, so most targets could be omitted without changing the statement count. Dependencies still have no independent closure comparison, and dependencies outside `ops.scac_*` remain beyond the namespace backstop.

R1. NOT FIXED — Rev 7 adds a callee requirement and the previously missing counterfeit-callee test, but still defers the closure-enumeration query itself. More importantly, “entire transitive callee closure” is narrower than the actual dependency closure. `ops.scac_reference_monitor_state()` and its callees depend on tables, trigger registrations, ACL/role state, and extension objects. Step (1) checks only existence, not those definitions. Genuine function definitions operating over counterfeit compatible relations or triggers can therefore still satisfy step (3).

R2. FIXED — Sections 0 and F02 now bind the actual shape: three categories for v1, four for v2–v8, and five for v9–v10, with absent categories represented explicitly as `"not_applicable"`.

S1. FIXED — The write-path rule now contains an explicit, narrowly scoped integrity-program activation exception governed by its own Joe-accepted plan, and acceptance requires that exception to appear in doctrine.

S2. NOT FIXED — Artifact A now has a path, source pin, field layout, and generation command. Artifact B still has no command or script that produces the matrix; its `shasum` command only verifies bytes that already exist. The “generator commands that exist today” are data to record inside B, not B’s generator. Additionally, both artifacts and A’s generator are placed under gitignored `out/` with no durable content attachment or generator digest specified, so later independent regeneration is not guaranteed.

NEW FINDINGS:
1. [BLOCKER] F01(d)(ii)’s protected-object refusal has no executable effect-resolution predicate. Candidate SQL can call an existing wrapper or use dynamic SQL that mutates a manifest-listed control row without directly naming that row or `ops.scac_*`. Such DML need not change any of the five catalog censuses, so the guard can commit it. Acceptance tests only a direct namespace reference; it never tests indirect or dynamic access.

2. [BLOCKER] The restorative exception—the plan’s only recovery path for a wrongly landed row—is neither executable nor tested. It changes the assertion from transaction-prestate equality to equality with a historical receipt, but defines no exact receipt/baseline binding, command, or failure behavior. F01 acceptance includes no successful restoration, wrong-baseline, or collateral-effect case, so an implementation that merely bypasses condition (d) could pass.

VERDICT: FAIL

## Round 6 — plan under review: rev 8 — prompt sha256 cd40561eeae2f6853e8f71cfbdc8e48d1b24142c45e8febd20775b03e567bca2

PER-FINDING RECHECK:

O5. NOT FIXED — Rev 8 names a transaction and client command, but that command is not executable against the pinned repository. At `0985dcc`, `tools/db-tap.py:405–441` explicitly uses `psycopg` and `cur.execute(script)`, not psql; therefore the psql-only `\i` on plan line 39 is sent to PostgreSQL as invalid syntax. The invocation-specific candidate and target-manifest loading interface also remains unspecified.

N2. RELOCATED — Statement-count classification is gone, but completeness moved into a bounded observation surface. Artifact A observes only the enumerated catalog projections, `pg_depend`, and preselected referenced tables. Artifact C similarly snapshots only manifest-named table rows. Dynamic DML against an unselected table and changes represented in omitted catalogs are absent from both diffs; the parse cross-check shares the dynamic-SQL blind spot.

R1. NOT FIXED — Rev 8 adds direct relation, trigger, ACL, and control-row comparisons, but dissolution still binds only objects the landed files create or replace plus a `pg_depend` closure of created objects. `pg_depend` is not the semantic dependency closure of PL/pgSQL bodies or dynamic SQL, and unchanged pre-existing dependencies are not state-bound. The counterfeit-relation test does not require a counterfeit dependency outside Artifact A, so a narrow implementation can pass it.

S2. FIXED — Artifact B now has a tracked path, named generator, defined inputs and fields, pinned source commit, doctrine-pinned artifact/generator digests, and independent regeneration/readback acceptance. The former gitignored-`out/` durability defect is removed.

T1. FIXED — For the exact rev-7 exploit, a wrapper or dynamic statement changing a manifest-named control row now produces a row-digest delta and the indirect-access acceptance test requires rollback. Broader unobserved state classes remain, as noted below.

T2. NOT FIXED — The restore predicate and test names now exist, but the receipt remains declarative rather than executable. No serialization/load command or schema binds the baseline to the original row, candidate digest, target-manifest digest, database, and successful transaction; it is also unclear whether “baseline” means the saved pre-state or the pre/post pair. Consequently the required wrong-baseline refusal has no defined identity predicate.

NEW FINDINGS:

1. [BLOCKER] The claimed full-state assertion has straightforward invisible effects. An update to a row in an unselected table, `CREATE OR REPLACE VIEW` with unchanged columns, an RLS-policy change, a column-default change, or a default-ACL change can avoid every listed snapshot (`pg_rewrite`, `pg_policy`, `pg_attrdef`, and `pg_default_acl` are omitted). Such a transaction can commit with an empty observed delta. Acceptance tests only a covered grant and a covered control row.

2. [BLOCKER] Candidate SQL controls the transaction boundary. With the specified include model, a candidate can mutate state and issue `COMMIT`—or, under psql, `\quit`/`\connect`—before the post-snapshot assertion. A later exception cannot roll back the already committed effect. The plan neither forbids/preflights transaction control and psql metacommands nor tests this escape.

3. [BLOCKER] Approval is not cryptographically bound to execution. No predicate verifies the candidate SQL digest and target-manifest digest against Joe’s approval before execution. A substituted candidate whose effects remain within the approved targets therefore commits. This is compounded by Artifact C being doctrine-digest-pinned yet containing invocation-specific candidate/manifest placeholders with no named generator or verified parameter interface.

4. [MAJOR] Artifact A’s cross-check acceptance contradicts itself: line 32 says every nonempty symmetric difference fails, while line 60 permits a nonempty difference when each element is merely “dispositioned.” The required terminal state must be an empty rerun after corrections; otherwise the completeness check can pass while the two target sets still disagree.

VERDICT: FAIL

## Round 7 — plan under review: rev 9 — prompt sha256 78a48f6418f2567bdd5d5d023038045632810af34f0c4ea5c22beeed13d2c30b

PER-FINDING RECHECK:
O5. FIXED — Rev 9 now matches [`db-tap.py`’s psycopg path](/Users/booko/carr-system/tools/db-tap.py:405): one generated SQL script, executed by `cur.execute`, with candidate and manifest inlined and no psql metacommands.

N2. NOT FIXED — The surface is broader but still incomplete for honest mistakes. For example, RLS enablement (`pg_class.relrowsecurity`), relation options (`pg_class.reloptions`), and trigger enabled state (`pg_trigger.tgenabled`) are absent from the enumerated projections. Section T cannot disclaim these cooperating-session mistakes, and no acceptance fixture tests them.

R1. RELOCATED — Semantic dependency completeness is now an admission obligation of the future activation plan. The dissolution check state-binds whatever list that plan supplies, but rev 9 still provides no completeness method, and the stubbed-callee test does not require an unchanged pre-existing dependency outside Artifact A. The risk is postponed, not discharged.

T2. NOT FIXED — The schema and meaning of `pre_snapshot` are clearer, but serialization remains non-executable: current `db-tap.py` prints SQL result sets, while the specified final `DO` block returns none, and no command/wrapper writes or loads the proposed JSON receipt. The identity predicate is also wrong: `pg_control_system().system_identifier` is cluster-wide, not database-specific, so two databases in one cluster share it; the receipt’s project, branch, and database fields are not compared. The proposed “different database → system-identifier mismatch” test can therefore false-pass. [PostgreSQL documentation](https://www.postgresql.org/docs/current/functions-info.html).

U1. FIXED — All five cited blind spots are now represented: all-user-table rows, view definitions, policies, column defaults, and default ACLs. Broader incompleteness remains under N2.

U2. NOT FIXED — The preflight omits `END`, PostgreSQL’s synonym for `COMMIT`, so `END;` can commit before the postcondition block. It also specifies a raw word-boundary scan that would reject legitimate PL/pgSQL `BEGIN` tokens, including the shape used by migration 0473. The pinned repository already has a SQL-aware lexer covering `END`, `ABORT`, and `START TRANSACTION`; rev 9 neither reuses nor matches it. [PostgreSQL `END` documentation](https://www.postgresql.org/docs/current/sql-end.html).

U3. FIXED — Because the candidate and manifest are inlined, Joe’s approved generated-script SHA-256 commits to both exact inputs. The explicit pre-execution equality predicate and mismatch-refusal acceptance test are sufficient under Section T’s cooperating-session model.

U4. FIXED — Artifact A now requires an empty terminal symmetric difference without a disposition exception.

NEW FINDINGS:
1. [BLOCKER] Restore mode is information-theoretically under-specified. The receipt retains function-definition and table-row digests, not the prior definitions or row values. A digest can verify restoration but cannot generate restorative SQL, so “ANY later-discovered wrong state repairable” and generic receipt-derived restoration are false. A canned fixture could pass without solving this.

2. [BLOCKER] The target manifest authorizes identities, not expected deltas. A candidate producing the wrong body for a declared view—or the wrong values within a declared row—passes because every difference is “in the target list”; an idempotent mistake also passes replay. This contradicts Section T’s promise to catch wrong assumptions and unintended side effects.

3. [BLOCKER] “Raises, aborting the whole transaction” is false for sequence state. An insert using `nextval()` can advance a sequence; if the final guard raises, the row rolls back but the sequence advancement does not. Sequence values are not included in the snapshot set, and PostgreSQL expressly makes `nextval`/`setval` effects non-rollbackable. [PostgreSQL sequence documentation](https://www.postgresql.org/docs/current/functions-sequence.html).

4. [BLOCKER] The pre/post pair is not a consistent baseline on the real client path. `db-tap.py` establishes no isolation level, so PostgreSQL’s default Read Committed permits successive snapshot statements to observe concurrent commits. Normal production traffic can therefore be misattributed to the candidate; on a declared target, a later restore could erase an unrelated concurrent write. Idle-clone acceptance tests cannot expose this. [PostgreSQL isolation documentation](https://www.postgresql.org/docs/17/transaction-iso.html).

VERDICT: FAIL

## Round 8 — plan under review: rev 10 — prompt sha256 64f63aac37ef9c0ec91eab1b9a18e45b47db25c88f558d584734740bd8ed5d8e

PER-FINDING RECHECK:

N2. NOT FIXED — Exclusion applies only within the enumerated catalogs; catalog families remain omitted. `pg_index`, `pg_sequence`, `pg_type`/`pg_enum`, `pg_partitioned_table`, and others carry behavior absent from `pg_class`. An undeclared enum-value addition, sequence-configuration change, or wrong partial-index predicate can therefore pass. The two new fixtures test columns, not catalog-family completeness. [PostgreSQL catalog documentation](https://www.postgresql.org/docs/current/catalogs-overview.html)

R1. NOT FIXED — The named union is not a completeness method. PostgreSQL deliberately omits body-only dependencies for string-defined functions; an unspecified identifier scan cannot reliably resolve dynamic, conditional, or constructed identifiers; and one green-disposable execution traces only the exercised path. One counterfeit fixture can pass while another legitimate branch remains absent. [PostgreSQL dependency tracking](https://www.postgresql.org/docs/16/ddl-depend.html)

T2. NOT FIXED — The one-connection driver and JSON writer fix serialization, but identity remains under-specified. At the pinned commit, `db-tap.py run` exports `DATABASE_URL` and `CLOUDFLARE_ACCOUNT_ID`, not authoritative project/branch identities; rev 10 names no endpoint-to-project/branch resolution. Its sole identity fixture changes only `database_name`, so an implementation ignoring project and branch can pass.

U2. FIXED — Rev 10 directly reuses the pinned SQL-aware lexer, which covers `END`, `ABORT`, and `START/PREPARE TRANSACTION` while ignoring dollar-quoted PL/pgSQL bodies. Both required acceptance directions are present.

V1. NOT FIXED — Full pre-images remove the digest-only information deficit, but generic restoration remains unspecified. Function/view definitions, ACL rows, and `row_to_json` do not define restoration for every authorized object/row lifecycle—such as absent pre-images, insert/delete cases, indexes, constraints, generated/identity columns, or trigger-mediated rows. One successful restore fixture cannot validate the broad claim.

V2. NOT FIXED — Expected post-state catches a wrong declared view body or row value, but the manifest contains no expected pre-state or required transition. If a target already equals the expected post-state, a candidate that mistakenly changes nothing satisfies both stated assertions. Rev 10’s claim that the second side rejects this is not implemented by its predicate.

V3. FIXED — The rollback overclaim is removed; `nextval` advancement is observed separately and explicitly reported as surviving rollback.

V4. FIXED — One connection with `REPEATABLE READ` supplies a consistent MVCC baseline and prevents unrelated concurrent commits from entering successive catalog/table snapshots.

NEW FINDINGS:

1. [BLOCKER] Approval binding has no trusted comparison source. The command supplies a file after `--approved`, but no expected digest, approval receipt, or authoritative lookup. Recomputing the selected file’s hash cannot establish that Joe approved that hash; the acceptance statement likewise does not specify the oracle.

2. [BLOCKER] Receipt durability is not atomic with the database outcome. The driver commits and then writes a filesystem JSON file, leaving a crash or write-failure window where production changed without the receipt containing its restorative pre-images. WR-000046 retains only receipt digests, and no durable authoritative home for the full JSON is specified.

3. [BLOCKER] Restore mode lacks a stale-state compare-and-swap guard. It asserts the restored post-state equals the old pre-image but never requires the restore run’s pre-state to equal the incident receipt’s recorded post-state. A delayed restore can therefore overwrite legitimate later changes to the same declared target while passing every stated assertion.

4. [BLOCKER] All sequence deltas are incorrectly treated as benign advancement. `setval` can move a sequence backward or change `is_called`, and those changes also survive rollback; rev 10 records only `last_value`. Sequence configuration in `pg_sequence` is omitted as well. A reset capable of causing duplicate keys can therefore commit as a “benign residual.” [Sequence functions](https://www.postgresql.org/docs/current/functions-sequence.html), [`pg_sequence`](https://www.postgresql.org/docs/current/catalog-pg-sequence.html)

VERDICT: FAIL

## Round 9 — plan under review: rev 11 — prompt sha256 869735996f87f0cf77f9dc28834d73c432f049b4a826ca0866993d4cd0768747

PER-FINDING RECHECK:
N2. FIXED — The omitted catalog families and targeted fixtures are now specified, while Section T expressly withdraws catalog-completeness claims and accurately carries the residual through human controls.
R1. RELOCATED — The three-way union is correctly limited to evidence; dependency completeness is now an explicit F02 cross-family human-review obligation, with PostgreSQL’s body-dependency limitation disclosed.
T2. NOT FIXED — At the pinned commit, `db-tap.py` consumes `--branch` itself but passes the child only `DATABASE_URL` and `CLOUDFLARE_ACCOUNT_ID`. Artifact C therefore cannot record the claimed branch argument or authoritative project/branch identity. The acceptance fixture still varies only `database_name`, not the missing branch axis.
V1. RELOCATED — Mechanical restoration is honestly limited to eligible value-level lifecycles; unsupported lifecycles are explicitly assigned to designed manual forward fixes.
V2. FIXED — The manifest now requires expected PRE and POST states, and acceptance exercises pre-execution refusal for a mismatched PRE state.
W1. FIXED — The append-only WR-000046 note is a named, attributable approval oracle carrying all three digests, with human comparison and later recomputation defined.
W2. NOT FIXED — Prewriting is the correct ordering, but the “never leaves” durability claim remains unfalsified: acceptance contains no crash injection at the prewrite/commit boundary, and the specification does not require syncing the newly created receipt’s parent directory.
W3. FIXED — Restore now performs per-target compare-and-swap against the recorded POST state and has an explicit stale-state refusal fixture.
W4. NOT FIXED — The prose now names configuration and `is_called`, but acceptance tests only a backward `setval`. An implementation that detects value movement while ignoring `ALTER SEQUENCE` configuration or `is_called` changes could still pass.

NEW FINDINGS:
1. [BLOCKER] The expected-PRE precheck conflicts with mandatory double-apply. After pass one, the clone is at expected POST, so the identical PRE→POST manifest must refuse pass two before executing it. A separately bound POST→POST rehearsal phase is required.
2. [BLOCKER] The central claim discipline contradicts itself. Section T says Artifact C is not load-bearing, but F01 says “the first four” conditions are load-bearing; condition (d) is Artifact C, while the fourth human control is actually condition (e), forward-fix duty.

VERDICT: FAIL
