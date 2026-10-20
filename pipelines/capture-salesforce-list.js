(() => {
  const result = { state: 'running' };
  window.__carrSalesforceRefresh = result;
  const clean = value => String(value ?? '').replace(/\s+/g, ' ').trim();
  const aliases = {
    'opportunity name': 'name', 'deal name': 'name', type: 'type', 'deal type': 'type',
    'close date': 'close', commission: 'commission', 'estimated commission': 'commission',
    stage: 'phase', phase: 'phase', 'transaction type': 'transaction', transaction: 'transaction',
    'opportunity owner': 'owner', 'owner full name': 'owner', owner: 'owner',
    'account name': 'company', 'company name': 'company', company: 'company',
  };
  const required = ['name', 'type', 'close', 'commission', 'phase', 'transaction', 'owner', 'company'];
  const pause = () => new Promise(resolve => setTimeout(resolve, 350));
  const fail = reason => { throw new Error(reason); };
  const initialUrl = location.href;
  const signedOut = () => document.querySelector('input[type="password"], #username, form[action*="login"]')
    || /\/secur\/|\/login(?:[/?#]|$)|login\.salesforce\.com/.test(location.href);
  const check = () => {
    if (signedOut()) fail('Salesforce sign-in needed');
    if (location.href !== initialUrl) fail('Salesforce page shape changed');
  };
  const count = () => {
    const counters = [...document.querySelectorAll('.countSortedByFilteredBy')];
    const values = counters.map(node => clean(node.textContent).match(/^([\d,]+) items?\b(?!\+)/)?.[1]);
    if (values.length !== 1 || !values[0]) fail('Salesforce page shape changed');
    const value = Number(values[0].replaceAll(',', ''));
    if (!Number.isSafeInteger(value) || value <= 0) fail('Salesforce page shape changed');
    return value;
  };
  (async () => {
    check();
    const grids = [...document.querySelectorAll('table[role="grid"], table[role="treegrid"]')];
    if (grids.length !== 1) fail('Salesforce page shape changed');
    const grid = grids[0], expected = count(), rows = new Map();
    const deadline = Date.now() + 90000;
    const scrollers = [];
    for (let node = grid; node; node = node.parentElement) scrollers.push(node);
    const harvest = () => {
      check();
      if (count() !== expected || Date.now() > deadline || !grid.isConnected) fail('Salesforce page shape changed');
      const headers = [...grid.querySelectorAll('thead th')].map((node, index) => ({
        index: node.getAttribute('aria-colindex') || String(index + 1),
        field: aliases[clean(node.querySelector('.slds-truncate')?.textContent || node.textContent)
          .replace(/\s+(?:sort|column actions).*$/i, '').toLowerCase()],
      }));
      if (!headers.length || new Set(headers.filter(h => h.field).map(h => h.field)).size !== headers.filter(h => h.field).length)
        fail('Salesforce page shape changed');
      const visible = new Set();
      for (const tr of grid.querySelectorAll('tbody tr')) {
        const ids = new Set([...tr.querySelectorAll('a[href]')].map(a =>
          a.getAttribute('href').match(/\/(006[A-Za-z0-9]{12}(?:[A-Za-z0-9]{3})?)(?=[/?#]|$)/)?.[1]).filter(Boolean));
        const rowKey = tr.getAttribute('data-row-key-value');
        if (/^006[A-Za-z0-9]{12}(?:[A-Za-z0-9]{3})?$/.test(rowKey ?? '')) ids.add(rowKey);
        if (ids.size !== 1) fail('Salesforce page shape changed');
        const id = [...ids][0];
        if (visible.has(id)) fail('Salesforce page shape changed');
        visible.add(id);
        const prior = rows.get(id) || { id };
        const cells = [...tr.children];
        if (cells.some(cell => !cell.hasAttribute('aria-colindex')) && cells.length !== headers.length)
          fail('Salesforce page shape changed');
        for (const [index, cell] of cells.entries()) {
          const header = headers.find(h => h.index === (cell.getAttribute('aria-colindex') || String(index + 1)));
          if (!header) fail('Salesforce page shape changed');
          if (!header.field) continue;
          const value = clean(cell.innerText);
          if (header.field in prior && prior[header.field] !== value) fail('Salesforce page shape changed');
          prior[header.field] = value;
        }
        rows.set(id, prior);
      }
      if (rows.size > expected) fail('Salesforce page shape changed');
    };
    const sweep = async (axis, visit) => {
      const position = axis === 'x' ? 'scrollLeft' : 'scrollTop';
      const extent = axis === 'x' ? 'scrollWidth' : 'scrollHeight';
      const viewport = axis === 'x' ? 'clientWidth' : 'clientHeight';
      scrollers.forEach(node => { node[position] = 0; });
      for (let step = 0; step < 500; step++) {
        await pause();
        await visit();
        let moved = false;
        for (const node of scrollers) {
          const maximum = node[extent] - node[viewport];
          if (node[position] < maximum) {
            node[position] += Math.max(100, node[viewport] - 80);
            moved = true;
          }
        }
        if (!moved) return;
      }
      fail('Salesforce page shape changed');
    };
    await sweep('x', () => sweep('y', harvest));
    await pause();
    harvest();
    if (rows.size !== expected || [...rows.values()].some(row => required.some(key => !(key in row))))
      fail('Salesforce page shape changed');
    Object.assign(result, { state: 'complete', expected_count: expected, rows: [...rows.values()] });
  })().catch(error => Object.assign(result, { state: 'failed', reason: error.message }));
  return 'started';
})();
