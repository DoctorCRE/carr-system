import type { Actor, Db, VerbInputs } from './verb-contract.ts';
import { ToolError } from './tool-error.ts';
import { canExercisePartnerAuthority } from './partner-authority.js';
import { organizationTenantForActor } from './identity.js';
import { Validator, type Schema } from '@cfworker/json-schema';

const cursorSchema: Schema = { type: 'object', additionalProperties: false, properties: {
  query: { type: 'string', minLength: 1, maxLength: 100 },
  name: { type: 'string', maxLength: 10000 },
  id: { type: 'string', format: 'uuid' },
}, required: ['query','name','id'] };
const rosterSchema: Schema = {type:'object',additionalProperties:false,properties:{}};
const searchSchema: Schema = {type:'object',additionalProperties:false,properties:{
  query:{type:'string',minLength:1,maxLength:100},
  limit:{type:'integer',minimum:1,maximum:100,default:25},
  cursor:cursorSchema,
},required:['query']};
const rosterValidator = new Validator(rosterSchema);
const searchValidator = new Validator(searchSchema);

async function authorize(c: Db, actor: Actor) {
  if (!canExercisePartnerAuthority(actor)) throw new ToolError({error:'partner_required'});
  const tenant = organizationTenantForActor(actor);
  const {rows} = await c.query("select coalesce(nullif(current_setting('carr.organization_tenant_id',true),''),'carr-internal') tenant");
  if (tenant !== 'carr-internal' || rows[0].tenant !== tenant)
    throw new ToolError({error:'directory_tenant_refused'});
}

export function peopleDirectoryTools() {
  return {
    'read-actor-roster-v1': {
      write: false,
      description: 'Read the active canonical actor roster in the server-bound CARR tenant. Requires partner authority. Returns actor-roster.v1 with UUID, slug, display_name and kind, without contact data. Refuses above 1000 active actors. Read only.',
      inputSchema: rosterSchema,
      handler: async (c: Db, actor: Actor, args: VerbInputs['read-actor-roster-v1']) => {
        if (!rosterValidator.validate(args).valid) throw new ToolError({error:'invalid_directory_arguments'});
        await authorize(c,actor);
        const {rows} = await c.query('select id,slug,display_name,kind from ops.people_directory_actors_v1 order by lower(display_name) collate "C",id limit 1001');
        if (rows.length > 1000) throw new ToolError({error:'actor_roster_capacity_exceeded'});
        return {ok:true,schema_version:'actor-roster.v1',actors:rows};
      },
    },
    'search-parties-v1': {
      write: false,
      description: 'Search live canonical people and organizations by case-insensitive name prefix independently of email. Requires partner authority in the server-bound tenant. Returns party-search.v1 with UUID, display_name, kind and next_cursor. Limit defaults to 25, maximum 100. Replay next_cursor with the same query. Pages read current data; names changed between pages may move. Merged and deleted parties are excluded. Read only.',
      inputSchema: searchSchema,
      handler: async (c: Db, actor: Actor, args: VerbInputs['search-parties-v1']) => {
        if (!searchValidator.validate(args).valid) throw new ToolError({error:'invalid_directory_arguments'});
        await authorize(c,actor);
        const query=args.query.trim().toLowerCase();
        if (!query || (args.cursor && args.cursor.query !== query))
          throw new ToolError({error:'invalid_party_search_cursor_or_query'});
        const limit=args.limit ?? 25;
        const pattern=query.replace(/[\\%_]/g,'\\$&')+'%';
        const {rows} = await c.query(`select id,name as display_name,kind,lower(name) as sort_name from public.party
          where merged_into is null and deleted_at is null
            and lower(name) collate "C" like $1
            and (lower(name) collate "C",id) > ($2::text collate "C",$3::uuid)
          order by lower(name) collate "C",id limit $4`,
        [pattern,args.cursor?.name ?? '',args.cursor?.id ?? '00000000-0000-0000-0000-000000000000',limit+1]);
        const page=rows.slice(0,limit);
        const last=page.at(-1);
        return {ok:true,schema_version:'party-search.v1',
          parties:page.map(({id,display_name,kind})=>({id,display_name,kind})),
          next_cursor:rows.length>limit && last ? {query,name:last.sort_name,id:last.id} : null};
      },
    },
  };
}
