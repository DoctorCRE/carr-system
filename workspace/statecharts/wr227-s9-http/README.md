# System design

Card: WR227.S9.J1

Medium HTTP module. Data shape is a Fetch Request mapped to a Response through
exchange/read/revoke result variants from client-portal-session.v1. HTTP owns
parsing, cookies, CSRF, bounds and escaped rendering. Postgres owns authorization,
passwords, lockout, expiry, revocation and the canonical projection. The injected
safeProjection seam redacts canonical content. No browser Request or headers
crosses a database seam. Only exchange receives a transient password.

Interface considered twice. Owning database connections here would couple HTTP
to transaction policy. Choose an injected Fetch handler instead. Future adapters
MUST use parameterized authenticated carr_writer calls, read-write transactions
even for reads, commit denied and locked outcomes, and resolve only after commit.
They must implement finite statement/transaction timeouts and honor AbortSignal.
This unit has no MCP or database network client.

Capacity and scaling. No measured production traffic claim. Defaults admit eight
concurrent dependencies and 128 requests per instance with no waiting queue.
At 10x bcrypt CPU and database capacity break first. Load spreads by slug across
instances. Each instance permits ten logins and 120 other actions per slug per
minute, with at most 1024 rate buckets. Expired buckets are swept on admission.
Integration must add distributed/IP ingress limits because local limits multiply
with instances. The database's password lockout remains shared fleet-wide.

Caching and eviction. No content cache, session cache or stale fallback. Every
response is private, no-store. Cookies expire within seven days and no later
than committed SQL expiry. SameSite=Lax allows links opened from email; exact
Origin checks protect every mutation. Per-slug host-only cookies allow two
portals in one browser. Secure, HttpOnly, Path=/, no Domain.

Availability and consistency. At most one database call and one safe projection
call per request, sequentially. Each dependency has a 3s deadline. Three failures
open that dependency's breaker for 30s; one half-open probe can recover. Every failure and
saturation emits only a fixed operational reason code and returns 503. A timeout
aborts the operation but retains its slot until settlement, bounding even a
dependency that ignores cancellation. No automatic retry. Live reads refuse on
expiry, revocation, disable or database outage. Status also authorizes live.

Storage and N+1. One SQL operation per action; canonical projection caps materials
at 100 in unit A. No per-material fetch, selectors or fan-out. Async/queues n/a:
responses wait for committed results. External APIs n/a: only injected operations.
Every write has a natural key: exchange uses a fresh random 256-bit token's
SHA-256 digest; revoke uses slug and digest. Tokens/digests are never reused for
new exchanges. Repeated logout converges to a cleared cookie. Lost exchange
responses leave a bounded SQL session until expiry. Failed revoke retains the
cookie and returns 503 so a later explicit logout can try again.

Routes are GET /slug (redirect), GET /slug/ (form or escaped canonical content),
POST /slug/login (bounded URL-encoded password), GET /slug/status and POST
/slug/logout. Package, file and selection routes return 501. Publication and
partner upload routes are unreachable. Unknown queries, encoded paths and
dot segments refuse. Fetch may normalize dot segments before the handler sees
them; the normalized route still requires its own slug's live authorization.

Throughput checkpoint. Read contract, chart, red tests, implementation, focused
checks, clean-environment rerun. One worker, no fan-out. Shared state is bounded
admission, breaker and rate buckets. Worker wiring and production proof are later.

```mermaid
stateDiagram-v2
  [*] --> Unauthenticated
  Unauthenticated --> Unauthenticated: form or denied password
  Unauthenticated --> Authenticated: committed exchange
  Unauthenticated --> Locked: allowance exhausted
  Locked --> Locked: exchange before retry_at
  Locked --> Authenticated: window elapsed and valid password
  Authenticated --> Authenticated: live read or status
  Authenticated --> Expired: database denies expired session
  Expired --> Unauthenticated: clear cookie
  Authenticated --> Revoked: committed logout
  Revoked --> Revoked: repeat logout
  Revoked --> Unauthenticated: read denied
  Authenticated --> Unavailable: dependency error or deadline
  Unauthenticated --> Unavailable: error or admission refusal
  Unavailable --> Unavailable: breaker open
  Unavailable --> Authenticated: half-open live read succeeds
```

Tests derive from these transitions with synthetic contract adapters. SQL expiry
and lockout correctness belong to unit A. These tests prove HTTP mapping and
bounds, not database integration or production availability.
