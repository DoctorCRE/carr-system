# Standard client portal site: design and slice plan

Card: WR227.S2.J2. Work Request WR-000227, slice S2. Design only; no product code.

## Outcome

Every client gets the same coded market-study site inside a password-protected
portal at `share.doctorcre.com/<slug>`. One template renders a typed per-deal
**data package**; client data and files live in the record layer and the
private `carr-documents` R2 bucket, never in git. The carr-mcp Worker serves the
portal behind the `ops.client_portal` password that slice S1 registers. The two
reference sites (the two newest, called REF-A and REF-V below)
are rebuilt from their own data and must match the live sites item for item
against the 456-row inventory in this document: 281 items for REF-A, 175 for
REF-V. Every existing client address and password keeps working (AC1).

Sources read for this design: WR-000227, decision 9cbc9aea (Joe 2026-10-27),
rule ac0a6579 (simple client passwords), decisions d6c453ff and 4ab3933e, the
`map-architecture` verb (contract `carr-workspace-market-map-route-planning`
1.2.0), `mcp-server/src/client-portal.ts`, migrations 0975, 0976 and 0981,
S1 (carr-system PR #2525, app PR #453), `mcp-server/src/reports-web.js`, and
read-only copies of the live site sources.

### Naming in this public document

carr-system is public, so this document carries no client names, people,
addresses, prices, figures or place names. The reference sites are codenamed:

| Codename | Site | Status |
|---|---|---|
| REF-A | One of the two newest market studies; has the ownership modeler | Reference format |
| REF-V | The other newest market study | Reference format |
| LEG-1, LEG-2 | The two older sites | Legacy; moved as-is |

`<slug-a>`, `<slug-v>`, `<slug-l1>` and `<slug-l2>` stand for their live URL
slugs. The mapping from codename to slug is the `ops.client_portal` row each
site gets during migration. Examples use the synthetic client "Example Health
Group" at slug `example-health`.

## Terms

These are the canonical words for this work. The glossary of record is the
doctrine store (`docs/agents/domain.md`); slice S3 writes these terms there.

- **Client portal**: the one password-protected page set at
  `share.doctorcre.com/<slug>` for one client and one deal, backed by one
  `ops.client_portal` row. It is where partners share everything the client
  should see. Avoid: share site, client site, presentation (for the whole).
- **Deliverable**: a piece of work CARR produces for the client and shares in
  the portal. Kinds today: market study, tour, report.
- **Document**: a file shared in the portal that CARR did not generate as a
  deliverable, such as a floor plan, an LOI or a lease abstract.
- **Portal material**: the code term (`ops.client_portal_material`) covering both
  deliverables and documents shared in one portal.
- **Market study**: the preliminary market study deliverable: the interactive
  site with property search, market data, demographics and a Tour List. It is
  the first deliverable a client sees and is presented on the onboarding call.
- **Template**: the one coded market-study site (HTML, CSS, JavaScript) that
  renders any valid data package. It holds no client data. Avoid: site source.
- **Data package**: the typed, versioned JSON document, plus the files it
  references, that holds every per-deal value one market study shows. Avoid:
  data.js, site data.
- **Publication**: one immutable published version of a data package for one
  portal. The portal serves its newest publication.
- **Tour List**: the client's saved shortlist of properties with notes inside a
  market study. It is not a CARR Tour (the reference code says so explicitly).

## Decisions

Grilling ran without an interview, as the brief requires: each question was
answered from the sources and the reason is recorded here. Each decision names
what would reopen it.

**D1. One template, built from REF-A as the superset.** REF-A's stylesheet
contains every one of REF-V's 1,034 CSS rules plus 64 more; 30 of REF-V's 34
named app.js functions also exist in REF-A; the fonts and CARR logo are
byte-identical. REF-V's only unique parts (an excluded market-context listing,
institution context markers, a current-practice dialog, a property table) become
optional template modules. Reopen if a new client needs a layout neither site has.

**D2. Optional modules switch on by data presence, not by flags.** A section
renders when its part of the data package is present: ownership modeler (REF-A
"30/70"), micro-zones, competitor directory, market-context listing, institution
anchors, master-plan overlay, property table, current-practice marker. No mode
flags (zero-tech-debt).

**D3. The template renders at request time from the data package; the
generator does not emit per-deal HTML.** Both reference sites already render
most content in the browser from data files. One deployed template means a
template fix reaches every portal at once (locality). The package carries its
schema version, and the template supports only the current one: a schema
change ships with a migration that re-publishes every live package at the new
version, so no old-version rendering path survives (zero-tech-debt).
Rejected: a build step that writes static HTML per deal, which multiplies every
template fix by the number of portals. Reopen if a client must see a frozen
snapshot that later template releases may not change.

**D4. Every per-deal value moves into the data package, including values
hard-coded in today's app.js.** The inventory marks these rows. Examples: map
centre and zoom, per-property pin offsets, review-point text, project blurbs and
image credits, growth and age figures, the current-practice address, the default
ownership scenario property, the competitor market list. Rich text in the
package is typed (text runs and links), never raw HTML.

**D5. Client data lives in the record layer and R2, never in git.** Postgres
holds the portal, its publications, sessions, Tour List state and portal
material rows. R2 `carr-documents` holds package JSON and files under
`client-portal/<slug>/...`, keyed by content hash. The public repo holds only the
template, the schema, the Worker adapter and a synthetic fixture.

**D6. share.doctorcre.com moves to the carr-mcp Worker as a fourth client
host.** Platform check: Cloudflare Workers custom domains already serve
`api`, `dealroom` and `reports.doctorcre.com` from this Worker, which already
binds R2 `carr-documents` and reaches Postgres. The gap in today's host (two
OpenAI Sites projects, one behind a proxy) is that it cannot read
`ops.client_portal`, R2 or the deal record without storing a CARR credential at
a third party. Rejected: keeping Sites and calling the CARR API from it (two
hosts, a stored credential, two deploy paths). Reopen if the Worker cannot take
the custom domain.

**D7. The portal adapter copies the `reports-web.js` shape.** One host adapter
module (`portal-web.js`) with injected record-layer seams, an opaque session
cookie whose digest is stored server-side, same-origin writes with CSRF, strict
CSP and self-hosted MapLibre. Front-end files ship in the pinned doctorcre-app
artifact under `/portal/`, as `/reports/` and `/tours/` do today.

**D8. Passwords stay simple and are checked by S1's function.** Rule ac0a6579:
the client-name password stays. `ops.read_client_portal` already does the bcrypt
check and the five-failures-in-15-minutes lock. The adapter calls it (or a
sibling that returns a session) through the Worker's database connection, not
through the authenticated MCP verb, which is documented as not a public login
endpoint.

**D9. The lockout needs a clear message on the onboarding call.** Concern: S1
returns the same null for a wrong password and for a locked slug, so a client
who mistypes five times during the video call sees "incorrect password" for 15
minutes. Safer option adopted for slices S8 and S9: the verify function returns a
reason (`denied` or `locked`) and the page says when to retry; a partner verb
clears the lock. Slug existence is not secret (the URL is given to the client),
so the reason leaks nothing new.

**D10. One portal per client and deal, as S1 built it.** `ops.client_portal`
binds a slug to one client and one deal and refuses a second active portal for
a deal. A client with a second deal reopens decision 9cbc9aea.

**D11. The Tour List stays a portal record, separate from CARR Tours.**
`ops.client_portal_selection` holds selections and per-property notes with a
version for compare-and-swap, exactly as both sites do in D1 today. Partners see
it on the deal card (slice S20).

**D12. Later deliverables and documents arrive as portal materials.** A partner
verb shares a file or a new deliverable into a portal and another withdraws it.
The existing material table gains file columns; text-only materials keep working.
A new market study version is a new publication, not a material.

**D13. The look stays as it is.** Joe 2026-10-27: "we like the way these sites
look so format is pretty much set." Source conflict, stated openly (K06): the
WR-000227 desired outcome (captured 2026-08-03) asks for a dark cinematic
register. Joe's newer, live direction wins for the market study; the roadmap and
explainer sections (AC2, AC4) get their own design in slices S18 and S19.

**D14. Reference format is REF-A and REF-V, not LEG-2.** Source conflict, stated
openly: decision d6c453ff (2026-10-05) named the LEG-2 site as the format to
generate from. Joe's 2026-10-27 direction names REF-A and REF-V. The newer live
direction wins; LEG-1 and LEG-2 move unchanged as legacy bundles.

**D15. Market studies keep their source captions.** Source conflict, stated
openly: decision 4ab3933e and the map doctrine say client-facing Tour maps,
shares and PDFs carry no source or caveat line. That ruling is scoped to Tours.
Market studies are evidence documents and both reference sites show sources on
every figure; the template keeps them. Market-study pins are labelled
approximate locators and are never handed to navigation, so the entrance-level
coordinate gate does not apply; a market study that adds navigation must pass it.

**D16. One MapLibre renderer, self-hosted, one pinned version.** Map doctrine:
one MapLibre renderer by default. Today REF-V loads 5.6.2 and REF-A loads 6.13.0
from a public CDN. The template self-hosts one pinned version (as reports-web
does). Basemap stays a data choice with two real adapters, because both exist
in the references: `local-layers` (REF-V: TIGER water and roads GeoJSON from R2)
and `vector-style` (REF-A: a public vector tile host). The vector tile host
needs a provider rights receipt on file before the template ships it (slice S16).

## Inventory of the reference sites

### How the reference sites are built, served and gated today

Both are hand-built folders of HTML, CSS, JavaScript, data files and media,
packed by a script that AES-GCM-encrypts each file and writes a route manifest.
A Worker releases a file only after a password form sets a signed
`__Host-` session cookie (7 days). Each site has a D1 table for its Tour List.

- **REF-V** is served directly by the share.doctorcre.com Worker (an OpenAI
  Sites project) at `/<slug-v>/`. Code `VS` in the tables.
- **REF-A** is a second OpenAI Sites project. The share.doctorcre.com Worker
  proxies `/<slug-a>/` to it with a service token. Code `AS` in the tables.
- The content keys and passwords are Sites secrets that cannot be read back, so
  the deployed ciphertext cannot be decrypted locally. The read-only source
  copies are the reference; acceptance stage 0 proves they match what is live.
- The share Worker's router sends every path that is not REF-A, REF-V or LEG-1
  to the LEG-2 handler, which also serves the bare-domain landing page.

### Counting rules

- One row per section, interactive tool, link, asset and gate behaviour,
  including one row per HTTP error path each Worker returns (404, 400, 401,
  403, 405, 413, 415, 502, 503), read from the Worker code.
- A link or asset that repeats once per data item gets one row per item, by
  ordinal. A link whose target changes with a switch (for example a source link
  that follows the selected geography) gets one row per state.
- **T** is template: identical structure and copy for every client. **D** is
  per-deal data. **T+D** is a template element whose content is per-deal data.
- "Hard-coded" means a per-deal value written inside today's app.js or Worker
  code; D4 moves each one into the data package.
- The tables were generated from the source copies by a script that counts data
  items by ordinal and prints no values. Readers can check each subtotal by
  counting rows.

### Totals

| Site | Gate | Section | Tool | Link | Asset | Total | T | T+D | D |
|---|---|---|---|---|---|---|---|---|---|
| REF-A | 39 | 47 | 56 | 99 | 40 | **281** | 126 | 64 | 91 |
| REF-V | 24 | 32 | 28 | 55 | 36 | **175** | 64 | 36 | 75 |
| Both | 63 | 79 | 84 | 154 | 76 | **456** | 190 | 100 | 166 |

Findings the inventory surfaced:

- REF-V serves five files nothing references (four retired listing photos and
  one GeoJSON), and its deploy carries two ciphertexts the manifest no longer
  maps. The template does not carry them (allowed difference AD4).
