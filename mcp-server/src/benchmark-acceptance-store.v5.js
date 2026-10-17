export function benchmarkAcceptanceStoreTools({ ToolError }) {
  return {
    "read-benchmark-manifest": {
      write: false,
      description: "Read the versioned benchmark inputs and recorded measurements. CI runs the benchmark thresholds.",
      inputSchema: {
        type: "object", additionalProperties: false,
        properties: { benchmark_ref: { type: "string" } }, required: ["benchmark_ref"],
      },
      handler: async (c, _actor, args) => {
        const row = (await c.query("select ops.benchmark_readback($1::text) as readback",
          [args.benchmark_ref])).rows[0]?.readback;
        if (!row) throw new ToolError({ error: "benchmark_readback_unavailable", benchmark_ref: args.benchmark_ref });
        return { ok: true, ...row };
      },
    },
  };
}
