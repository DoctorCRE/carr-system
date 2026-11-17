-- Keep one future email identity per mailbox independently of its contact activities.
-- rollback: drop activity.email_id, then public.email after stopping the new writer.
-- expand-contract: additive nullable link; historical rows are left unchanged.
-- lock-review: empty new table index; activity gets a nullable column without a rewrite.
CREATE TABLE public.email (
 id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
 mailbox_account text NOT NULL,
 message_id text CHECK (message_id = btrim(message_id))
);
CREATE UNIQUE INDEX email_mailbox_message_id ON public.email(mailbox_account,message_id)
 WHERE message_id IS NOT NULL;
ALTER TABLE public.activity ADD COLUMN email_id uuid REFERENCES public.email(id);
GRANT SELECT ON public.email TO carr_reader;
GRANT SELECT,INSERT ON public.email TO carr_writer;
DO $email_grants$
DECLARE contract jsonb := ops.scac_current_grant_contract();
BEGIN
 contract := jsonb_set(contract,'{relation_dml}',contract->'relation_dml' || jsonb_build_array(jsonb_build_object(
  'ingress_key','db-relation-acl:public.email:carr_writer:insert',
  'ingress_kind','db_relation_acl','relation','public.email',
  'relation_kind','r','grantee','carr_writer','privilege','insert','grantable',false)));
 EXECUTE format('create or replace function ops.scac_current_grant_contract() returns jsonb '
  'language sql immutable set search_path=pg_catalog as %L','select '||quote_literal(contract::text)||'::jsonb');
END $email_grants$;
