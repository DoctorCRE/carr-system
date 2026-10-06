import { organizationTenantForActor } from "./identity.js";

export const COMMAND_CENTER_PATH = "/api/v1/command-center";
// Every destination below is a deep link the Deal Room boot parser already
// honors (dealroom/js/app.js: workspace=team, filter=flagged, owner=me). There
// is no URL form for "mine, active", so that scope reports a null destination
// rather than inventing an unsupported filter.
export const TEAM_ACTIVE_DESTINATION = "/deals?workspace=team";
export const TEAM_FLAGGED_DESTINATION = "/deals?workspace=team&filter=flagged";
export const MY_FLAGGED_DESTINATION = "/deals?workspace=team&filter=flagged&owner=me";
export const NEEDS_JOE_DESTINATION = "/system-work.html";
export const HOME_SCOPES = ["team", "mine"];
const VALID_ACTORS = new Set(["joe", "dell"]);
const DEPENDENCY_CODES = new Set(["DEPENDENCY_UNAVAILABLE", "ECONNRESET", "ECONNREFUSED", "ETIMEDOUT", "ENOTFOUND", "08000", "57P01"]);

function typedError(code) {
  const error = new Error(code);
  error.code = code;
  return error;
}

function integer(row, key) {
  const raw = row?.[key];
  // Number(null) is 0, so an absent or empty count must be rejected before coercion.
  if (raw === null || raw === undefined || raw === "") return null;
  const value = Number(raw);
  return Number.isInteger(value) && value >= 0 ? value : null;
}

function source({ source, sourceRef, observedAt, validUntil, correlationId, freshness = "fresh" }) {
  return {
    source, source_ref: sourceRef, observed_at: observedAt, valid_until: validUntil,
    freshness, correlation_id: correlationId,
    safe_explanation: freshness === "fresh"
      ? "Fresh because this is a no-store request-time canonical database aggregate; valid for 60 seconds."
      : "This source is unavailable because legacy unscoped rows prevent a safe tenant-bound aggregate.",
  };
}

function classifyReadError(error) {
  if (error?.code === "DEPENDENCY_UNAVAILABLE") return typedError("DEPENDENCY_UNAVAILABLE");
  if (DEPENDENCY_CODES.has(error?.code)) return typedError("DEPENDENCY_UNAVAILABLE");
  if (error?.code === "INTERNAL_ERROR") return error;
  return typedError("INTERNAL_ERROR");
}