- REF-A serves its build input `properties.json`, which duplicates the
  properties in `data.js`. The template does not serve it (AD4).
- REF-A's location-illustration fallback references a second file that does not
  exist; no current property triggers it.
- REF-A's competitor filter offers a market with no offices. Parity keeps it
  (the market list becomes package data); whether to drop it is a content call.

#### REF-A gates (39)

| ID | Item | T/D | Built today | Served and gated today |
|---|---|---|---|---|
| A-G01 | Edge proxy on share.doctorcre.com: `/<slug-a>` and `/<slug-a>/*` forwarded to the origin site with a service token; strips auth headers; forwards only the portal cookie; rewrites redirects; 15 s timeout; Origin check on POST/PUT | T+D | share Worker proxy module for REF-A; origin host and token name per site | edge |
| A-G02 | Origin: `/` and `/<slug-a>` redirect 301 to `/<slug-a>/` | T | origin Worker code (`portal.js`) | AS, open |
| A-G03 | Password page: client name line, dark card, password form | T+D | HTML string in origin Worker code; client name hard-coded | AS, open |
| A-G04 | Password check: HMAC compare; success sets `__Host-` cookie (expiry, nonce, HMAC; 7 days) and 303 | T | origin Worker code; password is a Sites secret | AS, open |
| A-G05 | Wrong password: 401 with error line | T | origin Worker code | AS, open |
| A-G06 | Asset release: manifest lookup, AES-GCM decrypt, private headers, `frame-ancestors none` CSP | T | `assets.js` manifest generated by `tools/package.mjs` | AS, gated |
| A-G07 | Session status: `GET /<slug-a>/auth/status` returns authenticated flag and CSRF token | T | origin Worker code | AS |
| A-G08 | Sign out: `POST /<slug-a>/auth/logout` with Origin and CSRF, clears the cookie | T | origin Worker code | AS, gated + CSRF |
| A-G09 | Tour List read: `GET /<slug-a>/api/selection` | T | origin Worker code (`selection.js`) | AS, gated |
| A-G10 | Tour List write: `PUT` compare-and-swap with Origin and CSRF, 409 on conflict | T | origin Worker code | AS, gated + CSRF |
| A-G11 | Tour List storage: one D1 row per site (sorted selections, notes by property, version) | T+D | origin D1 table `selection_state` | AS, D1 |
| A-G12 | Property-ID allowlist for the Tour List generated from `properties.json` at build | T+D | `property-ids.js` generated by `tools/package.mjs` | AS |
| A-G13 | Path safety and private headers (same rules as REF-V) | T | origin Worker code | AS |
| A-G14 | Error path: 404 for an unknown or unsafe path, returned before the gate | T | origin Worker code | AS |
| A-G15 | Error path: 503 "temporarily unavailable" when the site secrets are not configured | T | origin Worker code | AS |
| A-G16 | Error path: 405 with `Allow: GET, HEAD, POST` for other page methods; 405 with `Allow: GET, HEAD` for a POST to any path but the root | T | origin Worker code | AS |
| A-G17 | Error path: 403 for a password POST from another origin or a cross-site fetch | T | origin Worker code | AS |
| A-G18 | Error path: 415 for a password form that is not URL-encoded | T | origin Worker code | AS |
| A-G19 | Error path: 413 for a password form over 2 KB | T | origin Worker code | AS |
| A-G20 | Error path: 400 for a password form that is not valid UTF-8 | T | origin Worker code | AS |
| A-G21 | Error path: 401 with an empty body for HEAD without a session | T | origin Worker code | AS |
| A-G22 | Error path: 503 when a released file is missing or fails to decrypt | T | origin Worker code | AS |
| A-G23 | Error path: Tour List API: 405 `method_not_allowed` | T | origin Worker code | AS |
| A-G24 | Error path: Tour List API: 401 `authentication_required` | T | origin Worker code | AS |
| A-G25 | Error path: Tour List API: 403 `forbidden` for a PUT with a bad Origin or CSRF token | T | origin Worker code | AS |
| A-G26 | Error path: Tour List API: 415 `unsupported_content_type` | T | origin Worker code | AS |
| A-G27 | Error path: Tour List API: 413 `request_too_large` over 96 KB | T | origin Worker code | AS |
| A-G28 | Error path: Tour List API: 400 `invalid_selection` | T | origin Worker code | AS |
| A-G29 | Error path: Tour List API: 503 `storage_unavailable` | T | origin Worker code | AS |
| A-G30 | Error path: Session status: 405 for a method other than GET | T | origin Worker code | AS |
| A-G31 | Error path: Session status: 401 with `authenticated: false` without a session | T | origin Worker code | AS |
| A-G32 | Error path: Sign out: 405 for a method other than POST | T | origin Worker code | AS |
| A-G33 | Error path: Sign out: 401 without a session | T | origin Worker code | AS |
| A-G34 | Error path: Sign out: 403 for a bad Origin or CSRF token | T | origin Worker code | AS |
| A-G35 | Error path: Edge proxy: 404 for an unsafe path | T | share Worker proxy module for REF-A | edge |
| A-G36 | Error path: Edge proxy: 405 with `Allow: GET, HEAD, POST, PUT` | T | share Worker proxy module for REF-A | edge |
| A-G37 | Error path: Edge proxy: 403 for a cross-origin POST or PUT | T | share Worker proxy module for REF-A | edge |
| A-G38 | Error path: Edge proxy: 503 when the service token is not configured | T | share Worker proxy module for REF-A | edge |
| A-G39 | Error path: Edge proxy: 502 for an upstream failure, a 15 s timeout, or a redirect off the origin | T | share Worker proxy module for REF-A | edge |

#### REF-A sections (47)

| ID | Item | T/D | Built today | Served and gated today |
|---|---|---|---|---|
| A-S01 | Scroll progress bar | T | index.html + app.js | AS, gated |
| A-S02 | Site header: CARR logo, client logo with wordmark fallback, main nav with Sign Out | T+D | index.html | AS, gated |
| A-S03 | Sticky page title per view (home title names the market) | T+D | app.js; titles hard-coded | AS, gated |
| A-S04 | Footer: CARR, client name, date, Sources link | T+D | index.html | AS, gated |
| A-S05 | Home view | T | index.html | AS, gated |
| A-S06 | Home hero: client logo, prepared-for line, market headline, intro | D | index.html | AS, gated |
| A-S07 | Home mini-map card with caption | T+D | index.html | AS, gated |
| A-S08 | Home market snapshot: three stat tiles | D | index.html | AS, gated |
| A-S09 | Major Development Activity section | T+D | index.html + app.js | AS, gated |
| A-S10 | Home call to action | T | index.html | AS, gated |
| A-S11 | Purchases view | T | index.html | AS, gated |
| A-S12 | Selection intro paragraph and six screening-factor chips | T+D | index.html; chips identical to REF-V | AS, gated |
| A-S13 | Purchase map block | T | index.html | AS, gated |
| A-S14 | Choose A Property directory | T+D | index.html + app.js | AS, gated |
| A-S15 | Purchases call to action | T | index.html | AS, gated |
| A-S16 | Ownership view (nav label "30/70") | T | index.html | AS, gated |
| A-S17 | Ownership intro paragraph | T+D | index.html | AS, gated |
| A-S18 | Stage 1 "Understand The Ownership Structure": four benefit cards | T+D | index.html; program minimums written into the copy | AS, gated |
| A-S19 | Stage 1 Assumptions heading, six assumption inputs and note | T+D | inserted by app.js; note names one property and its income | AS, gated |
| A-S20 | Stage 1 "The 30 / 70 Split" card: donut SVG and per-property allocation rows | T+D | index.html + app.js | AS, gated |
| A-S21 | Stage 1 "What Covers The Mortgage?" card (moved into stage 1 by app.js) | T | index.html + app.js | AS, gated |
| A-S22 | Stage 2 "Compare The Buildings": comparison table and "What Stands Out" cards | T+D | inserted by app.js | AS, gated |
| A-S23 | Stage 3 "Plan The Cash Commitment": buildout and tenant-improvement cards, scenario form card, Cash Carry table and note | T+D | index.html + app.js; note names one property and its income | AS, gated |
| A-S24 | Stage 4 "See The Holding-Period Outcome": line chart, equity explorer, ten-year outcome table, ownership-versus-leasing summary | T | index.html + app.js | AS, gated |
| A-S25 | "Illustration Methods And Qualifications" disclosure | T+D | index.html; names properties and figures | AS, gated |
| A-S26 | Tour List view (same structure as REF-V) | T | index.html + app.js | AS, gated |
| A-S27 | Demographics view | T | index.html | AS, gated |
| A-S28 | Regional growth: four stat tiles, reach paragraph | D | index.html | AS, gated |
| A-S29 | Local Age and Household Context heading | T | index.html | AS, gated |
| A-S30 | Household Context: median income for every geography | T+D | index.html + app.js from `data.js` | AS, gated |
| A-S31 | Higher-Income Households: radius-report and municipal cards, notes disclosure | T+D | index.html + app.js | AS, gated |
| A-S32 | Micro-zone Study Areas: zone switch, map, summary card, topic switch, profile, note, study PDF links | T+D | index.html + app.js from `microzones` | AS, gated |
| A-S33 | Competitor Map: counts, market filter, map, status line, office cards, scope disclosure | T+D | index.html + app.js from `competition` | AS, gated |
| A-S34 | "Sources and methodology" disclosure | D | index.html | AS, gated |
| A-S35 | Sources view: Search Criteria (client, criteria, website) | D | index.html | AS, gated |
| A-S36 | Sources view: Listing Sources (rendered list) | T+D | app.js from `data.js` | AS, gated |
| A-S37 | Sources view: Market Sources (three items) | D | index.html | AS, gated |
| A-S38 | Sources view: regional master-plan sources section | D | index.html | AS, gated |
| A-S39 | Sources view: Demographic Sources (rendered list and period note) | T+D | index.html + app.js | AS, gated |
| A-S40 | Sources view: radius report PDFs and municipal ACS paragraphs | D | index.html | AS, gated |
| A-S41 | Sources view: competitor directory sources (rendered list) | T+D | index.html + app.js | AS, gated |
| A-S42 | Sources view: Locator Sources paragraph | D | index.html | AS, gated |
| A-S43 | Sources view: Ownership Illustration paragraph | D | index.html | AS, gated |
| A-S44 | Property dialog: logos, photo, option number, facts table hiding "not verified" values, highlights, add-to-list toggle | T+D | app.js | AS, gated |
| A-S45 | Project dialog: overlay note, figure, status and metric, copy, relevance, precision, source and "View primary source" | T+D | app.js from the developments list in `data.js` | AS, gated |
| A-S46 | Practice profile dialog: name, category, logo, address, services, website, verification note | T+D | app.js from `competition.offices` | AS, gated |
| A-S47 | Current-practice context dialog | D | app.js; address hard-coded | AS, gated |

#### REF-A tools (56)

