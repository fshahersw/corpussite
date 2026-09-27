/* Real US map: every state selectable, every county border drawn. Plain SVG from the open us-atlas geometry
   (assets/us-counties-albers-10m.json, pre-projected Albers USA 975 x 610), decoded here without any library.
   window.UsMap.national(container, options) and window.UsMap.state(container, usps, options). */
(function () {
  const FIPS = { '01': 'AL', '02': 'AK', '04': 'AZ', '05': 'AR', '06': 'CA', '08': 'CO', '09': 'CT', '10': 'DE', '11': 'DC', '12': 'FL', '13': 'GA', '15': 'HI', '16': 'ID', '17': 'IL', '18': 'IN', '19': 'IA',
    '20': 'KS', '21': 'KY', '22': 'LA', '23': 'ME', '24': 'MD', '25': 'MA', '26': 'MI', '27': 'MN', '28': 'MS', '29': 'MO', '30': 'MT', '31': 'NE', '32': 'NV', '33': 'NH', '34': 'NJ', '35': 'NM', '36': 'NY',
    '37': 'NC', '38': 'ND', '39': 'OH', '40': 'OK', '41': 'OR', '42': 'PA', '44': 'RI', '45': 'SC', '46': 'SD', '47': 'TN', '48': 'TX', '49': 'UT', '50': 'VT', '51': 'VA', '53': 'WA', '54': 'WV', '55': 'WI', '56': 'WY' };
  const USPS_FIPS = Object.fromEntries(Object.entries(FIPS).map(([f, u]) => [u, f]));
  const NS = 'http://www.w3.org/2000/svg';
  const RAMP = ['#eef2ee', '#d5e6dc', '#aed0bd', '#7fb399', '#4f9277', '#2a6f5a'];
  const LEVEL_FILL = { rules_and_filing: '#2a6f5a', statewide_rules_and_filing: '#7fb399', rules_only: '#b9d6c5', filing_only: '#dbe9e0', none: '#f1f3ef' };
  const LEVEL_LABEL = { rules_and_filing: 'Local rules and filing sources', statewide_rules_and_filing: 'Statewide rules and filing sources', rules_only: 'Rules source only', filing_only: 'Filing sources only', none: 'Nothing recorded yet' };
  let geometry = null, pending = null;

  function injectStyle() { /* styles live in styles.css: the page's content security policy does not allow injected style blocks */ }

  function decode(topology) {
    const [kx, ky] = topology.transform.scale, [dx, dy] = topology.transform.translate;
    const arcs = topology.arcs.map(arc => { let x = 0, y = 0; return arc.map(([a, b]) => { x += a; y += b; return [x * kx + dx, y * ky + dy]; }); });
    const shape = g => {
      const polygons = g.type === 'Polygon' ? [g.arcs] : g.type === 'MultiPolygon' ? g.arcs : [];
      let d = '', minX = Infinity, minY = Infinity, maxX = -Infinity, maxY = -Infinity;
      for (const polygon of polygons) for (const ring of polygon) {
        const points = [];
        for (const index of ring) { const arc = index < 0 ? arcs[~index].slice().reverse() : arcs[index]; for (let i = points.length ? 1 : 0; i < arc.length; i++) points.push(arc[i]); }
        for (const [x, y] of points) { if (x < minX) minX = x; if (x > maxX) maxX = x; if (y < minY) minY = y; if (y > maxY) maxY = y; }
        d += 'M' + points.map(p => p[0].toFixed(1) + ',' + p[1].toFixed(1)).join('L') + 'Z';
      }
      return { d, box: [minX, minY, maxX, maxY] };
    };
    const states = {}, counties = {};
    for (const g of topology.objects.states.geometries) states[g.id] = { ...shape(g), name: g.properties.name, usps: FIPS[g.id] };
    for (const g of topology.objects.counties.geometries) counties[g.id] = { ...shape(g), name: g.properties.name, state: g.id.slice(0, 2) };
    return { states, counties, nation: shape(topology.objects.nation.geometries[0]) };
  }

  function load() {
    if (geometry) return Promise.resolve(geometry);
    if (!pending) pending = fetch('/assets/us-counties-albers-10m.json').then(r => { if (!r.ok) throw new Error('map geometry unavailable'); return r.json(); }).then(t => (geometry = decode(t)));
    return pending;
  }

  function svg(viewBox, label) { const s = document.createElementNS(NS, 'svg'); s.setAttribute('viewBox', viewBox); s.setAttribute('role', 'group'); s.setAttribute('aria-label', label); return s; }
  function path(d, cls) { const p = document.createElementNS(NS, 'path'); p.setAttribute('d', d); p.setAttribute('class', cls); return p; }
  function tooltip(host) {
    const tip = document.createElement('div'); tip.className = 'usmap-tip'; tip.hidden = true; host.append(tip);
    return { show(event, title, text) { tip.replaceChildren(); const strong = document.createElement('strong'); strong.textContent = title; tip.append(strong); if (text) { const span = document.createElement('span'); span.textContent = text; tip.append(span); }
        const box = host.getBoundingClientRect(); tip.style.left = Math.min(box.width - 20, Math.max(20, event.clientX - box.left)) + 'px'; tip.style.top = (event.clientY - box.top) + 'px'; tip.hidden = false; }, hide() { tip.hidden = true; } };
  }
  function interactive(node, label, onSelect, tip, title, text) {
    node.setAttribute('tabindex', '0'); node.setAttribute('role', 'link'); node.setAttribute('aria-label', label);
    node.addEventListener('click', onSelect); node.addEventListener('keydown', e => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); onSelect(); } });
    node.addEventListener('mousemove', e => tip.show(e, title, text)); node.addEventListener('mouseleave', tip.hide); node.addEventListener('blur', tip.hide);
  }
  function legend(entries) { const box = document.createElement('div'); box.className = 'usmap-legend'; for (const [color, text] of entries) { const span = document.createElement('span'); const i = document.createElement('i'); i.style.background = color; span.append(i, document.createTextNode(text)); box.append(span); } return box; }
  function bucket(value, max) { if (!value || !max) return 0; return Math.max(1, Math.min(5, Math.ceil(5 * Math.sqrt(value) / Math.sqrt(max)))); }

  /* options: { metrics: [{key,label,values:{USPS:number},format?:fn,unit?:string}], onState(usps), countyLines:true } */
  async function national(container, options) {
    injectStyle(); const g = await load(); container.replaceChildren();
    const host = document.createElement('div'); host.className = 'usmap'; const tip = tooltip(host);
    const bar = document.createElement('div'); bar.className = 'usmap-bar';
    const metrics = options.metrics || []; let active = metrics[0];
    const choose = document.createElement('label'); choose.textContent = 'Shade states by '; const select = document.createElement('select');
    metrics.forEach((m, i) => { const o = document.createElement('option'); o.value = String(i); o.textContent = m.label; select.append(o); }); choose.append(select);
    const lines = document.createElement('label'); const box = document.createElement('input'); box.type = 'checkbox'; box.checked = options.countyLines !== false; lines.append(box, document.createTextNode('County borders'));
    bar.append(choose, lines);
    const s = svg('0 0 975 610', 'Map of the United States; select a state'); const stateLayer = document.createElementNS(NS, 'g'), countyLayer = document.createElementNS(NS, 'g');
    const nodes = {};
    for (const [fips, st] of Object.entries(g.states)) { if (!st.usps) continue; const p = path(st.d, 'm-state'); nodes[st.usps] = p; stateLayer.append(p); }
    countyLayer.append(path(Object.values(g.counties).map(c => c.d).join(''), 'm-county-line'));
    s.append(stateLayer, countyLayer, path(Object.values(g.states).map(st => st.d).join(''), 'm-state-line'), path(g.nation.d, 'm-outline'));
    const legendHost = document.createElement('div');
    function paint() {
      const values = (active && active.values) || {}; const max = Math.max(0, ...Object.values(values).map(Number).filter(Number.isFinite));
      for (const [usps, node] of Object.entries(nodes)) {
        const value = Number(values[usps]) || 0; node.setAttribute('fill', RAMP[bucket(value, max)]);
        const name = g.states[USPS_FIPS[usps]].name; const text = active ? `${active.format ? active.format(value) : value.toLocaleString()} ${active.unit || active.label.toLowerCase()}` : '';
        const fresh = node.cloneNode(false); node.replaceWith(fresh); nodes[usps] = fresh; interactive(fresh, `${name}: ${text}`, () => options.onState && options.onState(usps), tip, name, text);
      }
      legendHost.replaceChildren(legend([[RAMP[0], 'none'], [RAMP[1], 'fewer'], [RAMP[3], ''], [RAMP[5], active ? 'more ' + (active.unit || active.label.toLowerCase()) : 'more']]));
      countyLayer.style.display = box.checked ? '' : 'none';
    }
    select.addEventListener('change', () => { active = metrics[Number(select.value)]; paint(); }); box.addEventListener('change', paint);
    host.append(s); container.append(bar, host, legendHost); paint();
  }

  /* options: { counties: {fips:{level,name,sources,unit}}, saved:{fips:number}, onCounty(fips) } */
  async function state(container, usps, options) {
    injectStyle(); const g = await load(); const fips = USPS_FIPS[String(usps).toUpperCase()]; if (!fips || !g.states[fips]) return false;
    container.replaceChildren(); const host = document.createElement('div'); host.className = 'usmap'; const tip = tooltip(host);
    let [x0, y0, x1, y1] = g.states[fips].box; const pad = Math.max(x1 - x0, y1 - y0) * 0.04; x0 -= pad; y0 -= pad; x1 += pad; y1 += pad;
    const s = svg(`${x0.toFixed(1)} ${y0.toFixed(1)} ${(x1 - x0).toFixed(1)} ${(y1 - y0).toFixed(1)}`, `Counties of ${g.states[fips].name}; select a county`);
    const info = options.counties || {}, saved = options.saved || {}; const used = new Set();
    for (const [id, county] of Object.entries(g.counties)) {
      if (county.state !== fips) continue; const row = info[id] || {}; const level = row.level || 'none'; used.add(level);
      const p = path(county.d, 'm-county'); p.setAttribute('fill', LEVEL_FILL[level] || LEVEL_FILL.none);
      const parts = [LEVEL_LABEL[level] || '']; if (row.unit) parts.push(row.unit); if (row.sources) parts.push(`${row.sources} source link${row.sources === 1 ? '' : 's'}`); if (saved[id]) parts.push(`${saved[id]} saved here`);
      interactive(p, `${row.name || county.name}: ${parts.join(', ')}`, () => options.onCounty && options.onCounty(id), tip, row.name || county.name, parts.filter(Boolean).join(' · '));
      s.append(p);
    }
    s.append(path(g.states[fips].d, 'm-outline')); host.append(s);
    container.append(host, legend(['rules_and_filing', 'statewide_rules_and_filing', 'rules_only', 'filing_only', 'none'].filter(l => used.has(l) || l === 'none').map(l => [LEVEL_FILL[l], LEVEL_LABEL[l]])));
    return true;
  }

  window.UsMap = { national, state, load, FIPS, USPS_FIPS };
})();