/** Aggregate-only, server-scoped Command Center read. Team is the default scope; mine is the viewer's own subset. */
export async function readCommandCenterSummary({ client, actor, tenant = organizationTenantForActor(actor), correlationId, now = () => new Date() }) {
  if (!correlationId || typeof correlationId !== "string") throw typedError("INTERNAL_ERROR");
  const boundTenant = organizationTenantForActor(actor);
  if (tenant !== boundTenant || tenant !== "carr-internal") throw typedError("TENANT_SCOPE_REFUSED");
  if (!actor?.slug || !VALID_ACTORS.has(actor.slug)) throw typedError("AUTHORIZATION_REFUSED");
  const observedAtDate = now();
  const observedAt = observedAtDate.toISOString();
  const validUntil = new Date(observedAtDate.valueOf() + 60_000).toISOString();
  let dealResult;
  let workResult;
  try {
    [dealResult, workResult] = await Promise.all([
      client.query(`select
          count(*) filter (where workspace_kind = 'team' and coalesce(operating_state, 'active') = 'active') as team_active,
          count(*) filter (where workspace_kind = 'team' and coalesce(operating_state, 'active') = 'active' and attention = true) as team_flagged,
          count(*) filter (where owner = $1::text and workspace_kind = 'team' and coalesce(operating_state, 'active') = 'active') as mine_active,
          count(*) filter (where owner = $1::text and workspace_kind = 'team' and coalesce(operating_state, 'active') = 'active' and attention = true) as mine_flagged
        from v_deal_room_board`, [actor.slug]),
      client.query(`select
          count(*) filter (where organization_tenant_id = $1::text and state = $2::text) as needs_viewer,
          count(*) filter (where organization_tenant_id = $1::text and state in ('claimed', 'in_progress', 'verification', 'awaiting_release', 'released') and executor_actor is not null and executor_actor not in ('joe', 'dell')) as doc_at_work,
          count(*) filter (where organization_tenant_id = $1::text and updated_at >= now() - interval '7 days') as changed_count,
          max(updated_at) filter (where organization_tenant_id = $1::text and updated_at >= now() - interval '7 days') as changed_at,
          count(*) filter (where organization_tenant_id is null and state in ('needs_joe', 'claimed', 'in_progress', 'verification', 'awaiting_release', 'released')) as legacy_unscoped_held,
          count(*) filter (where organization_tenant_id is null and updated_at >= now() - interval '7 days') as legacy_unscoped_recent
        from ops.work_request`, [tenant, actor.slug === "joe" ? "needs_joe" : "__no_needs_joe_state__"]),
    ]);
  } catch (error) {
    throw classifyReadError(error);
  }
  const deal = dealResult?.rows?.[0];
  const work = workResult?.rows?.[0];
  const teamActive = integer(deal, "team_active");
  const teamFlagged = integer(deal, "team_flagged");
  const mineActive = integer(deal, "mine_active");
  const mineFlagged = integer(deal, "mine_flagged");
  const needsViewer = integer(work, "needs_viewer");
  const docAtWork = integer(work, "doc_at_work");
  const changedCount = integer(work, "changed_count");
  const legacyHeld = integer(work, "legacy_unscoped_held");
  const legacyRecent = integer(work, "legacy_unscoped_recent");
  // Mine is a strict subset of team, so a count that breaks either the
  // flagged-within-active or the mine-within-team invariant is malformed and
  // must not be published as a fresh aggregate.
  if ([teamActive, teamFlagged, mineActive, mineFlagged].some((value) => value === null)) throw typedError("FRESHNESS_UNKNOWN");
  if (teamFlagged > teamActive || mineFlagged > mineActive || mineActive > teamActive || mineFlagged > teamFlagged) throw typedError("FRESHNESS_UNKNOWN");
  const dealSource = source({ source: "v_deal_room_board", sourceRef: "v_deal_room_board", observedAt, validUntil, correlationId });
  const workSafe = [needsViewer, docAtWork, changedCount, legacyHeld, legacyRecent].every((value) => value !== null) && legacyHeld === 0 && legacyRecent === 0;
  const workSource = source({ source: "ops.work_request", sourceRef: "ops.work_request", observedAt, validUntil, correlationId, freshness: workSafe ? "fresh" : "unknown" });
  const needs = [
    { kind: "team_flagged_deals", scope: "team", count: teamFlagged, destination: TEAM_FLAGGED_DESTINATION },
    { kind: "my_flagged_deals", scope: "mine", count: mineFlagged, destination: MY_FLAGGED_DESTINATION },
  ];
  if (workSafe && actor.slug === "joe" && needsViewer > 0) needs.push({ kind: "needs_joe_work", scope: "mine", count: needsViewer, destination: NEEDS_JOE_DESTINATION });
  const workUnavailable = { state: "unavailable" };
  return {
    viewer: actor.slug,
    needs_you_now: needs,
    // this_week and recent_calls are in the exact-key contract and carry nothing.
    // They are DECLARED empty, not left empty pending a query: the browser
    // validator (dealroom/js/workspace-command-center-model.js, emptyContractList)
    // refuses a non-empty value for either, so a later producer cannot quietly fill
    // one with an unvalidated shape that no renderer would ever show. Filling either
    // means declaring its element shape and its renderer in the same change.
    this_week: [],
    metrics: [
      { scope: "team", active_deals: teamActive, flagged_deals: teamFlagged, active_destination: TEAM_ACTIVE_DESTINATION, flagged_destination: TEAM_FLAGGED_DESTINATION, source: dealSource },
      { scope: "mine", active_deals: mineActive, flagged_deals: mineFlagged, active_destination: null, flagged_destination: MY_FLAGGED_DESTINATION, source: dealSource },
    ],
    recent_calls: [],
    doc_at_work: workSafe ? [{ kind: "active_nonhuman_work", count: docAtWork, source: workSource }] : [workUnavailable],
    recent_activity: workSafe ? [{ kind: "changed_work", count: changedCount, observed_at: work.changed_at || observedAt, source: workSource }] : [workUnavailable],
    source: source({ source: "command_center", sourceRef: "v_deal_room_board+ops.work_request", observedAt, validUntil, correlationId }),
  };
}
