export const regions = ["notificationVisibility","homePlacement","canonicalWatchSubject"];
const LABELS = {
	"Acknowledge": "Acknowledge",
	"Settle": "Settle",
	"Unsettle": "Unsettle",
	"Snooze": "Snooze"
};

export const readerActions = [];
export const state = (v) => regions.map((r) => v[r]).join(" | ");
export const step = (action) => LABELS[action] ?? action;
