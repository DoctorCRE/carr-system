-- Preserve whether a proposed follow-up date was supplied by the default rule.
-- rollback: source rollback may leave this additive column in place.
-- expand-contract: expand, nullable provenance preserves unknown historical dates.
-- lock-review: small capture proposal table; nullable column requires no row rewrite.
alter table capture_post_call_candidate add column due_defaulted boolean;
