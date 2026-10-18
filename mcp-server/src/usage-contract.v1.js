export const USAGE_SCHEMA = 'doctorcre-usage.v1';
export const RETENTION_SECONDS = 180 * 24 * 60 * 60;
export const PARTNERS = Object.freeze(['joe', 'dell']);
export const EVENT_NAMES = Object.freeze(['screen_viewed', 'primary_action_used', 'chat_opened', 'chat_sent', 'search_used', 'error_shown']);
export const SCREENS = Object.freeze({
  home: ['/', 'Home'], deals: ['/deals', 'Deals'], leads: ['/leads', 'Leads'], clients: ['/clients', 'Clients'],
  vendors: ['/vendors', 'Vendors'], calendar: ['/calendar', 'Calendar'], tours: ['/tours', 'Tours'],
  progress: ['/control-room/progress', 'Progress'], progress_work: ['/control-room/progress/work', 'Progress work'],
  control_room: ['/control-room', 'Control Room'], automations: ['/control-room/automations', 'Automations'],
  ideas: ['/ideas-events', 'Ideas and events'], work_requests: ['/work-requests', 'Work requests'],
  all_work: ['/all-work', 'All work'], search: ['/search', 'Search'], status: ['/status', 'Status'],
  incidents: ['/incidents', 'Incidents'], updates: ['/updates', 'Updates'], chats: ['/doc-chats', 'Doc Chats'],
  chat_work: ['/doc-chats/work', 'Chat work'], relationships: ['/relationships', 'Relationships'],
  doc_activity: ['/doc-activity', 'Doc Activity'], invoices: ['/invoices', 'Invoices'], leases: ['/leases', 'Leases'],
});
export function usageScreen(pathname) {
  if (typeof pathname !== 'string' || /[?#]/.test(pathname)) return null;
  if (pathname.startsWith('/control-room/progress/board/')) return 'progress';
  if (pathname === '/tours/day.html' || pathname === '/tours/day') return 'tours';
  return Object.keys(SCREENS).find(key => SCREENS[key][0] === pathname) || null;
}
export function validUsageEvent(event) {
  const fields = ['event_name', 'screen', 'partner', 'release_sha', 'timestamp'];
  return !!event && Object.getPrototypeOf(event) === Object.prototype && Object.keys(event).length === fields.length &&
    fields.every(key => Object.hasOwn(event, key) && typeof event[key] === 'string') &&
    EVENT_NAMES.includes(event.event_name) && Object.hasOwn(SCREENS, event.screen) && PARTNERS.includes(event.partner) &&
    /^[a-f0-9]{40}$/.test(event.release_sha) && /^\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d\.\d{3}Z$/.test(event.timestamp) &&
    Number.isFinite(Date.parse(event.timestamp)) && new Date(event.timestamp).toISOString() === event.timestamp;
}
export const FEATURES = Object.freeze([
  ...Object.entries(SCREENS).flatMap(([screen, [, label]]) => [
    { id: `${screen}:view`, label, event_name: 'screen_viewed', screen },
    { id: `${screen}:action`, label: `${label} · Primary action`, event_name: 'primary_action_used', screen },
    { id: `${screen}:error`, label: `${label} · Error shown`, event_name: 'error_shown', screen },
  ]),
  { id: 'doc:opened', label: 'Dr. CRE · Opened', event_name: 'chat_opened' },
  { id: 'doc:sent', label: 'Dr. CRE · Sent', event_name: 'chat_sent' },
  { id: 'search:used', label: 'Search · Used', event_name: 'search_used' },
]);
