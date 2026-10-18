import { App } from '@modelcontextprotocol/ext-apps';
import { OpenAIExtensions } from '@openai/mcp-extensions/app';
import { z } from 'zod';
import { spaceInput, spaceResults, canonical, VERTICALS } from '../src/verticals.mjs';
import { envelopeShape } from '../src/evidence.mjs';

const app = new App({ name: 'Practice space planner', version: '0.2.0' }, { availableDisplayModes: ['inline', 'fullscreen'] });
const extensions = new OpenAIExtensions(app);
const { catalog, groups, markets } = JSON.parse(document.querySelector('#catalog').textContent);
const $ = id => document.getElementById(id);
let current = null, previous = null, revision = 0, contextId;
let pendingPublication, publishing = false;
const localPublications = new Set();
let awaitingPublicationReceipt = false, deferredContextChange = false;
function options(select, rows, selected) {
  select.replaceChildren(...rows.map(([value, label]) => { const item = document.createElement('option'); item.value = value; item.textContent = label; return item; }));
  if (selected) select.value = selected;
}
options($('vertical'), Object.entries(groups).map(([key, group]) => [key, group.title]));
options($('market'), Object.entries(markets));
function subtypeOptions(selected) {
  const rows = catalog.filter(row => row.group === $('vertical').value);
  options($('subtype'), rows.map(row => [row.value, row.label]), selected);
  setCapacity();
}
function setCapacity() {
  const row = catalog.find(row => row.value === $('subtype').value);
  $('rooms-label').textContent = row.group === 'dental' ? 'Operatories' : 'Exam / private treatment rooms';
  $('providers').max = row.singleProvider ? 1 : 50;
  if (row.singleProvider) $('providers').value = 1;
  $('capacity-note').textContent = row.singleProvider ? 'This specialty has a single-provider reference band. It does not scale with room count.' : 'Counts describe the planned program; equipment and room fit still need review.';
}
subtypeOptions();
$('vertical').addEventListener('change', () => subtypeOptions());
$('subtype').addEventListener('change', setCapacity);
function controls(inputs) {
  const row = catalog.find(row => row.value === inputs.practice_type || (inputs.practice_type === 'dental' && row.value === 'dental_gp'));
  if (!row) return;
  $('vertical').value = row.group; subtypeOptions(row.value);
  $('providers').value = inputs.providers;
  $('rooms').value = row.group === 'dental' ? inputs.operatories : inputs.exam_rooms;
  $('market').value = inputs.market || 'other_market';
}
function readControls() {
  const dental = $('vertical').value === 'dental';
  return { practice_type: $('subtype').value, providers: Number($('providers').value), operatories: dental ? Number($('rooms').value) : 0,
    exam_rooms: dental ? 0 : Number($('rooms').value), market: $('market').value,
    ...(current?.inputs_used.rentable_to_usable_factor !== undefined ? { rentable_to_usable_factor: current.inputs_used.rentable_to_usable_factor } : {}) };
}
const contextInput = spaceInput.extend({ market: z.enum(Object.keys(markets)) });
function validInputs(inputs, withMarket = false) {
  if (!(withMarket ? contextInput : spaceInput).safeParse(inputs).success) return false;
  const type = canonical(inputs.practice_type), dental = type.startsWith('dental_');
  return (dental ? inputs.operatories >= 1 && inputs.exam_rooms === 0 : inputs.exam_rooms >= 1 && inputs.operatories === 0) &&
    (VERTICALS[type].sizing.kind !== 'single_provider_band' || inputs.providers === 1);
}
const planSchema = z.strictObject({ ...envelopeShape, inputs_used: spaceInput, results: spaceResults, market: z.enum(Object.keys(markets)), market_note: z.string() });
function validatedPlan(plan) {
  const parsed = planSchema.safeParse(plan);
  if (!parsed.success || !validInputs(parsed.data.inputs_used)) return null;
  const p = parsed.data, r = p.results;
  if (canonical(r.practice_type) !== canonical(p.inputs_used.practice_type)) return null;
  for (const value of [r.usable_square_feet, r.net_room_square_feet, r.rentable_square_feet, r.parking.spaces_needed]) {
    if (value && (value.low < 0 || value.high < value.low)) return null;
  }
  return p;
}
function element(tag, text, className) { const node = document.createElement(tag); node.textContent = text; if (className) node.className = className; return node; }
function list(title, rows) { const section = document.createElement('section'); section.append(element('h3', title)); const ul = document.createElement('ul'); ul.append(...rows.map(row => element('li', row))); section.append(ul); return section; }
const range = value => `${value.low.toLocaleString()}–${value.high.toLocaleString()}`;
function planningVisual(plan) {
  const r = plan.results, i = plan.inputs_used;
  const svgNode = (tag, attributes, text) => {
    const node = document.createElementNS('http://www.w3.org/2000/svg', tag);
    for (const [key, value] of Object.entries(attributes)) node.setAttribute(key, value);
    if (text !== undefined) node.textContent = text;
    return node;
  };
  const svg = svgNode('svg', { viewBox: '0 0 440 570', role: 'img', 'aria-label': 'Practice planning relationships', class: 'planning-flow' });
  svg.append(svgNode('title', {}, 'Program inputs flow through a sizing rule to usable area and preliminary parking; building checks remain independent verification.'),
    svgNode('desc', {}, 'Solid arrows show calculated planning estimates. The dashed branch shows due diligence; it does not establish local requirements or room allocations.'));
  const rows = [
    ['Program inputs', `${i.providers} provider(s) · ${i.operatories || i.exam_rooms} ${i.operatories ? 'operatories' : 'rooms'}`],
    ['Sizing rule', r.usable_area_type],
    ['Usable area', `${range(r.usable_square_feet)} usable SF`],
    ['Preliminary parking', r.parking.spaces_needed ? `${range(r.parking.spaces_needed)} spaces · estimate` : 'Ratio unknown · verify demand'],
    ['Building due diligence', 'Local requirements unverified'],
  ];
  rows.forEach(([label, value], index) => {
    const y = 12 + index * 110;
    if (index > 0 && index < 4) svg.append(svgNode('path', { d: `M220 ${y - 28}v28m-6 -8 6 8 6 -8`, class: 'flow-arrow' }));
    svg.append(svgNode('rect', { x: 40, y, width: 360, height: 82, rx: 12, class: index === 4 ? 'flow-check' : 'flow-node' }),
      svgNode('text', { x: 220, y: y + 30, 'text-anchor': 'middle', class: 'flow-label' }, label),
      svgNode('text', { x: 220, y: y + 59, 'text-anchor': 'middle', class: 'flow-value' }, value));
  });
  svg.append(svgNode('path', { d: 'M40 273H18V493H40m-8 -6 8 6 -8 6', class: 'flow-arrow flow-verify' }));
  return svg;
}
function render(plan) {
  if (!plan) { $('plan').replaceChildren(); $('status').classList.remove('busy'); $('status').textContent = 'Choose a program to create a space plan.'; return; }
  const root = document.createDocumentFragment();
  const r = plan.results;
  root.append(element('h2', catalog.find(row => row.value === r.practice_type || (r.practice_type === 'dental' && row.value === 'dental_gp'))?.label || 'Practice program'));
  root.append(element('div', `${range(r.usable_square_feet)} usable SF`, 'area'));
  root.append(element('p', `${r.usable_area_type} · ${r.source_class}`, 'muted'), element('p', r.sizing_rule));
  root.append(element('p', r.parking.spaces_needed ? `${range(r.parking.spaces_needed)} parking spaces (preliminary)` : 'Parking need unknown: no training ratio supplied.'));
  root.append(element('p', r.parking.area_basis + '. ' + r.parking.status, 'muted'));
  root.append(planningVisual(plan));
  root.append(list('Room functions', r.rooms.map(row => `${row.room} · ${row.source_class}`)));
  const checks = document.createElement('section'); checks.append(element('h3', 'Building due diligence'));
  for (const [topic, questions] of Object.entries(r.due_diligence)) {
    const details = document.createElement('details'); details.append(element('summary', topic.toUpperCase()));
    const ul = document.createElement('ul'); ul.append(...questions.map(question => element('li', question))); details.append(ul); checks.append(details);
  }
  root.append(checks);
  for (const [title, key] of [['Warnings', 'warnings'], ['Missing inputs', 'missing_inputs'], ['Assumptions', 'assumptions'], ['Limitations', 'limitations']]) {
    if (plan[key]?.length) root.append(list(title, plan[key]));
  }
  root.append(element('p', plan.market_note || 'Local requirements remain unverified.', 'muted'), element('p', plan.notice, 'muted'));
  $('plan').replaceChildren(root);
}
function adopt(plan) {
  const parsed = validatedPlan(plan);
  if (!parsed) return false;
  render(parsed);
  previous = current; current = parsed; $('undo').disabled = !previous; controls({ ...parsed.inputs_used, market: parsed.market }); return true;
}
async function publish(plan, token = revision) {
  if (!extensions.modelContext) return;
  pendingPublication = { plan, token };
  if (publishing) return;
  publishing = true;
  try {
    while (pendingPublication) {
      const next = pendingPublication; pendingPublication = undefined;
      if (next.token !== revision) continue;
      awaitingPublicationReceipt = true;
      try {
        const receipt = await extensions.modelContext.update(next.plan ? {
          content: [{ type: 'text', text: `${range(next.plan.results.usable_square_feet)} usable SF; preliminary ${next.plan.results.practice_type} space plan.`, _meta: { 'openai/title': 'Current space plan' } }],
          structuredContent: { practice_space_plan: { inputs: { ...next.plan.inputs_used, market: next.plan.market || 'other_market' } } },
        } : { content: [], structuredContent: {} });
        // Even an obsolete publication owns its host notification echo.
        if (receipt?.updateId) localPublications.add(receipt.updateId);
        if (next.token === revision) contextId = receipt?.updateId;
      } catch {
        if (next.token === revision) $('sync-note').textContent = 'Plan context could not be shared. Retry the update.';
      } finally {
        awaitingPublicationReceipt = false;
        if (deferredContextChange) { deferredContextChange = false; syncContext(); }
      }
    }
  } finally { publishing = false; }
}
async function update(inputs, share = true) {
  const token = ++revision;
  $('status').classList.add('busy'); $('status').textContent = 'Updating space plan…';
  try {
    const response = await app.callServerTool({ name: 'update_practice_space_plan', arguments: inputs });
    if (token !== revision) return;
    if (response.isError || !adopt(response.structuredContent)) throw new Error('invalid plan');
    $('status').textContent = 'Space plan updated.';
    if (share) await publish(current).catch(() => { $('sync-note').textContent = 'Plan updated; conversation context could not be shared.'; });
  } catch {
    if (token === revision) $('status').textContent = 'Plan could not update. Check the selected program and counts, then retry.';
  } finally { if (token === revision) $('status').classList.remove('busy'); }
}
$('program').addEventListener('submit', event => { event.preventDefault(); if ($('program').reportValidity()) void update(readControls()); });
$('undo').addEventListener('click', () => { if (previous) void update({ ...previous.inputs_used, market: previous.market || 'other_market' }); });
function syncContext() {
  const context = extensions.modelContext?.getCurrent();
  if (context === undefined) return;
  if (context !== null && localPublications.has(context.updateId)) return;
  if (context !== null && context.updateId === contextId) return;
  // Hosts may notify before acknowledging an update. Wait for its identity,
  // while keeping explicit removal (null) immediate.
  if (context !== null && awaitingPublicationReceipt) { deferredContextChange = true; return; }
  contextId = context?.updateId;
  if (context === null) { ++revision; current = previous = null; $('undo').disabled = true; render(null); if (publishing) void publish(null); return; }
  const inputs = context.structuredContent?.practice_space_plan?.inputs;
  if (!inputs && context.content?.length === 0) { render(null); return; }
  if (validInputs(inputs, true)) void update(contextInput.parse(inputs), false);
  else $('status').textContent = 'Plan context could not load. Check the selected program and counts.';
}
app.ontoolresult = response => {
  if (response.isError) { $('status').textContent = 'The last requested plan failed. Check program counts.'; return; }
  if (!current && response.structuredContent?.plan === null) { render(null); return; }
  try {
    if (!adopt(response.structuredContent)) throw new Error('invalid plan');
    ++revision; $('status').classList.remove('busy'); $('status').textContent = 'Space plan updated from conversation.'; void publish(current);
  } catch { $('status').textContent = 'Plan could not load. Check the selected program and counts.'; }
};
app.addEventListener('hostcontextchanged', syncContext);
app.connect().then(() => {
  syncContext();
  if (!extensions.modelContext) render(null);
  $('sync-note').textContent = extensions.modelContext ? 'Plan context is shared with this conversation. Removing it clears the panel.' : 'This host does not support Model-App Context. Planner controls remain available.';
}).catch(() => { $('status').textContent = 'Open this planner in a connected MCP App host to calculate a plan.'; });
