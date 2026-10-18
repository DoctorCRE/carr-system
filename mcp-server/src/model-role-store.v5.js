export function modelRoleStoreTools({ ToolError }) {
  return {
    "read-model-role": {
      write: false,
      description: "Read the stored versions of a role description. Model Room configuration selects the model and effort.",
      inputSchema: {
        type: "object", additionalProperties: false,
        properties: { role_key: { type: "string" } }, required: ["role_key"],
      },
      handler: async (c, _actor, args) => {
        const readback = (await c.query("select ops.model_role_readback($1::text) as readback",
          [args.role_key])).rows[0]?.readback;
        if (!readback) throw new ToolError({ error: "model_role_readback_unavailable", role_key: args.role_key });
        return { ok: true, ...readback };
      },
    },
  };
}
