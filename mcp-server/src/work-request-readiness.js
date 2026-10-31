// Readiness is derived from the recorded plan; it grants no completion authority.
export const validSliceChange = change => change && typeof change === 'object' && Object.keys(change).length === 2
  && typeof change.pr_url === 'string' && typeof change.merge_commit_sha === 'string'
  && /^https:\/\/github[.]com\/(DoctorCRE\/carr-system|jbookout\/(doctorcre-app|software-factory))\/pull\/[1-9][0-9]*$/.test(change.pr_url)
  && /^[0-9a-fA-F]{40}$/.test(change.merge_commit_sha);
export function readyToClose(state, slices) {
  if (['confirmed_closed','declined','superseded','failed'].includes(state) || !slices.length) return false;
  return slices.every(slice => slice.outcome === 'retired' ||
    (slice.outcome === 'done' && Array.isArray(slice.change_refs) && slice.change_refs.length > 0 && slice.change_refs.every(validSliceChange)))
    && slices.some(slice => slice.outcome === 'done');
}
