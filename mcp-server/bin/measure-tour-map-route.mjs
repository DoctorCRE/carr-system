#!/usr/bin/env node
// Measure a real exported Tour route version against the map doctrine.
//
//   node mcp-server/bin/measure-tour-map-route.mjs <route.json> [--receipt <receipt.json>]
//
// <route.json> is { tour_id, projection_id, route_version, stops: [...] } as read from the live
// record layer by someone holding a key. The script reads no network and writes
// nothing: it reports map/list/story/offline order parity, which stops are
// downgraded, and which would get a native navigation link (the receipt must
// be bound to the route's tour, projection and version and carry the full passed
// evidence set). Exit 1 on a parity failure or malformed input, including
// malformed durations, lock states, appointments and endpoints.
import { readFile } from "node:fs/promises";
import {
  buildRouteVersionState, checkParity, projectRoute, buildNativeNavLink,
} from "../src/tour-map-route-state.js";

const args = process.argv.slice(2);
const routePath = args.find(arg => !arg.startsWith("--"));
const receiptAt = args.indexOf("--receipt");
if (!routePath) {
  console.error("usage: measure-tour-map-route.mjs <route.json> [--receipt <receipt.json>]");
  process.exit(2);
}
try {
  const route = JSON.parse(await readFile(routePath, "utf8"));
  const receipt = receiptAt >= 0 ? JSON.parse(await readFile(args[receiptAt + 1], "utf8")) : null;
  const state = buildRouteVersionState(route, { mode: "tour" });
  const projection = projectRoute(state);
  const parity = checkParity(projection);
  const stops = projection.list.map(item => {
    const nav = buildNativeNavLink(state, {
      route_stop_id: item.route_stop_id, platform: "apple_maps", travel_mode: "driving",
      promotion_receipt: receipt, now: new Date().toISOString(), user_ref: "measurement-run",
    });
    return { label: item.label, display: item.display, navigation: nav.available ? "available" : nav.reason_code };
  });
  const report = {
    route_version: projection.route_version, stop_count: stops.length, parity_ok: parity.ok,
    downgraded_or_unknown: stops.filter(stop => stop.display !== "verified").length, stops,
    divergences: parity.divergences,
  };
  console.log(JSON.stringify(report, null, 2));
  process.exit(parity.ok ? 0 : 1);
} catch (error) {
  console.error(error.message);
  process.exit(1);
}
