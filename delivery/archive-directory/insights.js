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
      main.append(el('p', 'page-summary', 'These figures come from the hosted Federal Register and citation collections, official GovInfo bulk links captured 2026-08-20, Judicial Business 2025, the Judicial Panel on Multidistrict Litigation’s statistical reports, and the FDA Data Dashboard as read on September 28, 2026. They are not the firm matter list.'));
      const caseload = window.OFFICIAL_CASELOAD;
      if (caseload) {
        const civil = caseload.civil;
        const box = el('section', 'in-card');
        box.append(el('h2', '', 'U.S. district courts, year ending September 30, 2025'));
        const kpis = el('div', 'in-kpis');
        for (const [label, value] of [['Civil cases filed', civil.filed_2025], ['Civil cases terminated', civil.terminated_2025], ['Civil cases pending', civil.pending_2025], ['Median months to disposition', caseload.median_months.months]]) {
          const card = el('div', 'in-kpi');
          card.append(el('span', '', label), el('strong', '', count(value)));
          kpis.append(card);
        }
        box.append(kpis);
        box.append(el('p', 'in-note', `Table C. Filed rose from ${count(civil.filed_2024)} to ${count(civil.filed_2025)}. Pending fell from ${count(civil.pending_2024)} to ${count(civil.pending_2025)}. Table C-5 median is ${caseload.median_months.months} months across ${count(caseload.median_months.cases)} terminated cases. Table C-11 product-liability filings were ${count(caseload.product_liability.filed_2025)} in 2025 and ${count(caseload.product_liability.filed_2024)} in 2024. That table is not the same count as the personal-injury product-liability nature of suit below.`));
        main.append(box);
        const liability = (caseload.nature.rows || []).find(row => /product liability/i.test(row.label));
        if (liability) main.append(chart('Personal-injury product-liability cases filed', [['2021', liability.y2021], ['2022', liability.y2022], ['2023', liability.y2023], ['2024', liability.y2024], ['2025', liability.y2025]], 'Table C-2A, nature of suit. Counts are cases filed, as published.'));
        main.append(link('Judicial Business 2025 tables ↗', caseload.source_url, true));
      }
      const jpml = window.JPML_STATS;
      if (jpml) {
        const fiscal = jpml.fiscal_2025;
        const box = el('section', 'in-card');
        box.append(el('h2', '', 'Multidistrict litigation, fiscal year ending September 30, 2025'));
        const kpis = el('div', 'in-kpis');
        for (const [label, value] of [['Actions in MDL proceedings', fiscal.actions_subjected], ['Transferred by the Panel', fiscal.transferred], ['Filed in the transferee court', fiscal.direct_filed], ['Actions still pending', fiscal.pending_actions], ['MDLs still pending', fiscal.pending_mdls]]) {
          const card = el('div', 'in-kpi');
          card.append(el('span', '', label), el('strong', '', count(value)));
          kpis.append(card);
        }
        box.append(kpis);
        box.append(el('p', 'in-note', `JPML fiscal year statistical analysis. ${count(fiscal.actions_subjected)} civil actions were in coordinated proceedings: ${count(fiscal.transferred)} transferred from ${count(fiscal.transferor_districts)} districts into ${count(fiscal.transferee_districts)} transferee districts, and ${count(fiscal.direct_filed)} filed directly in a transferee district. The Panel received ${count(fiscal.motions_filed)} motions to centralize, granted ${count(fiscal.motions_granted)}, and denied ${count(fiscal.motions_denied)}, leaving ${count(fiscal.cases_not_transferred)} cases untransferred. Since 1968 the Panel has centralized ${count(fiscal.centralized_since_1968)} actions, remanded ${count(fiscal.remanded_since_1968)}, and transferee courts have terminated ${count(fiscal.terminated_in_transferee_courts)}. Pending counts are actions and dockets as of September 30, 2025. A calendar-year count below covers January through December and is not the same period.`));
        main.append(box);
        main.append(chart('New MDLs created in fiscal year 2025', fiscal.new_dockets.map(row => [row.type, row.mdls]), `${count(fiscal.motions_granted)} motions granted. The data-breach category is new in this fiscal year; those dockets were previously counted as miscellaneous.`));
        main.append(chart('Pending MDLs by type, December 31, 2025', jpml.pending_by_type.map(row => [`${row.type} (${row.share})`, row.mdls]), `${count(jpml.pending_by_type.reduce((sum, row) => sum + row.mdls, 0))} pending MDLs in the calendar-year report. Shares are the Panel’s printed shares. This is a docket count, not a count of member actions.`));
        const recent = jpml.series.slice(0, 15).slice().reverse();
        main.append(chart('Motions to centralize, calendar years 2011–2025', recent.map(row => [String(row.year), row.motions_filed]), 'Calendar Year Statistics, January through December. A fiscal-year motion count covers October through September and will not match the bar for the same numbered year.'));
        main.append(chart('Tag-along actions, calendar years 2011–2025', recent.map(row => [String(row.year), row.tag_alongs]), 'Tag-along actions in the calendar-year table. This is not the count of cases the Panel transferred.'));
        main.append(chart('Actions in motions the Panel granted, calendar years 2011–2025', recent.map(row => [String(row.year), row.actions_granted]), 'Civil actions covered by motions granted that calendar year.'));
        const current = jpml.current;
        if (current) {
          const now = el('section', 'in-card');
          now.append(el('h2', '', 'Open MDLs as of September 1, 2026'));
          const nowKpis = el('div', 'in-kpis');
          for (const [label, value] of [['Open MDLs', current.mdls], ['Actions still pending', current.actions_pending], ['Actions ever in those dockets', current.actions_historical], ['Transferee districts', current.transferee_districts], ['Transferee judges', current.transferee_judges]]) {
            const card = el('div', 'in-kpi');
            card.append(el('span', '', label), el('strong', '', count(value)));
            nowKpis.append(card);
          }
          now.append(nowKpis);
          now.append(el('p', 'in-note', 'JPML pending-docket report, limited to active transferred litigations. “Actions still pending” counts cases open on the report date. “Actions ever in those dockets” counts every action that has been in a docket that is still open, including ones already terminated. A docket can show a handful of pending cases and tens or hundreds of thousands of historical actions. This report is not the fiscal-year statistical analysis, so its pending total is not a revision of the September 30, 2025 figure.'));
          main.append(now);
          main.append(chart('Open MDLs by type, September 1, 2026', current.by_type.map(row => [row.type, row.mdls]), `${count(current.mdls)} open MDLs. This is a docket count. The December 31, 2025 type chart above is an earlier report and uses the Panel’s categories as of that date.`));
          main.append(chart('Where the pending actions sit', current.pending_buckets.map(row => [`${row.label} (${row.action_share} of actions)`, row.actions]), current.pending_buckets.map(row => `${row.docket_share} of dockets have ${row.label.toLowerCase()} (${count(row.dockets)} MDLs).`).join(' ') + ' Shares are the Panel’s printed shares.'));
          main.append(chart('Largest open MDLs by actions still pending', current.largest_pending.map(row => [`MDL ${row.mdl} ${row.name}`, row.pending]), 'Actions pending on September 1, 2026. The twelve largest open dockets.', 12));
          main.append(chart('Open dockets with the largest historical caseloads', current.mostly_resolved.map(row => [`MDL ${row.mdl} ${row.name} (${count(row.pending)} still pending)`, row.historical]), 'Historical actions in dockets that are still open. The pending count is in the label. 3M earplug is still on the open list with 2 actions pending and 391,225 historical actions.'));
        }
        const links = el('div', 'button-row');
        links.append(link('JPML statistics index ↗', jpml.index_url, true));
        links.append(link('Fiscal year 2025 analysis ↗', fiscal.source_url, true));
        links.append(link('Calendar year 2025 table ↗', jpml.source_url, true));
        if (jpml.current) links.append(link('Pending dockets, September 1, 2026 ↗', jpml.current.source_url, true));
        main.append(links);
      }
      const enrichment = window.LAW_ENRICHMENT;
      if (enrichment) {
        const labels = { settlement: 'Settlement', daubert_expert: 'Expert and Daubert', class_cert: 'Class certification', bellwether: 'Bellwether', summary_judgment: 'Summary judgment', master_complaint: 'Master complaint', complaint: 'Complaint', case_management_order: 'Case-management order', motion_to_dismiss: 'Motion to dismiss', motion: 'Motion', remand_transfer: 'Remand and transfer', opinion_order: 'Order and opinion', transcript: 'Transcript', answer: 'Answer', other: 'Other' };
        const amendments = enrichment.amendments;
        const dockets = enrichment.dockets;
        const box = el('section', 'in-card');
        box.append(el('h2', '', 'Public laws and master-docket filings'));
        const kpis = el('div', 'in-kpis');
        for (const [label, value] of [['Code edits marked in statute XML', amendments.edits], ['Of those, high confidence', amendments.high], ['MDL master dockets in this index', dockets.mdls.length], ['Docket entries on those dockets', dockets.entries]]) {
          const card = el('div', 'in-kpi');
          card.append(el('span', '', label), el('strong', '', count(value)));
          kpis.append(card);
        }
        box.append(kpis);
        box.append(el('p', 'in-note', `${amendments.source} ${dockets.source} Individual captions that are not “In re” captions are not shown. A category is the pipeline’s label on the docket text.`));
        main.append(box);
        main.append(chart('How public laws change the U.S. Code', amendments.actions.map(row => [row.action, row.count]), 'Each bar is one citation the statute XML marks with that action. Adds were absent from the prior semantics file and are included here.'));
        main.append(chart('U.S. Code titles with the most marked edits', amendments.titles.map(row => [`Title ${row.title}`, row.edits]), 'Direct edits only. A citation inside quoted text with no amending action is not in this chart.', 12));
        main.append(chart('Master-docket entries by pipeline category', dockets.totals.filter(row => row.key !== 'other').map(row => [labels[row.key] || row.key, row.count]), `${count(dockets.totals.find(row => row.key === 'other').count)} further entries are labeled other. ${count(dockets.available)} entries had a RECAP copy recorded. These labels are not the court’s document type.`));
        const substantive = dockets.mdls.map(row => {
          const keys = new Set(['settlement', 'daubert_expert', 'class_cert', 'bellwether', 'summary_judgment', 'master_complaint']);
          const total = row.categories.filter(item => keys.has(item.key)).reduce((sum, item) => sum + item.count, 0);
          const name = row.name ? row.name.replace(/^IN RE:\s*/i, '') : `MDL ${row.mdl}`;
          return [name.length > 42 ? name.slice(0, 40) + '…' : name, total, row.mdl];
        }).filter(row => row[1] > 0).sort((a, b) => b[1] - a[1]).slice(0, 12);
        main.append(chart('Settlement, expert, class, bellwether, and summary-judgment entries', substantive.map(row => [`MDL ${row[2]} ${row[0]}`, row[1]]), 'Sum of those six pipeline categories on each master docket. A routine order is not in this sum.', 12));
      }
      const fda = window.FDA_DASHBOARD;
      if (fda) {
        const recalls = fda.recalls;
        const inspections = fda.inspections;
        const compliance = fda.compliance;
        const third = fda.third_party;
        const productTotal = recalls.products_by_year.reduce((sum, row) => sum + row.products, 0);
        const eventTotal = recalls.events_by_year.reduce((sum, row) => sum + row.events, 0);
        const classOne = recalls.products_by_class.find(row => row.label === 'Class I').count;
        const box = el('section', 'in-card');
        box.append(el('h2', '', 'FDA classified recalls, fiscal years 2012–2026'));
        const kpis = el('div', 'in-kpis');
        for (const [label, value] of [['Recalled products', productTotal], ['Recall events', eventTotal], ['Class I products', classOne], ['Class I events', recalls.class_i_events_by_year.reduce((sum, row) => sum + row.events, 0)]]) {
          const card = el('div', 'in-kpi');
          card.append(el('span', '', label), el('strong', '', count(value)));
          kpis.append(card);
        }
        box.append(kpis);
        box.append(el('p', 'in-note', 'FDA recalls dashboard. Only recalls classified on or after June 8, 2012. An event is one firm’s recall of one or more products, so the product count is not the event count. Food recalls initiated on or after May 15, 2025 are under the Human Foods Program; the dashboard still labels that type Food/Cosmetics. Fiscal year 2026 was still open when these figures were read on September 28, 2026. The dashboard is updated weekly.'));
        main.append(box);
        main.append(chart('Recalled products by fiscal year', recalls.products_by_year.map(row => [String(row.year), row.products]), `${count(productTotal)} classified recalled products.`, 16));
        main.append(chart('Recall events by fiscal year', recalls.events_by_year.map(row => [String(row.year), row.events]), `${count(eventTotal)} classified recall events.`, 16));
        main.append(chart('Recalled products by type', recalls.products_by_type.map(row => [row.label, row.count]), 'Product rows, not events.'));
        main.append(chart('Recall events by status', recalls.events_by_status.map(row => [row.label, row.count]), 'Terminated, ongoing, and completed are the dashboard’s status values for events.'));
        main.append(chart('Class I recalled products by fiscal year', recalls.class_i_products_by_year.map(row => [String(row.year), row.products]), `Class I is the dashboard’s highest classification: ${count(classOne)} products, against ${count(recalls.products_by_class.find(row => row.label === 'Class II').count)} Class II and ${count(recalls.products_by_class.find(row => row.label === 'Class III').count)} Class III.`, 16));
        const letterTotal = compliance.warning_letters_by_year.reduce((sum, row) => sum + row.letters, 0);
        const action = el('section', 'in-card');
        action.append(el('h2', '', 'FDA warning letters, seizures, and injunctions'));
        const actionKpis = el('div', 'in-kpis');
        for (const [label, value] of [['Warning-letter rows, 2009–2026', letterTotal], ['Injunction rows', compliance.injunctions_by_type.reduce((sum, row) => sum + row.count, 0)], ['Seizure rows', compliance.seizures_by_type.reduce((sum, row) => sum + row.count, 0)], ['Rows in the details table', compliance.table_rows]]) {
          const card = el('div', 'in-kpi');
          card.append(el('span', '', label), el('strong', '', count(value)));
          actionKpis.append(card);
        }
        action.append(actionKpis);
        action.append(el('p', 'in-note', 'Final actions only. The dashboard counts establishments linked to an action, not the number of actions. A case tied to more than one product type is counted once for each type, so the type chart can exceed the yearly warning-letter total. The details table has more rows than the warning-letter, injunction, and seizure charts combined. Import alerts, the usual action for a foreign firm, are not in these charts.'));
        main.append(action);
        main.append(chart('Warning-letter rows by fiscal year', compliance.warning_letters_by_year.map(row => [String(row.year), row.letters]), 'Fiscal years 2009 through 2026. Tobacco accounts for most of the rows in the type chart.', 18));
        main.append(chart('Warning-letter rows by product type', compliance.warning_letters_by_type.map(row => [row.label, row.count]), 'One case can be counted under more than one product type.'));
        main.append(chart('Injunction rows by product type', compliance.injunctions_by_type.map(row => [row.label, row.count]), `Seizure rows, shown here as a total rather than a second chart: ${compliance.seizures_by_type.map(row => `${row.label} ${count(row.count)}`).join(', ')}.`));
        const fy2025 = inspections.by_year.find(row => row.year === 2025);
        const fy2026 = inspections.by_year.find(row => row.year === 2026);
        const inspect = el('section', 'in-card');
        inspect.append(el('h2', '', 'FDA inspections'));
        const inspectKpis = el('div', 'in-kpis');
        for (const [label, value] of [['Domestic inspections, FY 2025', fy2025.domestic], ['Foreign inspections, FY 2025', fy2025.foreign], ['Official action indicated, FY 2025', fy2025.oai], ['Domestic inspections, FY 2026 to date', fy2026.domestic]]) {
          const card = el('div', 'in-kpi');
          card.append(el('span', '', label), el('strong', '', count(value)));
          inspectKpis.append(card);
        }
        inspect.append(inspectKpis);
        inspect.append(el('p', 'in-note', 'The domestic and foreign series count inspections. NAI, VAI, and OAI count final classifications of each project area inside an inspection, so those three can add up to more than the inspection count. State-contract inspections, pre-approval inspections, mammography inspections, and inspections still waiting on a final action are not in this dashboard. Fiscal year 2026 was still open on September 28, 2026.'));
        main.append(inspect);
        main.append(chart('Domestic FDA inspections by fiscal year', inspections.by_year.map(row => [String(row.year), row.domestic]), 'Inspection count, not a classification count.', 18));
        main.append(chart('Foreign FDA inspections by fiscal year', inspections.by_year.map(row => [String(row.year), row.foreign]), 'Inspection count. Foreign inspections fell to 291 in fiscal year 2021.', 18));
        main.append(chart('Official action indicated, by fiscal year', inspections.by_year.map(row => [String(row.year), row.oai]), 'Project-area classifications, not inspections. OAI is the classification that can lead to an enforcement action.', 18));
        main.append(chart('Inspection classifications by product type', inspections.by_type.map(row => [row.label, row.nai + row.vai + row.oai]), 'NAI, VAI, and OAI added together. This is a classification count. Food and cosmetics include the Human Foods Program’s predecessor records under the dashboard’s label.'));
        const program = el('section', 'in-card');
        program.append(el('h2', '', 'Accredited third-party certification bodies'));
        const programKpis = el('div', 'in-kpis');
        for (const [label, value] of [['Recognized accreditation bodies', third.accreditation_bodies], ['Accredited certification bodies', third.certification_bodies], ['Scope accreditations', third.scope_rows]]) {
          const card = el('div', 'in-kpi');
          card.append(el('span', '', label), el('strong', '', count(value)));
          programKpis.append(card);
        }
        program.append(programKpis);
        program.append(el('p', 'in-note', `Recognized accreditation bodies: ${third.bodies.join('; ')}. A certification body can hold more than one scope, so scope accreditations exceed the number of bodies. Jamaica’s accreditation body is recognized and has no certification body listed under it.`));
        main.append(program);
        main.append(chart('Certification scopes', third.scopes.map(row => [row.label, row.count]), 'Rows in the certification-body table, one per body and scope.'));
        const fdaLinks = el('div', 'button-row');
        fdaLinks.append(link('FDA recalls dashboard ↗', recalls.source_url, true));
        fdaLinks.append(link('FDA compliance actions ↗', compliance.source_url, true));
        fdaLinks.append(link('FDA inspections ↗', inspections.source_url, true));
        fdaLinks.append(link('Third-party certification bodies ↗', third.source_url, true));
        fdaLinks.append(link('Firm and supplier search ↗', fda.firm_search_url, true));
        main.append(fdaLinks);
      }
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
