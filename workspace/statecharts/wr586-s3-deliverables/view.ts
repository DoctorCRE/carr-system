interface DeliverableState {
  authorized: boolean;
  phase: string;
  shared: boolean;
  downloaded: boolean;
}

export const title = "Deal deliverables";
export const state = (v: DeliverableState): string =>
  !v.authorized
    ? "Session revoked"
    : v.phase === "finalized"
      ? `${v.shared ? "Shared" : "Private"}${v.downloaded ? ", downloaded" : ""}`
      : v.phase;
export const step = (action: string): string => {
  const labels: Record<string, string | null> = {
    Start: "Partner starts upload",
    Claim: "Partner sends bytes",
    Abort: "Rejected bytes roll back reservation",
    Expire: "Unused reservation expires",
    Verify: "Bytes, size, content and hash verified",
    Finalize: "Partner finalizes privately",
    Share: "Partner changes visibility with current version",
    Download: "Partner downloads",
    Revoke: "Session revoked",
    Refuse: null,
  };
  return labels[action] ?? action;
};
