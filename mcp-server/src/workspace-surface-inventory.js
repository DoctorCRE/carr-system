// Declared CARR browser routes consumed by the Atlas inventory graph.
// Asset names refer to the pinned DoctorCRE release, materialized before deployment.
import { CLIENTS_ROUTE, VENDORS_ROUTE } from "./workspace-business-read.js";

export const AUTHENTICATED_SURFACES = Object.freeze([
  Object.freeze({
    asset: "workspace.html", routes: Object.freeze(["/"]),
    workspace_stylesheet: true,
  }),
  Object.freeze({
    asset: "business.html", routes: Object.freeze([CLIENTS_ROUTE, VENDORS_ROUTE]),
    workspace_stylesheet: true,
  }),
  Object.freeze({
    asset: "index.html", routes: Object.freeze(["/deals"]),
    workspace_stylesheet: false,
  }),
  Object.freeze({
    asset: "leads.html", routes: Object.freeze(["/leads"]),
    workspace_stylesheet: false,
  }),
  Object.freeze({
    asset: "room.html", routes: Object.freeze(["/room.html"]),
    workspace_stylesheet: false,
  }),
  Object.freeze({
    asset: "queue.html", routes: Object.freeze(["/queue.html"]),
    workspace_stylesheet: false,
  }),
  Object.freeze({
    asset: "system-work.html", routes: Object.freeze(["/system-work.html"]),
    workspace_stylesheet: false,
  }),
]);

/** Every canonical authenticated address, in surface order. */
export const WORKSPACE_ROUTES = Object.freeze(AUTHENTICATED_SURFACES.flatMap((surface) => [...surface.routes]));
