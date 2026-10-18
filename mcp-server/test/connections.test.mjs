import test from 'node:test';
import assert from 'node:assert/strict';
import { CONNECTION_PROVIDERS, connectionsProjection } from '../src/connections.v1.js';
import { resourceObservationTools } from '../src/resource-observation.v5.js';
const dashboard={generated_at:'2026-10-02T12:00:00Z',providers:[{provider:'model_route',state:'ok',observed_at:'2026-10-02T12:00:00Z',charge:99,estimate:12,allowance:500,configured_capacity:{configured:true}}]};
test('every named provider appears; configuration and capacity never imply connected or dollar spend',()=>{
 const read=connectionsProjection(dashboard);
 assert.equal(read.schema,'doctorcre-connections.v1');
 assert.deepEqual(read.providers.map(p=>p.id),CONNECTION_PROVIDERS.map(p=>p.id));
 assert.ok(read.providers.every(p=>p.status==='unknown'&&p.checked_at===null&&p.spend===null));
 assert.equal(read.devices.state,'unknown');assert.deepEqual(read.devices.items,[]);
});
test('read is registered through existing resource seam and executes exactly one read query',async()=>{
 const tool=resourceObservationTools({ToolError:Error})['read-resource-dashboard'];assert.equal(tool.write,false);
 const calls=[];const value=await tool.handler({query:async sql=>{calls.push(sql);return {rows:[{dashboard}]};}});
 assert.equal(value.connections.ok,true);assert.deepEqual(value.providers,dashboard.providers);assert.deepEqual(calls,['select ops.read_resource_dashboard() as dashboard']);
});
test('malformed or unavailable observations refuse without inventing healthy states',async()=>{
 const tool=resourceObservationTools({ToolError:Error})['read-resource-dashboard'];
 for(const rows of [[],[{dashboard:{providers:null}}]])await assert.rejects(()=>tool.handler({query:async()=>({rows})}));
});
