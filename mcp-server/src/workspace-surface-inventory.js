// V5-J101 — the workspace's own inventory of itself.
//
// WHAT THIS MODULE IS. Three declarations and nothing else:
//
//   1. AUTHENTICATED_SURFACES — the seven authenticated surfaces and, per
//      surface, which shell parts it actually carries TODAY. Not the shell the
//      slice wants; the shell that is on disk. Two of the seven load
//      css/workspace.css, carry the More disclosure and the phone bar, and link
//      Clients and Vendors. Five do not. Recording the true 2-of-7 is the point:
//      it is a regression fence now (a surface that silently drops the stylesheet
//      fails) and the acceptance test for the single-shell unit later (that unit
//      flips these flags and the suite makes it prove each one).
//
//   2. WORKSPACE_ROUTES — the canonical authenticated addresses a partner keeps.
//      Derived from the router's own tables, not restated: CLIENTS_ROUTE,
//      VENDORS_ROUTE and BUSINESS_ASSET_PATH come from the business read model,
//      COMMAND_CENTER_PATH from the Command Center read, and the plain paths
//      below are checked against DEALROOM_EXACT_PATHS by the suite, so a route
//      named here that the router does not serve is a test failure.
//
//   3. COMMAND_WRITE_VERBS / ACKNOWLEDGEMENT_ENDPOINTS — the subjects at which a
//      partner's typed command is acknowledged. The verbs are the ones the Deal
//      Room board actually writes through live-client.js, and the suite re-derives
//      that list from live-client.js itself, so this cannot drift into a wish.
//
// WHAT THIS MODULE IS NOT, and the suite enforces every clause of it:
//   * It carries NO measurement. There is no millisecond, no percentile, no
//     sample, no observation and no clock anywhere in this file.
//   * It carries NO threshold. It does not import benchmark-minimum.v5.js and it
//     does not restate a single SLO constant. A p95 is an observation of a running
//     deployment; source cannot produce one and this file does not pretend to.
//   * It carries NO receipt. Nothing here is issued, accepted, persisted or
//     minted. It names the SUBJECT AXIS a future benchmark run would measure —
//     which routes, which acknowledgement endpoints — and stops there.
//
// Tours is deliberately absent from WORKSPACE_ROUTES. The router carries a
// /tours path, but Tours is inert in this release on every authenticated surface,
// and naming it as a canonical J101 route would be declaring an affordance the
// slice does not ship.

import { COMMAND_CENTER_PATH } from "./workspace-command-center.js";
import { BUSINESS_API_PREFIX, BUSINESS_ASSET_PATH, CLIENTS_ROUTE, VENDORS_ROUTE } from "./workspace-business-read.js";

export const HOME_ROUTE = "/";
export const DEALS_ROUTE = "/deals";
export const LEADS_ROUTE = "/leads";
export const SYSTEM_WORK_ROUTE = "/system-work.html";
export const OBSERVATORY_ROUTE = "/room.html";
export const QUEUE_ROUTE = "/queue.html";

/** The one same-origin mount every typed Deal Room command travels over. */
export const COMMAND_ENDPOINT = "/mcp";

/**
 * One row per authenticated surface. `asset` is the file under dealroom/; `routes`
 * are the canonical addresses that serve it. The four booleans are observations of
 * the file, and the suite checks each one against the file in both directions — a
 * true that is missing fails, and a false that has quietly become true fails too.
 */
