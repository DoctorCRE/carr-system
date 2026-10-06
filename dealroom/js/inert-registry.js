/**
 * Shared inert/aria-hidden reference-count registry (V5-J101 round 3).
 *
 * Two independent, uncoordinated modal panels can both need the SAME
 * background region held inert at the same time — on business.html, the
 * record panel (workspace-business.js) and Doc (doc-panel.js) both reach
 * <header data-panel-background> (and its siblings) when both happen to be
 * modal together.
 *
 * A snapshot/restore approach — remember what a region looked like before I
 * touched it, put that back when I am done with it — silently assumes only
 * ONE owner ever exists at a time, in whichever order the two panels showed
 * up. Whichever one claims or releases SECOND can stomp the other's still-
 * active claim, in either direction: the round-3 review's reported defect
 * was the record panel releasing first and Doc's stale snapshot re-claiming
 * on top of it; the untested mirror case is Doc claiming a region FIRST
 * (nothing else holds it yet) and later releasing it while the record panel
 * has, in the meantime, independently and unconditionally also claimed it —
 * a snapshot/restore release would still force it back to Doc's own
 * pre-claim baseline, discarding the record panel's still-active claim.
 *
 * Reference counting removes the ordering dependency entirely: a region is
 * inert exactly while at least one named owner currently claims it. Its
 * pre-existing state (its own `inert`/`aria-hidden`, possibly already true
 * for a reason neither owner caused) is captured once, the first time ANY
 * owner claims it, and restored only once the LAST owner releases it — no
 * owner's claim or release can ever undo another still-active owner's.
 */

const claims = new Map(); // region -> { owners: Set<ownerId>, priorInert, priorAriaHidden }

/** Add `ownerId` to the set of owners currently wanting `region` inert. Idempotent. */
export function claimInert(region, ownerId) {
  let entry = claims.get(region);
  if (!entry) {
    entry = { owners: new Set(), priorInert: region.inert, priorAriaHidden: region.getAttribute('aria-hidden') };
    claims.set(region, entry);
  }
  entry.owners.add(ownerId);
  if (!region.inert) region.inert = true;
  if (region.getAttribute('aria-hidden') !== 'true') region.setAttribute('aria-hidden', 'true');
}

/**
 * Remove `ownerId` from `region`'s owners. The region is only restored to its
 * pre-claim state once NO owner remains — a still-active other owner's claim
 * is left completely untouched, including the aria-hidden it wants.
 */
export function releaseInert(region, ownerId) {
  const entry = claims.get(region);
  if (!entry || !entry.owners.has(ownerId)) return;
  entry.owners.delete(ownerId);
  if (entry.owners.size > 0) return;
  claims.delete(region);
  if (region.inert !== entry.priorInert) region.inert = entry.priorInert;
  const current = region.getAttribute('aria-hidden');
  if (entry.priorAriaHidden === null) { if (current !== null) region.removeAttribute('aria-hidden'); }
  else if (current !== entry.priorAriaHidden) region.setAttribute('aria-hidden', entry.priorAriaHidden);
}

/** True when some OTHER registered owner (not `ownerId`) currently claims `region`. */
export function isClaimedByOther(region, ownerId) {
  const entry = claims.get(region);
  return Boolean(entry) && [...entry.owners].some((id) => id !== ownerId);
}
