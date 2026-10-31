// Keep intake retries and sent receipts on the same captured email identity.
export const EMAIL_IDENTITY_SCHEMA = {
  type: 'object', additionalProperties: false, required: ['mailbox_owner', 'mailbox_account', 'item_id'],
  properties: { mailbox_owner: { type: 'string', enum: ['joe', 'dell'] },
    mailbox_account: { type: 'string', format: 'email', maxLength: 254 },
    item_id: { type: 'string', minLength: 1, maxLength: 500, pattern: '\\S' } },
};

export async function lockEmailIdentity(c, mailbox_owner, mailbox_account, item) {
  const item_id = item.trim();
  await c.query('select pg_advisory_xact_lock(hashtextextended($1,0))',
    [JSON.stringify(['email', mailbox_owner, mailbox_account, item_id])]);
  return { mailbox_owner, mailbox_account, item_id };
}

export async function capturedEmail(c, identity, subject = null) {
  return (await c.query(`select id,kind,email_context from activity
    where email_identity->>'mailbox_owner'=$1 and email_identity->>'mailbox_account'=$2
      and email_identity->>'item_id'=$3
      and ($4::text is null or email_identity->>'subject'=$4 or email_context->>'send_id' is not null)
    order by occurred_at,id limit 1`, [identity.mailbox_owner, identity.mailbox_account, identity.item_id, subject])).rows[0];
}
