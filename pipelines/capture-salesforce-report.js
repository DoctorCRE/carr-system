/* capture-salesforce-report.js — one-paste capture of a Salesforce Lightning report.
 *
 * WHY THIS EXISTS: report export is disabled on Joe's profile, so the pipeline is read
 * from the rendered page. Doing that by hand means re-deriving four non-obvious things
 * every time. They are all handled below:
 *   1. Lightning renders reports inside an IFRAME. Same-origin, so contentDocument works,
 *      but nothing in the top document sees the rows.
 *   2. ~237 shadow roots elsewhere on the page make get_page_text / querySelectorAll
 *      return nothing. Inside the iframe the grid is plain <tr>, so once you are in the
 *      right document it is ordinary DOM.
 *   3. Rows AND columns are virtualised — only what is on screen exists. You must scroll
 *      both axes and accumulate.
 *   4. The re-render is ASYNC. A synchronous scroll-then-read silently returns the old
 *      rows; this is the trap that makes a hand run look complete when it is not.
 *
 * Cells are keyed by COLUMN HEADER, not by index, because the visible column set changes
 * with horizontal scroll position — index arithmetic breaks, header names do not.
 *
 * USAGE (from a Claude Code session driving Chrome):
 *   1. Open the report and run this whole file via the javascript tool. It returns
 *      immediately with "started".
 *   2. Poll:  SF.status()   ->  {done:false, rows:N, passes:"3/8"} until done:true
 *   3. Fetch: SF.tsv(0)     ->  header + first chunk;  SF.tsv(1), SF.tsv(2)... until ""
 *      (chunked because the tool truncates long returns)
 *   4. Save the concatenated TSV to Automation/salesforce-deals-latest.tsv, then run
 *      ~/carr-system/run.sh salesforce-diff
 *
 * Verify before trusting: SF.status().rows must equal the report's own "Total Records".
 * If it is short, the run hit a lazy-render stall — call SF.go() again, it is additive.
 */
