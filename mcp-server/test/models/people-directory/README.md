# Directory request model

The request statechart is chart.mmd. The PostgreSQL tests cross the registered
verb interface and check paging, query changes, refusal, deletion, and merging.
The abstract chart-to-spec check covers TypeOK only. The PostgreSQL tests prove
the SQL authorization and pagination guards.

Model the Domain chose a name and UUID keyset cursor rather than offsets.
Boundary Discipline keeps schema validation at the handler entrance.
Type System Discipline derives request types from registered schemas.

Throughput checkpoint. Registry discovery and the main merge block implementation.
The contract, index and handler share one branch, so one owner is the smallest
safe decomposition. There are no independent workstreams or new mutable state.

System design. CARR owns canonical actor and party identities. A focused module
exposes two registered reads. A SQL definer API would duplicate partner authority
and add function grants. The selected handlers reuse existing authorization.
The roster uses a narrow active-actor view because carr_reader cannot read actor.

Production count is unobserved because this job forbids production data calls.
At N and 10N parties the partial party_directory_name_idx B-tree seeks by prefix
and name/UUID keyset, returning at most 101 rows. Pages cap at 100. Broad prefixes
and roster growth are the first pressure points. A roster above 1000 refuses.
Each request uses two queries with no N+1. Existing verb budgets cap authenticated
humans at 60 requests per minute and agents at 1200 per minute. Budget failures
refuse before application database access. Existing budget tests prove this path.

CARR stores actors and parties in its single server-bound carr-internal tenant.
Neither table has a tenant column. Reads check server binding and database session
scope and refuse foreign tenants. New tenants require a new ownership contract.

Pages read current committed data. Unchanged names page stably through duplicate
names and deletions. Renames can move rows across pages. Refresh after edits.
Cursors bind the normalized query. No cache, stale fallback, async jobs, writes,
idempotency keys, external provider calls, retry loops or circuit breakers apply.
Database errors refuse requests. Scaling follows the existing Postgres service.

The coordinator owns production proof after deployment. Confirm both partners in
the roster and select a name-only search result UUID in the Home waiting picker.
