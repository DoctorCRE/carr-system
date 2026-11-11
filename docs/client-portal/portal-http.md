# Client portal HTTP host

Card: WR227.S9.J3

`mcp-server/src/portal-web.ts` exports `createPortalHost(writerConnectionString,
configuredOrigin)` and the injectable `createPortalWebHandler`. Construct the host
once per isolate. The origin defaults to `https://share.doctorcre.com`. It is
configuration, never taken from a request header. This change does not register
DNS, mount an origin, modify Worker routing, deploy or change existing sites.

The host uses the registered `client-portal-session.v1` SQL functions and
`portal-proposals.v1` storage. It does not use partner Deal Room or report-share
authentication. All database calls run in read-write transactions. Denied and
locked password exchanges commit so the shared lockout allowance persists.
Connection acquisition is bounded at 1500ms, statements at 2500ms, locks at
1000ms and idle transactions at 3000ms. Each dependency retains its admission
slot through settlement, including after HTTP timeout. No implicit retry occurs.

## Routes and shapes

`slug` must match `^[a-z0-9][a-z0-9-]{0,62}$`. `publication` is an exact lowercase
UUID. Query parameters and encoded paths refuse. All responses are private,
no-store with no CORS headers. Errors are JSON objects with one `error` field,
except lockout also includes `message` and `retry_at`.

| Method and route | Request | Response |
| --- | --- | --- |
| GET `/<slug>` | None | 308 to `/<slug>/` |
| GET `/<slug>/` | Optional session cookie | Minimal HTML login form or escaped published portal projection |
| POST `/<slug>/login` | URL-encoded `password` and `csrf`, exact Origin, anonymous CSRF cookie | 303 to portal, opaque session cookie; 401 `portal_access_denied`; 429 `portal_locked` with `retry_at` and Retry-After |
| GET `/<slug>/status` | Session cookie | 200 `{"authenticated":true,"csrf":"64 hex characters"}`; 401 `unauthenticated` |
| POST `/<slug>/logout` | URL-encoded `csrf`, session cookie, exact Origin | 303 to portal after committed revoke, session cookie cleared |
| GET `/<slug>/proposals/<publication>` | Session cookie | Unwrapped `portal-proposals.v1` receipt |
| POST `/<slug>/proposals/<publication>` | JSON below, session cookie, exact Origin and `X-Portal-CSRF` | Unwrapped confirmed `portal-proposals.v1` receipt |

The login page sets `__Host-portal-csrf-<slug>` and exposes its state in the
`portal-csrf` meta element and hidden form field. Login requires both. An active
session uses a domain-separated SHA-256 CSRF value derived from its random token,
exposed through status and the page. The bearer token is never exposed in HTML.
The session cookie is `__Host-portal-<slug>`, Secure, HttpOnly, SameSite=Lax,
Path=/, with no Domain, and expires no later than the SQL expiry or seven days.
Two portal slugs keep independent sessions. Logout requires active-token CSRF;
repeated logout with the retained cookie and CSRF converges.

POST proposal body, with exactly these keys:

```json
{
  "publication_version": 2,
  "request": {
    "version": 4,
    "selected": ["00000000-0000-4000-8000-000000000021"],
    "notes": {"00000000-0000-4000-8000-000000000021": "Client note"},
    "idempotency_key": "frozen-request-key"
  }
}
```

Use the exact publication revision returned by the read, and freeze the whole
body and key for uncertain retries. The URL publication ID is an untrusted
resource selector checked against the live SQL session's portal; callers cannot
supply deal/client selectors. SQL enforces current publication membership, CAS,
request limits and exact-key replay. The receipt has `status: success`,
`schema_version: portal-proposals.v1`, `publication_id`, `publication_version`,
`selection: {version, selected, notes}` and `retired: string[]`. App PR 876 can
feed that unwrapped receipt directly into its retained parser.

Typed HTTP refusals are 401 `unauthenticated`, 403 `csrf`, 403 `origin`,
429 `rate_limited`, 409 `stale_version` (SQL version_conflict), and 409 `retired`
(SQL property_not_published or publication_unavailable). Other SQL refusals
preserve their code and 400/409 status, including invalid_proposal,
proposal_capacity and idempotency_conflict. An inaccessible publication is also
`retired`, without disclosing another portal. No proposal modifies canonical
records. Packages, files and old selections paths remain 501.

## Bounds and staging continuation

The total request budget is 3000ms, login/logout bodies 8192 bytes, and proposal
bodies 262144 bytes. Defaults admit eight dependency operations and 128 requests
per isolate. Rate limits are ten login attempts per slug per minute and 120
other requests per session per minute. Anonymous reads use a per-slug bucket.
At most 1024 buckets exist; expired buckets evict. Three dependency failures open
a 30-second circuit with a single recovery probe. Saturation and dependency
errors return 503 portal_unavailable and emit fixed redacted reason codes.
SQL lockout is fleet-wide; in-memory admission and rate limits are per isolate.

After merge, a separately assigned staging job must mount this factory on one
explicit HTTPS staging origin with the writer binding, origin routing before
OAuth/MCP fallback, distributed ingress/IP limits and redacted error monitoring.
It must serve the app shell and configure same-origin transport for PR 876,
verify existing slug/password and five-failure lockout, and run session,
cross-portal, save/reload, withdrawal and expiry journeys against real staged
publications. No password, package, alias, legacy selection import, DNS cutover,
production deployment or signed-in staging acceptance is included here.

The handler supports the minimal HTML shell only. Existing live portal content
and URLs remain on their current hosts until an explicit staging/cutover job.