(() => {
  const SETTLE_MS = 700;           // per scroll step; the async re-render needs real time
  const sleep = ms => new Promise(r => setTimeout(r, ms));

  const doc = () => {
    for (const f of document.querySelectorAll('iframe')) {
      try { if (f.contentDocument && f.contentDocument.querySelector('tr')) return f.contentDocument; }
      catch (e) { /* cross-origin, skip */ }
    }
    return null;
  };

  const store = {};                // rowNumber -> {header: value}
  let headerOrder = [];            // first-seen column order, for stable TSV output

  const scrollers = (d, axis) => [...d.querySelectorAll('div')].filter(e =>
    axis === 'y' ? e.scrollHeight > e.clientHeight + 40
                 : e.scrollWidth  > e.clientWidth  + 40);

  /* ALIGNMENT — the subtle part, and the one that silently corrupts a capture if wrong.
   * Salesforce pads header rows with blank cells that data rows do not have (observed:
   * 16 header cells vs 14 data cells — one leading pad, one trailing). Positional
   * i-to-i mapping therefore shifts every column by one and looks plausible.
   * The invariant that DOES hold: strip the blank header cells to get N names, and a
   * data row's cells[1:] (cell 0 is always the row number) are exactly those N values
   * in order. If a pass ever violates that, the row is SKIPPED rather than written —
   * a missing row is visible in the row count; a shifted row is not. */
  function harvest(d) {
    let names = null;
    for (const tr of d.querySelectorAll('tr')) {
      const cells = [...tr.children].map(c => c.innerText.replace(/\s+/g, ' ').trim());
      if (cells.length < 3) continue;
      if (cells.some(v => v.includes('Column Actions'))) {          // header row
        names = cells.map(v => v.replace(' Column Actions', '').trim()).filter(Boolean);
        for (const c of names) if (!headerOrder.includes(c)) headerOrder.push(c);
        continue;
      }
      if (!names) continue;                                         // no header seen yet at this position
      const key = cells[0];
      if (!/^\d+$/.test(key)) continue;
      const values = cells.slice(1);
      if (values.length !== names.length) { window.SF._skipped++; continue; }
      const row = store[key] || (store[key] = {});
      names.forEach((h, i) => { if (values[i] !== '') row[h] = values[i]; });
      const opportunityIds = [...new Set([...tr.querySelectorAll('a[href]')].map(a =>
        (a.getAttribute('href') || '').match(/\/(006[A-Za-z0-9]{12}(?:[A-Za-z0-9]{3})?)(?=[/?#]|$)/)?.[1]).filter(Boolean))];
      if (opportunityIds.length === 1) {
        row['Opportunity ID'] = opportunityIds[0];
        if (!headerOrder.includes('Opportunity ID')) headerOrder.push('Opportunity ID');
      }
    }
  }

  async function go() {
    const d = doc();
    if (!d) { window.SF._err = 'report iframe not found — is the report open and finished loading?'; return; }
    const ys = scrollers(d, 'y'), xs = scrollers(d, 'x');
    const yStops = [...new Set(ys.flatMap(e => {
      const out = []; for (let t = 0; t <= e.scrollHeight; t += Math.max(200, e.clientHeight - 120)) out.push(t);
      out.push(e.scrollHeight); return out;
    }))].sort((a, b) => a - b);
    const xStops = xs.length ? [0, ...xs.map(e => e.scrollWidth)] : [0];

    window.SF._total = yStops.length * xStops.length;
    window.SF._done = 0;
    for (const x of xStops) {
      xs.forEach(e => { try { e.scrollLeft = x; } catch (err) {} });
      await sleep(SETTLE_MS);
      for (const y of yStops) {
        ys.forEach(e => { try { e.scrollTop = y; } catch (err) {} });
        await sleep(SETTLE_MS);
        harvest(d);
        window.SF._done++;
      }
    }
    window.SF._running = false;
  }

  window.SF = {
    _running: true, _done: 0, _total: 0, _err: null, _skipped: 0,
    go() { window.SF._running = true; window.SF._err = null; go(); return 'started'; },
    status() {
      return JSON.stringify({
        done: !window.SF._running,
        rows: Object.keys(store).length,
        columns: headerOrder.length,
        passes: `${window.SF._done}/${window.SF._total}`,
        skippedMisaligned: window.SF._skipped,
        error: window.SF._err
      });
    },
    /* Salesforce numbers its grand-total row like a data row, so it lands in `store`
       looking like deal #41. It is identified by having no value in the key column
       (a real deal always has a Deal Name) and is EXCLUDED from tsv() — but kept and
       exposed by totals(), because it carries the report's own sums and is therefore
       the cheapest possible check that the capture is complete and correct. */
    _dataKeys() {
      const key = headerOrder.includes('Deal Name') ? 'Deal Name' : null;
      return Object.keys(store).map(Number).sort((a, b) => a - b).filter(k =>
        key ? !!store[k][key] : Object.values(store[k]).filter(v => v).length > 3);
    },
    /* chunk 0 returns the header line first; later chunks are rows only. 12 rows/chunk
       keeps each return under the tool's truncation limit. */
    tsv(chunk = 0, per = 12) {
      const slice = window.SF._dataKeys().slice(chunk * per, (chunk + 1) * per);
      if (!slice.length) return '';
      const line = k => [k, ...headerOrder.map(h => (store[k][h] || '').replace(/\t/g, ' '))].join('\t');
      const body = slice.map(line).join('\n');
      return chunk === 0 ? ['row_index\t' + headerOrder.join('\t'), body].join('\n') : body;
    },
    /* The report's own totals row — compare against the figures shown at the top of the
       report before trusting any capture. */
    totals() {
      const all = Object.keys(store).map(Number).sort((a, b) => a - b);
      const data = window.SF._dataKeys();
      const t = all.filter(k => !data.includes(k)).map(k =>
        Object.fromEntries(Object.entries(store[k]).filter(([, v]) => v)));
      return JSON.stringify({ dataRows: data.length, totalsRow: t });
    },
    missing(expected) {                     // sanity check against the report's Total Records
      const have = window.SF._dataKeys();
      return JSON.stringify([...Array(expected)].map((_, i) => i + 1).filter(n => !have.includes(n)));
    }
  };
  go();
  return 'started';
})();
