/* Regulations & agencies workbench (regsui.js).

   Two replacement page renderers, installed by areas.js as the "regulations" and "agencies"
   pages exactly the way statsviz.js is installed for "statistics":

       window.RegsUI = { regulations(signal, supplement), agencies(signal, supplement) }

   heading() has already run when either is called; both append into the global `main` and use
   only the script-level helpers app.js defines (el, append, api, action, link, routeLink,
   navigate, makeHash, count, date, human, badge, loading, emptyState, pagination, dataNote,
   cleanTitle, filterField, setOptions, announce, route, readRoute).

   Layout contract: each page appends exactly one `form.filters` as a DIRECT child of `main`
   before anything else; app.js moves that form into the left rail and puts everything appended
   after it to the right.

   Data rules, without exception:
     - Every number printed here came from an API response. Nothing is summed across agencies,
       no share, rate or average is computed, and a count is always labelled with what it counts
       and the date the collection was made.
     - Federal Register, eCFR amendment, GPO volume-marker, publisher-export and snapshot dates
       are separate facts and are never substituted for one another.
     - Text is written with textContent (el()); innerHTML is never used.

   The archive server sends "Content-Security-Policy: style-src 'self'", which blocks the text of
   any <style> element this page creates. A constructed stylesheet is CSSOM rather than inline
   style, so it is allowed; the <style> element below is only the fallback for engines without
   constructed stylesheets. Either way the rules are added once and every selector is "rg-". */
