-- rollback: forward-only — the actor row carries the corrected partner address for future identity checks.

UPDATE public.actor
SET email = 'dell.mccraney@carr.us'
WHERE slug = 'dell'
  AND email IS NULL;
