-- Provision Joe's Studio capture device actor.
-- rollback: remove quill-joe-studio from the CAPTURE_TOKENS map to revoke claims.
--
-- WHAT THIS IS. POST /capture/claim authenticates a CAPTURE_TOKENS bearer,
-- then resolves its device slug in actor. The Studio bridge sends
-- quill-joe-studio; without this row every claim returns
-- device_actor_not_provisioned (503), including pipeline call recordings.
-- This uses the same columns, kind and active flag as quill-joe-mac.
--
-- THE SLUG IS THE DEVICE ID. This slug MUST equal the device_id the rig
-- sends and the key in CAPTURE_TOKENS. Three names, one string.
--
-- KIND. The device is automation, never human. It posts proposals;
-- resolve-candidate is humanOnly and refuses a device actor.
--
-- REVOCATION. Remove the device from the secret map to revoke claims.
-- The precedent also describes active=false as revocation, but the current
-- capture:device-actor query does not filter active. Do not rely on it here.
-- This migration contains no secret and does not change CAPTURE_TOKENS.

insert into actor (slug, kind, display_name, active)
values ('quill-joe-studio', 'automation', 'Quill capture rig (Joe Mac Studio)', true)
on conflict (slug) do nothing;
