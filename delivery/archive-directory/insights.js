/* Firm, MDL and year cross-counts from the saved catalog.

   Three controls and four views. Every number is a count of rows in that catalog.
   Bar length is relative to the longest bar on that chart. No rate or share is computed.
   Firm names that differ only by punctuation, capitalization, an ampersand, or a firm suffix are counted once.
   Attorney initials are ignored. Repeat docket files of one case are collapsed. */
(function () {
  'use strict';
  const STYLE_ID = 'insights-style';
  const CSS = [
    '.in-controls{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:10px;margin:0 0 14px}',
    '.in-controls label{display:flex;flex-direction:column;gap:4px;font-size:12px;color:var(--muted)}',
    '.in-controls select{min-width:0}',
    '.in-switch{display:flex;flex-wrap:wrap;gap:6px;margin:0 0 14px}',
    '.in-kpis{display:grid;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));gap:8px;margin:0 0 14px}',
    '.in-kpi{background:var(--surface);border:1px solid var(--line);border-radius:var(--radius);padding:12px 14px}',
    '.in-kpi:first-child{border-top:3px solid var(--teal);padding-top:10px}',
    '.in-kpi span{display:block;font-size:10px;font-weight:650;letter-spacing:.4px;text-transform:uppercase;color:var(--muted)}',
    '.in-kpi strong{display:block;font:400 28px/1.15 Georgia,"Times New Roman",serif;margin-top:6px;font-variant-numeric:tabular-nums}',
    '.in-card{background:var(--surface);border:1px solid var(--line);border-radius:var(--radius);padding:14px 16px;margin:0 0 12px}',
    '.in-card h2{margin-bottom:10px}',
    '.in-row{display:grid;grid-template-columns:minmax(120px,34%) 1fr auto;gap:8px;align-items:center;margin:0 0 6px}',
    '.in-label{font-size:12px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}',
    '.in-track{display:block;height:8px;background:#e7eeeb;border-radius:99px;overflow:hidden}',
    '.in-fill{display:block;height:100%;background:var(--teal);border-radius:99px}',
    '.in-count{font-size:12px;font-variant-numeric:tabular-nums;color:var(--muted)}',
    '.in-note{margin:8px 0 0;font-size:12px;color:var(--muted);line-height:1.5}',
    '.in-list{margin:0;padding:0;list-style:none}',
    '.in-list li{padding:6px 0;border-top:1px solid var(--line);font-size:13px}',
    '@media(max-width:700px){.in-controls{grid-template-columns:1fr}.in-row{grid-template-columns:1fr auto}.in-track{display:none}}'
  ].join('');

  function installStyle() {
    if (document.getElementById(STYLE_ID)) return;
    const node = document.createElement('style');
    node.id = STYLE_ID;
    node.textContent = CSS;
    document.head.append(node);
  }

  function tally(items) {
    const map = new Map();
    for (const item of items) map.set(item, (map.get(item) || 0) + 1);
    return [...map.entries()].sort((a, b) => b[1] - a[1] || String(a[0]).localeCompare(String(b[0])));
  }

  function option(select, value, label) {
    const node = el('option', '', label);
    node.value = value;
    select.append(node);
  }

  function chart(title, pairs, note, limit) {
    const box = el('section', 'in-card');
    box.append(el('h2', '', title));
    if (!pairs.length) {
      box.append(el('p', 'in-note', 'Nothing in this selection.'));
      return box;
    }
    const max = Math.max(...pairs.map(pair => pair[1]));
    for (const [label, value] of pairs.slice(0, limit || 12)) {
      const row = el('div', 'in-row');
      const track = el('span', 'in-track');
      const fill = el('span', 'in-fill');
      fill.style.width = Math.max(2, Math.round(value / max * 100)) + '%';
      track.append(fill);
      row.append(el('span', 'in-label', label), track, el('span', 'in-count', count(value)));
      box.append(row);
    }
    box.append(el('p', 'in-note', note));
    return box;
  }

  async function render(signal) {
    installStyle();
    const data = window.CATALOG_INSIGHTS;
    if (!data) { emptyState(main, 'Insights are not loaded', 'Reload this page.', () => navigate('insights')); return; }
    const params = route.params;
    const year = params.get('year') || '';
    const mdl = params.get('mdl') || '';
    const firm = params.get('firm') || '';
    const focus = params.get('focus') || 'matters';
    heading('Insights', 'Firm, MDL and year counts from the saved matter catalog.', 'Courts & litigation');
    const form = el('form', 'in-controls');
    form.setAttribute('aria-label', 'Cross analysis');
    const yearSelect = el('select'); yearSelect.name = 'year';
    const mdlSelect = el('select'); mdlSelect.name = 'mdl';
    const firmSelect = el('select'); firmSelect.name = 'firm';
    option(yearSelect, '', 'All years');
    option(mdlSelect, '', 'All MDLs');
    option(firmSelect, '', 'All firms');
    const years = tally(data.matters.map(row => row.year).filter(Boolean));
    const mdls = new Map();
    const firms = new Set();
    for (const row of data.matters) {
      if (row.mdl && !mdls.has(row.mdl)) mdls.set(row.mdl, row.mdl_name || '');
      for (const name of row.firms) if (name) firms.add(name);
    }
    for (const [value] of years) option(yearSelect, value, value);
    option(mdlSelect, 'none', 'No MDL link');
    option(mdlSelect, 'master', 'Master docket, no MDL number');
    for (const [number, name] of [...mdls.entries()].filter(([number]) => !String(number).startsWith('master:')).sort((a, b) => Number(a[0]) - Number(b[0]) || a[0].localeCompare(b[0]))) option(mdlSelect, number, name ? `MDL ${number} — ${name}` : `MDL ${number}`);
    for (const name of [...firms].sort()) option(firmSelect, name, name);
    yearSelect.value = year; mdlSelect.value = mdl; firmSelect.value = firm;
    const yearLabel = el('label', '', 'Year'); yearLabel.append(yearSelect);
    const mdlLabel = el('label', '', 'MDL'); mdlLabel.append(mdlSelect);
    const firmLabel = el('label', '', 'Firm on the matter'); firmLabel.append(firmSelect);
    append(form, yearLabel, mdlLabel, firmLabel);
    form.addEventListener('change', () => navigate('insights', { year: yearSelect.value, mdl: mdlSelect.value, firm: firmSelect.value, focus }));
    main.append(form);

    const matters = data.matters.filter(row => {
      if (year && row.year !== year) return false;
      if (mdl === 'none' && row.mdl) return false;
      if (mdl === 'master' && !String(row.mdl).startsWith('master:')) return false;
      if (mdl && mdl !== 'none' && mdl !== 'master' && row.mdl !== mdl) return false;
      if (firm && !row.firms.includes(firm)) return false;
      return true;
    });
    const inMdl = row => !mdl ? true : mdl === 'none' ? !row.mdl : mdl === 'master' ? String(row.mdl).startsWith('master:') : row.mdl === mdl;
    const parties = data.parties.filter(inMdl);
    const counsel = data.counsel.filter(inMdl);
    const firmPairs = tally(matters.flatMap(row => row.firms.filter(Boolean)));
    const kpis = el('div', 'in-kpis');
    for (const [label, value] of [['Matters in this selection', matters.length], ['Firms named on those matters', firmPairs.length], ['Parties on the MDL selection', mdl ? parties.length : data.parties.length], ['Citation edges in the catalog', data.citation_edges]]) {
      const card = el('div', 'in-kpi');
      card.append(el('span', '', label), el('strong', '', count(value)));
      kpis.append(card);
    }
    if (focus !== 'public') main.append(kpis);

    const switcher = el('div', 'in-switch');
    for (const [id, label] of [['matters', 'Matters'], ['public', 'Public record'], ['universe', 'Docket universe'], ['firms', 'Firms'], ['parties', 'Parties'], ['citations', 'Citations']]) {
      const button = el('button', focus === id ? 'button button-primary' : 'button', label);
      button.type = 'button';
      button.addEventListener('click', () => navigate('insights', { year, mdl, firm, focus: id }));
      switcher.append(button);
    }
    main.append(switcher);

    const largest = (pairs, noun) => pairs.length ? `${pairs[0][0] || 'Not recorded'} has the most ${noun} in this selection: ${count(pairs[0][1])}.` : 'This selection is empty.';
    if (focus === 'public') {
      const maps = data.maps || {};
      const mapped = rows => (rows || []).map(row => [row.label, row.count]);
      let register = null;
      let cited = null;
      try { register = await api('/api/area/federal-register?limit=1', signal); } catch (error) { if (error.name === 'AbortError') return; }
      try { cited = await api('/api/area/citation-index?limit=1', signal); } catch (error) { if (error.name === 'AbortError') return; }
      const options = (payload, name) => ((payload && payload.filters) || []).find(filter => filter.name === name);
      const year = options(register, 'year');
      const type = options(register, 'type');
      const agency = options(register, 'agency');
      const kind = options(cited, 'kind');
      main.append(el('p', 'page-summary', 'These figures come from the hosted Federal Register and citation collections, plus official GovInfo bulk links captured 2026-08-20. They are not the firm matter list.'));
      if (year) main.append(chart('Federal Register documents by year', year.options.map(row => [row.label, row.count]).slice().reverse(), `${count(register.total)} documents, 1994 through mid-2026. Bar length is relative to the longest year.`, 40));
      if (type) main.append(chart('Federal Register by document type', type.options.map(row => [row.label, row.count]), 'Each document has one type in the publisher index.'));
      if (agency) main.append(chart('Federal Register by agency', agency.options.slice(0, 12).map(row => [row.label, row.count]), 'Top 12 agencies by document count. A document can name more than one agency, so these counts overlap.'));
      if (kind) main.append(chart('Authorities cited in saved documents', kind.options.map(row => [row.label, row.count]), `${count(cited.total)} citations extracted from saved documents.`));
      main.append(chart('U.S. Code titles cited as CFR authority', mapped(maps.cfr_authority_by_usc_title), `${count(maps.cfr_authority_edges)} U.S. Code citations printed in 2025 CFR authority notes.`));
      main.append(chart('CFR titles with a Federal Register source note', mapped(maps.cfr_fr_by_title), `${count(maps.cfr_fr_edges)} section source notes cite a Federal Register page. One section can have several.`));
      main.append(chart('U.S. Reports decisions by decade', mapped(maps.us_reports_by_decade), `${count(maps.us_reports)} Supreme Court decisions identified in the GovInfo U.S. Reports collection.`, 30));
      main.append(dataNote(data.qualification));
      return;
    }
    if (focus === 'matters') {
      const byYear = tally(matters.map(row => row.year || 'No filing date'));
      const byMdl = tally(matters.map(row => !row.mdl ? 'No MDL link' : String(row.mdl).startsWith('master:') ? 'Master docket, no MDL number' : `MDL ${row.mdl}`));
      main.append(el('p', 'page-summary', `${count(matters.length)} matters in this selection. ${largest(byYear, 'filings')}`));
      main.append(chart('Filings by year', byYear, 'Top 12 years by count in this selection. Bar length is relative to the longest bar, not a share of all cases.'));
      main.append(chart('Matters by state', tally(matters.map(row => row.state || 'No state')), 'State is taken from the court id. Circuit courts are not given a state.'));
      main.append(chart('Matters by status', tally(matters.map(row => row.status || 'Not recorded')), 'Status is the value stored on the case row.'));
      main.append(chart('Matters by MDL', byMdl, 'Top 12 MDLs by count in this selection. A matter with no master-docket link stays in its own row.'));
    } else if (focus === 'universe') {
      const masters = data.masters || [];
      const entries = masters.reduce((sum, row) => sum + row.entries, 0);
      const copies = masters.reduce((sum, row) => sum + (row.copies - 1), 0);
      main.append(el('p', 'page-summary', `${count(masters.length)} scoped dockets after collapsing ${count(copies)} repeat files. ${count(entries)} docket-sheet rows in the larger copy of each. These are not member-case counts. Year and firm do not filter this list.`));
      main.append(chart('Dockets by type', tally(masters.map(row => row.kind || 'Other')), 'Type comes from the docket number in the file name.'));
      main.append(chart('Dockets by state', tally(masters.map(row => row.state || 'No state on the MDL crosswalk')), 'State is the court on the MDL crosswalk. Civil and miscellaneous files with no MDL number stay under no state.'));
      main.append(chart('Docket-sheet rows by docket', masters.slice(0, 12).map(row => [`${row.kind} ${row.number || row.year}`, row.entries]), 'The 12 dockets with the most saved rows. Repeat files are not added together.'));
    } else if (focus === 'firms') {
      main.append(el('p', 'page-summary', `${largest(firmPairs, 'matters')} A matter that names two firms is counted once for each.`));
      main.append(chart('Matters by firm', firmPairs, 'Firm names are the strings on the matter record. Spellings are not merged.'));
      const counselPairs = tally(counsel.map(row => row.firm));
      main.append(chart('Counsel appearances by firm', counselPairs, !mdl ? 'Attorneys on all 59 party-list dockets, counted once per party. Choose an MDL to limit this chart. Year and the matter-firm control do not apply here.' : mdl === 'none' ? 'Attorneys on party lists that have no MDL link, counted once per party.' : 'Attorneys on the party lists for this MDL, counted once per party. Year and the matter-firm control do not rename these firms.'));
      const aliases = (data.firm_aliases || []).slice(0, 12);
      if (aliases.length) {
        const list = el('ul', 'in-list');
        for (const alias of aliases) list.append(el('li', '', `${alias.name} — also recorded as ${alias.also.join(', ')}`));
        const box = el('section', 'in-card');
        box.append(el('h2', '', 'Firm spellings counted together'), list, el('p', 'in-note', 'Only capitalization, punctuation, an ampersand, and a firm suffix are ignored. Other similar names stay separate.'));
        main.append(box);
      }
      const people = (data.attorney_aliases || []).slice(0, 12);
      if (people.length) {
        const list = el('ul', 'in-list');
        for (const person of people) list.append(el('li', '', `${person.name} — also recorded as ${person.also.join(', ')}`));
        const box = el('section', 'in-card');
        box.append(el('h2', '', 'Attorney spellings counted together'), list, el('p', 'in-note', 'Initials and punctuation are ignored. Different surnames stay separate.'));
        main.append(box);
      }
    } else if (focus === 'parties') {
      const roles = tally(parties.map(row => row.role));
      main.append(el('p', 'page-summary', `${count(parties.length)} parties ${mdl ? 'on the selected MDL' : 'on the 59 dockets that have a party list'}. ${largest(roles, 'parties')}${year || firm ? ' Year and the matter-firm control do not filter party rows.' : ''}`));
      main.append(chart('Parties by role', roles, 'Role is the first party type recorded on that row.'));
      const list = el('ul', 'in-list');
      for (const row of parties.slice(0, 40)) {
        const where = !row.mdl ? '' : String(row.mdl).startsWith('master:') ? ' — master docket, no MDL number' : ` — MDL ${row.mdl}`;
        list.append(el('li', '', `${row.name} — ${row.role}${where}`));
      }
      const box = el('section', 'in-card');
      box.append(el('h2', '', 'Party names'), list, el('p', 'in-note', parties.length > 40 ? `First 40 of ${count(parties.length)} parties in this MDL selection, in catalog order.` : 'Every party in this MDL selection.'));
      main.append(box);
    } else {
      const cited = data.cited.map(row => [row.id, row.cites]);
      main.append(el('p', 'page-summary', `${count(data.citation_edges)} citation edges point at ${count(data.cited.length)} opinions. ${largest(cited, 'incoming citations')}`));
      const pairs = data.cited.slice(0, 12).map(row => [row.id, row.cites]);
      const box = chart('Most cited opinions in this catalog', pairs, 'Each count is the number of edges that name that opinion id. Year, MDL and firm are not recorded on these edges, so those controls do not change this chart.');
      main.append(box);
      const links = el('div', 'button-row');
      for (const row of data.cited.slice(0, 8)) links.append(link(`Opinion ${row.id} ↗`, `https://www.courtlistener.com/opinion/${row.id}/`, true));
      main.append(links);
    }
    main.append(dataNote(data.qualification));
  }

  const areas = window.ARCHIVE_AREAS = window.ARCHIVE_AREAS || {};
  areas.insights = { title: 'Insights', nav: 'Insights', parent: 'counsel', render };
})();