(function () {
  'use strict';

  const STYLE_ID = 'regsui-style';
  const HUB = '/api/agency-hub';
  const FR_AREA = '/api/area/federal-register';
  const DOC_AREA = '/api/area/agency-documents';

  /* ------------------------------------------------------------------------------- styles */
  const CSS = [
    '.rg{display:block}',
    /* headers, summaries, small print */
    '.rg-line{display:flex;flex-wrap:wrap;align-items:baseline;gap:4px 16px;margin:0 0 8px;font-size:11.5px;line-height:1.5;color:var(--muted)}',
    '.rg-line strong{color:var(--ink);font-size:12.5px;font-weight:650;font-variant-numeric:tabular-nums lining-nums}',
    '.rg-line .data-note{margin:0}',
    '.rg-head{display:flex;flex-wrap:wrap;align-items:baseline;justify-content:space-between;gap:8px;margin:0 0 8px}',
    '.rg-h{font:400 17px/1.3 Georgia,"Times New Roman",serif;letter-spacing:-.2px;margin:0}',
    '.rg-eyebrow{font-size:10px;font-weight:650;letter-spacing:.4px;text-transform:uppercase;color:var(--muted)}',
    '.rg-note{font-size:11.5px;line-height:1.55;color:var(--muted);margin:0}',
    '.rg-empty{font-size:12.5px;line-height:1.5;color:var(--muted);padding:12px;border:1px dashed var(--line);border-radius:8px;background:var(--surface)}',
    '.rg-warn{font-size:11.5px;line-height:1.5;color:var(--gold);margin:0 0 8px}',
    '.rg-block{margin:0 0 16px}',
    '.rg-block:last-child{margin-bottom:0}',
    /* dense tables */
    '.rg-scroll{overflow:auto;border:1px solid var(--line);border-radius:8px;background:var(--surface);-webkit-overflow-scrolling:touch;max-height:min(70vh,640px)}',
    '.rg-table{border-collapse:collapse;width:100%;font-size:12.5px}',
    '.rg-table th,.rg-table td{padding:6px 10px;text-align:left;border-bottom:1px solid var(--line);vertical-align:top;white-space:nowrap}',
    '.rg-table thead th{position:sticky;top:0;z-index:1;padding:0;background:#f4f7f4;box-shadow:inset 0 -1px 0 var(--line)}',
    '.rg-table thead th.rg-plain{padding:7px 10px;font-size:10px;font-weight:650;letter-spacing:.4px;text-transform:uppercase;color:#4e6660}',
    '.rg-table tbody tr:nth-child(even){background:#fafbf9}',
    '.rg-table tbody tr:hover,.rg-table tbody tr:focus-within{background:var(--teal-light)}',
    '.rg-table tbody tr:last-child td{border-bottom:0}',
    '.rg-table td.rg-num,.rg-table th.rg-num{text-align:right;font-variant-numeric:tabular-nums lining-nums}',
    '.rg-table td.rg-wrap{white-space:normal;min-width:220px;max-width:460px}',
    '.rg-table caption{caption-side:top;text-align:left;padding:7px 10px;font-size:10.5px;color:var(--muted);background:var(--surface)}',
    '.rg-sort{width:100%;border:0;background:transparent;padding:7px 10px;font:inherit;font-size:10px;font-weight:650;letter-spacing:.4px;text-transform:uppercase;color:#4e6660;display:flex;align-items:center;gap:5px;text-align:left}',
    '.rg-sort.rg-numhead{justify-content:flex-end}',
    '.rg-sort:hover{color:var(--teal-dark)}',
    '.rg-sort i{font-style:normal;font-size:9px;opacity:.4}',
    '.rg-table th[aria-sort] .rg-sort{color:var(--teal-dark)}',
    '.rg-table th[aria-sort] .rg-sort i{opacity:1}',
    '.rg-rowtitle{border:0;background:transparent;padding:0;font:inherit;font-size:12.5px;font-weight:650;color:var(--teal-dark);text-align:left;text-decoration:underline;text-underline-offset:3px}',
    'a.rg-rowtitle{display:inline-block}',
    '.rg-sub{font-size:10.5px;line-height:1.45;color:var(--muted);font-weight:400;margin-top:2px;white-space:normal;max-width:52ch}',
    '.rg-clamp{display:-webkit-box;-webkit-line-clamp:2;-webkit-box-orient:vertical;overflow:hidden}',
    '.rg-offscreen{position:absolute;width:1px;height:1px;padding:0;margin:0;border:0;opacity:0;pointer-events:none}',
    /* The shared pagination is a nowrap flex row; inside this page it is allowed to wrap so a
       narrow screen never pushes the document sideways. Only paginations rendered here change. */
    '.rg .pagination{flex-wrap:wrap;justify-content:flex-start;gap:8px 14px;padding-left:0;padding-right:0}',
    '.rg .pagination .button-row{flex-wrap:wrap}',
    '.rg-tag{display:inline-block;font-size:9.5px;font-weight:650;letter-spacing:.3px;text-transform:uppercase;color:#4e6660;background:#eef2f0;border-radius:3px;padding:1px 5px;margin:2px 4px 0 0;white-space:nowrap}',
    '.rg-tag.rg-on{background:var(--teal-light);color:var(--teal-dark)}',
    /* key figures */
    '.rg-kpis{display:grid;grid-template-columns:repeat(auto-fit,minmax(124px,1fr));gap:8px;margin:0 0 16px}',
    '.rg-kpi{border:1px solid var(--line);border-top:2px solid var(--teal);border-radius:6px;background:var(--surface);padding:7px 10px 8px;min-width:0}',
    '.rg-kpi-label{font-size:9.5px;font-weight:650;letter-spacing:.35px;text-transform:uppercase;color:var(--muted);display:block}',
    '.rg-kpi-value{display:block;font:400 21px/1.15 Georgia,"Times New Roman",serif;letter-spacing:-.4px;margin:4px 0 2px;font-variant-numeric:tabular-nums lining-nums}',
    '.rg-kpi-note{font-size:10px;line-height:1.4;color:var(--muted);display:block;overflow-wrap:anywhere}',
    /* underline tabs */
    '.rg-tabs{display:flex;gap:16px;align-items:stretch;border-bottom:1px solid var(--line);margin:0 0 12px;overflow-x:auto;scrollbar-width:none}',
    '.rg-tabs::-webkit-scrollbar{display:none}',
    '.rg-tab{padding:8px 1px 7px;font-size:12.5px;font-weight:650;color:var(--muted);text-decoration:none;border-bottom:2px solid transparent;white-space:nowrap;background:none;border-top:0;border-left:0;border-right:0}',
    '.rg-tab:hover{color:var(--teal-dark)}',
    '.rg-tab[aria-selected="true"]{color:var(--teal-dark);border-bottom-color:var(--teal)}',
    '.rg-tab span{font-weight:400;color:var(--muted);font-variant-numeric:tabular-nums}',
    '.rg-panel{display:block}',
    /* segmented toggle (a control, never navigation) */
    '.rg-toggle{display:inline-flex;border:1px solid #ccd8d1;background:#fff;border-radius:6px;padding:2px;gap:2px;height:28px}',
    '.rg-toggle button{border:0;background:transparent;border-radius:4px;font-size:11.5px;font-weight:650;color:var(--muted);padding:0 10px;height:22px;display:inline-flex;align-items:center}',
    '.rg-toggle button[aria-pressed="true"]{background:var(--teal-light);color:var(--teal-dark);box-shadow:inset 0 0 0 1px #cbe0d6}',
    '.rg-inline-field{display:inline-flex;align-items:center;gap:6px;font-size:11px;font-weight:650;color:#596c67}',
    '.rg-inline-field select{height:28px;background:#fff;border:1px solid #ccd8d1;border-radius:5px;padding:0 6px;color:var(--ink);font-size:12px;max-width:180px}',
    '.rg-actions{display:flex;flex-wrap:wrap;align-items:center;gap:8px;margin:0 0 8px}',
    '.rg-actions .button,.rg-actions .button-link{min-height:28px;height:28px;padding:0 10px;font-size:11.5px;display:inline-flex;align-items:center}',
    /* chart */
    '.rg-figure{margin:0;display:grid;gap:6px}',
    '.rg-chart{width:100%;height:auto;display:block;overflow:visible}',
    '.rg-fill-rule{fill:var(--teal-dark)}.rg-fill-proposed{fill:var(--teal)}.rg-fill-notice{fill:#9cc3b5}.rg-fill-other{fill:var(--gold)}',
    '.rg-axis{fill:var(--muted);font-size:8.5px;font-family:"Segoe UI",system-ui,sans-serif}',
    '.rg-grid{stroke:var(--line);stroke-width:1}',
    '.rg-legend{display:flex;flex-wrap:wrap;gap:4px 12px;font-size:10.5px;color:var(--muted);align-items:center}',
    '.rg-swatch{width:9px;height:9px;border-radius:2px;display:inline-block;margin-right:4px;vertical-align:-1px}',
    '.rg-cap{font-size:10.5px;line-height:1.45;color:var(--muted)}',
    /* ranked bars */
    '.rg-rank{display:grid;grid-template-columns:minmax(96px,40%) minmax(24px,1fr) max-content;align-items:center;gap:4px 8px}',
    '.rg-rank-label{font-size:12px;line-height:1.35;min-width:0;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}',
    '.rg-rank-label a{font-weight:650;text-decoration:none}',
    '.rg-rank-label a:hover{text-decoration:underline}',
    '.rg-rank-label small{color:var(--muted);font-weight:400}',
    '.rg-track{height:6px;border-radius:2px;background:#edf1ef;min-width:0}',
    '.rg-bar{display:block;height:100%;border-radius:2px;background:var(--teal);opacity:.85;min-width:2px}',
    '.rg-rank-value{font-size:11px;font-variant-numeric:tabular-nums lining-nums;color:var(--muted);text-align:right;white-space:nowrap}',
    /* browse strip (compact title rows, not cards) */
    '.rg-browse{border:1px solid var(--line);border-radius:8px;background:var(--surface);overflow:hidden;margin:0 0 16px}',
    '.rg-browse-row{display:grid;grid-template-columns:minmax(0,1fr) max-content;align-items:center;gap:8px;padding:7px 10px;border-bottom:1px solid var(--line);font-size:12.5px}',
    '.rg-browse-row:last-of-type{border-bottom:0}',
    '.rg-browse-open{border:0;background:transparent;padding:0;font:inherit;font-weight:650;color:var(--teal-dark);text-align:left;display:flex;align-items:center;gap:6px;min-width:0}',
    '.rg-browse-open i{font-style:normal;font-size:9px;opacity:.55;width:9px;flex:none}',
    '.rg-browse-open b{font-weight:650;white-space:nowrap}',
    '.rg-browse-open em{font-style:normal;font-weight:400;color:var(--muted);overflow:hidden;text-overflow:ellipsis;white-space:nowrap;min-width:0}',
    '.rg-browse-meta{font-size:10.5px;color:var(--muted);font-variant-numeric:tabular-nums;white-space:nowrap}',
    '.rg-parts{display:grid;grid-template-columns:repeat(auto-fill,minmax(210px,1fr));gap:2px 12px;padding:6px 10px 10px;background:#fafbf9;border-bottom:1px solid var(--line)}',
    '.rg-part{font-size:12px;line-height:1.5;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}',
    '.rg-part a{text-decoration:none}.rg-part a:hover{text-decoration:underline}',
    '.rg-part small{color:var(--muted)}',
    /* section reader */
    '.rg-read{display:grid;gap:12px}',
    '.rg-read-head{display:flex;flex-wrap:wrap;align-items:baseline;gap:6px 12px}',
    '.rg-read .rg-tabs{margin-bottom:0}',
    '.rg-text{white-space:pre-wrap;font:14px/1.62 Georgia,"Times New Roman",serif;color:var(--ink);max-width:76ch;margin:0;background:var(--surface);border:1px solid var(--line);border-radius:8px;padding:14px 16px;overflow-wrap:break-word}',
    '.rg-dl{display:grid;grid-template-columns:minmax(110px,max-content) minmax(0,1fr);gap:3px 14px;margin:0;font-size:12px;max-width:76ch}',
    '.rg-dl dt{color:var(--muted)}',
    '.rg-dl dd{margin:0;overflow-wrap:anywhere}',
    '.rg-timeline{list-style:none;margin:0;padding:0;font-size:12.5px;max-width:76ch}',
    '.rg-timeline li{padding:5px 0 5px 12px;border-left:2px solid var(--line);margin-left:2px}',
    '.rg-timeline li b{font-weight:650}',
    '.rg-timeline li span{color:var(--muted)}',
    /* responsive */
    /* styles.css currently repeats ".workbench{grid-template-columns:232px minmax(0,1fr)}" outside any
       media query and after the max-width:1000px rule that collapses it, so on a phone the results
       column is squeezed to about 116px on every page. This guard restores the single column for a
       workbench holding one of these two pages only; delete it once that duplicate rule is fixed. */
    '@media (max-width:1000px){.workbench:has(> .workbench-body > .rg){grid-template-columns:minmax(0,1fr)}}',
    '@media (max-width:760px){.rg-opt{display:none}.rg-kpis{grid-template-columns:repeat(auto-fit,minmax(112px,1fr))}}',
    '@media (max-width:520px){.rg-rank{grid-template-columns:minmax(72px,46%) minmax(16px,1fr) max-content}.rg-dl{grid-template-columns:1fr;gap:1px 0}.rg-dl dt{margin-top:6px}.rg-table td.rg-wrap{min-width:150px}}',
  ].join('\n');

  function installStyle() {
    const root = document.documentElement;
    if (root.dataset.regsuiStyle || document.getElementById(STYLE_ID)) return;
    if (typeof CSSStyleSheet === 'function' && Array.isArray(document.adoptedStyleSheets)) {
      try {
        const sheet = new CSSStyleSheet();
        sheet.replaceSync(CSS);
        document.adoptedStyleSheets = document.adoptedStyleSheets.concat(sheet);
        root.dataset.regsuiStyle = STYLE_ID;
        return;
      } catch (error) { /* fall through to the element */ }
    }
    const node = document.createElement('style');
    node.id = STYLE_ID;
    node.textContent = CSS;
    document.head.append(node);
    root.dataset.regsuiStyle = STYLE_ID;
  }

  /* -------------------------------------------------------------------------- tiny helpers */
  const hubCache = new Map();
  let hubMissing = false;            // true once /api/agency-hub has answered 404

  function params() {
    try { if (typeof route !== 'undefined' && route && route.params) return route.params; } catch (error) { /* fall through */ }
    try { return readRoute().params; } catch (error) { return new URLSearchParams(); }
  }
  function get(name) { return params().get(name) || ''; }
  function text(value) { return String(value === null || value === undefined ? '' : value).replace(/\s+/g, ' ').trim(); }
  function isMissing(error) { return /HTTP\s*404/.test(String((error && error.message) || error)); }
  function num(value) { return Number.isFinite(Number(value)) ? Number(value) : null; }

  async function hub(path, signal) {
    if (hubCache.has(path)) return hubCache.get(path);
    const payload = await api(path, signal);
    hubCache.set(path, payload);
    return payload;
  }
  // The agency-hub routes may not be installed yet; the page then says so in one line and keeps working.
  async function hubOrNull(path, signal) {
    if (hubMissing) return null;
    try { return await hub(path, signal); } catch (error) {
      if (error.name === 'AbortError') throw error;
      if (isMissing(error)) { hubMissing = true; return null; }
      throw error;
    }
  }
  function hubNotice(target, what) {
    const box = el('p', 'rg-warn', `The agency overview service is not installed on this server, so ${what} cannot be shown. `
      + 'Everything else on this page is unaffected.');
    target.append(box);
  }

  function heading2(title, eyebrow) {
    const box = el('div', 'rg-head'), left = el('div');
    if (eyebrow) left.append(el('div', 'rg-eyebrow', eyebrow));
    left.append(el('h2', 'rg-h', title));
    box.append(left);
    return box;
  }
  function dl(pairs) {
    const list = el('dl', 'rg-dl');
    for (const [key, value] of pairs) {
      if (value === null || value === undefined || value === '' || (Array.isArray(value) && !value.length)) continue;
      append(list, el('dt', '', key), el('dd', '', Array.isArray(value) ? value.join(', ') : String(value)));
    }
    return list;
  }
  function kpi(label, value, note) {
    const box = el('div', 'rg-kpi');
    append(box, el('span', 'rg-kpi-label', label), el('strong', 'rg-kpi-value', value === null || value === undefined ? '—' : count(value)),
      note ? el('span', 'rg-kpi-note', note) : null);
    return box;
  }
  function scrollTable(table, caption) {
    if (caption) { const c = el('caption', '', caption); table.prepend(c); }
    const box = el('div', 'rg-scroll'); box.append(table); return box;
  }
  function headRow(columns, state, onSort) {
    // columns: [{key,label,numeric,optional,sortable}]
    const row = el('tr');
    for (const column of columns) {
      const cell = el('th', (column.numeric ? 'rg-num' : '') + (column.optional ? ' rg-opt' : '') + (onSort && column.sortable !== false ? '' : ' rg-plain'));
      if (!onSort || column.sortable === false) { cell.textContent = column.label; row.append(cell); continue; }
      const button = el('button', 'rg-sort' + (column.numeric ? ' rg-numhead' : ''));
      button.type = 'button';
      append(button, el('span', '', column.label), el('i', '', state.key === column.key ? (state.dir > 0 ? '▲' : '▼') : '↕'));
      if (state.key === column.key) cell.setAttribute('aria-sort', state.dir > 0 ? 'ascending' : 'descending');
      button.addEventListener('click', () => onSort(column));
      cell.append(button);
      row.append(cell);
    }
    return append(el('thead'), row);
  }

  /* Underline tabs that switch in place and keep the address bar deep-linkable.
     Arrow keys move between tabs; the panel is rebuilt by build(panel, id). */
  function tabs(target, items, current, build, hashFor) {
    const bar = el('div', 'rg-tabs'); bar.setAttribute('role', 'tablist');
    const panel = el('div', 'rg-panel'); panel.setAttribute('role', 'tabpanel'); panel.tabIndex = -1;
    const anchors = [];
    let active = items.some(item => item.id === current) ? current : items[0].id;

    function show(id, focus) {
      active = id;
      for (const anchor of anchors) {
        const on = anchor.dataset.tab === id;
        anchor.setAttribute('aria-selected', on ? 'true' : 'false');
        anchor.tabIndex = on ? 0 : -1;
        if (on && focus) anchor.focus();
      }
      panel.replaceChildren();
      panel.setAttribute('aria-label', (items.find(item => item.id === id) || {}).label || '');
      try { build(panel, id); } catch (error) { panel.append(el('p', 'rg-empty', 'This section could not be drawn.')); }
      if (hashFor) { try { history.replaceState(null, '', hashFor(id)); } catch (error) { /* history is optional */ } }
    }
    items.forEach((item, index) => {
      const anchor = el('a', 'rg-tab');
      anchor.href = hashFor ? hashFor(item.id) : '#';
      anchor.dataset.tab = item.id;
      anchor.setAttribute('role', 'tab');
      anchor.id = 'rg-tab-' + item.id;
      anchor.append(document.createTextNode(item.label));
      if (item.hint !== undefined && item.hint !== null) anchor.append(el('span', '', ' ' + count(item.hint)));
      anchor.addEventListener('click', event => { event.preventDefault(); show(item.id, false); });
      anchor.addEventListener('keydown', event => {
        const step = event.key === 'ArrowRight' ? 1 : event.key === 'ArrowLeft' ? -1 : event.key === 'Home' ? -index : event.key === 'End' ? items.length - 1 - index : 0;
        if (!step && event.key !== 'Home' && event.key !== 'End') return;
        event.preventDefault();
        show(items[(index + step + items.length) % items.length].id, true);
      });
      anchors.push(anchor); bar.append(anchor);
    });
    append(target, bar, panel);
    show(active, false);
    return { show: id => show(id, false) };
  }

  /* -------------------------------------------------------------------------- record dialog */
  function openDialog(title, build) {
    const dialogNode = document.querySelector('#record-dialog');
    const body = document.querySelector('#record-body');
    if (!dialogNode || !body) return;
    if (!dialogNode.open) dialogNode.showModal();
    body.replaceChildren();
    const h = el('h2', '', title); h.id = 'record-title';
    body.append(h);
    try { build(body); } catch (error) { body.append(el('p', 'rg-empty', 'This record could not be drawn.')); }
    const close = document.querySelector('#close-record'); if (close) close.focus();
  }
  function copyButton(value) {
    return action('Copy citation', async event => {
      const button = event.currentTarget;
      let done = false;
      try { await navigator.clipboard.writeText(value); done = true; } catch (error) { done = false; }
      if (!done) {
        // The fallback field has to live inside the open dialog: a node outside it is inert, so it
        // cannot take focus and the copy would silently fail.
        try {
          const area = el('textarea', 'rg-offscreen'); area.value = value; area.setAttribute('readonly', 'readonly');
          area.setAttribute('aria-hidden', 'true'); area.tabIndex = -1;
          (button.parentNode || document.body).append(area);
          area.focus(); area.select(); area.setSelectionRange(0, value.length);
          done = document.execCommand('copy');
          area.remove(); button.focus();
        } catch (error) { done = false; }
      }
      button.textContent = done ? 'Citation copied' : 'Copy failed';
      announce(done ? value + ' copied.' : 'The citation could not be copied.');
      setTimeout(() => { if (button.isConnected) button.textContent = 'Copy citation'; }, 2500);
    }, 'button-link');
  }

  /* ------------------------------------------------------------- Federal Register documents */
  async function openFederalRegisterDocument(id) {
    let payload;
    try { payload = await api(`${FR_AREA}/item?id=${encodeURIComponent(id)}`); }
    catch (error) { announce('Could not open the Federal Register document.'); return; }
    openDialog(text(payload.title) || String(id), body => {
      if (payload.subtitle) body.append(el('p', 'rg-note', payload.subtitle));
      const plain = (payload.facts || []).filter(pair => !/\b(id|ids|hash|sha\d*)\b/i.test(String(pair[0])));
      const list = dl(plain.map(pair => [String(pair[0]), pair[1]]));
      if (list.children.length) body.append(list);
      const row = el('div', 'rg-actions');
      for (const item of payload.links || []) {
        const url = String((item && item.url) || '');
        if (!url) continue;
        if (url.startsWith('#')) {
          const anchor = el('a', 'button-link', item.label || 'Open');
          anchor.href = url;
          anchor.addEventListener('click', () => { const d = document.querySelector('#record-dialog'); if (d && d.open) d.close(); });
          row.append(anchor);
        } else { const anchor = link((item.label || 'Open') + ' ↗', url, true, 'button-link'); if (anchor) row.append(anchor); }
      }
      if (row.children.length) body.append(row);
      const note = dataNote(payload.qualification); if (note) body.append(note);
    });
  }

  /* ================================================================================ CHART ==
     A stacked column per year, drawn as inline SVG. Bar heights are the only arithmetic: a
     value is divided by the largest yearly total purely to pick a pixel height. */
  const TYPE_STYLE = [
    ['Rule', 'rg-fill-rule', 'var(--teal-dark)'],
    ['Proposed Rule', 'rg-fill-proposed', 'var(--teal)'],
    ['Notice', 'rg-fill-notice', '#9cc3b5'],
  ];
  function svgNode(name, attributes) {
    const node = document.createElementNS('http://www.w3.org/2000/svg', name);
    for (const [key, value] of Object.entries(attributes || {})) node.setAttribute(key, String(value));
    return node;
  }
  function stackedChart(rows, types) {
    const width = 720, height = 190, left = 34, right = 6, top = 10, bottom = 22;
    const plot = width - left - right, area = height - top - bottom;
    const max = rows.reduce((best, row) => Math.max(best, Number(row.total) || 0), 0) || 1;
    const step = plot / Math.max(rows.length, 1), barWidth = Math.max(2, Math.min(18, step - 2));
    const svg = svgNode('svg', {viewBox: `0 0 ${width} ${height}`, class: 'rg-chart', role: 'img', preserveAspectRatio: 'xMidYMid meet'});
    svg.setAttribute('aria-label', `Documents published per year, ${rows.length ? rows[0].year : ''} to ${rows.length ? rows[rows.length - 1].year : ''}, stacked by document type. The table view lists every value.`);

    for (const fraction of [0, 0.5, 1]) {
      const y = top + area - area * fraction;
      svg.append(svgNode('line', {x1: left, x2: width - right, y1: y, y2: y, class: 'rg-grid'}));
      const label = svgNode('text', {x: left - 5, y: y + 3, 'text-anchor': 'end', class: 'rg-axis'});
      label.textContent = count(Math.round(max * fraction));
      svg.append(label);
    }
    const known = TYPE_STYLE.map(entry => entry[0]);
    const order = TYPE_STYLE.filter(entry => types.includes(entry[0])).concat(types.some(t => !known.includes(t)) ? [['Other', 'rg-fill-other', 'var(--gold)']] : []);
    rows.forEach((row, index) => {
      const x = left + index * step + (step - barWidth) / 2;
      let y = top + area;
      for (const [name, className] of order) {
        const value = name === 'Other'
          ? Object.entries(row.counts || {}).reduce((sum, [k, v]) => sum + (known.includes(k) ? 0 : (Number(v) || 0)), 0)
          : Number((row.counts || {})[name]) || 0;
        if (!value) continue;
        const bar = Math.max(1, (value / max) * area);
        y -= bar;
        const rect = svgNode('rect', {x: x.toFixed(1), y: y.toFixed(1), width: barWidth.toFixed(1), height: bar.toFixed(1), class: className});
        const title = svgNode('title'); title.textContent = `${row.year} · ${name} ${count(value)}`;
        rect.append(title); svg.append(rect);
      }
      if (rows.length <= 12 || index % Math.ceil(rows.length / 11) === 0 || index === rows.length - 1) {
        const label = svgNode('text', {x: (x + barWidth / 2).toFixed(1), y: height - 7, 'text-anchor': 'middle', class: 'rg-axis'});
        label.textContent = row.year;
        svg.append(label);
      }
    });
    const legend = el('div', 'rg-legend');
    for (const [name, , colour] of order) {
      const item = el('span', '');
      const swatch = el('span', 'rg-swatch'); swatch.style.background = colour;
      item.append(swatch, document.createTextNode(name === 'Other' ? 'Other document types' : name + 's'));
      legend.append(item);
    }
    const figure = el('figure', 'rg-figure');
    append(figure, svg, legend);
    return figure;
  }
  function yearTable(rows, types) {
    const known = TYPE_STYLE.map(entry => entry[0]);
    const shown = TYPE_STYLE.filter(entry => types.includes(entry[0])).map(entry => entry[0]);
    const extra = types.some(t => !known.includes(t));
    const table = el('table', 'rg-table');
    const columns = [{key: 'year', label: 'Year'}].concat(shown.map(name => ({key: name, label: name + 's', numeric: true})));
    if (extra) columns.push({key: 'other', label: 'Other types', numeric: true});
    columns.push({key: 'total', label: 'All documents', numeric: true});
    table.append(headRow(columns, {}, null));
    const body = el('tbody');
    for (const row of rows.slice().reverse()) {
      const line = el('tr');
      line.append(el('td', '', row.year));
      for (const name of shown) line.append(el('td', 'rg-num', count(Number((row.counts || {})[name]) || 0)));
      if (extra) line.append(el('td', 'rg-num', count(Object.entries(row.counts || {}).reduce((sum, [k, v]) => sum + (known.includes(k) ? 0 : (Number(v) || 0)), 0))));
      line.append(el('td', 'rg-num', count(row.total)));
      body.append(line);
    }
    table.append(body);
    return scrollTable(table);
  }
  function chartBlock(rows, types, caption) {
    const box = el('div', 'rg-block');
    const bar = el('div', 'rg-actions');
    const toggle = el('div', 'rg-toggle'); toggle.setAttribute('role', 'group'); toggle.setAttribute('aria-label', 'Chart or table');
    const slot = el('div');
    let mode = 'chart';
    const chartButton = el('button', '', 'Chart'), tableButton = el('button', '', 'Table');
    chartButton.type = tableButton.type = 'button';
    function draw() {
      chartButton.setAttribute('aria-pressed', mode === 'chart' ? 'true' : 'false');
      tableButton.setAttribute('aria-pressed', mode === 'table' ? 'true' : 'false');
      slot.replaceChildren(mode === 'chart' ? stackedChart(rows, types) : yearTable(rows, types));
    }
    chartButton.addEventListener('click', () => { mode = 'chart'; draw(); });
    tableButton.addEventListener('click', () => { mode = 'table'; draw(); });
    append(toggle, chartButton, tableButton);
    bar.append(toggle);
    append(box, bar, slot, caption ? el('p', 'rg-cap', caption) : null);
    draw();
    return box;
  }
  function rankedBars(items, caption) {
    const box = el('div', 'rg-block');
    const max = items.reduce((best, item) => Math.max(best, Number(item.value) || 0), 0) || 1;
    const grid = el('div', 'rg-rank');
    for (const item of items) {
      const label = el('div', 'rg-rank-label');
      if (item.href) { const anchor = el('a', '', item.label); anchor.href = item.href; label.append(anchor); }
      else label.append(el('span', '', item.label));
      if (item.hint) { label.append(document.createTextNode(' ')); label.append(el('small', '', item.hint)); }
      if (item.title) label.title = item.title;
      const track = el('div', 'rg-track'), fill = el('span', 'rg-bar');
      fill.style.width = Math.max(2, ((Number(item.value) || 0) / max) * 100).toFixed(1) + '%';
      track.append(fill);
      append(grid, label, track, el('div', 'rg-rank-value', count(item.value)));
    }
    append(box, grid, caption ? el('p', 'rg-cap', caption) : null);
    return box;
  }

  /* =========================================================================== AGENCIES ==== */
  const AGENCY_TABS = [
    {id: 'overview', label: 'Overview'},
    {id: 'rules', label: 'Rules & notices'},
    {id: 'regulations', label: 'Regulations'},
    {id: 'safety', label: 'Safety & enforcement'},
    {id: 'documents', label: 'Documents'},
    {id: 'addresses', label: 'Official addresses'},
  ];
  const SAFETY_KEYS = ['dataset', 'q', 'firm', 'classification', 'product_code', 'date_type', 'dfrom', 'dto'];

  function agencyFilterForm(mode, rows, active) {
    const form = el('form', 'filters'); form.setAttribute('aria-label', mode === 'directory' ? 'Search the agency directory' : 'Choose an agency');
    if (mode === 'directory') {
      const q = filterField('Search agencies', 'q', 'search', get('q'), 'Name or short name');
      const sort = filterField('Sort by', 'sort', 'select', get('sort'));
      setOptions(sort.input, [
        {value: 'name', label: 'Agency name'}, {value: 'documents', label: 'Federal Register documents'},
        {value: 'rules', label: 'Rules'}, {value: 'cfr', label: 'CFR parts cited'},
        {value: 'safety', label: 'Safety records'}, {value: 'documents_saved', label: 'Saved documents'},
        {value: 'addresses', label: 'Known addresses'},
      ], 'Federal Register documents', get('sort'));
      append(form, q.label, sort.label);
    } else {
      const picker = filterField('Agency', 'agency', 'select', active);
      setOptions(picker.input, (rows || []).map(row => ({value: row.key, label: row.short_name ? `${row.short_name} — ${row.name}` : row.name})), 'Choose an agency', active);
      picker.input.addEventListener('change', () => navigate('agencies', {agency: picker.input.value}));
      append(form, picker.label);
    }
    const submit = el('button', 'button button-primary', 'Search'); submit.type = 'submit';
    append(form, submit, action('Clear', () => navigate('agencies')));
    form.addEventListener('submit', event => {
      event.preventDefault();
      const values = Object.fromEntries(new FormData(form));
      if (mode !== 'directory') return navigate('agencies', {agency: values.agency || active});
      navigate('agencies', values);
    });
    return form;
  }

  function agencyDirectory(target, rows) {
    const query = text(get('q')).toLowerCase();
    let visible = rows.filter(row => !query
      || String(row.name).toLowerCase().includes(query) || String(row.short_name || '').toLowerCase().includes(query)
      || String(row.parent || '').toLowerCase().includes(query));
    const columns = [
      {key: 'name', label: 'Agency'},
      {key: 'documents', label: 'Federal Register documents', numeric: true},
      {key: 'rules', label: 'Rules', numeric: true, optional: true},
      {key: 'proposed', label: 'Proposed', numeric: true, optional: true},
      {key: 'notices', label: 'Notices', numeric: true, optional: true},
      {key: 'cfr', label: 'CFR parts cited', numeric: true},
      {key: 'safety', label: 'Safety records', numeric: true},
      {key: 'documents_saved', label: 'Saved documents', numeric: true},
      {key: 'addresses', label: 'Known addresses', numeric: true},
    ];
    const value = {
      name: row => String(row.name).toLowerCase(),
      documents: row => num(row.counts.federal_register_documents),
      rules: row => num(row.counts.rules), proposed: row => num(row.counts.proposed_rules),
      notices: row => num(row.counts.notices), cfr: row => num(row.counts.cfr_parts_cited),
      safety: row => num(row.counts.safety_records), documents_saved: row => num(row.counts.saved_documents),
      addresses: row => num(row.counts.known_addresses),
    };
    const chosen = get('sort');
    const state = {key: ({name: 'name', documents: 'documents', rules: 'rules', cfr: 'cfr', safety: 'safety', documents_saved: 'documents_saved', addresses: 'addresses'})[chosen] || 'documents',
      dir: chosen === 'name' ? 1 : -1};
    const box = el('div', 'rg-block');

    function draw() {
      box.replaceChildren();
      const sorted = visible.slice().sort((a, b) => {
        const left = value[state.key](a), right = value[state.key](b);
        if (left === right) return String(a.name).localeCompare(String(b.name));
        if (left === null) return 1;
        if (right === null) return -1;
        return (left > right ? 1 : -1) * state.dir;
      });
      const table = el('table', 'rg-table');
      table.append(headRow(columns, state, column => { if (state.key === column.key) state.dir = -state.dir; else { state.key = column.key; state.dir = column.key === 'name' ? 1 : -1; } draw(); }));
      const body = el('tbody');
      for (const row of sorted) {
        const line = el('tr');
        const first = el('td', 'rg-wrap');
        const anchor = routeLink(row.short_name ? `${row.short_name} — ${row.name}` : row.name, 'agencies', {agency: row.key}, 'rg-rowtitle');
        first.append(anchor);
        if (row.covers_components) { const tag = el('span', 'rg-tag', 'includes components'); tag.title = `Federal Register figures include the documents filed by ${row.covers_components}.`; first.append(tag); }
        const sub = [];
        if (row.parent) sub.push(row.parent);
        if (row.latest_rule && row.latest_rule.date) sub.push(`latest rule ${date(row.latest_rule.date)}`);
        if (row.latest_rule && row.latest_rule.title) sub.push(text(row.latest_rule.title));
        if (sub.length) { const note = el('div', 'rg-sub rg-clamp', sub.join(' · ')); note.title = sub.join(' · '); first.append(note); }
        line.append(first);
        for (const column of columns.slice(1)) {
          const cell = el('td', 'rg-num' + (column.optional ? ' rg-opt' : ''));
          const raw = value[column.key](row);
          cell.textContent = raw === null ? '—' : count(raw);
          line.append(cell);
        }
        body.append(line);
      }
      table.append(body);
      box.append(scrollTable(table, `${visible.length} agencies. Every figure is a count of records this archive holds; select an agency for its sources and dates.`));
    }
    draw();
    if (!visible.length) { box.replaceChildren(); emptyState(box, 'No agency matches', 'Clear the search to see the full directory.', () => navigate('agencies')); }
    target.append(box);
  }

  /* ---- the safety-dataset browser this page has always offered, kept whole ---- */
  async function safetyDatasetPage(signal) {
    const data = await api('/api/agency/datasets', signal); if (signal.aborted) return;
    const status = data.status || {};
    const items = data.items || [];
    const form = el('form', 'filters'); form.setAttribute('aria-label', 'Search agency safety records');
    const q = filterField('Search', 'q', 'search', get('q'), 'Product, reason, firm or subject');
    const dataset = filterField('Dataset', 'dataset', 'select', get('dataset'));
    setOptions(dataset.input, items.map(item => ({value: item.dataset, label: `${item.label} (${count(item.rows)})`})), 'All datasets', get('dataset'));
    const firm = filterField('Firm', 'firm', 'search', get('firm'), 'Firm name contains');
    const classification = filterField('Classification', 'classification', 'select', get('classification'));
    setOptions(classification.input, ['Class I', 'Class II', 'Class III'].map(v => ({value: v, label: v})), 'Any class', get('classification'));
    const current = items.find(item => item.dataset === get('dataset'));
    const dateType = filterField('Date type', 'date_type', 'select', get('date_type'));
    setOptions(dateType.input, (current ? current.date_types || [] : [...new Set(items.flatMap(item => item.date_types || []))]).map(v => ({value: v, label: human(v)})), 'Choose a date field', get('date_type'));
    const from = filterField('From', 'dfrom', 'date', get('dfrom')), to = filterField('To', 'dto', 'date', get('dto'));
    const submit = el('button', 'button button-primary', 'Search'); submit.type = 'submit';
    append(form, q.label, dataset.label, firm.label, classification.label, dateType.label, from.label, to.label, submit,
      action('Clear', () => navigate('agencies', {datasets: '1'})));
    form.addEventListener('submit', event => { event.preventDefault(); navigate('agencies', Object.assign({datasets: '1'}, Object.fromEntries(new FormData(form)))); });
    dataset.input.addEventListener('change', () => { const values = Object.fromEntries(new FormData(form)); delete values.date_type; navigate('agencies', Object.assign({datasets: '1'}, values)); });
    main.append(form);
    const page = el('div', 'rg');
    main.append(page);

    const line = el('div', 'rg-line');
    append(line, el('strong', '', count(status.records)), el('span', '', `records across ${count(status.datasets)} datasets, as published`),
      routeLink('← Agency directory', 'agencies', {}, 'button-link'));
    page.append(line);
    const note = dataNote(`${status.qualification || ''} Every publisher date is kept as its own field; a recall, letter or shortage entry is an agency record about a product or firm, not a finding of defect or liability.`);
    if (note) page.append(note);

    if (!SAFETY_KEYS.some(key => get(key))) {
      const table = el('table', 'rg-table');
      table.append(headRow([{key: 'a', label: 'Dataset'}, {key: 'b', label: 'Records', numeric: true},
        {key: 'c', label: 'Publisher data as of'}, {key: 'd', label: 'Captured'}, {key: 'e', label: 'Original file'}], {}, null));
      const body = el('tbody');
      for (const item of items) {
        const line2 = el('tr'), first = el('td', 'rg-wrap');
        first.append(routeLink(item.label, 'agencies', {datasets: '1', dataset: item.dataset}, 'rg-rowtitle'));
        first.append(el('div', 'rg-sub', item.publisher || ''));
        line2.append(first, el('td', 'rg-num', count(item.rows)),
          el('td', '', item.source_as_of ? date(item.source_as_of) : 'Not recorded'),
          el('td', '', item.captured_at ? date(item.captured_at) : 'Not recorded'));
        const files = el('td', '');
        for (const file of (item.original_files || []).slice(0, 1)) if (file.file_id) {
          const anchor = link(`Original ↗ (${Math.round((file.bytes || 0) / 1048576)} MB)`, `/agency-files/${encodeURIComponent(file.file_id)}`, false, 'button-link');
          if (anchor) files.append(anchor);
        }
        line2.append(files);
        body.append(line2);
      }
      table.append(body);
      page.append(scrollTable(table, `${items.length} datasets. Select one to browse its records.`));
      return;
    }

    const query = new URLSearchParams();
    for (const key of SAFETY_KEYS) if (get(key)) query.set(key, get(key));
    query.set('limit', '25'); query.set('page', String(Math.max(1, Number(get('page')) || 1)));
    const results = await api(`/api/agency/search?${query}`, signal); if (signal.aborted) return;
    if (!results.available) { page.append(el('p', 'rg-empty', results.reason || 'Agency data is not available.')); return; }
    if (!(results.results || []).length) { emptyState(page, 'No matching agency records', 'Try a broader search, another dataset or remove the date filter.', () => navigate('agencies', {datasets: '1'})); return; }
    const table = el('table', 'rg-table');
    table.append(headRow([{key: 'a', label: 'Record'}, {key: 'b', label: 'Firm'}, {key: 'c', label: 'Class / status'}, {key: 'd', label: 'Dates as published'}], {}, null));
    const body = el('tbody');
    for (const row of results.results) {
      const line3 = el('tr'), first = el('td', 'rg-wrap');
      const open = action(text(row.title) || row.native_id, async () => {
        let full;
        try { full = await api(`/api/agency/record?dataset=${encodeURIComponent(row.dataset)}&id=${encodeURIComponent(row.id)}`); }
        catch (error) { announce('Could not open the agency record.'); return; }
        openDialog(text(full.title) || text(row.title) || row.native_id, target => {
          const spec = items.find(item => item.dataset === row.dataset);
          target.append(el('p', 'rg-note', `${(spec && spec.label) || human(row.dataset)} · ${row.native_id}${full.publisher ? ' · ' + full.publisher : ''}`));
          if (full.dates) { target.append(el('h3', 'rg-eyebrow', 'Dates as published')); target.append(dl(Object.entries(full.dates).map(([k, v]) => [human(k), v ? date(v) : 'Not recorded']))); }
          target.append(el('h3', 'rg-eyebrow', 'Publisher fields'));
          target.append(dl(Object.entries(full.fields || {}).filter(([, v]) => typeof v !== 'object' || Array.isArray(v)).map(([k, v]) => [human(k), v]).slice(0, 80)));
          const row2 = el('div', 'rg-actions');
          for (const item of (Array.isArray(full.links) ? full.links : [])) if (item && item.url) { const anchor = link((item.label || 'Publisher link') + ' ↗', item.url, true, 'button-link'); if (anchor) row2.append(anchor); }
          if (full.original_file && full.original_file.file_id) { const anchor = link('Original bulk file ↗', `/agency-files/${encodeURIComponent(full.original_file.file_id)}`, false, 'button-link'); if (anchor) row2.append(anchor); }
          if (row2.children.length) target.append(row2);
          const note2 = dataNote(full.qualification || 'Agency record as published; dates are the publisher\'s own fields.');
          if (note2) target.append(note2);
        });
      }, 'rg-rowtitle');
      first.append(open, el('div', 'rg-sub', `${human(row.dataset)} · ${row.native_id}${row.snippet ? ' · ' + text(row.snippet) : ''}`));
      line3.append(first);
      const firmCell = el('td', '');
      if (row.firm) firmCell.append(routeLink(row.firm, 'agencies', {datasets: '1', firm: row.firm, dataset: get('dataset')}, 'button-link'));
      line3.append(firmCell, el('td', '', [row.classification, row.status].filter(Boolean).join(' · ')));
      const dates = el('td', '');
      for (const [key, value] of Object.entries(row.dates || {})) if (value) dates.append(el('div', 'rg-sub', `${human(key)}: ${date(value)}`));
      line3.append(dates);
      body.append(line3);
    }
    table.append(body);
    append(page, scrollTable(table, `${count(results.total)} agency records; publisher-reported records, not legal or medical conclusions.`),
      pagination(results.total, Number(results.page) || 1, Number(results.limit) || 25));
  }

  /* ---- one agency profile ---- */
  function agencyProfile(target, item, rows) {
    const counts = item.counts || {};
    const asOf = item.as_of || {};
    const identity = el('div', 'rg-block');
    append(identity,
      el('p', 'rg-eyebrow', [item.short_name, item.parent || 'Independent agency'].filter(Boolean).join(' · ')),
      el('h2', 'rg-h', item.name),
      item.note ? el('p', 'rg-note', item.note) : null,
      item.covers_components ? el('p', 'rg-note', `Its Federal Register figures below therefore also cover ${item.covers_components}.`) : null);
    const crumbs = el('div', 'rg-actions');
    crumbs.append(routeLink('← All agencies', 'agencies', {}, 'button-link'));
    if (item.links && item.links.federal_register) { const a = el('a', 'button-link', 'Federal Register documents →'); a.href = item.links.federal_register; crumbs.append(a); }
    if (item.links && item.links.regulations) { const a = el('a', 'button-link', 'Regulations →'); a.href = item.links.regulations; crumbs.append(a); }
    identity.append(crumbs);
    target.append(identity);

    const tiles = el('div', 'rg-kpis');
    append(tiles,
      kpi('Federal Register documents', counts.federal_register_documents, `${asOf.federal_register_span || ''} index, collected ${asOf.federal_register ? date(asOf.federal_register) : 'date not recorded'}`),
      kpi('Rules', counts.rules, 'Documents the index types as a rule'),
      kpi('Proposed rules', counts.proposed_rules, 'Documents typed as a proposed rule'),
      kpi('CFR parts cited', counts.cfr_parts_cited, 'Distinct parts named by those documents'),
      kpi('Safety records', counts.safety_records, counts.safety_datasets ? `${count(counts.safety_datasets)} publisher datasets` : 'No safety dataset for this agency'),
      kpi('Saved documents', counts.saved_documents, 'Files downloaded from the agency'),
      kpi('Known addresses', counts.known_addresses, 'Addresses found in saved discovery lists'));
    target.append(tiles);

    const chosen = get('tab');
    tabs(target, AGENCY_TABS.map(tab => Object.assign({}, tab, {hint: tabHint(tab.id, item)})), chosen,
      (panel, id) => agencyPanel(panel, id, item),
      id => makeHash('agencies', {agency: item.key, tab: id}));

    const note = dataNote(item.qualification || '');
    if (note) target.append(note);
  }
  function tabHint(id, item) {
    const counts = item.counts || {};
    if (id === 'rules') return counts.federal_register_documents || null;
    if (id === 'regulations') return (item.cfr && item.cfr.slice_parts ? item.cfr.slice_parts.length : 0) || null;
    if (id === 'safety') return (item.safety_datasets || []).length || null;
    if (id === 'documents') return counts.saved_documents || null;
    if (id === 'addresses') return counts.known_addresses || null;
    return null;
  }

  function agencyPanel(panel, id, item) {
    const asOf = item.as_of || {};
    if (id === 'overview') {
      const years = item.fr_by_year || [];
      if (years.length) {
        panel.append(heading2('Documents published per year, by type', 'Federal Register'));
        panel.append(chartBlock(years, item.fr_types || [],
          `Documents the Federal Register index lists for this agency, ${years[0].year}–${years[years.length - 1].year}. Index collected ${asOf.federal_register ? date(asOf.federal_register) : 'date not recorded'}; the final year is partial because collection stopped on that date.`));
      } else panel.append(el('p', 'rg-empty', 'No Federal Register documents are recorded for this agency in the saved index.'));
      const parts = item.top_cfr_parts || [];
      if (parts.length) {
        panel.append(heading2('CFR parts these documents cite most often', 'Rule history'));
        panel.append(rankedBars(parts.map(part => ({
          label: `${part.title} CFR ${part.part}`, value: part.documents, href: part.rule_history_link,
          hint: part.heading ? text(part.heading) : '', title: part.heading ? `${part.title} CFR ${part.part} — ${text(part.heading)}` : '',
        })), 'Top 15 parts by the number of documents that name them. A document that names a part may propose, amend, correct or only discuss it; select a part for its full document list.'));
      }
      return;
    }
    if (id === 'rules') {
      const documents = item.latest_documents || [];
      if (!documents.length) { panel.append(el('p', 'rg-empty', 'No Federal Register documents are recorded for this agency in the saved index.')); return; }
      const bar = el('div', 'rg-actions');
      const field = el('label', 'rg-inline-field'); field.append(document.createTextNode('Type'));
      const select = el('select'); select.setAttribute('aria-label', 'Document type');
      const kinds = [...new Set(documents.map(document => document.type).filter(Boolean))];
      setOptions(select, kinds.map(kind => ({value: kind, label: kind})), 'All types', '');
      field.append(select);
      const more = el('a', 'button-link', 'Open the full list →');
      bar.append(field, more);
      const slot = el('div');
      function draw() {
        const kind = select.value;
        more.href = (item.links && item.links.federal_register ? item.links.federal_register : '#federal-register') + (kind ? '&type=' + encodeURIComponent(kind) : '');
        more.textContent = kind ? `Open every ${kind.toLowerCase()} →` : `Open all ${count((item.counts || {}).federal_register_documents)} documents →`;
        const rows = documents.filter(document => !kind || document.type === kind);
        const table = el('table', 'rg-table');
        table.append(headRow([{key: 'a', label: 'Document'}, {key: 'b', label: 'Type'}, {key: 'c', label: 'Published'}, {key: 'd', label: 'Citation'}], {}, null));
        const body = el('tbody');
        for (const document_ of rows) {
          const line = el('tr'), first = el('td', 'rg-wrap');
          const open = action(text(document_.title) || document_.id, () => openFederalRegisterDocument(document_.id), 'rg-rowtitle');
          first.append(open);
          line.append(first, el('td', '', document_.type || ''), el('td', '', document_.date ? date(document_.date) : ''), el('td', '', document_.citation || ''));
          body.append(line);
        }
        table.append(body);
        slot.replaceChildren(rows.length
          ? scrollTable(table, `The ${rows.length} most recent of this agency's documents in the saved index; select one to read what the index records.`)
          : el('p', 'rg-empty', 'None of the twelve most recent documents has that type. Open the full list for every document of this type.'));
      }
      select.addEventListener('change', draw);
      append(panel, bar, slot);
      draw();
      return;
    }
    if (id === 'regulations') {
      const cfr = item.cfr || {};
      const chapters = cfr.chapters || [];
      if (chapters.length) {
        panel.append(heading2('CFR titles and chapters this agency administers', 'Code of Federal Regulations'));
        const row = el('div', 'rg-actions');
        for (const chapter of chapters) {
          const pieces = String(chapter).split(':');
          const anchor = el('a', 'button-link', `Title ${pieces[0]}, chapter ${pieces[1]}`);
          anchor.href = makeHash('regulations', {title: pieces[0]});
          row.append(anchor);
        }
        panel.append(row);
        panel.append(el('p', 'rg-cap', `${cfr.basis || ''}${cfr.parts_in_referenced_chapters ? ' · ' + count(cfr.parts_in_referenced_chapters) + ' parts sit in those chapters' : ''}. Only Titles 16, 21, 40 and 49 are held in full by this archive.`));
      }
      const slice = cfr.slice_parts || [];
      if (slice.length) {
        panel.append(heading2(`Parts held in full (${count(slice.length)})`, 'In this archive'));
        const table = el('table', 'rg-table');
        table.append(headRow([{key: 'a', label: 'Part'}, {key: 'b', label: 'Heading'}, {key: 'c', label: 'Rule history'}], {}, null));
        const body = el('tbody');
        for (const part of slice) {
          const line = el('tr'), first = el('td', '');
          const anchor = el('a', 'rg-rowtitle', `${part.title} CFR ${part.part}`); anchor.href = part.regulations_link; first.append(anchor);
          const heading = el('td', 'rg-wrap', text(part.heading) || '');
          const history = el('td', '');
          const historyAnchor = el('a', 'button-link', 'Federal Register →'); historyAnchor.href = part.rule_history_link; history.append(historyAnchor);
          append(line, first, heading, history);
          body.append(line);
        }
        table.append(body);
        panel.append(scrollTable(table, 'Parts of the CFR slice that the eCFR agency list attributes to this agency; select a part to read its sections.'));
      }
      const parts = item.top_cfr_parts || [];
      if (parts.length) {
        panel.append(heading2('Most-cited parts, in or outside the slice', 'Federal Register'));
        const table = el('table', 'rg-table');
        table.append(headRow([{key: 'a', label: 'Part'}, {key: 'b', label: 'Heading'}, {key: 'c', label: 'Documents', numeric: true}, {key: 'd', label: 'Held here'}], {}, null));
        const body = el('tbody');
        for (const part of parts) {
          const line = el('tr'), first = el('td', '');
          const anchor = el('a', 'rg-rowtitle', `${part.title} CFR ${part.part}`); anchor.href = part.rule_history_link; first.append(anchor);
          append(line, first, el('td', 'rg-wrap', text(part.heading) || ''), el('td', 'rg-num', count(part.documents)));
          const held = el('td', '');
          held.append(el('span', 'rg-tag' + (part.in_slice ? ' rg-on' : ''), part.in_slice ? 'Full text' : 'Index only'));
          line.append(held);
          body.append(line);
        }
        table.append(body);
        panel.append(scrollTable(table, 'Counted from the Federal Register index, not from the CFR itself.'));
      }
      if (!chapters.length && !slice.length && !parts.length) panel.append(el('p', 'rg-empty', 'The regulations layer records no CFR chapter for this agency, and no document in the saved index names a CFR part for it.'));
      return;
    }
    if (id === 'safety') {
      const datasets = item.safety_datasets || [];
      if (!datasets.length) {
        append(panel, el('p', 'rg-empty', 'This archive holds no safety or enforcement dataset published by this agency.'));
        const row = el('div', 'rg-actions');
        row.append(routeLink('Browse every safety dataset →', 'agencies', {datasets: '1'}, 'button-link'));
        panel.append(row);
        return;
      }
      const table = el('table', 'rg-table');
      table.append(headRow([{key: 'a', label: 'Dataset'}, {key: 'b', label: 'Records', numeric: true}, {key: 'c', label: 'Publisher data as of'}, {key: 'd', label: 'Captured'}, {key: 'e', label: ''}], {}, null));
      const body = el('tbody');
      for (const dataset of datasets) {
        const line = el('tr'), first = el('td', 'rg-wrap');
        const anchor = el('a', 'rg-rowtitle', dataset.label); anchor.href = makeHash('agencies', {datasets: '1', dataset: dataset.id}); first.append(anchor);
        if (dataset.publisher) first.append(el('div', 'rg-sub', dataset.publisher));
        const open = el('td', '');
        const openAnchor = el('a', 'button-link', 'Open dataset →'); openAnchor.href = makeHash('agencies', {datasets: '1', dataset: dataset.id}); open.append(openAnchor);
        append(line, first, el('td', 'rg-num', count(dataset.records)),
          el('td', '', dataset.source_as_of ? date(dataset.source_as_of) : 'Not recorded'),
          el('td', '', dataset.captured_at ? date(dataset.captured_at) : 'Not recorded'), open);
        body.append(line);
      }
      table.append(body);
      panel.append(scrollTable(table, 'Publisher exports as of their own stated dates. A recall, warning letter, shortage entry or approval is an agency record about a product or firm, not a finding of defect, causation or liability.'));
      const row = el('div', 'rg-actions');
      row.append(routeLink('Search across every safety dataset →', 'agencies', {datasets: '1'}, 'button-link'));
      panel.append(row);
      return;
    }
    if (id === 'documents') {
      const saved = item.saved_documents;
      if (!saved || !saved.total) { panel.append(el('p', 'rg-empty', 'No document published by this agency has been downloaded into this archive.')); return; }
      const table = el('table', 'rg-table');
      table.append(headRow([{key: 'a', label: 'Document'}, {key: 'b', label: 'File'}], {}, null));
      const body = el('tbody');
      for (const document_ of saved.results || []) {
        const line = el('tr'), first = el('td', 'rg-wrap');
        const open = action(cleanTitle(document_.title, document_.url) || text(document_.title) || document_.id, async () => {
          let full;
          try { full = await api(`${DOC_AREA}/item?id=${encodeURIComponent(document_.id)}`); }
          catch (error) { announce('Could not open the document.'); return; }
          openDialog(cleanTitle(full.title, full.url) || text(full.title) || document_.id, target => {
            if (full.subtitle) target.append(el('p', 'rg-note', full.subtitle));
            const facts = dl((full.facts || []).filter(pair => !/\b(id|hash|sha\d*|file id)\b/i.test(String(pair[0]))).map(pair => [String(pair[0]), pair[1]]));
            if (facts.children.length) target.append(facts);
            for (const section of full.sections || []) {
              if (section.heading) target.append(el('h3', 'rg-eyebrow', section.heading));
              if (section.text) target.append(el('p', 'rg-text', section.text));
            }
            const row = el('div', 'rg-actions');
            for (const item2 of full.links || []) if (item2 && item2.url && !String(item2.url).startsWith('#')) { const anchor = link((item2.label || 'Open') + ' ↗', item2.url, /^https?:/.test(String(item2.url)), 'button-link'); if (anchor) row.append(anchor); }
            if (row.children.length) target.append(row);
            const note = dataNote(full.qualification); if (note) target.append(note);
          });
        }, 'rg-rowtitle');
        first.append(open);
        if (document_.url) first.append(el('div', 'rg-sub', String(document_.url)));
        line.append(first, el('td', '', document_.kind || ''));
        body.append(line);
      }
      table.append(body);
      panel.append(scrollTable(table, `${count((saved.results || []).length)} of ${count(saved.total)} downloaded documents; select one to read the saved text.`));
      const row = el('div', 'rg-actions');
      const anchor = el('a', 'button-link', `Open all ${count(saved.total)} documents →`); anchor.href = saved.link; row.append(anchor);
      panel.append(row);
      return;
    }
    if (id === 'addresses') {
      const addresses = item.addresses;
      if (!addresses || !addresses.total) { panel.append(el('p', 'rg-empty', 'No address for this agency was found in the saved discovery lists.')); return; }
      const line = el('div', 'rg-line');
      append(line, el('strong', '', count(addresses.total)), el('span', '', 'distinct addresses'),
        el('strong', '', count(addresses.saved)), el('span', '', 'already saved in this archive'));
      panel.append(line);
      if ((addresses.by_content || []).length) {
        const table = el('table', 'rg-table');
        table.append(headRow([{key: 'a', label: 'Content'}, {key: 'b', label: 'Addresses', numeric: true}, {key: 'c', label: ''}], {}, null));
        const body = el('tbody');
        for (const entry of addresses.by_content) {
          const row = el('tr'), first = el('td', 'rg-wrap');
          const anchor = el('a', 'rg-rowtitle', entry.value); anchor.href = entry.link; first.append(anchor);
          const open = el('td', '');
          const openAnchor = el('a', 'button-link', 'Open →'); openAnchor.href = entry.link; open.append(openAnchor);
          append(row, first, el('td', 'rg-num', count(entry.count)), open);
          body.append(row);
        }
        if (addresses.content_not_labelled) {
          const row = el('tr'), first = el('td', 'rg-wrap', 'Not labelled by content');
          append(row, first, el('td', 'rg-num', count(addresses.content_not_labelled)), el('td', ''));
          body.append(row);
        }
        table.append(body);
        panel.append(scrollTable(table, 'Content labels come from the saved discovery lists; an address that is not labelled is not thereby uncategorised at the source.'));
      }
      if ((addresses.top_hosts || []).length) {
        panel.append(heading2('Largest sites', 'Addresses'));
        panel.append(rankedBars(addresses.top_hosts.map(host => ({label: host.host, value: host.count})), 'Distinct addresses per host in the saved lists.'));
      }
      const row = el('div', 'rg-actions');
      const anchor = el('a', 'button-link', `Open the address directory for this agency →`); anchor.href = addresses.link; row.append(anchor);
      panel.append(row);
    }
  }

  async function agenciesPage(signal, supplement) {
    installStyle();
    if (get('datasets') === '1' || (get('dataset') && !get('agency'))) return safetyDatasetPage(signal);

    const rows = await hubOrNull(HUB, signal);
    if (signal.aborted) return;
    if (!rows) {
      // The agency overview route is not installed; fall back to what the page has always offered.
      hubNotice(main, 'the agency directory');
      return safetyDatasetPage(signal);
    }
    const key = get('agency');
    const chosen = rows.find(row => row.key === key) || null;
    main.append(agencyFilterForm(chosen ? 'profile' : 'directory', rows, key));

    if (chosen) {
      let item = null;
      try { item = await hubOrNull(`${HUB}/item?key=${encodeURIComponent(key)}`, signal); }
      catch (error) { if (error.name === 'AbortError') return; item = null; }
      if (signal.aborted) return;
      if (!item) { emptyState(main, 'Agency profile unavailable', 'The overview service did not answer for this agency. The directory and the dataset browser still work.', () => navigate('agencies')); return; }
      const box = el('div', 'rg');
      agencyProfile(box, item, rows);
      main.append(box);
      announce(`${item.name} profile opened.`);
      return;
    }

    const asOf = (rows[0] && rows[0].as_of) || {};
    const line = el('div', 'rg-line');
    append(line, el('strong', '', count(rows.length)), el('span', '', 'agencies joined across the regulation, Federal Register, safety, document and address collections'),
      routeLink('Safety dataset browser →', 'agencies', {datasets: '1'}, 'button-link'));
    main.append(line);
    for (const problem of (rows[0] && rows[0].unavailable) || []) main.append(el('p', 'rg-warn', problem));
    const box = el('div', 'rg');
    agencyDirectory(box, rows);
    main.append(box);
    const note = dataNote('Each agency is joined to the Federal Register index by exact agency slug, to the address directory by exact agency key, to the downloaded documents by exact publisher family and to the safety datasets by exact dataset id; nothing is matched by name similarity. '
      + 'The Federal Register index names every agency the publisher printed on a document, so a department such as HHS or DOT already includes the documents its component agencies file: these rows overlap and must not be added together. '
      + `Federal Register index collected ${asOf.federal_register ? date(asOf.federal_register) : 'date not recorded'} covering ${asOf.federal_register_span || 'its stated span'}; `
      + `safety datasets validated ${asOf.safety ? date(asOf.safety) : 'date not recorded'}; downloaded documents validated ${asOf.documents ? date(asOf.documents) : 'date not recorded'}; `
      + `address directory validated ${asOf.addresses ? date(asOf.addresses) : 'date not recorded'}; regulations layer validated ${asOf.regulations ? date(asOf.regulations) : 'date not recorded'}.`);
    if (note) main.append(note);
    announce(`${rows.length} agencies listed.`);
  }

  /* ======================================================================== REGULATIONS ==== */
  const SOURCE_LABEL = {gpo_ecfr_xml: 'Official GPO text', open_us_law: 'Publisher text'};
  const SEARCH_KEYS = ['q', 'agency', 'record_type', 'date_type', 'dfrom', 'dto'];
  const CITATION = /^\s*(\d{1,2})\s*(?:C\.?\s*F\.?\s*R\.?|CFR)\s*(?:§\s*)?([0-9][0-9A-Za-z.\-]*)\s*$/i;

  function temporalLine(temporal) {
    if (!temporal) return '';
    const parts = [];
    if (temporal.source_as_of) parts.push(`source as of ${date(temporal.source_as_of)}`);
    if (temporal.published_at) parts.push(`published ${date(temporal.published_at)}`);
    if (temporal.captured_at) parts.push(`captured ${date(temporal.captured_at, true)}`);
    return parts.join(' · ');
  }

  /* The section reader: header, copy-citation, then underline tabs over the two texts, the
     eCFR version history and the Federal Register documents for the part. */
  async function openSection(citation) {
    let row;
    try { row = await api(`/api/regulation?citation=${encodeURIComponent(citation)}`); }
    catch (error) { announce('Could not open the regulation section.'); return; }
    openDialog(text(row.citation) || String(citation), body => {
      const wrap = el('div', 'rg-read');
      const head = el('div', 'rg-read-head');
      append(head, el('p', 'rg-note', `Title ${row.title} · Part ${row.part}${row.subpart ? ' · Subpart ' + row.subpart : ''}${row.reserved ? ' · Reserved' : ''}${row.part_heading ? ' · ' + text(row.part_heading) : ''}`));
      wrap.append(el('h3', 'rg-h', text(row.heading) || text(row.citation)), head);
      const bar = el('div', 'rg-actions');
      bar.append(copyButton(text(row.citation)));
      const history = el('a', 'button-link', 'Rule history for this part →');
      history.href = makeHash('federal-register', {cfr_title: row.title, cfr_part: row.part});
      history.addEventListener('click', () => { const d = document.querySelector('#record-dialog'); if (d && d.open) d.close(); });
      bar.append(history);
      const partLink = el('a', 'button-link', 'All sections of this part →');
      partLink.href = makeHash('regulations', {title: row.title, part: `${row.title}:${row.part}`});
      partLink.addEventListener('click', () => { const d = document.querySelector('#record-dialog'); if (d && d.open) d.close(); });
      bar.append(partLink);
      if (row.ecfr_url) { const anchor = link('Current eCFR ↗', row.ecfr_url, true, 'button-link'); if (anchor) bar.append(anchor); }
      wrap.append(bar);

      const texts = Array.isArray(row.texts) ? row.texts : [];
      const related = row.related_fr_documents || {};
      const documents = Array.isArray(related) ? related : (related.documents || related.items || []);
      const frHistory = row.fr_history || null;
      const items = [];
      for (const source of texts) items.push({id: 'text-' + source.source, label: SOURCE_LABEL[source.source] || human(source.source), source});
      items.push({id: 'facts', label: 'Dates & sources'});
      if ((row.history || []).length) items.push({id: 'history', label: 'Version history', hint: row.history.length});
      if (documents.length || (frHistory && frHistory.total)) items.push({id: 'fr', label: 'Federal Register', hint: (frHistory && frHistory.total) || documents.length});

      tabs(wrap, items, items[0].id, (panel, id) => {
        const chosen = items.find(entry => entry.id === id);
        if (chosen && chosen.source) {
          const source = chosen.source;
          panel.append(el('p', 'rg-cap', `${source.label || ''}${source.text_hash_verified ? ' · hash verified' : ''}${temporalLine(source.temporal) ? ' · ' + temporalLine(source.temporal) : ''}`));
          if (source.text) panel.append(el('pre', 'rg-text', source.text));
          else panel.append(el('p', 'rg-empty', source.unavailable_reason || 'Text is not available from this source.'));
          if (source.amendment_marker) panel.append(el('p', 'rg-cap', `Volume amendment marker: ${text(source.amendment_marker.raw || source.amendment_marker)} — the currency of the printed volume, not a per-section date.`));
          for (const printed of source.amendment_citations_as_printed || []) panel.append(el('p', 'rg-cap', text(printed)));
          return;
        }
        if (id === 'facts') {
          panel.append(dl([
            ['Latest eCFR amendment date', row.latest_amendment_date ? `${date(row.latest_amendment_date)} (${row.latest_amendment_date_basis || 'eCFR version metadata'})` : 'Not recorded'],
            ['Latest issue date', row.latest_issue_date ? date(row.latest_issue_date) : 'Not recorded'],
            ['Versions recorded by eCFR', row.versions_count],
            ['Text sources available', (row.text_sources_available || []).map(source => SOURCE_LABEL[source] || human(source)).join(', ')],
            ['Authority as printed', row.authority_note_as_printed],
            ['Source note as printed', row.source_note_as_printed],
            ['Section identity as of', temporalLine(row.temporal)],
          ]));
          if (row.reconciliation) panel.append(el('p', 'rg-cap', `Comparison between the two texts: ${human(row.reconciliation.category)}. Neither source is chosen as correct.`));
          panel.append(el('p', 'rg-cap', 'Amendment date, Federal Register publication date, publisher-extracted effective date and snapshot date are separate facts; none is inferred from another. eCFR is authoritative but unofficial; the official edition is the annual CFR.'));
          return;
        }
        if (id === 'history') {
          const list = el('ul', 'rg-timeline');
          for (const entry of row.history || []) {
            const line = el('li');
            append(line, el('b', '', entry.ecfr_amendment_date ? date(entry.ecfr_amendment_date) : 'Amendment date not recorded'),
              el('span', '', ` ${entry.issue_date || entry.ecfr_issue_date ? '· issued ' + date(entry.issue_date || entry.ecfr_issue_date) : ''}${entry.substantive === false ? ' · non-substantive' : ''}${entry.removed ? ' · removed' : ''}`));
            list.append(line);
          }
          append(panel, list, el('p', 'rg-cap', 'eCFR version metadata; eCFR history begins in 2016/2017, so an older section can show few versions.'));
          return;
        }
        if (id === 'fr') {
          if (frHistory && frHistory.total) {
            panel.append(el('p', 'rg-cap', `${count(frHistory.total)} documents cite ${row.title} CFR part ${row.part}, ${String(frHistory.first_date || '').slice(0, 4)}–${String(frHistory.last_date || '').slice(0, 4)}: `
              + (frHistory.by_type || []).map(entry => `${entry.value} ${count(entry.count)}`).join(' · ')));
            const list = el('ul', 'rg-timeline');
            for (const entry of (frHistory.results || []).slice(0, 8)) {
              const line = el('li');
              append(line, el('b', '', text(entry.subtitle)), el('span', '', ' — ' + text(entry.title)));
              list.append(line);
            }
            panel.append(list);
            if (frHistory.link) { const anchor = el('a', 'button-link', 'Open the full rule history for this part →'); anchor.href = frHistory.link; anchor.addEventListener('click', () => { const d = document.querySelector('#record-dialog'); if (d && d.open) d.close(); }); panel.append(anchor); }
          }
          if (documents.length) {
            panel.append(heading2(`Documents this section's notes name (${count(documents.length)})`, 'Printed citations'));
            const table = el('table', 'rg-table');
            table.append(headRow([{key: 'a', label: 'Document'}, {key: 'b', label: 'Type'}, {key: 'c', label: 'Published'}], {}, null));
            const list = el('tbody');
            for (const document_ of documents.slice(0, 40)) {
              const line = el('tr'), first = el('td', 'rg-wrap');
              first.append(el('span', 'rg-rowtitle', `${document_.citation || document_.document_number || document_.id}: ${text(document_.title)}`));
              append(line, first, el('td', '', document_.type || ''), el('td', '', document_.publication_date ? date(document_.publication_date) : ''));
              list.append(line);
            }
            table.append(list);
            panel.append(scrollTable(table));
          }
          panel.append(el('p', 'rg-cap', 'A document that cites a part may propose, amend, correct or only discuss it.'));
        }
      }, null);
      body.append(wrap);
    });
  }

  function sourceTags(cell, sources) {
    for (const source of (sources || []).slice(0, 2)) cell.append(el('span', 'rg-tag' + (source === 'gpo_ecfr_xml' ? ' rg-on' : ''), SOURCE_LABEL[source] || human(source)));
    if (!(sources || []).length) cell.append(el('span', 'rg-tag', 'Metadata only'));
  }

  function browseStrip(target, titles) {
    const box = el('div', 'rg-browse');
    for (const entry of titles) {
      const row = el('div', 'rg-browse-row');
      const open = el('button', 'rg-browse-open'); open.type = 'button';
      const caret = el('i', '', '▸');
      append(open, caret, el('b', '', `Title ${entry.title}`), el('em', '', entry.name || ''));
      open.setAttribute('aria-expanded', 'false');
      const meta = el('div', 'rg-browse-meta', `${count(entry.parts_in_slice)} of ${count(entry.parts_total)} parts · ${count(entry.sections_in_slice)} sections · ${entry.official_text_local ? 'official text local' : 'publisher text only'}`);
      append(row, open, meta);
      const slot = el('div');
      box.append(row, slot);
      let loaded = false;
      open.addEventListener('click', async () => {
        const isOpen = open.getAttribute('aria-expanded') === 'true';
        open.setAttribute('aria-expanded', isOpen ? 'false' : 'true');
        caret.textContent = isOpen ? '▸' : '▾';
        if (isOpen) { slot.replaceChildren(); return; }
        if (!loaded) {
          slot.replaceChildren(el('div', 'rg-parts', 'Reading the part list…'));
          try {
            const data = await api(`/api/regulations/parts?title=${encodeURIComponent(entry.title)}`);
            const grid = el('div', 'rg-parts');
            for (const part of data.parts || []) {
              const item = el('div', 'rg-part');
              const anchor = routeLink(`Part ${part.part}`, 'regulations', {title: entry.title, part: `${entry.title}:${part.part}`});
              anchor.title = `${entry.title} CFR part ${part.part} — ${text(part.heading)}`;
              append(item, anchor, el('small', '', ' ' + text(part.heading)));
              grid.append(item);
            }
            if (!(data.parts || []).length) grid.append(el('div', 'rg-part', 'No part of this title is held in full.'));
            slot.replaceChildren(grid);
            loaded = true;
          } catch (error) { slot.replaceChildren(el('div', 'rg-parts', 'The part list could not be read.')); }
        } else slot.firstChild.hidden = false;
      });
    }
    target.append(box);
  }

  async function regulationsPage(signal, supplement) {
    installStyle();
    const info = await api('/api/regulations/info', signal); if (signal.aborted) return;
    const counts = info.counts || info;

    const form = el('form', 'filters'); form.setAttribute('aria-label', 'Search regulations');
    const q = filterField('Search or citation', 'q', 'search', get('q'), 'Words, or 21 CFR 314.80');
    const titleField = filterField('Title', 'title', 'select', get('title'));
    const partField = filterField('Part', 'part', 'search', get('part'), 'e.g. 314');
    const agencyField = filterField('Agency', 'agency', 'select', get('agency'));
    const recordType = filterField('Record type', 'record_type', 'select', get('record_type'));
    setOptions(recordType.input, [{value: 'section', label: 'CFR sections'}, {value: 'fr_document', label: 'Federal Register documents'}], 'Sections and documents', get('record_type'));
    const dateType = filterField('Date type', 'date_type', 'select', get('date_type'));
    const from = filterField('From', 'dfrom', 'date', get('dfrom')), to = filterField('To', 'dto', 'date', get('dto'));
    const submit = el('button', 'button button-primary', 'Search'); submit.type = 'submit';
    append(form, q.label, titleField.label, partField.label, agencyField.label, recordType.label, dateType.label, from.label, to.label,
      submit, action('Clear', () => navigate('regulations')));
    form.addEventListener('submit', event => { event.preventDefault(); navigate('regulations', Object.fromEntries(new FormData(form))); });
    main.append(form);

    const line = el('div', 'rg-line');
    append(line, el('strong', '', count(counts.sections_indexed)), el('span', '', `CFR sections indexed across ${count(counts.titles)} titles`),
      el('strong', '', count(counts.sections_in_slice)), el('span', '', 'held in full (Titles 16, 21, 40 and 49)'),
      el('strong', '', count(counts.fr_documents)), el('span', '', 'Federal Register documents linked to those parts'));
    main.append(line);

    const [titles, agencies] = await Promise.all([api('/api/regulations/titles', signal), api('/api/regulations/agencies?slice_only=1', signal)]);
    if (signal.aborted) return;
    setOptions(titleField.input, (titles.titles || []).map(entry => ({value: entry.title, label: `Title ${entry.title} — ${entry.name}`})), 'All titles', get('title'));
    setOptions(agencyField.input, (agencies.agencies || []).map(entry => ({value: entry.slug, label: entry.display_name || entry.name})), 'Any agency', get('agency'));
    const dateTypes = Array.isArray(info.date_types)
      ? info.date_types.map(value => (typeof value === 'string' ? {value, label: human(value)} : value))
      : Object.keys(info.date_types || {}).map(value => ({value, label: human(value)}));
    setOptions(dateType.input, dateTypes, 'Choose a date field', get('date_type'));

    const body = el('div', 'rg');
    main.append(body);

    // A citation typed into the search box opens the section reader directly.
    const typed = CITATION.exec(get('q'));
    if (typed) {
      const citation = `${typed[1]} CFR ${typed[2]}`;
      const row = el('div', 'rg-actions');
      row.append(action(`Open ${citation} in the reader →`, () => openSection(citation), 'button button-primary'));
      body.append(row);
    }

    if (get('part')) {
      const query = new URLSearchParams({part: get('part')});
      if (get('title')) query.set('title', get('title'));
      const data = await api(`/api/regulations/sections?${query}`, signal); if (signal.aborted) return;
      if (!data.found) { emptyState(body, 'Part not found', 'Choose a title and a part number from the browse list.', () => navigate('regulations')); return; }
      const part = data.part || {};
      body.append(heading2(`${part.title} CFR Part ${part.part} — ${text(part.heading)}`, [part.chapter_name, part.subchapter_name].filter(Boolean).join(' · ')));
      const facts = el('div', 'rg-line');
      append(facts, el('strong', '', count(part.sections_in_ecfr_structure)), el('span', '', 'sections in the eCFR structure'),
        el('strong', '', count(part.sections_with_local_official_text)), el('span', '', 'with official text held here'),
        el('strong', '', count(part.publisher_index_rows)), el('span', '', 'publisher rows'));
      body.append(facts);
      const actions = el('div', 'rg-actions');
      const history = el('a', 'button-link', 'Rule history: Federal Register documents citing this part →');
      history.href = makeHash('federal-register', {cfr_title: part.title, cfr_part: part.part});
      actions.append(history, routeLink('← All parts of this title', 'regulations', {title: part.title}, 'button-link'));
      body.append(actions);
      if (part.authority_note_as_printed) body.append(el('p', 'rg-cap', `Authority as printed: ${text(part.authority_note_as_printed)}`));
      if (part.source_note_as_printed) body.append(el('p', 'rg-cap', `Source as printed: ${text(part.source_note_as_printed)}`));
      if (part.gpo_amendment_marker) body.append(el('p', 'rg-cap', `GPO volume ${part.gpo_volume || ''} amendment marker: ${text(part.gpo_amendment_marker.raw)} — the currency of the printed volume, not a per-section date.`));
      const sections = data.sections || data.items || data.results || [];
      const table = el('table', 'rg-table');
      table.append(headRow([{key: 'a', label: 'Section'}, {key: 'b', label: 'Text held'}, {key: 'c', label: 'Latest amendment (eCFR)'}], {}, null));
      const list = el('tbody');
      for (const section of sections) {
        const row = el('tr'), first = el('td', 'rg-wrap');
        first.append(action(`${section.citation || section.section} ${text(section.heading)}`, () => openSection(section.id || section.citation), 'rg-rowtitle'));
        if (section.reserved) first.append(el('div', 'rg-sub', 'Reserved'));
        const tags = el('td', ''); sourceTags(tags, section.text_sources_available);
        append(row, first, tags, el('td', '', section.latest_amendment_date ? date(section.latest_amendment_date) : '—'));
        list.append(row);
      }
      table.append(list);
      body.append(scrollTable(table, `${count(sections.length)} sections. ${(data.as_of && data.as_of.caveat) || 'Section list from the eCFR structure.'}`));
      const note = dataNote(info.qualification || ''); if (note) body.append(note);
      return;
    }

    if (SEARCH_KEYS.some(key => get(key))) {
      const query = new URLSearchParams(params());
      query.set('limit', '25');
      query.set('page', String(Math.max(1, Number(query.get('page')) || 1)));
      const data = await api(`/api/regulations/search?${query}`, signal); if (signal.aborted) return;
      if (!data.available) { body.append(el('p', 'rg-empty', data.reason || 'Regulation data is not available.')); return; }
      if (!(data.results || []).length) { emptyState(body, 'No matching regulations', 'Try fewer words, another title, or remove the date filter.', () => navigate('regulations')); return; }
      const table = el('table', 'rg-table');
      table.append(headRow([{key: 'a', label: 'Provision or document'}, {key: 'b', label: 'Part'}, {key: 'c', label: 'Latest date as published'},
        {key: 'd', label: 'Text held'}, {key: 'e', label: 'Rule history'}], {}, null));
      const list = el('tbody');
      for (const record of data.results) {
        const isDocument = record.record_type === 'fr_document';
        const row = el('tr'), first = el('td', 'rg-wrap');
        first.append(action(isDocument ? `${record.citation || record.document_number}: ${text(record.title)}` : `${record.citation} ${text(record.heading)}`,
          () => (isDocument ? openFederalRegisterDocument(record.id) : openSection(record.id)), 'rg-rowtitle'));
        first.append(el('div', 'rg-sub', isDocument
          ? `Federal Register ${record.type || ''} · ${(record.cfr_part_ids || []).map(value => String(value).replace('cfr:', '').replace(':', ' CFR ')).join(', ')}`
          : `${record.title} CFR${record.subpart ? ' · Subpart ' + record.subpart : ''}${record.part_heading ? ' · ' + text(record.part_heading) : ''}`));
        const partCell = el('td', '');
        if (isDocument) partCell.textContent = '—';
        else partCell.append(routeLink(`Part ${record.part}`, 'regulations', {title: record.title, part: `${record.title}:${record.part}`}, 'button-link'));
        const dates = el('td', '');
        if (isDocument) {
          if (record.publication_date) dates.append(el('div', '', `Published ${date(record.publication_date)}`));
          if (record.effective_on_publisher_extracted) dates.append(el('div', 'rg-sub', `Publisher effective ${date(record.effective_on_publisher_extracted)} (unverified)`));
        } else {
          if (record.latest_amendment_date) dates.append(el('div', '', `Amended ${date(record.latest_amendment_date)}`));
          if (record.temporal && record.temporal.source_as_of) dates.append(el('div', 'rg-sub', `Source as of ${date(record.temporal.source_as_of)}`));
        }
        if (!dates.children.length) dates.textContent = 'Not recorded';
        const tags = el('td', '');
        if (isDocument) tags.append(el('span', 'rg-tag', 'Federal Register'));
        else sourceTags(tags, record.text_sources_available);
        const history = el('td', '');
        const parts = isDocument ? (record.cfr_part_ids || []).slice(0, 1).map(value => String(value).replace('cfr:', '').split(':')) : [[record.title, record.part]];
        for (const [partTitle, partNumber] of parts) if (partTitle && partNumber) {
          const anchor = el('a', 'button-link', 'Documents →');
          anchor.href = makeHash('federal-register', {cfr_title: partTitle, cfr_part: partNumber});
          history.append(anchor);
        }
        append(row, first, partCell, dates, tags, history);
        list.append(row);
      }
      table.append(list);
      append(body, scrollTable(table, `${count(data.total)} results, ordered by citation and publication date, not by relevance.`),
        pagination(data.total, Number(data.page) || 1, Number(data.limit) || 25));
      const note = dataNote(info.qualification || ''); if (note) body.append(note);
      return;
    }

    if (get('title')) {
      const data = await api(`/api/regulations/parts?title=${encodeURIComponent(get('title'))}`, signal); if (signal.aborted) return;
      const entry = data.title || {};
      body.append(heading2(`Title ${entry.title} — ${entry.name}`, `${count(entry.parts_in_slice)} parts held in full of ${count(entry.parts_total)}`));
      const table = el('table', 'rg-table');
      table.append(headRow([{key: 'a', label: 'Part'}, {key: 'b', label: 'Heading'}, {key: 'c', label: 'Sections', numeric: true},
        {key: 'd', label: 'Text held'}, {key: 'e', label: 'Rule history'}], {}, null));
      const list = el('tbody');
      for (const part of data.parts || []) {
        const row = el('tr'), first = el('td', '');
        first.append(routeLink(`Part ${part.part}`, 'regulations', {title: entry.title, part: `${entry.title}:${part.part}`}, 'rg-rowtitle'));
        const tags = el('td', '');
        tags.append(el('span', 'rg-tag' + (part.official_text_local ? ' rg-on' : ''), part.official_text_local ? 'Official GPO text' : 'Publisher text'));
        const history = el('td', '');
        const anchor = el('a', 'button-link', 'Documents →');
        anchor.href = makeHash('federal-register', {cfr_title: entry.title, cfr_part: part.part});
        history.append(anchor);
        append(row, first, el('td', 'rg-wrap', text(part.heading)), el('td', 'rg-num', count(part.sections_in_ecfr_structure)), tags, history);
        list.append(row);
      }
      table.append(list);
      append(body, routeLink('← All titles', 'regulations', {}, 'button-link'),
        scrollTable(table, `${count((data.parts || []).length)} parts of Title ${entry.title} held in full; select a part to read its sections.`));
      const note = dataNote(info.qualification || ''); if (note) body.append(note);
      return;
    }

    body.append(heading2('Browse the code', 'Titles held in full'));
    browseStrip(body, titles.titles || []);
    const agencyRow = el('div', 'rg-actions');
    agencyRow.append(el('span', 'rg-eyebrow', 'Agencies with parts here'));
    for (const agency of agencies.agencies || []) agencyRow.append(routeLink(agency.short_name || agency.display_name || agency.name, 'regulations', {agency: agency.slug}, 'button-link'));
    body.append(agencyRow);
    body.append(el('p', 'rg-cap', 'Search words or a citation such as "21 CFR 314.80" in the filters, or open a title above to browse its parts. Every section opens in a reader with the official text, the publisher text, its eCFR version history and the Federal Register documents for its part.'));
    const note = dataNote(`${info.qualification || ''} Official GPO text is held locally for Title 21; publisher text comes from the saved Open US Law snapshot. Amendment, publication, effective and snapshot dates are kept as separate facts. Regulations layer validated ${info.validated_at ? date(info.validated_at, true) : 'date not recorded'}.`);
    if (note) body.append(note);
    announce('Regulations workbench opened.');
  }

  /* --------------------------------------------------------------------------------- export */
  window.RegsUI = {
    regulations: (signal, supplement) => regulationsPage(signal, supplement),
    agencies: (signal, supplement) => agenciesPage(signal, supplement),
  };
})();
