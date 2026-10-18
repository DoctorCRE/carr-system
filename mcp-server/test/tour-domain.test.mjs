import assert from "node:assert/strict";
import test from "node:test";
import { tourDomainTools } from "../src/tour-domain.js";

class ToolError extends Error { constructor(payload) { super(payload.error); this.payload = payload; } }
const actor = { id: "actor-00000000-0000-4000-8000-000000000001", slug: "codex" };
const ids = { tour: "10000000-0000-4000-8000-000000000001", route: "20000000-0000-4000-8000-000000000001", base: "30000000-0000-4000-8000-000000000001", property: "40000000-0000-4000-8000-000000000001", stop: "50000000-0000-4000-8000-000000000001", oldStop: "60000000-0000-4000-8000-000000000001", rights: "70000000-0000-4000-8000-000000000001", transition: "80000000-0000-4000-8000-000000000001", acceptance: "90000000-0000-4000-8000-000000000001", cheat: "a0000000-0000-4000-8000-000000000001", restore: "b0000000-0000-4000-8000-000000000001" };
const key = "c0000000-0000-4000-8000-000000000001", digest = x => `sha256:${x.repeat(64)}`;
const point = role => ({ latitude: 30.4451, longitude: -87.1893, position_role: role, source_ref: "asset:public:abcdefghijklmnoq", precision_class: "entrance" });
const providerRequest = { travel_mode: "driving", optimize_waypoint_order: true, waypoint_count: 2, departure_at: null, request_digest: digest("a") };
const appointmentStop = (start, end) => ({ idempotency_key: key, route_version_id: ids.route, property_id: ids.property,
  route_sequence: 1, route_label: "A", stop_state: "active", appointment_start: start, appointment_end: end,
  locked_appointment: true, dwell_minutes: 20, buffer_minutes: 5,
  access_coordinate_status: "approved", assertion_set_digest: digest("c") });
function harness() { const calls=[],events=[],envelopes=[]; const c={async query(sql,params){calls.push({sql,params});if(sql.includes("create_tour_domain"))return{rows:[{tour_id:ids.tour}]};if(sql.includes("prepare_tour_route_version"))return{rows:[{route_version_id:ids.route}]};if(sql.includes("append_tour_route_version"))return{rows:[{route_version_id:ids.route}]};if(sql.includes("append_tour_route_stop_transition"))return{rows:[{route_stop_transition_id:ids.transition}]};if(sql.includes("append_tour_route_stop"))return{rows:[{route_stop_id:ids.stop}]};if(sql.includes("accept_tour_route_version"))return{rows:[{route_version_acceptance_id:ids.acceptance}]};if(sql.includes("append_tour_cheat_sheet_revision"))return{rows:[{cheat_sheet_revision_id:ids.cheat}]};if(sql.includes("restore_tour_cheat_sheet_revision"))return{rows:[{cheat_sheet_revision_id:ids.restore}]};throw new Error(sql)}};const withEnvelope=async(client,a,verb,args,fn)=>{assert.equal(client,c);assert.equal(a,actor);assert.equal(args.idempotency_key,key);envelopes.push({verb,args});return fn()};return {c,calls,events,envelopes,tools:tourDomainTools({withEnvelope,writeEvent:async(...e)=>events.push(e),ToolError})}; }

test("domain rejects reversed microsecond windows before writes and preserves valid windows", async t => {
  for (const [name, start, end, valid] of [
    ["reversed", "2026-10-01T14:00:00.123999Z", "2026-10-01T14:00:00.123001Z", false],
    ["equal across offsets", "2026-10-01T09:00:00.123456-05:00", "2026-10-01T14:00:00.123456Z", true],
    ["increasing", "2026-10-01T14:00:00.123001Z", "2026-10-01T14:00:00.123002Z", true],
    ["reversed across offsets", "2026-10-01T09:00:00.123999-05:00", "2026-10-01T14:00:00.123001Z", false],
    ["reversed before epoch", "1969-12-31T23:59:59.999999Z", "1969-12-31T23:59:59.999998Z", false],
    ["increasing across epoch", "1969-12-31T23:59:59.999999Z", "1970-01-01T00:00:00.000001Z", true],
    ["reversed distant date", "9999-10-01T14:00:00.123999Z", "9999-10-01T14:00:00.123998Z", false],
  ]) await t.test(name, async () => {
    const h = harness(), stop = appointmentStop(start, end);
    if (valid) {
      await h.tools["append-tour-route-stop"].handler(h.c, actor, stop);
      assert.deepEqual(h.calls[0].params.slice(6, 8), [start, end]);
    } else {
      await assert.rejects(h.tools["append-tour-route-stop"].handler(h.c, actor, stop),
        e => e.payload?.error === "tour_appointment_invalid");
      assert.deepEqual([h.calls, h.envelopes, h.events], [[], [], []]);
    }
  });
});