| ID | Item | T/D | Built today | Served and gated today |
|---|---|---|---|---|
| A-T01 | Hash router across six views; lazy start of four maps | T | app.js | AS, gated (runs in the browser) |
| A-T02 | Scroll motion and reveal | T | app.js | AS, gated (runs in the browser) |
| A-T03 | Header-height tracker | T | app.js | AS, gated (runs in the browser) |
| A-T04 | Client-logo activation | T | app.js | AS, gated (runs in the browser) |
| A-T05 | MapLibre ES-module import with "map unavailable" fallback | T | app.js | AS, gated (runs in the browser) |
| A-T06 | Vector basemap style with CARR palette recolour on load | T | app.js; style URL hard-coded | AS, gated (runs in the browser) |
| A-T07 | Home development map: project pins with preview and dialog | T+D | app.js; fit bounds include hard-coded points | AS, gated (runs in the browser) |
| A-T08 | Home master-plan overlay: fill, road-context lines, dashed closure, click and floating label open its project | T+D | app.js; geometry in the developments list in `data.js` | AS, gated (runs in the browser) |
| A-T09 | Purchase map: numbered pins, preview, open property | T+D | app.js; preferred offsets hard-coded per property | AS, gated (runs in the browser) |
| A-T10 | Current-practice marker on home and purchase maps with context dialog | D | app.js; coordinates and address hard-coded | AS, gated (runs in the browser) |
| A-T11 | Pin spacing with stems (attribution-aware on the competition map) | T | app.js | AS, gated (runs in the browser) |
| A-T12 | Property tiles highlight their map pin on hover or focus | T | app.js | AS, gated (runs in the browser) |
| A-T13 | Hover/focus preview popover | T | app.js | AS, gated (runs in the browser) |
| A-T14 | Property dialog open, close, backdrop close, focus return | T | app.js | AS, gated (runs in the browser) |
| A-T15 | Add To The List toggle | T | app.js | AS, gated (runs in the browser) |
| A-T16 | Development tiles with preview, open project dialog | T | app.js | AS, gated (runs in the browser) |
| A-T17 | "View primary source" closes dialogs and jumps to Sources | T | app.js | AS, gated (runs in the browser) |
| A-T18 | Regional growth switch (5 geographies) and bars | T+D | app.js from `regional` | AS, gated (runs in the browser) |
| A-T19 | Age donut switch (5 geographies) | T+D | app.js from `regional` | AS, gated (runs in the browser) |
| A-T20 | Median income meters with selected highlight | T+D | app.js from `regional` | AS, gated (runs in the browser) |
| A-T21 | Income threshold switch (3) over radius and municipal cards | T+D | app.js from `reports`, `incomeMunicipal`; zone short names hard-coded | AS, gated (runs in the browser) |
| A-T22 | Micro-zone switch (3 buttons) | T+D | app.js | AS, gated (runs in the browser) |
| A-T23 | Micro-zone map: circles with selected highlight, numbered centre pins, click a circle to select | T+D | app.js; circle colours in code | AS, gated (runs in the browser) |
| A-T24 | Micro-zone summary card with study PDF button | T+D | app.js | AS, gated (runs in the browser) |
| A-T25 | Micro-zone topic switch (3) and profile charts | T+D | app.js | AS, gated (runs in the browser) |
| A-T26 | Competition count tiles per market | T+D | app.js; market list hard-coded | AS, gated (runs in the browser) |
| A-T27 | Competition market filter (All plus four markets; one market has no offices) | T+D | index.html; market list hard-coded | AS, gated (runs in the browser) |
| A-T28 | Competition map: office pins, filter hides pins, highlight sync with cards | T+D | app.js | AS, gated (runs in the browser) |
| A-T29 | Office map preview popover: logo, close, "View Practice Details" | T | app.js | AS, gated (runs in the browser) |
| A-T30 | Office cards open the practice dialog | T | app.js | AS, gated (runs in the browser) |
| A-T31 | Practice dialog open, close, backdrop close, focus return | T | app.js | AS, gated (runs in the browser) |
| A-T32 | Tour List available cards | T | app.js | AS, gated (runs in the browser) |
| A-T33 | Tour List selected items with notes and counter | T | app.js | AS, gated (runs in the browser) |
| A-T34 | Drag a card into the selections | T | app.js | AS, gated (runs in the browser) |
| A-T35 | Save Selections (compare-and-swap; conflict locks saving until reload) | T | app.js | AS, gated (runs in the browser) |
| A-T36 | Reload Shared List | T | app.js | AS, gated (runs in the browser) |
| A-T37 | Load the shared list on start | T | app.js | AS, gated (runs in the browser) |
| A-T38 | Sign Out: fetch CSRF if needed, POST logout, confirm when unsaved | T | app.js | AS, gated (runs in the browser) |
| A-T39 | Leave-page guard while Tour List edits are unsaved | T | app.js | AS, gated (runs in the browser) |
| A-T40 | Ownership eligibility rule: which properties enter the model (building tenure, size, offering, contiguous space, existing income) | T | app.js `ownershipChoices()` | AS, gated (runs in the browser) |
| A-T41 | Ownership finance model: amortisation, lease-up phasing, rent collection, appreciation, cash contributed, equity | T | app.js `holdingPoint()` | AS, gated (runs in the browser) |
| A-T42 | Shared assumption inputs (6) kept in step with the scenario form | T | app.js | AS, gated (runs in the browser) |
| A-T43 | Property scenario selector | T+D | app.js; default property hard-coded | AS, gated (runs in the browser) |
| A-T44 | Scenario form: 13 fields, read-only practice SF, Calculate, validation messages | T+D | app.js; default values hard-coded | AS, gated (runs in the browser) |
| A-T45 | Per-property practice occupancy inputs (minimum 30% and 2,200 SF) and allocation bars | T | app.js | AS, gated (runs in the browser) |
| A-T46 | Coverage rows: payment against outside rent; click opens the property | T | app.js | AS, gated (runs in the browser) |
| A-T47 | Comparison table (13 metrics) | T | app.js | AS, gated (runs in the browser) |
| A-T48 | "What Stands Out" property cards open the property | T | app.js | AS, gated (runs in the browser) |
| A-T49 | Tenant-improvement allowance input and two synced $25/$50/$75 toggle groups | T | app.js | AS, gated (runs in the browser) |
| A-T50 | Practice lease rate and lease buildout inputs | T | app.js | AS, gated (runs in the browser) |
| A-T51 | Cash Carry table (8 metrics) | T | app.js | AS, gated (runs in the browser) |
| A-T52 | Line chart: view selector, property selector, legend, SVG chart, summary with crossing year | T | app.js | AS, gated (runs in the browser) |
| A-T53 | Equity year slider (0 to 10) and equity bars | T | app.js | AS, gated (runs in the browser) |
| A-T54 | Ten-year outcome table (5 metrics) | T | app.js | AS, gated (runs in the browser) |
| A-T55 | Ownership-versus-leasing table (6 metrics) | T | app.js | AS, gated (runs in the browser) |
| A-T56 | Currency inputs reformat on blur | T | app.js | AS, gated (runs in the browser) |

#### REF-A links (99)

| ID | Item | T/D | Built today | Served and gated today |
|---|---|---|---|---|
| A-L01 | Skip to content | T | index.html or app.js | in page |
| A-L02 | Header CARR logo to the CARR website | T | index.html or app.js | external, new tab |
| A-L03 | Header client logo to Home | T | index.html or app.js | in page |
| A-L04 | Nav: Home | T | index.html or app.js | in page |
| A-L05 | Nav: Purchases | T | index.html or app.js | in page |
| A-L06 | Nav: 30/70 | T | index.html or app.js | in page |
| A-L07 | Nav: Tour List | T | index.html or app.js | in page |
| A-L08 | Nav: Demographics | T | index.html or app.js | in page |
| A-L09 | Nav: Sources | T | index.html or app.js | in page |
| A-L10 | Snapshot tile 1 to Demographics | T+D | index.html or app.js | in page |
| A-L11 | Snapshot tile 2 to Demographics | T+D | index.html or app.js | in page |
| A-L12 | Snapshot tile 3 to Demographics | T+D | index.html or app.js | in page |
| A-L13 | "Explore demographics and sources" to Demographics | T | index.html or app.js | in page |
| A-L14 | Home call to action to Purchases | T | index.html or app.js | in page |
| A-L15 | Purchases call to action to Tour List | T | index.html or app.js | in page |
| A-L16 | Higher-income notes link to Sources | T | index.html or app.js | in page |
| A-L17 | Competition scope link to Sources | T | index.html or app.js | in page |
| A-L18 | Search Criteria: client website | D | index.html or app.js | external, new tab |
| A-L19 | Market source 1 (hospital announcement) | D | index.html or app.js | external, new tab |
| A-L20 | Market source 2 (city grant announcement) | D | index.html or app.js | external, new tab |
| A-L21 | Market source 3 (county roadway agreement) | D | index.html or app.js | external, new tab |
| A-L22 | Master-plan ordinance PDF | D | index.html or app.js | external, new tab |
| A-L23 | County road-centreline GIS service | D | index.html or app.js | external, new tab |
| A-L24 | City comprehensive plan PDF | D | index.html or app.js | external, new tab |
| A-L25 | Municipal ACS income table | D | index.html or app.js | external, new tab |
| A-L26 | Municipal profile public mirror | D | index.html or app.js | external, new tab |
| A-L27 | Basemap provider quick-start (attribution) | T | index.html or app.js | external, new tab |
| A-L28 | Footer Sources link | T | index.html or app.js | in page |
| A-L29 | Property dialog CARR logo to the CARR website | T | index.html or app.js | external, new tab |
| A-L30 | Current-practice dialog "View sources" | T | index.html or app.js | in page |
| A-L31 | Listing Sources: property 1, source 1 | D | app.js from `data.js` | external, new tab |
| A-L32 | Listing Sources: property 1, source 2 | D | app.js from `data.js` | external, new tab |
| A-L33 | Listing Sources: property 2, source 1 | D | app.js from `data.js` | external, new tab |
| A-L34 | Listing Sources: property 3, source 1 | D | app.js from `data.js` | external, new tab |
| A-L35 | Listing Sources: property 3, source 2 | D | app.js from `data.js` | external, new tab |
| A-L36 | Listing Sources: property 4, source 1 | D | app.js from `data.js` | external, new tab |
| A-L37 | Listing Sources: property 5, source 1 | D | app.js from `data.js` | external, new tab |
| A-L38 | Listing Sources: property 6, source 1 | D | app.js from `data.js` | external, new tab |
| A-L39 | Listing Sources: property 7, source 1 | D | app.js from `data.js` | external, new tab |
| A-L40 | Listing Sources: property 8, source 1 | D | app.js from `data.js` | external, new tab |
| A-L41 | Demographic Sources: QuickFacts, geography 1 | D | app.js from `regional` | external, new tab |
| A-L42 | Demographic Sources: QuickFacts, geography 2 | D | app.js from `regional` | external, new tab |
| A-L43 | Demographic Sources: QuickFacts, geography 3 | D | app.js from `regional` | external, new tab |
| A-L44 | Demographic Sources: QuickFacts, geography 4 | D | app.js from `regional` | external, new tab |
| A-L45 | Demographic Sources: QuickFacts, geography 5 | D | app.js from `regional` | external, new tab |
| A-L46 | Growth chart source, geography 1 state | D | app.js from `regional` | external, new tab |
| A-L47 | Growth chart source, geography 2 state | D | app.js from `regional` | external, new tab |
| A-L48 | Growth chart source, geography 3 state | D | app.js from `regional` | external, new tab |
| A-L49 | Growth chart source, geography 4 state | D | app.js from `regional` | external, new tab |
| A-L50 | Growth chart source, geography 5 state | D | app.js from `regional` | external, new tab |
| A-L51 | Age dashboard source, geography 1 state | D | app.js from `regional` | external, new tab |
| A-L52 | Age dashboard source, geography 2 state | D | app.js from `regional` | external, new tab |
| A-L53 | Age dashboard source, geography 3 state | D | app.js from `regional` | external, new tab |
| A-L54 | Age dashboard source, geography 4 state | D | app.js from `regional` | external, new tab |
| A-L55 | Age dashboard source, geography 5 state | D | app.js from `regional` | external, new tab |
| A-L56 | Micro-zone summary: study PDF button, zone 1 state | D | app.js from `microzones` | gated file, new tab |
| A-L57 | Micro-zone summary: study PDF button, zone 2 state | D | app.js from `microzones` | gated file, new tab |
| A-L58 | Micro-zone summary: study PDF button, zone 3 state | D | app.js from `microzones` | gated file, new tab |
| A-L59 | Micro-zone report link, zone 1 | D | app.js from `microzones` | gated file, new tab |
| A-L60 | Micro-zone report link, zone 2 | D | app.js from `microzones` | gated file, new tab |
| A-L61 | Micro-zone report link, zone 3 | D | app.js from `microzones` | gated file, new tab |
| A-L62 | Competitor directory source, office 1 | D | app.js from `competition` | external, new tab |
| A-L63 | Competitor directory source, office 2 | D | app.js from `competition` | external, new tab |
| A-L64 | Competitor directory source, office 3 | D | app.js from `competition` | external, new tab |
| A-L65 | Competitor directory source, office 4 | D | app.js from `competition` | external, new tab |
| A-L66 | Competitor directory source, office 5 | D | app.js from `competition` | external, new tab |
| A-L67 | Competitor directory source, office 6 | D | app.js from `competition` | external, new tab |
| A-L68 | Competitor directory source, office 7 | D | app.js from `competition` | external, new tab |
| A-L69 | Practice dialog "Primary practice website", office 1 | D | app.js from `competition` | external, new tab |
| A-L70 | Practice dialog "Primary practice website", office 2 | D | app.js from `competition` | external, new tab |
| A-L71 | Practice dialog "Primary practice website", office 3 | D | app.js from `competition` | external, new tab |
| A-L72 | Practice dialog "Primary practice website", office 4 | D | app.js from `competition` | external, new tab |
| A-L73 | Practice dialog "Primary practice website", office 5 | D | app.js from `competition` | external, new tab |
| A-L74 | Practice dialog "Primary practice website", office 6 | D | app.js from `competition` | external, new tab |
| A-L75 | Practice dialog "Primary practice website", office 7 | D | app.js from `competition` | external, new tab |
| A-L76 | Project 1 dialog "View primary source" to Sources | T | app.js | in page |
| A-L77 | Project 2 dialog "View primary source" to Sources | T | app.js | in page |
| A-L78 | Project 3 dialog "View primary source" to Sources | T | app.js | in page |
| A-L79 | Project 4 dialog "View primary source" to Sources | T | app.js | in page |
| A-L80 | Coverage row, eligible property 1, opens the property | T+D | app.js | in page |
| A-L81 | Coverage row, eligible property 2, opens the property | T+D | app.js | in page |
| A-L82 | Coverage row, eligible property 3, opens the property | T+D | app.js | in page |
| A-L83 | Comparison table header, eligible property 1, opens the property | T+D | app.js | in page |
| A-L84 | Comparison table header, eligible property 2, opens the property | T+D | app.js | in page |
| A-L85 | Comparison table header, eligible property 3, opens the property | T+D | app.js | in page |
| A-L86 | Cash Carry table header, eligible property 1, opens the property | T+D | app.js | in page |
| A-L87 | Cash Carry table header, eligible property 2, opens the property | T+D | app.js | in page |
| A-L88 | Cash Carry table header, eligible property 3, opens the property | T+D | app.js | in page |
| A-L89 | Ten-year outcome table header, eligible property 1, opens the property | T+D | app.js | in page |
| A-L90 | Ten-year outcome table header, eligible property 2, opens the property | T+D | app.js | in page |
| A-L91 | Ten-year outcome table header, eligible property 3, opens the property | T+D | app.js | in page |
| A-L92 | Ownership-versus-leasing table header, eligible property 1, opens the property | T+D | app.js | in page |
| A-L93 | Ownership-versus-leasing table header, eligible property 2, opens the property | T+D | app.js | in page |
| A-L94 | Ownership-versus-leasing table header, eligible property 3, opens the property | T+D | app.js | in page |
| A-L95 | Redirect page `demographics.html` to `#demographics` | T | hand-written HTML (client name in title) | gated file |
| A-L96 | Redirect page `ownership.html` to `#strategy` | T | hand-written HTML (client name in title) | gated file |
| A-L97 | Redirect page `purchases.html` to `#purchases` | T | hand-written HTML (client name in title) | gated file |
| A-L98 | Redirect page `sources.html` to `#sources` | T | hand-written HTML (client name in title) | gated file |
| A-L99 | Redirect page `tour.html` to `#tour` | T | hand-written HTML (client name in title) | gated file |

