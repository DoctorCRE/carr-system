-- rollback: revoke select on public.v_email_reply_partner from carr_writer in a new migration.
-- expand-contract: expand; Home uses the existing limited partner-recipient view.
-- lock-review: privilege metadata only; no domain data changes or table locks.
set local lock_timeout = '5s';
set local statement_timeout = '30s';

grant select on public.v_email_reply_partner to carr_writer;
