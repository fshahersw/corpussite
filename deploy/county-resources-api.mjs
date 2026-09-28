/* County resources use source-native title/venue search and scoped facets. */
const number = (value, fallback, max) => /^\d+$/.test(String(value ?? '')) ? Math.min(max, Math.max(1, Number(value))) : fallback;
const lower = value => String(value ?? '').toLowerCase().replaceAll('ß', 'ss');

export async function handleCountyResources(path, params, context) {
  if (!['/api/county-litigation', '/api/county-litigation-record', '/api/county-litigation-asset'].includes(path)) return null;
  if (path === '/api/county-litigation-asset') return context.asset(path + '?' + new URLSearchParams(params));
  if (path === '/api/county-litigation-record') return await context.detail(params.id, ['county_litigation']) ?? Response.json({ error: 'County resource not found or not published' }, { status: 404 });
  const dataset = await context.dataset('county_litigation');
  const page = number(params.page, 1, 100000), limit = number(params.limit, 24, 100);
  if (!dataset?.ready) return { available: false, total: 0, items: [], page, limit, facets: { resource_types: [], availability: [] }, county_geoid: params.geoid || null };
  const metadata = dataset.metadata ?? dataset;
  const base = metadata.listing ?? {};
  // The county reader sends full state names; saved county resources retain USPS
  // abbreviations. Resolve both only through the published jurisdiction aliases.
  const aliases = params.state ? (await context.context?.('state:aliases')) ?? {} : {};
  const stateKey = value => lower(aliases[lower(value).trim()] ?? value).trim();
  const scoped = (metadata.filter_index ?? []).filter(row => (!params.state || stateKey(row.state) === stateKey(params.state)) &&
    (!params.geoid || row.county_geoids.includes(params.geoid)));
  const facets = Object.fromEntries([['resource_types', 'resource_type'], ['availability', 'availability']].map(([name, field]) => [name,
    (base.facets?.[name] ?? []).map(option => ({ ...option, count: scoped.filter(row => row[field] === option.value).length })).filter(option => option.count)]));
  const kind = params.resource_type || params.category;
  const term = lower(params.q).trim();
  const matched = scoped.filter(row => (kind ? row.resource_type === kind : params.include_directories === '1' || !['source_directory', 'unknown'].includes(row.resource_type)) &&
    (!params.availability || row.availability === params.availability) &&
    (!params.document_shape || row.document_shape === params.document_shape) &&
    (!term || lower([row.title, row.county, row.state, row.resource_type].join(' ')).includes(term)));
  // Export order already follows the native (case-folded title, id) order.
  const ids = matched.slice((page - 1) * limit, page * limit).map(row => row.id);
  let items = [];
  if (ids.length) {
    const data = await context.query({ datasets: ['county_litigation'], filters: { __ids: ids }, q: '', limit: ids.length, offset: 0, sort: 'title' });
    const byId = new Map(data.items.map(row => [row.id, row]));
    items = ids.map(id => byId.get(id)).filter(Boolean);
  }
  return { ...base, available: true, total: matched.length, items, page, limit, facets, county_geoid: params.geoid || null };
}
