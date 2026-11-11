// Adapt the slug-bound proposal SQL contract to the retained Tour List ports.
import type { Db } from "./verb-contract.ts";

type Scope = { slug: string; digest: string; publication_id: string };
type PortalProposalSelection = { version: number; selected: string[]; notes: Record<string, string> };
export type PortalProposalRequest = PortalProposalSelection & { idempotency_key: string };
export type PortalProposalReceipt = {
  status: "success";
  schema_version: "portal-proposals.v1";
  publication_id: string;
  publication_version: number;
  selection: PortalProposalSelection;
  retired: string[];
};
class ProposalError extends Error {
  readonly status: number;
  constructor(code: string, status: number) {
    super(code);
    this.status = status;
  }
}
function parse(value: unknown): PortalProposalReceipt {
  const r = value as (Partial<PortalProposalReceipt> & { error?: string }) | null;
  if (r?.status !== "success") {
    const failure = value as { status?: string; error?: string } | null;
    throw new ProposalError(
      failure?.error ?? "proposal_unavailable",
      failure?.status === "denied" ? 401 : failure?.status === "conflict" ? 409 : 400,
    );
  }
  if (
    r.schema_version !== "portal-proposals.v1" ||
    !r.selection ||
    !Number.isSafeInteger(r.selection.version) ||
    !Array.isArray(r.selection.selected) ||
    !r.selection.notes ||
    !Array.isArray(r.retired) ||
    typeof r.publication_id !== "string" ||
    !Number.isInteger(r.publication_version)
  )
    throw Error("invalid_proposal_receipt");
  return r as PortalProposalReceipt;
}
export function portalProposalStorage(c: Db) {
  const call = async (scope: Scope, version: number | null, request: unknown) =>
    parse(
      (
        await c.query("select ops.portal_proposal($1::text,$2::text,$3::uuid,$4::integer,$5::jsonb) result", [
          scope.slug,
          scope.digest,
          scope.publication_id,
          version,
          request === null ? null : JSON.stringify(request),
        ])
      ).rows[0]?.result,
    );
  return {
    read: (scope: Scope) => call(scope, null, null),
    save: (scope: Scope, publicationVersion: number, request: unknown) => {
      if (request === null || typeof request !== "object" || Array.isArray(request))
        return Promise.reject(new ProposalError("invalid_proposal", 400));
      return call(scope, publicationVersion, request);
    },
  };
}
