-- 0472: bind the heavy-build-protocol trigger rule to its pack-delivery enforcement,
-- following the 0289 precedent (delegation model-and-effort rule binding).
--
-- Rule 1fcaa63a-73d7-498d-9f76-e99bcd821a89 was taught from Joe's words on
-- 2026-09-01 ("my goal is to prompt a heavy build protocol and all these things
-- are triggered without me having to remind a session"), admitted the same day
-- with enforcement point pack_delivery (installed, verified), and approved by
-- Joe in chat: "I approve the heavy build protocol trigger rule". approve-rule
-- refuses until this binding row exists; that refusal is the enforcement-first
-- design working, and this migration is the enforcement half it demands.

insert into ops.rule_control_binding (rule_id, control_key, statement_hash, binding_contract)
select r.id, 'pack_delivery',
       encode(digest(r.statement, 'sha256'), 'hex'),
       jsonb_build_object(
         'source', 'migration 0472',
         'rule_id', r.id,
         'binding_moment', 'jit pack delivery when heavy-build work is detected: partner invocation of the protocol, or the admission machinery deriving work heavy',
         'canonical_reference', 'doctrine:heavy-build-protocol',
         'human_order', 'Joe 2026-09-01: I approve the heavy build protocol trigger rule')
  from rule r
 where r.id = '1fcaa63a-73d7-498d-9f76-e99bcd821a89'
   and r.status = 'proposed'
on conflict (rule_id, control_key) do nothing;
