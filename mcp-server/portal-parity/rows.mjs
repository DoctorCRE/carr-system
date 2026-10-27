// Parse `--rows` (IDs and inclusive ranges) against the inventory IDs.
const ID = /^([AV])-([GSTLA])(\d{2})$/;

function split(id) {
  const match = ID.exec(id);
  if (!match) throw new Error(`not an inventory ID: ${id}`);
  return { prefix: `${match[1]}-${match[2]}`, number: Number(match[3]) };
}

// Returns the selected IDs in inventory order. Every named ID and both ends of
// every range must exist; a range stays inside one site and one row group.
export function selectRows(spec, inventoryIds) {
  if (spec === undefined || spec === null) return [...inventoryIds];
  const known = new Set(inventoryIds);
  const wanted = new Set();
  const tokens = String(spec).split(',').map(token => token.trim()).filter(Boolean);
  if (!tokens.length) throw new Error('--rows names no rows');
  for (const token of tokens) {
    const [first, last, extra] = token.split('..');
    if (extra !== undefined) throw new Error(`bad row range: ${token}`);
    for (const id of last === undefined ? [first] : [first, last]) {
      if (!known.has(id)) throw new Error(`unknown inventory ID: ${id}`);
    }
    if (last === undefined) { wanted.add(first); continue; }
    const from = split(first), to = split(last);
    if (from.prefix !== to.prefix || from.number > to.number) throw new Error(`bad row range: ${token}`);
    for (const id of inventoryIds) {
      const row = split(id);
      if (row.prefix === from.prefix && row.number >= from.number && row.number <= to.number) wanted.add(id);
    }
  }
  return inventoryIds.filter(id => wanted.has(id));
}
