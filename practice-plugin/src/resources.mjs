import { plannerHtml } from '../.build/planner-resource.mjs';
import { PLANNER_URI } from './planner.mjs';
export const RESOURCES = [
  { uri: PLANNER_URI, name: 'Practice space planner', mimeType: 'text/html;profile=mcp-app' },
];
export function readResource(uri) {
  if (!RESOURCES.some(r => r.uri === uri)) return null;
  return { contents: [{ uri, mimeType: 'text/html;profile=mcp-app', text: plannerHtml, _meta: {
    ui: { csp: { connectDomains: [], resourceDomains: [] }, prefersBorder: false },
    'openai/ui': { preferredDisplayMode: 'fullscreen', availableDisplayModes: ['inline', 'fullscreen'] },
  } }] };
}
