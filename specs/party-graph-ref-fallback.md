# v_party_graph: fall back to the party ref, and resolve merged endpoints

*Handoff spec, 2026-08-02, loop #133. Written by the seat that owns `mcp-server/src/tools.js`,
`pipelines/build-graph-notes.py` and `lib/record_sources.py`. It proposes a DB view change it is
NOT allowed to write — migrations 0060–0065 belong to another seat this cycle — so the SQL below
is a proposal, not an applied change. The Python half of the fix IS applied and is described here
so whoever lands the view knows exactly what to delete afterwards.*

## The defect

`v_party_graph` held 31 edges on 2026-08-02 and the graph pipeline rendered 24. All seven of the
missing edges have a NULL ref on one endpoint, and six of the seven are Joe's own `can_introduce`
edges:

| from | kind | to |
|---|---|---|
| Joe Bookout *(no ref)* | can_introduce | Heather Lavigne V-CPA-036 |
| Joe Bookout *(no ref)* | can_introduce | Josh Dunmore V-CPA-039 |
| Joe Bookout *(no ref)* | can_introduce | Bruce Partridge V-ATT-015 |
| Joe Bookout *(no ref)* | can_introduce | Justin Gale V-CPA-037 |
| Joe Bookout *(no ref)* | can_introduce | Katherine Welby V-CPA-040 |
| Joe Bookout *(no ref)* | can_introduce | Gary Trellis V-CPA-038 |
| Joe Earl Jenkins V-SUP-051 | knows | Dr. James Allen Okafor *(no ref)* |

"Joe can introduce you to X" is the highest-value edge class in the referral engine, and 100% of it
was invisible on the one surface built to show relationships.

Two different causes produce the same NULL, and they want different fixes:

1. **A bare party.** Joe is party `P-1084` and carries no client, lead or vendor row — correctly, he
   is the agent, not his own prospect. `v_party_graph`'s `party_ref` CTE draws only from
   `client.roster_ref`, `vendor.vendor_ref` and `lead.registry_ref`, so a party with no role has no
   ref and cannot be an endpoint. Dell is `P-1083` and is in the same position.
2. **A link still pointing at a tombstone.** `party_link` `4aecf3b0-62ce-40d5-b23e-cf35e67e9514`
   points at `P-0365`, which was merged into `P-0384` (Dr. James Allen Okafor). The survivor carries
   BOTH `C-155` and `L-208`; the loser carries no role at all, hence the NULL. Note that
   `v_party_graph` does not follow `merged_into` on either endpoint.

## Proposed view change

Two edits, independent of one another, both inside `v_party_graph`:

```sql
create or replace view v_party_graph as
with resolved as (
  -- (a) FOLLOW THE MERGE on both ends. An edge written against a party that was
  --     later merged away must speak for the survivor, or the merge is defeated
  --     every time anything reads the graph.
  select pl.id,
         coalesce(f.merged_into, f.id) as from_party,
         coalesce(t.merged_into, t.id) as to_party,
         pl.kind, pl.note, pl.created_at
    from party_link pl
    join party f on f.id = pl.from_party
    join party t on t.id = pl.to_party
),
party_ref as (
  select distinct on (r.party_id) r.party_id, r.ref
    from (
      select c.party_id, c.roster_ref  as ref, 1 as pref from client c
      union all select v.party_id, v.vendor_ref,   2 from vendor v
      union all select l.party_id, l.registry_ref, 3 from lead l
      -- (b) THE PARTY'S OWN P- REF, LAST. pref 4 means a role ref still wins
      --     wherever one exists, so no existing endpoint changes value; a bare
      --     party stops being unaddressable instead of staying invisible.
      union all select p.id,       p.ref,          4 from party p
                 where p.deleted_at is null
    ) r
   order by r.party_id, (r.ref is null), r.pref
)
select fr.ref as from_ref, fp.name as from_name,
       tr.ref as to_ref,   tp.name as to_name,
       e.kind, e.note, e.created_at as linked_at
  from resolved e
  join party fp on fp.id = e.from_party
  join party tp on tp.id = e.to_party
  left join party_ref fr on fr.party_id = e.from_party
  left join party_ref tr on tr.party_id = e.to_party;
```

## What the migration author must check before applying — NOT verified here

