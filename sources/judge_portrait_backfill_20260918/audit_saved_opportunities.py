"""Bounded read-only image-reference audit of named saved judge sources."""
import collections, hashlib, html, json, re, sqlite3, time
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urljoin, urlsplit

ROOT = Path(__file__).resolve().parents[2]
OUT = Path(__file__).resolve().parent
started = time.monotonic()
latest = json.loads((ROOT/'delivery/judge_enrichment_20260914/latest.json').read_text(encoding='utf-8'))
database = ROOT/latest['snapshot_path']/'judge_corpus.sqlite3'
c = sqlite3.connect(database.as_uri()+'?mode=ro', uri=True)
c.row_factory = sqlite3.Row
observations = [(r['observation_id'], r['source_class'], json.loads(r['payload']))
                for r in c.execute('SELECT observation_id,source_class,payload FROM observations')]
c.close()
candidates = []; inspected = []; issues = []; paths_seen = set(); generic_images = 0
classes = collections.Counter(); pdf_candidates = []
for oid, cls, p in observations:
    classes[cls] += 1
    path = p.get('native_record', {}).get('source_path') or p.get('source_path')
    if not path or path in paths_seen or not path.lower().endswith(('.json','.html','.htm')): continue
    if time.monotonic()-started > 90:
        issues.append('Ninety-second scan bound reached; remaining source paths not scanned.'); break
    source = ROOT/path
    if not source.is_file() or source.stat().st_size > 8_000_000: continue
    paths_seen.add(path)
    raw = source.read_bytes(); digest = hashlib.sha256(raw).hexdigest()
    try: d = json.loads(raw.decode('utf-8-sig')) if source.suffix=='.json' else {'html':raw.decode('utf-8',errors='replace')}
    except ValueError: issues.append({'path':path,'issue':'invalid JSON'}); continue
    if 'data' in d and isinstance(d['data'],dict): d=d['data']
    markup=d.get('html') or d.get('rawHtml') or ''; markdown=d.get('markdown') or ''
    references=[]
    for tag in re.findall(r'<img\b[^>]*>', markup, re.I):
        attrs={k.lower():html.unescape(v) for k,_,v in re.findall(r'([\w:-]+)\s*=\s*([\"\'])(.*?)\2',tag,re.S)}
        src=attrs.get('src') or attrs.get('data-src')
        if src:references.append((src,attrs.get('alt',''),'saved HTML img'))
    references += [(url,alt,'saved Markdown image') for alt,url in re.findall(r'!\[([^\]]*)\]\((https?://[^\s)]+)\)',markdown)]
    profile_url=p.get('source_url') or ''; surname=re.findall(r'[a-z]+',p.get('name','').lower())
    surname=next((s for s in reversed(surname) if s not in {'jr','sr','ii','iii','iv'}),'')
    kept=0
    for url,alt,basis in references:
        url=urljoin(profile_url,url)
        if urlsplit(url).scheme not in {'https','http'}:continue
        if re.search(r'logo|favicon|icon|blank|default|placeholder|spacer|gavel|loader|spinner|word-motto|dashboard-at-a-glance',url+' '+alt,re.I):generic_images+=1;continue
        image_tokens=re.findall(r'[a-z]+',(alt+' '+urlsplit(url).path.rsplit('/',1)[-1]).lower())
        if len(surname)<4 or surname in {'judge','justice'} or surname not in image_tokens:continue
        candidates.append({'observation_id':oid,'source_class':cls,'name_as_reported':p.get('name'),
            'courts_as_reported':p.get('courts'),'source_url':profile_url,'image_url_observed':url,
            'image_alt_as_reported':alt,'evidence_kind':basis,'source_path':path,'source_sha256':digest,
            'verification_status':'unverified_candidate_only','identity_link_accepted':False,
            'image_bytes_downloaded':False,'next_step':'Inspect source image context and verify image is a portrait; resolve identity with explicit court and independent career/native-ID facts before linking.'})
        kept+=1
    inspected.append({'path':path,'sha256':digest,'source_class':cls,'observation_id':oid,
                      'image_tags_or_markdown_refs':len(references),'candidate_references':kept})

