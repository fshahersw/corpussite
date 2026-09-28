/* Research areas published as separate supplements. Each area registers a renderer on
   window.ARCHIVE_AREAS; app.js dispatches unknown views here. Until an area's data layer
   passes validation the page shows the supplement status instead of inventing content. */
(function () {
  const registry = window.ARCHIVE_AREAS = window.ARCHIVE_AREAS || {};
  // Internal identifiers stay behind a "Technical details" disclosure; one accent colour marks a warning state.
  const TECHNICAL_FACT = /\b(id|ids|hash|checksum|sha\d*|md5|guid|uuid|slug|geoid|fips|native id|file id|record id|docket id|entity id|person id)\b/i;
  const WARNING_BADGE = /not verified|unverified|needs review|pending|missing|awaiting|no filing|unresolved|stale/i;
  const AREAS = [
    { view: 'mdls', title: 'MDLs & mass torts', nav: 'MDLs', supplement: 'jpml_mdl_20260919', description: 'Multidistrict litigation as listed in the Judicial Panel on Multidistrict Litigation reports, with transferee courts and judges where the join is exact.' },
    { view: 'settlements', title: 'Settlements', nav: 'Settlements', supplement: 'settlements_20260919', description: 'Settlement references with standardized document types, deadline states computed from published dates, and saved official documents.' },
    { view: 'regulations', title: 'Regulations', nav: 'Regulations', supplement: 'federal_regulations_20260919', description: 'Federal regulations for product, drug, device and consumer-safety research, with amendment history and Federal Register documents kept as separate dated facts.' },
    { view: 'agencies', title: 'Agencies & safety data', nav: 'Agencies', supplement: 'agency_safety_20260919', description: 'Recalls, enforcement actions, approvals and device classifications as published by the agencies, with every date field kept distinct.' },
    { view: 'courts', title: 'Courts & courthouses', nav: 'Courts', supplement: 'court_spine_20260919', description: 'Court registry with identifiers, court marks, rules, forms and instructions linked to the court that published them.' },
    { view: 'statistics', title: 'Court statistics', nav: 'Statistics', supplement: 'federal_court_statistics_20260919', description: 'Official caseload tables and per-judge Civil Justice Reform Act reports as publisher-reported counts for a stated period.' },
    { view: 'counsel', title: 'Firms & attorneys', nav: 'Counsel', supplement: 'mdl_counsel_20260919', description: 'Counsel and parties recorded on major MDL dockets, with partial-coverage notes and no name-only merging.' },
    { view: 'resources', title: 'State legal resources', nav: 'Resources', supplement: 'doj_state_resource_map_20260919', description: 'The Department of Justice state legal resource directory as published, section by section, linked to the source directory.' },
    { view: 'urls', title: 'URL directory', nav: 'URL directory', supplement: 'url_directory_20260919', description: 'Every agency, regulation, court, county, toxicology and DOJ address found in the locally saved discovery lists, de-duplicated and filterable.' },
    { view: 'uscourts', title: 'U.S. Courts pages', nav: 'U.S. Courts pages', supplement: 'uscourts_pages_20260919', description: 'Saved uscourts.gov pages: statistics and reports, judges and judgeships, rules, forms and administration policies.' },
    { view: 'state-proceedings', title: 'State mass torts', nav: 'State mass torts', supplement: 'state_coordinated_proceedings_20260919', description: 'New Jersey Multicounty Litigation and California coordinated proceedings (JCCP) as listed in the saved registries, with counties, judges as printed, and federal MDLs the source itself names.' },
    { view: 'court-documents', title: 'Court documents', nav: 'Court documents', supplement: 'court_document_library_20260919', description: 'Forms, local rules, standing orders, fee schedules and other files downloaded from court websites in August 2026, served from the saved copies.' },
    { view: 'federal-opinions', title: 'Federal court opinions', nav: 'Federal opinions', supplement: 'federal_court_opinions_20260820', description: 'Directory of federal district, bankruptcy and appellate court opinions published in the GovInfo United States Courts Opinions collection, searchable by case name, court, case number, case type and date, each linked to the official record.' },
    { view: 'court-coverage', title: 'Court document coverage', nav: 'Court document coverage', supplement: 'docsupload_coverage_20260927', description: 'Browse the existing document library by its source court. Historical coverage, exact links to saved originals, and unresolved source aliases stay distinct.' },
    { view: 'judge-disclosures', title: 'Judge financial disclosures', nav: 'Disclosures', supplement: 'judge_financial_disclosures_20260919', description: 'Annual financial disclosure filings from a CourtListener snapshot: holdings as described by the filer with value codes only.' },
    { view: 'federal-register', title: 'Federal Register 1994–2026', nav: 'Federal Register', supplement: 'federal_register_history_20260919', description: 'Every Federal Register document listed by the federalregister.gov API from 1994 to mid-2026: rules, proposed rules, notices and presidential documents with the CFR parts, agencies, dockets and RINs each one lists.' },
    { view: 'mdl-cases', title: 'MDL member cases (saved docket sample)', nav: 'MDL cases', supplement: 'mdl_case_inventory_20260919', description: 'Dockets in the saved firm-focused docket sample, linked to an MDL by native docket ids. A sample of what this archive holds, not an MDL\'s member-case list.' },
    { view: 'mdl-appearances', title: 'MDL counsel appearances', nav: 'Appearances', supplement: 'mdl_counsel_appearances_20260919', description: 'Counsel appearances, firms and party counts recorded on MDL-linked dockets in the saved docket sample.' },
    { view: 'mdl-documents', title: 'MDL docket documents', nav: 'MDL documents', supplement: 'mdl_docket_documents_20260919', description: 'Master-docket documents for the MDLs the saved catalog reaches, typed by order kind, linking out to CourtListener.' },
    { view: 'mdl-activity', title: 'MDL docket activity', nav: 'MDL activity', supplement: 'mdl_docket_activity_20260919', description: 'Docket entries on MDL-linked matters in the saved docket release, typed by entry kind.' },
    { view: 'sd-statutes', title: 'South Dakota Codified Laws', nav: 'SD statutes', supplement: 'sd_statutes_20260919', description: 'South Dakota statutes as retrieved into the local registry cache; not verified current.' },
    { view: 'agency-documents', title: 'Agency & science documents', nav: 'Agency documents', supplement: 'agency_science_documents_20260919', description: 'Toxicological profiles, safety reports, recall and investigation files and JPML orders downloaded from official agency and science sites, with searchable text and the saved original.' },
    { view: 'saved-pages', title: 'Saved web pages', nav: 'Saved pages', supplement: 'saved_web_pages_20260919', description: 'Searchable text of pages captured in the August 2026 crawls: Justice Department and federal court pages, Illinois circuit courts, Pennsylvania and Philadelphia courts, D.C. Courts, the Montana Code and the Nevada Legislature.' },
    { view: 'indiana-code', title: 'Indiana Code (2026 edition)', nav: 'Indiana Code', supplement: 'indiana_code_2026_20260919', description: 'Every section of the 2026 Indiana Code from the General Assembly HTML edition saved locally, searchable by text or citation. Not verified against the current code.' },
    { view: 'public-laws', title: 'Public Laws & U.S. Code', nav: 'Public Laws', supplement: 'public_law_uscode_20260919', description: 'Public Laws as enacted and the United States Code sections they touch, from the saved federal publication files.' },
    { view: 'state-codes', title: 'Full-text state codes', nav: 'State codes', description: 'Complete state codes saved in full text, searchable by citation or words. Choose the state in the filters.' },
    { view: 'counsel-directory', title: 'Firms & attorneys', nav: 'Firms & attorneys', supplement: 'counsel_directory_20260919', description: 'Law firms and counsel of record across the saved dockets, grouped by exact normalised firm name and native attorney ids.' },
    { view: 'verdict-reports', title: 'Verdict & settlement reports', nav: 'Verdict reports', supplement: 'verdict_settlement_reports_20260919', description: 'Publisher-reported verdicts and settlements by state, year, practice area and amount. Not verified against court records.' },
    { view: 'cpsc-injury-data', title: 'Injury & incident data', nav: 'Injury data', supplement: 'cpsc_injury_data_20260919', description: 'Consumer product injury surveillance records and incident reports as published.' },
    { view: 'expert-rulings', title: 'Expert challenges', nav: 'Expert challenges', supplement: 'expert_rulings_scan_20260919', description: 'Docket filings about expert admissibility found in the saved dockets, with links to the docket.' },
    { view: 'source-documents', title: 'Downloaded source files', nav: 'Source files', supplement: 'source_directory_documents_20260919', description: 'Settlement agreements and consent judgments, court forms, rules, standing orders, jury instructions and agency guidance downloaded from the source directory, with searchable text and the saved original.' },
    { view: 'citation-guide', title: 'Reporters & citation forms', nav: 'Citation guide', supplement: 'citation_reference_flp_20260920', description: 'Case reporters with their series and years, statute and regulation citation forms by jurisdiction, law journals and standard abbreviations.' },
    { view: 'limitation-periods', title: 'Limitation periods', nav: 'Limitation periods', supplement: 'limitation_periods_20260920', description: 'Filing deadlines by state and claim type from a published summary table, with personal injury, wrongful death and malpractice periods checked against the statute text saved here.' },
    { view: 'citation-index', title: 'Cited authorities', nav: 'Cited authorities', supplement: 'citation_index_20260920', description: 'Cases, statutes, regulations and journal articles cited inside the saved documents, with the documents that cite them.' },
    { view: 'coverage', title: 'Coverage & gaps', nav: 'Coverage', supplement: 'law_tier_20260919', description: 'What is saved for each jurisdiction and document family, what is missing, and which venues need work.' },
  ];

  async function supplementStatus(name, signal) {
    try { return await api(`/api/supplements?name=${encodeURIComponent(name)}`, signal); }
    catch (error) { if (error.status === 404) return null; throw error; }
  }

  function statusCard(area, item) {
    const card = el('section', 'panel supplement-status');
    if (!item) {
      append(card, el('h2', '', 'Not published yet'), el('p', '', `The ${area.title.toLowerCase()} data layer has not been published. This page will show its records once the supplement exists and passes validation.`));
      return card;
    }
    const state = item.ready ? 'Validated' : 'Awaiting validation';
    append(card, el('h2', '', `${state}: ${item.name}`), dataNote(item.qualification || 'No qualification statement was recorded.'));
    const facts = el('dl', 'profile-facts');
    const row = (label, value) => { if (value === null || value === undefined || value === '') return; append(facts, el('dt', '', label), el('dd', '', String(value))); };
    row('Status', item.status);
    row('Validated', item.validated_at ? date(item.validated_at, true) : 'Unknown');
    row('Envelope', item.envelope);
    row('Licence', item.license_ref);
    for (const [key, value] of Object.entries(item.counts || {})) if (typeof value === 'number') row(human(key), count(value));
    card.append(facts);
    if (item.issues?.length) { const list = el('ul', 'supplement-issues'); for (const issue of item.issues) list.append(el('li', '', issue)); card.append(list); }
    if (item.data_files?.length) card.append(el('p', 'coverage-footnote', `${count(item.data_files.length)} data file${item.data_files.length === 1 ? '' : 's'}; ${count(item.data_files.filter(f => f.verified).length)} hash-verified.`));
    return card;
  }

  for (const area of AREAS) {
    registry[area.view] = {
      title: area.title, nav: area.nav, parent: area.view, supplement: area.supplement,
      async render(signal) {
        heading(area.title, area.description, 'Research areas');
        loading(main, 'Checking the data layer…');
        let item = null;
        try { item = area.supplement ? await supplementStatus(area.supplement, signal) : { ready: true, name: null }; } catch (error) { if (error.name === 'AbortError') return; main.removeAttribute('aria-busy'); main.replaceChildren(); heading(area.title, area.description, 'Research areas'); errorState(main, error, () => render()); return; }
        if (signal.aborted) return;
        main.removeAttribute('aria-busy');
        main.replaceChildren();
        heading(area.title, area.description, 'Research areas');
        const renderer = registry[area.view].page;
        if (item && item.ready && typeof renderer === 'function') await renderer(signal, item);
        else main.append(statusCard(area, item));
        announce(`${area.title} opened.`);
      },
    };
  }
  /* ---- State legal resources (DOJ JMD directory as published) ---- */
  registry.resources.page = async function (signal, supplement) {
    const state = (route.params.get('state') || '').toUpperCase();
    const section = route.params.get('section') || '';
    const form = el('form', 'filters resource-filters'); form.setAttribute('aria-label', 'Filter state legal resources');
    const stateField = filterField('State or territory', 'state', 'select', state); const sectionField = filterField('Published section', 'section', 'select', section);
    append(form, stateField.label, sectionField.label, action('Clear', () => navigate('resources')));
    main.append(form);
    const stateList = await api('/api/resources/states', signal); if (signal.aborted) return;
    setOptions(stateField.input, stateList.items.map(s => ({ value: s.usps, label: `${s.label} (${count(s.resource_count)})` })), 'Choose a state', state);
    const sectionList = await api(`/api/resources/sections${state ? '?state=' + encodeURIComponent(state) : ''}`, signal); if (signal.aborted) return;
    setOptions(sectionField.input, sectionList.items.map(s => ({ value: s.value, label: `${s.label} (${count(s.count)})` })), 'All sections', section);
    form.addEventListener('change', () => { const params = { state: stateField.input.value, section: sectionField.input.value }; if (params.state !== state) delete params.section; navigate('resources', params); });
    append(main, el('p', 'page-summary', `${count(stateList.total)} state and territory pages captured from the Department of Justice legal resources directory on ${date(stateList.items[0]?.captured_at)}.`), dataNote(`Section labels and link text are shown as published; each link is a reference, not saved content, and a page's "updated" date is not the date any single link was added.`));
    if (!state) {
      const circuits = await api('/api/resources/circuits', signal); if (signal.aborted) return;
      const grid = el('div', 'collection-grid');
      for (const circuit of circuits.items || []) {
        const card = el('article', 'collection-card');
        append(card, el('h2', '', circuit.circuit_label), el('p', '', (circuit.states || []).map(s => s.label_as_published || s.usps).join(', ') || 'Member states as published'));
        const row = el('div', 'button-row');
        for (const member of circuit.states || []) if (member.usps) row.append(routeLink(member.usps, 'resources', { state: member.usps }, 'button-link'));
        card.append(row); grid.append(card);
      }
      append(main, el('h2', 'section-heading', 'Choose a state, or start from its federal circuit'), grid);
      return;
    }
    const params = new URLSearchParams(route.params); params.set('limit', '50'); params.set('page', Math.max(1, Number(params.get('page')) || 1));
    const data = await api(`/api/resources?${params}`, signal); if (signal.aborted) return;
    if (!data.found) { emptyState(main, 'No page for this state', 'The DOJ directory has no captured page for this selection.', () => navigate('resources')); return; }
    const head = el('section', 'panel resource-state');
    const s = data.state;
    append(head, el('h2', '', `${s.label}: ${count(s.resource_count)} published links in ${count(s.section_count)} sections`),
      el('p', '', `DOJ page updated ${s.page_updated_label || 'unknown'} (publisher footer); captured ${date(s.captured_at, true)}. ${s.circuit ? `Federal circuit as published: ${s.circuit.circuit_label}.` : ''} ${count(s.not_in_source_directory_count)} links are not in the source directory.`),
      append(el('div', 'button-row'), link('Open the DOJ page ↗', s.page_url, true, 'button-link')));
    main.append(head);
    const table = el('div', 'table-panel'); const scroll = el('div', 'table-scroll'); const t = el('table'); const thead = el('thead'); const hr = el('tr');
    for (const h of ['Link as published', 'Section', 'Host', 'Source directory', 'Dates']) hr.append(el('th', '', h)); thead.append(hr); t.append(thead);
    const tbody = el('tbody');
    for (const r of data.items) {
      const tr = el('tr');
      const c1 = el('td'); append(c1, link(r.link_text || r.url, r.url, true, 'document-title'), el('div', 'record-subline', r.url)); tr.append(c1);
      tr.append(el('td', '', [r.section_h2, r.section_h3].filter(Boolean).join(' › ')));
      tr.append(el('td', 'jurisdiction', r.host || ''));
      const c4 = el('td', 'table-badges');
      if (Array.isArray(r.directory_ref_ids) && r.directory_ref_ids.length) for (const id of r.directory_ref_ids.slice(0, 2)) c4.append(routeLink('Listed →', `source/${encodeURIComponent(id)}`, { from: location.hash }, 'badge'));
      else c4.append(el('span', 'badge neutral', 'Not in directory'));
      tr.append(c4);
      tr.append(el('td', '', `Page updated ${r.page_updated_label || 'unknown'}${r.link_status ? ` · ${human(r.link_status)}` : ''}`));
      tbody.append(tr);
    }
    t.append(tbody); scroll.append(t); table.append(scroll);
    append(main, append(el('div', 'results-heading'), el('strong', '', `${count(data.total)} links`), el('small', '', 'As published by DOJ; not a verification of current availability')), table, pagination(data.total, Number(data.page) || 1, Number(data.limit) || 50));
  };
  /* ---- Agencies & safety data (openFDA, FDA.gov exports, local CPSC file) ---- */
  function agencyDialog(title, build) {
    const body = document.querySelector('#record-body'); if (!dialog.open) dialog.showModal(); body.replaceChildren();
    const h = el('h2', '', title); h.id = 'record-title'; body.append(h); build(body); document.querySelector('#close-record').focus();
  }
  function dl(pairs) { const list = el('dl', 'record-meta'); for (const [k, v] of pairs) if (v !== null && v !== undefined && v !== '' && !(Array.isArray(v) && !v.length)) append(list, el('dt', '', k), el('dd', '', Array.isArray(v) ? v.join(', ') : typeof v === 'object' ? JSON.stringify(v) : String(v))); return list; }
  registry.agencies.page = async function (signal, supplement) {
    const p = route.params; const get = k => p.get(k) || '';
    const datasets = await api('/api/agency/datasets', signal); if (signal.aborted) return;
    const status = datasets.status || {};
    append(main, el('p', 'page-summary', `${count(status.records)} agency records across ${count(status.datasets)} datasets, as published.`), dataNote(`${status.qualification ? status.qualification + ' ' : ''}Every publisher date is kept as its own field; a recall or letter is an agency record about a product or firm, not a finding of defect or liability.`));
    const form = el('form', 'filters agency-filters'); form.setAttribute('aria-label', 'Search agency records');
    const q = filterField('Search', 'q', 'search', get('q'), 'Product, reason, firm or subject');
    const ds = filterField('Dataset', 'dataset', 'select', get('dataset'));
    setOptions(ds.input, datasets.items.map(d => ({ value: d.dataset, label: `${d.label} (${count(d.rows)})` })), 'All datasets', get('dataset'));
    const firm = filterField('Firm', 'firm', 'search', get('firm'), 'Firm name contains');
    const cls = filterField('Classification', 'classification', 'select', get('classification'));
    setOptions(cls.input, ['Class I', 'Class II', 'Class III'].map(v => ({ value: v, label: v })), 'Any class', get('classification'));
    const current = datasets.items.find(d => d.dataset === get('dataset'));
    const dtype = filterField('Date type', 'date_type', 'select', get('date_type'));
    setOptions(dtype.input, (current ? current.date_types || [] : [...new Set(datasets.items.flatMap(d => d.date_types || []))]).map(v => ({ value: v, label: human(v) })), 'Choose a date field', get('date_type'));
    const dfrom = filterField('From', 'dfrom', 'date', get('dfrom')); const dto = filterField('To', 'dto', 'date', get('dto'));
    append(form, q.label, ds.label, firm.label, cls.label, dtype.label, dfrom.label, dto.label, action('Search', () => {}, 'button-primary'), action('Clear', () => navigate('agencies')));
    form.addEventListener('submit', event => { event.preventDefault(); const params = Object.fromEntries(new FormData(form)); navigate('agencies', params); });
    form.querySelector('.button-primary').addEventListener('click', () => form.requestSubmit());
    ds.input.addEventListener('change', () => { const params = Object.fromEntries(new FormData(form)); delete params.date_type; navigate('agencies', params); });
    main.append(form);
    if (![...p.keys()].some(k => ['q', 'dataset', 'firm', 'classification', 'product_code', 'date_type'].includes(k))) {
      const grid = el('div', 'collection-grid');
      for (const d of datasets.items) {
        const card = el('article', 'collection-card');
        append(card, el('h2', '', d.label), el('p', '', `${d.publisher}. ${count(d.rows)} records. Publisher data as of ${d.source_as_of ? date(d.source_as_of) : 'unknown'}; captured ${date(d.captured_at, true)}.`));
        const row = el('div', 'button-row'); row.append(routeLink('Browse →', 'agencies', { dataset: d.dataset }, 'button-link'));
        for (const file of (d.original_files || []).slice(0, 2)) if (file.file_id) row.append(link(`Original file ↗ (${Math.round((file.bytes || 0) / 1048576)} MB)`, `/agency-files/${encodeURIComponent(file.file_id)}`, false, 'button-link'));
        card.append(row); grid.append(card);
      }
      append(main, el('h2', 'section-heading', 'Datasets'), grid);
      return;
    }
    const params = new URLSearchParams(p); params.set('limit', '25'); params.set('page', Math.max(1, Number(params.get('page')) || 1));
    const data = await api(`/api/agency/search?${params}`, signal); if (signal.aborted) return;
    if (!data.available) { main.append(el('p', 'registry-empty', data.reason || 'Agency data is not available.')); return; }
    if (!data.results.length) { emptyState(main, 'No matching agency records', 'Try a broader search, another dataset or remove the date filter.', () => navigate('agencies')); return; }
    const table = el('div', 'table-panel'); const scroll = el('div', 'table-scroll'); const t = el('table'); const thead = el('thead'); const hr = el('tr');
    for (const h of ['Record', 'Firm', 'Class / status', 'Dates as published']) hr.append(el('th', '', h)); thead.append(hr); t.append(thead);
    const tbody = el('tbody');
    for (const r of data.results) {
      const tr = el('tr'); const c1 = el('td');
      const open = action(r.title || r.native_id, async event => { try { const full = await api(`/api/agency/record?dataset=${encodeURIComponent(r.dataset)}&id=${encodeURIComponent(r.id)}`); agencyDialog(full.title || r.title || r.native_id, body => { append(body, el('p', 'reader-context', `${datasets.items.find(d => d.dataset === r.dataset)?.label || human(r.dataset)} · ${r.native_id}${full.publisher ? ' · ' + full.publisher : ''}`)); if (full.dates) { body.append(el('h3', '', 'Dates as published')); body.append(dl(Object.entries(full.dates).map(([k, v]) => [human(k), v ? date(v) : 'Not recorded']))); } body.append(el('h3', '', 'Publisher fields')); body.append(dl(Object.entries(full.fields || {}).filter(([k, v]) => typeof v !== 'object' || Array.isArray(v)).map(([k, v]) => [human(k), v]).slice(0, 80))); const row = el('div', 'button-row'); for (const l of (Array.isArray(full.links) ? full.links : [])) if (l && l.url) row.append(link((l.label || 'Publisher link') + ' ↗', l.url, true)); if (full.original_file && full.original_file.file_id) row.append(link('Original bulk file ↗', `/agency-files/${encodeURIComponent(full.original_file.file_id)}`)); if (row.children.length) body.append(row); body.append(dataNote(full.qualification || 'Agency record as published; dates are the publisher\'s own fields.')); }); } catch (error) { announce('Could not open the agency record.'); } }, 'document-title');
      append(c1, open, el('div', 'record-subline', `${human(r.dataset)} · ${r.native_id}${r.snippet ? ' · ' + r.snippet : ''}`)); tr.append(c1);
      const c2 = el('td'); if (r.firm) c2.append(routeLink(r.firm, 'agencies', { firm: r.firm, dataset: get('dataset') }, 'button-link')); tr.append(c2);
      tr.append(el('td', '', [r.classification, r.status].filter(Boolean).join(' · ')));
      const c4 = el('td', 'availability'); for (const [k, v] of Object.entries(r.dates || {})) if (v) c4.append(el('span', '', `${human(k)}: ${date(v)}`)); tr.append(c4);
      tbody.append(tr);
    }
    t.append(tbody); scroll.append(t); table.append(scroll);
    append(main, append(el('div', 'results-heading'), el('strong', '', `${count(data.total)} agency records`), el('small', '', 'Publisher-reported records; not legal or medical conclusions')), table, pagination(data.total, Number(data.page) || 1, Number(data.limit) || 25));
  };
  /* ---- Coverage & gaps (jurisdiction matrix, official-tier labels, topic candidates, venues) ---- */
  const FAMILIES = [['statutes', 'Statutes'], ['constitution', 'Constitution'], ['regulations', 'Regulations'], ['court_rules', 'Court rules']];
  function familyCell(cell) {
    if (!cell) return el('td', '', '—');
    const official = cell.official_capture || {}; const parts = [];
    if (official.total) parts.push(`${count(official.total)} official (${count(official.body)} bodies, ${count(official.unreviewed)} unreviewed)`);
    if (cell.imported_collection) parts.push(`${count(cell.imported_collection)} imported`);
    if (cell.third_party_snapshot) parts.push(`${count(cell.third_party_snapshot)} third-party rows`);
    if (cell.pending_publication) parts.push(`${count(cell.pending_publication)} pending`);
    const td = el('td', 'coverage-cell'); if (!parts.length) { td.append(el('span', 'badge amber', 'None saved')); return td; }
    for (const part of parts) td.append(el('div', '', part)); return td;
  }
  registry.coverage.page = async function (signal, supplement) {
    const state = (route.params.get('state') || '').toUpperCase(); const topic = route.params.get('topic') || '';
    const form = el('form', 'filters county-filters'); form.setAttribute('aria-label', 'Choose a jurisdiction or topic');
    const stateField = filterField('Jurisdiction', 'state', 'select', state); const topicField = filterField('Mass-tort topic', 'topic', 'select', topic);
    append(form, stateField.label, topicField.label, action('Clear', () => navigate('coverage'))); main.append(form);
    const matrix = await api('/api/coverage/matrix', signal); if (signal.aborted) return;
    setOptions(stateField.input, matrix.rows.map(r => ({ value: r.abbr, label: r.name })), 'All jurisdictions', state);
    let catalogSource;
    try { catalogSource = await api(`/api/coverage/topics?limit=1${state ? '&state=' + state : ''}`, signal); }
    catch (error) { if (error.code !== 'publication_pending') throw error; catalogSource = { available: false, topic_catalog: {} }; }
    if (signal.aborted) return;
    const topicsPending = catalogSource.available === false;
    setOptions(topicField.input, Object.entries(catalogSource.topic_catalog || {}).map(([k, v]) => ({ value: k, label: `${v.label} (${count(v.provisions)})` })), 'Choose a topic', topic);
    topicField.input.disabled = topicsPending;
    if (topicsPending) { const note = el('p', 'county-section-note', 'Topic search is not yet published. State coverage and county information remain available below.'); note.setAttribute('role', 'status'); main.append(note); }
    form.addEventListener('change', () => navigate('coverage', { state: stateField.input.value, topic: topicsPending ? '' : topicField.input.value }));
    main.append(dataNote(`${matrix.qualification} Labels are automated structural triage of already-saved pages.${topicsPending ? '' : ` Topic lists are ${catalogSource.label}.`}`));
    if ((!topic || topicsPending) && matrix.trellis_progress) {
      const selected=state?matrix.rows.find(r=>r.abbr===state):null;
      const progress=selected?{saved_county_urls:selected.trellis_county_profiles?.saved||0,observed_county_urls:selected.trellis_county_profiles?.observed||0,remaining_county_urls:selected.trellis_county_profiles?.unsaved||0}:matrix.trellis_progress, panel=el('section','panel');
      append(panel,el('h2','',`County profile backfill${selected?' · '+selected.name:''}`),el('p','section-intro',`${count(progress.saved_county_urls)} of ${count(progress.observed_county_urls)} discovered county-profile URLs saved · ${count(progress.remaining_county_urls)} gaps`),
        el('p','coverage-footnote',`Updated ${date(matrix.trellis_refreshed_at)}. These are profile snapshots, not complete case files or coverage of every U.S. county.`),
        routeLink('Browse refreshed county details →','counties',{availability:'trellis_details',...(selected?{state:selected.name}:{})},'button'));
      main.append(panel);
    }
    if (topic && !topicsPending) {
      const params = new URLSearchParams(route.params); params.set('limit', '50'); params.set('page', Math.max(1, Number(params.get('page')) || 1));
      const data = await api(`/api/coverage/topics?${params}`, signal); if (signal.aborted) return;
      const cat = (data.topic_catalog || {})[topic] || {};
      append(main, el('h2', 'section-heading', `${cat.label || human(topic)}: ${count(data.total)} candidate provisions${state ? ' in ' + state : ''}`), el('p', 'coverage-footnote', `Search phrases used: ${Object.values(cat.queries || {}).join(' | ')}`));
      if (!data.items.length) { emptyState(main, 'No candidates found', 'The saved statutes and rules for this selection contain no matching phrases.', () => navigate('coverage', { state })); return; }
      const table = el('div', 'table-panel'); const scroll = el('div', 'table-scroll'); const t = el('table'); const thead = el('thead'); const hr = el('tr');
      for (const h of ['Provision', 'State', 'Family', 'Source', 'Match']) hr.append(el('th', '', h)); thead.append(hr); t.append(thead); const tbody = el('tbody');
      for (const r of data.items) {
        const tr = el('tr'); const c1 = el('td');
        const opener = r.record_id ? action(r.title || r.citation, event => openRecord({ id: r.record_id }, event.currentTarget), 'document-title') : (r.row_id ? action(r.title || r.citation, event => openRecord({ id: r.row_id }, event.currentTarget), 'document-title') : el('span', 'document-title', r.title || r.citation));
        append(c1, opener, el('div', 'record-subline', `${r.citation || ''}${r.publisher_status ? ' · ' + human(r.publisher_status) : ''}`)); tr.append(c1);
        tr.append(el('td', 'jurisdiction', r.state)); tr.append(el('td', '', human(r.family)));
        const c4 = el('td', ''); c4.append(el('div', '', human(r.source_tier))); if (r.temporal?.source_as_of) c4.append(el('div', 'record-subline', `Source as of ${date(r.temporal.source_as_of)}`)); tr.append(c4);
        tr.append(el('td', '', r.match === 'title' ? 'Title' : 'Text')); tbody.append(tr);
      }
      t.append(tbody); scroll.append(t); table.append(scroll); append(main, table, pagination(data.total, Number(data.page) || 1, Number(data.limit) || 50)); return;
    }
    if (state) {
      const s = await api(`/api/coverage/state?state=${encodeURIComponent(state)}`, signal); if (signal.aborted) return;
      const head = el('section', 'panel'); append(head, el('h2', '', `${s.name}: what is saved and what is missing`));
      if (s.gaps?.length) { const list = el('ul', 'supplement-issues'); for (const g of s.gaps) list.append(el('li', '', human(g))); append(head, el('h3', '', 'Recorded gaps'), list); } else head.append(el('p', '', 'No family-level gap recorded.'));
      const facts = el('dl', 'profile-facts');
      const row = (k, v) => { if (v !== null && v !== undefined && v !== '') append(facts, el('dt', '', k), el('dd', '', String(v))); };
      const county = s.county_layer || {}; row('Counties', count(county.counties_total)); row('With a saved court profile', count(county.with_saved_trellis_profile)); row('With saved county site page', count(county.with_saved_site_page)); row('With a published local resource', count(county.with_published_local_resource));
      row('Unsaved County court profiles', count(s.trellis_unsaved_profiles?.count)); row('Source-directory references', count(s.source_directory?.references)); row('Unreviewed official records classified', count(Object.values(s.reviewed_labels?.by_class || {}).reduce((a, b) => a + b, 0)));
      if(s.trellis_refreshed_at)row('County profile coverage refreshed',date(s.trellis_refreshed_at));
      head.append(facts); main.append(head);
      const t = el('table'); const thead = el('thead'); const hr = el('tr'); for (const h of ['Family', 'Saved coverage']) hr.append(el('th', '', h)); thead.append(hr); t.append(thead); const tbody = el('tbody');
      for (const [key, label] of FAMILIES) { const tr = el('tr'); tr.append(el('td', '', label)); tr.append(familyCell(s.families?.[key])); tbody.append(tr); }
      t.append(tbody); const panel = el('div', 'table-panel'); const scroll = el('div', 'table-scroll'); scroll.append(t); panel.append(scroll); main.append(panel);
      if (!topicsPending) { const topics = el('div', 'button-row'); for (const [k, v] of Object.entries(s.topic_counts || {})) topics.append(routeLink(`${human(k)} (${count(v)})`, 'coverage', { state, topic: k }, 'button'));
      append(main, el('h2', 'section-heading', 'Topic candidates in saved law'), topics); }
      if (s.venues?.length) {
        append(main, el('h2', 'section-heading', 'Mass-tort venues in this state'));
        const grid = el('div', 'collection-grid');
        for (const v of s.venues) { const card = el('article', 'collection-card'); append(card, el('h2', '', v.venue), el('p', '', `${human(v.tier)} venue · county-linked records: ${count(v.county_linked_total)} · missing: ${(v.missing_county_linked || []).map(human).join(', ') || 'none'} · ${count(v.saved_documents_on_court_hosts_not_county_linked)} saved court-host documents not yet linked to the county`)); card.append(routeLink('Open county →', `county/${v.fips}`, {}, 'button-link')); grid.append(card); }
        main.append(grid);
      }
      return;
    }
    const gaps = matrix.gaps || {};
    const summary = el('div', 'summary-cards');
    for (const [key, label] of [['statutes_none_any_source', 'No statutes from any source'], ['regulations_none_any_source', 'No regulations from any source'], ['court_rules_none_any_source', 'No court rules from any source'], ['statewide_forms_none', 'No statewide forms']]) { const chip = el('div', 'summary-chip'); append(chip, el('strong', '', count((gaps[key] || []).length)), el('span', '', `${label}: ${(gaps[key] || []).join(', ') || 'none'}`)); summary.append(chip); }
    main.append(summary);
    const table = el('div', 'table-panel'); const scroll = el('div', 'table-scroll'); const t = el('table'); const thead = el('thead'); const hr = el('tr');
    for (const h of ['Jurisdiction', ...FAMILIES.map(f => f[1]), 'Counties', 'Gaps']) hr.append(el('th', '', h)); thead.append(hr); t.append(thead); const tbody = el('tbody');
    for (const r of matrix.rows) {
      const tr = el('tr'); const c1 = el('td'); c1.append(routeLink(r.name, 'coverage', { state: r.abbr }, 'document-title')); if (r.jurisdiction_kind !== 'state') c1.append(el('div', 'record-subline', human(r.jurisdiction_kind))); tr.append(c1);
      for (const [key] of FAMILIES) tr.append(familyCell(r.families?.[key]));
      const cl = r.county_layer || {}; tr.append(el('td', '', cl.counties_total ? `${count(cl.with_saved_trellis_profile)} / ${count(cl.counties_total)} profiles` : '—'));
      const c6 = el('td', 'table-badges'); for (const g of (r.gaps || []).slice(0, 4)) c6.append(el('span', 'badge amber wrap', human(g))); if ((r.gaps || []).length > 4) c6.append(el('span', 'badge neutral', `+${r.gaps.length - 4}`)); tr.append(c6);
      tbody.append(tr);
    }
    t.append(tbody); scroll.append(t); table.append(scroll);
    append(main, append(el('div', 'results-heading'), el('strong', '', `${count(matrix.rows.length)} jurisdictions`), el('small', '', 'Counts are saved records or exported rows, never unique laws')), table);
  };
  /* ---- Regulations (CFR slice: official GPO text, publisher text, eCFR history, Federal Register documents) ---- */
  function temporalLine(t) { if (!t) return ''; const parts = []; if (t.source_as_of) parts.push(`source as of ${date(t.source_as_of)}`); if (t.published_at) parts.push(`published ${date(t.published_at)}`); if (t.captured_at) parts.push(`captured ${date(t.captured_at, true)}`); return parts.join(' · '); }
  async function openRegulation(citation) {
    let row; try { row = await api(`/api/regulation?citation=${encodeURIComponent(citation)}`); } catch (error) { announce('Could not open the regulation section.'); return; }
    agencyDialog(`${row.citation} ${row.heading || ''}`, body => {
      append(body, el('p', 'reader-context', `Title ${row.title} · Part ${row.part}${row.subpart ? ' · Subpart ' + row.subpart : ''}${row.reserved ? ' · Reserved' : ''}`));
      const facts = [['Latest eCFR amendment date', row.latest_amendment_date ? `${date(row.latest_amendment_date)} (${row.latest_amendment_date_basis || 'eCFR version metadata'})` : 'Not recorded'], ['Latest issue date', row.latest_issue_date ? date(row.latest_issue_date) : 'Not recorded'], ['Versions recorded by eCFR', row.versions_count], ['Text sources available', (row.text_sources_available || []).map(human).join(', ')]];
      body.append(dl(facts));
      const texts = Array.isArray(row.texts) ? row.texts : Object.entries(row.sources || row.texts || {}).map(([source, src]) => ({ source, ...(src || {}) }));
      for (const src of texts) {
        const heading = el('div', 'record-text-heading'); append(heading, el('h3', '', src.label || (src.source === 'gpo_ecfr_xml' ? 'Official text (GPO eCFR XML, local copy)' : src.source === 'open_us_law' ? 'Publisher text (Open US Law snapshot, CC BY 4.0)' : human(src.source))), el('small', '', `${temporalLine(src.temporal)}${src.text_hash_verified ? ' · hash verified' : ''}`));
        body.append(heading);
        if (src.text) body.append(el('pre', 'record-text', src.text)); else body.append(el('p', 'quiet-empty', src.unavailable_reason || 'Text not available from this source.'));
        if (src.amendment_marker) body.append(el('p', 'coverage-footnote', `Volume amendment marker: ${src.amendment_marker.raw || src.amendment_marker} (currency of the printed volume, not a per-section date).`));
      }
      if (row.reconciliation) body.append(el('p', 'coverage-footnote', `Text reconciliation between sources: ${human(row.reconciliation.category)}. Neither source is chosen as correct.`));
      if (Array.isArray(row.amendment_citations_as_printed) && row.amendment_citations_as_printed.length) { body.append(el('h3', '', 'Amendment note as printed')); for (const c of row.amendment_citations_as_printed) body.append(el('p', 'quality-note', c)); }
      if (Array.isArray(row.history) && row.history.length) { body.append(el('h3', '', 'eCFR version history')); const list = el('ul', 'career-timeline'); for (const h of row.history) list.append(el('li', '', `${h.ecfr_amendment_date ? 'Amended ' + date(h.ecfr_amendment_date) : 'Amendment date not recorded'}${h.issue_date ? ' · issued ' + date(h.issue_date) : ''}${h.substantive === false ? ' · non-substantive' : ''}${h.removed ? ' · removed' : ''}`)); body.append(list); }
      const related = row.related_fr_documents; const items = Array.isArray(related) ? related : (related && Array.isArray(related.items) ? related.items : []);
      if (items.length) { body.append(el('h3', '', `Federal Register documents (${count(items.length)}${related && related.truncated ? ', truncated' : ''})`)); const list = el('div', 'profile-documents'); for (const d of items) { const card = el('article', 'profile-document'); append(card, el('h3', '', `${d.citation || d.document_number || d.id}: ${d.title || ''}`), el('p', '', `${d.type || ''}${d.publication_date ? ' · published ' + date(d.publication_date) : ''}${d.effective_on_publisher_extracted ? ' · publisher-extracted effective date ' + date(d.effective_on_publisher_extracted) + ' (unverified)' : ''}${Array.isArray(d.relations) ? ' · ' + d.relations.map(human).join(', ') : ''}`)); const rowEl = el('div', 'button-row'); if (d.html_url) rowEl.append(link('Federal Register ↗', d.html_url, true)); rowEl.append(action('Details', () => openFrDocument(d.id || d.document_number), 'button-link')); card.append(rowEl); list.append(card); } body.append(list); }
      if (row.fr_history && row.fr_history.total) { const h = row.fr_history; body.append(el('h3', '', `Federal Register documents citing ${row.title} CFR part ${row.part}, ${String(h.first_date || '').slice(0, 4)}–${String(h.last_date || '').slice(0, 4)} (${count(h.total)})`)); body.append(el('p', 'record-subline', (h.by_type || []).map(t => `${t.value} ${count(t.count)}`).join(' · '))); const list = el('ul', 'career-timeline'); for (const d of (h.results || []).slice(0, 8)) list.append(el('li', '', `${d.subtitle} — ${d.title}`)); body.append(list); const all = el('a', 'button-link', 'Open the full rule history for this part →'); all.href = h.link; body.append(all); body.append(el('p', 'coverage-footnote', 'A document that cites a part may propose, amend, correct or only discuss it. Index collected 2026-08-20.')); }
      const rowEl = el('div', 'button-row'); if (row.ecfr_url) rowEl.append(link('Current eCFR ↗', row.ecfr_url, true)); body.append(rowEl);
      body.append(el('p', 'coverage-footnote', 'Amendment date, Federal Register publication date, publisher-extracted effective date and snapshot date are separate facts; none is inferred from another. eCFR is authoritative but unofficial; the official edition is the annual CFR.'));
    });
  }
  async function openFrDocument(id) {
    let d; try { d = await api(`/api/regulations/document?id=${encodeURIComponent(id)}`); } catch (error) { announce('Could not open the Federal Register document.'); return; }
    agencyDialog(`${d.citation || d.document_number}: ${d.title || ''}`, body => {
      body.append(dl([['Type', d.type], ['Action', d.action], ['Publication date', d.publication_date ? `${date(d.publication_date)} (${d.publication_date_basis || 'publisher field'})` : null], ['Publisher-extracted effective date', d.effective_on_publisher_extracted ? `${date(d.effective_on_publisher_extracted)} (${d.effective_on_basis || 'unverified'})` : 'Not recorded'], ['Signing date', d.signing_date], ['Comments close', d.comments_close_on], ['Agencies', (d.agency_slugs || []).map(human)], ['CFR parts', (d.cfr_part_ids || []).map(x => x.replace('cfr:', '').replace(':', ' CFR '))], ['Docket ids', d.docket_ids], ['RINs', d.regulation_id_numbers || d.rins]]));
      const rowEl = el('div', 'button-row'); if (d.html_url) rowEl.append(link('Federal Register ↗', d.html_url, true)); if (d.pdf_url) rowEl.append(link('PDF ↗', d.pdf_url, true)); body.append(rowEl);
      body.append(el('p', 'coverage-footnote', temporalLine(d.temporal)));
    });
  }
  registry.regulations.page = async function (signal, supplement) {
    const p = route.params; const get = k => p.get(k) || '';
    const info = await api('/api/regulations/info', signal); if (signal.aborted) return;
    append(main, el('p', 'page-summary', `${count(info.sections_indexed || info.counts?.sections_indexed)} CFR sections indexed across ${count(info.titles || info.counts?.titles)} titles; ${count(info.sections_in_slice || info.counts?.sections_in_slice)} in the mass-tort slice (Titles 16, 21, 40, 49).`), dataNote('Official GPO text is local for Title 21; publisher text comes from the Open US Law snapshot. Amendment, publication, effective and snapshot dates are kept separate.'));
    const form = el('form', 'filters agency-filters'); form.setAttribute('aria-label', 'Search regulations');
    const q = filterField('Search', 'q', 'search', get('q'), 'Words in headings, text or Federal Register titles');
    const titleField = filterField('Title', 'title', 'select', get('title')); const partField = filterField('Part', 'part', 'search', get('part'), 'e.g. 314');
    const agencyField = filterField('Agency', 'agency', 'select', get('agency')); const rtype = filterField('Record type', 'record_type', 'select', get('record_type'));
    setOptions(rtype.input, [{ value: 'section', label: 'CFR sections' }, { value: 'fr_document', label: 'Federal Register documents' }], 'Sections and documents', get('record_type'));
    const dtype = filterField('Date type', 'date_type', 'select', get('date_type')); const dfrom = filterField('From', 'dfrom', 'date', get('dfrom')); const dto = filterField('To', 'dto', 'date', get('dto'));
    append(form, q.label, titleField.label, partField.label, agencyField.label, rtype.label, dtype.label, dfrom.label, dto.label, action('Search', () => {}, 'button-primary'), action('Clear', () => navigate('regulations')));
    form.addEventListener('submit', event => { event.preventDefault(); navigate('regulations', Object.fromEntries(new FormData(form))); }); form.querySelector('.button-primary').addEventListener('click', () => form.requestSubmit());
    main.append(form);
    const [titles, agencies] = await Promise.all([api('/api/regulations/titles', signal), api('/api/regulations/agencies?slice_only=1', signal)]); if (signal.aborted) return;
    setOptions(titleField.input, (titles.titles || []).map(t => ({ value: t.title, label: `Title ${t.title} — ${t.name}` })), 'All titles', get('title'));
    setOptions(agencyField.input, (agencies.agencies || []).map(a => ({ value: a.slug, label: a.display_name || a.name })), 'Any agency', get('agency'));
    // info().date_types is a map of date type -> basis sentence; accept a plain list too.
    const dateTypes = Array.isArray(info.date_types) ? info.date_types.map(v => (typeof v === 'string' ? { value: v, label: human(v) } : v)) : Object.keys(info.date_types || {}).map(v => ({ value: v, label: human(v) }));
    setOptions(dtype.input, dateTypes, 'Choose a date field', get('date_type'));
    const searching = ['q', 'agency', 'record_type', 'date_type', 'dfrom', 'dto'].some(k => get(k));
    if (searching) {
      const params = new URLSearchParams(p); params.set('limit', '25'); params.set('page', Math.max(1, Number(params.get('page')) || 1));
      const data = await api(`/api/regulations/search?${params}`, signal); if (signal.aborted) return;
      if (!data.available) { main.append(el('p', 'registry-empty', data.reason || 'Regulation data is not available.')); return; }
      if (!data.results.length) { emptyState(main, 'No matching regulations', 'Try fewer words, another title or remove the date filter.', () => navigate('regulations')); return; }
      const table = el('div', 'table-panel'); const scroll = el('div', 'table-scroll'); const t = el('table'); const thead = el('thead'); const hr = el('tr'); for (const h of ['Provision or document', 'Type', 'Dates as published']) hr.append(el('th', '', h)); thead.append(hr); t.append(thead); const tbody = el('tbody');
      for (const r of data.results) {
        const tr = el('tr'); const c1 = el('td');
        const isDoc = r.record_type === 'fr_document';
        c1.append(action(isDoc ? `${r.citation || r.document_number}: ${r.title || ''}` : `${r.citation} ${r.heading || ''}`, () => isDoc ? openFrDocument(r.id) : openRegulation(r.id), 'document-title'));
        c1.append(el('div', 'record-subline', isDoc ? `${r.type || ''} · ${(r.cfr_part_ids || []).map(x => x.replace('cfr:', '').replace(':', ' CFR ')).join(', ')}` : `Part ${r.part}${r.subpart ? ' · Subpart ' + r.subpart : ''} · ${(r.text_sources_available || []).map(human).join(', ')}`)); tr.append(c1);
        tr.append(el('td', '', isDoc ? 'Federal Register' : 'CFR section'));
        const c3 = el('td', 'availability'); if (isDoc) { if (r.publication_date) c3.append(el('span', '', `Published ${date(r.publication_date)}`)); if (r.effective_on_publisher_extracted) c3.append(el('span', '', `Publisher effective ${date(r.effective_on_publisher_extracted)}`)); } else { if (r.latest_amendment_date) c3.append(el('span', '', `Amended ${date(r.latest_amendment_date)}`)); if (r.temporal?.source_as_of) c3.append(el('span', '', `Source as of ${date(r.temporal.source_as_of)}`)); } tr.append(c3);
        tbody.append(tr);
      }
      t.append(tbody); scroll.append(t); table.append(scroll);
      append(main, append(el('div', 'results-heading'), el('strong', '', `${count(data.total)} results`), el('small', '', 'Not relevance-ranked; ordered by citation and publication date')), table, pagination(data.total, Number(data.page) || 1, Number(data.limit) || 25)); return;
    }
    if (get('part')) {
      const params = new URLSearchParams({ part: get('part') }); if (get('title')) params.set('title', get('title'));
      const data = await api(`/api/regulations/sections?${params}`, signal); if (signal.aborted) return;
      if (!data.found) { emptyState(main, 'Part not found', 'Choose a title and a part number from the CFR slice.', () => navigate('regulations')); return; }
      const part = data.part; const head = el('section', 'panel'); append(head, el('h2', '', `${part.title} CFR Part ${part.part}: ${part.heading || ''}`), el('p', '', `${part.chapter_name || ''}${part.subchapter_name ? ' · ' + part.subchapter_name : ''}. ${count(part.sections_in_ecfr_structure)} sections in the eCFR structure; ${count(part.sections_with_local_official_text)} with local official text; ${count(part.publisher_index_rows)} publisher rows.`));
      if (part.authority_note_as_printed) head.append(el('p', 'quality-note', `Authority: ${part.authority_note_as_printed}`)); if (part.source_note_as_printed) head.append(el('p', 'quality-note', `Source: ${part.source_note_as_printed}`));
      if (part.gpo_amendment_marker) head.append(el('p', 'coverage-footnote', `GPO volume ${part.gpo_volume || ''} amendment marker: ${part.gpo_amendment_marker.raw} (currency of the printed volume, not a per-section date).`));
      main.append(head);
      const list = data.sections || data.items || data.results || [];
      const table = el('div', 'table-panel'); const scroll = el('div', 'table-scroll'); const t = el('table'); const thead = el('thead'); const hr = el('tr'); for (const h of ['Section', 'Text sources', 'Latest amendment (eCFR)']) hr.append(el('th', '', h)); thead.append(hr); t.append(thead); const tbody = el('tbody');
      for (const s of list) { const tr = el('tr'); const c1 = el('td'); c1.append(action(`${s.citation || s.section} ${s.heading || ''}`, () => openRegulation(s.id || s.citation), 'document-title')); if (s.reserved) c1.append(el('div', 'record-subline', 'Reserved')); tr.append(c1); tr.append(el('td', '', (s.text_sources_available || []).map(human).join(', ') || 'Metadata only')); tr.append(el('td', '', s.latest_amendment_date ? date(s.latest_amendment_date) : '—')); tbody.append(tr); }
      t.append(tbody); scroll.append(t); table.append(scroll); append(main, append(el('div', 'results-heading'), el('strong', '', `${count(list.length)} sections`), el('small', '', data.as_of?.caveat || 'Section list from the eCFR structure')), table); return;
    }
    if (get('title')) {
      const data = await api(`/api/regulations/parts?title=${encodeURIComponent(get('title'))}`, signal); if (signal.aborted) return;
      const t0 = data.title || {}; main.append(el('h2', 'section-heading', `Title ${t0.title} — ${t0.name}: ${count(t0.parts_in_slice)} parts in the slice of ${count(t0.parts_total)}`));
      const grid = el('div', 'collection-grid');
      for (const part of data.parts || []) { const card = el('article', 'collection-card'); append(card, el('h2', '', `Part ${part.part}`), el('p', '', `${part.heading || ''} · ${count(part.sections_in_ecfr_structure)} sections${part.official_text_local ? ' · official text local' : ''}`)); card.append(routeLink('Open part →', 'regulations', { title: t0.title, part: `${t0.title}:${part.part}` }, 'button-link')); grid.append(card); }
      main.append(grid); return;
    }
    const grid = el('div', 'collection-grid');
    for (const t of titles.titles || []) { const card = el('article', 'collection-card'); append(card, el('h2', '', `Title ${t.title} — ${t.name}`), el('p', '', `${count(t.parts_in_slice)} slice parts of ${count(t.parts_total)}; ${count(t.sections_in_slice)} sections. ${t.official_text_local ? 'Official GPO text local.' : 'Publisher text only.'} eCFR currency ${t.ecfr?.up_to_date_as_of ? date(t.ecfr.up_to_date_as_of) : 'unknown'}.`)); card.append(routeLink('Browse parts →', 'regulations', { title: t.title }, 'button-link')); grid.append(card); }
    append(main, el('h2', 'section-heading', 'CFR titles in the slice'), grid);
    const ag = el('div', 'button-row'); for (const a of agencies.agencies || []) ag.append(routeLink(a.short_name || a.display_name || a.name, 'regulations', { agency: a.slug }, 'button')); append(main, el('h2', 'section-heading', 'Agencies with slice parts'), ag);
  };
  /* ---- MDLs & mass torts (JPML pending-MDL reports) ---- */
  function mdlRow(m) {
    const tr = el('tr'); const c1 = el('td');
    c1.append(routeLink(`MDL ${m.mdl_number}: ${m.title}`, `mdl/${m.mdl_number}`, { from: location.hash }, 'document-title'));
    c1.append(el('div', 'record-subline', `${m.litigation_type || 'Type not printed'} · ${m.master_docket || ''}${m.has_local_collection ? ' · local collection' : ''}`)); tr.append(c1);
    const c2 = el('td'); append(c2, el('div', '', m.court_name || m.cl_court_id || ''), el('div', 'record-subline', m.circuit || '')); tr.append(c2);
    const c3 = el('td'); if (m.judge_resolved && m.judge_entity_id) c3.append(routeLink(m.judge_name_as_printed, `judge/${encodeURIComponent(m.judge_entity_id)}`, { from: location.hash }, 'button-link')); else c3.append(el('span', '', m.judge_name_as_printed || 'Not printed')); c3.append(el('div', 'record-subline', m.judge_resolved ? `Profile match: ${human(m.judge_match_kind || 'exact')}` : 'No profile match; name as printed')); tr.append(c3);
    const c4 = el('td', 'small-number'); append(c4, el('div', '', `${count(m.actions_pending)} pending`), el('div', 'record-subline', `${count(m.total_actions)} total · ${m.date_transferred ? 'transferred ' + date(m.date_transferred) : ''}`)); tr.append(c4);
    return tr;
  }
  registry.mdls.page = async function (signal, supplement) {
    const p = route.params; const get = k => p.get(k) || '';
    const summary = await api('/api/mdls/summary', signal); if (signal.aborted) return;
    const c = summary.counts || {};
    append(main, el('p', 'page-summary', `${count(c.mdls_pending)} pending MDLs with ${count(c.actions_pending)} actions pending (${count(c.total_actions)} total) in ${count(c.transferee_districts)} transferee districts, ${summary.counts_label}.`), dataNote(`Judge links are exact name-and-court joins to saved profiles; ${count(c.judges_unresolved_mdls)} MDLs list a judge who could not be matched and are shown as printed.`));
    const form = el('form', 'filters agency-filters'); form.setAttribute('aria-label', 'Filter MDLs');
    const q = filterField('Search captions', 'q', 'search', get('q'), 'Product, defendant or caption words');
    const type = filterField('Litigation type', 'litigation_type', 'select', get('litigation_type')); const circuit = filterField('Circuit', 'circuit', 'select', get('circuit')); const court = filterField('Transferee court', 'court', 'select', get('court'));
    const status = filterField('Status', 'status', 'select', get('status')); setOptions(status.input, [{ value: 'pending', label: 'Pending' }, { value: 'terminated', label: 'Recently terminated' }, { value: 'all', label: 'All listed' }], 'Pending (default)', get('status'));
    const resolved = filterField('Judge profile', 'judge_resolved', 'select', get('judge_resolved')); setOptions(resolved.input, [{ value: 'true', label: 'Matched to a saved profile' }, { value: 'false', label: 'Not matched' }], 'Any', get('judge_resolved'));
    const sort = filterField('Sort', 'sort', 'select', get('sort')); setOptions(sort.input, [{ value: 'pending_desc', label: 'Most actions pending' }, { value: 'mdl_number', label: 'MDL number' }, { value: 'title', label: 'Caption' }], 'Most actions pending', get('sort'));
    append(form, q.label, type.label, circuit.label, court.label, status.label, resolved.label, sort.label, action('Apply', () => {}, 'button-primary'), action('Clear', () => navigate('mdls')));
    form.addEventListener('submit', event => { event.preventDefault(); navigate('mdls', Object.fromEntries(new FormData(form))); }); form.querySelector('.button-primary').addEventListener('click', () => form.requestSubmit());
    main.append(form);
    const params = new URLSearchParams(p); params.set('limit', '50'); params.set('page', Math.max(1, Number(params.get('page')) || 1));
    const data = await api(`/api/mdls?${params}`, signal); if (signal.aborted) return;
    const facetOptions = (rows, labelFor) => (rows || []).map(([value, n]) => ({ value, label: `${labelFor ? labelFor(value) : value} (${count(n)})` }));
    setOptions(type.input, facetOptions(data.facets?.litigation_type), 'All types', get('litigation_type')); setOptions(circuit.input, facetOptions(data.facets?.circuit), 'All circuits', get('circuit')); setOptions(court.input, facetOptions(data.facets?.court), 'All courts', get('court'));
    if (!data.available) { main.append(el('p', 'registry-empty', 'The MDL registry is not available.')); return; }
    if (!data.results.length) { emptyState(main, 'No MDLs match', 'Try another caption word, type or court.', () => navigate('mdls')); return; }
    const table = el('div', 'table-panel'); const scroll = el('div', 'table-scroll'); const t = el('table'); const thead = el('thead'); const hr = el('tr'); for (const h of ['MDL', 'Transferee court', 'Transferee judge (as printed)', 'Actions (JPML)']) hr.append(el('th', '', h)); thead.append(hr); t.append(thead); const tbody = el('tbody');
    for (const m of data.results) tbody.append(mdlRow(m));
    t.append(tbody); scroll.append(t); table.append(scroll);
    append(main, append(el('div', 'results-heading'), el('strong', '', `${count(data.total)} MDLs`), el('small', '', data.counts_label)), table, pagination(data.total, Number(data.page) || 1, Number(data.limit) || 50));
  };
  registry.mdl = { title: 'MDL', nav: 'MDLs', parent: 'mdls', async render(signal, route) {
    const number = String(route.id || '').replace(/\D/g, '');
    heading('MDL ' + number, '', 'MDLs & mass torts'); loading(main, 'Opening the MDL…');
    let m; try { m = await api(`/api/mdl?number=${encodeURIComponent(number)}`, signal); } catch (error) { if (error.name === 'AbortError') return; main.removeAttribute('aria-busy'); main.replaceChildren(); heading('MDL ' + number, '', 'MDLs & mass torts'); errorState(main, error, () => render()); return; }
    if (signal.aborted) return; main.removeAttribute('aria-busy'); main.replaceChildren();
    heading(`MDL ${m.mdl_number}: ${m.title}`, `${m.litigation_type || 'Type not printed'} · ${m.status} · ${m.court_name || ''} (${m.circuit || ''})`, 'MDLs & mass torts');
    main.append(append(el('div', 'button-row'), routeLink('← Back to MDLs', route.params.get('from') ? route.params.get('from').replace(/^#/, '') : 'mdls', {}, 'button-link')));
    const head = el('section', 'panel'); const facts = el('dl', 'profile-facts');
    const row = (k, v) => { if (v !== null && v !== undefined && v !== '') append(facts, el('dt', '', k), el('dd', '', String(v))); };
    row('Actions pending', `${count(m.actions_pending)} (${m.counts_label})`); row('Total actions', count(m.total_actions)); row('Master docket', m.master_docket); row('Date filed', m.date_filed ? date(m.date_filed) : null); row('Date transferred', m.date_transferred ? date(m.date_transferred) : null); row('Date closed', m.date_closed ? date(m.date_closed) : null);
    const judge = m.transferee_judge || {}; row('Transferee judge as printed', judge.name_as_printed ? `${judge.name_as_printed}${judge.title_as_printed ? ', ' + judge.title_as_printed : ''}` : null);
    head.append(facts); main.append(head);
    const links = Array.isArray(m.judge_links) ? m.judge_links : [];
    const judgeBox = el('section', 'panel'); judgeBox.append(el('h2', '', 'Judge profile links'));
    if (links.length) { const rowEl = el('div', 'button-row'); for (const l of links) rowEl.append(routeLink(`${l.display_name || l.name || judge.name_as_printed} (${human(l.match_kind || l.basis || 'name and court')})`, `judge/${encodeURIComponent(l.entity_id)}`, { from: location.hash }, 'button')); judgeBox.append(rowEl); judgeBox.append(footnote('Joined by exact name and court to a saved profile; describes the transferee judge as printed, not a current-assignment check.')); }
    else judgeBox.append(el('p', 'quiet-empty', (m.unresolved && m.unresolved.length) ? `No saved profile matched: ${m.unresolved.map(u => u.reason || u).join('; ')}` : 'No saved profile matched the judge as printed.'));
    main.append(judgeBox);
    if (Array.isArray(m.snapshots) && m.snapshots.length) { const box = el('section', 'panel'); box.append(el('h2', '', 'Counts by report date')); const list = el('ul', 'career-timeline'); for (const s of m.snapshots) list.append(el('li', '', `${date(s.as_of)}: ${count(s.actions_pending)} pending, ${count(s.total_actions)} total (${human(s.report_kind)} report)`)); box.append(list); main.append(box); }
    const cl = Array.isArray(m.cl_links) ? m.cl_links : (m.cl_links ? [m.cl_links] : []);
    if (cl.length) { const box = el('section', 'panel'); box.append(el('h2', '', 'CourtListener docket (connector response)')); for (const l of cl) box.append(el('p', '', `Docket ${l.cl_docket_id || l.docket_id || ''}${l.court_id ? ' · ' + l.court_id : ''}${l.date_filed ? ' · filed ' + date(l.date_filed) : ''}${l.assigned_to_id ? ' · assigned person id ' + l.assigned_to_id : ''}${l.agreement ? ' · ' + human(l.agreement) : ''}`)); box.append(el('p', 'coverage-footnote', 'Saved connector responses, not original court bytes.')); main.append(box); }
    const local = Array.isArray(m.local_collections) ? m.local_collections : [];
    for (const [title, block, view] of [['Counsel, firms and parties on the saved dockets', m.appearances, 'mdl-appearances'], ['Key orders and settlement-related filings (master docket documents)', m.docket_documents, 'mdl-documents'], ['Docket activity in the saved release', m.docket_activity, 'mdl-activity'], ['Member cases in the saved docket sample', m.cases, 'mdl-cases'], ['Firms and counsel on the saved dockets', m.counsel_directory, 'counsel-directory'], ['Expert challenges on the docket', m.expert_rulings, 'expert-rulings'], ['Reported verdicts and settlements', m.verdict_reports, 'verdict-reports']]) { const box = mdlLayerBlock(title, block, view); if (box) main.append(box); }
    { const sp = relatedBlock('Related state coordinated proceedings (named by the state registry)', m.state_proceedings, 'state-proceedings'); if (sp) main.append(sp); }
    if (local.length) { const box = el('section', 'panel'); box.append(el('h2', '', 'Saved filings, orders and exhibits')); const rowEl = el('div', 'button-row'); for (const l of local) rowEl.append(routeLink(`Open the MDL ${m.mdl_number} document collection →`, `collection/${encodeURIComponent(l.collection_id || l.id)}`, {}, 'button button-primary')); box.append(rowEl); box.append(el('p', 'coverage-footnote', `${count(local[0].records)} document previews are selectable in the local collection; the full catalog is larger. Linked to this MDL by its MDL number.`)); main.append(box); }
    const docs = Array.isArray(m.documents) ? m.documents : [];
    if (docs.length) { const box = el('section', 'panel'); box.append(el('h2', '', 'JPML reports this entry comes from')); const list = el('div', 'profile-documents'); for (const d of docs) { const card = el('article', 'profile-document'); append(card, el('h3', '', d.title || d.report_kind || d.id), el('p', '', `${d.as_of ? 'Report dated ' + date(d.as_of) : ''}${d.bytes ? ' · ' + Math.round(d.bytes / 1024) + ' KB' : ''}`)); const rowEl = el('div', 'button-row'); if (d.id || d.document_id) rowEl.append(link('Open original ↗', `/mdl-files/${encodeURIComponent(d.id || d.document_id)}`)); if (d.source_url || d.url) rowEl.append(link('JPML page ↗', d.source_url || d.url, true)); card.append(rowEl); list.append(card); } box.append(list); main.append(box); }
    main.append(footnote('Publisher-reported JPML statistics as of the printed report date; not independently verified docket counts and not legal advice.'));
  } };
  /* ---- Generic listing/detail pages (settlements, court statistics, courts, counsel) ---- */
  function openGenericDetail(view, id) {
    api(`/api/area/${view}/item?id=${encodeURIComponent(id)}`).then(d => agencyDialog(cleanTitle(d.title, d.url) || d.title || id, body => {
      if (d.subtitle) body.append(el('p', 'reader-context', d.subtitle));
      if (Array.isArray(d.facts) && d.facts.length) {
        const plain = d.facts.filter(([k]) => !TECHNICAL_FACT.test(String(k))), tech = d.facts.filter(([k]) => TECHNICAL_FACT.test(String(k)));
        const list = dl(plain); if (list.children.length) body.append(list);
        const techList = dl(tech); if (techList.children.length) body.append(append(el('details', 'data-note'), el('summary', '', 'Technical details'), techList));
      }
      for (const s of d.sections || []) {
        body.append(el('h3', '', s.heading || ''));
        if (s.text) body.append(el('p', 'quality-note', s.text));
        if (Array.isArray(s.rows)) { const wrap = el('div', 'table-scroll'), t = el('table', 'dense-table'); if (Array.isArray(s.header)) { const hr = el('tr'); for (const h of s.header) hr.append(el('th', '', String(h ?? ''))); t.append(append(el('thead'), hr)); } const tb = el('tbody'); for (const r of s.rows.slice(0, 200)) { const tr = el('tr'); for (const c of r) tr.append(el('td', '', c === null || c === undefined ? '' : String(c))); tb.append(tr); } t.append(tb); wrap.append(t); body.append(wrap); }
        if (Array.isArray(s.items)) { const list = el('div', 'profile-documents'); for (const it of s.items.slice(0, 200)) { const card = el('article', 'profile-document'); const picture = (it.links || []).find(l => /^\/supplement-files\/[a-z_]+\/[A-Za-z0-9_.:-]+$/.test(String(l.url || '')) && /mark|logo|image|seal/i.test(`${s.heading || ''} ${l.label || ''}`)); if (picture) { const img = el('img', 'court-mark'); img.src = picture.url; img.alt = it.title || 'Court identity mark'; img.loading = 'lazy'; card.append(img); } append(card, el('h3', '', it.title || ''), it.subtitle ? el('p', '', it.subtitle) : null); const row = el('div', 'button-row'); for (const l of it.links || []) row.append(genericLink(l)); if (row.children.length) card.append(row); list.append(card); } body.append(list); }
      }
      const row = el('div', 'button-row'); for (const l of d.links || []) row.append(genericLink(l)); if (row.children.length) body.append(row);
      body.append(dataNote(d.qualification));
    })).catch(() => announce('Could not open this record.'));
  }
  function genericLink(l) { const url = String(l.url || ''); if (url.startsWith('#')) { const a = el('a', 'button', l.label || 'Open'); a.href = url; a.addEventListener('click', () => { if (dialog.open) dialog.close(); }); return a; } return link((l.label || 'Open') + ' ↗', url, /^https?:/.test(url)); }
  function genericPage(view) {
    return async function (signal) {
      const p = route.params; const params = new URLSearchParams(p); params.set('limit', '50'); params.set('page', Math.max(1, Number(params.get('page')) || 1));
      const data = await api(`/api/area/${view}?${params}`, signal); if (signal.aborted) return;
      if (!data.available) { main.append(el('p', 'registry-empty', data.reason || 'This data layer is not available yet.')); return; }
      // Material that used to sit under "Curated collections" now lives with the page it belongs to.
      const related = ({ settlements: [['Saved official settlement PDFs (previews)', 'collection/settlements']], courts: [['Court document coverage', 'court-coverage'], ['Official court website directory', 'collection/court-registries'], ['Court and judiciary visual library', 'collection/court-assets']] })[view];
      if (related) { const row = el('div', 'scope-switch'); row.append(el('span', 'record-subline', 'Saved reference material:')); for (const [label, target] of related) row.append(routeLink(label + ' →', target, {}, 'chip')); main.append(row); }
      if (view === 'court-coverage' && data.counts) { const c = data.counts, panel = el('section', 'panel'); append(panel, el('h2', '', `${count(c.court_entries)} court and jurisdiction entries`), el('p', '', `${count(c.existing_originals_reused)} existing originals linked · ${count(c.new_roster_entries)} additional roster entry · no duplicate document copies`), el('p', 'record-subline', `Source snapshot ${String(data.source_as_of || '').slice(0, 10)}. ${count(c.courts_with_exact_document_links)} entries have exact document links; ${count(c.court_count_discrepancies)} retain a difference between reported counts and exact links.`)); main.append(panel); }
      const form = el('form', 'filters'); form.setAttribute('aria-label', 'Filters');
      for (const f of data.filters || []) { const field = filterField(f.label || human(f.name), f.name, f.type === 'select' ? 'select' : f.type === 'date' ? 'date' : 'search', p.get(f.name) || '', f.placeholder || ''); if (f.type === 'select') setOptions(field.input, (f.options || []).map(o => ({ value: o.value, label: o.count === undefined ? (o.label || o.value) : `${o.label || o.value} (${count(o.count)})` })), `Any ${String(f.label || f.name).toLowerCase()}`, p.get(f.name) || ''); form.append(field.label); }
      const apply = el('button', 'button button-primary', 'Apply'); apply.type = 'submit'; append(form, apply, action('Clear', () => navigate(view)));
      form.addEventListener('submit', event => { event.preventDefault(); navigate(view, Object.fromEntries(new FormData(form))); });
      for (const select of form.querySelectorAll('select')) select.addEventListener('change', () => form.requestSubmit());
      main.append(form);
      if (!data.results?.length) { emptyState(main, 'Nothing matches', 'Remove a filter or try another search.', () => navigate(view)); main.append(dataNote(data.qualification)); return; }
      const cols = data.columns || []; const table = el('div', 'table-panel'), scroll = el('div', 'table-scroll'), t = el('table', 'dense-table'), hr = el('tr');
      // The first declared column is the title column only when it does not carry a value of its own (or carries the title itself).
      const sample = data.results[0] || {}, firstKey = cols[0]?.key, ownTitle = !firstKey || !(sample.cells && firstKey in sample.cells) || String(sample.cells[firstKey] ?? '') === String(sample.title ?? '');
      const dataCols = ownTitle ? cols.slice(1) : cols, TITLE_LABEL = { 'verdict-reports': 'Case', 'expert-rulings': 'Filing', 'cpsc-injury-data': 'Record' };
      hr.append(el('th', '', ownTitle ? (cols[0]?.label || 'Record') : (data.title_label || TITLE_LABEL[view] || 'Record'))); for (const c of dataCols) hr.append(el('th', '', c.label)); t.append(append(el('thead'), hr)); const tb = el('tbody');
      for (const r of data.results) { const tr = el('tr'), c1 = el('td');
        const open = action('', () => openGenericDetail(view, r.id), 'document-title'); titleText(open, r.title || r.id, r.url); c1.append(open);
        // Only the parts of the subtitle that a column does not already show.
        const columnValues = new Set(dataCols.map(c => String((r.cells || {})[c.key] ?? '').trim().toLowerCase()).filter(Boolean));
        const repeats = part => { const lower = part.toLowerCase(); if (columnValues.has(lower)) return true; const lead = lower.match(/^([\d,]+)\s+\S/); return Boolean(lead && (columnValues.has(lead[1]) || columnValues.has(lead[1].replace(/,/g, '')))); };
        const subtitle = String(r.subtitle || '').split(' · ').map(part => part.trim()).filter(part => part && !repeats(part)).join(' · ');
        if (subtitle) c1.append(el('div', 'record-subline', subtitle));
        // At most two quiet badges, and never one that repeats a value already visible in a column.
        const visible = new Set([subtitle.toLowerCase(), ...columnValues]);
        const picked = (Array.isArray(r.badges) ? r.badges : []).map(x => String(x).trim()).filter(x => x && !visible.has(x.toLowerCase()) && !repeats(x)).slice(0, 2);
        if (picked.length) { const b = el('div', 'table-badges'); for (const x of picked) b.append(badge(x, WARNING_BADGE.test(x) ? 'amber' : 'neutral')); c1.append(b); }
        tr.append(c1); for (const c of dataCols) tr.append(el('td', '', r.cells && r.cells[c.key] !== undefined && r.cells[c.key] !== null ? String(r.cells[c.key]) : '')); tb.append(tr); }
      t.append(tb); scroll.append(t); table.append(scroll);
      append(main, append(el('div', 'results-heading'), el('strong', '', `${count(data.total)}${data.total_capped ? '+' : ''} records`), el('small', '', 'Select a row for details and sources')), dataNote(data.qualification), table, pagination(data.total, Number(data.page) || 1, Number(data.limit) || 50));
    };
  }
  for (const view of ['settlements', 'statistics', 'courts', 'counsel', 'urls', 'uscourts', 'state-proceedings', 'court-documents', 'federal-opinions', 'court-coverage', 'judge-disclosures', 'federal-register', 'mdl-cases', 'mdl-appearances', 'mdl-documents', 'mdl-activity', 'sd-statutes', 'agency-documents', 'saved-pages', 'indiana-code', 'public-laws', 'state-codes', 'counsel-directory', 'verdict-reports', 'cpsc-injury-data', 'expert-rulings', 'source-documents', 'citation-guide', 'limitation-periods', 'citation-index']) if (registry[view] && !registry[view].page) registry[view].page = genericPage(view);
  /* The statistics dashboard module (statsviz.js) replaces the generic table page when it is loaded. */
  if (registry.regulations) { const genericRegulations = registry.regulations.page; registry.regulations.page = function (signal, supplement) { return (window.RegsUI && typeof window.RegsUI.regulations === 'function') ? window.RegsUI.regulations(signal, supplement) : genericRegulations(signal, supplement); }; }
  if (registry.agencies) { const genericAgencies = registry.agencies.page; registry.agencies.page = function (signal, supplement) { return (window.RegsUI && typeof window.RegsUI.agencies === 'function') ? window.RegsUI.agencies(signal, supplement) : genericAgencies(signal, supplement); }; }
  if (registry.statistics) { const genericStatistics = registry.statistics.page; registry.statistics.page = function (signal, supplement) { return (window.StatsViz && typeof window.StatsViz.page === 'function') ? window.StatsViz.page(signal, route, supplement) : genericStatistics(signal, supplement); }; }

  /* MDL docket layers share one tolerant renderer: a counts line from any by_* maps, up to ten rows, a link and the layer's own qualification. */
  function mdlLayerBlock(title, block, view) {
    if (!block) return null; const total = block.total ?? block.total_appearances_extended_linkage; const typed = block.by_doc_type || block.by_entry_type; const typeFilter = block.by_doc_type ? 'doc_type' : 'entry_type';
    const rows = block.results || block.sample || block.firms || (typed ? [] : (block.latest || block.latest_25 || []));
    if (!total && !rows.length) return null;
    const box = el('section', 'panel'); box.append(append(el('div', 'section-heading'), el('h2', '', title), el('small', '', total !== undefined ? `${count(total)} in the saved docket sample` : '')));
    const bits = []; for (const [k, v] of Object.entries(block)) if (/^by_(?!doc_type|entry_type)/.test(k) && v && typeof v === 'object' && !Array.isArray(v)) bits.push(`${human(k.slice(3))}: ` + Object.entries(v).sort((a, b) => b[1] - a[1]).slice(0, 6).map(([name, n]) => `${human(String(name))} ${count(n)}`).join(', '));
    if (block.date_first || block.first_date) bits.push(`entries dated ${block.date_first || block.first_date} to ${block.date_last || block.last_date || '?'}`); if (block.capped) bits.push('entries capped by the source release');
    if (block.total_appearances_direct_linkage !== undefined) bits.push(`${count(block.total_appearances_direct_linkage)} appearances on the master docket itself, ${count((block.total_appearances_extended_linkage || 0) - block.total_appearances_direct_linkage)} on linked member cases`);
    if (block.lead_attorney_appearance_count) bits.push(`${count(block.lead_attorney_appearance_count)} marked lead attorney`);
    if (block.party_counts_by_category) { const c = block.party_counts_by_category; bits.push(`parties: ${count(c.organization || 0)} organizations, ${count(c.natural_person_count_only || 0)} individuals (counted, never named)`); }
    if (bits.length) box.append(el('p', 'record-subline', bits.join(' · ')));
    if (typed) { const chips = el('div', 'chip-row'); const entries = Object.entries(typed).filter(([, n]) => n > 0).sort((a, b) => (a[0] === 'other') - (b[0] === 'other') || b[1] - a[1]); for (const [kind, n] of entries) chips.append(routeLink(`${human(kind)} ${count(n)}`, view, { mdl: block.mdl_number || '', [typeFilter]: kind }, 'chip')); box.append(chips); }
    if (rows.length) { const list = el('ul', 'related-list'); for (const r of rows.slice(0, 10)) { const li = el('li'); const label = r.title || r.name || r.canonical_name || r.firm_name || r.description || r.id; if (r.id !== undefined) li.append(action(String(label).slice(0, 140), () => openGenericDetail(view, String(r.id)), 'document-title')); else li.append(el('span', 'document-title', String(label).slice(0, 140))); const sub = r.subtitle || [r.date_filed || r.date || r.entry_date, r.status, r.court_id, r.appearance_count !== undefined ? `${count(r.appearance_count)} appearances` : ''].filter(Boolean).join(' · '); if (sub) li.append(el('div', 'record-subline', sub)); list.append(li); } box.append(list); }
    { const target = block.mdl_number ? makeHash(view, { mdl: block.mdl_number }) : block.link; if (target) { const a = el('a', 'button-link', 'See all →'); a.href = target; box.append(a); } }
    box.append(footnote(block.qualification));
    return box;
  }

  /* ---- Related-material blocks from optional data layers (same shape everywhere: total, results, link, qualification) ---- */
  function relatedBlock(title, block, view, max = 8) {
    if (!block || !block.total) return null; const box = el('section', 'panel'); box.append(append(el('div', 'section-heading'), el('h2', '', title), el('small', '', `${count(block.total)} records`)));
    const list = el('ul', 'related-list'); for (const r of (block.results || []).slice(0, max)) { const li = el('li'); const open = action('', () => openGenericDetail(view, r.id), 'document-title'); titleText(open, r.title || r.id, r.url); li.append(open); if (r.subtitle) li.append(el('div', 'record-subline', r.subtitle)); if (r.evidence) li.append(el('div', 'record-subline', r.evidence)); list.append(li); }
    box.append(list); if (block.link) { const a = el('a', 'button-link', 'See all →'); a.href = block.link; box.append(a); } box.append(footnote(block.qualification)); return box;
  }
  window.judgeDisclosuresSection = function (profile) {
    const d = profile.disclosures; if (!d || !d.available) return null; const box = el('section', 'judge-structured'); box.append(el('h2', '', 'Financial disclosures (CourtListener snapshot 2026-06-30)'));
    if (d.state === 'no_filing_in_snapshot') { box.append(el('p', 'quiet-empty', 'No filing for this judge in the snapshot. That is a gap in the snapshot, not a statement about holdings.')); return box; }
    const years = d.years_filed || []; box.append(el('p', 'record-subline', `Filed for ${years.length} year${years.length === 1 ? '' : 's'}${years.length ? ` (${years[0]}–${years[years.length - 1]})` : ''} · ${count(d.total_holdings || 0)} holding rows in all filings · latest year ${d.latest_year || 'not stated'}`));
    const top = d.top_holdings_latest_year || []; if (top.length) { box.append(el('h3', '', `Holdings named in the ${d.latest_year} filing (alphabetical, first ${top.length})`)); const chips = el('div', 'chip-row'); for (const h of top) { const label = (h && typeof h === 'object') ? (h.redacted ? 'Redacted by the filer' : h.description) : h; const chip = el('span', 'chip', String(label || '').slice(0, 80) + (h && h.inferred ? ' *' : '')); if (h && h.inferred) chip.title = 'Value fields on this row were inferred during extraction'; chips.append(chip); } box.append(chips); } else box.append(el('p', 'quiet-empty', 'The latest filing lists no reportable holdings.'));
    if (d.link) { const a = el('a', 'button-link', 'All filings and holdings →'); a.href = d.link; box.append(a); } box.append(footnote(d.qualification)); return box;
  };

  /* ---- US state grid and state pages ---- */
  const GRID = { AK: [0, 0], ME: [10, 0], VT: [9, 1], NH: [10, 1], WA: [0, 2], ID: [1, 2], MT: [2, 2], ND: [3, 2], MN: [4, 2], IL: [5, 2], WI: [6, 2], MI: [7, 2], NY: [8, 2], RI: [9, 2], MA: [10, 2], OR: [0, 3], NV: [1, 3], WY: [2, 3], SD: [3, 3], IA: [4, 3], IN: [5, 3], OH: [6, 3], PA: [7, 3], NJ: [8, 3], CT: [9, 3], CA: [0, 4], UT: [1, 4], CO: [2, 4], NE: [3, 4], MO: [4, 4], KY: [5, 4], WV: [6, 4], VA: [7, 4], MD: [8, 4], DE: [9, 4], AZ: [1, 5], NM: [2, 5], KS: [3, 5], AR: [4, 5], TN: [5, 5], NC: [6, 5], SC: [7, 5], DC: [8, 5], OK: [3, 6], LA: [4, 6], MS: [5, 6], AL: [6, 6], GA: [7, 6], HI: [0, 7], TX: [3, 7], FL: [8, 7], PR: [10, 7] };
  const familyTotal = f => (f?.official_capture?.total || 0) + (f?.imported_collection || 0) + (f?.third_party_snapshot || 0) + (f?.pending_publication || 0);
  const METRICS = [
    ['law', 'Saved law records', row => Object.values(row.families || {}).reduce((n, f) => n + familyTotal(f), 0)],
    ['official', 'Official captures', row => Object.values(row.families || {}).reduce((n, f) => n + (f?.official_capture?.total || 0), 0)],
    ['counties', 'Counties with local resources', row => row.county_layer?.with_local_resource ?? row.counties_with_local_resource ?? 0],
    ['gaps', 'Open coverage gaps', row => (row.gaps || []).length],
  ];
  let matrixCache = null;
  window.renderUsMap = async function (target, signal) {
    target.append(append(el('div', 'section-heading'), el('h2', '', 'Browse by state'), el('small', '', 'Select a state for its counties, laws, judges and sources')));
    let data; try { data = matrixCache || (matrixCache = await api('/api/coverage/matrix', signal)); } catch (error) { target.append(el('p', 'registry-empty', 'State coverage is not available right now.')); return; }
    if (signal?.aborted || !data.available) return;
    if (window.UsMap) {
      try {
        let filing = null; try { filing = await api('/api/county-filing/coverage', signal); } catch (error) { if (error.name === 'AbortError') return; }
        const rows = data.rows || [], values = fn => Object.fromEntries(rows.map(r => [r.abbr, fn(r)]));
        const metrics = METRICS.map(([key, label, fn]) => ({ key, label, values: values(fn) }));
        if (filing && filing.available) { const by = Object.fromEntries((filing.states || []).map(s => [s.state, s]));
          const share = (k, field) => by[k].counties_total ? Math.round(100 * (by[k][field] || 0) / by[k].counties_total) : 0;
          metrics.unshift({ key: 'filing', label: 'Share of counties with a court rules or filing source', unit: 'of counties have an official rules or filing source', format: v => v + '%', values: Object.fromEntries(Object.keys(by).map(k => [k, share(k, 'with_any')])) },
            { key: 'local', label: 'Share of counties with a local rules source', unit: 'of counties have a local rules source', format: v => v + '%', values: Object.fromEntries(Object.keys(by).map(k => [k, share(k, 'with_local_rules')])) }); }
        const holder = el('div', 'map-holder'); target.append(holder);
        await window.UsMap.national(holder, { metrics, onState: usps => navigate(`state/${usps}`) });
        const extra = rows.filter(r => !window.UsMap.USPS_FIPS[r.abbr]); if (extra.length) { const line = el('p', 'record-subline'); line.append(document.createTextNode('Territories: ')); for (const r of extra) line.append(routeLink(r.name, `state/${r.abbr}`, {}, 'button-link'), document.createTextNode('  ')); target.append(line); }
        return;
      } catch (error) { if (error.name === 'AbortError') return; }
    }
    const controls = el('div', 'map-controls'), grid = el('div', 'us-grid'), legend = el('p', 'coverage-footnote');
    append(target, controls, grid, legend);
    const rows = new Map((data.rows || []).map(r => [r.abbr, r]));
    function draw(metricKey) {
      const [, label, fn] = METRICS.find(m => m[0] === metricKey) || METRICS[0];
      controls.replaceChildren(); for (const [key, text] of METRICS) { const b = action(text, () => draw(key), key === metricKey ? 'button-small selected' : 'button-small'); b.setAttribute('aria-pressed', String(key === metricKey)); controls.append(b); }
      const values = [...rows.values()].map(fn), max = Math.max(1, ...values); grid.replaceChildren();
      for (const [abbr, [col, row]] of Object.entries(GRID)) {
        const r = rows.get(abbr), value = r ? fn(r) : 0, level = value ? Math.min(5, 1 + Math.floor(4 * Math.log10(1 + value) / Math.log10(1 + max))) : 0;
        const tile = el('a', `us-tile level-${metricKey === 'gaps' ? 6 - Math.max(1, level) : level}`); tile.href = `#state/${abbr}`; tile.style.gridColumn = String(col + 1); tile.style.gridRow = String(row + 1);
        append(tile, el('strong', '', abbr), el('span', '', count(value))); tile.title = `${r?.name || abbr}: ${count(value)} ${label.toLowerCase()}`; tile.setAttribute('aria-label', tile.title); grid.append(tile);
      }
      const extra = [...rows.values()].filter(r => !GRID[r.abbr]); legend.replaceChildren(document.createTextNode(`${label}, as recorded in the coverage layer (${data.generated_at ? date(data.generated_at) : 'date unknown'}). Darker means more. `));
      for (const r of extra) legend.append(routeLink(r.name, `state/${r.abbr}`, {}, 'button-link'), document.createTextNode(' '));
    }
    draw('law');
  };
  const GAP_LABELS = { regulations_none_any_source: 'No regulations from any source', court_rules_third_party_absent: 'Court rules absent from the third-party snapshot', court_rules_no_civil_procedure_set_typed: 'No civil-procedure rule set identified', court_rules_no_evidence_set_typed: 'No evidence rule set identified', court_rules_no_appellate_set_typed: 'No appellate rule set identified', county_local_rules_none: 'No county local rules saved', statutes_none_any_source: 'No statutes from any source' };
  registry.state = { title: 'State', nav: 'State', parent: 'overview', async render(signal, route) {
    const abbr = String(route.id || '').toUpperCase().slice(0, 2);
    heading(abbr, '', 'States & counties'); loading(main, 'Opening the state…');
    let s; try { s = await api(`/api/coverage/state?state=${encodeURIComponent(abbr)}`, signal); } catch (error) { if (error.name === 'AbortError') return; main.removeAttribute('aria-busy'); main.replaceChildren(); emptyState(main, 'State not found', 'Choose a state from the map.', () => navigate('overview')); return; }
    if (signal.aborted) return; main.removeAttribute('aria-busy'); main.replaceChildren();
    heading(s.name, `${human(s.jurisdiction_kind)} · what is saved, what is missing, and every county`, 'States & counties');
    const coverageLine = el('p', 'muted-note state-coverage'); coverageLine.hidden = true; main.append(coverageLine);
    // County filing coverage for the state. The endpoint may be missing or report that it is unavailable: the line then stays hidden.
    api(`/api/county-filing/state?state=${encodeURIComponent(abbr)}`, signal).then(cf => {
      if (!cf || !cf.available || signal.aborted) return;
      const bits = [];
      if (cf.counties_total) bits.push(`${count(cf.counties_with_local_rules)} of ${count(cf.counties_total)} counties have local rules saved`);
      if (cf.counties_with_any !== undefined) bits.push(`${count(cf.counties_with_any)} have at least one filing source`);
      if (cf.structure) bits.push(cf.structure);
      if (!bits.length) return;
      const full = [bits.join(' · '), cf.has_local_rules].filter(Boolean).join(' ');
      coverageLine.textContent = sentenceClip(bits.join(' · '), 150); coverageLine.title = full; coverageLine.hidden = false;
    }).catch(() => {});
    const quick = el('div', 'button-row state-quick');
    append(quick, routeLink('← US map', 'overview', {}, 'button-link'), routeLink('State law', 'laws', { state: s.name }, 'button'), routeLink('Judges', 'judges', { state: s.name }, 'button'), routeLink('Counties list', 'counties', { state: s.name }, 'button'), routeLink(`Source references${s.source_directory?.references ? ' (' + count(s.source_directory.references) + ')' : ''}`, 'sources', { jurisdiction: abbr.toLowerCase() }, 'button'), routeLink('DOJ state resources', 'resources', { state: abbr }, 'button'), routeLink('Topic candidates', 'coverage', { state: abbr }, 'button'));
    main.append(quick);
    const layout = el('div', 'state-layout'), left = el('section', 'panel'), right = el('section', 'panel');
    left.append(el('h2', '', 'Saved law by family and source tier'));
    const t = el('table', 'dense-table'), hr = el('tr'); for (const h of ['Family', 'Official capture', 'Imported', 'Third-party snapshot']) hr.append(el('th', '', h)); t.append(append(el('thead'), hr)); const tb = el('tbody');
    for (const [family, f] of Object.entries(s.families || {})) { const tr = el('tr'); const c = el('td'); c.append(routeLink(human(family), 'laws', { state: s.name, category: ({ statutes: 'statutes', constitution: 'constitutions', regulations: 'regulations', court_rules: 'rules', forms: 'forms' })[family] || '' }, 'button-link')); tr.append(c, el('td', 'small-number', count(f?.official_capture?.total || 0)), el('td', 'small-number', count(f?.imported_collection || 0)), el('td', 'small-number', count(f?.third_party_snapshot || 0))); tb.append(tr); }
    t.append(tb); left.append(append(el('div', 'table-scroll'), t));
    left.append(footnote('Counts are saved records by source tier, not unique or current provisions. Third-party snapshot rows come from the Open US Law publisher snapshot.'));
    right.append(el('h2', '', 'Gaps in the coverage snapshot'));
    right.append(el('p','muted-note','New source additions are listed separately below; they do not establish complete or current coverage.'));
    if ((s.gaps || []).length) { const list = el('ul', 'gap-list'); for (const g of s.gaps) list.append(el('li', '', GAP_LABELS[g] || human(g))); right.append(list); } else right.append(el('p', 'quiet-empty', 'No gap flags recorded for this state.'));
    if (s.topic_counts && Object.keys(s.topic_counts).length) { right.append(el('h3', '', 'Mass-tort topic candidates')); const chips = el('div', 'chip-row'); for (const [topic, n] of Object.entries(s.topic_counts)) chips.append(routeLink(`${human(topic)} ${count(n)}`, 'coverage', { state: abbr, topic }, 'chip')); right.append(chips); right.append(footnote(s.topic_label || 'Search-derived candidates, not a survey or legal advice.')); }
    append(layout, left, right); main.append(layout);
    const counties = el('section', 'panel'); counties.append(append(el('div', 'section-heading'), el('h2', '', 'Counties'), el('small', '', 'Darker = local rules, forms or litigation resources saved; lighter = profile or site only')));
    const countyMap = el('div', 'map-holder state-map'); counties.append(countyMap);
    const grid = el('div', 'county-chip-grid'); counties.append(grid); main.append(counties);
    if (window.UsMap && window.UsMap.USPS_FIPS[abbr]) (async () => { try { const f = await api(`/api/county-filing/state-counties?state=${encodeURIComponent(abbr)}`, signal); if (signal.aborted) return; const info = Object.fromEntries(((f && f.counties) || []).map(c => [c.fips, c])); await window.UsMap.state(countyMap, abbr, { counties: info, onCounty: fips => navigate(`county/${fips}`, { state: s.name }) }); } catch (error) { countyMap.remove(); } })(); else countyMap.remove();
    try { const list = await api(`/api/counties?state=${encodeURIComponent(s.name)}&limit=100`, signal); let items = list.items || []; for (let page = 2; items.length < list.total && page <= 4; page++) { const more = await api(`/api/counties?state=${encodeURIComponent(s.name)}&limit=100&page=${page}`, signal); items = items.concat(more.items || []); }
      if (signal.aborted) return; if (!items.length) grid.append(el('p', 'quiet-empty', 'No county-equivalents are listed for this jurisdiction.'));
      for (const c of items) { const strong = (Number(c.local_resources) || 0) + (Number(c.litigation_resources) || 0), some = (Number(c.saved_profiles) || 0) + (Number(c.saved_sites) || 0) + (c.has_court_registry ? 1 : 0); const chip = el('a', `county-chip ${strong ? 'strong' : some ? 'some' : 'none'}`); chip.href = makeHash(`county/${encodeURIComponent(c.geoid)}`, { state: c.state, county: c.geoid, from: location.hash }); append(chip, el('strong', '', c.name.replace(/ (County|Parish|Borough|Census Area|Municipality)$/, '')), el('span', '', strong ? `${count(strong)} local` : some ? 'profile' : '—')); chip.title = `${c.name}: ${strong} local or litigation resources, ${c.saved_profiles || 0} saved profiles, ${c.saved_sites || 0} saved sites`; grid.append(chip); }
    } catch (error) { if (error.name !== 'AbortError') grid.append(el('p', 'registry-empty', 'Counties could not be loaded.')); }
    if (Array.isArray(s.venues) && s.venues.length) { const v = el('section', 'panel'); v.append(el('h2', '', 'Mass-tort venues in this state')); const row = el('div', 'button-row'); for (const venue of s.venues) row.append(routeLink(`${venue.venue} (${human(venue.tier)})`, `county/${venue.fips}`, { state: s.name, county: venue.fips, from: location.hash }, 'button')); v.append(row); main.append(v); }
    try { const b = await api(`/api/blocks?state=${encodeURIComponent(abbr)}`, signal); if (!signal.aborted) { const lp = limitationBlock(b.limitation_periods); if (lp) main.append(lp); const sp = relatedBlock('State coordinated mass-tort proceedings', b.state_proceedings, 'state-proceedings'); if (sp) main.append(sp); const cd = relatedBlock('Court documents saved for this state', b.court_documents, 'court-documents', 6); if (cd) main.append(cd); const pg = relatedBlock('Saved web pages for this state (courts and legislature)', b.saved_pages, 'saved-pages', 6); if (pg) main.append(pg); } } catch (error) { if (error.name === 'AbortError') return; }
    try { const u = (await api(`/api/urls/block?state=${encodeURIComponent(abbr)}`, signal)).block; if (u && !signal.aborted) { const box = el('section', 'panel'); box.append(append(el('div', 'section-heading'), el('h2', '', 'Known addresses for this state'), el('small', '', `${count(u.total)} addresses in the saved discovery lists · ${count(u.saved)} already saved here`)));
        const chips = el('div', 'chip-row'); for (const l of u.by_layer || []) chips.append(routeLink(`${l.label} ${count(l.count)}`, 'urls', { state: abbr, layer: l.value }, 'chip')); for (const k of (u.by_kind || []).filter(k => k.value !== 'page').slice(0, 4)) chips.append(routeLink(`${k.label} ${count(k.count)}`, 'urls', { state: abbr, kind: k.value }, 'chip')); box.append(chips);
        const hosts = el('p', 'record-subline'); hosts.textContent = 'Largest sites: ' + (u.top_hosts || []).slice(0, 8).map(h => `${h.host} (${count(h.count)})`).join(' · '); box.append(hosts, routeLink('Open the URL directory for this state →', 'urls', { state: abbr }, 'button-link')); main.append(box); } } catch (error) { if (error.name === 'AbortError') return; }
    main.append(dataNote(s.qualification));
  } };

  /* ---- Limitation periods on a state page ---- */
  function limitationBlock(block) {
    if (!block || !block.available || !(block.rows || []).length) return null;
    const box = el('section', 'panel limitation-block');
    box.append(append(el('div', 'section-heading'), el('h2', '', 'Limitation periods'), el('small', '', 'Published summary table; three claim types checked against the saved statutes')));
    const wrap = el('div', 'table-scroll'), t = el('table', 'dense-table'), hr = el('tr'); for (const h of ['Claim', 'Period', 'Section looked up', 'Saved statute check']) hr.append(el('th', '', h)); t.append(append(el('thead'), hr));
    const tb = el('tbody');
    for (const r of block.rows) { const tr = el('tr'), c1 = el('td'); const open = action(r.claim, () => openGenericDetail('limitation-periods', r.id), 'document-title'); c1.append(open); if (r.note) c1.append(el('div', 'record-subline', r.note));
      const c3 = el('td'); if (r.record_id) { const a = el('a', '', r.section); a.href = `#record/${encodeURIComponent(r.record_id)}`; c3.append(a); } else c3.textContent = r.section || '';
      const c4 = el('td', r.outcome === 'different_wording_found' ? 'limitation-differs' : '', r.check || ''); tr.append(c1, el('td', '', r.period), c3, c4); tb.append(tr); }
    t.append(tb); wrap.append(t); box.append(wrap, routeLink('All limitation periods for this state →', 'limitation-periods', { state: (block.link || '').split('state=')[1] || '' }, 'button-link'), dataNote(`${block.qualification} Source table: ${block.attribution}`));
    return box;
  }

  /* ---- Judge profile helpers (used by app.js) ---- */
  window.judgeChips = function (profile) {
    const row = el('div', 'judge-chips'); const s = profile.structured || {};
    for (const a of (profile.aliases || []).slice(0, 3)) { const b = badge(`Also printed as “${a.alias}”`, 'neutral'); b.title = `${a.source || ''}. ${a.basis || ''}`; row.append(b); }
    const status = s.fjc?.fjc_status || s.fjc_status; if (status?.value) row.append(badge(`${human(status.value)} · FJC-reported, as of ${status.as_of || 'snapshot date'}`, status.value === 'active' ? '' : 'neutral'));
    if (s.role_normalized) row.append(badge(human(typeof s.role_normalized === 'string' ? s.role_normalized : s.role_normalized.value), 'neutral'));
    const mdls = profile.mdls?.results || []; if (mdls.length) { const pending = mdls.reduce((n, m) => n + (Number(m.actions_pending) || 0), 0); row.append(badge(`${mdls.length} MDL${mdls.length === 1 ? '' : 's'} · ${count(pending)} actions pending (JPML ${mdls[0].as_of || ''})`, 'amber')); }
    if (profile.analysis_count) row.append(badge(`${count(profile.analysis_count)} analysis records`, 'neutral'));
    if (s.completeness?.score !== undefined) row.append(badge(`Profile completeness ${s.completeness.score}/100`, 'neutral'));
    return row.children.length ? row : null;
  };
  window.judgeStructuredSection = function (profile) {
    const s = profile.structured; if (!s) return null; const box = el('section', 'judge-structured');
    const appts = s.fjc?.appointments || s.appointments || [];
    if (appts.length) { box.append(el('h2', '', 'Federal appointments (Federal Judicial Center)')); const t = el('table', 'dense-table'), hr = el('tr'); for (const h of ['Court and title', 'Appointed by', 'Confirmed / commissioned', 'ABA rating', 'Senate vote']) hr.append(el('th', '', h)); t.append(append(el('thead'), hr)); const tb = el('tbody');
      for (const a of appts) { const tr = el('tr'); tr.append(el('td', '', [a.court, a.title].filter(Boolean).join(' · ')), el('td', '', [a.appointing_president, a.party_of_appointing_president || a.party].filter(Boolean).join(' · ')), el('td', '', [a.confirmation_date && `confirmed ${date(a.confirmation_date)}`, a.commission_date && `commissioned ${date(a.commission_date)}`, a.senior_status_date && `senior ${date(a.senior_status_date)}`, a.termination_date && `${human(a.termination_reason || 'terminated')} ${date(a.termination_date)}`].filter(Boolean).join('; ')), el('td', '', a.aba_rating || ''), el('td', '', [a.senate_vote_type, a.ayes_nays].filter(Boolean).join(' '))); tb.append(tr); }
      t.append(tb); box.append(append(el('div', 'table-scroll'), t)); }
    const cl = s.courtlistener || {}; const aff = cl.political_affiliations || [];
    if (aff.length) { box.append(el('h3', '', 'Political affiliation as recorded by CourtListener')); const list = el('ul', 'career-timeline'); for (const a of aff) list.append(el('li', '', [a.party || a.political_party, a.source && `source: ${human(a.source)}`, a.date_start && `from ${a.date_start}`, a.date_end && `to ${a.date_end}`].filter(Boolean).join(' · '))); box.append(list); }
    const ids = s.ids || {}; const idText = [ids.fjc_nid && `FJC nid ${ids.fjc_nid}`, ids.fjc_jid && `FJC jid ${ids.fjc_jid}`, ids.cl_person_id && `CourtListener person ${ids.cl_person_id}`].filter(Boolean).join(' · ');
    if (idText) box.append(el('p', 'coverage-footnote', `Identifiers (native-id bridge, never by name): ${idText}.`));
    if (s.completeness?.missing?.length) box.append(el('p', 'coverage-footnote', `Not yet in this profile: ${s.completeness.missing.map(human).join(', ')}.`));
    return box.children.length ? box : null;
  };

  window.judgeEvidenceSection = function (profile) {
    const e = profile.evidence; if (!e) return null; const box = el('section', 'judge-structured');
    const cjra = e.cjra; if (cjra && Array.isArray(cjra.tables) && cjra.tables.length) {
      box.append(el('h2', '', `Civil Justice Reform Act report (as of ${cjra.as_of ? date(cjra.as_of) : 'date not recorded'})`));
      const t = el('table', 'dense-table'), tb = el('tbody'); for (const row of cjra.tables) { const tr = el('tr'); tr.append(el('td', '', row.label || human(row.table)), el('td', 'small-number', count(row.count))); tb.append(tr); } t.append(tb); box.append(append(el('div', 'table-scroll'), t));
      box.append(footnote(`${cjra.caveat || 'Publisher-reported counts for the period; never a rate.'} Printed as "${cjra.judge_name_as_printed || ''}", ${cjra.court_as_printed || ''}; match: ${human(cjra.match_basis || 'exact name and court')}.`));
    }
    const a = e.assignments; if (a && ((a.assigned_count || 0) + (a.referred_count || 0))) {
      box.append(el('h2', '', 'Matters in the saved docket dataset'));
      box.append(el('p', '', `${count(a.assigned_count)} assigned · ${count(a.referred_count)} referred${a.date_filed_first ? ` · filed ${a.date_filed_first} to ${a.date_filed_last || '?'}` : ''}. ${a.dataset_label || a.label || ''}`));
      const chips = el('div', 'chip-row'); for (const [k, n] of (a.by_nature_of_suit || []).slice(0, 8).map(x => Array.isArray(x) ? x : [x.label || x.value, x.count])) chips.append(el('span', 'chip', `${k} ${count(n)}`)); if (chips.children.length) box.append(chips);
      const list = el('ul', 'career-timeline'); for (const m of (a.recent_matters || a.matters || []).slice(0, 12)) list.append(el('li', '', [m.case_name, m.docket_number, m.court, m.date_filed && `filed ${m.date_filed}`, m.role && human(m.role)].filter(Boolean).join(' · '))); if (list.children.length) box.append(list);
      box.append(footnote('Counts describe this saved dataset only, not the full docket of the judge; no rates or outcomes are computed.'));
    }
    const edu = e.education || []; if (edu.length && !(profile.education || []).length) { box.append(el('h3', '', 'Education as recorded by CourtListener')); const list = el('ul', 'career-timeline'); for (const x of edu) list.append(el('li', '', [x.school, x.degree || x.degree_detail, x.degree_year].filter(Boolean).join(' · '))); box.append(list); }
    const pos = e.positions || []; if (pos.length && !(profile.appointments || []).length && !(profile.structured?.fjc?.appointments || []).length) { box.append(el('h3', '', 'Positions as recorded by CourtListener')); const list = el('ul', 'career-timeline'); for (const x of pos.slice(0, 15)) list.append(el('li', '', [x.position_type || x.job_title, x.court || x.organization_name, x.date_start && `from ${x.date_start}`, x.date_termination && `to ${x.date_termination}`].filter(Boolean).join(' · '))); box.append(list); }
    return box.children.length ? box : null;
  };
  // Pages whose data layer is published; the hub tab bar lists only those.
  // One retry: a request that loses the race with a slow page query would otherwise leave every gated tab hidden.
  (function loadReadySupplements(attempt) {
    api('/api/supplements').then(data => {
      window.READY_SUPPLEMENTS = new Set((data.items || []).filter(i => i.ready).map(i => i.name));
      if (typeof renderHubTabs === 'function') renderHubTabs();
    }).catch(() => { if (attempt < 2) setTimeout(() => loadReadySupplements(attempt + 1), 2500); });
  })(1);
})();