#### REF-A assets (40)

| ID | Item | T/D | Built today | Served and gated today |
|---|---|---|---|---|
| A-A01 | `index.html` | T+D | hand-written; mixes layout with deal text | AS, gated, encrypted at rest |
| A-A02 | `styles.css` | T | hand-written; superset of REF-V (every REF-V rule plus 64) | AS, gated, encrypted at rest |
| A-A03 | `app.js` | T+D | hand-written; template code with deal values inside | AS, gated, encrypted at rest |
| A-A04 | `data.js` (properties, regional, reports, competition, municipal income, micro-zones, developments) | D | hand-built data | AS, gated, encrypted at rest |
| A-A05 | `properties.json` (build input for the Tour List allowlist; also served) | D | hand-built; identical to the properties in `data.js` | AS, gated, encrypted at rest |
| A-A06 | Redirect page HTML 1 of 5 | T | hand-written | AS, gated, encrypted at rest |
| A-A07 | Redirect page HTML 2 of 5 | T | hand-written | AS, gated, encrypted at rest |
| A-A08 | Redirect page HTML 3 of 5 | T | hand-written | AS, gated, encrypted at rest |
| A-A09 | Redirect page HTML 4 of 5 | T | hand-written | AS, gated, encrypted at rest |
| A-A10 | Redirect page HTML 5 of 5 | T | hand-written | AS, gated, encrypted at rest |
| A-A11 | CARR logo PNG | T | shared file | AS, gated, encrypted at rest |
| A-A12 | Client logo PNG | D | supplied | AS, gated, encrypted at rest |
| A-A13 | Heading font TTF | T | shared file | AS, gated, encrypted at rest |
| A-A14 | Body font TTF | T | shared file | AS, gated, encrypted at rest |
| A-A15 | Property photo 1 | D | supplied or screenshot | AS, gated, encrypted at rest |
| A-A16 | Property photo 2 | D | supplied or screenshot | AS, gated, encrypted at rest |
| A-A17 | Property photo 3 | D | supplied or screenshot | AS, gated, encrypted at rest |
| A-A18 | Property photo 4 | D | supplied or screenshot | AS, gated, encrypted at rest |
| A-A19 | Property photo 5 | D | supplied or screenshot | AS, gated, encrypted at rest |
| A-A20 | Property photo 6 | D | supplied or screenshot | AS, gated, encrypted at rest |
| A-A21 | Property photo 7 | D | supplied or screenshot | AS, gated, encrypted at rest |
| A-A22 | Property photo 8 | D | supplied or screenshot | AS, gated, encrypted at rest |
| A-A23 | Development image 1 | D | supplied, screenshot or drawn SVG | AS, gated, encrypted at rest |
| A-A24 | Development image 2 | D | supplied, screenshot or drawn SVG | AS, gated, encrypted at rest |
| A-A25 | Development image 3 | D | supplied, screenshot or drawn SVG | AS, gated, encrypted at rest |
| A-A26 | Development image 4 | D | supplied, screenshot or drawn SVG | AS, gated, encrypted at rest |
| A-A27 | Competitor logo 1 | D | screenshot of the practice website | AS, gated, encrypted at rest |
| A-A28 | Competitor logo 2 | D | screenshot of the practice website | AS, gated, encrypted at rest |
| A-A29 | Competitor logo 3 | D | screenshot of the practice website | AS, gated, encrypted at rest |
| A-A30 | Competitor logo 4 | D | screenshot of the practice website | AS, gated, encrypted at rest |
| A-A31 | Competitor logo 5 | D | screenshot of the practice website | AS, gated, encrypted at rest |
| A-A32 | Competitor logo 6 | D | screenshot of the practice website | AS, gated, encrypted at rest |
| A-A33 | Competitor logo 7 | D | screenshot of the practice website | AS, gated, encrypted at rest |
| A-A34 | Location illustration SVG (fallback for a property with no photo; no current property uses it) | T | drawn | AS, gated, encrypted at rest |
| A-A35 | Radius study PDF 1 | D | supplied report | AS, gated, encrypted at rest |
| A-A36 | Radius study PDF 2 | D | supplied report | AS, gated, encrypted at rest |
| A-A37 | Radius study PDF 3 | D | supplied report | AS, gated, encrypted at rest |
| A-A38 | MapLibre CSS 6.13.0 from a public CDN | T | third party | public CDN, outside the gate |
| A-A39 | MapLibre ES module 6.13.0 from a public CDN | T | third party | public CDN, outside the gate |
| A-A40 | Vector basemap style, tiles, glyphs and sprites from a public tile host | T | third party | public tile host, outside the gate |

#### REF-V gates (24)

| ID | Item | T/D | Built today | Served and gated today |
|---|---|---|---|---|
| V-G01 | Bare path `/<slug-v>` redirects 301 to `/<slug-v>/` | T | share Worker module for REF-V | VS |
| V-G02 | Password page: client wordmark, "Presented by CARR" line, password form | T+D | HTML string in Worker code; client name and brand colours hard-coded | VS, open |
| V-G03 | Password check: HMAC compare against the site password secret; success sets `__Host-` session cookie (expiry + HMAC, 7 days) and 303 to the site | T | Worker code; password is a Sites secret | VS, open |
| V-G04 | Wrong password: 401 with the password page and an error line | T | Worker code | VS, open |
| V-G05 | Asset release: manifest lookup, AES-GCM decrypt of the ciphertext, private no-store headers | T | route manifest generated by the REF-V packer script | VS, gated |
| V-G06 | Tour List read: `GET /<slug-v>/api/selection` returns version, selections, per-property notes, CSRF token | T | share Worker selection module for REF-V | VS, gated |
| V-G07 | Tour List write: `PUT` with Origin check and CSRF header, compare-and-swap on version, 409 with current state on conflict | T | Worker code | VS, gated + CSRF |
| V-G08 | Tour List storage: one D1 row per site (selections, notes by property, legacy notes column, version); retired property IDs carried forward on save | T+D | D1 table `<site>_selection_state`; allowed and retired IDs hard-coded in Worker code | VS, D1 |
| V-G09 | Path safety and private headers: no `%`, `\`, NUL, `//`, dot segments; no-store, nosniff, noindex, same-origin referrer; strict CSP on the password page | T | Worker code | VS |
| V-G10 | Error path: 404 for an unknown or unsafe path, returned before the gate | T | Worker code | VS |
| V-G11 | Error path: 503 "temporarily unavailable" when the site secrets are not configured | T | Worker code | VS |
| V-G12 | Error path: 405 with `Allow: GET, HEAD, POST` for other page methods; 405 with `Allow: GET, HEAD` for a POST to any path but the root | T | Worker code | VS |
| V-G13 | Error path: 403 for a password POST from another origin or a cross-site fetch | T | Worker code | VS |
| V-G14 | Error path: 413 for a password form over 2 KB (declared or streamed) | T | Worker code | VS |
| V-G15 | Error path: 415 for a password form that is not URL-encoded | T | Worker code | VS |
| V-G16 | Error path: 401 with an empty body for HEAD without a session | T | Worker code | VS |
| V-G17 | Error path: 503 when a released file is missing or fails to decrypt | T | Worker code | VS |
| V-G18 | Error path: Tour List API: 401 without a session | T | Worker code | VS |
| V-G19 | Error path: Tour List API: 405 with `Allow: GET, PUT` | T | Worker code | VS |
| V-G20 | Error path: Tour List API: 403 for a PUT with a bad Origin or CSRF token | T | Worker code | VS |
| V-G21 | Error path: Tour List API: 413 for a body over 96 KB | T | Worker code | VS |
| V-G22 | Error path: Tour List API: 415 for a body that is not JSON | T | Worker code | VS |
| V-G23 | Error path: Tour List API: 400 for an invalid selection | T | Worker code | VS |
| V-G24 | Error path: Tour List API: 503 when storage is unavailable or a stored row is invalid | T | Worker code | VS |

