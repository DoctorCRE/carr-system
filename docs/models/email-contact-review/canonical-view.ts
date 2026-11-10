export const title = "Two signatures sharing a Message-ID";
export const state = (v: { findings: readonly unknown[]; badMerge: boolean; badBinding: boolean; phase: string }) =>
  v.findings.length > 2
    ? "Duplicate proposals recorded"
    : v.badMerge
      ? "Ambiguous source merged"
      : v.badBinding
        ? "Changed evidence submitted"
        : v.phase === "resolve"
          ? "Checking signature source"
          : "Frozen proposals retained";
export const step = (a: string) => actions[a];
const actions: Record<string, string> = {
  Scan: "Read another mail representation",
  Resolve: "Resolve source and hold or record",
  Retry: "Retry after lost receipt",
  Restart: "Restart intake",
};
export const readerActions: string[] = [];
