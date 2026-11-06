import { readMailNeedsPartner, mailNeedsPartnerInputSchema } from '../mail-needs-partner.ts';
export default {
  source: 'mcp-server/src/mail-needs-partner.ts',
  createTools: () => ({
    'read-mail-needs-partner': {
      write: false,
      description: 'Rank recorded inbound pending email decisions for the verified partner mailbox. Recorded overdue dates first, upcoming dates next, unknown dates last. Returns reasons, versions and existing review command references. Perform the existing proposal review before confirming. Never sends or interprets email instructions. Unavailable differs from empty.',
      inputSchema: mailNeedsPartnerInputSchema,
      handler: readMailNeedsPartner,
    },
  }),
};