test("domain appointment timestamps respect PostgreSQL offset and year boundaries", async t => {
  for (const [name, start, end, valid] of [
    ["positive offset boundary", "2026-10-01T14:00:00.123456+15:59", "2026-10-01T14:00:00.123457+15:59", true],
    ["negative offset boundary", "2026-10-01T14:00:00.123456-15:59", "2026-10-01T14:00:00.123457-15:59", true],
    ["first AD year", "0001-10-01T14:00:00Z", "0001-10-01T14:30:00Z", true],
    ["last four-digit year", "9999-10-01T14:00:00Z", "9999-10-01T14:30:00Z", true],
    ["positive offset overflow", "2026-10-01T14:00:00+16:00", "2026-10-01T14:30:00+16:00", false],
    ["negative offset overflow", "2026-10-01T14:00:00-16:00", "2026-10-01T14:30:00-16:00", false],
    ["minute overflow", "2026-10-01T14:00:00+15:60", "2026-10-01T14:30:00+15:60", false],
    ["year zero", "0000-10-01T14:00:00Z", "0000-10-01T14:30:00Z", false],
    ["extended year", "+010000-10-01T14:00:00Z", "+010000-10-01T14:30:00Z", false],
  ]) await t.test(name, async () => {
    const h = harness(), stop = appointmentStop(start, end);
    if (valid) {
      await h.tools["append-tour-route-stop"].handler(h.c, actor, stop);
      assert.deepEqual(h.calls[0].params.slice(6, 8), [start, end]);
    } else {
      for (const field of ["appointment_start", "appointment_end"]) {
        await assert.rejects(h.tools["append-tour-route-stop"].handler(h.c, actor,
          { ...appointmentStop("2026-10-01T14:00:00Z", "2026-10-01T14:30:00Z"), [field]: stop[field] }),
          e => e.payload?.error === "tour_input_invalid" && e.payload.field === field);
        assert.deepEqual([h.calls, h.envelopes, h.events], [[], [], []]);
      }
    }
  });
});

test("route appointment offsets round-trip through the domain without losing microseconds", async () => {
  const h = harness();
  const stop = { idempotency_key: key, route_version_id: ids.route, property_id: ids.property,
    route_sequence: 1, route_label: "A", stop_state: "active",
    appointment_start: "2026-10-01T09:00:00.123456-05:00", appointment_end: "2026-10-01T09:30:00.123456-05:00",
    locked_appointment: true, dwell_minutes: 20, buffer_minutes: 5,
    access_coordinate_status: "approved", assertion_set_digest: digest("c") };
  await h.tools["append-tour-route-stop"].handler(h.c, actor, stop);
  assert.deepEqual(h.calls.at(-1).params.slice(6, 8), [stop.appointment_start, stop.appointment_end]);
  for (const value of ["2026-02-30T14:00:00+00:00", "2026-10-01T14:00:00", "2026-10-01T14:00:00+24:00", "2026-10-01T14:00:00-05:60"]) {
    await assert.rejects(h.tools["append-tour-route-stop"].handler(h.c, actor,
      { ...stop, appointment_start: value }), e => e.payload?.error === "tour_input_invalid");
  }
});

test("writes use all exact revised SQL seams, derived tenancy, and never leak internal request or cheat-sheet content", async () => {
  const h=harness();
  assert.deepEqual(Object.keys(h.tools).sort(),["accept-tour-route-version","append-tour-cheat-sheet-revision","append-tour-route-stop","append-tour-route-stop-transition","append-tour-route-version","create-tour-domain","prepare-tour-route-version","restore-tour-cheat-sheet-revision"]);
  assert.equal(h.tools["accept-tour-route-version"].authorityOnly,true);
  await h.tools["create-tour-domain"].handler(h.c,actor,{idempotency_key:key,tour_name:"Tour",subject_type:"client",subject_id:"client:demo",canonical_dataset_version:"v1",start_point:point("start"),end_point:point("end")});
  assert.deepEqual(h.calls.at(-1).params,["carr-internal","Tour","client","client:demo","v1",JSON.stringify(point("start")),JSON.stringify(point("end"))]);
  await h.tools["append-tour-route-version"].handler(h.c,actor,{idempotency_key:key,tour_id:ids.tour,route_version:2,base_route_version_id:ids.base,start_point:point("start"),end_point:point("end"),routing_source:"provider",routing_provider:"routing.example",routing_policy_key:"route-planning",routing_rights_receipt_id:ids.rights,routing_request:providerRequest,routing_response_digest:digest("b"),expected_route_version:1});
  assert.equal(h.calls.at(-1).params.length,13); assert.deepEqual(h.calls.at(-1).params.slice(6),["provider","routing.example",ids.rights,JSON.stringify(providerRequest),digest("b"),1,"route-planning"]);
  await h.tools["prepare-tour-route-version"].handler(h.c,actor,{idempotency_key:key,tour_id:ids.tour,base_route_version_id:ids.base,expected_route_version:1,stop_ids:[ids.oldStop,ids.stop]});
  assert.deepEqual(h.calls.at(-1).params,["carr-internal",ids.tour,ids.base,1,JSON.stringify([ids.oldStop,ids.stop])]);
  await h.tools["append-tour-route-stop"].handler(h.c,actor,{idempotency_key:key,route_version_id:ids.route,property_id:ids.property,route_sequence:1,route_label:"A",stop_state:"active",appointment_start:"2026-08-27T14:00:00Z",appointment_end:"2026-08-27T14:30:00Z",locked_appointment:true,dwell_minutes:20,buffer_minutes:10,access_coordinate_status:"approved",assertion_set_digest:digest("c")});
  assert.equal(h.calls.at(-1).params.length,13);
  await h.tools["append-tour-route-stop-transition"].handler(h.c,actor,{idempotency_key:key,old_route_version_id:ids.base,new_route_version_id:ids.route,old_route_stop_id:ids.oldStop,new_route_stop_id:ids.stop,disposition:"reordered"});
  assert.deepEqual(h.calls.at(-1).params,["carr-internal",ids.base,ids.route,ids.oldStop,ids.stop,"reordered"]);
  await h.tools["accept-tour-route-version"].handler(h.c,actor,{idempotency_key:key,route_version_id:ids.route,expected_prior_route_version:1,acceptance_digest:digest("d")});
  const content={private_notes:["inspect entrance"],access_notes:"private"}; await h.tools["append-tour-cheat-sheet-revision"].handler(h.c,actor,{idempotency_key:key,tour_id:ids.tour,content,expected_revision_number:0}); await h.tools["restore-tour-cheat-sheet-revision"].handler(h.c,actor,{idempotency_key:key,tour_id:ids.tour,restore_revision_id:ids.cheat,expected_revision_number:1});
  for(const event of h.events)assert.doesNotMatch(JSON.stringify(event),/private_notes|access_notes|routing_request/); assert.equal(h.envelopes.length,8);
});

