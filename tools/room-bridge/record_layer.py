"""The record-layer posture admits CARR tools or refuses before launch."""
from desks import DeskError, tool_posture

REFUSALS = {
    'codex-session': 'Codex has no verified complete tool allowlist in this adapter; read-only still exposes non-CARR tools',
    'grok-cli': 'Grok has no verified strict MCP config override; --tools alone cannot exclude inherited MCP servers',
    'flash-local': 'Flash has no CARR MCP tool loop; its script and code routes cannot take record-layer work',
}


def enforce(entry, *, kind=None):
    kind = kind or entry['kind']
    tool_posture(kind, entry.get('posture'))
    if entry.get('posture') == 'record-layer' and kind in REFUSALS:
        raise DeskError('record_layer_unsupported', REFUSALS[kind])
