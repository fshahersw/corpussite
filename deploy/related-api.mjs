/* Finite, validated related blocks and a port of the saved courts-db matcher. */
const cache = new WeakMap();
const clean = value => String(value ?? '').replace(/\s+/g, ' ').trim();
const sorted = values => [...new Set(values)].sort().slice(0, 12);

function compiled(data) {
  if (!cache.has(data)) cache.set(data, data.patterns.map(row => ({ ...row, regex: new RegExp(row.pattern, 'i') })));
  return cache.get(data);
}

export function findCourtIds(data, text, bankruptcy, partial = false) {
  let matches = [];
  for (const row of compiled(data)) {
    if (bankruptcy === true && row.type !== 'bankruptcy' || bankruptcy === false && row.type === 'bankruptcy') continue;
    const match = row.regex.exec(text);
    if (match && (partial || match[0].length === text.length)) matches.push({ text: match[0], id: row.id, parent: row.parent });
  }
  if (!matches.length) {
    matches = data.native_courts.filter(row => clean(row.name).toLowerCase() === clean(text).toLowerCase())
      .map(row => ({ text, id: row.id, parent: row.parent }));
  }
  const parents = new Set(matches.map(row => row.parent));
  if (matches.length > 1) matches = matches.filter(row => !parents.has(row.id));
  matches = matches.filter(row => !matches.some(other => row.text !== other.text && other.text.includes(row.text)));
  let ids = new Set(matches.map(row => row.id));
  if (bankruptcy != null) ids = new Set(data.native_courts.filter(row => ids.has(row.id) && (row.type === 'bankruptcy') === bankruptcy).map(row => row.id));
  if (ids.size > 1) {
    const parentIds = new Set(data.native_courts.filter(row => ids.has(row.id) && ids.has(row.parent)).map(row => row.parent));
    ids = new Set([...ids].filter(id => !parentIds.has(id)));
  }
  return sorted(ids);
}

export function resolveCourt(data, query) {
  const text = clean(query).slice(0, 300);
  if (!data?.available || !text) return { available: Boolean(data?.available), reason: data?.reason || 'nothing to resolve', query: text, results: [] };
  let basis = 'citation abbreviation, exactly as published';
  let ids = Object.values(data.courts).filter(row => row.cited_as && row.cited_as.toLowerCase() === text.toLowerCase()).slice(0, 12).map(row => row.id);
  if (!ids.length) {
    const bankruptcy = text.toLowerCase().includes('bankr');
    try {
      ids = findCourtIds(data, text, bankruptcy); basis = 'whole-name pattern match';
      if (!ids.length) ids = findCourtIds(data, text, null);
      if (!ids.length) { ids = findCourtIds(data, text, bankruptcy, true); basis = 'court name found inside the text (partial match)'; }
    } catch {
      return { available: false, reason: 'Saved court name matcher is incompatible', query: text, results: [] };
    }
  }
  return { available: true, query: text, results: ids.map(id => data.courts[id] ?? { id, name: id, cited_as: '', place: '', system: '', level: '', in_registry: false, link: '' }),
    basis, note: 'More than one result means the text is ambiguous (for example a district court and its bankruptcy court).' };
}

export async function handleRelated(path, params, context) {
  if (path === '/api/court-resolve') return resolveCourt(await context.context('related:court-matcher'), params.q);
  if (path === '/api/citations/record') {
    const key = String(params.id ?? '').trim().slice(0, 120);
    const value = key ? await context.context('related:citations:' + key) : null;
    if (value) return value;
    const status = await context.context('related:citations:status');
    return { available: Boolean(status?.available && key), total: 0, results: [] };
  }
  if (path === '/api/blocks') {
    if (params.state) return await context.context('related:blocks:state:' + String(params.state).toUpperCase()) ??
      { state_proceedings: null, court_documents: null, saved_pages: null, limitation_periods: null };
    if (params.court) return await context.context('related:blocks:court:' + params.court) ?? { court_documents: null, urls: null };
    return {};
  }
  if (path === '/api/urls/block') {
    for (const key of ['state', 'agency', 'court']) if (params[key]) {
      return { block: await context.context('related:urls:' + key + ':' + (key === 'state' ? params[key].toUpperCase() : params[key])) };
    }
    return { block: null };
  }
  return null;
}
