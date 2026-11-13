import { activityAttributionInputSchema, readActivityDealAttribution } from '../activity-deal-attribution.ts';
export default {
  source: 'mcp-server/src/activity-deal-attribution.ts',
  createTools: () => ({
    'read-activity-deal-attribution': {
      write: false,
      description: 'Explain the existing deal attribution for one exact activity UUID. Direct recorded attribution receives one credit. Explicit source-activity links are review candidates only. Multiple candidates remain ambiguous. Never matches names, relinks records, or writes activity.',
      inputSchema: activityAttributionInputSchema,
      handler: readActivityDealAttribution,
    },
  }),
};