#### REF-V sections (32)

| ID | Item | T/D | Built today | Served and gated today |
|---|---|---|---|---|
| V-S01 | Scroll progress bar | T | index.html + CSS variable from app.js | VS, gated |
| V-S02 | Site header: CARR logo, client logo with wordmark fallback, main nav | T+D | index.html; client logo and wordmark per deal | VS, gated |
| V-S03 | Sticky page title per view (home title names the market) | T+D | app.js map of view to title; home title hard-coded per deal | VS, gated |
| V-S04 | Footer: CARR, client name, date, feasibility disclaimer, Sources link | T+D | index.html, values hard-coded | VS, gated |
| V-S05 | Home view | T | index.html | VS, gated |
| V-S06 | Home hero: client wordmark, prepared-for line (contact and date), market headline, intro | D | index.html, hand-written | VS, gated |
| V-S07 | Home mini-map card with caption | T+D | index.html; caption per deal | VS, gated |
| V-S08 | Home market snapshot: three stat tiles | D | index.html, values hand-written | VS, gated |
| V-S09 | Major Development Activity section (heading, intro, directory) | T+D | index.html + app.js | VS, gated |
| V-S10 | Home call to action | T | index.html | VS, gated |
| V-S11 | Purchases view | T | index.html | VS, gated |
| V-S12 | Selection intro paragraph and six screening-factor chips | T+D | index.html; paragraph per deal, chips identical in both sites | VS, gated |
| V-S13 | Properties At A Glance: note, purchase map, locator note, table, parking note | T+D | index.html + app.js; notes and dates per deal | VS, gated |
| V-S14 | Choose A Property directory | T+D | index.html + app.js from `data.js` | VS, gated |
| V-S15 | Market-context card: listing shown but excluded from the shortlist | T+D | app.js from the `market-context` property; explanation text hard-coded | VS, gated |
| V-S16 | Purchases call to action (Start Tour List) | T | index.html | VS, gated |
| V-S17 | Demographics view | T | index.html | VS, gated |
| V-S18 | Regional growth: heading, area label, four stat tiles, reach paragraph, source link | D | index.html, values hand-written | VS, gated |
| V-S19 | Local Age and Household Context heading | T | index.html | VS, gated |
| V-S20 | Household Context: median household income comparison | T+D | index.html + app.js (values hard-coded in app.js) | VS, gated |
| V-S21 | Higher-Income Household Context with "Geography and source notes" disclosure | T+D | index.html + app.js from `affluent-data.js` | VS, gated |
| V-S22 | "Sources and methodology" disclosure | D | index.html, hand-written | VS, gated |
| V-S23 | Demographics source-links note | D | index.html | VS, gated |
| V-S24 | Tour List view: intro, available panel, selected panel, status line, drop target, save and reload buttons, not-an-appointment note | T | index.html + app.js | VS, gated |
| V-S25 | Sources view: Listing Sources paragraph | D | index.html | VS, gated |
| V-S26 | Sources view: Market Sources list | D | index.html | VS, gated |
| V-S27 | Sources view: Locator Sources paragraph and plans/photos disclaimer | T+D | index.html | VS, gated |
| V-S28 | Property dialog: logos, photo and caption, option number, four facts, Property Review, First Question To Resolve, discrepancy/possession/public-check notes, standard walkthrough note, floor-plan button, source caption, add-to-list toggle | T+D | app.js template; review-point text per property hard-coded in app.js | VS, gated |
| V-S29 | Market-context dialog variant | T+D | app.js; explanation hard-coded | VS, gated |
| V-S30 | Current-practice context dialog: client name, address, size, locator caveat, website link | D | app.js, all values hard-coded | VS, gated |
| V-S31 | Project dialog: figure and credit, location, sponsor concept, map precision, status, latest update, scale, relevance, primary sources | T+D | app.js from `market-data.js`; image paths and credits hard-coded in app.js | VS, gated |
| V-S32 | Regional-context dialog for an institution anchor: name, address, category, precision, two source links | T+D | app.js from the anchors geojson | VS, gated |

#### REF-V tools (28)

| ID | Item | T/D | Built today | Served and gated today |
|---|---|---|---|---|
| V-T01 | Hash router: one view at a time, nav `aria-current`, sticky title, scroll to top, lazy map start | T | app.js `show()` | VS, gated (runs in the browser) |
| V-T02 | Scroll motion: progress variable, landmark shift, reveal-on-scroll, reduced-motion respected | T | app.js | VS, gated (runs in the browser) |
| V-T03 | Header-height tracker for sticky offsets | T | app.js | VS, gated (runs in the browser) |
| V-T04 | Client-logo activation with wordmark fallback | T | app.js | VS, gated (runs in the browser) |
| V-T05 | Home development map: MapLibre, local water and roads layers, zoom buttons, scroll zoom off, fit to markers | T+D | app.js; centre, zoom and layer files hard-coded per deal | VS, gated (runs in the browser) |
| V-T06 | Development pins: numbered, preview on hover or focus, click opens project dialog | T+D | app.js from `development-coordinates.js` | VS, gated (runs in the browser) |
| V-T07 | Institution context markers: category icon, preview, click opens context dialog | T+D | app.js fetches the anchors geojson | VS, gated (runs in the browser) |
| V-T08 | Current-practice marker on both maps, preview, click opens practice dialog | D | app.js; label letter, address and coordinates hard-coded | VS, gated (runs in the browser) |
| V-T09 | Pin spacing: collision-avoiding offsets with stems; per-property preferred offsets | T+D | app.js; offsets hard-coded per property | VS, gated (runs in the browser) |
| V-T10 | Purchase map: numbered locator pins, preview, click opens property | T+D | app.js from `coordinates.js` | VS, gated (runs in the browser) |
| V-T11 | Purchase table: number, address button, asking, SF, parking, review point | T+D | app.js from `data.js`; one parking override hard-coded | VS, gated (runs in the browser) |
| V-T12 | Property directory tiles: preview and open | T+D | app.js | VS, gated (runs in the browser) |
| V-T13 | Hover/focus preview popover (property, project, context) | T | app.js | VS, gated (runs in the browser) |
| V-T14 | Property dialog open, close button, backdrop close, focus return | T | app.js | VS, gated (runs in the browser) |
| V-T15 | Add To The List toggle synced with Tour List state | T | app.js | VS, gated (runs in the browser) |
| V-T16 | Market-context open button | T | app.js | VS, gated (runs in the browser) |
| V-T17 | Development directory tiles open project dialog | T+D | app.js; stage and summary text hard-coded per project | VS, gated (runs in the browser) |
| V-T18 | Project dialog open, close, backdrop close, focus return | T | app.js | VS, gated (runs in the browser) |
| V-T19 | Regional growth geography switch (3) and bar chart with projection | D | app.js, all figures hard-coded | VS, gated (runs in the browser) |
| V-T20 | Age and household geography switch (2) and age donut | D | app.js, all figures hard-coded | VS, gated (runs in the browser) |
| V-T21 | Median household income meters | D | app.js, figures hard-coded | VS, gated (runs in the browser) |
| V-T22 | Income threshold switch (3) with combined, county and place bars | T+D | app.js from `affluent-data.js`; place short names hard-coded | VS, gated (runs in the browser) |
| V-T23 | Tour List available cards: drag handle, open, Add | T | app.js | VS, gated (runs in the browser) |
| V-T24 | Tour List selected items: open, Remove, notes field with 2,000-character counter and NUL stripping | T | app.js | VS, gated (runs in the browser) |
| V-T25 | Drag a card into the selections | T | app.js | VS, gated (runs in the browser) |
| V-T26 | Save Selections: compare-and-swap PUT, conflict and partial-save messages | T | app.js | VS, gated (runs in the browser) |
| V-T27 | Reload Shared List with discard confirmation | T | app.js | VS, gated (runs in the browser) |
| V-T28 | Load the shared list on start, status line states | T | app.js | VS, gated (runs in the browser) |

#### REF-V links (55)

| ID | Item | T/D | Built today | Served and gated today |
|---|---|---|---|---|
| V-L01 | Skip to content | T | index.html or app.js | in page |
| V-L02 | Header CARR logo to the CARR website | T | index.html or app.js | external, new tab |
| V-L03 | Header client logo to Home | T | index.html or app.js | in page |
| V-L04 | Nav: Home | T | index.html or app.js | in page |
| V-L05 | Nav: Purchases | T | index.html or app.js | in page |
| V-L06 | Nav: Tour List | T | index.html or app.js | in page |
| V-L07 | Nav: Demographics | T | index.html or app.js | in page |
| V-L08 | Nav: Sources | T | index.html or app.js | in page |
| V-L09 | Snapshot tile 1 to Demographics | T+D | index.html or app.js | in page |
| V-L10 | Snapshot tile 2 to Demographics | T+D | index.html or app.js | in page |
| V-L11 | Snapshot tile 3 to Demographics | T+D | index.html or app.js | in page |
| V-L12 | "Explore demographics and sources" to Demographics | T | index.html or app.js | in page |
| V-L13 | Home call to action to Purchases | T | index.html or app.js | in page |
| V-L14 | Purchases call to action to Tour List | T | index.html or app.js | in page |
| V-L15 | Regional growth: state metro population source | D | index.html or app.js | external, new tab |
| V-L16 | Income notes: ACS summary-file source | D | index.html or app.js | external, new tab |
| V-L17 | Methodology note: state population estimates | D | index.html or app.js | external, new tab |
| V-L18 | Methodology note: Census QuickFacts, city | D | index.html or app.js | external, new tab |
| V-L19 | Methodology note: Census QuickFacts, county | D | index.html or app.js | external, new tab |
| V-L20 | Sources view: market source 1 (state estimates) | D | index.html or app.js | external, new tab |
| V-L21 | Sources view: market source 2 (hospital location page) | D | index.html or app.js | external, new tab |
| V-L22 | Footer Sources link | T | index.html or app.js | in page |
| V-L23 | Property dialog CARR logo to the CARR website | T | index.html or app.js | external, new tab |
| V-L24 | Growth chart: state projection source | D | index.html or app.js | external, new tab |
| V-L25 | Age dashboard source link, city state | D | index.html or app.js | external, new tab |
| V-L26 | Age dashboard source link, county state | D | index.html or app.js | external, new tab |
| V-L27 | Income comparison "View source", city state | D | index.html or app.js | external, new tab |
| V-L28 | Income comparison "View source", county state | D | index.html or app.js | external, new tab |
| V-L29 | Current-practice dialog: client website | D | index.html or app.js | external, new tab |
| V-L30 | Property dialog "View Supplied Floor Plan", property with a plan #1 | T+D | app.js from `data.js` | gated file, new tab |
| V-L31 | Property dialog "View Supplied Floor Plan", property with a plan #2 | T+D | app.js from `data.js` | gated file, new tab |
| V-L32 | Property dialog "View Supplied Floor Plan", property with a plan #3 | T+D | app.js from `data.js` | gated file, new tab |
| V-L33 | Project 1 dialog: primary source 1 | D | app.js from `market-data.js` | external, new tab |
| V-L34 | Project 1 dialog: primary source 2 | D | app.js from `market-data.js` | external, new tab |
| V-L35 | Project 1 dialog: primary source 3 | D | app.js from `market-data.js` | external, new tab |
| V-L36 | Project 2 dialog: primary source 1 | D | app.js from `market-data.js` | external, new tab |
| V-L37 | Project 2 dialog: primary source 2 | D | app.js from `market-data.js` | external, new tab |
| V-L38 | Project 3 dialog: primary source 1 | D | app.js from `market-data.js` | external, new tab |
| V-L39 | Project 3 dialog: primary source 2 | D | app.js from `market-data.js` | external, new tab |
| V-L40 | Project 3 dialog: primary source 3 | D | app.js from `market-data.js` | external, new tab |
| V-L41 | Project 3 dialog: primary source 4 | D | app.js from `market-data.js` | external, new tab |
| V-L42 | Project 4 dialog: primary source 1 | D | app.js from `market-data.js` | external, new tab |
| V-L43 | Project 4 dialog: primary source 2 | D | app.js from `market-data.js` | external, new tab |
| V-L44 | Project 4 dialog: primary source 3 | D | app.js from `market-data.js` | external, new tab |
| V-L45 | Project 4 dialog: primary source 4 | D | app.js from `market-data.js` | external, new tab |
| V-L46 | Institution anchor 1 dialog: address source | D | app.js from the anchors geojson | external, new tab |
| V-L47 | Institution anchor 1 dialog: Census locator source | D | app.js from the anchors geojson | external, new tab |
| V-L48 | Institution anchor 2 dialog: address source | D | app.js from the anchors geojson | external, new tab |
| V-L49 | Institution anchor 2 dialog: Census locator source | D | app.js from the anchors geojson | external, new tab |
| V-L50 | Institution anchor 3 dialog: address source | D | app.js from the anchors geojson | external, new tab |
| V-L51 | Institution anchor 3 dialog: Census locator source | D | app.js from the anchors geojson | external, new tab |
| V-L52 | Institution anchor 4 dialog: address source | D | app.js from the anchors geojson | external, new tab |
| V-L53 | Institution anchor 4 dialog: Census locator source | D | app.js from the anchors geojson | external, new tab |
| V-L54 | Institution anchor 5 dialog: address source | D | app.js from the anchors geojson | external, new tab |
| V-L55 | Institution anchor 5 dialog: Census locator source | D | app.js from the anchors geojson | external, new tab |

