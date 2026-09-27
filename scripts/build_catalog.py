"""Incremental source catalog; link discovery never implies a downloaded document."""
import collections,csv,datetime,hashlib,json,pathlib,re,sqlite3,urllib.parse
from bs4 import BeautifulSoup

ROOT=pathlib.Path(__file__).resolve().parents[1]
BASE=ROOT/'sources'/'trellis'
OUT=BASE/'catalog'
EXTRACTION_VERSION='2-website-href'

def classify(url):
    p=urllib.parse.urlsplit(url); parts=p.path.strip('/').split('/')
    if p.hostname not in ('trellis.law','www.trellis.law'): return 'external'
    if parts[0]=='coverage': return 'coverage_state' if len(parts)==2 else ('coverage_county' if len(parts)==3 else 'coverage_index')
    return {'state-rules':'state_rule','judges':'judge_directory','judge':'judge_profile','case':'case','doc':'filing','motion-type-dictionary':'motion_dictionary','motion-type':'motion'}.get(parts[0], 'other')

def canonical(url,base=None):
    if not isinstance(url,str) or not url.strip() or any(c in url for c in ('\ufffd','\u2026')) or re.search(r'[\x00-\x20\x7f]',url.strip()):return None
    try:
        p=urllib.parse.urlsplit(urllib.parse.urljoin(base,url.strip()) if base else url.strip())
        if p.scheme not in ('http','https') or not p.hostname or p.username or p.password:return None
        if not re.fullmatch(r'[A-Za-z0-9.-]+',p.hostname) or '..' in p.hostname:return None
        port=p.port
        host=p.hostname.lower() if port is None or (p.scheme,port) in (('http',80),('https',443)) else p.hostname.lower()+':'+str(port)
        return urllib.parse.urlunsplit((p.scheme,host,p.path or '/',p.query,''))
    except ValueError:return None

def extract_website(soup,markdown,source_url,source_path,fields):
    """Prefer an observed Website-field href; display text may be truncated."""
    display=fields.get('website')
    candidates=[]
    region=soup.select_one('#court-records') or soup
    for heading in region.find_all(re.compile('^h[1-6]$')):
        if heading.get_text(' ',strip=True).casefold()!='website':continue
        value=heading.find_next_sibling()
        if value is None:continue
        if display is None:display=value.get_text(' ',strip=True)
        anchors=[value] if value.name=='a' and value.get('href') else value.find_all('a',href=True)
        for anchor in anchors:
            href=anchor.get('href');url=canonical(href,source_url)
            candidates.append({'method':'html_website_field_href','observed_href':href,'url':url,'displayed_text':anchor.get_text(' ',strip=True),'selector':'Website heading > next sibling > a[href]'})
    if not candidates:
        match=re.search(r'#{1,6}\s*WEBSITE\s*\n+(?:#{1,6}\s*)?\[([^]]+)\]\((https?://[^)]+)\)',markdown or '',re.I)
        if match:
            candidates.append({'method':'markdown_website_field_link','observed_href':match[2],'url':canonical(match[2]),'displayed_text':match[1],'selector':'WEBSITE section markdown link'})
            if display is None:display=match[1]
    if not candidates and display:
        candidates.append({'method':'displayed_url_no_href','observed_href':None,'url':canonical(display),'displayed_text':display,'selector':'Website field display text'})
    usable={c['url'] for c in candidates if c['url'] and urllib.parse.urlsplit(c['url']).hostname not in ('trellis.law','www.trellis.law')}
    website=next(iter(usable)) if len(usable)==1 else None
    status='observed_href' if website and any(c['url']==website and c['observed_href'] for c in candidates) else ('displayed_url_only' if website else ('ambiguous_multiple_hrefs' if len(usable)>1 else ('invalid_or_internal_url' if candidates else 'missing')))
    provenance={'source_url':source_url,'source_path':source_path,'source_category':'trellis_public_provider_extract','authority_classification':'trellis_reported_government_site','site_authority_verified':False,'extraction_version':EXTRACTION_VERSION,'candidates':candidates}
    return website,display,status,provenance

def safe_cell(v):
    v='' if v is None else str(v)
    return "'"+v if v.startswith(('=','+','-','@','\t','\r')) else v