test("manual/provider requests, actor derivation, and authority selectors fail closed", async () => {
  const h=harness(); const base={idempotency_key:key,tour_id:ids.tour,route_version:2,base_route_version_id:ids.base,start_point:point("start"),end_point:point("end"),expected_route_version:1};
  await h.tools["append-tour-route-version"].handler(h.c,actor,{...base,routing_source:"manual",routing_provider:null,routing_policy_key:null,routing_rights_receipt_id:null,routing_request:{},routing_response_digest:null});
  assert.deepEqual(h.calls.at(-1).params.slice(6),["manual",null,null,JSON.stringify({}),null,1,null]);
  for(const request of [{travel_mode:"driving"},{...providerRequest,address:"leak"}])await assert.rejects(h.tools["append-tour-route-version"].handler(h.c,actor,{...base,routing_source:"provider",routing_provider:"routing.example",routing_policy_key:"route-planning",routing_rights_receipt_id:ids.rights,routing_request:request,routing_response_digest:digest("e")}),e=>e instanceof ToolError&&e.payload.error==="tour_routing_request_invalid");
  await assert.rejects(h.tools["create-tour-domain"].handler(h.c,null,{idempotency_key:key,tour_name:"Tour",subject_type:"client",subject_id:"client:demo",canonical_dataset_version:"v1",start_point:point("start"),end_point:point("end")}),e=>e instanceof ToolError&&e.payload.error==="tour_actor_context_required");
  await assert.rejects(h.tools["accept-tour-route-version"].handler(h.c,actor,{idempotency_key:key,route_version_id:ids.route,expected_prior_route_version:1,acceptance_digest:digest("f"),organization_tenant_id:"other"}),e=>e instanceof ToolError&&e.payload.error==="caller_authority_field_forbidden");
});

test("stop, appointment, and complete transition state rules reject invalid mappings and permit held mapping", async () => {
  const h=harness(); const stop={idempotency_key:key,route_version_id:ids.route,property_id:ids.property,route_sequence:null,route_label:null,stop_state:"active",appointment_start:null,appointment_end:null,locked_appointment:false,dwell_minutes:0,buffer_minutes:0,access_coordinate_status:"unknown",assertion_set_digest:digest("f")};
  await assert.rejects(h.tools["append-tour-route-stop"].handler(h.c,actor,stop),e=>e instanceof ToolError&&e.payload.error==="tour_stop_state_invalid");
  await assert.rejects(h.tools["append-tour-route-stop"].handler(h.c,actor,{...stop,route_sequence:1,route_label:"A",appointment_start:"2026-08-27T14:00:00Z",locked_appointment:true}),e=>e instanceof ToolError&&e.payload.error==="tour_appointment_invalid");
  await assert.rejects(h.tools["append-tour-route-stop-transition"].handler(h.c,actor,{idempotency_key:key,old_route_version_id:ids.base,new_route_version_id:ids.route,old_route_stop_id:ids.oldStop,new_route_stop_id:null,disposition:"reordered"}),e=>e instanceof ToolError&&e.payload.error==="tour_transition_invalid");
  await h.tools["append-tour-route-stop-transition"].handler(h.c,actor,{idempotency_key:key,old_route_version_id:ids.base,new_route_version_id:ids.route,old_route_stop_id:ids.oldStop,new_route_stop_id:ids.stop,disposition:"held"});
  assert.deepEqual(h.calls.at(-1).params,["carr-internal",ids.base,ids.route,ids.oldStop,ids.stop,"held"]);
});
