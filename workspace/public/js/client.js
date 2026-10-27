// Load workspace fixtures and expose authored states.
const knownSurfaces = new Set([
  "command-center",
  "market-map",
  "deal-room",
  "call-review",
  "doc-request",
  "tour",
  "lead-board",
  "marketing",
  "more",
  "notifications"
]);

export const authoredStates = [
  "normal",
  "loading",
  "empty",
  "partial",
  "stale",
  "offline",
  "unauthorized",
  "conflict",
  "refusal",
  "retry"
];

export async function loadFixture(surface) {
  if (!knownSurfaces.has(surface)) throw new Error(`Unknown fixture surface: ${surface}`);
  const response = await fetch(`/fixtures/${surface}.v1.json`, {
    method: "GET",
    cache: "no-store",
    credentials: "same-origin"
  });
  if (!response.ok) throw new Error(`Fixture read failed with ${response.status}`);
  const fixture = await response.json();
  if (fixture.schema_version !== "workspace-fixture/v1" || fixture.synthetic !== true) {
    throw new Error("Fixture boundary validation failed");
  }
  return fixture;
}

export function isActionableState(state) {
  return state === "normal";
}
