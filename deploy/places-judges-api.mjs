import {composeJudge} from './research-compose.mjs';
/* Native county/judge/biography contracts over the migrated public projections. */
import portraitMembership from './judge-portrait-membership.json' with { type: 'json' };
const normalize = value => String(value ?? '').normalize('NFKD').replace(/\p{M}/gu, '').toLowerCase().replaceAll('ß', 'ss');
const number = (value, fallback, maximum) => {
  const parsed = Number.parseInt(value, 10);
  return Number.isFinite(parsed) ? Math.min(maximum, Math.max(1, parsed)) : fallback;
};
const metadata = row => row?.metadata ?? row ?? {};
const error = (message, status = 404) => Response.json({ error: message }, { status });
const equal = (left, right) => normalize(left) === normalize(right);
const array = value => Array.isArray(value) ? value : value == null ? [] : [value];
const includes = (values, wanted, insensitive = false) => array(values).some(value => insensitive ? equal(value, wanted) : value === wanted);
const canonical = (value, choices) => choices.find(choice => equal(choice, value)) ?? value;

export function validatedPortraitIds(meta, membership = portraitMembership) {
  // Older native feature flags predate the validated portrait overlay. The
  // frozen export supplies exact judge-to-route pairs; never infer from names.
  if (meta.export_jsonl_sha256 !== membership.source_export_sha256 || !Array.isArray(meta.validated_portrait_routes) || !Array.isArray(meta.filter_index)) return null;
  const routes = new Set(meta.validated_portrait_routes), ids = new Set(meta.filter_index.map(row => row.id));
  return new Set(membership.entries.filter(([id, route]) => ids.has(id) && routes.has(route)).map(([id]) => id));
}

export function judgeSearch(row, query) {
  const source = normalize(query).slice(0, 200);
  const words = source.split(/\s+/).filter(Boolean);
  if (!words.length) return true;
  if (words.every(word => String(row.search ?? '').includes(word))) return true;
  const aliasWords = source.replace(/[^0-9a-z]+/g, ' ').trim().split(/\s+/).filter(Boolean);
  return aliasWords.length > 0 && (row.aliases ?? []).some(alias => {
    const text = normalize(alias).replace(/[^0-9a-z]+/g, ' ');
    return aliasWords.every(word => text.includes(word));
  });
}

export function personSearch(row, query) {
  const wanted = normalize(query).slice(0, 200).match(/[\p{L}\p{N}]+/gu) ?? [];
  if (!String(query ?? '').trim()) return true;
  if (!wanted.length) return false;
  const tokens = normalize(row.search).match(/[\p{L}\p{N}]+/gu) ?? [];
  return wanted.slice(0, 12).every(word => tokens.some(token => token.startsWith(word)));
}

function judgesMatch(row, params, { omitCourt = false, photoIds = null } = {}) {
  const filters = row.filters ?? {};
  if (!judgeSearch(row, params.q)) return false;
  for (const key of ['state', 'system', 'court', 'has', 'status', 'role', 'president']) {
    if (omitCourt && key === 'court') continue;
    if (key === 'has' && params.has === 'photo' && photoIds !== null) { if (!photoIds.has(row.id)) return false; continue; }
    if (key === 'has' && !['details', 'photo', 'biography', 'analysis', 'reports'].includes(params.has)) continue;
    if (params[key] && !includes(filters[key], params[key], ['state', 'system', 'court'].includes(key))) return false;
  }
  return true;
}

function countyMatches(row, params) {
  if (params.state && row.filters?.state !== params.state) return false;
  const term = String(params.q ?? '').slice(0, 200);
  return !term || String(row.id) === term || String(row.name).toLowerCase().includes(term.toLowerCase());
}

