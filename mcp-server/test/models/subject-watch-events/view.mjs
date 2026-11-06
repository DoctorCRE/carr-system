export const state = v => !v.subscribed ? 'Unsubscribed' : v.delivered.length ? 'Notified' : 'Quiet';
export const step = a => ({Change:'source changes',Consume:'consume source',Acknowledge:'acknowledge',Unsubscribe:'unsubscribe',Scan:'continue bounded batch',Done:'idle'})[a] || a;
export const title = 'Watched record notifications';
