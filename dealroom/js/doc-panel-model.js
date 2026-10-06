/**
 * Doc panel — pure state/logic (V5-J101).
 *
 * Kept separate from doc-panel.js so the parsing, modality and receipt
 * formatting rules are testable without a DOM. Mirrors the split already used
 * for the record panel (workspace-business.js / workspace-business-model.js):
 * this file owns no DOM, no client, no timers.
 */

/**
 * Whether the panel currently behaves as a docked complementary region or a
 * full-screen modal dialog. Same rule as the record panel
 * (workspace-business-model.js panelModality): a panel that only COVERS the
 * page at narrow widths must trap focus and make the background inert there,
 * and must NOT do either when it sits beside usable content on a desktop.
 * @param {{open:boolean, phoneWidth:boolean}} args
 * @returns {'closed'|'modal'|'inline'}
 */
export function docPanelModality({ open, phoneWidth = false }) {
  if (!open) return 'closed';
  return phoneWidth ? 'modal' : 'inline';
}

/**
 * Escape only closes the panel when it is not pinned. Pinning is an explicit
 * "keep this open while I work" choice; Escape closing a pinned panel would
 * silently discard that choice the next time a modal-width visitor hits the
 * key out of habit.
 * @param {{pinned:boolean}} args
 */
export function escapeShouldClose({ pinned }) {
  return !pinned;
}

const COMMAND_PATTERN = /^\/([a-z_]+)\s*(.*)$/i;

/**
 * Split raw Doc composer text into a command name plus the remaining text.
 * `/set_next_step tomorrow we send the LOI` -> {command:'set_next_step', text:'tomorrow we send the LOI'}
 * Anything without a leading `/command` is a plain note — the single most
 * common daily action — never a guess at some other verb.
 * @param {string} raw
 * @returns {{command:string, text:string}}
 */
export function parseDocInput(raw) {
  const trimmed = String(raw ?? '').trim();
  const match = COMMAND_PATTERN.exec(trimmed);
  if (match) return { command: match[1].toLowerCase(), text: match[2].trim() };
  return { command: 'add_note', text: trimmed };
}

/**
 * A retry (or the original attempt) always runs against the deal it captured
 * at the moment it was made — never whatever deal happens to be open when
 * the button is clicked (see doc-panel.js `attempt`). When that captured
 * deal is no longer the open one, the reader is told plainly rather than
 * left thinking the line applied to whatever is currently on screen.
 * @param {{dealId?:string|null, label?:string}} capturedContext the context an attempt/retry actually ran against
 * @param {{dealId?:string|null}} currentContext the context the panel is showing right now
 * @returns {string} an empty string, or a parenthetical note to append to the receipt line
 */
export function retryContextDriftNote(capturedContext, currentContext) {
  const captured = capturedContext || {};
  const current = currentContext || {};
  if (!captured.dealId || current.dealId === captured.dealId) return '';
  return ` (ran against ${captured.label || captured.dealId}, no longer the open record)`;
}

/**
 * Turn a command receipt (see commands.js) into the tone/text a reader sees.
 * No status here is ever silently upgraded to success: unavailable, refused,
 * and conflict each keep their own distinct copy.
 * @param {{status:string, command:string, deal:string|null, detail:Object}} receipt
 */
export function formatReceiptLine(receipt) {
  const label = receipt.command.replaceAll('_', ' ');
  switch (receipt.status) {
    case 'ok':
      return { tone: 'ok', text: `${label} — confirmed${receipt.deal ? ` on ${receipt.deal}` : ''}.` };
    case 'conflict':
      return { tone: 'conflict', text: `${label} — someone else changed this first. Resolve the conflict before retrying.` };
    case 'unavailable':
      return { tone: 'unavailable', text: `${label} — unavailable. ${receipt.detail?.reason || 'This backend is not deployed yet.'}` };
    case 'refused':
    default:
      return { tone: 'refused', text: `${label} — not done. ${receipt.detail?.reason || 'The command was refused.'}` };
  }
}