def main(root=ROOT):
    ROOT=pathlib.Path(root).resolve();BASE=ROOT/'sources'/'trellis';OUT=BASE/'catalog'
    OUT.mkdir(parents=True,exist_ok=True)
    con=sqlite3.connect(OUT/'catalog.sqlite3');con.row_factory=sqlite3.Row
    con.executescript('''PRAGMA journal_mode=WAL;
    CREATE TABLE IF NOT EXISTS pages(url TEXT PRIMARY KEY,category TEXT,jurisdiction TEXT,title TEXT,status INTEGER,provider TEXT,source_path TEXT,content_sha256 TEXT,markdown_path TEXT,html_path TEXT,scrape_id TEXT,observed_at TEXT,source_mtime REAL);
    CREATE TABLE IF NOT EXISTS links(source_url TEXT,url TEXT,category TEXT,label TEXT,PRIMARY KEY(source_url,url));
    CREATE INDEX IF NOT EXISTS links_url ON links(url);
    CREATE TABLE IF NOT EXISTS counties(url TEXT PRIMARY KEY,state TEXT,county_slug TEXT,title TEXT,fields_json TEXT,official_website TEXT,case_links INTEGER,judge_links INTEGER);
    ''')
    for table,columns in {'pages':{'extraction_version':'TEXT'},'counties':{'official_website_display':'TEXT','website_provenance_json':'TEXT','website_validation_status':'TEXT','extraction_version':'TEXT'}}.items():
        existing={r[1] for r in con.execute('PRAGMA table_info('+table+')')}
        for name,kind in columns.items():
            if name not in existing:con.execute('ALTER TABLE '+table+' ADD COLUMN '+name+' '+kind)
    con.commit()
    processed=0;invalid=0
    for f in BASE.rglob('*.firecrawl.json'):
        try:
            mtime=f.stat().st_mtime
            if con.execute('SELECT 1 FROM pages WHERE source_path=? AND source_mtime=? AND extraction_version=?',(str(f.relative_to(ROOT)),mtime,EXTRACTION_VERSION)).fetchone():continue
            raw=f.read_bytes();payload=json.loads(raw);d=payload.get('data',payload)
            if not isinstance(d,dict):invalid+=1;continue
            meta=d.get('metadata',{});url=meta.get('sourceURL') or meta.get('url')
            if not url:invalid+=1;continue
            url=canonical(url)
            if not url:invalid+=1;continue
            cat=classify(url);parts=urllib.parse.urlsplit(url).path.strip('/').split('/')
            state=parts[1] if len(parts)>1 and parts[0] in ('coverage','state-rules','judges') else None
            digest=hashlib.sha256(raw).hexdigest();artifact=OUT/'extracted'/digest[:2];artifact.mkdir(parents=True,exist_ok=True)
            md_path=artifact/(digest+'.md');md_path.write_text(d.get('markdown',''),encoding='utf-8')
            html_path=None
            if d.get('html'):
                hp=artifact/(digest+'.html');hp.write_text(d['html'],encoding='utf-8');html_path=str(hp.relative_to(ROOT))
            soup=BeautifulSoup(d.get('html',''),'html.parser')
            labels={canonical(a['href'],url):a.get_text(' ',strip=True) for a in soup.select('a[href]') if canonical(a['href'],url)}
            links={canonical(u,url) for u in d.get('links',[]) if canonical(u,url)}
            links.update(labels)
            con.execute('DELETE FROM links WHERE source_url=?',(url,))
            con.executemany('INSERT OR REPLACE INTO links VALUES(?,?,?,?)',[(url,u,classify(u),labels.get(u)) for u in links])
            con.execute('INSERT OR REPLACE INTO pages(url,category,jurisdiction,title,status,provider,source_path,content_sha256,markdown_path,html_path,scrape_id,observed_at,source_mtime,extraction_version) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)',(url,cat,state,meta.get('title'),meta.get('statusCode'),'Firecrawl basic/public',str(f.relative_to(ROOT)),digest,str(md_path.relative_to(ROOT)),html_path,meta.get('scrapeId'),datetime.datetime.fromtimestamp(mtime,datetime.timezone.utc).isoformat(),mtime,EXTRACTION_VERSION))
            if cat=='coverage_county':
                fields={}
                for h in soup.select('#court-records h2'):
                    value=h.find_next_sibling()
                    if value:fields[h.get_text(' ',strip=True).lower()]=value.get_text(' ',strip=True)
                website,website_display,website_status,website_provenance=extract_website(soup,d.get('markdown',''),url,str(f.relative_to(ROOT)),fields)
                con.execute('INSERT OR REPLACE INTO counties(url,state,county_slug,title,fields_json,official_website,case_links,judge_links,official_website_display,website_provenance_json,website_validation_status,extraction_version) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)',(url,state,parts[2],meta.get('title'),json.dumps(fields),website,sum(classify(u)=='case' for u in links),sum(classify(u)=='judge_profile' for u in links),website_display,json.dumps(website_provenance,ensure_ascii=False),website_status,EXTRACTION_VERSION))
            processed+=1
            if processed%25==0:con.commit()
        except (json.JSONDecodeError,OSError,TypeError,KeyError,ValueError) as exc:
            invalid+=1
    con.commit()
    for name,query in {
        'downloaded_pages':'SELECT * FROM pages ORDER BY category,jurisdiction,url',
        'county_profiles':'SELECT * FROM counties ORDER BY state,county_slug',
        'discovered_urls':'SELECT url,category,count(*) AS linked_from_pages FROM links GROUP BY url,category ORDER BY category,url',
        'case_urls':"SELECT l.url,l.category,l.source_url AS discovered_from,p.jurisdiction FROM links l LEFT JOIN pages p ON p.url=l.source_url WHERE l.category='case' GROUP BY l.url ORDER BY l.url",
        'judge_urls':"SELECT DISTINCT url,category FROM links WHERE category IN ('judge_directory','judge_profile') ORDER BY category,url",
        'rule_urls':"SELECT DISTINCT url,category FROM links WHERE category='state_rule' ORDER BY url",
        'official_county_websites':"SELECT url AS discovered_from,state,county_slug,official_website AS url,official_website_display AS displayed_value,website_validation_status,website_provenance_json,extraction_version FROM counties WHERE official_website IS NOT NULL ORDER BY state,county_slug"
    }.items():
        rows=[dict(r) for r in con.execute(query)]
        with (OUT/(name+'.jsonl')).open('w',encoding='utf-8') as fh:
            for r in rows:fh.write(json.dumps(r,ensure_ascii=False)+'\n')
        if rows:
            with (OUT/(name+'.csv')).open('w',encoding='utf-8-sig',newline='') as fh:
                w=csv.DictWriter(fh,fieldnames=rows[0].keys());w.writeheader();w.writerows({k:safe_cell(v) for k,v in r.items()} for r in rows)
    summary={'updated_at':datetime.datetime.now(datetime.timezone.utc).isoformat(),'extraction_version':EXTRACTION_VERSION,'full_corpus_complete':False,'processed_this_pass':processed,'unparsed_provider_files':invalid,
             'downloaded_pages_by_category':{r[0]:r[1] for r in con.execute('SELECT category,count(*) FROM pages WHERE status BETWEEN 200 AND 299 GROUP BY category')},
             'discovered_urls_by_category':{r[0]:r[1] for r in con.execute('SELECT category,count(DISTINCT url) FROM links GROUP BY category')},
             'county_profiles':con.execute('SELECT count(*) FROM counties').fetchone()[0],
             'county_website_links':con.execute('SELECT count(*) FROM counties WHERE official_website IS NOT NULL').fetchone()[0],
             'county_website_extraction_statuses':{r[0]:r[1] for r in con.execute('SELECT website_validation_status,count(*) FROM counties GROUP BY website_validation_status')},
             'notes':['Public Firecrawl extracts are provider-rendered representations; SHA256 identifies the saved provider response, not the original HTTP response.','Discovered URLs and downloaded pages are distinct. County cases displayed on coverage pages are not an exhaustive inventory.','Trellis account currently covers CA and IL only; see browser/access_observations.json.','Downloaded legal text retains its source date and may have been amended. No assertion of legal currency or corpus completeness.']}
    (OUT/'summary.json').write_text(json.dumps(summary,indent=2),encoding='utf-8')
    print(json.dumps(summary,indent=2));con.close()

if __name__=='__main__':main()
