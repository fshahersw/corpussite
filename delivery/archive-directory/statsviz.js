/* Court statistics dashboard (statsviz.js).

   A full replacement renderer for the "Court statistics" area. It is installed by app.js /
   areas.js as window.ARCHIVE_AREAS.statistics.page and uses only the helpers those files
   define at script level (el, append, api, action, link, routeLink, navigate, makeHash,
   count, date, human, badge, loading, emptyState, dataNote, cleanTitle, announce, main,
   dialog, route). It injects its own <style id="statsviz-style"> block once; every class it
   writes is prefixed "sv-".

   Data rules this file obeys, without exception:
     - Every figure shown is a publisher-reported count, printed for a stated period.
     - No rate, percentage of a total, share, average or ranking-as-fact is computed here.
       Bars are sorted for legibility and the caption says so ("top N rows by <column>").
     - The only arithmetic performed is a plain sum across the rows of one table for one
       period, and it is always labelled "sum of the rows shown".
     - Cells are rendered exactly as the archive stored them; numbers are parsed only to
       decide a bar's length, never to reformat the printed value.
     - Internal identifiers (table ids, file ids, SHA-256 digests) never appear as text in
       the main view; in the detail panel they sit behind a "Technical details" disclosure. */
(function () {
  'use strict';

  const STYLE_ID = 'statsviz-style';
  const AREA = 'statistics';
  const API = '/api/area/statistics';
  const BARS = 12;               // bars per mini chart card
  const MINI_ROWS = 5;           // rows in a non-numeric fallback card
  const CONCURRENCY = 5;         // simultaneous detail requests
  const PUBLISHER_NOTE = 'publisher-reported counts';

  /* ------------------------------------------------------------------ styles */
  const CSS = [
    '.sv{display:block}',
    '.sv-kpis{display:grid;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));gap:8px;margin:0 0 16px}',
    '.sv-kpi{background:var(--surface);border:1px solid var(--line);border-radius:var(--radius);padding:12px 14px 11px;min-width:0}',
    '.sv-kpi:first-child{border-top:3px solid var(--teal);padding-top:10px}',
    '.sv-kpi-label{font-size:10px;font-weight:650;letter-spacing:.4px;text-transform:uppercase;color:var(--muted)}',
    '.sv-kpi-value{display:block;font:400 28px/1.15 Georgia,"Times New Roman",serif;letter-spacing:-.6px;margin:6px 0 4px;font-variant-numeric:tabular-nums lining-nums}',
    '.sv-kpi-note{font-size:11px;line-height:1.45;color:var(--muted);overflow-wrap:anywhere}',
    '.sv-kpi-value.sv-wait{color:var(--muted)}',
    '.sv-controls{display:grid;grid-template-columns:minmax(150px,1.1fr) minmax(150px,1.1fr) minmax(130px,.9fr) minmax(170px,1.4fr) auto;align-items:end;gap:8px;background:#f0f3ef;border:1px solid var(--line);border-radius:10px;padding:12px;margin:0 0 8px}',
    '.sv-controls label{display:grid;gap:4px;font-size:10px;font-weight:650;letter-spacing:.3px;color:#596c67;min-width:0}',
    '.sv-controls select,.sv-controls input{height:34px;width:100%;min-width:0;background:#fff;border:1px solid #ccd8d1;border-radius:5px;padding:5px 8px;color:var(--ink);font-size:13px;text-overflow:ellipsis}',
    '.sv-actions{display:flex;align-items:end;gap:8px;flex-wrap:wrap}',
    '.sv-toggle{display:inline-flex;border:1px solid #ccd8d1;background:#fff;border-radius:6px;padding:2px;gap:2px;height:34px}',
    '.sv-toggle button{border:0;background:transparent;border-radius:4px;font-size:12px;font-weight:650;color:var(--muted);padding:0 12px;height:28px;display:inline-flex;align-items:center;gap:6px}',
    '.sv-toggle button[aria-pressed="true"]{background:var(--teal-light);color:var(--teal-dark);box-shadow:inset 0 0 0 1px #cbe0d6}',
    '.sv-controls .sv-clear{height:34px;min-height:34px;padding:0 12px;font-size:12px}',
    '.sv-meta{display:flex;flex-wrap:wrap;align-items:baseline;gap:6px 12px;margin:0 0 16px;font-size:11px;color:var(--muted)}',
    '.sv-meta strong{color:var(--ink);font-size:12px;font-weight:650;font-variant-numeric:tabular-nums}',
    '.sv-meta .data-note{margin:0}',
    '.sv-blockbar{display:flex;flex-wrap:wrap;align-items:center;justify-content:space-between;gap:8px;font-size:11px;color:var(--muted)}',
    '.sv-grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(320px,1fr));gap:8px}',
    '.sv-card{display:flex;flex-direction:column;gap:8px;background:var(--surface);border:1px solid var(--line);border-radius:var(--radius);padding:14px 14px 10px;min-width:0}',
    '.sv-card-eyebrow{font-size:10px;font-weight:650;letter-spacing:.4px;text-transform:uppercase;color:var(--muted)}',
    '.sv-card h3{font:400 15px/1.35 Georgia,"Times New Roman",serif;letter-spacing:-.2px;display:-webkit-box;-webkit-line-clamp:2;-webkit-box-orient:vertical;overflow:hidden}',
    '.sv-card-meta{font-size:11px;line-height:1.45;color:var(--muted)}',
    '.sv-card-slot{min-height:64px}',
    '.sv-card-foot{margin-top:auto;display:flex;flex-wrap:wrap;gap:6px;padding-top:8px;border-top:1px solid var(--line)}',
    '.sv-card-foot .button,.sv-card-foot .sv-mini-button{min-height:30px;height:30px;padding:0 10px;font-size:11px;font-weight:650}',
    '.sv-mini-button{border:1px solid #cfdad5;background:#fff;border-radius:6px;display:inline-flex;align-items:center;justify-content:center;gap:6px;color:var(--ink);text-decoration:none}',
    '.sv-figure{margin:0;display:grid;gap:6px}',
    '.sv-bars{display:grid;grid-template-columns:minmax(58px,38%) minmax(30px,1fr) max-content;align-items:center;gap:4px 8px}',
    '.sv-bar-label{font-size:11px;line-height:1.3;color:var(--ink);white-space:nowrap;overflow:hidden;text-overflow:ellipsis;min-width:0}',
    '.sv-bar-track{height:7px;border-radius:2px;background:#edf1ef;min-width:0}',
    '.sv-bar-fill{display:block;height:100%;border-radius:2px;background:var(--teal);opacity:.85;min-width:2px}',
    '.sv-bar-fill.sv-neg{background:var(--gold)}',
    '.sv-bar-value{font-size:11px;font-variant-numeric:tabular-nums lining-nums;color:var(--muted);text-align:right;white-space:nowrap}',
    '.sv-figcaption{font-size:10.5px;line-height:1.45;color:var(--muted)}',
    '.sv-sum{font-size:11px;color:var(--muted);font-variant-numeric:tabular-nums}',
    '.sv-sum b{color:var(--ink);font-weight:650}',
    '.sv-court{display:grid;gap:4px;font-size:10px;font-weight:650;letter-spacing:.3px;color:#596c67}',
    '.sv-court select{height:32px;width:100%;min-width:0;background:#fff;border:1px solid #ccd8d1;border-radius:5px;padding:4px 8px;color:var(--ink);font-size:12px;text-overflow:ellipsis}',
    '.sv-note{font-size:11px;line-height:1.5;color:var(--muted)}',
    '.sv-skeleton{height:7px;border-radius:2px;background:#eef2f0;margin:9px 0}',
    '.sv-skeleton:nth-child(2){width:78%}.sv-skeleton:nth-child(3){width:54%}.sv-skeleton:nth-child(4){width:36%}',
    '.sv-scroll{overflow-x:auto;overflow-y:auto;border:1px solid var(--line);border-radius:8px;background:var(--surface);-webkit-overflow-scrolling:touch}',
    '.sv-table{border-collapse:collapse;width:100%;font-size:12.5px}',
    '.sv-table th,.sv-table td{padding:6px 10px;text-align:left;border-bottom:1px solid var(--line);vertical-align:top;white-space:nowrap}',
    '.sv-table thead th{position:sticky;top:0;z-index:1;background:#f4f7f4;font-size:10px;font-weight:650;letter-spacing:.4px;text-transform:uppercase;color:#4e6660;white-space:normal;box-shadow:inset 0 -1px 0 var(--line)}',
    '.sv-table tbody tr:nth-child(even){background:#fafbf9}',
    '.sv-table tbody tr:last-child td{border-bottom:0}',
    '.sv-table td.sv-num,.sv-table th.sv-num{text-align:right;font-variant-numeric:tabular-nums lining-nums}',
    '.sv-table td.sv-wrap{white-space:normal;min-width:180px}',
    '.sv-table caption{caption-side:top;text-align:left;padding:8px 10px;font-size:10.5px;color:var(--muted);background:var(--surface)}',
    '.sv-list tbody tr{cursor:pointer}',
    '.sv-list tbody tr:hover,.sv-list tbody tr:focus-within{background:var(--teal-light)}',
    '.sv-list thead th{padding:0}',
    '.sv-sort{width:100%;border:0;background:transparent;padding:8px 10px;font:inherit;font-size:10px;font-weight:650;letter-spacing:.4px;text-transform:uppercase;color:#4e6660;display:flex;align-items:center;gap:5px;text-align:left}',
    '.sv-sort.sv-num-head{justify-content:flex-end}',
    '.sv-sort:hover{color:var(--teal-dark)}',
    '.sv-sort i{font-style:normal;font-size:9px;opacity:.45}',
    '.sv-list th[aria-sort] .sv-sort{color:var(--teal-dark)}',
    '.sv-list th[aria-sort] .sv-sort i{opacity:1}',
    '.sv-list td.sv-wrap{max-width:340px}',
    '.sv-rowtitle{border:0;background:transparent;padding:0;font:inherit;font-size:12.5px;font-weight:600;color:var(--teal-dark);text-align:left;text-decoration:underline;text-underline-offset:3px;display:-webkit-box;-webkit-line-clamp:2;-webkit-box-orient:vertical;overflow:hidden}',
    '.sv-rowsub{font-size:10.5px;color:var(--muted);font-weight:400;margin-top:2px;white-space:normal}',
    /* detail panel */
    '.sv-detail{display:grid;gap:16px}',
    '.sv-detail h2{font:400 21px/1.3 Georgia,"Times New Roman",serif;letter-spacing:-.3px}',
    '.sv-detail h3{font-size:12px;font-weight:650;letter-spacing:.3px;text-transform:uppercase;color:#4e6660}',
    '.sv-detail-sub{font-size:12px;color:var(--muted);margin-top:4px}',
    '.sv-dl{display:grid;grid-template-columns:minmax(120px,max-content) minmax(0,1fr);gap:4px 16px;margin:0;font-size:12px}',
    '.sv-dl dt{color:var(--muted)}',
    '.sv-dl dd{margin:0;overflow-wrap:anywhere}',
    '.sv-block{display:grid;gap:8px}',
    '.sv-detail .sv-scroll{max-height:min(52vh,460px)}',
    '.sv-detail .sv-bars{grid-template-columns:minmax(90px,34%) minmax(40px,1fr) max-content}',
    '.sv-detail .button-row{display:flex;flex-wrap:wrap;gap:8px}',
    '.sv-detail .data-note{margin:0}',
    '@media (max-width:900px){.sv-controls{grid-template-columns:1fr 1fr}.sv-controls label:nth-child(4){grid-column:1/-1}.sv-actions{grid-column:1/-1}}',
    '@media (max-width:640px){.sv-kpis{grid-template-columns:repeat(auto-fit,minmax(140px,1fr))}.sv-grid{grid-template-columns:1fr}.sv-controls{grid-template-columns:1fr}.sv-toggle{flex:1}.sv-toggle button{flex:1;justify-content:center}.sv-dl{grid-template-columns:1fr;gap:2px 0}.sv-dl dt{margin-top:6px}}',
  ].join('\n');

  // The archive server sends "Content-Security-Policy: style-src 'self'", which blocks the
  // text of any <style> element the page creates (an empty one is blocked too, so leaving a
  // marker element behind would log a violation). A constructed stylesheet is CSSOM rather
  // than inline style, so it is allowed and applies normally; the <style id="statsviz-style">
  // element is only used where constructed stylesheets do not exist. Either way the rules are
  // added once and every selector is prefixed "sv-".
  function injectStyle() {
    const root = document.documentElement;
    if (root.dataset.statsvizStyle || document.getElementById(STYLE_ID)) return;
    if (typeof CSSStyleSheet === 'function' && Array.isArray(document.adoptedStyleSheets)) {
      try {
        const sheet = new CSSStyleSheet();
        sheet.replaceSync(CSS);
        document.adoptedStyleSheets = document.adoptedStyleSheets.concat(sheet);
        root.dataset.statsvizStyle = STYLE_ID;
        return;
      } catch (error) { /* fall through to the element */ }
    }
    const node = document.createElement('style');
    node.id = STYLE_ID;
    node.textContent = CSS;
    document.head.append(node);
    root.dataset.statsvizStyle = STYLE_ID;
  }

  /* ------------------------------------------------------------ small helpers */
  const detailCache = new Map();   // id -> detail payload
  const inFlight = new Map();      // id -> promise
  const courtChoice = new Map();   // table id -> court slug
  let cjraSummary = null;          // archive-wide CJRA facts, fetched once
  let observer = null;
  let queue = [];
  let active = 0;
  let sort = {key: null, dir: 1};

  function text(value) { return String(value === null || value === undefined ? '' : value).replace(/\s+/g, ' ').trim(); }
  function stripMarks(value) { return text(value).replace(/[¹²³⁴-⁹⁰ªº]/g, '').replace(/\s+/g, ' ').trim(); }

  // "1,234" / "(12)" / "-" / "—" / "N/A" / "12.5%" -> number or null. Never guesses.
  function parseNumber(raw) {
    let s = String(raw === null || raw === undefined ? '' : raw).replace(/ /g, ' ').trim();
    if (!s) return null;
    if (/^[-–—.*]+$/.test(s)) return null;
    if (/^(n\/?a|na|nr|none|not stated|not reported)$/i.test(s)) return null;
    let negative = false;
    if (/^\(.*\)$/.test(s)) { negative = true; s = s.slice(1, -1); }
    s = s.replace(/[¹²³⁴-⁹⁰]/g, '').replace(/[%$,\s]/g, '');
    if (!/^-?\d+(\.\d+)?$/.test(s)) return null;
    const value = Number(s);
    if (!Number.isFinite(value)) return null;
    return negative ? -value : value;
  }

  // Rows the publisher prints as a total or a circuit subtotal; kept out of the bars so the
  // chart compares like with like. The caption says when any were left out.
  function isAggregateLabel(label) {
    const t = stripMarks(label).replace(/[.,;:]+$/, '');
    if (!t) return false;
    if (/^(sub)?totals?\b/i.test(t)) return true;
    if (/[,\s]totals?$/i.test(t)) return true;
    if (/^(all|u\.?s\.?|united states)\s+(cases|courts|circuits|districts|judgeships|trials)\b/i.test(t)) return true;
    return /^(dc|d\.c\.|\d+(st|nd|rd|th))$/i.test(t);
  }

  function skeleton(rows) {
    const box = el('div', 'sv-card-slot');
    for (let i = 0; i < (rows || 4); i++) box.append(el('div', 'sv-skeleton'));
    box.setAttribute('aria-hidden', 'true');
    return box;
  }

  function natural(value) {
    return String(value === null || value === undefined ? '' : value).toLowerCase()
      .split(/(\d+)/).map(part => (/^\d+$/.test(part) ? String(part).padStart(12, '0') : part)).join('');
  }

  // Sortable/period key taken from the record key, never shown to the reader.
  function periodKey(id) {
    const parts = String(id || '').split(':');
    const value = parts.length >= 3 ? parts[parts.length - 2] : '';
    return /^\d{4}-\d{2}-\d{2}$/.test(value) ? value : '';
  }
  function badgeRows(result) {
    for (const raw of (result && result.badges) || []) {
      const match = /^([\d,]+) per-judge rows parsed$/.exec(String(raw));
      if (match) return Number(match[1].replace(/,/g, ''));
    }
    return null;
  }
  function facet(data, name) {
    return ((data && data.filters) || []).find(f => f && f.name === name) || null;
  }
  function facetTotal(data, name) {
    const f = facet(data, name);
    return f ? (f.options || []).reduce((sum, o) => sum + (Number(o.count) || 0), 0) : 0;
  }
  function originalLink(record) {
    return ((record && record.links) || []).find(l => /^\/supplement-files\//.test(String(l && l.url || ''))) || null;
  }
  /* --------------------------------------------------------------- data access */
  function loadDetail(id, signal) {
    if (detailCache.has(id)) return Promise.resolve(detailCache.get(id));
    if (inFlight.has(id)) return inFlight.get(id);
    const promise = api(API + '/item?id=' + encodeURIComponent(id), signal)
      .then(payload => { detailCache.set(id, payload); inFlight.delete(id); return payload; })
      .catch(error => { inFlight.delete(id); throw error; });
    inFlight.set(id, promise);
    return promise;
  }

  // A small work queue so a filtered page never opens 90 sockets at once.
  function enqueue(job) {
    queue.push(job);
    pump();
  }
  function pump() {
    while (active < CONCURRENCY && queue.length) {
      const job = queue.shift();
      active++;
      Promise.resolve().then(job).catch(() => {}).finally(() => { active--; pump(); });
    }
  }
  function resetQueue() { queue = []; }

  async function loadCjraSummary(signal) {
    if (cjraSummary) return cjraSummary;
    const data = await api(API + '?series=cjra&limit=100', signal);
    const results = (data && data.results) || [];
    let rows = 0;
    const perJudge = [];
    for (const record of results) {
      const n = badgeRows(record);
      if (n !== null) { rows += n; perJudge.push(record); }
    }
    cjraSummary = {
      rows,
      tables: results.length,
      perJudge,
      period: perJudge.length ? text(perJudge[0].cells && perJudge[0].cells.period) : '',
      courts: null,
    };
    return cjraSummary;
  }

  /* ------------------------------------------------------- reading one payload */
  function sectionsOf(detail, matcher) {
    return ((detail && detail.sections) || []).filter(s => s && matcher(String(s.heading || '')));
  }

  function analyse(detail) {
    const previews = sectionsOf(detail, h => /^Preview: sheet/.test(h)).filter(s => Array.isArray(s.rows));
    const perJudge = sectionsOf(detail, h => /^Per-judge totals as printed/.test(h))[0] || null;
    const reconciliation = sectionsOf(detail, h => /^Reconciliation:/.test(h))[0] || null;
    const courtsSection = ((detail && detail.sections) || []).find(s => s && Array.isArray(s.items)) || null;
    const courts = ((courtsSection && courtsSection.items) || []).map(item => {
      const id = String(item.id || '');
      const at = id.indexOf('@');
      const rowMatch = /^([\d,]+)\s+judge rows/.exec(String(item.subtitle || ''));
      return {slug: at >= 0 ? id.slice(at + 1) : '', title: text(item.title), rows: rowMatch ? Number(rowMatch[1].replace(/,/g, '')) : null};
    }).filter(c => c.slug && c.title);

    let rowsTotal = null;
    for (const section of previews) {
      const m = /\bof\s+([\d,]+)\s+data rows/.exec(String(section.heading || ''));
      if (m) { rowsTotal = Number(m[1].replace(/,/g, '')); break; }
    }
    if (rowsTotal === null && perJudge) {
      const m = /\bof\s+([\d,]+)\s+rows/.exec(String(perJudge.heading || ''));
      if (m) rowsTotal = Number(m[1].replace(/,/g, ''));
    }

    let countLabel = '', asOf = '';
    if (perJudge) {
      const m = /^Per-judge totals as printed \(([^;]+);\s*([^)]*)\)/.exec(String(perJudge.heading || ''));
      if (m) { countLabel = text(m[1]); asOf = text(m[2]); }
    }

    return {previews, perJudge, reconciliation, courts, rowsTotal, countLabel, asOf,
            isPerJudge: Boolean(perJudge && courts.length) || Boolean(perJudge)};
  }

  // Pick a label column and a numeric column out of one preview section.
  function chartFromPreview(section) {
    if (!section || !Array.isArray(section.rows) || !section.rows.length) return null;
    const header = section.header || [];
    let best = null;
    for (let c = 1; c < header.length; c++) {
      let numeric = 0, filled = 0;
      for (const row of section.rows) {
        if (text(row[c])) filled++;
        if (parseNumber(row[c]) !== null) numeric++;
      }
      if (numeric < 3 || !filled || numeric / filled < 0.8) continue;
      const head = stripMarks(header[c]);
      // A publisher percent column is still the publisher's own cell, but it makes a poor
      // bar next to counts, so a count column wins whenever one exists.
      const score = numeric - (/%|percent/i.test(head) ? 1000 : 0) - c;
      if (!best || score > best.score) best = {index: c, head: head || 'Value', score};
    }
    if (!best) return null;

    let rows = section.rows
      .map(row => ({label: stripMarks(row[0]), value: parseNumber(row[best.index])}))
      .filter(row => row.value !== null);
    if (!rows.length) return null;
    const labelled = rows.filter(row => row.label).length;
    if (labelled / rows.length < 0.6) return null;   // transposed sheet: no usable row labels
    rows = rows.filter(row => row.label);

    const kept = rows.filter(row => !isAggregateLabel(row.label));
    let omitted = 0;
    if (kept.length >= 3) { omitted = rows.length - kept.length; rows = kept; }

    const years = rows.every(row => /^(19|20)\d{2}\b/.test(row.label));
    const total = rows.length;
    if (!years) rows = rows.slice().sort((a, b) => Math.abs(b.value) - Math.abs(a.value));
    const shown = rows.slice(0, BARS);
    const caption = years
      ? shown.length + ' of ' + total + ' rows, in the order printed · ' + best.head
      : (shown.length < total ? 'Top ' + shown.length + ' of ' + total + ' rows' : 'All ' + total + ' rows') +
        ' by ' + best.head + (omitted ? ' · total and subtotal rows omitted' : '');
    return {rows: shown, caption, column: best.head};
  }

  function chartFromCjra(section) {
    if (!section || !Array.isArray(section.rows)) return null;
    const header = (section.header || []).map(h => text(h));
    const nameIndex = header.findIndex(h => /judge name/i.test(h));
    const countIndex = header.findIndex(h => /^count$/i.test(h));
    if (nameIndex < 0 || countIndex < 0) return null;
    const rows = section.rows
      .map(row => ({label: stripMarks(row[nameIndex]), value: parseNumber(row[countIndex])}))
      .filter(row => row.label && row.value !== null);
    if (!rows.length) return null;
    const sum = rows.reduce((acc, row) => acc + row.value, 0);
    const ordered = rows.slice().sort((a, b) => b.value - a.value).slice(0, BARS);
    return {rows: ordered, sum, total: rows.length,
            caption: (ordered.length < rows.length ? 'Top ' + ordered.length + ' of ' + rows.length : 'All ' + rows.length) +
              ' judge rows by count, as printed'};
  }

  /* ------------------------------------------------------------------- drawing */
  function bars(spec, options) {
    const opts = options || {};
    const figure = el('figure', 'sv-figure');
    const grid = el('div', 'sv-bars');
    const scale = spec.rows.reduce((max, row) => Math.max(max, Math.abs(row.value)), 0) || 1;
    for (const row of spec.rows) {
      const label = el('span', 'sv-bar-label', row.label);
      label.title = row.label;
      const track = el('span', 'sv-bar-track');
      track.setAttribute('aria-hidden', 'true');
      const fill = el('span', 'sv-bar-fill' + (row.value < 0 ? ' sv-neg' : ''));
      fill.style.width = Math.max(1.2, (Math.abs(row.value) / scale) * 100) + '%';
      track.append(fill);
      append(grid, label, track, el('span', 'sv-bar-value', count(row.value)));
    }
    figure.append(grid);
    const caption = [spec.caption, opts.note].filter(Boolean).join(' · ');
    if (caption) figure.append(el('figcaption', 'sv-figcaption', caption));
    return figure;
  }

  function numericColumns(header, rows) {
    const width = (header || []).length || (rows[0] || []).length;
    const flags = [];
    for (let c = 0; c < width; c++) {
      let numeric = 0, filled = 0;
      for (const row of rows) { if (text(row[c])) filled++; if (parseNumber(row[c]) !== null) numeric++; }
      flags.push(c > 0 && filled > 0 && numeric / filled >= 0.8 && numeric >= 2);
    }
    return flags;
  }

  function gridTable(header, rows, caption, limit) {
    const scroll = el('div', 'sv-scroll');
    const table = el('table', 'sv-table');
    const body = rows.slice(0, limit || rows.length);
    const flags = numericColumns(header, body);
    if (caption) table.append(el('caption', '', caption));
    if ((header || []).length) {
      const head = el('thead'), tr = el('tr');
      header.forEach((cell, index) => {
        const th = el('th', flags[index] ? 'sv-num' : '', text(cell));
        th.scope = 'col';
        tr.append(th);
      });
      head.append(tr);
      table.append(head);
    }
    const tbody = el('tbody');
    for (const row of body) {
      const tr = el('tr');
      for (let c = 0; c < flags.length; c++) {
        const raw = row[c] === null || row[c] === undefined ? '' : String(row[c]);
        const td = el('td', flags[c] ? 'sv-num' : (c === 0 ? 'sv-wrap' : ''), raw);
        tr.append(td);
      }
      tbody.append(tr);
    }
    table.append(tbody);
    scroll.append(table);
    return scroll;
  }

  // A five-row peek at a sheet whose rows carry no comparable numeric column. Blank rows and
  // columns that are blank across the peek are left out so the card stays readable; the
  // caption says when that happened. Cell text is never altered.
  function tidyPreview(section, rowsTotal) {
    const source = (section.rows || []).filter(row => row.some(cell => text(cell)));
    if (!source.length) return null;
    const rows = source.slice(0, MINI_ROWS);
    const header = section.header || [];
    const width = Math.max(header.length, ...rows.map(row => row.length));
    const keep = [];
    for (let c = 0; c < width; c++) if (rows.some(row => text(row[c]))) keep.push(c);
    if (keep.length < 2) return null;
    const dropped = keep.length > 4 || keep.length < width;
    const columns = keep.slice(0, 4);
    return {
      header: columns.map(c => header[c] === undefined ? '' : header[c]),
      rows: rows.map(row => columns.map(c => row[c] === undefined ? '' : row[c])),
      caption: 'First ' + rows.length + ' of ' + count(rowsTotal !== null ? rowsTotal : source.length) +
        ' rows, as printed' + (dropped ? ' · blank columns hidden' : ''),
    };
  }

  /* ---------------------------------------------------------------- KPI header */
  function kpi(label, value, note) {
    const tile = el('article', 'sv-kpi');
    const strong = el('strong', 'sv-kpi-value', value === null ? '—' : count(value));
    if (value === null) strong.classList.add('sv-wait');
    append(tile, el('div', 'sv-kpi-label', label), strong, el('div', 'sv-kpi-note', note || ''));
    return {tile, strong};
  }

  function renderKpis(root, data, cjra, signal) {
    const box = el('div', 'sv-kpis');
    box.setAttribute('aria-label', 'Collection at a glance');
    const tables = facetTotal(data, 'format');
    const seriesFacet = facet(data, 'series');
    const seriesNames = ((seriesFacet && seriesFacet.options) || []).slice(0, 3).map(o => text(o.label)).join(' · ');
    const periodFacet = facet(data, 'period');
    const dated = ((periodFacet && periodFacet.options) || []).filter(o => o.value !== 'undated');
    const undated = ((periodFacet && periodFacet.options) || []).find(o => o.value === 'undated');
    const isoList = dated.map(o => String(o.value)).sort();
    const span = isoList.length
      ? date(isoList[0]) + (isoList.length > 1 ? ' – ' + date(isoList[isoList.length - 1]) : '')
      : 'No period stated';

    append(box,
      kpi('Official tables saved', tables, 'Administrative Office of the U.S. Courts · latest periods only').tile,
      kpi('Report series', (seriesFacet && seriesFacet.options || []).length, seriesNames || 'Publication families as listed').tile,
      kpi('Reporting periods', dated.length, span + (undated && undated.count ? ' · ' + count(undated.count) + ' state no period' : '')).tile);

    const rowsTile = kpi('CJRA per-judge rows', cjra ? cjra.rows : null, cjra && cjra.period ? cjra.period : 'Civil Justice Reform Act report');
    const courtsTile = kpi('Courts in CJRA tables', cjra && cjra.courts !== null ? cjra.courts : null,
      cjra && cjra.period ? cjra.period + ' · as printed' : 'Courts named in the per-judge report');
    append(box, rowsTile.tile, courtsTile.tile);
    root.append(box);

    // The court count comes from the per-judge table's own list of courts; filled in when it arrives.
    if (cjra && cjra.courts === null && cjra.perJudge.length) {
      enqueue(() => loadDetail(cjra.perJudge[0].id, signal).then(detail => {
        const courts = analyse(detail).courts.length;
        if (!courts) return;
        cjra.courts = courts;
        if (courtsTile.strong.isConnected) {
          courtsTile.strong.textContent = count(courts);
          courtsTile.strong.classList.remove('sv-wait');
        }
      }).catch(() => {}));
    }
  }

  /* ------------------------------------------------------------- control strip */
  function currentView() { return route.params.get('view') === 'table' ? 'table' : 'charts'; }

  function renderControls(root, data, redraw) {
    const form = el('form', 'sv-controls');
    form.setAttribute('aria-label', 'Filter and display the saved tables');
    const fields = {};
    for (const name of ['series', 'topic', 'period']) {
      const spec = facet(data, name);
      if (!spec) continue;
      const current = route.params.get(name) || '';
      const field = filterField(spec.label || human(name), 'sv-' + name, 'select', current);
      setOptions(field.input, (spec.options || []).map(o => ({value: o.value, label: (o.label || o.value) + ' (' + count(o.count) + ')'})),
        'All ' + String(spec.label || name).toLowerCase(), current);
      fields[name] = field.input;
      form.append(field.label);
    }
    const search = filterField('Search titles', 'sv-q', 'search', route.params.get('q') || '', 'Table number, court, subject…');
    fields.q = search.input;
    form.append(search.label);

    const actions = el('div', 'sv-actions');
    const group = el('div', 'sv-toggle');
    group.setAttribute('role', 'group');
    group.setAttribute('aria-label', 'Display results as');
    const view = currentView();
    for (const [value, label, glyph] of [['charts', 'Charts', '▤'], ['table', 'Table', '≣']]) {
      const button = action(label, () => setView(value, redraw));
      button.classList.remove('button');
      button.dataset.view = value;
      button.setAttribute('aria-pressed', String(view === value));
      const mark = el('span', '', glyph);
      mark.setAttribute('aria-hidden', 'true');
      button.prepend(mark);
      group.append(button);
    }
    const clear = action('Clear', () => navigate(AREA, {view}));
    clear.classList.add('sv-clear');
    append(actions, group, clear);
    form.append(actions);

    function submit() {
      const params = {view: currentView()};
      for (const [name, input] of Object.entries(fields)) { const value = String(input.value || '').trim(); if (value) params[name] = value; }
      navigate(AREA, params);
    }
    form.addEventListener('submit', event => { event.preventDefault(); submit(); });
    for (const select of form.querySelectorAll('select')) select.addEventListener('change', submit);
    root.append(form);
    return group;
  }

  // The view choice lives in the hash so it survives a reload and can be linked, but it does
  // not need another round trip: the listing is already in hand.
  function setView(value, redraw) {
    if (currentView() === value) return;
    const params = Object.fromEntries(route.params);
    params.view = value;
    history.replaceState(null, '', makeHash(AREA, params));
    route = readRoute();
    redraw();
  }

  function renderMeta(root, data) {
    const total = facetTotal(data, 'format');
    const matched = Number(data.total) || 0;
    const listed = (data.results || []).length;
    const line = el('p', 'sv-meta');
    const tail = PUBLISHER_NOTE + ' for the period each table states; no rate is computed here.';
    append(line,
      el('strong', '', count(listed) + (listed === 1 ? ' table' : ' tables')),
      el('span', '', listed === total ? 'Every saved table · ' + tail
        : (listed < matched ? 'of ' + count(matched) + ' matching (' + count(total) + ' saved) · ' + tail
                            : 'of ' + count(total) + ' saved · ' + tail)));
    const note = dataNote(data.qualification);
    if (note) line.append(note);
    root.append(line);
  }

  /* -------------------------------------------------------------- charts view */
  function cardShell(record) {
    const card = el('article', 'sv-card');
    const cells = record.cells || {};
    const eyebrow = [text(cells.series), text(cells.table) && text(cells.table) !== '—' ? 'Table ' + text(cells.table) : ''].filter(Boolean).join(' · ');
    if (eyebrow) card.append(el('p', 'sv-card-eyebrow', eyebrow));
    const title = el('h3', '', cleanTitle(record.title) || text(record.title));
    title.title = text(record.title);
    card.append(title);
    card.append(el('p', 'sv-card-meta', [text(cells.period) || 'Period not stated', PUBLISHER_NOTE].join(' · ')));
    const slot = skeleton(4);
    card.append(slot);
    const foot = el('div', 'sv-card-foot');
    const open = action('Open table', event => openDetail(record.id, event.currentTarget));
    open.classList.remove('button');
    open.classList.add('sv-mini-button');
    foot.append(open);
    const original = originalLink(record);
    if (original) {
      const anchor = link('Original file ↗', original.url, false, 'sv-mini-button');
      if (anchor) foot.append(anchor);
    }
    card.append(foot);
    return {card, slot};
  }

  function fillCard(slot, record, detail, signal) {
    const facts = analyse(detail);
    if (facts.perJudge && facts.courts.length) { fillCjraCard(slot, record, detail, facts, signal); return; }

    let spec = null;
    for (const preview of facts.previews) { spec = chartFromPreview(preview); if (spec) break; }
    if (!spec && facts.perJudge) spec = chartFromCjra(facts.perJudge);
    if (spec) { slot.replaceChildren(bars(spec)); return; }

    const fallback = facts.previews[0] || facts.perJudge;
    const mini = fallback ? tidyPreview(fallback, facts.rowsTotal) : null;
    if (mini) { slot.replaceChildren(gridTable(mini.header, mini.rows, mini.caption)); return; }
    const format = text((record.cells || {}).format) || 'file';
    slot.replaceChildren(el('p', 'sv-note',
      'No rows were extracted from this ' + format + '. The registered original is linked below.'));
  }

  function fillCjraCard(slot, record, detail, facts, signal) {
    const courts = facts.courts;
    const chosen = courtChoice.get(record.id) || courts[0].slug;
    courtChoice.set(record.id, chosen);

    const wrap = el('div', 'sv-block');
    const label = el('label', 'sv-court', 'Court (as printed)');
    const select = el('select');
    select.id = 'sv-court-' + Math.random().toString(36).slice(2, 8);
    label.htmlFor = select.id;
    for (const court of courts) {
      const option = el('option', '', court.title + (court.rows !== null ? ' (' + count(court.rows) + ' judges)' : ''));
      option.value = court.slug;
      select.append(option);
    }
    select.value = chosen;
    label.append(select);
    const chartBox = el('div', 'sv-block');
    append(wrap, label, chartBox);
    slot.replaceChildren(wrap);

    function draw(slug) {
      chartBox.replaceChildren(skeleton(3));
      enqueue(() => loadDetail(record.id + '@' + slug, signal).then(payload => {
        if (select.value !== slug || !chartBox.isConnected) return;
        const section = sectionsOf(payload, h => /^Per-judge totals as printed/.test(h))[0];
        const spec = chartFromCjra(section);
        if (!spec) { chartBox.replaceChildren(el('p', 'sv-note', 'No numeric rows were printed for this court.')); return; }
        const figure = bars(spec, {note: facts.countLabel});
        const sum = el('p', 'sv-sum', 'Sum of the rows shown (' + count(spec.total) + ' judge rows): ');
        sum.append(el('b', '', count(spec.sum)));
        chartBox.replaceChildren(figure, sum);
      }).catch(error => {
        if (error && error.name === 'AbortError') return;
        chartBox.replaceChildren(el('p', 'sv-note', 'This court’s rows could not be read just now.'));
      }));
    }
    select.addEventListener('change', () => { courtChoice.set(record.id, select.value); draw(select.value); });
    draw(chosen);
  }

  function renderCharts(target, data, signal) {
    const grid = el('div', 'sv-grid');
    for (const record of data.results) {
      const {card, slot} = cardShell(record);
      grid.append(card);
      watch(card, () => {
        enqueue(() => loadDetail(record.id, signal).then(detail => {
          if (!slot.isConnected) return;
          fillCard(slot, record, detail, signal);
        }).catch(error => {
          if (error && error.name === 'AbortError' || !slot.isConnected) return;
          slot.replaceChildren(el('p', 'sv-note', 'This table could not be read just now.'));
        }));
      });
    }
    target.append(grid);
  }

  /* --------------------------------------------------------------- table view */
  const COLUMNS = [
    {key: 'table', label: 'Table', sort: r => natural((r.cells || {}).table)},
    {key: 'title', label: 'Title', sort: r => natural(cleanTitle(r.title) || r.title), wide: true},
    {key: 'series', label: 'Series', sort: r => natural((r.cells || {}).series)},
    {key: 'period', label: 'Period', sort: r => periodKey(r.id) || '0000-00-00'},
    {key: 'format', label: 'Format', sort: r => natural((r.cells || {}).format)},
    {key: 'rows', label: 'Rows', numeric: true, sort: r => {
      const known = knownRows(r);
      return known === null ? -1 : known;
    }},
  ];

  function knownRows(record) {
    const detail = detailCache.get(record.id);
    if (detail) {
      const total = analyse(detail).rowsTotal;
      if (total !== null) return total;
    }
    const badge = badgeRows(record);
    return badge === null ? null : badge;
  }

  function renderTable(target, data, signal, redraw) {
    const scroll = el('div', 'sv-scroll');
    const table = el('table', 'sv-table sv-list');
    const head = el('thead'), headRow = el('tr');
    for (const column of COLUMNS) {
      const th = el('th', column.numeric ? 'sv-num' : '');
      th.scope = 'col';
      if (column.key === 'rows') th.title = 'Data rows the saved preview reports, where it states them';
      if (sort.key === column.key) th.setAttribute('aria-sort', sort.dir === 1 ? 'ascending' : 'descending');
      const button = el('button', 'sv-sort' + (column.numeric ? ' sv-num-head' : ''));
      button.type = 'button';
      const mark = el('i', '', sort.key === column.key ? (sort.dir === 1 ? '▲' : '▼') : '↕');
      mark.setAttribute('aria-hidden', 'true');
      append(button, document.createTextNode(column.label), mark);
      button.setAttribute('aria-label', 'Sort by ' + column.label);
      button.addEventListener('click', () => {
        sort = sort.key === column.key ? {key: column.key, dir: -sort.dir} : {key: column.key, dir: column.key === 'rows' ? -1 : 1};
        redraw();
      });
      th.append(button);
      headRow.append(th);
    }
    head.append(headRow);
    table.append(head);

    const rows = data.results.slice();
    const column = COLUMNS.find(c => c.key === sort.key);
    if (column) {
      rows.sort((a, b) => {
        const left = column.sort(a), right = column.sort(b);
        if (left === right) return natural(a.title).localeCompare(natural(b.title));
        return (left > right ? 1 : -1) * sort.dir;
      });
    }

    const body = el('tbody');
    for (const record of rows) {
      const cells = record.cells || {};
      const tr = el('tr');
      append(tr,
        el('td', '', text(cells.table) || '—'));
      const titleCell = el('td', 'sv-wrap');
      const button = el('button', 'sv-rowtitle', cleanTitle(record.title) || text(record.title));
      button.type = 'button';
      button.title = text(record.title);
      button.addEventListener('click', event => { event.stopPropagation(); openDetail(record.id, event.currentTarget); });
      titleCell.append(button);
      const sub = text(record.subtitle).split(' · ').filter(part => part && part !== text(cells.series)).join(' · ');
      if (sub) titleCell.append(el('div', 'sv-rowsub', sub));
      tr.append(titleCell);
      append(tr,
        el('td', '', text(cells.series)),
        el('td', '', text(cells.period) || 'Not stated'),
        el('td', '', text(cells.format)));
      const rowsCell = el('td', 'sv-num', knownRows(record) === null ? '…' : count(knownRows(record)));
      tr.append(rowsCell);
      tr.addEventListener('click', () => openDetail(record.id, button));
      body.append(tr);

      if (knownRows(record) === null) {
        watch(tr, () => {
          enqueue(() => loadDetail(record.id, signal).then(() => {
            if (!rowsCell.isConnected) return;
            const total = knownRows(record);
            rowsCell.textContent = total === null ? '—' : count(total);
          }).catch(() => { if (rowsCell.isConnected) rowsCell.textContent = '—'; }));
        });
      }
    }
    table.append(body);
    scroll.append(table);
    target.append(scroll);
  }

  /* ----------------------------------------------------------- lazy observation */
  function watch(node, run) {
    if (typeof IntersectionObserver !== 'function') { run(); return; }
    if (!observer) {
      observer = new IntersectionObserver(entries => {
        for (const entry of entries) {
          if (!entry.isIntersecting) continue;
          observer.unobserve(entry.target);
          const job = entry.target.__svJob;
          entry.target.__svJob = null;
          if (job) job();
        }
      }, {rootMargin: '320px 0px'});
    }
    node.__svJob = run;
    observer.observe(node);
  }
  function resetObserver() {
    if (observer) { observer.disconnect(); observer = null; }
    resetQueue();
  }

  /* ------------------------------------------------------------- detail panel */
  const TECHNICAL = /\b(sha-?256|file url|size|source page|record id|file id|checksum|hash)\b/i;
  const FACT_ORDER = ['Table', 'Series', 'Publication (as listed)', 'Topic', 'Reporting period (as printed)',
    'Period end / as-of date', 'Period start', 'Format', 'Sheets', 'PDF pages',
    'Saved (capture time, from the fetch receipt)', 'Published', 'Title inside the file'];

  function definitionList(pairs) {
    const list = el('dl', 'sv-dl');
    for (const [key, value] of pairs) {
      if (!text(value)) continue;
      append(list, el('dt', '', text(key)), el('dd', '', text(value)));
    }
    return list;
  }

  function openDetail(id, trigger) {
    if (typeof lastTrigger !== 'undefined' && trigger) lastTrigger = trigger;
    const body = document.querySelector('#record-body');
    if (!dialog.open) dialog.showModal();
    loading(body, 'Opening the saved table…');
    loadDetail(id).then(detail => {
      if (!dialog.open) return;
      body.removeAttribute('aria-busy');
      body.replaceChildren(detailPanel(detail));
      const close = document.querySelector('#close-record');
      if (close) close.focus();
    }).catch(() => {
      body.removeAttribute('aria-busy');
      body.replaceChildren(el('p', 'sv-note', 'This table could not be opened. The local archive server may have stopped.'));
      announce('Could not open this table.');
    });
  }

  function detailPanel(detail) {
    const panel = el('div', 'sv-detail');
    const facts = analyse(detail);
    const title = el('h2', '', cleanTitle(detail.title) || text(detail.title));
    title.id = 'record-title';
    const head = el('div');
    append(head, title, detail.subtitle ? el('p', 'sv-detail-sub', text(detail.subtitle) + ' · ' + PUBLISHER_NOTE) : null);
    panel.append(head);

    const pairs = Array.isArray(detail.facts) ? detail.facts : [];
    const plain = pairs.filter(([key]) => !TECHNICAL.test(String(key)));
    const technical = pairs.filter(([key]) => TECHNICAL.test(String(key)));
    plain.sort((a, b) => {
      const left = FACT_ORDER.indexOf(String(a[0])), right = FACT_ORDER.indexOf(String(b[0]));
      return (left < 0 ? 99 : left) - (right < 0 ? 99 : right);
    });
    const list = definitionList(plain);
    if (list.children.length) {
      const block = el('section', 'sv-block');
      append(block, el('h3', '', 'This table'), list);
      panel.append(block);
    }

    // Per-judge tables: one court at a time, chosen from a select, instead of a wall of cards.
    if (facts.perJudge && facts.courts.length) panel.append(cjraBlock(detail, facts));
    else if (facts.perJudge) panel.append(tableBlock('Per-judge totals as printed', facts.perJudge, chartFromCjra(facts.perJudge), facts.countLabel));

    for (const preview of facts.previews) {
      const sheet = /sheet "([^"]+)"/.exec(String(preview.heading || ''));
      panel.append(tableBlock(sheet ? 'Sheet: ' + sheet[1] : 'Saved preview', preview, chartFromPreview(preview), ''));
    }
    if (facts.reconciliation) panel.append(tableBlock('Reconciliation against the report’s own summary table', facts.reconciliation, null, ''));

    if (!facts.previews.length && !facts.perJudge) {
      const only = sectionsOf(detail, h => /^Contents$/i.test(h))[0];
      panel.append(el('p', 'sv-note', text(only && only.text) ||
        'No rows were extracted from this file. Open the registered original below.'));
    }

    const row = el('div', 'button-row');
    for (const entry of detail.links || []) {
      const url = String(entry.url || '');
      const anchor = link(text(entry.label) + ' ↗', url, /^https?:/i.test(url));
      if (anchor) row.append(anchor);
    }
    if (row.children.length) panel.append(row);

    if (technical.length) {
      const box = el('details', 'data-note');
      append(box, el('summary', '', 'Technical details'), definitionList(technical));
      panel.append(box);
    }
    const note = dataNote(detail.qualification);
    if (note) panel.append(note);
    return panel;
  }

  // A titled block with a Chart / Table switch when the rows carry a numeric column.
  function tableBlock(label, section, spec, note) {
    const block = el('section', 'sv-block');
    const bar = el('div', 'sv-blockbar');
    bar.append(el('h3', '', label));
    block.append(bar);

    const caption = text(section.heading).replace(/^Preview: sheet "[^"]*"\s*\(?/, '').replace(/\)$/, '')
      .replace(/^[a-z]/, letter => letter.toUpperCase());
    const slot = el('div');
    const renderTableMode = () => slot.replaceChildren(gridTable(section.header || [], section.rows || [], caption, 200));
    if (spec) {
      const group = el('div', 'sv-toggle');
      group.setAttribute('role', 'group');
      group.setAttribute('aria-label', label + ': display as');
      let mode = 'table';
      const draw = () => {
        for (const button of group.children) button.setAttribute('aria-pressed', String(button.dataset.mode === mode));
        if (mode === 'chart') slot.replaceChildren(bars(spec, {note: note}));
        else renderTableMode();
      };
      for (const [value, text_] of [['chart', 'Chart'], ['table', 'Table']]) {
        const button = action(text_, () => { mode = value; draw(); });
        button.classList.remove('button');
        button.dataset.mode = value;
        group.append(button);
      }
      bar.append(group);
      draw();
    } else {
      renderTableMode();
    }
    block.append(slot);
    return block;
  }

  function cjraBlock(detail, facts) {
    const block = el('section', 'sv-block');
    const bar = el('div', 'sv-blockbar');
    bar.append(el('h3', '', 'Per-judge totals as printed'));
    block.append(bar);
    if (facts.countLabel) block.append(el('p', 'sv-note', 'Counts of ' + facts.countLabel + (facts.asOf ? ' · ' + facts.asOf : '') +
      ' · ' + PUBLISHER_NOTE + '. Judge names are shown exactly as printed and are not linked to judge profiles.'));

    const label = el('label', 'sv-court', 'Court (as printed)');
    const select = el('select');
    select.id = 'sv-detail-court';
    label.htmlFor = select.id;
    const first = el('option', '', 'First ' + count((facts.perJudge.rows || []).length) + ' rows of the report, in document order');
    first.value = '';
    select.append(first);
    for (const court of facts.courts) {
      const option = el('option', '', court.title + (court.rows !== null ? ' (' + count(court.rows) + ' judges)' : ''));
      option.value = court.slug;
      select.append(option);
    }
    label.append(select);
    block.append(label);

    const slot = el('div', 'sv-block');
    block.append(slot);

    const show = section => {
      const spec = chartFromCjra(section);
      const inner = tableBlockBody(section, spec, facts.countLabel);
      slot.replaceChildren(inner);
    };
    show(facts.perJudge);

    select.addEventListener('change', () => {
      const slug = select.value;
      if (!slug) { show(facts.perJudge); return; }
      slot.replaceChildren(el('p', 'sv-note', 'Reading this court’s rows…'));
      loadDetail(detail.id.split('@')[0] + '@' + slug).then(payload => {
        if (select.value !== slug) return;
        const section = sectionsOf(payload, h => /^Per-judge totals as printed/.test(h))[0];
        if (!section) { slot.replaceChildren(el('p', 'sv-note', 'No rows were printed for this court.')); return; }
        show(section);
      }).catch(() => slot.replaceChildren(el('p', 'sv-note', 'This court’s rows could not be read just now.')));
    });
    return block;
  }

  // One court at a time repeats its own name on every row; that column is dropped while a
  // single court is shown, because the select above already names it.
  function withoutConstantColumns(section) {
    const header = section.header || [], rows = section.rows || [];
    if (rows.length < 2 || header.length < 3) return section;
    const keep = [];
    for (let c = 0; c < header.length; c++) {
      const first = text(rows[0][c]);
      if (!first || !rows.every(row => text(row[c]) === first)) keep.push(c);
    }
    if (keep.length === header.length || keep.length < 2) return section;
    return {heading: section.heading, header: keep.map(c => header[c]), rows: rows.map(row => keep.map(c => row[c]))};
  }

  // The switch + panel body used inside the per-judge block (no extra heading).
  function tableBlockBody(input, spec, note) {
    const section = withoutConstantColumns(input);
    const wrap = el('div', 'sv-block');
    const bar = el('div', 'sv-blockbar');
    const caption = text(input.heading).replace(/^Per-judge totals as printed \([^)]*\)\s*-?\s*/, '');
    bar.append(el('span', '', caption));
    const slot = el('div');
    const renderTableMode = () => slot.replaceChildren(gridTable(section.header || [], section.rows || [], '', 400));
    if (spec) {
      const group = el('div', 'sv-toggle');
      group.setAttribute('role', 'group');
      group.setAttribute('aria-label', 'Per-judge totals: display as');
      let mode = 'table';
      const draw = () => {
        for (const button of group.children) button.setAttribute('aria-pressed', String(button.dataset.mode === mode));
        if (mode === 'chart') {
          const figure = bars(spec, {note: note});
          const sum = el('p', 'sv-sum');
          append(sum, document.createTextNode('Sum of the rows shown (' + count(spec.total) + ' judge rows): '), el('b', '', count(spec.sum)));
          slot.replaceChildren(figure, sum);
        } else renderTableMode();
      };
      for (const [value, label] of [['chart', 'Chart'], ['table', 'Table']]) {
        const button = action(label, () => { mode = value; draw(); });
        button.classList.remove('button');
        button.dataset.mode = value;
        group.append(button);
      }
      bar.append(group);
      wrap.append(bar);
      draw();
    } else {
      wrap.append(bar);
      renderTableMode();
    }
    wrap.append(slot);
    return wrap;
  }

  /* ------------------------------------------------------------------ the page */
  async function page(signal) {
    injectStyle();
    resetObserver();

    const params = new URLSearchParams();
    for (const name of ['q', 'series', 'topic', 'period']) {
      const value = route.params.get(name);
      if (value) params.set(name, value);
    }
    params.set('limit', '100');
    params.set('page', '1');

    const data = await api(API + '?' + params.toString(), signal);
    if (signal.aborted) return;
    if (!data.available) {
      main.append(el('p', 'sv-note', text(data.reason) || 'This data layer is not available yet.'));
      return;
    }
    let cjra = null;
    try { cjra = await loadCjraSummary(signal); } catch (error) { if (error && error.name === 'AbortError') return; }
    if (signal.aborted) return;

    const root = el('section', 'sv');
    main.append(root);

    const body = el('div', 'sv-body');
    const redraw = () => {
      resetObserver();
      body.replaceChildren();
      for (const button of root.querySelectorAll('.sv-controls .sv-toggle button')) {
        button.setAttribute('aria-pressed', String(button.dataset.view === currentView()));
      }
      draw();
    };
    function draw() {
      if (!data.results || !data.results.length) {
        emptyState(body, 'Nothing matches', 'Remove a filter or search for a table number, a court or a subject.', () => navigate(AREA, {view: currentView()}));
        return;
      }
      if (currentView() === 'table') renderTable(body, data, signal, redraw);
      else renderCharts(body, data, signal);
    }

    renderKpis(root, data, cjra, signal);
    renderControls(root, data, redraw);
    renderMeta(root, data);
    root.append(body);
    draw();
    announce(count(data.total) + ' saved court statistics tables, shown as ' + (currentView() === 'table' ? 'a table' : 'charts') + '.');
  }

  window.StatsViz = {page};
})();
