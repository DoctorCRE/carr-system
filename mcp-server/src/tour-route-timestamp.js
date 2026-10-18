// Route appointments round-trip through PostgreSQL timestamptz JSON, which
// uses numeric offsets and up to six fractional digits. Keep that precision
// intact for the database's locked-appointment equality check.
export function validRouteTimestamp(value) {
  if (typeof value !== "string" || value.length > 64) return false;
  const match = /^(\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2})(?:\.(\d{1,6}))?(Z|[+-](\d{2}):(\d{2}))$/.exec(value);
  // PostgreSQL has no year zero and accepts displacements only through 15:59.
  // This four-digit AD format deliberately supports years 0001 through 9999.
  if (!match || match[1].startsWith("0000-") ||
      (match[3] !== "Z" && (Number(match[4]) > 15 || Number(match[5]) > 59))) return false;
  // Validate the wall-clock fields separately so Date.parse's normalization
  // cannot quietly turn February 30 into a different appointment date.
  const wallClock = `${match[1]}.${(match[2] || "").padEnd(3, "0").slice(0, 3)}Z`;
  const wallTime = Date.parse(wallClock);
  return Number.isFinite(wallTime) && new Date(wallTime).toISOString() === wallClock && Number.isFinite(Date.parse(value));
}

// Call only after validRouteTimestamp. Date.parse retains milliseconds; add
// the remaining digits with integer arithmetic, including before the epoch.
export function routeTimestampMicroseconds(value) {
  const fraction = /\.(\d{1,6})/.exec(value)?.[1] || "";
  return BigInt(Date.parse(value)) * 1000n + BigInt(fraction.padEnd(6, "0").slice(3));
}
