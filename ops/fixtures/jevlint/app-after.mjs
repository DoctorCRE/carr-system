export function pendingCommand(state, operationKey) {
  return (state || {})[operationKey] || null;
}
