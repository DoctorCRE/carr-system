// The one Authorization parser every machine-authentication door uses
// (WR-000527, audit F6 in PR #2183).
//
// RFC 6750 §2.1: the case-insensitive scheme "Bearer", one or more spaces, and
// exactly one credential. The credential is any visible ASCII except the
// comma, which is how a repeated Authorization header arrives once joined.
// That is wider than RFC 6750's b64token on purpose: the Worker's existing
// token maps were minted outside this source, so a narrower alphabet could
// refuse a legitimate caller this file cannot see.
//
// Everything else is refused before a door reads its token map: a missing
// scheme (the bare secret), another scheme, an empty credential, a second
// credential, surrounding whitespace, tabs, controls and non-ASCII bytes.
const BEARER = /^bearer +([\x21-\x2b\x2d-\x7e]+)$/i;

/** The credential from an Authorization header value, or null when malformed. */
export function bearerCredential(header: unknown): string | null {
  if (typeof header !== "string") return null;
  return BEARER.exec(header)?.[1] ?? null;
}
