export const regions = ["transaction","receipt"];
const LABELS = {
	"RegisteredWrite": "Registered write",
	"EquivalentCommit": "Equivalent commit",
	"KeyedRawIngress": "Keyed raw ingress",
	"UnkeyedRawIngress": "Unkeyed raw ingress",
	"AnotherWrite": "Another write",
	"Rollback": "Rollback",
	"Commit": "Commit",
	"Replay": "Replay"
};

export const readerActions = [];
export const state = (v) => ({
  Quiet: "No new change or receipt",
  Open: "Change uncommitted, no receipt visible",
  Committed: "Change committed, receipt pending"
})[v.transaction];
export const step = (action) => LABELS[action] ?? action;
