import { briefPreferences } from './brief-preferences.js';
import { OWNER_FILTERS } from './owner-filters.js';

export const briefOwnerLabel = row => OWNER_FILTERS.find(filter => filter.value !== 'all' && filter.value === row.owner_partner)?.label || '';
export const filterBriefOwners = (rows, owner = 'all') => rows.filter(row => owner === 'all' || row.owner_partner == null || row.owner_partner === owner);

export function mountBriefOwnerFilter(root, host, onChange) {
  let storage; try { storage = root.defaultView.localStorage; } catch {}
  const current = () => root.documentElement.dataset.briefOwner || 'all';
  host.className = 'chip-bar';
  host.setAttribute('role', 'group');
  host.setAttribute('aria-label', 'Brief owners');
  host.innerHTML = OWNER_FILTERS.map(({value,label}) => `<button class="chip chip-plain" type="button" data-brief-owner="${value}" aria-pressed="false">${label}</button>`).join('');
  const paint = () => { for (const button of host.children) button.setAttribute('aria-pressed', String(button.dataset.briefOwner === current())); };
  const changed = () => { paint(); onChange(current()); };
  const select = owner => {
    root.documentElement.dataset.briefOwner = OWNER_FILTERS.some(filter => filter.value === owner) ? owner : 'all';
    root.dispatchEvent(new root.defaultView.Event('doctorcre:brief-owner'));
  };
  const setOwner = owner => {
    const viewer = root.documentElement.dataset.briefViewer;
    if (viewer) briefPreferences(storage, viewer).save({owner});
    select(owner);
  };
  const setViewer = viewer => {
    if (root.documentElement.dataset.briefViewer === viewer) return;
    root.documentElement.dataset.briefViewer = viewer;
    select(briefPreferences(storage, viewer).value.owner);
  };
  host.onclick = event => {
    const button = event.target.closest('[data-brief-owner]');
    if (!button) return;
    setOwner(button.dataset.briefOwner);
  };
  root.addEventListener('doctorcre:brief-owner', changed);
  paint();
  return { current, setOwner, setViewer, dispose() { root.removeEventListener('doctorcre:brief-owner', changed); } };
}
