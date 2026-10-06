// Read projection for W7. Configuration/capacity is never authentication evidence.
// Existing resource observations do not contain authenticated capability checks,
// currency-scoped billing observations, or a tailnet device census. Preserve those
// gaps explicitly rather than minting connected states, dollar totals or devices.
export const CONNECTION_PROVIDERS = Object.freeze([
  { id:'claude', name:'Claude', manage_url:'https://claude.ai/settings/usage' },
  { id:'codex', name:'Codex', manage_url:'https://chatgpt.com/codex' },
  { id:'grok', name:'Grok', manage_url:'https://console.x.ai/' },
  { id:'jev', name:'Jev', manage_url:'https://console.typesafe.ai/' },
  { id:'tailscale', name:'Tailscale', manage_url:'https://login.tailscale.com/admin/machines' },
  { id:'neon', name:'Neon', manage_url:'https://console.neon.tech/' },
  { id:'github', name:'GitHub', manage_url:'https://github.com/settings/billing' },
  { id:'cloudflare', name:'Cloudflare', manage_url:'https://dash.cloudflare.com/' },
  { id:'local_compute', name:'Local compute', manage_url:'/control-room?tab=system-map' },
  { id:'model_route', name:'Model tools', manage_url:'/control-room?tab=agents' },
]);

export function connectionsProjection(dashboard) {
  return { ok:true, schema:'doctorcre-connections.v1', generated_at:dashboard.generated_at,
    providers:CONNECTION_PROVIDERS.map(provider => {
      const resource = dashboard.providers.find(row => row.provider === provider.id);
      return { ...provider, status:'unknown', checked_at:null,
        reason:'authenticated_capability_observation_absent', spend:null,
        spend_reason:'currency_scoped_billing_observation_absent',
        resource:resource ? { state:resource.state, observed_at:resource.observed_at || null } : null };
    }),
    devices:{ state:'unknown', observed_at:null, items:[], reason:'tailnet_device_observation_absent' } };
}