export const AUTHENTICATED_SURFACES = Object.freeze([
  Object.freeze({
    asset: "workspace.html", routes: Object.freeze([HOME_ROUTE]),
    workspace_stylesheet: true, nav_more: true, mobile_nav: true, links_clients: true, links_vendors: true,
  }),
  Object.freeze({
    asset: "business.html", routes: Object.freeze([CLIENTS_ROUTE, VENDORS_ROUTE]),
    workspace_stylesheet: true, nav_more: true, mobile_nav: true, links_clients: true, links_vendors: true,
  }),
  // The Deal Room is a second shell: css/app.css, its own type and theme colour,
  // a .workspaces nav with no Clients, no Vendors and no phone bar. That split is
  // asserted from the other side in deal-change-receipts.test.mjs, which pins
  // index.html as carrying none of it. This row and that assertion are one
  // statement, not two tests that happen to agree.
  Object.freeze({
    asset: "index.html", routes: Object.freeze([DEALS_ROUTE]),
    workspace_stylesheet: false, nav_more: false, mobile_nav: false, links_clients: false, links_vendors: false,
  }),
  Object.freeze({
    asset: "leads.html", routes: Object.freeze([LEADS_ROUTE]),
    workspace_stylesheet: false, nav_more: false, mobile_nav: false, links_clients: false, links_vendors: false,
  }),
  Object.freeze({
    asset: "room.html", routes: Object.freeze([OBSERVATORY_ROUTE]),
    workspace_stylesheet: false, nav_more: false, mobile_nav: false, links_clients: false, links_vendors: false,
  }),
  Object.freeze({
    asset: "queue.html", routes: Object.freeze([QUEUE_ROUTE]),
    workspace_stylesheet: false, nav_more: false, mobile_nav: false, links_clients: false, links_vendors: false,
  }),
  Object.freeze({
    asset: "system-work.html", routes: Object.freeze([SYSTEM_WORK_ROUTE]),
    workspace_stylesheet: false, nav_more: false, mobile_nav: false, links_clients: false, links_vendors: false,
  }),
]);

/** Every canonical authenticated address, in surface order. */
export const WORKSPACE_ROUTES = Object.freeze(AUTHENTICATED_SURFACES.flatMap((surface) => [...surface.routes]));

/** The read endpoints the workspace calls. Reads are not commands and are listed apart. */
export const WORKSPACE_READ_ENDPOINTS = Object.freeze([
  COMMAND_CENTER_PATH,
  `${BUSINESS_API_PREFIX}clients`,
  `${BUSINESS_API_PREFIX}vendors`,
]);

/** The asset path Clients and Vendors are both served from. */
export const WORKSPACE_ASSET_PATHS = Object.freeze([BUSINESS_ASSET_PATH]);

/**
 * The typed write verbs the Deal Room board sends over COMMAND_ENDPOINT. Read off
 * dealroom/js/live-client.js, where every one of them goes through `write(...)`;
 * the suite re-derives the set from that file so this list cannot drift.
 * Home, Clients and Vendors issue none of these — they are read-only surfaces.
 */
export const COMMAND_WRITE_VERBS = Object.freeze([
  "add-deal-note",
  "create-national-account",
  "create-national-market-deal",
  "end-deal-review",
  "new-deal",
  "patch-deal-field",
  "presence-lease",
  "resolve-candidate",
  "resolve-conflict",
  "resolve-post-call-candidate",
  "revert-deal-field",
  "review-deal",
  "set-lead",
  "set-market-agent",
  "set-national-account-owner",
  "set-next-step",
  "start-deal-review",
]);

/**
 * The subject axis for command acknowledgement: one entry per verb, on the single
 * mount that carries them. A benchmark run would measure these; this file states
 * WHICH, never how fast, and never how fast is fast enough.
 */
export const ACKNOWLEDGEMENT_ENDPOINTS = Object.freeze(
  COMMAND_WRITE_VERBS.map((verb) => `POST ${COMMAND_ENDPOINT} tools/call ${verb}`),
);

/** The surface row for an asset file name, or null. */
export function surfaceForAsset(asset) {
  return AUTHENTICATED_SURFACES.find((surface) => surface.asset === asset) || null;
}

/** How many surfaces carry the workspace shell today. Two, and the suite proves it. */
export function shellCoverage() {
  const total = AUTHENTICATED_SURFACES.length;
  const onShell = AUTHENTICATED_SURFACES.filter((surface) => surface.workspace_stylesheet).length;
  return { on_shell: onShell, total, off_shell: total - onShell };
}
