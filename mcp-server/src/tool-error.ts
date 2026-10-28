// ToolError lives in a LEAF module on purpose.
//
// mcp.js maps any thrown value that is not a ToolError to the literal string
// "internal_error" (see mcp.js, the publicError fallback). So a module that cannot
// import this class cannot report a named failure, and its errors reach the
// operator as an unreadable "internal error".
//
// tools.js already imports situation-retrieval.js, so situation-retrieval.js
// importing ToolError back out of tools.js would be a cycle. Keeping the class
// here lets any module — including ones tools.js depends on — throw a typed,
// self-naming failure. tools.js re-exports it so existing imports keep working.
export type ToolErrorPayload = { error: string; [field: string]: unknown };

export class ToolError extends Error {
  declare payload: ToolErrorPayload;
  constructor(payload: ToolErrorPayload) { super(payload.error); this.payload = payload; }
}
