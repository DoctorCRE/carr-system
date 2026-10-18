import { z } from 'zod';
import { OpenAIFormSchema, createOpenAIFormContentSchema } from '@openai/mcp-extensions/server';
import { VERTICALS, spaceInput } from './verticals.mjs';
import { callTool } from './tools.mjs';

export const PLANNER_URI = 'ui://practice/space-planner';
// Both entrypoints remount the same app, whose attached context the host returns.
export const PANEL_URI = PLANNER_URI;
export const MARKETS = { mobile_downtown: 'Downtown Mobile', other_market: 'Other market (local requirements unverified)' };
export const GROUPS = {
  dental: { title: 'Dental', types: Object.keys(VERTICALS).filter(k => k.startsWith('dental_')) },
  medical: { title: 'Medical', types: ['medical'] }, veterinary: { title: 'Veterinary', types: ['veterinary'] },
  vision: { title: 'Vision', types: ['vision'] }, chiropractic: { title: 'Chiropractic', types: ['chiropractic'] },
  therapy: { title: 'Therapy', types: ['therapy'] },
};
const shapes = {
  dental: '<path d="M6 3c-4 0-4 6-2 9 1 2 1 5 3 5s1-6 3-6 1 6 3 6 2-3 3-5c2-3 2-9-2-9-2 0-2 1-4 1S8 3 6 3Z"/>',
  medical: '<path d="M7 3h6v4h4v6h-4v4H7v-4H3V7h4Z"/>',
  veterinary: '<circle cx="6" cy="5" r="2"/><circle cx="14" cy="5" r="2"/><path d="M4 14c0-5 12-5 12 0s-4 2-6 2-6 3-6-2Z"/>',
  vision: '<path d="M2 10s3-6 8-6 8 6 8 6-3 6-8 6-8-6-8-6Z"/><circle cx="10" cy="10" r="3"/>',
  chiropractic: '<path d="M10 2v16M6 5h8M6 10h8M6 15h8"/>',
  therapy: '<circle cx="10" cy="4" r="2"/><path d="M4 8h12M10 6v7m0 0-5 5m5-5 5 5"/>',
};
export const icon = group => ({ src: 'data:image/svg+xml;base64,' + btoa(`<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 20 20" fill="none" stroke="currentColor" stroke-width="1.33">${shapes[group] || shapes.medical}</svg>`), mimeType: 'image/svg+xml', sizes: ['any'] });
export const CATALOG = Object.entries(GROUPS).flatMap(([group, row]) => row.types.map(value => ({
  value, group, label: VERTICALS[value].label, icon: icon(group), singleProvider: VERTICALS[value].sizing.kind === 'single_provider_band',
})));
export const plannerInput = spaceInput.extend({ market: z.enum(Object.keys(MARKETS)) });
export const INTAKE_FORM = OpenAIFormSchema.parse({
  type: 'object', properties: {
    practice_type: { type: 'string', title: 'Vertical and subtype', oneOf: CATALOG.map(row => ({ const: row.value,
      title: `${GROUPS[row.group].title}: ${row.label}`, 'x-openai-thumbnail': row.icon })) },
    providers: { type: 'integer', title: 'Simultaneous providers (specialty bands support one)', minimum: 1, maximum: 50, default: 1 },
    operatories: { type: 'integer', title: 'Dental operatories (zero for other verticals)', minimum: 0, maximum: 100, default: 0 },
    exam_rooms: { type: 'integer', title: 'Exam or private treatment rooms (zero for dental)', minimum: 0, maximum: 100, default: 0 },
    market: { type: 'string', title: 'Market context (sizing is not a local code requirement)', oneOf: Object.entries(MARKETS).map(([value, title]) => ({ const: value, title })) },
  }, required: ['practice_type', 'providers', 'operatories', 'exam_rooms', 'market'],
});
export const formContent = createOpenAIFormContentSchema(INTAKE_FORM);
export function calculatePlan(args) {
  const parsed = plannerInput.safeParse(args);
  if (!parsed.success) return errorResult('Use supported selections and bounded counts; no personal or property data.');
  const { market, ...space } = parsed.data;
  const plan = callTool('plan_practice_space', space);
  if (plan.isError) return plan;
  return result({ ...plan.structuredContent, market, market_note: 'Market is context only. SF heuristics do not vary by market; adopted parking, zoning and code remain unverified.' });
}
export function result(structuredContent) { return { content: [{ type: 'text', text: JSON.stringify(structuredContent) }], structuredContent }; }
export function errorResult(error) { return { ...result({ error }), isError: true }; }
const annotations = { readOnlyHint: true, destructiveHint: false, idempotentHint: true, openWorldHint: false };
const empty = { type: 'object', properties: {}, additionalProperties: false };
export const PLANNER_TOOLS = [
  { name: 'open_practice_space_planner', title: 'Practice space planner', description: 'Open an educational fullscreen space planner. Choose a vertical and subtype, counts and market context. No saved practice data or property search.',
    inputSchema: empty, annotations, icons: [icon('medical')], _meta: { ui: { resourceUri: PLANNER_URI, visibility: ['app'] }, 'openai/ui': { entrypoints: [{ type: 'global' }, { type: 'thread' }] } } },
  { name: 'update_practice_space_plan', title: 'Update practice space plan', description: 'Calculate an educational current space plan after the user changes vertical, subtype, counts or market in chat or the app. Returns existing tool SF, rooms, preliminary parking and building checks. Market does not change sizing. No stored data.',
    inputSchema: z.toJSONSchema(plannerInput, { target: 'draft-7' }), annotations, _meta: { ui: { resourceUri: PANEL_URI } } },
  { name: 'intake_practice_space_plan', title: 'Practice planning intake', description: 'Ask for educational practice vertical/subtype and bounded room/provider counts with a native form, then calculate the space plan. Requires OpenAI forms and MCP 2026-07-28 multi-round-trip support. No free-text practice type, personal data or persistence.',
    inputSchema: empty, annotations, _meta: { ui: { resourceUri: PANEL_URI } } },
];
export function callPlanner(name, args = {}) {
  if (name === 'update_practice_space_plan') return calculatePlan(args);
  if (!z.strictObject({}).safeParse(args).success) return errorResult('This entrypoint accepts no fields.');
  if (name === 'intake_practice_space_plan') return errorResult('Native intake requires OpenAI form support and MCP 2026-07-28. Use the planner controls on other hosts.');
  return result({ catalog: CATALOG, groups: GROUPS, markets: MARKETS, plan: null });
}
export function intake(params, capabilities) {
  if (!z.strictObject({}).safeParse(params.arguments ?? {}).success || params.requestState !== undefined) return errorResult('Invalid intake request.');
  const response = params.inputResponses?.practice_intake;
  if (response === undefined) {
    if (!capabilities?.extensions?.['openai/elicitation']?.form) return null;
    // No server state or requestState is needed: each answer is independently validated.
    return { resultType: 'input_required', inputRequests: { practice_intake: { method: 'openai/elicitation/create',
      params: { mode: 'form', message: 'Choose the practice program. Enter counts only; no names, addresses or patient information.', requestedSchema: INTAKE_FORM } } } };
  }
  if (['cancel', 'decline'].includes(response?.action)) return result({ status: response.action, plan: null });
  const parsed = formContent.safeParse(response?.content);
  if (response?.action !== 'accept' || !parsed.success) return errorResult('Invalid form answers. Choose supported options and counts.');
  return calculatePlan(response.content);
}