#### REF-V assets (36)

| ID | Item | T/D | Built today | Served and gated today |
|---|---|---|---|---|
| V-A01 | `index.html` | T+D | hand-written; mixes layout with deal text | VS, gated, encrypted at rest |
| V-A02 | `styles.css` | T | hand-written; a strict subset of REF-A styles | VS, gated, encrypted at rest |
| V-A03 | `app.js` | T+D | hand-written; template code with deal values inside | VS, gated, encrypted at rest |
| V-A04 | `data.js` (properties, sources, floor plans) | D | hand-built data | VS, gated, encrypted at rest |
| V-A05 | `coordinates.js` (property and practice locators) | D | hand-built data | VS, gated, encrypted at rest |
| V-A06 | `development-coordinates.js` | D | hand-built data | VS, gated, encrypted at rest |
| V-A07 | `market-data.js` (demographics, projects) | D | hand-built data | VS, gated, encrypted at rest |
| V-A08 | `affluent-data.js` (income bands) | D | hand-built data | VS, gated, encrypted at rest |
| V-A09 | `tour-api-config.js` (selection endpoint) | T+D | hand-written; path names the slug | VS, gated, encrypted at rest |
| V-A10 | CARR logo PNG | T | shared file (byte-identical in REF-A) | VS, gated, encrypted at rest |
| V-A11 | Client logo PNG | D | supplied | VS, gated, encrypted at rest |
| V-A12 | Heading font TTF | T | shared file (byte-identical in REF-A) | VS, gated, encrypted at rest |
| V-A13 | Body font TTF | T | shared file (byte-identical in REF-A) | VS, gated, encrypted at rest |
| V-A14 | Listing photo 1 (referenced) | D | supplied | VS, gated, encrypted at rest |
| V-A15 | Listing photo 2 (referenced) | D | supplied | VS, gated, encrypted at rest |
| V-A16 | Listing photo 3 (referenced) | D | supplied | VS, gated, encrypted at rest |
| V-A17 | Listing photo 4 (referenced) | D | supplied | VS, gated, encrypted at rest |
| V-A18 | Listing photo 5 (referenced) | D | supplied | VS, gated, encrypted at rest |
| V-A19 | Listing photo 6 (served, not referenced: retired listing) | D | supplied | VS, gated, encrypted at rest |
| V-A20 | Listing photo 7 (served, not referenced: retired listing) | D | supplied | VS, gated, encrypted at rest |
| V-A21 | Listing photo 8 (served, not referenced: retired listing) | D | supplied | VS, gated, encrypted at rest |
| V-A22 | Listing photo 9 (served, not referenced: retired listing) | D | supplied | VS, gated, encrypted at rest |
| V-A23 | Floor plan PDF 1 | D | supplied | VS, gated, encrypted at rest |
| V-A24 | Floor plan PDF 2 | D | supplied | VS, gated, encrypted at rest |
| V-A25 | Floor plan image 3 | D | supplied | VS, gated, encrypted at rest |
| V-A26 | Development image 1 | D | supplied or screenshot | VS, gated, encrypted at rest |
| V-A27 | Development image 2 | D | supplied or screenshot | VS, gated, encrypted at rest |
| V-A28 | Development image 3 | D | supplied or screenshot | VS, gated, encrypted at rest |
| V-A29 | Development image 4 | D | supplied or screenshot | VS, gated, encrypted at rest |
| V-A30 | Water layer GeoJSON (Census TIGER) | D | built from public data | VS, gated, encrypted at rest |
| V-A31 | Roads layer GeoJSON (Census TIGER) | D | built from public data | VS, gated, encrypted at rest |
| V-A32 | Institution anchors GeoJSON | D | built from public data | VS, gated, encrypted at rest |
| V-A33 | Project anchors GeoJSON (served, not referenced) | D | built from public data | VS, gated, encrypted at rest |
| V-A34 | Development asset manifest JSON (provenance; skipped by the packer, not served) | D | hand-built | not served |
| V-A35 | MapLibre CSS 5.6.2 from a public CDN | T | third party | public CDN, outside the gate |
| V-A36 | MapLibre JS 5.6.2 from a public CDN | T | third party | public CDN, outside the gate |


## Target design

### The pieces

```text
deal + client record ─────────┐
research package (partial, R2) ┴─► package builder ─► validate (schema) ─► publish-market-study
reference extractor (one-off, migration only) ─────────────────────────────┘ │
                                                                              ▼
                       Postgres: ops.client_portal_publication ◄──┴──► R2: client-portal/<slug>/...
                                                                  │
browser ─► share.doctorcre.com/<slug>/ ─► carr-mcp Worker: portal-web.js
              password form ─► ops verify function (bcrypt, lockout) ─► session cookie
              template files (/portal/ in the pinned app artifact, no client data)
              gated: package JSON, files, Tour List API, materials, sign-out
```

| Module | Repo | Interface | What it hides |
|---|---|---|---|
| Data package schema `carr.market-study.v1` | carr-system `client-portal/market-study.v1.schema.json` | One JSON Schema plus a synthetic fixture | Every per-deal field and its type |
| Package builder | carr-system `tools/client-portal/` | `build(deal, research) -> package` | Overlaying the record-layer fields onto a research package |
| Reference extractor (migration tool, deleted in S22) | carr-system `tools/client-portal/` | `extract(site-folder) -> package` | Reading today's data files and hard-coded app.js values |
| Template | doctorcre-app, shipped under `/portal/` | `render(package)` in the browser | All layout, maps, charts, the ownership model, the Tour List UI |
| Portal adapter `portal-web.js` | carr-system `mcp-server/src/` | `handle(request, env, ctx, seams)` for host share.doctorcre.com | Gate, session, routing, CSP, file release, API |
| Record-layer functions and verbs | carr-system migrations and `client-portal.ts` | `publish-market-study`, `share-portal-material`, `withdraw-portal-material`, `clear-portal-lockout`, plus S1's `register-client-portal` | Storage, isolation, versions, hashing |

One seam is real because two adapters exist on day one: the basemap
(`local-layers`, `vector-style`). The reference extractor is not a second
package source: it has exactly two inputs, runs once during migration, and is
deleted with the cutover (S22). No other seam is introduced.

### Data package `carr.market-study.v1`

Field groups, in render order. `?` marks an optional module (D2). `Asset` is a
reference to a file in R2: `{ "sha256": "<hex>", "name": "photo-1.jpg", "type": "image/jpeg" }`.
`Rich` is a list of runs: `{ "text": "..." }` or `{ "link": { "label": "...", "url": "https://..." } }`.

```text
schema            "carr.market-study.v1"
client            display_name, wordmark {line1, line2}, logo?: Asset, website?: url,
                  current_practice?: {address, size_note, marker_letter, locator, source_note: Rich}
prepared          for (contact name), on (date)
market            name, home_title, headline, intro, snapshot [3 × {value, label}],
                  footer_note, mini_map_caption
map               basemap: {kind: "local-layers", water: Asset, roads: Asset, attribution}
                         | {kind: "vector-style", style_url, attribution_link},
                  center [lon, lat], zoom, home_extent_points? [[lon, lat]]
properties[]      id, number, address, city?, status, role: "candidate" | "market-context",
                  photo: Asset, facts [{label, value}], summary, review_point?,
                  notes? {plan, possession, public_check}, floorplan?: Asset,
                  sources [{label, url}], source_caption?,
                  locator? {lon, lat, precision, preferred_offset? [x, y]},
                  ownership? {total_sf, asking_price, tenure, offering, contiguous_sf,
                              available_sf?, current_occupancy?, current_income_annual?}
purchases         intro, screening_factors [6], table? {locator_note, parking_note},
                  market_context_note?
developments[]    id, title, place, status, stage, summary, copy?, relevance,
                  image: {asset: Asset, alt, credit, fit?}, locator? {lon, lat, precision},
                  overlay?: GeoJSON, overlay_note?, concept_note?, scale?, latest_update?,
                  sources [{date?, label, url}]
context_anchors?[] name, category, address, locator, source_note, address_source_url, locator_source_url
demographics      growth {area_label, tiles [4], geographies [{name, series [{label, value,
                          kind: census|estimate|projection}], source_url}], reach_note, source: Rich},
                  age {geographies [{name, under_18_pct, over_65_pct, source_url}]},
                  median_income {geographies [{name, value}], households_note},
                  income_bands {thresholds [100000, 150000, 200000],
                                areas [{name, group: county|place|radius|municipal, households,
                                        population?, median?, counts {threshold: n}, caption}],
                                combined?, notes: Rich},
                  micro_zones? {zones [...as REF-A microzones, study_pdf: Asset]},
                  competition? {markets [names], offices [{name, address, market, service_type,
                                services, source_url, logo: Asset, locator}], scope_note: Rich},
                  methodology: Rich
ownership?        program {min_share, min_sf}, defaults {financed_pct, rate, years, rent_psf,
                  collection_pct, buildout_psf, lease_up_months, appreciation_pct, ti_options [3]},
                  default_property, copy {intro, benefits [4], notes: Rich, methods: Rich}
sources           sections [{heading, body: Rich}]
```

Synthetic example (fragment):

