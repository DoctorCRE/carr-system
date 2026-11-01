-- rollback: revoke select (version) on public.party from carr_reader; leave org_id, which 1020_email_deal_matching.sql granted first.
-- WR637.S1: vendor-assignment-context runs as carr_reader and reads the vendor party's row version
-- (the base_version update-party-contact needs) and org_id (to collect the company's domains).
-- A row version and an internal org id are not sensitive. Repeating 1020's org_id grant is a no-op where it applied.
grant select (version, org_id) on public.party to carr_reader;
