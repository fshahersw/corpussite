/* Law reader: a heading-by-heading browser for the saved law collection, a reading layout for provision text, and
   previous/next navigation. Self-contained; styles live in styles.css. Display only: wording is never changed. */
(function () {
  'use strict';
  const h = (tag, cls, text) => { const node = document.createElement(tag); if (cls) node.className = cls; if (text != null) node.textContent = text; return node; };
  const fmt = value => Number(value || 0).toLocaleString('en-US');
  async function get(path) { const response = await fetch(path); if (!response.ok) throw new Error('Request failed'); return response.json(); }
  const query = values => Object.entries(values).filter(([, v]) => v !== '' && v != null).map(([k, v]) => `${k}=${encodeURIComponent(v)}`).join('&');

  /* ---------- one heading from a citation and a publisher title, without saying the number twice ---------- */
  function title(citation, heading) {
    const cite = String(citation || '').trim(), text = String(heading || '').trim();
    if (!cite) return text; if (!text || text === cite) return cite;
    const num = (cite.replace(/\s*\(\d{4}\)\s*$/, '').match(/([\w.:\-–]+)$/) || [])[1] || '';
    let rest = text;
    if (num) {
      const escaped = num.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
      rest = text.replace(new RegExp('^(?:§+\\s*|Rule\\s+|Section\\s+|Sec\\.\\s+)?' + escaped + '\\.?(?:\\s+|$)', 'i'), '').trim();
    }
    return rest ? `${cite} — ${rest}` : cite;
  }

  /* ---------- reading layout ---------- */
  const ENUM = /^\(([a-z]{1,4}|[A-Z]{1,4}|\d{1,3})\)/;
  const ROMAN_LOWER = /^(?:i{1,3}|iv|v|vi{0,3}|ix|x|xi{0,3}|xiv|xv|xvi{0,3}|xix|xx)$/;
  function kindOf(token, previous) {
    if (/^\d+$/.test(token)) return 'digit';
    const lower = token === token.toLowerCase();
    const roman = ROMAN_LOWER.test(token.toLowerCase());
    if (roman) {
      // "(i)" after "(h)" continues the letters; otherwise a roman-looking token is a roman numeral level
      const alphaKind = lower ? 'alpha' : 'ALPHA', last = previous[alphaKind];
      if (token.length === 1 && last && last.length === 1 && last.charCodeAt(0) + 1 === token.charCodeAt(0) && !previous.justOpened) return alphaKind;
      if (!(token.length === 1 && /^[a-hj-uwyz]$/i.test(token))) return lower ? 'roman' : 'ROMAN';
    }
    return lower ? 'alpha' : 'ALPHA';
  }
  function splitInline(block) {
    // A one-block provision ("… the following: (1) … ; (2) …"): break before an enumerator that follows closing punctuation.
    return block.replace(/([.;:—]|; and|; or|, and|, or) (?=\((?:[a-z]{1,3}|[A-Z]{1,2}|\d{1,2})\) )/g, '$1\n');
  }
  function paragraphs(text) {
    let source = String(text || '').replace(/\r\n?/g, '\n');
    const lines = source.split('\n');
    const hardWrapped = lines.length > 8 && lines.filter(line => line.length > 55 && line.length < 82).length > lines.length * 0.6;
    if (hardWrapped) {
      // Fixed-width source (lines cut near 72 characters): a new paragraph starts at an indented or blank line.
      const out = []; let current = '';
      for (const line of lines) {
        if (!line.trim()) { if (current) out.push(current); current = ''; continue; }
        if (/^\s{2,}\S/.test(line) && current) { out.push(current); current = line.trim(); continue; }
        current = current ? current + ' ' + line.trim() : line.trim();
      }
      if (current) out.push(current);
      return out;
    }
    const blocks = source.split(/\n\s*\n|\n(?=\s*\([a-zA-Z0-9]{1,4}\)\s)/).map(s => s.replace(/\s*\n\s*/g, ' ').trim()).filter(Boolean);
    const out = [];
    for (const block of blocks) {
      if (block.length > 700 && (block.match(/\((?:[a-z]|[A-Z]|\d{1,2})\) /g) || []).length >= 3) out.push(...splitInline(block).split('\n').map(s => s.trim()).filter(Boolean));
      else out.push(block);
    }
    return out;
  }
  function format(text) {
    const box = h('div', 'lawtext');
    const stack = []; const previous = {};
    for (const paragraph of paragraphs(text)) {
      let rest = paragraph, level = null, labels = '';
      previous.justOpened = false;
      for (let guard = 0; guard < 4; guard++) {
        const found = rest.match(ENUM); if (!found) break;
        const kind = kindOf(found[1], previous);
        const at = stack.indexOf(kind);
        if (at === -1) { stack.push(kind); previous.justOpened = true; } else stack.length = at + 1;
        previous[kind] = found[1];
        if (level === null) level = stack.length - 1;
        labels += found[0]; rest = rest.slice(found[0].length);
        if (!/^\(/.test(rest)) break;
      }
      const p = h('p', 'lawtext-p');
      if (level === null) { p.textContent = paragraph; p.dataset.level = String(Math.min(stack.length ? 0 : 0, 5)); }
      else { p.dataset.level = String(Math.min(level, 5)); p.append(h('span', 'lawtext-enum', labels), document.createTextNode(rest)); }
      box.append(p);
    }
    return box;
  }
  function reading(text) {
    // Formatted view with a switch back to the text exactly as saved.
    const wrap = h('div', 'lawtext-wrap'), bar = h('div', 'lawtext-bar');
    const note = h('span', 'lawtext-note', 'Paragraph breaks and indentation are added for reading; the wording is as saved.');
    const toggle = h('button', 'lawtext-toggle', 'Show as saved'); toggle.type = 'button';
    let formatted = true, body = format(text);
    toggle.addEventListener('click', () => {
      formatted = !formatted; toggle.textContent = formatted ? 'Show as saved' : 'Show reading layout';
      const next = formatted ? format(text) : Object.assign(h('pre', 'lawtext-raw'), { textContent: String(text || '') });
      body.replaceWith(next); body = next;
    });
    bar.append(note, toggle); wrap.append(bar, body); return wrap;
  }

  /* ---------- where a provision sits, previous / next ---------- */
  async function context(host, recordId, options) {
    const open = (options && options.open) || (() => {});
    try {
      const data = await get('/api/law-outline/context?' + query({ id: recordId }));
      if (!data.available) return;
      const bar = h('nav', 'lawctx'); bar.setAttribute('aria-label', 'Position in the code');
      const crumbs = h('ol', 'lawctx-path');
      const first = h('li'); const home = h('a', '', `${data.state} · ${data.kind_label}`); home.href = `#laws?${query({ state: data.state, toc: data.kind })}`; first.append(home); crumbs.append(first);
      for (const step of data.path) { const li = h('li'); const a = h('a', '', step.label); a.href = `#laws?${query({ state: data.state, toc: data.kind, node: step.id })}`; li.append(a); crumbs.append(li); }
      const pager = h('div', 'lawctx-pager');
      const button = (label, target, side) => {
        const b = h('button', 'lawctx-step'); b.type = 'button'; b.disabled = !target;
        b.append(h('span', 'lawctx-arrow', side === 'previous' ? '‹' : '›'), h('span', 'lawctx-cite', target ? (target.citation || target.title) : label));
        if (side === 'next') b.append(b.firstChild);
        b.title = target ? [target.citation, target.title].filter(Boolean).join(' — ') : '';
        if (target) b.addEventListener('click', () => open(target.id));
        return b;
      };
      pager.append(button('Start of heading', data.previous, 'previous'), h('span', 'lawctx-place', data.of ? `${fmt(data.position)} of ${fmt(data.of)}` : ''), button('End of heading', data.next, 'next'));
      bar.append(crumbs, pager); host.replaceChildren(bar);
    } catch (error) { /* navigation is optional */ }
  }

  /* ---------- saved documents that cite this provision ---------- */
  async function citedBy(host, recordId) {
    try {
      const data = await get('/api/citations/record?' + query({ id: recordId }));
      if (!data.available || !data.total) return;
      const box = h('section', 'lawcited');
      box.append(h('h3', 'lawcited-h', `Cited in ${fmt(data.total)} saved document${data.total === 1 ? '' : 's'}`));
      const list = h('ul', 'lawcited-list');
      for (const row of data.results) { const li = h('li'); const first = (row.links || [])[0]; const a = h('a', 'lawcited-title', row.title); a.href = first ? first.url : '#citation-index'; li.append(a, h('p', 'lawcited-sub', row.subtitle)); list.append(li); }
      const more = h('a', 'lawcited-more', 'Open in Cited authorities \u2192'); more.href = data.link || '#citation-index';
      box.append(list, more); host.replaceChildren(box);
    } catch (error) { /* optional block */ }
  }

  /* ---------- browse a jurisdiction heading by heading ---------- */
  async function browser(host, state, options) {
    const open = (options && options.open) || (() => {});
    const start = (options && options.start) || {};
    let data;
    try { data = await get('/api/law-outline?' + query({ state })); } catch (error) { return; }
    if (!data.available || !data.collections.length) return;
    const section = h('section', 'lawb');
    const head = h('div', 'lawb-head');
    const total = data.collections.reduce((sum, c) => sum + c.provisions, 0);
    head.append(h('h2', '', `${data.state} law, heading by heading`), h('p', 'lawb-sub', `${fmt(total)} provisions with full text. Open a code or title, then a chapter, then a section.`));
    if (data.statute_audit) { const a = data.statute_audit, line = h('p', 'lawb-audit' + (a.verdict === 'complete' ? '' : ' is-warn'));
      line.append(h('strong', '', 'Statutes: ' + a.verdict_label), document.createTextNode([a.containers, a.notes].filter(Boolean).map(s => ' \u00b7 ' + s).join('')));
      if (/^https?:/.test(a.official_source || '')) { const src = h('a', '', ' Official source \u2197'); src.href = a.official_source.split(' ')[0]; src.target = '_blank'; src.rel = 'noopener noreferrer'; line.append(src); }
      head.append(line); }
    const tabs = h('div', 'lawb-tabs'); tabs.setAttribute('role', 'tablist');
    const crumbs = h('ol', 'lawb-path'), body = h('div', 'lawb-body');
    section.append(head, tabs, crumbs, body); host.replaceChildren(section);
    let kind = data.collections.some(c => c.kind === start.kind) ? start.kind : data.collections[0].kind;

    function remember(node) {
      try { history.replaceState(null, '', '#laws?' + query({ state: data.state, toc: kind, node: node || '' })); } catch (error) { /* address bar only */ }
    }
    function drawTabs() {
      tabs.replaceChildren();
      for (const c of data.collections) {
        const b = h('button', 'lawb-tab' + (c.kind === kind ? ' is-on' : '')); b.type = 'button'; b.setAttribute('role', 'tab'); b.setAttribute('aria-selected', String(c.kind === kind));
        b.append(h('span', '', c.label), h('small', '', fmt(c.provisions)));
        b.addEventListener('click', () => { kind = c.kind; drawTabs(); show(0); });
        tabs.append(b);
      }
    }
    async function show(parent) {
      body.setAttribute('aria-busy', 'true');
      let level;
      try { level = await get('/api/law-outline/children?' + query({ state: data.usps, kind, parent })); } catch (error) { body.replaceChildren(h('p', 'lawb-empty', 'This collection could not be opened.')); return; }
      remember(level.parent);
      crumbs.replaceChildren();
      const label = (data.collections.find(c => c.kind === kind) || {}).label || kind;
      const steps = [{ id: 0, label }].concat(level.path || []);
      steps.forEach((step, index) => {
        const li = h('li');
        if (index === steps.length - 1) li.append(h('span', 'is-here', step.label));
        else { const b = h('button', '', step.label); b.type = 'button'; b.addEventListener('click', () => show(step.id)); li.append(b); }
        crumbs.append(li);
      });
      const list = h('ul', 'lawb-list');
      for (const node of level.nodes) {
        const li = h('li'), b = h('button', 'lawb-row'); b.type = 'button';
        b.append(h('span', 'lawb-label', node.label), h('span', 'lawb-count', fmt(node.provisions)), h('span', 'lawb-chev', '›'));
        b.addEventListener('click', () => (node.has_children ? show(node.id) : show(node.id)));
        li.append(b); list.append(li);
      }
      body.replaceChildren();
      if (level.nodes.length) body.append(list);
      const here = level.parent; const current = (level.path || [])[level.path.length - 1];
      if (here) await provisions(here, body, !level.nodes.length, current ? current.label : '');
      body.removeAttribute('aria-busy');
    }
    async function provisions(node, target, only, label) {
      let offset = 0;
      const box = h('div', 'lawb-provisions'), list = h('ul', 'lawb-sections'), more = h('button', 'lawb-more', 'Show more'); more.type = 'button';
      async function page() {
        let result;
        try { result = await get('/api/law-outline/provisions?' + query({ node, offset, limit: 100 })); } catch (error) { return; }
        if (!result.total) return;
        if (!box.isConnected) { if (!only) box.append(h('h3', 'lawb-sub-h', `Provisions filed directly under ${label || 'this heading'}`)); box.append(list, more); target.append(box); }
        for (const row of result.results) {
          const li = h('li'), b = h('button', 'lawb-section'); b.type = 'button';
          const whole = title(row.citation, row.title), rest = row.citation && whole.startsWith(row.citation) ? whole.slice(row.citation.length).replace(/^\s*—\s*/, '') : '';
          b.append(h('span', 'lawb-cite', row.citation || row.title || 'Provision'), h('span', 'lawb-title', rest));
          if (row.status && row.status !== 'in_force') b.append(h('span', 'lawb-status', row.status.replace(/_/g, ' ')));
          b.addEventListener('click', () => open(row.id));
          li.append(b); list.append(li);
        }
        offset += result.results.length; more.hidden = offset >= result.total;
        more.textContent = `Show more (${fmt(result.total - offset)} remaining)`;
      }
      more.addEventListener('click', page);
      await page();
    }
    drawTabs();
    show(Number(start.node) || 0);
  }

  window.LawReader = { format, reading, context, browser, title, citedBy };
})();
