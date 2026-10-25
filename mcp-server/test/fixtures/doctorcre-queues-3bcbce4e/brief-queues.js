import { escapeText } from './change-receipts.mjs';
import { readWithDeadline } from './auto-refresh.mjs';

const labels = { today:'Today queue', loops:'Open loops', needs_joe:'Needs Joe', assurance_cadence:'Assurance alerts' };
const present = value => typeof value === 'string' && value.length > 0;
const identity = (queue,row) => {
  if (queue === 'today') return present(row.id) && ['next_action','post_call_action','critical_date','ingest'].includes(row.item_kind) ? `${row.item_kind}:${row.id}` : null;
  if (queue === 'loops') return row.kind === 'open_loop' && present(String(row.number ?? '')) ? `${row.kind}:${row.number}` : null;
  return queue === 'needs_joe' ? present(row.key) && present(row.title) ? JSON.stringify([row.key,row.title]) : null : row.notification_id;
};
const title = (queue,row) => queue === 'today' ? row.what || row.subject_name || `${row.item_kind} ${row.id}` : queue === 'loops' ? row.title || row.label || `Open loop ${row.number}` : queue === 'needs_joe' ? row.title || `Decision ${row.key}` : row.reason || `Alert ${row.subject_ref || row.notification_id}`;
function validPage(value,sponsor,previousCursor) {
  return value?.sponsor === sponsor && value.state !== 'unavailable' && Array.isArray(value.items) && value.returned === value.items.length && Number.isInteger(value.total) && value.total >= value.items.length
    && typeof value.has_more === 'boolean' && (value.has_more ? typeof value.next_cursor === 'string' && value.next_cursor.length > 0 && value.next_cursor !== previousCursor : value.next_cursor === null);
}

export function mountBriefQueues({menu,content,getClient,getSponsor,onOpen,onBack,onInvalidate,onChange}) {
  let active=null,items=[],page=null,busy=false,error=false,epoch=0,viewer=null,observedAt=null;
  const reset = () => { ++epoch; active=null;items=[];page=null;busy=false;error=false;viewer=null;observedAt=null; };
  const render = () => {
    const count = page ? `${items.length} of ${page.total}${page.total_scope === 'available_sources' ? ' from available sources' : ''}` : 'Coverage unknown';
    onChange();
    content.innerHTML = `<section class="morning-queue"><h3>${labels[active]}</h3><p role="status">${count}${busy ? ' · Updating…' : ''}${error ? ' · Queue update unavailable; retry to continue.' : ''}</p>${page?.state === 'partial' ? '<p>Some sources are unavailable. This list may be incomplete.</p>' : ''}<p>Live urgency order. Changed rows can move; reopen for a fresh list.</p><ol>${items.map(row => {
      const facts = active === 'today' ? [['Subject',row.subject_name || row.subject_ref],['Kind',row.item_kind],['Due',row.due_on],['Owner',row.owner_partner || row.owner]] : active === 'loops' ? [['Due',row.due_on],['Owner',row.owner_partner || row.owner],['Blocker',row.blocker_detail]] : active === 'needs_joe' ? [['Why',row.why?.text],['Action',row.action],['Blocks',row.blocks?.text],['Since',row.since]] : [['Severity',row.severity],['Subject',row.subject_ref],['Created',row.created_at]];
      return `<li data-queue-item="${escapeText(identity(active,row))}"><details><summary>${escapeText(title(active,row))}</summary><dl>${facts.filter(([,value])=>value).map(([label,value])=>`<div><dt>${label}</dt><dd>${escapeText(value)}</dd></div>`).join('')}</dl></details></li>`;
    }).join('')}</ol>${!page || page.has_more ? `<button type="button" data-brief-queue-more${busy ? ' disabled' : ''}>${error ? 'Retry queue' : page ? 'Load more' : 'Load queue'}</button>` : ''}<button type="button" data-brief-queue-back>Back to priorities</button></section>`;
  };
  const load = async () => {
    if (!active || busy) return;
    const request=++epoch,queue=active,sponsor=viewer,cursor=page?.next_cursor;
    busy=true;error=false;observedAt=null;render();
    try {
      const client=await getClient();
      const value=await readWithDeadline(()=>client.morningBriefQueue(queue,{...(cursor ? {cursor} : {})}));
      if (request!==epoch || sponsor!==getSponsor() || active!==queue) return;
      if (['joe','dell'].includes(value?.sponsor) && value.sponsor!==sponsor) {reset();onInvalidate();return;}
      if (!validPage(value,sponsor,cursor) || !value.items.every(row=>present(identity(queue,row)) && present(title(queue,row)))) throw Error('Invalid queue page');
      if (new Set(value.items.map(row=>identity(queue,row))).size!==value.items.length) throw Error('Duplicate queue identities');
      const seen=new Set(items.map(row=>identity(queue,row)));
      const next=[...items,...value.items.filter(row=>!seen.has(identity(queue,row)))];
      if (next.length>value.total || value.has_more && (value.items.length===0 || next.length>=value.total) || !value.has_more && next.length!==value.total) throw Error('Invalid queue coverage');
      items=next;page=value;observedAt=new Date().toISOString();
    } catch (failure) {
      if (request!==epoch || sponsor!==getSponsor() || active!==queue) return;
      if ([401,403].includes(failure.status)) {reset();onInvalidate();return;}
      error=true;
    } finally {
      if (request===epoch && active===queue) {busy=false;render();}
    }
  };
  menu.onclick = event => {
    const queue=event.target.closest('[data-brief-queue]')?.dataset.briefQueue;
    if (!Object.hasOwn(labels,queue) || !getSponsor()) return;
    reset();active=queue;viewer=getSponsor();onOpen(labels[queue]);load();
  };
  return {
    get active(){return active!==null;},reset,
    get updated(){return {observedAt,error};},
    refresh() {
      ++epoch;items=[];page=null;busy=false;error=false;observedAt=null;
      return load();
    },
    menu(facts) {
      if (viewer && viewer!==getSponsor()) reset();
      const html = facts ? `<p>Selected priorities. Open a queue for all eligible work.</p>${Object.entries(labels).filter(([queue])=>queue!=='needs_joe'||facts.sponsor==='joe').map(([queue,label])=> {
        const section=facts.sections[queue];
        const coverage=Number.isInteger(section?.total) ? `${section.items.length} of ${section.total}${section.total_scope === 'available_sources' ? ' from available sources' : ''}` : 'coverage unknown';
        const status=section?.state === 'partial' ? ' · Some sources are unavailable' : section?.state === 'unavailable' ? ' · Unavailable' : '';
        return `<button type="button" data-brief-queue="${queue}">${label} · ${coverage}${status}</button>`;
      }).join('')}` : '';
      if (menu.innerHTML!==html) menu.innerHTML=html;
    },
    click(event) {
      if (event.target.closest('[data-brief-queue-back]')) {reset();onBack();return true;}
      if (event.target.closest('[data-brief-queue-more]')) {load();return true;}
      return active!==null;
    },
  };
}
