-- rollback: revoke select on public.v_last_touch from carr_writer.
-- Quiet follow-ups reads canonical contact-kind dates in a read-only writer transaction.
grant select on public.v_last_touch to carr_writer;