```json
{
  "schema": "carr.market-study.v1",
  "client": { "display_name": "Example Health Group",
              "wordmark": { "line1": "EXAMPLE", "line2": "HEALTH GROUP" } },
  "prepared": { "for": "Dr. Sample Person", "on": "2026-01-15" },
  "market": { "name": "Sample County", "home_title": "Sample County Market Overview",
              "headline": "Sample County's Next Chapter",
              "snapshot": [ { "value": "+10.0%", "label": "County population · 2020 to 2025" } ] },
  "properties": [ { "id": "sample-property-1", "number": 1, "address": "<street address>",
                    "role": "candidate", "status": "For sale",
                    "photo": { "sha256": "0000…", "name": "photo-1.jpg", "type": "image/jpeg" },
                    "facts": [ { "label": "Asking", "value": "$1,000,000" } ],
                    "summary": "Single-story office building.",
                    "sources": [ { "label": "Listing", "url": "https://example.com/listing" } ] } ]
}
```

**Research package.** The research a partner or research job prepares for a new
deal (demographics, developments, competition, ownership defaults, copy) is a
*partial* `carr.market-study.v1` package: the same schema, validated by the same
validator in partial mode, which only waives the record-layer groups. The
builder overlays those groups from the record layer (`client`, `prepared`, the
deal's properties and the slug) and validates the result in full. One format,
one validator; there is no separate research schema.

### Where data and files live

| Thing | Where | Key |
|---|---|---|
| Portal (slug, client, deal, password verifier, lockout) | `ops.client_portal` (exists, S1) | slug |
| Publication (package hash, schema, published by and at) | new `ops.client_portal_publication` | slug + version |
| Session | new `ops.client_portal_session` (session digest, slug, expiry); one row per slug signed in | digest |
| Tour List | new `ops.client_portal_selection` (selected ids, notes by property, version) | slug |
| Portal materials | `ops.client_portal_material` (exists), plus nullable `object_sha256`, `name`, `content_type`, `byte_size` | id |
| Package JSON | R2 `carr-documents` `client-portal/<slug>/package/<sha256>.json` | content hash |
| Photos, PDFs, logos, GeoJSON | R2 `carr-documents` `client-portal/<slug>/files/<sha256>` | content hash |
| Research package (partial package) for a new deal | R2 `carr-documents` `client-portal/<slug>/research/<sha256>.json` | content hash |

R2 doctrine: client documents are the bucket's only tenant, keys are structured
by record, and deletion goes through record-layer verbs, never a raw bucket call.

### How share.doctorcre.com/<slug> serves the portal

1. `GET /<slug>` redirects to `/<slug>/`. `GET /` serves the neutral "Shared
   Presentations" page that LEG-2 serves today.
2. Without a valid session, `GET /<slug>/` returns 401 with the sign-in page
   branded from the portal's publication (client name and wordmark, which the
   sign-in pages show publicly today). An unknown slug returns 404. Slug
   existence is not secret (D9): the client receives the URL, and the branded
   page and the lockout message both name a real portal, so nothing tries to hide it.
3. `POST /<slug>/` with the password calls the verify function. Success inserts a
   session digest and sets a cookie named per portal, `__Host-portal-<slug>`
   (HttpOnly, Secure, SameSite=Lax, `Path=/` as `__Host-` requires, 7 days,
   matching today). Because each portal has its own cookie, signing in to one
   portal never ends another portal's session in the same browser (a partner can
   keep two clients' portals open before calls). Failure returns 401 with
   "incorrect password" or, when locked, "too many attempts; try again after
   <time>" (D9).
4. With a valid session for that slug, the adapter serves the template shell; the
   template loads `/<slug>/package.json` and `/<slug>/files/<sha256>`, both
   released only for that session's slug. A session for slug A never releases
   slug B (AC3).
5. `GET` and `PUT /<slug>/api/selection` are the Tour List (same contract as
   today: version, compare-and-swap, Origin and CSRF on writes, 409 on conflict).
   `GET /<slug>/auth/status` and `POST /<slug>/auth/logout` serve Sign Out.
6. `GET /<slug>/materials` lists shared portal materials and
   `/<slug>/materials/<id>/<name>` releases one file.
7. Every response carries private no-store, nosniff, noindex and same-origin
   referrer headers. The template runs under a CSP that allows only same-origin
   scripts, styles, fonts and images, plus the vector tile host when the package
   uses one.

### How later deliverables and documents get added

- A partner calls `share-portal-material` with the deal, a kind (document, tour,
  report), a title and a file or text. The verb stores the file in R2 by hash and
  inserts a material row with `shared_at`. The portal's Documents view lists it.
- `withdraw-portal-material` sets `withdrawn_at`; the file stops being released
  at once. The R2 object stays until a record-layer retention verb removes it.
- A revised market study is `publish-market-study` again: a new publication row,
  the newest is served, earlier ones stay for audit.
- The app's deal card gets "Share to portal" and "Publish market study" actions
  (app slices), using these verbs.

## Acceptance check (written first)

The check passes when a market study rebuilt by the generator from REF-A's and
REF-V's own data matches the live sites item for item against the inventory
above: every one of the 456 rows reports PASS or a declared allowed difference,
and none reports FAIL.

### How the comparison runs

One command, the same for a partner at the Studio and for CI:
`./run.sh portal-parity --site <REF-A|REF-V> --stage <0|1|2> --reference <url> --candidate <url> --probes <file> [--rows <ids>]`.
It drives headless Chromium (Playwright) and writes one result line per inventory
ID. `--rows` takes IDs and ranges (for example `--rows A-T40..A-T56,A-S16..A-S25`)
so each template slice runs only its own rows; a full run omits it.

**Stage 0, source fidelity and the live gate baseline (slice S6, before anything
is built against the copies).** A partner supplies each site password at run time
through the terminal prompt; it is never stored, logged or committed. The harness:

- signs in to the live site, requests every manifest path, and compares the
  SHA-256 of each released body with the matching file in the read-only source
  copy. Pass: every hash matches. This proves the copies are what clients see,
  because the content keys cannot be read back. The successful sign-in also proves
  the password the partner will register in the migration is the live one.
- runs every gate probe that needs no write and no induced fault against the live
  site and records the observed status, headers and redirect per gate row: the
  redirects, the sign-in page, a wrong password, every 404, 405, 403, 413, 415 and
  400 path, HEAD without a session, and the Tour List API's read-side errors. This
  recorded baseline is the reference for those gate rows. A wrong-password probe
  counts toward nothing today (no lockout exists on the live sites) and the run
  sends fewer than five.

**Stage 1, extraction.** The reference extractor turns each source copy into a
data package and its files. Pass: both packages validate against
`carr.market-study.v1`, and every row marked D or T+D in the inventory maps to a
package field (the probe file lists the field path per row).

**Stage 2, item parity.** The reference is the reference sites' own Worker modules
(the REF-V module and the REF-A origin module, copied from the read-only sources
into a scratch directory), run under Miniflare with a test content key and test
password over the source files re-packed with that key. This is the real gate
code, not a mimic, and Stage 0 proved the files equal what is live. The candidate
is the template plus the extracted package served by `portal-web.js` under
`wrangler dev` with local R2 and a disposable Postgres. Both run the same probe
file. Each inventory row has exactly one probe of one of these kinds:

| Probe kind | Used for | What is compared |
|---|---|---|
| `dom` | sections | Presence, heading text, visible text (whitespace-normalised) and a screenshot at 1440, 820 and 390 px wide with map canvases masked; pixel difference at most 0.5% |
| `interaction` | tools | A scripted action (click, hover, focus, type, drag, select, slider) and the resulting DOM state, focus target and any request made |
| `link` | links | `href`, `target`, `rel`, visible label, and the in-page destination or new-tab behaviour; external URLs are compared, not fetched |
| `media` | media assets: photos, PDFs, fonts, logos, SVG, GeoJSON | SHA-256 of the released bytes and the `Content-Type` |
| `replaced` | code and data files the template and package replace (AD6) | Not compared as bytes. The row passes when Stage 1 mapped every field it held and every `dom`, `interaction` and `link` row it feeds passes |
| `http` | gate rows | Status, `Allow` and other headers, redirect target and cookie attributes for each scripted request, against the Stage 0 live baseline; fault paths that cannot be induced on a live site (unconfigured secrets, storage down, decrypt failure, upstream failure) are compared against the reference modules under Miniflare |

**Result.** The harness prints, per site, the inventory count, PASS, ALLOWED
(with the allowed-difference ID) and FAIL. Pass for the release is: REF-A 281
rows and REF-V 175 rows accounted for, FAIL = 0, and every ALLOWED cites a row
of the table below. A slice passes when FAIL = 0 on its own `--rows` set. Results
with no client values (IDs, verdicts, hashes) are posted to the Work Request; the
run's screenshots stay on the Studio.

**CI.** Client data never reaches public CI. CI runs the harness against the
synthetic fixture rendered by the same template, which proves the harness and the
probe kinds. The Stage 2 runs against REF-A and REF-V happen at the Studio
before each template release.

### Declared allowed differences

| ID | Difference | Rows affected | Why |
|---|---|---|---|
| AD1 | MapLibre is self-hosted at one pinned version instead of two CDN versions | A-T05, A-A38, A-A39, V-A35, V-A36 | Map doctrine: one renderer; reports-web self-hosts |
| AD2 | Per-portal cookie `__Host-portal-<slug>`, server-side session, bcrypt verifier, five-failure lockout with a retry message; error bodies use the portal's wording while status codes and `Allow` headers stay the same; existing browser sessions sign in once more | A-G04, A-G05, A-G11, V-G03, V-G04, V-G08, and the body text of A-G14 to A-G34 and V-G10 to V-G24 | S1 verifier and D8, D9 |
| AD3 | The five REF-A redirect pages become 302 redirects to the same `#view` | A-L95 to A-L99, A-A06 to A-A10 | Same destination, no HTML file per view |
| AD4 | Unreferenced files and build inputs are not served | V-A19 to V-A22, V-A33, V-A34, A-A05 | Nothing links to them |
| AD5 | REF-V gains Sign Out | V-S02 | Template standard (REF-A has it) |
| AD6 | Code and data files are replaced by the template and the package: different bytes and content-hashed names instead of `?v=` query strings | A-A01 to A-A04, V-A01 to V-A09 | D3, D4; checked by the `replaced` probe |
| AD7 | REF-V's password page uses the template sign-in page with the same client name, wordmark and password field | V-G02, V-G04 | One sign-in design |
| AD8 | The edge proxy and its error paths are gone; the portal is served directly | A-G01, A-G35 to A-G39 | D6: one Worker, no second host |

Any other difference is a FAIL until a partner adds it here with a reason.

## Migration that keeps every address and password working (AC1)

Preconditions:

- **S1 is live.** carr-system #2525 (`register-client-portal`) and app #453 must
  be merged and deployed. S8 and every slice after it wait for that, server first.
- **One deal per site.** `ops.register_client_portal` needs an existing deal whose
  client is not merged, and refuses a second active portal on a deal. Before
  registration, a partner resolves the deal for each of the four sites with
  `get-deal-room` (by client), and where none exists (expected for LEG-1 and LEG-2,
  which predate the deal record) creates one with the existing deal verb, marked
  with the phase the client is actually in. Four sites, four deals, confirmed in
  one desk session.
- **Stage 0 passed** for REF-A and REF-V (S6), so the copies and passwords are
  proven against the live sites.

Steps:

1. **Register each existing portal.** In the same desk session, a partner
   registers REF-A, REF-V, LEG-1 and LEG-2 with `register-client-portal`, using
   each site's current slug and current password (the client name, rule ac0a6579).
   The verb is partner-only and the passwords cannot be read back from the current
   host. For LEG-1 and LEG-2, the partner signs in to the live site with the same
   password first, which proves it is the live one.
2. **Publish the two reference packages** from Stage 1, and upload LEG-1 and LEG-2
   as legacy bundles: their plaintext files served unchanged behind the same portal
   gate, after a Stage 0 hash check of each file against the live site. LEG-2's
   top-level `/recovery/` PDF paths become aliases inside the LEG-2 portal.
