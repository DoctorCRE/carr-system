export function journeyOneClockDoorTools() {
  return {
    "read-journey-one-clock": {
      write: false,
      description: "Read the recorded Journey One start, pauses, deadline and history for a clock scope.",
      inputSchema: {
        type: "object", additionalProperties: false,
        properties: { clock_scope_key: { type: "string", pattern: "^sha256:[0-9a-f]{64}$" } },
        required: ["clock_scope_key"],
      },
      handler: async (c, _actor, args) => {
        const bindings = (await c.query("select ops.j1_clock_scope_bindings($1::text,$2::text) as bindings",
          [args.clock_scope_key, null])).rows[0]?.bindings;
        const clockKey = bindings?.by_scope?.clock_key ?? null;
        const clock = clockKey === null ? null : (await c.query(
          "select ops.j1_clock_history($1::text) as clock", [clockKey])).rows[0]?.clock ?? null;
        return { ok: true, clock_scope_key: args.clock_scope_key, clock_key: clockKey,
          clock_started: clock?.history?.events?.some(event => event.type === "clock_started") ?? false,
          clock };
      },
    },
  };
}