# Check only the existing named evaluation pages in the one already saved PDF.
pdf_summary={'inspected':False}
try:
    import fitz
    evaluations=[(oid,p) for oid,cls,p in observations if cls=='state_evaluations']
    if evaluations:
        pdf_path=ROOT/evaluations[0][1]['native_record']['source_path']
        doc=fitz.open(pdf_path); pages={}
        for oid,p in evaluations:
            for page_number in p['native_record'].get('source_pages',[]):
                if 1 <= page_number <= len(doc):pages.setdefault(page_number,[]).append({'observation_id':oid,'name':p['name']})
        image_pages=0
        for number,names in pages.items():
            page=doc[number-1]; images=page.get_images(full=True)
            if images:image_pages+=1
            for image in images:
                if image[2] >= 60 and image[3] >= 60:
                    pdf_candidates.append({'source_path':pdf_path.relative_to(ROOT).as_posix(),'source_url':evaluations[0][1]['source_url'],
                        'page':number,'image_xref':image[0],'width':image[2],'height':image[3],
                        'named_page_observations':names,'verification_status':'unverified_page_image_not_asserted_portrait',
                        'identity_link_accepted':False,'image_extracted':False})
        pdf_summary={'inspected':True,'path':pdf_path.relative_to(ROOT).as_posix(),'sha256':hashlib.sha256(pdf_path.read_bytes()).hexdigest(),
            'named_pages_inspected':len(pages),'pages_with_images':image_pages,'large_image_candidates':len(pdf_candidates)}
        doc.close()
except (ImportError,OSError,KeyError,ValueError) as error:pdf_summary={'inspected':False,'reason':type(error).__name__+': '+str(error)}

result={'completed_at':datetime.now(timezone.utc).isoformat(),'elapsed_seconds':round(time.monotonic()-started,2),
    'scope':'Existing published judge observations and their directly referenced saved JSON/HTML; named evaluation pages of the existing Colorado PDF. No whole-disk scan, network request, image acquisition or face identification.',
    'observation_database':database.relative_to(ROOT).as_posix(),'observation_class_counts':dict(classes),
    'saved_json_html_files_inspected':len(inspected),'source_files':inspected,
    'generic_image_references_excluded':generic_images,'explicit_named_image_reference_candidates':candidates,
    'pdf_check':pdf_summary,'unverified_pdf_page_image_candidates':pdf_candidates,'issues':issues,
    'candidate_count':len(candidates),'candidate_reference_count':len(candidates),
    'distinct_candidate_image_urls':len({x['image_url_observed'] for x in candidates}),
    'distinct_candidate_observations':len({x['observation_id'] for x in candidates}),
    'pdf_page_image_candidate_count':len(pdf_candidates),
    'new_portraits_verified':0,'identity_links_added':0,'network_requests':0,
    'known_integrated_portraits':14,'national_portrait_coverage_complete':False,
    'method_limits':['Checks explicit HTML img and Markdown image references in directly referenced saved captures, not CSS backgrounds or all possible external source pages.','Image candidates require a whole name token in alt text or image filename; a surname substring in the Trellis domain is not identity evidence.','Existing generic analytics thumbnails, logos, mottos and spinner images are excluded.','PDF inspection checks embedded image objects on known evaluation pages without extraction or facial identification.'],
    'conclusion':'No additional source-bound portrait opportunity found in inspected evidence.' if not candidates and not pdf_candidates else 'Unverified source-bound image opportunities found; image-content and identity verification remain required.'}
(OUT/'remaining_source_opportunities.json').write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
print(json.dumps({k:v for k,v in result.items() if k not in {'source_files','explicit_named_image_reference_candidates','unverified_pdf_page_image_candidates'}},indent=2))
