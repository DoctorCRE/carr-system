#!/bin/sh
# logitech-keymap.sh — repair swapped right-side modifiers on a Logitech
# keyboard (USB receiver 046d:c52b) that needs it.
#
# THE DEFECT, measured rather than guessed (CGEvent field 87 carries the
# originating HID service's registry id, which is how two keyboards emitting
# identical keycodes get told apart):
#   - the Command key immediately RIGHT of space emits RIGHT CONTROL (E4);
#   - the Control key farther right emits RIGHT GUI / Command (E7).
# The prior workaround repaired only the second key (E7 -> left Control) and
# taught Quill to accept right-Control too. That made the physical Control key
# the Quill trigger whenever the transient hidutil mapping was absent. Repair
# both directions instead so the labels, macOS semantics, and Quill all agree.
#
# PER-MACHINE OPT-OUT (added 2026-09-23, measured on Joe's Mac Studio): the
# receiver's vendor/product ids cannot tell two different physical Logitech
# keyboards apart, and not every one of them has the swapped defect above. On
# the Mac Studio the current Logitech keyboard already sends Right Control and
# Right GUI correctly, so applying the swap there reverses a keyboard that was
# already right (a CGEvent tap confirmed: with the swap active, the physical
# Command key right of space arrived as keycode 62 / Right Control instead of
# 54 / Right Command, so quill-dictate's trigger never fired from it). Since
# detection from HID ids alone is not possible, this is a per-machine marker
# instead: if $HOME/.config/carr/logitech-keymap.off exists (consistent with
# the per-machine marker convention in $HOME/.config/carr, e.g.
# lib/machine_role.py's machine-role.json), this script CLEARS any mapping for
# 046d:c52b and exits, leaving that keyboard's own correct HID usages alone.
# Every other machine — none of which carry the marker — keeps applying the
# swap exactly as before.
#
# THE REPAIR: for THIS vendor/product only, swap HID Right Control (E4) and
# Right GUI / Command (E7). The immediate-right Command becomes a real Command
# and the farther-right Control becomes a real Control. Scoped by
# VendorID/ProductID so no other keyboard — least of all the MacBook's — is
# touched. Fully reversible:
#   hidutil property --matching '{"VendorID":1133,"ProductID":50475}' \
#       --set '{"UserKeyMapping":[]}'
#
# hidutil mappings do NOT persist across reboot, sleep-with-replug, or the
# receiver being moved to another port, which is why com.carr.logitech-keymap
# runs this at login and re-applies periodically. The set is idempotent.
set -eu

MARKER="${HOME:-}/.config/carr/logitech-keymap.off"

if [ -e "$MARKER" ]; then
    /usr/bin/hidutil property \
        --matching '{"VendorID":1133,"ProductID":50475}' \
        --set '{"UserKeyMapping":[]}' \
        >/dev/null
    exit 0
fi

/usr/bin/hidutil property \
    --matching '{"VendorID":1133,"ProductID":50475}' \
    --set '{"UserKeyMapping":[{"HIDKeyboardModifierMappingSrc":0x7000000E4,"HIDKeyboardModifierMappingDst":0x7000000E7},{"HIDKeyboardModifierMappingSrc":0x7000000E7,"HIDKeyboardModifierMappingDst":0x7000000E4}]}' \
    >/dev/null
