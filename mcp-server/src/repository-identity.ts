// Resolve exact repository identities from the shared progress registry.
import registry from "./progress-address.v1.json" with { type: "json" };

export const repositoryNames = Object.keys(registry.repositories);
const identities = new Map<string, string>(repositoryNames.map((name) => [name, name]));
for (const identity of Object.values(registry.repository_identities)) {
  for (const alias of identity.aliases) identities.set(alias, identity.canonical);
}
export const acceptedRepositories = [...identities.keys()];
export const canonicalRepository = (name: string) => identities.get(name) ?? null;

export const repositoryAliases = (name: string) =>
  Object.values(registry.repository_identities).find((identity) => identity.canonical === canonicalRepository(name))
    ?.aliases ?? [];
export const repositoryLookupName = (name: string) => repositoryAliases(name)[0] ?? canonicalRepository(name);
