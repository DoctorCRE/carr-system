-- rollback: drop view public.v_email_reply_partner;
-- expand-contract: expose only active partner copy addresses to the read role.
-- lock-review: catalog-only view creation; no actor data rewrite.
create view public.v_email_reply_partner with (security_barrier=true) as
  select slug,email from public.actor
  where kind='human' and active and slug in ('joe','dell');

revoke all on public.v_email_reply_partner from public, dot_reader;
grant select on public.v_email_reply_partner to carr_reader;
