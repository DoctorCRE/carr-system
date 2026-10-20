-- rollback: forward-only; the deal-room-board closed-deal read requires these grants.
grant select on public.v_client_account to carr_reader, carr_writer;
