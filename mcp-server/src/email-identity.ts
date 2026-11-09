type Identity = { mailbox_owner: string; mailbox_account: string; item_id: string };
type EmailContext = { lifecycle?: { state?: string }; [key: string]: unknown };
type Activity = { id?: string; kind?: string; email_context?: EmailContext | null };
type EmailReader = { query(sql: string, params: unknown[]): Promise<{ rows: Activity[] }> };
type Action = {
  activity_id?: string;
  thread_activity_id?: string;
  email_context?: { source_activity_id?: string };
  proposal_id?: string;
  event_id?: string;
};
type ErrorConstructor = new (fields: { error: string }) => Error;

// Keep intake retries and sent receipts on the same captured email identity.
export const EMAIL_IDENTITY_SCHEMA = {
  type: "object",
  additionalProperties: false,
  required: ["mailbox_owner", "mailbox_account", "item_id"],
  properties: {
    mailbox_owner: { type: "string", enum: ["joe", "dell"] },
    mailbox_account: { type: "string", format: "email", maxLength: 254 },
    item_id: { type: "string", minLength: 1, maxLength: 500, pattern: "\\S" },
  },
};

export async function lockEmailIdentity(c: EmailReader, mailbox_owner: string, mailbox_account: string, item: string) {
  const item_id = item.trim();
  await c.query("select pg_advisory_xact_lock(hashtextextended($1,0))", [
    JSON.stringify(["email", mailbox_owner, mailbox_account, item_id]),
  ]);
  return { mailbox_owner, mailbox_account, item_id };
}

export async function capturedEmail(c: EmailReader, identity: Identity, subject: string | null = null) {
  const rows = (
    await c.query(
      `select id,kind,email_context from activity
    where email_identity->>'mailbox_owner'=$1 and email_identity->>'mailbox_account'=$2
      and email_identity->>'item_id'=$3
      and ($4::text is null or email_identity->>'subject'=$4 or party_id::text=$4 or email_context->>'send_id' is not null)
    order by (email_context->'lifecycle' is not null) desc,occurred_at,id limit 1`,
      [identity.mailbox_owner, identity.mailbox_account, identity.item_id, subject],
    )
  ).rows;
  return rows[0];
}

export async function guardEmailAction(c: EmailReader, args: Action, ToolError: ErrorConstructor, lock = true) {
  const ids = [args.activity_id, args.thread_activity_id, args.email_context?.source_activity_id].filter(Boolean);
  if (!ids.length && !args.proposal_id && !args.event_id) return;
  const rows = (
    await c.query(
      `select email_context from activity where id=any($1::uuid[])
    or id=(select activity_id from email_proposal where id=$2)
    ${args.event_id ? "or id::text=(select new_value->>'activity_id' from v_email_thread_filing_event where id=$3)" : ""}
    order by id ${lock ? "for update" : ""}`,
      args.event_id ? [ids, args.proposal_id ?? null, args.event_id] : [ids, args.proposal_id ?? null],
    )
  ).rows;
  if (rows.some((row) => row.email_context?.lifecycle?.state && row.email_context.lifecycle.state !== "live"))
    throw new ToolError({ error: "email_not_live" });
}