3. **Copy each Tour List.** Preferred: export each D1 row from the Sites projects,
   including REF-V's retired-listing entries and legacy notes column. Fallback: a
   partner signs in and the harness reads `GET /api/selection`, which omits the
   retired entries no viewer sees today. Either way, the imported row keeps the
   version number, so a later save conflicts correctly.
4. **Rehearse on staging.** Point a staging host at the Worker, run Stage 2 for
   REF-A and REF-V and an `http` probe set for every slug of LEG-1 and LEG-2
   (address resolves, password opens, each file releases).
5. **Cut over.** Freeze Tour List saves on the old host (read-only flag in the
   share Worker), re-copy any Tour List whose version moved since step 3, move the
   `share.doctorcre.com` custom domain from the Sites project to the carr-mcp
   Worker route, and run the smoke set: every slug, its password, one file each,
   one Tour List read. Rollback is re-attaching the domain to the Sites project,
   which stays deployed untouched for 14 days.
6. **Retire** the two Sites projects after 14 days with no rollback, as a removal,
   and delete the reference extractor in the same removal.

What changes for clients: nothing in the address or password. Each client signs
in once more, because the old session cookie is not carried over (AD2).

## Slices

Each slice fits one session or less; none is larger than medium. Order is
smallest first within dependency order. Every job brief carries the Card line
shown, and states the rule "never put client data in git; use the synthetic
fixture". A template slice passes when `./run.sh portal-parity --stage 2 --rows
<its rows>` reports FAIL = 0 for both sites where the rows exist.

| Slice | Size | Depends on | Card |
|---|---|---|---|
| S3 Glossary terms into the doctrine store | tiny | none | `Card: WR227.S3.J1` |
| S4 Data package schema, partial mode and synthetic fixture | small | none | `Card: WR227.S4.J1` |
| S5 Parity harness, probe file and `--rows` | medium | S4 | `Card: WR227.S5.J1` |
| S6 Stage 0: source fidelity and live gate baseline | small | S5 | `Card: WR227.S6.J1` |
| S7 Reference extractor | medium | S4, S6 | `Card: WR227.S7.J1` |
| S8 Record-layer storage and isolation test | medium | S4, #2525 merged | `Card: WR227.S8.J1` |
| S9 Portal adapter on staging (all gate rows) | medium | S8 | `Card: WR227.S9.J1` |
| S10 Template: shell, router, Home and Sources | medium | S7, S9 | `Card: WR227.S10.J1` |
| S11 Template: Purchases | medium | S10 | `Card: WR227.S11.J1` |
| S12 Template: Demographics core | medium | S10 | `Card: WR227.S12.J1` |
| S13 Template: Tour List UI and Sign Out | small | S10 | `Card: WR227.S13.J1` |
| S14 Template: ownership modeler | medium | S11 | `Card: WR227.S14.J1` |
| S15 Template: micro-zones and competitor directory | medium | S12 | `Card: WR227.S15.J1` |
| S16 Template: master-plan overlay and vector basemap | small | S10 | `Card: WR227.S16.J1` |
| S17 Portal materials and Documents view | medium | S9 | `Card: WR227.S17.J1` |
| S18 Roadmap view from the deal phase (AC2) | small | S9 | `Card: WR227.S18.J1` |
| S19 The two explainers (AC4) | small | S18 | `Card: WR227.S19.J1` |
| S20 Package builder from the deal record, and app actions | medium | S9, S4 | `Card: WR227.S20.J1` |
| S21 Migration rehearsal on staging | medium | S11 to S17 | `Card: WR227.S21.J1` |
| S22 Cutover, retirement and extractor deletion | small | S21 | `Card: WR227.S22.J1` |

### Template row sets

Every S, T, L and A row belongs to exactly one template slice, and every G row to
S9. After S13 all 175 REF-V rows are covered; after S16 all 281 REF-A rows are.

| Slice | REF-V rows | REF-A rows |
|---|---|---|
| S9 | V-G01..V-G24 | A-G01..A-G39, A-L95..A-L99, A-A05..A-A10 |
| S10 | V-S01..V-S10, V-S25..V-S27, V-S30..V-S32, V-T01..V-T09, V-T13, V-T17, V-T18, V-L01..V-L13, V-L20..V-L22, V-L29, V-L33..V-L55, V-A01..V-A03, V-A06, V-A10..V-A13, V-A26..V-A36 | A-S01..A-S10, A-S35..A-S43, A-S45, A-S47, A-T01..A-T05, A-T07, A-T10, A-T11, A-T13, A-T16, A-T17, A-L01..A-L14, A-L18..A-L28, A-L30..A-L45, A-L76..A-L79, A-A01..A-A04, A-A11..A-A14, A-A23..A-A26, A-A38, A-A39 |
| S11 | V-S11..V-S16, V-S28, V-S29, V-T10..V-T12, V-T14..V-T16, V-L14, V-L23, V-L30..V-L32, V-A04, V-A05, V-A14..V-A25 | A-S11..A-S15, A-S44, A-T09, A-T12, A-T14, A-T15, A-L15, A-L29, A-A15..A-A22, A-A34 |
| S12 | V-S17..V-S23, V-T19..V-T22, V-L15..V-L19, V-L24..V-L28, V-A07, V-A08 | A-S27..A-S31, A-S34, A-T18..A-T21, A-L16, A-L46..A-L55 |
| S13 | V-S24, V-T23..V-T28, V-A09 | A-S26, A-T32..A-T39 |
| S14 | none | A-S16..A-S25, A-T40..A-T56, A-L80..A-L94 |
| S15 | none | A-S32, A-S33, A-S46, A-T22..A-T31, A-L17, A-L56..A-L75, A-A27..A-A33, A-A35..A-A37 |
| S16 | none | A-T06, A-T08, A-A40 |

### Slice details

**S3. Glossary terms into the doctrine store.** Write the nine terms above with
`write-doctrine-section`. Acceptance: each term is returned by `search-doctrine`
with this wording. Check: `search-doctrine` for each term after the write.

**S4. Data package schema, partial mode and synthetic fixture.** Add
`client-portal/market-study.v1.schema.json`, a partial-mode validation that waives
only the record-layer groups, and a synthetic fixture that turns on every optional
module. Acceptance: the fixture validates; a partial fixture validates in partial
mode and fails in full mode; ten named invalid fixtures (missing required group,
raw HTML in rich text, non-https URL, unknown role, bad asset hash, and so on) are
refused with the field path. Check: `node --test` over the schema tests.

**S5. Parity harness, probe file and `--rows`.** Add the harness behind
`./run.sh portal-parity` with stages 0 to 2, the six probe kinds and `--rows`, and
a probe file with one probe per inventory ID (456) holding selectors, field paths
and expected kinds but no client values. Acceptance: run against the synthetic
fixture served twice, it reports PASS for every probe; one deliberate DOM change
reports exactly that row as FAIL; `--rows` runs only the named rows; the probe
file's IDs equal this document's inventory IDs. Check: harness self-test in CI.

**S6. Stage 0: source fidelity and the live gate baseline.** A partner runs Stage 0
for REF-A and REF-V, typing each password at the prompt. Acceptance: every
released file hashes equal to the source copy; the live gate baseline is recorded
for every gate row Stage 0 can reach without writes or induced faults. Check: the
harness report (IDs, verdicts, hashes only) posted to the Work Request.

**S7. Reference extractor.** A one-off migration tool that reads a site folder
(today's data files plus the hard-coded app.js values the inventory marks) and
writes a package and files to a private directory outside the repo. Acceptance:
both packages validate; every D and T+D row maps to a field; tests use only
synthetic copies of the data-file shapes. Check: `node --test` and a Studio run
that prints only counts.

**S8. Record-layer storage and isolation test.** Waits for #2525. Migration for
publication, per-slug session and selection tables and the material file columns;
functions for verify (returning `denied` or `locked`), session lookup, package and
file release, Tour List read and compare-and-swap; verbs `publish-market-study`
(full validation) and `clear-portal-lockout`. Acceptance: AC3, an automated
Postgres test that one portal's session never returns another portal's package,
file, Tour List or material, and never any internal note; lockout returns its
reason; passwords never reach `tool_call` or `event`. Check: Postgres test suite
plus the grant-contract test.

**S9. Portal adapter on staging.** `portal-web.js` with the routes above, wired to
S8's functions, on a staging host, serving a minimal template shell. Acceptance:
Stage 2 FAIL = 0 on every G row of both sites and on the REF-A redirect rows;
Miniflare tests for each row, including per-portal cookies (signing in to one
portal keeps another's session) and the lockout message. Check: Worker tests and
`portal-parity --rows` for the S9 set on staging.

**S10 to S16. Template slices.** Each builds its part of the template in
doctorcre-app under `/portal/` and passes Stage 2 on its row set above. S10 also
self-hosts MapLibre and the `local-layers` basemap. S13 completes REF-V (175 rows,
FAIL = 0). S16 records a provider rights receipt for the vector tile host or swaps
the basemap, and completes REF-A (281 rows, FAIL = 0). Check for each: the Studio
parity run on its rows, plus the synthetic fixture run in CI.

**S17. Portal materials and Documents view.** Verbs `share-portal-material` and
`withdraw-portal-material`, the file release route and the Documents view.
Acceptance: a shared file appears and releases; a withdrawn file stops releasing at
once; another portal cannot list or fetch it. Check: Postgres and Worker tests and
a staging smoke.

**S18. Roadmap view from the deal phase (AC2).** Render the roadmap that
`ops.read_client_portal` already returns: every phase, the current one marked from
the deal record. Acceptance: changing the deal phase changes the marked phase with
no text entry. Check: Postgres test plus a template test.

**S19. The two explainers (AC4).** How the transaction goes, and why CARR, as
portal sections. Acceptance: both pass the client-facing writing lint and an
independent fresh-eyes review before any client sees them. Check: lint output and
the reviewer's verdict on the PR.

**S20. Package builder from the deal record, and app actions.** Overlay the
record-layer groups onto a validated research package; deal-card "Publish market
study" and "Share to portal" actions and a Tour List readout. Acceptance: a new
synthetic deal goes from record plus research package to a live staging portal
with no hand-written HTML; the partner sees the client's Tour List on the deal
card. Measure, per decision d6c453ff: hours and model spend per new site before
and after. Check: end-to-end staging test and the measurement posted to the Work
Request.

**S21. Migration rehearsal on staging.** The preconditions and steps 1 to 4 of the
migration, with the partner desk session for deals and registration. Acceptance:
four deals resolved or created; four portals registered; full Stage 2 passes on
staging for both sites; every LEG-1 and LEG-2 address, password and file works on
staging; Tour List rows match the source rows. Check: the harness report posted to
the Work Request.

**S22. Cutover, retirement and extractor deletion.** Step 5 and, after 14 days,
step 6. Acceptance: every existing address opens with its existing password on the
production domain; the smoke set passes; rollback was rehearsed once on staging;
the extractor is deleted. Check: smoke report on the Work Request, and the removal
PR.

## Not verified in this slice

- That the deployed ciphertext equals the read-only source copies. The content
  keys are not readable; Stage 0 in S6 proves it before anything is built.
- How the `share.doctorcre.com` custom domain is wired in DNS today (Sites-managed
  or a record in the Cloudflare zone). S22 checks it before cutover.
- Whether the Sites projects allow a D1 export. S21 tries it; the fallback is
  stated in migration step 3.
- Runtime behaviour of the live sites. This inventory comes from reading the code
  and data, not from clicking through the signed-in sites; S6 records the live
  gate behaviour.
- Which deals LEG-1 and LEG-2 belong to. S21 resolves or creates them.
- A provider rights receipt for the vector tile host used by REF-A. S16 records
  one or swaps the basemap.
- LEG-1 and LEG-2 internals beyond their routes and gates; they move unchanged.