export async function handlePlacesJudges(path, params, context) {
  if (!['/api/counties', '/api/judges', '/api/judge', '/api/people', '/api/person'].includes(path)) return null;
  const dataset = path.startsWith('/api/count') ? 'counties' : path.startsWith('/api/judg') ? 'judges' : 'people';
  const record = await context.dataset(dataset);
  if (!record || record.ready === false) return Response.json({error:'This directory is not published yet.',code:'publication_pending'}, {status:503});
  const meta = metadata(record);
  const baseline = meta.listing ?? {};
  const index = meta.filter_index ?? [];

  if (path === '/api/judge') {
    const key = meta.id_aliases?.[params.id] ?? params.id;
    const profile=await context.detail(key, ['judges'], {full:false});
    return profile ? (profile.entity_id ? await composeJudge(profile,context) : profile) : error('Judge profile not found');
  }
  if (path === '/api/person') {
    if (!/^[1-9][0-9]{0,11}$/.test(params.id ?? '')) return error('Biographical record not found');
    return await context.detail(params.id, ['people'], { full: false }) ?? error('Biographical record not found');
  }

  if (path === '/api/counties') {
    const page = number(params.page, 1, 1000000);
    const limit = number(params.limit, 50, 100);
    const filters = {};
    if (params.state) filters.state = params.state;
    if (params.availability) filters.availability = params.availability;
    const matches = index.filter(row => countyMatches(row, params));
    if (params.q) filters.id = matches.map(row => row.id);
    const result = await context.query({ datasets: ['counties'], filters, q: '', limit, offset: (page - 1) * limit, sort: 'rank' });
    const availabilities = (baseline.availabilities ?? []).map(facet => ({ ...facet,
      count: matches.filter(row => includes(row.filters?.availability, facet.value)).length }));
    return { ...baseline, ...result, page, limit, availabilities };
  }

  if (path === '/api/judges') {
    const page = number(params.page, 1, 1000000);
    const limit = number(params.limit, 24, 60);
    const filters = {};
    for (const key of ['state', 'system', 'court', 'status', 'role', 'president']) {
      if (!params[key]) continue;
      const choices = key === 'state' ? baseline.states ?? [] : key === 'system' ? baseline.systems ?? [] :
        key === 'court' ? (baseline.courts ?? []).map(row => row.value) : [];
      filters[key] = choices.length ? canonical(params[key], choices) : params[key];
    }
    const photoIds = params.has === 'photo' ? validatedPortraitIds(meta) : null;
    if (photoIds !== null) filters.__ids = [...photoIds];
    else if (['details', 'photo', 'biography', 'analysis', 'reports'].includes(params.has)) filters.has = params.has;
    // Match the saved native substring/alias index, rather than interpreting a
    // partial judge name as a PostgreSQL whole-word full-text query.
    if (params.q) filters.id = index.filter(row => judgeSearch(row, params.q)).map(row => row.id);
    const result = await context.query({ datasets: ['judges'], filters, q: '', limit,
      offset: (page - 1) * limit, sort: params.sort === 'name' ? 'title' : 'rank' });
    const counts = new Map();
    for (const row of index.filter(row => judgesMatch(row, params, { omitCourt: true, photoIds }))) {
      for (const court of new Set(row.filters?.court ?? [])) counts.set(court, (counts.get(court) ?? 0) + 1);
    }
    const courts = [...counts].sort(([a], [b]) => a.toLowerCase() < b.toLowerCase() ? -1 : a.toLowerCase() > b.toLowerCase() ? 1 : 0)
      .map(([value, count]) => ({ value, label: value, count }));
    if (params.court && !courts.some(row => row.value === params.court)) courts.push({ value: params.court, label: params.court, count: 0 });
    return { ...baseline, ...result, page, limit, courts };
  }

  const limit = number(params.limit, 30, 60);
  const offset = Math.min(1000000, Math.max(0, Number.parseInt(params.offset, 10) || 0));
  const query = String(params.q ?? '').trim().slice(0, 200);
  const court = String(params.court ?? '').slice(0, 100);
  const includeAliases = params.include_aliases === '1';
  const filters = {};
  if (!includeAliases) filters.is_alias = '0';
  if (court) filters.court = court;
  if (query) filters.id = index.filter(row => personSearch(row, query)).map(row => row.id);
  const result = await context.query({ datasets: ['people'], filters, q: '', limit, offset, sort: 'rank' });
  return { ...baseline, ...result, ready: true, offset, limit,
    filters: { q: query, court, include_aliases: includeAliases } };
}
