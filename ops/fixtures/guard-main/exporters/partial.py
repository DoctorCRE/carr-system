"""Partial-file rendering — a generated block inside a hand-authored file.

Every other export target owns its whole file: the exporter writes it, a human
never touches it, and the do-not-hand-edit banner at the top is the whole
contract. This module is for the other shape, which the rule-gist index needs:
a file that is MOSTLY hand-written (CLAUDE.md) and carries ONE generated block.

The contract, and why each half of it is what it is:

* The block lives between two HTML-comment markers the human puts in the file
  once. The exporter replaces ONLY the bytes between them. Everything above the
  BEGIN line and below the END line comes back byte-identical, including the
  marker lines themselves — a human may add a note inside the comment and it
  survives.

* If the markers are ABSENT the exporter FAILS LOUDLY and writes nothing. It
  does not append, it does not create the file, it does not "helpfully" put the
  block at the end. A generated block that can appear anywhere is a generated
  block that will one day appear twice, and the second copy is the one that
  goes stale. The failure message carries the exact two lines to paste and the
  file to paste them into, so the fix is thirty seconds of a human's time.

* The host file is read from the LIVE path, always — never from the draft
  copy. The hand-authored 90% of the file only exists in the vault, so a
  draft dry run that read draft would splice into nothing. In draft mode
  the merged result is still WRITTEN to draft, which is what makes the dry
  run worth running: it shows the real merge without touching the vault.
"""

from pathlib import Path

BEGIN_PREFIX = "<!-- BEGIN GENERATED: {name}"
END_PREFIX = "<!-- END GENERATED: {name}"


class PartialRenderError(RuntimeError):
    """Raised when the host file cannot take the block. run_export turns this
    into a `failed` export_run row and leaves the previous file untouched."""


def marker_lines(name):
    """The two lines a human pastes into the host file, in canonical form."""
    return (
        f"<!-- BEGIN GENERATED: {name} — do not hand-edit between these markers; "
        f"regenerate with run.sh export -->",
        f"<!-- END GENERATED: {name} -->",
    )


def _locate(lines, prefix, host, name, which):
    hits = [i for i, ln in enumerate(lines) if ln.lstrip().startswith(prefix)]
    if len(hits) == 1:
        return hits[0]
    begin, end = marker_lines(name)
    if not hits:
        raise PartialRenderError(
            f"{host}: no {which} marker for generated block '{name}'. This target "
            f"renders INTO a hand-authored file and refuses to guess where the block "
            f"goes. Paste these two lines, in this order, where the block belongs:\n"
            f"    {begin}\n    {end}\n"
            f"then re-run the export."
        )
    raise PartialRenderError(
        f"{host}: {len(hits)} {which} markers for generated block '{name}' "
        f"(lines {', '.join(str(h + 1) for h in hits)}). Exactly one is required — "
        f"two blocks means one of them starts going stale today. Delete the extra."
    )


def splice(host_path: Path, name: str, block_lines):
    """Return the full text of `host_path` with `block_lines` between the markers.

    Reads nothing else and writes nothing. The caller writes the returned text
    to the exporter's temp path, so the poison gate in common.run_export still
    owns the atomic rename and the export_run row.
    """
    host_path = Path(host_path)
    if not host_path.exists():
        raise PartialRenderError(
            f"{host_path}: host file does not exist. This target renders a block "
            f"INTO an existing hand-authored file and never creates one."
        )
    text = host_path.read_text()
    lines = text.split("\n")
    b = _locate(lines, BEGIN_PREFIX.format(name=name), host_path, name, "BEGIN")
    e = _locate(lines, END_PREFIX.format(name=name), host_path, name, "END")
    if e < b:
        raise PartialRenderError(
            f"{host_path}: the END marker for '{name}' (line {e + 1}) comes before "
            f"the BEGIN marker (line {b + 1})."
        )
    return "\n".join(lines[: b + 1] + list(block_lines) + lines[e:])
