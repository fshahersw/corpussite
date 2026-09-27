/* judgeui.js — presentation layer for judge profiles and historical biographies.
 *
 * Exposes exactly one global:
 *   window.JudgeUI = {
 *     profile(container, profile, context),  // /api/judge?id=... payload
 *     person(container, person, context),    // /api/person?id=... payload
 *     card(item)                             // one row of /api/judges or /api/people -> DOM node
 *   }
 * context = { back(): return to the list }  (optional; a hash fallback is used when absent)
 *
 * Self-contained: it injects its own <style id="judgeui-style"> once and uses only the
 * globals app.js already defines (el, append, count, date, human, readable, badge, link,
 * routeLink, makeHash, action, metaRow, prose, footnote, safeURL, profileArray,
 * isOfficialSourceProfile, openRecord, renderAnalysis). It never edits app.js, areas.js,
 * styles.css or index.html. Nothing is merged by name and no date is invented: every
 * number and date shown is printed by the payload, except the two clearly-labelled
 * derivations (years since a stated commission date, and the span of stated dates).
 */
(function () {
  'use strict';

  var STYLE_ID = 'judgeui-style';
  var seq = 0;

  /* ------------------------------------------------------------------ styles */

  var CSS = [
    '.ju{--ju-top:0px;--ju-bar:46px;display:block;min-width:0}',
    '.ju *{min-width:0}',
    '.ju-back{display:inline-flex;align-items:center;gap:6px;border:0;background:none;padding:0;margin:0 0 14px;color:var(--muted);font-size:12px;font-weight:600;text-decoration:none;min-height:28px}',
    '.ju-back:hover{color:var(--teal-dark)}',

    /* identity */
    '.ju-hero{display:grid;grid-template-columns:auto minmax(0,1fr);gap:18px;align-items:start;background:var(--surface);border:1px solid var(--line);border-radius:var(--radius);padding:18px 20px}',
    '.ju-avatar{width:68px;height:68px;border-radius:10px;background:var(--teal-light);color:var(--teal-dark);display:grid;place-items:center;font-family:Georgia,"Times New Roman",serif;font-size:23px;letter-spacing:-.5px;position:relative;overflow:hidden;border:1px solid var(--line);flex:none}',
    '.ju-avatar img{position:absolute;inset:0;width:100%;height:100%;object-fit:cover;object-position:top center}',
    '.ju-avatar.sm{width:30px;height:30px;font-size:12px;border-radius:7px}',
    '.ju-avatar.card{width:44px;height:44px;font-size:16px;border-radius:9px}',
    '.ju-eyebrow{font-size:10px;font-weight:650;letter-spacing:1.6px;text-transform:uppercase;color:var(--teal);margin:0}',
    '.ju-name{font-family:Georgia,"Times New Roman",serif;font-weight:400;font-size:clamp(23px,2.6vw,31px);letter-spacing:-.7px;line-height:1.15;margin:5px 0 0}',
    '.ju-court{font-size:14px;font-weight:600;margin:7px 0 0;line-height:1.4}',
    '.ju-meta{font-size:12px;color:var(--muted);margin:3px 0 0;line-height:1.5}',
    '.ju-chips{display:flex;flex-wrap:wrap;gap:6px;margin-top:11px}',
    '.ju-chips .badge{margin:0}',

    /* stat tiles */
    '.ju-tiles{grid-column:1/-1;display:grid;grid-template-columns:repeat(auto-fit,minmax(134px,1fr));gap:8px;margin-top:16px}',
    '.ju-tile{background:var(--paper);border:1px solid var(--line);border-radius:9px;padding:10px 12px 11px}',
    '.ju-tile b{display:block;font-size:21px;font-weight:650;letter-spacing:-.5px;font-variant-numeric:tabular-nums;line-height:1.1}',
    '.ju-tile span{display:block;font-size:11px;font-weight:650;margin-top:3px}',
    '.ju-tile small{display:block;font-size:10px;color:var(--muted);margin-top:2px;line-height:1.45}',

    /* sticky section bar */
    '.ju-bar{position:sticky;top:var(--ju-top);z-index:4;display:flex;align-items:center;gap:10px;flex-wrap:wrap;margin-top:14px;padding:5px 0;background:var(--paper);border-bottom:1px solid var(--line)}',
    '.ju-barid{display:none;align-items:center;gap:8px;min-width:0;max-width:230px}',
    '.ju-bar.pinned .ju-barid{display:flex}',
    '.ju-barid b{font-size:13px;font-weight:650;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}',
    '.ju-nav{display:flex;flex-wrap:wrap;gap:2px;min-width:0}',
    '.ju-nav button{border:0;background:none;border-radius:6px;padding:6px 10px;font-size:12px;font-weight:600;color:var(--muted);min-height:32px;white-space:nowrap}',
    '.ju-nav button:hover{background:var(--teal-light);color:var(--teal-dark)}',
    '.ju-nav button[aria-current="true"]{color:var(--teal-dark);background:var(--teal-light);box-shadow:inset 0 -2px 0 var(--teal)}',

    /* body */
    '.ju-body{display:grid;grid-template-columns:minmax(0,1fr) 262px;gap:18px;align-items:start;margin-top:16px}',
    '.ju-col{display:grid;gap:14px;min-width:0}',
    '.ju-rail{display:grid;gap:12px;position:sticky;top:calc(var(--ju-top) + var(--ju-bar) + 14px)}',
    '.ju-sec{background:var(--surface);border:1px solid var(--line);border-radius:var(--radius);padding:17px 19px;scroll-margin-top:calc(var(--ju-top) + var(--ju-bar) + 14px)}',
    '.ju-sec>h2{font-family:Georgia,"Times New Roman",serif;font-weight:400;font-size:19px;letter-spacing:-.3px;margin:0}',
    '.ju-sec>h2:focus-visible{outline:3px solid #409486;outline-offset:4px}',
    '.ju-sec>h3,.ju-vizhead h3{font-size:13px;font-weight:650;letter-spacing:.1px;margin:0}',
    '.ju-sechead{display:flex;align-items:baseline;justify-content:space-between;gap:10px;flex-wrap:wrap}',
    '.ju-lead{font-size:12px;color:var(--muted);line-height:1.6;margin:5px 0 0}',
    '.ju-block{margin-top:16px}',
    '.ju-block:first-of-type{margin-top:12px}',
    '.ju-rail .ju-sec{padding:14px 15px}',
    '.ju-rail h2{font-size:12px!important;font-family:"Segoe UI",system-ui,sans-serif!important;font-weight:650!important;letter-spacing:.7px!important;text-transform:uppercase;color:var(--muted)}',

    /* definition rows */
    '.ju-dl{margin:9px 0 0;display:grid}',
    '.ju-dl>div{display:grid;gap:2px;padding:8px 0;border-top:1px solid var(--line)}',
    '.ju-dl>div:first-child{border-top:0;padding-top:0}',
    '.ju-dl dt{font-size:10px;font-weight:650;letter-spacing:.7px;text-transform:uppercase;color:var(--muted)}',
    '.ju-dl dd{margin:0;font-size:13px;line-height:1.5;overflow-wrap:anywhere}',
    '.ju-dl dd small{display:block;color:var(--muted);font-size:10px;margin-top:2px;line-height:1.45}',

    /* timeline */
    '.ju-tl{list-style:none;margin:10px 0 0;padding:0;position:relative}',
    '.ju-tl::before{content:"";position:absolute;left:52px;top:16px;bottom:16px;width:1px;background:var(--line)}',
    '.ju-tl li{display:grid;grid-template-columns:44px minmax(0,1fr);gap:20px;padding:11px 0;border-top:1px solid var(--line);position:relative}',
    '.ju-tl li:first-child{border-top:0;padding-top:2px}',
    '.ju-tl li::after{content:"";position:absolute;left:49px;top:17px;width:7px;height:7px;border-radius:50%;background:var(--teal);box-shadow:0 0 0 3px var(--surface)}',
    '.ju-tl li:first-child::after{top:8px}',
    '.ju-tl li.faint::after{background:var(--line);box-shadow:0 0 0 3px var(--surface)}',
    '.ju-year{font-size:12px;font-weight:650;font-variant-numeric:tabular-nums;color:var(--teal-dark);line-height:1.5}',
    '.ju-tl li.faint .ju-year{color:var(--muted)}',
    '.ju-tl-title{font-size:13.5px;font-weight:650;line-height:1.45}',
    '.ju-tl-place{font-size:12.5px;line-height:1.5;margin-top:1px}',
    '.ju-tl-dates{font-size:11px;color:var(--muted);line-height:1.6;margin-top:3px;font-variant-numeric:tabular-nums}',
    '.ju-tl .badge{margin:4px 6px 0 0}',

    /* tables */
    '.ju-scroll{overflow-x:auto;max-width:100%}',
    '.ju-table{width:100%;border-collapse:collapse;font-size:12.5px;margin-top:9px}',
    '.ju-table th{text-align:left;font-size:10px;font-weight:650;letter-spacing:.7px;text-transform:uppercase;color:var(--muted);padding:0 12px 7px 0;border-bottom:1px solid var(--line);white-space:nowrap}',
    '.ju-table td{padding:8px 12px 8px 0;border-top:1px solid var(--line);vertical-align:top;line-height:1.45}',
    '.ju-table tr:first-child td{border-top:0}',
    '.ju-table th:last-child,.ju-table td:last-child{padding-right:0}',
    '.ju-num{text-align:right;font-variant-numeric:tabular-nums;white-space:nowrap}',
    '.ju-nowrap{white-space:nowrap;font-variant-numeric:tabular-nums}',
    '.ju-table .ju-cap{min-width:190px}',
    '.ju-quiet{color:var(--muted)}',

    /* bar chart */
    '.ju-bars{display:grid;gap:9px;margin-top:10px}',
    '.ju-brow{display:grid;grid-template-columns:minmax(0,1fr) auto;gap:1px 10px;align-items:baseline}',
    '.ju-blabel{font-size:12px;line-height:1.4;overflow-wrap:anywhere}',
    '.ju-bval{font-size:12px;font-weight:650;font-variant-numeric:tabular-nums;white-space:nowrap}',
    '.ju-btrack{grid-column:1/-1;height:7px;border-radius:4px;background:#eef3f1;margin-top:4px;overflow:hidden}',
    '.ju-bfill{height:100%;border-radius:4px;background:var(--teal)}',
    '.ju-bfill.gold{background:var(--gold)}',

    /* column chart */
    '.ju-cols{display:flex;align-items:flex-end;gap:3px;height:92px;margin-top:10px;border-bottom:1px solid var(--line);padding-bottom:1px}',
    '.ju-colitem{flex:1 1 0;display:flex;flex-direction:column;justify-content:flex-end;height:100%}',
    '.ju-colfill{background:var(--teal);border-radius:2px 2px 0 0;min-height:3px}',
    '.ju-colzero{height:2px;background:var(--line);border-radius:1px}',
    '.ju-colaxis{display:flex;gap:3px;margin-top:5px}',
    '.ju-colaxis span{flex:1 1 0;min-width:0;text-align:center;font-size:9.5px;color:var(--muted);font-variant-numeric:tabular-nums;white-space:nowrap;overflow:visible}',

    /* view toggle */
    '.ju-vizhead{display:flex;align-items:center;justify-content:space-between;gap:10px;flex-wrap:wrap}',
    '.ju-seg{display:inline-flex;border:1px solid var(--line);border-radius:6px;overflow:hidden;background:var(--surface);flex:none}',
    '.ju-seg button{border:0;background:none;padding:4px 11px;font-size:11px;font-weight:650;color:var(--muted);min-height:27px}',
    '.ju-seg button+button{border-left:1px solid var(--line)}',
    '.ju-seg button[aria-pressed="true"]{background:var(--teal-light);color:var(--teal-dark)}',

    /* misc pieces */
    '.ju-strip{display:flex;flex-wrap:wrap;gap:5px;margin-top:9px}',
    '.ju-strip span{font-size:11px;font-weight:650;font-variant-numeric:tabular-nums;color:#3e6255;background:#f4f8f5;border:1px solid #d9e4df;border-radius:5px;padding:3px 7px}',
    '.ju-strip span.off{color:var(--muted);background:var(--paper);border-color:var(--line);font-weight:600}',
    '.ju-two{display:grid;grid-template-columns:repeat(auto-fit,minmax(215px,1fr));gap:2px 20px;margin:10px 0 0;padding:0;list-style:none}',
    '.ju-two li{font-size:12.5px;line-height:1.5;padding:5px 0;border-top:1px solid var(--line);display:flex;justify-content:space-between;gap:10px}',
    '.ju-two li b{font-weight:600;overflow-wrap:anywhere}',
    '.ju-two li em{font-style:normal;color:var(--muted);font-size:11px;white-space:nowrap;font-variant-numeric:tabular-nums}',
    '.ju-list{list-style:none;margin:10px 0 0;padding:0;display:grid}',
    '.ju-list li{padding:10px 0;border-top:1px solid var(--line)}',
    '.ju-list li:first-child{border-top:0;padding-top:2px}',
    '.ju-list .ju-rowtitle{font-size:13px;font-weight:650;line-height:1.45}',
    '.ju-list .ju-rowsub{font-size:11.5px;color:var(--muted);line-height:1.55;margin-top:2px}',
    '.ju-actions{display:flex;flex-wrap:wrap;gap:8px 14px;margin-top:6px}',
    '.ju-more{font-size:12px;font-weight:650;color:var(--teal);background:none;border:0;padding:6px 0;text-decoration:none;display:inline-block;min-height:30px}',
    '.ju-foot{font-size:11px;color:var(--muted);line-height:1.65;margin:14px 0 0}',
    '.ju-about{margin-top:10px;border:1px solid var(--line);border-radius:9px;background:var(--surface);padding:0 14px}',
    '.ju-about>summary{cursor:pointer;font-size:11.5px;font-weight:650;color:var(--muted);padding:10px 0;list-style-position:inside}',
    '.ju-about p{font-size:11.5px;color:var(--muted);line-height:1.7;margin:0 0 10px}',
    '.ju-about p b{color:var(--ink);font-weight:650}',
    '.ju-tech{margin-top:10px}',
    '.ju-tech>summary{cursor:pointer;font-size:11px;font-weight:650;color:var(--muted);padding:6px 0;min-height:28px}',
    '.ju-tech .ju-dl dd{font-size:11.5px}',
    '.ju-tech code{font-family:ui-monospace,SFMono-Regular,Menlo,Consolas,monospace;font-size:11px;overflow-wrap:anywhere}',
    '.ju-srclist{list-style:none;margin:9px 0 0;padding:0;display:grid;gap:0}',
    '.ju-srclist li{padding:9px 0;border-top:1px solid var(--line)}',
    '.ju-srclist li:first-child{border-top:0;padding-top:0}',

    /* result cards */
    '.ju-pcard{display:flex;gap:13px;align-items:flex-start;height:100%;background:var(--surface);border:1px solid var(--line);border-radius:var(--radius);padding:15px 16px;text-decoration:none;color:var(--ink)}',
    '.ju-pcard:hover{border-color:#c3d4cd;box-shadow:var(--shadow)}',
    '.ju-pcard:focus-visible{outline:3px solid #409486;outline-offset:3px}',
    '.ju-pcard-body{display:flex;flex-direction:column;min-width:0;flex:1}',
    '.ju-pcard-role{font-size:10px;font-weight:650;letter-spacing:1.1px;text-transform:uppercase;color:var(--muted);margin:0}',
    '.ju-pcard-name{font-family:Georgia,"Times New Roman",serif;font-size:17px;font-weight:400;letter-spacing:-.3px;line-height:1.25;margin:4px 0 0;color:var(--teal-dark)}',
    '.ju-pcard-court{font-size:12px;line-height:1.45;margin:5px 0 0;display:-webkit-box;-webkit-line-clamp:2;-webkit-box-orient:vertical;overflow:hidden}',
    '.ju-pcard-facts{font-size:11px;color:var(--muted);line-height:1.5;margin:6px 0 0}',
    '.ju-pcard .badge{margin:8px 0 0;align-self:flex-start}',
    '.ju-pcard-foot{margin-top:auto;padding-top:9px;font-size:11px;font-weight:650;color:var(--teal)}',

    /* responsive */
    '@media(max-width:1000px){.ju-body{grid-template-columns:1fr}.ju-rail{position:static}}',
    '@media(max-width:620px){',
    '.ju-hero{grid-template-columns:1fr;gap:12px;padding:15px 15px 16px}',
    '.ju-avatar{width:54px;height:54px;font-size:19px}',
    '.ju-sec{padding:15px 15px}',
    '.ju-bar{gap:6px;margin-top:12px}',
    '.ju-nav button{padding:6px 8px;font-size:11.5px}',
    '.ju-tiles{grid-template-columns:repeat(auto-fit,minmax(104px,1fr))}',
    '.ju-tile b{font-size:18px}',
    '.ju-tl li{grid-template-columns:38px minmax(0,1fr);gap:14px}',
    '.ju-tl::before{content:none}.ju-tl li::after{content:none}',
    '.ju-barid{display:none!important}',
    '}',
    '@media(prefers-reduced-motion:reduce){.ju{scroll-behavior:auto}}'
  ].join('');

  // One <style id="judgeui-style"> block, injected once. The server sends
  // Content-Security-Policy: style-src 'self', which blocks an inline <style> element
  // (node.sheet stays null); the same CSS is then adopted through CSSOM, which the policy
  // does allow. The marker element is kept either way so the block is injected only once.
  function ensureStyle() {
    if (document.documentElement.dataset.judgeuiStyle) return;
    document.documentElement.dataset.judgeuiStyle = STYLE_ID;
    // Constructed stylesheet first: the page's policy (style-src 'self') blocks an injected <style> and logs an error for it.
    try {
      var sheet = new CSSStyleSheet();
      sheet.replaceSync(CSS);
      document.adoptedStyleSheets = Array.prototype.slice.call(document.adoptedStyleSheets).concat(sheet);
      return;
    } catch (error) { /* older engine: fall through to a style element */ }
    var node = document.createElement('style');
    node.id = STYLE_ID;
    node.textContent = CSS;
    (document.head || document.documentElement).append(node);
  }

  /* ------------------------------------------------------------- tiny helpers */

  // Repair UTF-8 bytes that were stored as latin-1 ("Judge â€” U.S. District Court").
  // Display only: no word is added or removed.
  function fix(value) {
    var s = String(value == null ? '' : value);
    if (!/[ÃÂâ]/.test(s)) return s;
    try {
      var out = decodeURIComponent(escape(s));
      return out.indexOf('�') === -1 ? out : s;
    } catch (error) {
      return s
        .replace(/â€™/g, '’').replace(/â€œ/g, '“').replace(/â€/g, '”')
        .replace(/â€“/g, '–').replace(/â€”/g, '—').replace(/â€¢/g, '•')
        .replace(/Â·/g, '·').replace(/Â /g, ' ');
    }
  }

  function txt(value) {
    if (value == null) return '';
    if (typeof value === 'string' || typeof value === 'number') return fix(String(value));
    if (typeof value === 'object') {
      for (var i = 0, keys = ['text', 'literal_text', 'as_reported', 'value', 'label']; i < keys.length; i++) {
        if (value[keys[i]]) return fix(String(value[keys[i]]));
      }
    }
    return '';
  }

  function arr(value) { return Array.isArray(value) ? value : (value ? [value] : []); }
  function num(value) { var n = Number(value); return Number.isFinite(n) ? n : null; }
  function join(parts, sep) { return parts.filter(Boolean).join(sep || ' · '); }
  function texts(value) {
    var out = [];
    arr(value).forEach(function (item) { var s = txt(item); if (s) out.push(s); });
    return out.filter(function (s, i) { return out.indexOf(s) === i; });
  }
  function yearOf(value) {
    var m = String(value == null ? '' : value).match(/\b((?:1[6-9]|20)\d{2})\b/);
    return m ? m[1] : '';
  }
  function sentence(value) { var s = String(value || ''); return s ? s.charAt(0).toUpperCase() + s.slice(1) : s; }

  function initials(name) {
    var parts = String(name || '').replace(/^(hon\.?|judge|justice|chief judge)\s+/i, '')
      .replace(/[^\p{L}\p{N}\s.'-]/gu, ' ').split(/\s+/)
      .map(function (part) { return part.replace(/^[^\p{L}\p{N}]+/u, ''); }).filter(Boolean);
    if (!parts.length) return 'J';
    return parts.length > 1 ? (parts[0][0] + parts[parts.length - 1][0]).toUpperCase() : parts[0].slice(0, 2).toUpperCase();
  }

  function avatar(name, photoURL, size) {
    var holder = el('div', 'ju-avatar' + (size ? ' ' + size : ''));
    holder.setAttribute('aria-hidden', 'true');
    holder.append(el('span', '', initials(name)));
    var url = typeof safeURL === 'function' ? safeURL(photoURL) : (photoURL || '');
    if (url) {
      var photo = el('img');
      photo.src = url; photo.alt = ''; photo.loading = 'lazy';
      photo.addEventListener('error', function () { photo.remove(); }, { once: true });
      holder.append(photo);
    }
    return holder;
  }

  function dlBox(className) {
    var list = el('dl', 'ju-dl' + (className ? ' ' + className : ''));
    list.row = function (label, value, note) {
      var display = value && typeof value === 'object' && !(value instanceof Node) ? readable(value) : value;
      if (display === null || display === undefined || display === '' || display === 'Not recorded') return list;
      var row = el('div');
      row.append(el('dt', '', label));
      var dd = el('dd');
      if (display instanceof Node) dd.append(display); else dd.append(document.createTextNode(fix(String(display))));
      if (note) dd.append(el('small', '', note));
      row.append(dd);
      list.append(row);
      return list;
    };
    return list;
  }

  function tile(value, label, note) {
    var box = el('div', 'ju-tile');
    append(box, el('b', '', typeof value === 'number' ? count(value) : String(value)), el('span', '', label), note ? el('small', '', note) : null);
    return box;
  }

  function sectionCard(id, title, lead) {
    var box = el('section', 'ju-sec');
    box.id = id;
    var head = el('div', 'ju-sechead');
    head.append(el('h2', '', title));
    box.append(head);
    if (lead) box.append(el('p', 'ju-lead', lead));
    box.head = head;
    return box;
  }

  function block(title, control) {
    var box = el('div', 'ju-block');
    var head = el('div', 'ju-vizhead');
    head.append(el('h3', '', title));
    if (control) head.append(control);
    box.append(head);
    return box;
  }

  /* --------------------------------------------------------------- data views */

  function dataTable(headers, rows) {
    var table = el('table', 'ju-table');
    var headRow = el('tr');
    headers.forEach(function (h) {
      var cell = el('th', h && h.numeric ? 'ju-num' : '', h && h.label !== undefined ? h.label : h);
      if (h && h.className) cell.className += ' ' + h.className;
      headRow.append(cell);
    });
    table.append(append(el('thead'), headRow));
    var body = el('tbody');
    rows.forEach(function (cells) {
      var tr = el('tr');
      cells.forEach(function (cell, i) {
        var spec = headers[i] || {};
        var td = el('td', spec.numeric ? 'ju-num' : (spec.className || ''));
        if (cell instanceof Node) td.append(cell);
        else td.append(document.createTextNode(cell === null || cell === undefined || cell === '' ? '—' : String(cell)));
        tr.append(td);
      });
      body.append(tr);
    });
    table.append(body);
    return append(el('div', 'ju-scroll'), table);
  }

  function barChart(rows, options) {
    var opts = options || {};
    var top = Math.max.apply(null, [1].concat(rows.map(function (r) { return Number(r.value) || 0; })));
    var box = el('div', 'ju-bars');
    box.setAttribute('role', 'img');
    if (opts.summary) box.setAttribute('aria-label', opts.summary);
    rows.forEach(function (r) {
      var row = el('div', 'ju-brow');
      var label = el('span', 'ju-blabel', r.label);
      if (r.title) label.title = r.title;
      var value = el('span', 'ju-bval', typeof r.display === 'string' ? r.display : count(r.value));
      var track = el('div', 'ju-btrack');
      var fill = el('div', 'ju-bfill' + (opts.accent ? ' gold' : ''));
      fill.style.width = Math.max(1.5, (Number(r.value) || 0) / top * 100) + '%';
      track.append(fill);
      append(row, label, value, track);
      box.append(row);
    });
    return box;
  }

  function columnChart(rows, options) {
    var opts = options || {};
    var top = Math.max.apply(null, [1].concat(rows.map(function (r) { return Number(r.value) || 0; })));
    var wrap = el('div');
    var chart = el('div', 'ju-cols');
    chart.setAttribute('role', 'img');
    if (opts.summary) chart.setAttribute('aria-label', opts.summary);
    rows.forEach(function (r) {
      var item = el('div', 'ju-colitem');
      item.title = r.label + ': ' + count(r.value);
      var value = Number(r.value) || 0;
      if (value > 0) {
        var fill = el('div', 'ju-colfill');
        fill.style.height = Math.max(4, value / top * 100) + '%';
        item.append(fill);
      } else item.append(el('div', 'ju-colzero'));
      chart.append(item);
    });
    var axis = el('div', 'ju-colaxis');
    var step = rows.length > 8 ? Math.ceil(rows.length / 6) : 1;
    rows.forEach(function (r, i) {
      var show = i === 0 || i === rows.length - 1 || i % step === 0;
      axis.append(el('span', '', show ? (r.short || r.label) : ''));
    });
    append(wrap, chart, axis);
    return wrap;
  }

  // Segmented Chart / Table control. Returns { control, body }.
  function viewSwitch(label, views, initial) {
    var seg = el('div', 'ju-seg');
    seg.setAttribute('role', 'group');
    seg.setAttribute('aria-label', label);
    var body = el('div');
    var buttons = views.map(function (view) {
      var button = el('button', '', view.label);
      button.type = 'button';
      button.addEventListener('click', function () { show(view.key); });
      seg.append(button);
      return button;
    });
    function show(key) {
      var chosen = views.filter(function (v) { return v.key === key; })[0] || views[0];
      buttons.forEach(function (button, i) { button.setAttribute('aria-pressed', String(views[i].key === chosen.key)); });
      body.replaceChildren(chosen.build());
    }
    show(initial || views[0].key);
    return { control: seg, body: body };
  }

  function chartAndTable(title, chartBuild, tableBuild, note) {
    var switcher = viewSwitch(title + ' view', [
      { key: 'chart', label: 'Chart', build: chartBuild },
      { key: 'table', label: 'Table', build: tableBuild }
    ], 'chart');
    var box = block(title, switcher.control);
    if (note) box.append(el('p', 'ju-lead', note));
    box.append(switcher.body);
    return box;
  }

  /* ------------------------------------------------------------- timeline */

  function timelineItem(entry) {
    var item = el('li', entry.faint ? 'faint' : '');
    item.append(el('div', 'ju-year', entry.year || ''));
    var body = el('div');
    append(body,
      entry.title ? el('div', 'ju-tl-title', entry.title) : null,
      entry.place ? el('div', 'ju-tl-place', entry.place) : null,
      entry.dates ? el('div', 'ju-tl-dates', entry.dates) : null);
    (entry.tags || []).forEach(function (tag) { body.append(badge(tag.label, tag.style || 'neutral')); });
    item.append(body);
    return item;
  }

  function timeline(entries) {
    var list = el('ol', 'ju-tl');
    entries.forEach(function (entry) { list.append(timelineItem(entry)); });
    return list;
  }

  /* ------------------------------------------------- sticky bar + scroll spy */

  function stickyOffset() {
    var total = 0;
    ['.hub-tabs', '.hub-segments'].forEach(function (selector) {
      var node = document.querySelector(selector);
      if (!node || !node.offsetParent && node.offsetHeight === 0) return;
      var style = window.getComputedStyle(node);
      if (style.position !== 'sticky' && style.position !== 'fixed') return;
      var box = node.getBoundingClientRect();
      if (box.height) total += box.height;
    });
    return Math.round(total);
  }

  function buildNav(wrap, hero, name, courtLabel, photoURL, sections) {
    var bar = el('div', 'ju-bar');
    var identity = el('div', 'ju-barid');
    append(identity, avatar(name, photoURL, 'sm'), el('b', '', name));
    identity.title = join([name, courtLabel]);
    bar.append(identity);

    var nav = el('nav', 'ju-nav');
    nav.setAttribute('aria-label', 'Sections of this profile');
    var buttons = sections.map(function (entry) {
      var button = el('button', '', entry.label);
      button.type = 'button';
      button.setAttribute('aria-controls', entry.node.id);
      button.addEventListener('click', function () {
        var reduce = window.matchMedia && window.matchMedia('(prefers-reduced-motion: reduce)').matches;
        entry.node.scrollIntoView({ behavior: reduce ? 'auto' : 'smooth', block: 'start' });
        var heading = entry.node.querySelector('h2');
        if (heading) { heading.setAttribute('tabindex', '-1'); heading.focus({ preventScroll: true }); }
      });
      nav.append(button);
      return button;
    });
    bar.append(nav);

    var ticking = false;
    function measure() {
      var offset = stickyOffset();
      wrap.style.setProperty('--ju-top', offset + 'px');
      wrap.style.setProperty('--ju-bar', Math.round(bar.getBoundingClientRect().height || 46) + 'px');
      return offset;
    }
    function update() {
      ticking = false;
      if (!wrap.isConnected) { detach(); return; }
      var offset = measure();
      var line = offset + (bar.getBoundingClientRect().height || 46) + 16;
      var active = sections[0];
      sections.forEach(function (entry) {
        if (entry.node.getBoundingClientRect().top <= line + 6) active = entry;
      });
      if (window.innerHeight + window.scrollY >= document.documentElement.scrollHeight - 6) active = sections[sections.length - 1];
      buttons.forEach(function (button, i) {
        if (sections[i] === active) button.setAttribute('aria-current', 'true');
        else button.removeAttribute('aria-current');
      });
      bar.classList.toggle('pinned', hero.getBoundingClientRect().bottom < offset + 8);
    }
    function onScroll() { if (!ticking) { ticking = true; window.requestAnimationFrame(update); } }
    function detach() {
      window.removeEventListener('scroll', onScroll);
      window.removeEventListener('resize', onScroll);
    }
    window.addEventListener('scroll', onScroll, { passive: true });
    window.addEventListener('resize', onScroll);
    window.requestAnimationFrame(update);
    return bar;
  }

  function backControl(kind, context) {
    var label = kind === 'person' ? '← Back to historical biographies' : '← Back to judges';
    if (context && typeof context.back === 'function') {
      var button = el('button', 'ju-back', label);
      button.type = 'button';
      button.addEventListener('click', function () { context.back(); });
      return button;
    }
    var anchor = el('a', 'ju-back', label);
    anchor.href = kind === 'person' ? '#people' : '#judges';
    return anchor;
  }

  function aboutBox(entries) {
    var rows = entries.filter(function (entry) { return entry && entry[1]; });
    if (!rows.length) return null;
    var box = el('details', 'ju-about');
    box.append(el('summary', '', 'About this data'));
    rows.forEach(function (entry) {
      var paragraph = el('p');
      paragraph.append(el('b', '', entry[0] + ' '), document.createTextNode(sentence(fix(String(entry[1])).replace(/\s+/g, ' ').trim())));
      box.append(paragraph);
    });
    return box;
  }

  function missingLine(items) {
    var list = items.filter(Boolean);
    if (!list.length) return null;
    var text = list.length === 1 ? list[0] : list.slice(0, -1).join(', ') + ' and ' + list[list.length - 1];
    return el('p', 'ju-foot', 'Not recorded in this profile: ' + text + '.');
  }

  /* --------------------------------------------------------------- sections */

  function mdlRows(profile) {
    var mdls = profile.mdls;
    return mdls && mdls.available && Array.isArray(mdls.results) ? mdls.results : [];
  }

  function buildMdlSection(id, profile) {
    var rows = mdlRows(profile);
    var evidence = profile.evidence || {};
    var assignments = evidence.assignments;
    var hasAssignments = assignments && ((num(assignments.assigned_count) || 0) + (num(assignments.referred_count) || 0)) > 0;
    var cjra = evidence.cjra && Array.isArray(evidence.cjra.tables) && evidence.cjra.tables.length ? evidence.cjra : null;
    if (!rows.length && !hasAssignments && !cjra) return null;

    var asOf = profile.mdls && profile.mdls.as_of ? profile.mdls.as_of : '';
    var box = sectionCard(id, 'MDLs & cases');

    if (rows.length) {
      var pending = rows.reduce(function (n, r) { return n + (num(r.actions_pending) || 0); }, 0);
      var chartRows = rows.map(function (r) {
        return {
          label: 'MDL ' + r.mdl_number + ' · ' + shortCaption(r.title),
          title: fix(r.title || ''),
          value: num(r.actions_pending) || 0
        };
      });
      var mdlBlock = chartAndTable('Multidistrict litigation',
        function () { return barChart(chartRows, { summary: 'Actions pending in each assigned MDL, as listed in the JPML report' + (asOf ? ' dated ' + asOf : '') + '.' }); },
        function () {
          return dataTable(
            ['MDL', { label: 'Caption', className: 'ju-cap' }, { label: 'Actions pending', numeric: true }, { label: 'Total actions', numeric: true }, 'Transferred'],
            rows.map(function (r) {
              var openLink = routeLink(String(r.mdl_number), 'mdl/' + r.mdl_number, { from: location.hash }, 'ju-more');
              var caption = el('div');
              caption.append(document.createTextNode(fix(r.title || 'Caption not recorded')));
              var sub = join([r.court_name, r.litigation_type, r.master_docket]);
              if (sub) caption.append(el('div', 'ju-rowsub', sub));
              return [openLink, caption, count(r.actions_pending), count(r.total_actions), r.date_transferred ? date(r.date_transferred) : '—'];
            })
          );
        },
        (asOf ? 'Counts as listed in the JPML report dated ' + date(asOf) + '. ' : '') +
        count(rows.length) + ' assigned · ' + count(pending) + ' actions pending in total.');
      box.append(mdlBlock);
    }

    if (hasAssignments) {
      var natures = (assignments.by_nature_of_suit || []).map(function (row) {
        if (Array.isArray(row)) return { label: fix(row[0]), value: num(row[1]) || 0 };
        return { label: fix(row.nature_of_suit || row.label || row.value || 'Not recorded'), value: num(row.matters !== undefined ? row.matters : row.count) || 0 };
      }).filter(function (row) { return row.value > 0; });

      var filed = assignments.date_filed_first ? assignments.date_filed_first + ' to ' + (assignments.date_filed_last || 'not stated') : '';
      var summary = join([
        count(assignments.assigned_count) + ' assigned',
        (num(assignments.referred_count) || 0) > 0 ? count(assignments.referred_count) + ' referred' : '',
        filed ? 'filed ' + filed : '',
        assignments.matters_in_dataset ? count(assignments.matters_for_this_judge || assignments.assigned_count) + ' of ' + count(assignments.matters_in_dataset) + ' matters in the dataset' : ''
      ]);

      if (natures.length) {
        box.append(chartAndTable('Saved docket assignments',
          function () { return barChart(natures, { summary: 'Matters in the saved docket dataset by nature of suit.' }); },
          function () {
            return dataTable(['Nature of suit', { label: 'Matters', numeric: true }],
              natures.map(function (row) { return [row.label, count(row.value)]; }));
          },
          summary));
      } else {
        var plain = block('Saved docket assignments');
        plain.append(el('p', 'ju-lead', summary));
        box.append(plain);
      }

      var matters = (assignments.recent_matters || assignments.matters || []).slice(0);
      if (matters.length) {
        var shown = 8;
        var holder = el('div', 'ju-block');
        holder.append(el('h3', '', 'Matters in the saved dataset'));
        var target = el('div');
        var more = el('button', 'ju-more');
        more.type = 'button';
        function draw() {
          target.replaceChildren(dataTable(['Case', { label: 'Docket', className: 'ju-nowrap' }, { label: 'Filed', className: 'ju-nowrap' }, 'Status'], matters.slice(0, shown).map(function (m) {
            var name = el('div');
            name.append(document.createTextNode(fix(m.case_name || 'Case name not recorded')));
            if (m.nature_of_suit) name.append(el('div', 'ju-rowsub', fix(m.nature_of_suit)));
            return [name, fix(m.docket_number || ''), m.date_filed ? date(m.date_filed) : '—', human(m.case_status || (m.date_terminated ? 'terminated' : ''))];
          })));
          more.hidden = shown >= matters.length;
          more.textContent = 'Show the remaining ' + count(matters.length - shown) + ' matters';
        }
        more.addEventListener('click', function () { shown = matters.length; draw(); more.hidden = true; });
        draw();
        append(holder, target, more);
        box.append(holder);
      }
    }

    if (cjra) {
      var cjraRows = cjra.tables.map(function (row) { return { label: fix(row.label || human(row.table)), value: num(row.count) || 0 }; });
      box.append(chartAndTable('Civil Justice Reform Act report',
        function () { return barChart(cjraRows, { accent: true, summary: 'Counts printed in the CJRA report.' }); },
        function () { return dataTable(['Reported table', { label: 'Count', numeric: true }], cjraRows.map(function (r) { return [r.label, count(r.value)]; })); },
        'As of ' + (cjra.as_of ? date(cjra.as_of) : 'a date the report does not state') +
        (cjra.judge_name_as_printed ? '. Printed as “' + fix(cjra.judge_name_as_printed) + '”' + (cjra.court_as_printed ? ', ' + fix(cjra.court_as_printed) : '') + '.' : '.')));
    }

    return box;
  }

  function shortCaption(title) {
    var s = fix(title || '').replace(/^IN RE:\s*/i, '');
    return s.length > 46 ? s.slice(0, 45).replace(/\s+\S*$/, '') + '…' : s;
  }

  function appointmentEntries(profile) {
    var structured = profile.structured || {};
    var fjc = structured.fjc || {};
    return (fjc.appointments || structured.appointments || []).map(function (a) {
      var dates = join([
        a.nomination_date ? 'nominated ' + date(a.nomination_date) : '',
        a.confirmation_date ? 'confirmed ' + date(a.confirmation_date) : '',
        a.commission_date ? 'commissioned ' + date(a.commission_date) : '',
        a.chief_judge_begin ? 'chief judge ' + a.chief_judge_begin + (a.chief_judge_end ? '–' + a.chief_judge_end : '') : '',
        a.senior_status_date ? 'senior status ' + date(a.senior_status_date) : '',
        a.termination_date ? human(a.termination_reason || 'terminated') + ' ' + date(a.termination_date) : ''
      ], ' · ');
      var tags = [];
      if (a.appointing_president) tags.push({ label: 'Appointed by ' + a.appointing_president });
      if (a.aba_rating) tags.push({ label: 'ABA: ' + a.aba_rating });
      return {
        year: yearOf(a.commission_date || a.confirmation_date || a.nomination_date),
        sort: Number(yearOf(a.commission_date || a.confirmation_date || a.nomination_date) || 0),
        title: join([a.title, a.court], ' · ') || 'Federal appointment',
        place: a.court_type || '',
        dates: dates,
        tags: tags
      };
    });
  }

  function textEntries(profile, keys) {
    var entries = [];
    keys.forEach(function (key) {
      texts(profile[key]).forEach(function (whole) {
        whole.split(/;\s+(?=[A-Z0-9])/).forEach(function (part) {
          var s = part.trim().replace(/[;,]$/, '');
          if (!s) return;
          var span = s.match(/((?:1[6-9]|20)\d{2})(?:\s*[-–]\s*((?:1[6-9]|20)\d{2}))?\s*$/);
          entries.push({
            year: span ? (span[2] ? span[1] + '–' + span[2] : span[1]) : '',
            sort: span ? Number(span[1]) : -1,
            title: s,
            faint: true
          });
        });
      });
    });
    return entries;
  }

  function buildCareerSection(id, profile) {
    var structured = profile.structured || {};
    var evidence = profile.evidence || {};
    var appointments = appointmentEntries(profile);
    var textKeys = appointments.length ? ['service', 'professional_career'] : ['appointments', 'service', 'professional_career'];
    var others = textEntries(profile, textKeys);
    var positions = (evidence.positions || []).slice(0);
    if (!appointments.length && !others.length && !positions.length) return null;

    var box = sectionCard(id, 'Career');
    var entries = appointments.concat(others).sort(function (a, b) { return (b.sort || -1) - (a.sort || -1); });
    if (entries.length) {
      box.append(timeline(entries));
      box.append(el('p', 'ju-foot', appointments.length
        ? 'Appointment dates are the Federal Judicial Center export values. Remaining rows are the saved profile text, split at its own semicolons; the year shown is the year stated at the end of the entry.'
        : 'Rows are the saved profile text, split at its own semicolons; the year shown is the year stated at the end of the entry.'));
    }

    if (positions.length) {
      var details = el('details', 'ju-tech');
      details.append(el('summary', '', 'Positions as recorded by CourtListener (' + count(positions.length) + ')'));
      details.append(timeline(positions.map(function (p) {
        var start = p.date_start_display || p.date_start || '';
        var end = p.date_termination_display || p.date_termination || '';
        return {
          year: yearOf(start),
          faint: true,
          title: fix(p.job_title || human(p.position_type) || 'Position'),
          place: join([fix(p.court_name || ''), fix(p.organization_name || ''), join([p.location_city, p.location_state], ', ')]),
          dates: start ? (end ? start + ' – ' + end : 'from ' + start) : (end ? 'to ' + end : ''),
          tags: p.has_inferred_values ? [{ label: 'Source-inferred values' }] : []
        };
      })));
      box.append(details);
    }

    if (appointments.length) {
      var raw = texts(profile.appointments);
      if (raw.length) {
        var rawBox = el('details', 'ju-tech');
        rawBox.append(el('summary', '', 'Appointment text as saved in the profile'));
        var list = el('ul', 'ju-list');
        raw.forEach(function (s) { list.append(append(el('li'), el('div', 'ju-rowsub', s))); });
        rawBox.append(list);
        box.append(rawBox);
      }
    }
    return box;
  }

  function educationRows(profile) {
    var rows = texts(profile.education).map(function (s) {
      var match = s.match(/^(.*?),\s*((?:1[6-9]|20)\d{2})\s*$/);
      return match ? { label: match[1], year: match[2] } : { label: s, year: '' };
    });
    if (rows.length) return rows;
    var structured = profile.structured || {};
    var source = (structured.courtlistener && structured.courtlistener.educations) || (profile.evidence && profile.evidence.education) || [];
    return source.map(function (e) {
      return { label: join([fix(e.school || ''), fix(e.degree_detail || e.degree || '')], ', '), year: e.degree_year || '' };
    }).filter(function (row) { return row.label; });
  }

  function buildEducationSection(id, profile) {
    var rows = educationRows(profile);
    if (!rows.length) return null;
    var box = sectionCard(id, 'Education');
    var list = el('ul', 'ju-two');
    rows.forEach(function (row) {
      var item = el('li');
      append(item, el('b', '', row.label), row.year ? el('em', '', row.year) : null);
      list.append(item);
    });
    box.append(list);
    return box;
  }

  function buildDisclosureSection(id, profile) {
    var d = profile.disclosures;
    var years = d && d.available ? (d.years_filed || []) : [];
    if (!years.length) return null;

    var box = sectionCard(id, 'Financial disclosures');
    var counts = d.holdings_count_by_year || {};
    var span = years.length ? years[0] + '–' + years[years.length - 1] : '';
    box.append(el('p', 'ju-lead', join([
      count(years.length) + ' year' + (years.length === 1 ? '' : 's') + ' filed' + (span ? ' (' + span + ')' : ''),
      count(d.total_holdings || 0) + ' holding rows across all filings',
      'latest filing ' + (d.latest_year || 'not stated'),
      'as filed'
    ])));

    var yearRows = years.map(function (y) { return { label: String(y), value: num(counts[String(y)]) || 0 }; });
    var anyCount = yearRows.some(function (r) { return r.value > 0; });
    if (anyCount) {
      box.append(chartAndTable('Reportable holdings by filing year',
        function () { return columnChart(yearRows, { summary: 'Reportable holding rows in each filing year, as filed.' }); },
        function () { return dataTable(['Filing year', { label: 'Holding rows', numeric: true }], yearRows.map(function (r) { return [r.label, count(r.value)]; })); },
        'Rows reported in each filing. A year with no bar filed a report with no reportable holding rows.'));
    } else {
      var strip = el('div', 'ju-strip');
      years.forEach(function (y) { strip.append(el('span', '', String(y))); });
      var yearsBlock = block('Years filed');
      yearsBlock.append(strip);
      box.append(yearsBlock);
    }

    var holdings = d.top_holdings_latest_year || [];
    if (holdings.length) {
      var holdingsBlock = block('Holdings named in the ' + (d.latest_year || 'latest') + ' filing');
      if (d.top_holdings_latest_year_limit && holdings.length >= d.top_holdings_latest_year_limit) {
        holdingsBlock.append(el('p', 'ju-lead', 'First ' + count(holdings.length) + ' rows, alphabetical.'));
      }
      var list = el('ul', 'ju-two');
      holdings.forEach(function (h) {
        var label = h && typeof h === 'object' ? (h.redacted ? 'Redacted by the filer' : fix(h.description || '')) : fix(h);
        var value = h && typeof h === 'object' ? (h.value_code || h.value || '') : '';
        var item = el('li');
        var name = el('b', '', label || 'Not described');
        if (h && h.inferred) name.title = 'Value fields on this row were inferred during extraction';
        append(item, name, el('em', '', value ? String(value) : (h && h.inferred ? 'inferred' : (h && h.redacted ? 'redacted' : ''))));
        list.append(item);
      });
      holdingsBlock.append(list);
      box.append(holdingsBlock);
    }

    if (d.link) {
      var openAll = el('a', 'ju-more', 'All filings and holdings →');
      openAll.href = d.link;
      box.append(openAll);
    }
    return box;
  }

  function buildDocumentsSection(id, profile) {
    var documents = arr(profile.documents);
    var analyses = arr(profile.analyses);
    var references = arr(profile.analysis_references);
    if (!documents.length && !analyses.length && !references.length) return null;

    var box = sectionCard(id, 'Documents & analysis');

    if (analyses.length && typeof renderAnalysis === 'function') {
      var holder = el('div', 'ju-block');
      try { renderAnalysis(holder, analyses, references, { showEmpty: false, preserveFilters: false }); } catch (error) { holder.replaceChildren(); }
      if (holder.children.length) box.append(holder);
    } else if (analyses.length) {
      var analysisBlock = block('Saved analysis records');
      var list = el('ul', 'ju-list');
      analyses.forEach(function (row) {
        var a = row.payload || row;
        var item = el('li');
        append(item,
          el('div', 'ju-rowtitle', join([fix(a.label || human(a.metric) || human(a.analysis_type) || 'Reported measure'), fix(a.value_as_reported != null ? a.value_as_reported : a.value)], ' — ')),
          el('div', 'ju-rowsub', join([fix(a.publisher || ''), a.period && a.period.evaluation_cycle ? a.period.evaluation_cycle + ' cycle' : '', a.captured_at ? 'saved ' + date(a.captured_at) : ''])));
        var actions = el('div', 'ju-actions');
        append(actions, link('Publisher source ↗', a.source_url, true, 'ju-more'), link('Methodology ↗', a.methodology_url, true, 'ju-more'));
        if (actions.children.length) item.append(actions);
        list.append(item);
      });
      analysisBlock.append(list);
      box.append(analysisBlock);
    }

    if (documents.length) {
      var groups = {};
      documents.forEach(function (doc) {
        var key = human(doc.kind || doc.availability || 'document') || 'Document';
        (groups[key] = groups[key] || []).push(doc);
      });
      Object.keys(groups).sort().forEach(function (key) {
        var group = block(({ 'publisher link': 'Publisher reports', 'indexed record': 'Saved documents' })[key] || key.charAt(0).toUpperCase() + key.slice(1));
        var list = el('ul', 'ju-list');
        groups[key].forEach(function (doc) {
          var item = el('li');
          item.append(el('div', 'ju-rowtitle', fix(doc.title || 'Related document')));
          var sub = join([fix(doc.publisher || ''), doc.date ? date(doc.date) : '', fix(doc.description || '')]);
          if (sub) item.append(el('div', 'ju-rowsub', sub));
          var actions = el('div', 'ju-actions');
          var recordID = doc.record_id || (doc.availability === 'indexed_record' ? doc.id : null);
          if (recordID && typeof openRecord === 'function') {
            actions.append(action('Read document', function (event) { openRecord({ id: recordID, title: doc.title }, event.currentTarget); }, 'button-small'));
          }
          append(actions, link('Open original ↗', doc.original_url, true, 'ju-more'), link(doc.availability === 'publisher_link' || doc.external ? 'Open report ↗' : 'Visit source ↗', doc.source_url || doc.url, true, 'ju-more'));
          if (actions.children.length) item.append(actions);
          list.append(item);
        });
        group.append(list);
        box.append(group);
      });
    }

    if (references.length) {
      var referenceBlock = block('Report & dashboard links');
      var refList = el('ul', 'ju-list');
      references.forEach(function (ref) {
        var item = el('li');
        append(item,
          el('div', 'ju-rowtitle', fix(ref.title || ref.label || 'Publisher report')),
          el('div', 'ju-rowsub', join([fix(ref.publisher || ''), human(ref.kind || '')])),
          link('Open publisher reference ↗', ref.url || ref.source_url, true, 'ju-more'));
        refList.append(item);
      });
      referenceBlock.append(refList);
      box.append(referenceBlock);
    }
    return box;
  }

  function sourceEntries(profile) {
    var out = [], seen = {}, urls = {};
    function push(publisher, title, url, note) {
      var key = (url || '') + '|' + (title || '') + '|' + (publisher || '');
      if (seen[key]) return;
      seen[key] = true;
      if (url) urls[url] = true;
      out.push({ publisher: publisher, title: title, url: url, note: note });
    }
    arr(profile.sources).forEach(function (s) {
      push(fix(s.publisher || 'Source'), fix(s.title || ''), s.url || s.source_url, s.captured_at ? 'captured ' + date(s.captured_at) : '');
    });
    var structured = profile.structured || {};
    if (structured.fjc && structured.fjc.source) {
      push('Federal Judicial Center', fix(structured.fjc.source), structured.fjc.source_url, structured.fjc.captured_at ? 'captured ' + date(structured.fjc.captured_at) : '');
    }
    if (structured.courtlistener && structured.courtlistener.source) {
      push('CourtListener', fix(structured.courtlistener.source), '', 'snapshot ' + (structured.courtlistener.source_snapshot || 'not stated'));
    }
    var evidenceSource = profile.evidence && profile.evidence.courtlistener_source;
    if (evidenceSource && evidenceSource.source) {
      push('CourtListener', fix(evidenceSource.source), '', 'snapshot ' + (evidenceSource.source_snapshot || 'not stated'));
    }
    var insights = profile.library_insights || {};
    arr(insights.source_urls).forEach(function (url) { if (!urls[url]) push('Saved source page', '', url, ''); });
    return out;
  }

  function buildSourcesSection(id, profile) {
    var entries = sourceEntries(profile);
    if (!entries.length) return null;
    var box = sectionCard(id, 'Sources');
    var list = el('ul', 'ju-srclist');
    entries.forEach(function (entry) {
      var item = el('li');
      append(item,
        el('div', 'ju-rowtitle', entry.publisher),
        entry.title || entry.note ? el('div', 'ju-rowsub', join([entry.title, entry.note])) : null,
        link('Open source ↗', entry.url, true, 'ju-more'));
      list.append(item);
    });
    box.append(list);
    return box;
  }

  /* ----------------------------------------------------------- right-hand rail */

  function judgeRail(profile) {
    var structured = profile.structured || {};
    var fjc = structured.fjc || {};
    var appointment = (fjc.appointments || [])[0] || {};
    var status = structured.fjc_status || null;
    var official = typeof isOfficialSourceProfile === 'function' && isOfficialSourceProfile(profile);
    var rail = el('div', 'ju-rail');

    var glance = sectionCard('', 'At a glance');
    glance.removeAttribute('id');
    var facts = dlBox();
    facts.row('Court', arr(profile.courts).map(fix).join('; '));
    facts.row('Court system', profile.system);
    facts.row('Jurisdiction', arr(profile.states).length ? arr(profile.states).join('; ') : (profile.state || profile.location));
    facts.row('Role', structured.role_label || (official ? profile.role : ''));
    facts.row('Term as published', fix(profile.term_as_published || ''));
    facts.row('Reported status', status && status.value ? sentence(human(status.value)) : '', status && status.as_of ? 'FJC export as of ' + date(status.as_of) + '; not independently verified' : '');
    facts.row('Appointed by', appointment.appointing_president);
    facts.row('Party of appointing president', appointment.party_of_appointing_president, 'Party of the appointing president as published, not the judge’s own affiliation');
    facts.row('ABA rating', appointment.aba_rating);
    facts.row('Senate vote', join([appointment.senate_vote_type, appointment.senate_vote_ayes_nays || appointment.ayes_nays], ' '));
    facts.row('Chief judge', appointment.chief_judge_begin ? appointment.chief_judge_begin + (appointment.chief_judge_end ? '–' + appointment.chief_judge_end : '–present as published') : '');
    facts.row('Chambers', chambers(profile));
    if (facts.children.length) glance.append(facts);
    if (glance.children.length > 1) rail.append(glance);

    var technical = sectionCard('', 'Record');
    technical.removeAttribute('id');
    var details = el('details', 'ju-tech');
    details.append(el('summary', '', 'Technical details'));
    var ids = structured.ids || (profile.evidence && profile.evidence.ids) || {};
    var idFacts = dlBox();
    idFacts.row('Profile identity', profile.entity_id ? 'Consolidated entity' : (official ? 'Official source profile' : 'Source profile'));
    idFacts.row('FJC nid', ids.fjc_nid ? codeNode(ids.fjc_nid) : '');
    idFacts.row('FJC jid', ids.fjc_jid ? codeNode(ids.fjc_jid) : '');
    idFacts.row('CourtListener person', ids.cl_person_id ? codeNode(ids.cl_person_id) : '');
    idFacts.row('Identifier bridge', ids.bridge_status ? human(ids.bridge_status) : '', ids.bridge_basis ? 'Native ids only; never joined by name' : '');
    idFacts.row('Entity id', profile.entity_id ? codeNode(profile.entity_id) : '');
    idFacts.row('Record id', profile.record_id || profile.id ? codeNode(profile.record_id || profile.id) : '');
    if (structured.completeness && structured.completeness.score !== undefined) {
      idFacts.row('Evidence coverage', structured.completeness.score + ' / ' + (structured.completeness.max || 100), 'Coverage of this record in the saved corpus, not a measure of the judge');
    }
    if (idFacts.children.length) details.append(idFacts);
    var record = profile.record_id || (!official ? profile.id : null);
    if (record && typeof openRecord === 'function') {
      var inspect = action('Inspect saved record', function (event) { openRecord({ id: record }, event.currentTarget); }, 'button-small');
      inspect.style.marginTop = '10px';
      details.append(inspect);
    }
    technical.append(details);
    rail.append(technical);
    return rail;
  }

  function codeNode(value) { return el('code', '', String(value)); }

  function chambers(profile) {
    var positions = (profile.evidence && profile.evidence.positions) || [];
    for (var i = 0; i < positions.length; i++) {
      var p = positions[i];
      if (!p.date_termination && (p.location_city || p.location_state)) return join([p.location_city, p.location_state], ', ');
    }
    return '';
  }

  /* ------------------------------------------------------------ judge profile */

  function renderProfile(container, profile, context) {
    ensureStyle();
    if (!container) return null;
    var p = profile || {};
    var id = 'ju' + (++seq);
    var official = typeof isOfficialSourceProfile === 'function' && isOfficialSourceProfile(p);
    var structured = p.structured || {};
    var fjc = structured.fjc || {};
    var appointment = (fjc.appointments || [])[0] || {};
    var status = structured.fjc_status || null;
    var name = fix(official ? (p.profile_heading_as_published || p.name) : p.name) || 'Judicial profile';
    var courtLabel = arr(p.courts).length ? fix(arr(p.courts)[0]) : readable(p.courts);

    var wrap = el('div', 'ju ju-judge');
    wrap.append(backControl('judge', context));

    /* hero */
    var hero = el('section', 'ju-hero');
    hero.append(avatar(p.name, p.photo_url));
    var identity = el('div');
    append(identity,
      el('p', 'ju-eyebrow', official ? 'Official court profile' : (structured.role_label || p.role || 'Judicial profile')),
      el('h1', 'ju-name', name),
      courtLabel ? el('p', 'ju-court', courtLabel) : null,
      el('p', 'ju-meta', join([p.location || p.state || arr(p.states).join('; '), p.system, appointment.title])));

    var chips = el('div', 'ju-chips');
    if (status && status.value) {
      chips.append(badge(sentence(human(status.value)) + ' — FJC as of ' + (status.as_of ? date(status.as_of) : 'snapshot date'), status.value === 'active' ? '' : 'neutral'));
    }
    if (appointment.appointing_president) chips.append(badge('Appointed by ' + appointment.appointing_president, 'neutral'));
    if (official && p.term_as_published) chips.append(badge('Term as published: ' + fix(p.term_as_published), 'neutral'));
    arr(p.aliases).slice(0, 3).forEach(function (a) {
      var label = typeof a === 'string' ? a : a.alias || a.name;
      if (!label) return;
      var chip = badge('Also printed as “' + fix(label) + '”', 'neutral');
      if (a && a.source) chip.title = join([fix(a.source), fix(a.basis || '')], '. ');
      chips.append(chip);
    });
    if (chips.children.length) identity.append(chips);
    hero.append(identity);

    /* stat tiles */
    var tiles = [];
    var mdls = mdlRows(p);
    var asOf = p.mdls && p.mdls.as_of ? date(p.mdls.as_of) : '';
    if (mdls.length) {
      tiles.push(tile(mdls.length, 'MDLs assigned', asOf ? 'JPML report ' + asOf : ''));
      tiles.push(tile(mdls.reduce(function (n, m) { return n + (num(m.actions_pending) || 0); }, 0), 'Actions pending', asOf ? 'as listed ' + asOf : ''));
    }
    var commission = appointment.commission_date;
    if (commission && /^\d{4}-\d{2}-\d{2}$/.test(commission) && !appointment.termination_date) {
      var years = Math.floor((Date.now() - new Date(commission + 'T12:00:00Z').getTime()) / 31557600000);
      if (years >= 0) tiles.push(tile(years, 'Years since commission', 'commissioned ' + date(commission) + ' (FJC)'));
    }
    var assignments = p.evidence && p.evidence.assignments;
    if (assignments && (num(assignments.matters_for_this_judge) || num(assignments.assigned_count))) {
      tiles.push(tile(num(assignments.matters_for_this_judge) || num(assignments.assigned_count), 'Matters in saved dataset', assignments.release_built_at ? 'release ' + date(assignments.release_built_at) : ''));
    }
    var disclosureYears = p.disclosures && p.disclosures.available ? (p.disclosures.years_filed || []) : [];
    if (disclosureYears.length) {
      tiles.push(tile(disclosureYears.length, 'Disclosure years', disclosureYears[0] + '–' + disclosureYears[disclosureYears.length - 1]));
    }
    if (arr(p.analyses).length) tiles.push(tile(arr(p.analyses).length, 'Saved analysis records', 'publisher measures'));
    if (arr(p.documents).length) tiles.push(tile(arr(p.documents).length, 'Saved documents', ''));
    if (!tiles.length) {
      if (educationRows(p).length) tiles.push(tile(educationRows(p).length, 'Education records', 'as saved'));
      var careerCount = texts(p.professional_career).length + texts(p.service).length;
      if (careerCount) tiles.push(tile(careerCount, 'Career entries', 'as saved'));
    }
    if (tiles.length) {
      var tileRow = el('div', 'ju-tiles');
      tiles.slice(0, 5).forEach(function (node) { tileRow.append(node); });
      hero.append(tileRow);
    }
    wrap.append(hero);

    /* sections */
    var bio = texts(p.biography).join('\n\n');
    var insights = p.library_insights || {};
    var overview = sectionCard(id + '-overview', 'Overview');
    if (bio) overview.append(prose(bio, 'reading-content'));
    else overview.append(el('p', 'ju-lead', 'No biography is saved for this profile. Everything below is what the sources print, each with its own date.'));

    arr(p.aliases).forEach(function (a) {
      var label = typeof a === 'string' ? a : a.alias || a.name;
      if (!label) return;
      overview.append(el('p', 'ju-foot', 'Also printed as “' + fix(label) + '”' + (a && a.source ? ' — ' + fix(a.source) + '.' : '.')));
    });

    var saved = join([
      p.saved_at ? 'Saved ' + date(p.saved_at) : '',
      p.source_as_of ? 'source as of ' + date(p.source_as_of) : 'no source as-of date stated'
    ]);
    if (arr(insights.available_sections).length) {
      var kept = block('Saved for this profile');
      var strip = el('div', 'ju-strip');
      arr(insights.available_sections).forEach(function (label) { strip.append(el('span', 'off', fix(label))); });
      kept.append(strip);
      overview.append(kept);
    }
    if (saved) overview.append(el('p', 'ju-foot', saved + '.'));

    var list = [
      { key: 'overview', label: 'Overview', node: overview },
      { key: 'mdls', label: 'MDLs & cases', node: buildMdlSection(id + '-mdls', p) },
      { key: 'career', label: 'Career', node: buildCareerSection(id + '-career', p) },
      { key: 'education', label: 'Education', node: buildEducationSection(id + '-education', p) },
      { key: 'disclosures', label: 'Disclosures', node: buildDisclosureSection(id + '-disclosures', p) },
      { key: 'documents', label: 'Documents', node: buildDocumentsSection(id + '-documents', p) },
      { key: 'sources', label: 'Sources', node: buildSourcesSection(id + '-sources', p) }
    ].filter(function (entry) { return entry.node; });

    wrap.append(buildNav(wrap, hero, name, courtLabel, p.photo_url, list));

    var body = el('div', 'ju-body');
    var column = el('div', 'ju-col');
    list.forEach(function (entry) { column.append(entry.node); });

    var missing = missingLine([
      bio ? '' : 'biography',
      typeof safeURL === 'function' && safeURL(p.photo_url) ? '' : 'portrait',
      arr(p.analyses).length ? '' : 'published analysis',
      arr(p.documents).length ? '' : 'saved documents',
      mdls.length ? '' : 'MDL assignments',
      p.evidence && p.evidence.cjra ? '' : 'CJRA report',
      assignments ? '' : 'saved docket assignments',
      disclosureYears.length ? '' : disclosureLabel(p.disclosures)
    ]);
    if (missing) column.append(missing);

    var about = aboutBox([
      ['Profile.', p.career_note],
      ['Dates.', p.date_note],
      ['Structured overlay.', structured.qualification],
      ['MDL list.', mdls.length ? 'Transferee judge as printed in the JPML report dated ' + (p.mdls.as_of || 'not stated') + ', joined by ' + human(p.mdls.basis || 'exact name and court') + '. Counts are as listed; they are not a current-assignment check and imply nothing about outcomes.' : ''],
      ['Saved docket dataset.', assignments ? assignments.dataset_note : ''],
      ['CJRA report.', p.evidence && p.evidence.cjra ? p.evidence.cjra.caveat : ''],
      ['Financial disclosures.', p.disclosures ? p.disclosures.qualification : ''],
      ['CourtListener blocks.', p.evidence && p.evidence.courtlistener_source ? p.evidence.courtlistener_source.note : ''],
      ['Method.', insights.methodology]
    ]);
    if (about) column.append(about);

    append(body, column, judgeRail(p));
    wrap.append(body);
    container.append(wrap);
    return wrap;
  }

  function disclosureLabel(disclosures) {
    if (!disclosures) return 'financial disclosures';
    if (disclosures.state === 'no_filing_in_snapshot') return 'financial disclosure filings (none in this snapshot)';
    if (disclosures.state === 'not_bridged_to_courtlistener_person_id') return 'financial disclosures (no CourtListener identifier bridge)';
    return 'financial disclosures';
  }

  /* ------------------------------------------------------- historical person */

  function personPositions(person) {
    return arr(person.positions).map(function (position) {
      var start = txt(position.start), end = txt(position.end);
      var extra = arr(position.dates).map(function (entry) { return join([txt(entry.label), txt(entry.text)], ': '); }).filter(Boolean);
      return {
        year: yearOf(start) || yearOf(end),
        sort: Number(yearOf(start) || yearOf(end) || -1),
        title: txt(position.role) || 'Reported position',
        place: join([txt(position.court), txt(position.organization), txt(position.location)]),
        dates: join([start && end ? start + ' – ' + end : start ? 'from ' + start : end ? 'to ' + end : ''].concat(extra)),
        tags: position.inferred === true ? [{ label: 'Source-inferred values' }] : []
      };
    });
  }

  function renderPerson(container, person, context) {
    ensureStyle();
    if (!container) return null;
    var p = person || {};
    var id = 'ju' + (++seq);
    var name = fix(p.name) || 'Historical biography';
    var source = p.source || {};

    var wrap = el('div', 'ju ju-person');
    wrap.append(backControl('person', context));

    var hero = el('section', 'ju-hero');
    hero.append(avatar(p.name, p.photo_url || ''));
    var identity = el('div');
    var life = join([txt(p.birth), txt(p.death)], ' – ');
    append(identity,
      el('p', 'ju-eyebrow', 'Historical record · CourtListener people database'),
      el('h1', 'ju-name', name),
      arr(p.positions).length && txt(arr(p.positions)[0].court) ? el('p', 'ju-court', txt(arr(p.positions)[0].court)) : null,
      life ? el('p', 'ju-meta', life) : null);

    var chips = el('div', 'ju-chips');
    chips.append(badge(source.snapshot_label ? 'Saved snapshot: ' + fix(source.snapshot_label) : 'Saved historical snapshot', 'neutral'));
    if (p.is_alias) chips.append(badge('Alias record', 'amber'));
    if (source.has_inferred_values === true) chips.append(badge('Contains source-inferred values', 'neutral'));
    identity.append(chips);
    hero.append(identity);

    var positions = personPositions(p);
    var educations = arr(p.educations);
    var courts = {};
    arr(p.positions).forEach(function (position) { var court = txt(position.court); if (court) courts[court] = true; });
    var courtNames = Object.keys(courts);
    var statedYears = positions.map(function (entry) { return entry.sort; }).filter(function (year) { return year > 0; });
    var tiles = [];
    if (positions.length) tiles.push(tile(positions.length, 'Positions recorded', 'source rows'));
    if (courtNames.length) tiles.push(tile(courtNames.length, 'Courts named', 'as recorded'));
    if (educations.length) tiles.push(tile(educations.length, 'Education records', 'source rows'));
    if (statedYears.length > 1) {
      tiles.push(tile(Math.min.apply(null, statedYears) + '–' + Math.max.apply(null, statedYears), 'Stated date range', 'earliest and latest stated years'));
    }
    if (tiles.length) {
      var tileRow = el('div', 'ju-tiles');
      tiles.slice(0, 5).forEach(function (node) { tileRow.append(node); });
      hero.append(tileRow);
    }
    wrap.append(hero);

    /* overview */
    var overview = sectionCard(id + '-overview', 'Overview');
    overview.append(el('p', 'ju-lead', 'Career and education exactly as recorded in the saved CourtListener people collection. This collection is separate from the consolidated judge directory and is not merged with it.'));
    if (p.is_alias) {
      var notice = el('div', 'ju-block');
      notice.append(el('p', 'ju-rowtitle', 'This is an alias record in the source collection.'));
      if (p.alias_of && p.alias_of.id !== undefined && p.alias_of.id !== null) {
        notice.append(routeLink('Open ' + fix(p.alias_of.name || 'the referenced person') + ' →', 'person', { id: p.alias_of.id, from: location.hash || '#people' }, 'ju-more'));
      }
      overview.append(notice);
    }
    if (courtNames.length) {
      var courtList = el('div', 'ju-strip');
      courtNames.forEach(function (court) { courtList.append(el('span', 'off', court)); });
      var courtBlock = block('Courts named in this record');
      courtBlock.append(courtList);
      overview.append(courtBlock);
    }

    var career = null;
    if (positions.length) {
      career = sectionCard(id + '-career', 'Career');
      career.append(timeline(positions.sort(function (a, b) { return (b.sort || -1) - (a.sort || -1); })));
      career.append(el('p', 'ju-foot', 'Dates exactly as recorded, with the source’s own precision. A missing end date does not establish current service.'));
    }

    var education = null;
    if (educations.length) {
      education = sectionCard(id + '-education', 'Education');
      var list = el('ul', 'ju-two');
      educations.forEach(function (entry) {
        var item = el('li');
        append(item,
          el('b', '', join([txt(entry.school) || 'School not recorded', join([txt(entry.degree), txt(entry.degree_detail)], ' ')], ', ')),
          txt(entry.year) ? el('em', '', txt(entry.year)) : null);
        list.append(item);
      });
      education.append(list);
    }

    var sources = sectionCard(id + '-sources', 'Sources');
    var sourceFacts = dlBox();
    sourceFacts.row('Publisher', fix(source.label || 'CourtListener'));
    sourceFacts.row('Snapshot', fix(source.snapshot_label || ''));
    sourceFacts.row('Collection', 'CourtListener people bulk data, saved locally');
    sources.append(sourceFacts);

    var list = [
      { key: 'overview', label: 'Overview', node: overview },
      { key: 'career', label: 'Career', node: career },
      { key: 'education', label: 'Education', node: education },
      { key: 'sources', label: 'Sources', node: sources }
    ].filter(function (entry) { return entry.node; });

    wrap.append(buildNav(wrap, hero, name, courtNames[0] || '', '', list));

    var body = el('div', 'ju-body');
    var column = el('div', 'ju-col');
    list.forEach(function (entry) { column.append(entry.node); });

    var missing = missingLine([
      positions.length ? '' : 'career positions',
      educations.length ? '' : 'education',
      txt(p.birth) ? '' : 'birth date',
      txt(p.death) ? '' : 'death date',
      p.photo_reference ? '' : 'portrait reference'
    ]);
    if (missing) column.append(missing);

    var about = aboutBox([
      ['Collection.', source.qualification],
      ['Inferred values.', source.has_inferred_values === true ? 'Some values in this record were inferred by the source. Affected rows are labelled and have not been independently verified.' : ''],
      ['Portrait.', p.photo_credit ? p.photo_credit + ' Attached by the same CourtListener person id; it may be historical.' : (p.photo_reference ? 'The publisher records a photo reference for this person; no portrait file is supplied by this view.' : '')]
    ]);
    if (about) column.append(about);

    /* rail */
    var rail = el('div', 'ju-rail');
    var glance = sectionCard('', 'At a glance');
    glance.removeAttribute('id');
    var facts = dlBox();
    facts.row('Born', txt(p.birth), p.birth && p.birth.precision ? human(p.birth.precision) + ' precision' : '');
    facts.row('Died', txt(p.death), p.death && p.death.precision ? human(p.death.precision) + ' precision' : '');
    facts.row('Courts named', courtNames.join('; '));
    facts.row('Record type', p.is_alias ? 'Alias record' : 'Person record');
    facts.row('Snapshot', fix(source.snapshot_label || ''));
    if (facts.children.length) glance.append(facts);
    if (glance.children.length > 1) rail.append(glance);

    var technical = sectionCard('', 'Record');
    technical.removeAttribute('id');
    var details = el('details', 'ju-tech');
    details.append(el('summary', '', 'Technical details'));
    var idFacts = dlBox();
    idFacts.row('Native person id', codeNode(source.person_id != null ? source.person_id : p.id));
    if (source.legacy_fjc_id) idFacts.row('Legacy FJC id', codeNode(source.legacy_fjc_id));
    if (p.alias_of && p.alias_of.id != null) idFacts.row('Alias of', codeNode(p.alias_of.id));
    details.append(idFacts);
    if (arr(source.source_files).length) {
      var files = el('ul', 'ju-list');
      arr(source.source_files).forEach(function (file) {
        var item = el('li');
        append(item, el('div', 'ju-rowtitle', txt(file.table) || 'Source table'), el('div', 'ju-rowsub', codeNode(txt(file.sha256))));
        files.append(item);
      });
      details.append(el('p', 'ju-foot', 'Saved source files (SHA-256):'));
      details.append(files);
    }
    technical.append(details);
    rail.append(technical);

    append(body, column, rail);
    wrap.append(body);
    container.append(wrap);
    return wrap;
  }

  /* ------------------------------------------------------------------- cards */

  function isPersonRow(item) {
    return item && (item.is_alias !== undefined || item.career_summary !== undefined || item.has_photo_reference !== undefined) && item.profile_layer === undefined;
  }

  function renderCard(item) {
    ensureStyle();
    var row = item || {};
    var person = isPersonRow(row);
    var name = fix(person ? row.name : (row.profile_heading_as_published || row.name)) || (person ? 'Unnamed source record' : 'Judge profile');
    var card = el('a', 'ju-pcard');
    card.href = person
      ? makeHash('person', { id: row.id, from: location.hash || '#people' })
      : makeHash('judge/' + encodeURIComponent(row.id), { from: location.hash || '#judges' });

    card.append(avatar(row.name, row.photo_url || '', 'card'));
    var body = el('div', 'ju-pcard-body');

    var role = person ? 'Historical record' : (row.profile_layer === 'official_source' ? 'Official court profile' : (fix(row.role) || 'Judicial profile'));
    body.append(el('p', 'ju-pcard-role', role));
    body.append(el('h3', 'ju-pcard-name', name));

    var courts = arr(row.courts).map(fix).filter(Boolean);
    var courtLabel = courts.length ? courts[0] : (person ? '' : readable(row.courts));
    if (courtLabel) {
      var court = el('p', 'ju-pcard-court', courtLabel + (courts.length > 1 ? ' · +' + (courts.length - 1) + ' more' : ''));
      court.title = courts.join(' · ');
      body.append(court);
    }

    var status = person
      ? (row.is_alias ? badge('Alias record', 'amber') : badge('CourtListener people', 'neutral'))
      : (row.term_as_published ? badge('Term: ' + fix(row.term_as_published), 'neutral')
        : (row.has_details ? badge('Detailed profile', 'neutral') : badge('Directory entry', 'neutral')));
    body.append(status);

    var facts = [];
    if (person) {
      var summary = fix(row.career_summary || '');
      if (summary) facts.push(summary);
      var educationSummary = fix(row.education_summary || '');
      if (educationSummary) facts.push(educationSummary);
      if (!facts.length && row.has_photo_reference) facts.push('Photo reference recorded');
    } else {
      facts.push(fix(row.location || row.state || arr(row.states).join('; ')));
      var counts = [];
      if (num(row.education_count)) counts.push(count(row.education_count) + ' education');
      if (num(row.career_count)) counts.push(count(row.career_count) + ' career');
      if (num(row.analysis_count)) counts.push(count(row.analysis_count) + ' analysis');
      if (num(row.report_link_count)) counts.push(count(row.report_link_count) + ' report links');
      if (counts.length) facts.push(counts.join(' · '));
      else if (row.biography_excerpt) facts.push(fix(row.biography_excerpt).slice(0, 96) + '…');
    }
    facts.filter(Boolean).slice(0, 2).forEach(function (fact) { body.append(el('p', 'ju-pcard-facts', fact)); });

    body.append(el('div', 'ju-pcard-foot', person ? 'Read biography →' : 'View profile →'));
    card.append(body);
    return card;
  }

  /* ------------------------------------------------------------------ export */

  window.JudgeUI = {
    profile: renderProfile,
    person: renderPerson,
    card: renderCard
  };
})();
