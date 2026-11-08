export const regions = ['notificationVisibility','homePlacement','canonicalWatchSubject'];
const labels = {
  Acknowledge: 'Read the alert',
  Settle: 'Settle the card',
  Unsettle: 'Return the card to Inbox',
  Snooze: 'Snooze the card',
};
export const readerActions = Object.keys(labels);
export const state = v => `${v.notificationVisibility} alert | ${v.homePlacement} card | Work still open`;
export const step = action => labels[action] ?? action;