`neonctl connection-string` timed out repeatedly on the afternoon of 2026-08-02 (three retries,
60s each) and `carr_exporter` has no grant on `party` or `party_link`, so these five could not be
measured from this seat. They are the whole risk surface; do not apply the view without them.

1. **Merge-chain depth.** `coalesce(merged_into, id)` resolves ONE level. If any party's
   `merged_into` points at a row that is itself merged, this returns a tombstone.
   `select count(*) from party a join party b on b.id = a.merged_into where b.merged_into is not null;`
   must be 0, or the CTE has to become recursive.
2. **Self-links created by resolution.** An edge from a tombstone to its own survivor collapses to
   `from_party = to_party`. `build-graph-notes.py` already drops self-links silently, but
   `who-do-we-know`'s recursive walk should not be asked to hold a self-loop. Count them; consider
   filtering `where e.from_party <> e.to_party` in the view.
3. **Duplicate edges created by resolution.** The Okafor case is exactly this: after resolution,
   `V-SUP-051 knows P-0384` exists twice (once from the live link, once from the tombstone link).
   The graph pipeline dedups with `set()` and `find` shows both. Decide whether the view should
   `distinct` them, and remember `find`'s CONNECTIONS_CAP is a budget those duplicates spend.
4. **Parties with a NULL `party.ref`.** Any such party stays unaddressable; the fallback improves
   nothing for it. Count them so the remaining gap is a known number rather than a surprise.
5. **Downstream readers.** Three, all already merge-aware after this session's changes:
   `mcp-server/src/tools.js` (`find`'s connections block, `who-do-we-know`'s node resolution and
   `WHO_EDGES`), `lib/record_sources.py:load_party_links`, `pipelines/build-graph-notes.py`.
   `WHO_EDGES` filters `from_ref is not null and to_ref is not null`; once (b) lands, that filter
   stops excluding the partner edges and `who-do-we-know "Joe Bookout"` starts returning real
   paths instead of the `unwalkable_edges` report it returns today.

## What was done in Python instead, and why

The view fix is necessary but **not sufficient**, which is why it was not the only work done. Even
with `from_ref = 'P-1084'`, `build-graph-notes.py` would still drop the edge: its `ref_node` map is
built exclusively from vendor, client and lead IDs, so a P- ref names no node and the endpoint stays
unmapped. The load-bearing half of loop #133 is in the pipeline, and it works today with no
migration and no deploy:

- `lib/record_sources.py:load_party_links` no longer filters NULL refs in SQL. It returned 24 of 31
  edges and the consumer had no way to know. A filter in a loader is a filter nobody downstream can
  count.
- `lib/record_sources.py:load_ref_index` is new — every ref the record layer carries, with the
  `merged` flag, so a consumer can identify a NULL endpoint.
- `pipelines/build-graph-notes.py` mints **partner nodes** for Joe and Dell into `Graph/partners/`
  and resolves a NULL endpoint by EXACT full name against the live half of `v_ref_index`, requiring
  the name to map to exactly one live PARTY (not one row — Okafor's two refs are one party, which is
  what makes that seventh edge resolvable). Tombstones are excluded from the index, so a link
  pointing at `P-0365` resolves to the survivor's `C-155`.

Measured after the change: 31 edges in, 30 rendered (the Okafor duplicate dedups), 0 unmapped,
2 partner nodes, 7 endpoints recovered by name. Byte-diffed against the vault's existing `Graph/`
tree: the ONLY difference is the new `partners/` folder.

**When the view change lands, delete the name-recovery path, not the partner nodes.** The name index
exists solely because the view hands over a NULL instead of a P- ref; a ref is always safer than a
name and the exact-match guard is a mitigation, not a preference. The partner nodes stay either way
— a P- ref still names no node without them.

## Two follow-ups this spec does not cover

- **Palette.** Partner notes carry `#partner` (plus `#owner-joe` / `#owner-dell`).
  `.obsidian/graph.json` has no colour group for `partner`, so the two nodes render in the default
  colour. That is a vault file, not a repo file.
- **Files-mode parity.** Whole-tree equality between `--files` and `--records` is not
  an acceptance criterion: the intro graph is records-only, so vendor notes carrying an edge
  differ between modes. `partners/` adds records-only paths to that set. Any future parity
  check must compare shared surfaces separately from the records-only intro surface.
