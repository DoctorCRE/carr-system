# Spec: attribution for agent-originated writes

**Status:** proposed, 2026-08-02. **Premise partly WRONG — read the box below first.**
**Type:** record-layer change. Not implementable through the MCP tool surface.
**Evidence:** the 2026-08-02 system audit, Finding 7. That audit file no longer exists;
its durable findings are loops #124–128, and this spec's own loop is **#124**.

> **CORRECTION, 2026-08-02 (added when this file was rescued into carr-system).**
> This spec was written by a cloud session that could not see the record layer's source
> and inferred its design from tool responses. Its central recommendation — "derive actor
> server-side from connection identity, do not add an actor parameter" — describes what
> `mcp-server/src/identity.js` **already does**: a two-entry Google OIDC allowlist maps a
> verified email to `joe` or `dell`, verbs never accept an actor argument, and `mcp.js:35`
> enforces a `humanOnly` gate against a `human` flag already present on the props. Actor
> is not forgeable today.
>
> The real remaining gap is narrower than this document claims: there is no
> `on_behalf_of` field, so an agent writing under Joe's credential is indistinguishable
> from Joe typing. Read what follows as background on *why that distinction matters*, not
> as an accurate account of what is missing. Do not implement the actor-derivation
> sections — they are already built.
>
> Originally filed in `jbookout/carr-automation`, an empty scaffold repo the cloud session
> mistook for the CARR repo; deleted 2026-08-02. Code and its specs live in `carr-system`
> and nowhere else.

---

## Problem

Every write lands as a human. The `update-deal` call made during this audit was recorded as:

```json
{"entry_kind":"event","occurred_at":"2026-08-02T13:36:52.974Z",
 "actor":"joe","verb":"update-deal","summary":"notes_path"}
```

`actor: joe`. An agent write is byte-identical to Joe changing the field by hand.

The write verbs take no `actor` parameter — `update-deal` accepts only `idempotency_key`,
`deal`, `base_version`, `fields`. The value is assigned server-side, so this cannot be
fixed from the client.

## Why it matters

**The operating rules depend on the distinction.** Two-writer discipline on shared files
and the standing gate — *"Claude drafts, Joe sends"* — both assume you can tell who wrote
something. Right now you cannot.

**It is already the audit's most corrosive finding, in a different form.** The import
stamped `actor: dell` on rows plainly authored by Joe and Claude, including one whose body
reads `[stamp: Joe's brain, Claude Code, 2026-07-22]`. That is why `writes_by_dell_24h` is
not a usable signal and why "who decided this?" is unanswerable for anything pre-freeze.

**Every write from here compounds it.** With seven verbs now allowlisted, the queued
cleanup — `notes_path` across 40 deals, clearing 29 boilerplate actions, migrating the
`DECISIONS.md` gates into loops — would produce hundreds of events that look like Joe did
them by hand, in one sitting, on a Sunday. That is a worse artifact than the `dell` rows,
because it is denser and more plausible.

**This is why it goes first.** It is cheap now and expensive to unwind later.

---

## Design

### The agent must not be able to set its own actor

Do **not** add an `actor` parameter to the write verbs. An attestation the writer can
forge is not an attestation. The value must be derived server-side from the connection's
identity, exactly as it is today — the change is what it derives *to*.

### Two fields, not one

| Field | Meaning | Example |
|---|---|---|
| `actor` | Who performed the write | `claude` |
| `on_behalf_of` | Which human's session it originated in | `joe` |

A write Joe makes directly: `actor: joe`, `on_behalf_of: null`.
A write an agent makes in Joe's session: `actor: claude`, `on_behalf_of: joe`.

Keeping `on_behalf_of` matters for the "whose book is this" question — an agent write in
Joe's session is still Joe's deal, still counts toward his activity, still respects
two-writer discipline against Dell. Collapsing to `actor: claude` alone would lose that.

### Consumers to update

- `integrity-digest` — `writes_by_dell_24h` becomes meaningful again; consider adding
  `writes_by_agent_24h` alongside it
- `catch-me-up` — render the distinction in the timeline, so a reader sees at a glance
  which rows a human authored
- The dossier export — `analysis` rows already print `*2026-07-24 · joe*`; that line
  should say `claude (for joe)` where true

### Migration

Only one row is known to be mis-stamped by an agent: the `update-deal` event of
2026-08-02T13:36:52.974Z on the Ferris deal, made during this audit. It is
`actor: joe` and should be `actor: claude, on_behalf_of: joe`.

**This is a correction, not a backfill.** The no-backfill policy in `DECISIONS.md` covers
reconstructing values nobody recorded. This value was recorded and is wrong, and there is
exactly one of it.

The pre-freeze `dell`-stamped rows are a different matter and stay as they are — there is
no record of who actually authored them, so restamping would be invention. Leave them and
treat pre-freeze attribution as unknown.

---

## Acceptance tests

| # | Given | Expect |
|---|---|---|
| 1 | An agent calls `update-deal` in Joe's session | `actor: claude`, `on_behalf_of: joe` |
| 2 | Joe edits the same field directly | `actor: joe`, `on_behalf_of: null` |
| 3 | An agent attempts to pass `actor` in the payload | Rejected or ignored — never honored |
| 4 | `integrity-digest` after an agent-only day | `writes_by_dell_24h: 0` and a non-zero agent count, rather than inflated human counts |
| 5 | The 2026-08-02 Ferris `update-deal` event | Corrected to `actor: claude` |

---

## Where this has to be done

Not reachable from this session. `jbookout/carr-automation` is the only repo on the
account, and it contains no record-layer source. The service is known to exist — the
dossier header references `run.sh export --only dossier`, and there are database views
(`v_pool`) — but it is hosted somewhere this session cannot see.

**Open question for Joe:** where does the record layer live? If it is a repo under another
owner it can be attached with `add_repo`. If it is local on your machine, a desktop
session can do both this and the read-path traversal directly.
