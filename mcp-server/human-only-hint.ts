// The local client's answer to a human_only refusal.
//
// THE MISREADING THIS EXISTS TO KILL. A human_only verb refuses the local CLI
// no matter who is at the keyboard, because the local-token path derives an
// AGENT principal — human:false is set server-side and, per src/index.js,
// nothing on the wire can change it. Only a token minted through the OAuth
// connector against a verified partner email carries human:true.
//
// The server's own hint names the two paths but never says that, so a human
// standing at the prompt reads "reconnect through the interactive OAuth
// connector" as something they might satisfy by trying again. On 2026-08-14 a
// session told Joe to run `./run.sh call activate-rule ...` himself; he ran it,
// was refused, and the round trip taught neither of us anything the first
// error could not have said.
//
// Printed by the local client, so it costs no Worker deploy.

/** True only for a parsed tool-error payload whose error is human_only. */
export function isHumanOnlyError(payload: unknown): boolean {
  return payload !== null && typeof payload === "object" && "error" in payload && payload.error === "human_only";
}

/** What to actually do about it, with the caller's own verb already filled in. */
export function humanOnlyGuidance(verb?: unknown): string {
  const v = String(verb || "<verb>");
  return [
    "",
    "WHY THIS CANNOT WORK HERE, whoever is typing:",
    "  This terminal authenticates with the local token, which is an AGENT",
    "  principal (human:false, set server-side). Being a human at this prompt",
    "  does not satisfy a human_only verb — the gate cannot observe you.",
    "",
    "THE TWO PATHS THAT DO WORK:",
    "  1. An interactive Claude session signed in through the OAuth connector",
    "     (claude.ai or Cowork). That token carries a verified partner email and",
    "     is the designed route for a human decision.",
    `  2. A receipted local break-glass act, when no interactive session exists:`,
    "",
    `     CARR_BREAK_GLASS=1 ./run.sh call --reason "why this is the human's own decision" \\`,
    `       ${v} '<json args>'`,
    "",
    "  Break-glass writes a receipt to out/break-glass-receipts.log naming the",
    "  reason. Use it to CARRY a decision a partner has actually made — never to",
    "  supply one on their behalf.",
  ].join("\n");
}
