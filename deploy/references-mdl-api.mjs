/* Source-reference taxonomy and JPML report contracts; all joins use saved IDs. */
const integer = (value, fallback) => /^-?\d+$/.test(String(value ?? '').trim()) ? Number(value) : fallback;
const normalized = value => String(value ?? '').toLowerCase();
const array = value => Array.isArray(value) ? value : value == null ? [] : [value];
const bool = value => ['true', '1', 'yes'].includes(normalized(value)) ? true : ['false', '0', 'no'].includes(normalized(value)) ? false : null;
const fail = (error, status = 404) => Response.json({ error }, { status });
const counts = values => [...values.reduce((out, value) => out.set(value, (out.get(value) ?? 0) + 1), new Map())]
  .sort(([a, n], [b, m]) => m - n || String(a).localeCompare(String(b)));

function mdlSort(rows, sort) {
  return [...rows].sort((a, b) => sort === 'mdl_number' ? a.item.mdl_number - b.item.mdl_number :
    sort === 'title' ? normalized(a.item.title).localeCompare(normalized(b.item.title)) || a.item.mdl_number - b.item.mdl_number :
      Number(a.item.actions_pending == null) - Number(b.item.actions_pending == null) ||
      (b.item.actions_pending ?? 0) - (a.item.actions_pending ?? 0) || a.item.mdl_number - b.item.mdl_number);
}

export async function handleReferencesMdl(path, params, context) {
  const source = ['/api/sources', '/api/source'].includes(path);
  const mdl = ['/api/mdls', '/api/mdl', '/api/mdls/summary', '/api/mdls/for-judge', '/api/mdls/for-person'].includes(path);
  if (!source && !mdl) return null;
  const dataset = source ? 'sources' : 'mdls';
  const descriptor = await context.dataset(dataset);
  if (!descriptor || descriptor.ready === false) return fail('This directory is not published yet.', 503);
  const meta = descriptor.metadata ?? descriptor;
  const baseline = meta.listing ?? {};
  const index = meta.filter_index ?? [];
  if (path === '/api/source') return await context.detail(params.id, ['sources'], { full: false }) ?? fail('Source reference not found');
  if (path === '/api/mdl') {
    const number = integer(params.number, null);
    return number == null ? fail('MDL not found') : await context.detail(String(number), ['mdls'], { full: false }) ?? fail('MDL not found');
  }
  if (path === '/api/mdls/summary') return meta.registry_summary ?? { available: false };

  if (source) {
    const page = Math.max(1, Math.min(100000, integer(params.page, 1)));
    const limit = Math.max(1, Math.min(100, integer(params.limit, 30)));
    const filters = { ...params };
    if (filters.jurisdiction) {
      filters.jurisdiction = baseline.facets?.jurisdictions?.find(row =>
        [row.value, row.label].some(value => normalized(value) === normalized(filters.jurisdiction)))?.value ?? filters.jurisdiction;
    }
    const terms = normalized(params.q).trim().split(/\s+/).filter(Boolean);
    let matched = index.filter(row => terms.every(term => row.search.includes(term)) &&
      ['jurisdiction', 'category', 'access_method', 'verification_status', 'layer', 'task_family', 'content_kind', 'source_type', 'access_requirements']
        .every(key => !filters[key] || row.filters[key] === filters[key]) &&
      (params.api_bulk !== '1' || row.filters.api_bulk === '1') &&
      (!['saved', 'archived', 'links_only'].includes(params.has) || array(row.filters.has).includes(params.has)));
    if (params.order === 'registry') matched = [...matched].sort((a, b) => a.registry_order - b.registry_order);
    const total = matched.length;
    const ids = matched.slice((page - 1) * limit, page * limit).map(row => row.id);
    let items = [];
    if (ids.length) {
      const result = await context.query({ datasets: ['sources'], filters: { id: ids }, q: '', offset: 0, limit: ids.length, sort: 'rank' });
      const byId = new Map(result.items.map(row => [row.id, row]));
      items = ids.map(id => byId.get(id)).filter(Boolean);
    }
    return { ...baseline, ready: true, total, items, page, limit };
  }

  if (path === '/api/mdls/for-judge' || path === '/api/mdls/for-person') {
    const person = path.endsWith('for-person');
    const value = person ? integer(params.cl_person_id, null) : String(params.entity_id ?? '').trim();
    const selected = mdlSort(index.filter(row => person ? value != null && row.filters.cl_person_id === String(value) :
      value && array(row.filters.entity_id).includes(value)), 'pending_desc');
    const results = selected.map(row => row.item);
    const bases = [...new Set(results.map(row => row.judge_link_basis).filter(Boolean))].sort();
    return { available: true, as_of: baseline.as_of, counts_label: baseline.counts_label, total: results.length, results,
      ...(person ? { cl_person_id: value, basis: 'cl_assigned_to_id' } : { entity_id: value || null, basis: bases.join('+') || 'jpml_name_court' }) };
  }

  const status = ['pending', 'terminated', 'all'].includes(normalized(params.status)) ? normalized(params.status) : 'pending';
  const scoped = index.filter(row => status === 'all' || row.filters.status === status);
  const q = normalized(params.q).trim();
  const court = normalized(params.court).trim();
  const circuit = normalized(params.circuit).trim();
  const litigationType = normalized(params.litigation_type).trim();
  const resolved = bool(params.judge_resolved);
  const minimum = integer(params.min_pending, null);
  const sort = ['pending_desc', 'mdl_number', 'title'].includes(params.sort) ? params.sort : 'pending_desc';
  const selected = mdlSort(scoped.filter(row => (!q || row.search.includes(q)) &&
    (!court || array(row.filters.court).includes(court)) && (!circuit || row.filters.circuit === circuit) &&
    (!litigationType || row.filters.litigation_type === litigationType) &&
    (resolved == null || row.filters.judge_resolved === String(resolved)) &&
    (minimum == null || (row.item.actions_pending ?? 0) >= minimum)), sort);
  const limit = Math.max(1, Math.min(200, integer(params.limit, 25)));
  const pages = Math.ceil(selected.length / limit);
  const requestedPage = Math.max(1, integer(params.page, 1));
  const page = pages ? Math.min(pages, requestedPage) : requestedPage;
  const facets = {
    litigation_type: counts(scoped.map(row => row.item.litigation_type).filter(Boolean)),
    circuit: counts(scoped.map(row => row.item.circuit).filter(Boolean)),
    court: counts(scoped.map(row => row.item.cl_court_id || row.item.district_code)),
    judge_resolved: counts(scoped.map(row => row.filters.judge_resolved)).sort(([a], [b]) => a.localeCompare(b))
  };
  return { ...baseline, available: true, total: selected.length, page, limit, pages, sort, facets,
    filters: { status, q: q || null, court: court || null, circuit: circuit || null, litigation_type: litigationType || null,
      judge_resolved: resolved, min_pending: minimum },
    results: selected.slice((page - 1) * limit, page * limit).map(row => row.item) };
}
