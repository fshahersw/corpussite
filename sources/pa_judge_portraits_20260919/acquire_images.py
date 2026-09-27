"""Bounded acquisition of exact, previously verified official PA portrait URLs."""
from pathlib import Path
import datetime,hashlib,io,json,os,sqlite3,sys,time,urllib.parse,urllib.robotparser,uuid,warnings
import truststore
truststore.inject_into_ssl()  # Windows trust store; certificate validation remains enabled.
import requests
from PIL import Image

OUT=Path(__file__).resolve().parent;ROOT=OUT.parent.parent
HOST='www.pacourts.us';UA='LocalResearchArchive/1.0'
LIMIT=10*1024*1024;START=time.monotonic();DEADLINE=START+240
PROFILES=OUT/'official_profiles.jsonl';SHARED=ROOT/'corpus/_shared_hosts/hosts.sqlite3'
def now():return datetime.datetime.now(datetime.timezone.utc).isoformat()
def digest(p):return hashlib.sha256(Path(p).read_bytes()).hexdigest()
def atomic(path,body):
    p=Path(path);tmp=p.with_name(p.name+'.'+uuid.uuid4().hex+'.tmp')
    tmp.write_text(json.dumps(body,ensure_ascii=False,indent=2)+'\n',encoding='utf-8');os.replace(tmp,p)
def jsonl(path,rows):
    p=Path(path);tmp=p.with_name(p.name+'.'+uuid.uuid4().hex+'.tmp')
    tmp.write_text(''.join(json.dumps(r,ensure_ascii=False)+'\n' for r in rows),encoding='utf-8');os.replace(tmp,p)
def shared():
    c=sqlite3.connect(SHARED.as_uri()+'?mode=ro',uri=True);c.row_factory=sqlite3.Row
    try:
        r=c.execute('SELECT host,delay_seconds,next_start_at,cooldown_until,pause_reason,owner_pid,owner_collection FROM host_state WHERE host=?',(HOST,)).fetchone()
        return dict(r) if r else {}
    finally:c.close()
class StopHost(RuntimeError):pass
session=requests.Session();session.headers.update({'User-Agent':UA,'Accept':'image/*,text/plain;q=0.8,*/*;q=0.1'})
last_start=0;delay=2.0;requests_count=0;host_stop=None
def get(url,limit):
    global last_start,requests_count
    parsed=urllib.parse.urlsplit(url)
    if parsed.scheme!='https' or parsed.hostname!=HOST or parsed.username or parsed.password:
        raise StopHost('URL outside exact approved HTTPS host')
    state=shared()
    if state.get('pause_reason'):raise StopHost('Shared host pause: '+str(state['pause_reason']))
    if state.get('owner_pid'):raise StopHost('Shared host is currently owned by another collector')
    if state.get('cooldown_until',0)>time.time():raise StopHost('Shared host cooldown is active')
    wait=max(0,last_start+max(delay,float(state.get('delay_seconds',0)))-time.monotonic(),float(state.get('next_start_at',0))-time.time())
    if time.monotonic()+wait+20>DEADLINE:raise StopHost('Bounded run deadline reached')
    if wait:time.sleep(wait)
    last_start=time.monotonic();requests_count+=1
    response=session.get(url,timeout=(10,20),allow_redirects=False,stream=True)
    try:
        headers={k:response.headers[k] for k in ['Content-Type','Content-Length','ETag','Last-Modified'] if k in response.headers}
        if response.status_code in (401,403,429):raise StopHost('HTTP access/rate restriction '+str(response.status_code))
        if 300<=response.status_code<400:raise StopHost('Unreviewed redirect; no alternate URL attempted')
        body=bytearray()
        if int(response.headers.get('Content-Length','0') or '0')>limit:raise RuntimeError('Content-Length exceeds byte limit')
        for chunk in response.iter_content(65536):
            body.extend(chunk)
            if len(body)>limit:raise RuntimeError('Response exceeds byte limit')
        return response.status_code,bytes(body),headers
    finally:response.close()

profiles=[json.loads(l) for l in PROFILES.open(encoding='utf-8')]
retained_images=[];mime_retry=False
if (OUT/'images_ready.json').exists() and json.loads((OUT/'images_ready.json').read_text()).get('ready'):
    old=json.loads((OUT/'images_ready.json').read_text())
    old_robots=json.loads((OUT/'robots_receipt.json').read_text())
    if '--retry-local-tls' in sys.argv and old.get('downloaded')==0 and 'certificate_verify_failed' in str(old_robots.get('error','')).lower() and 'status' not in old_robots:
        prior=OUT/'first_attempt_local_tls_failure';prior.mkdir(exist_ok=True)
        for name in ['images_ready.json','images.jsonl','image_failures.jsonl','image_validation.json','robots_receipt.json']:
            original=OUT/name;retained=prior/name
            if retained.exists() and retained.read_bytes()!=original.read_bytes():raise RuntimeError('Prior failure receipt already differs')
            retained.write_bytes(original.read_bytes())
    elif '--retry-mime-mismatch' in sys.argv:
        for filename,key in [('images.jsonl','images_manifest_sha256'),('official_profiles.jsonl','profile_manifest_sha256'),('image_validation.json','validation_sha256'),('image_failures.jsonl','failures_sha256')]:
            if digest(OUT/filename)!=old[key]:raise RuntimeError('Prior publication hash mismatch: '+filename)
        prior_failures=[json.loads(l) for l in (OUT/'image_failures.jsonl').open(encoding='utf-8')]
        if not prior_failures or any(r['reason']!='HTTP MIME does not match decoded format' for r in prior_failures):raise RuntimeError('Only a decoded image MIME discrepancy is eligible for this bounded correction')
        prior=OUT/'second_attempt_mime_hold';prior.mkdir(exist_ok=False)
        for name in ['images_ready.json','images.jsonl','image_failures.jsonl','image_validation.json','robots_receipt.json','robots.txt']:
            (prior/name).write_bytes((OUT/name).read_bytes())
        retained_images=[json.loads(l) for l in (OUT/'images.jsonl').open(encoding='utf-8')]
        for row in retained_images:
            row['provenance']['robots_receipt_path']=(prior/'robots_receipt.json').relative_to(ROOT).as_posix()
        mime_retry=True
    else:raise SystemExit('A completed image publication already exists; no repeat requests performed.')
atomic(OUT/'images_ready.json',{'ready':False,'status':'acquiring','started_at':now(),'requested':len(profiles)})
(OUT/'images').mkdir(exist_ok=True)
robots_url='https://www.pacourts.us/robots.txt';robots=urllib.robotparser.RobotFileParser();robots_receipt={'url':robots_url,'checked_at':now(),'shared_host_before':shared(),'tls_verification':'Windows system trust store; enabled'}
try:
    status,body,headers=get(robots_url,1024*1024)
    (OUT/'robots.txt').write_bytes(body)
    robots_receipt.update({'status':status,'headers':headers,'body_sha256':hashlib.sha256(body).hexdigest(),'path':(OUT/'robots.txt').relative_to(ROOT).as_posix()})
    if status==200:
        robots.parse(body.decode('utf-8','replace').splitlines())
        delay=max(delay,float(robots.crawl_delay(UA) or robots.crawl_delay('*') or 0))
    elif status==404:robots.parse([])
    else:raise StopHost('Robots status is not usable: '+str(status))
    robots_receipt['delay_seconds']=delay
except Exception as exc:
    host_stop='Robots/access preflight failed: '+str(exc)
    robots_receipt['error']=host_stop
atomic(OUT/'robots_receipt.json',robots_receipt)
images=retained_images;failures=[]
for profile in profiles:
    pid=profile['source_profile_id'];url=profile['image_reference']['url'];stamp=now()
    if any(r['source_profile_id']==pid for r in retained_images):continue
    try:
        if host_stop:raise StopHost(host_stop)
        if not profile['eligible_as_separate_source_profile']:raise RuntimeError('Profile is not eligible')
        if digest(profile['capture']['path'])!=profile['capture']['sha256']:raise RuntimeError('Saved capture hash changed')
        if not robots.can_fetch(UA,url):raise RuntimeError('robots.txt disallows this image URL')
        status,body,headers=get(url,LIMIT)
        if status!=200:raise RuntimeError('Image HTTP status '+str(status))
        content_type=headers.get('Content-Type','').split(';')[0].strip().lower()
        if not content_type.startswith('image/'):raise RuntimeError('Response is not an image MIME type')
        with warnings.catch_warnings():
            warnings.simplefilter('error',Image.DecompressionBombWarning)
            with Image.open(io.BytesIO(body)) as im:
                fmt=im.format;width,height=im.size;im.verify()
            with Image.open(io.BytesIO(body)) as im:im.load()
        formats={'JPEG':('jpg','image/jpeg'),'PNG':('png','image/png'),'WEBP':('webp','image/webp'),'GIF':('gif','image/gif')}
        if fmt not in formats:raise RuntimeError('Unsupported decoded image format '+str(fmt))
        ext,mime=formats[fmt]
        if width<32 or height<32 or width*height>30_000_000:raise RuntimeError('Unexpected image dimensions')
        aliases={'image/jpg':'image/jpeg','image/pjpeg':'image/jpeg','image/x-png':'image/png'}
        mime_mismatch=aliases.get(content_type,content_type)!=mime
        if mime_mismatch and not mime_retry:raise RuntimeError('HTTP MIME does not match decoded format')
        image_hash=hashlib.sha256(body).hexdigest();path=OUT/'images'/(image_hash+'.'+ext)
        if path.exists() and digest(path)!=image_hash:raise RuntimeError('Existing image path has different bytes')
        if not path.exists():path.write_bytes(body)
        record={'source_profile_id':pid,'source_url':profile['source_url'],'image_source_url':url,
          'id':'pa-portrait:'+hashlib.sha256((pid+'|'+url).encode()).hexdigest()[:24],
          'path':path.relative_to(ROOT).as_posix(),'sha256':image_hash,'mime':mime,'bytes':len(body),'width':width,'height':height,
          'verification':{'http_status':status,'http_content_type':content_type,'http_mime_differs_from_decoded_format':mime_mismatch,'mime_basis':'Pillow decoded format; original HTTP content type retained separately','pillow_format':fmt,'pillow_verify':True,'pillow_load':True,'source_capture_hash_match':True,'robots_allowed':True,'exact_profile_and_image_url_link':True,'facial_identification':False},
          'provenance':{'retrieved_at':stamp,'source_profile_id':pid,'source_url':profile['source_url'],'image_source_url':url,
             'capture_path':profile['capture']['path'],'capture_sha256':profile['capture']['sha256'],
             'identity_evidence_path':profile['identity_review_evidence_path'],'identity_evidence_sha256':profile['identity_review_evidence_sha256'],
             'profile_manifest_sha256':digest(PROFILES),'official_role_heading':profile['profile_heading_as_published'],
             'emeritus_or_emerita_in_source_heading':profile['emeritus_or_emerita_in_source_heading'],'current_service_verified':False,
             'headers':headers,'representation':'Original HTTP response image bytes; no conversion or modification','robots_receipt_path':(OUT/'robots_receipt.json').relative_to(ROOT).as_posix(),'robots_receipt_sha256':digest(OUT/'robots_receipt.json')}}
        images.append(record)
        print(json.dumps({'saved':len(images),'name':profile['name'],'width':width,'height':height,'bytes':len(body)}),flush=True)
    except Exception as exc:
        if isinstance(exc,StopHost):host_stop=str(exc)
        failures.append({'source_profile_id':pid,'source_url':profile['source_url'],'image_source_url':url,'observed_at':stamp,'reason':str(exc),'retried':False,'bypass_attempted':False})
        print(json.dumps({'failed':profile['name'],'reason':str(exc)}),flush=True)

jsonl(OUT/'images.jsonl',images);jsonl(OUT/'image_failures.jsonl',failures)
validation={'passed':True,'checked_at':now(),'requested':len(profiles),'downloaded':len(images),'failed':len(failures),
  'all_success_hashes_and_byte_counts_verified':all(digest(ROOT/r['path'])==r['sha256'] and (ROOT/r['path']).stat().st_size==r['bytes'] for r in images),
  'all_success_decoded':all(r['verification']['pillow_verify'] and r['verification']['pillow_load'] for r in images),
  'source_profile_ids_unique':len({r['source_profile_id'] for r in images})==len(images),
  'exact_source_profile_url_matches':all(any(p['source_profile_id']==r['source_profile_id'] and p['source_url']==r['source_url'] and p['image_reference']['url']==r['image_source_url'] for p in profiles) for r in images),
  'all_requested_accounted_for':len(images)+len(failures)==len(profiles),'original_bytes_preserved':True,'facial_identification_performed':False,
  'network_requests':requests_count,'provider_credits_spent':0,'host_pause_or_failure':host_stop}
validation['passed']=all(validation[k] for k in ['all_success_hashes_and_byte_counts_verified','all_success_decoded','source_profile_ids_unique','exact_source_profile_url_matches','all_requested_accounted_for'])
atomic(OUT/'image_validation.json',validation)
ready={'ready':validation['passed'],'schema_version':'official-pa-portrait-publication.v1','completed_at':now(),
  'images_manifest_sha256':digest(OUT/'images.jsonl'),'profile_manifest_sha256':digest(PROFILES),
  'validation_sha256':digest(OUT/'image_validation.json'),'failures_sha256':digest(OUT/'image_failures.jsonl'),
  'profiles':len(profiles),'downloaded':len(images),'failed':len(failures),'requested':len(profiles),'complete_image_acquisition':not failures}
atomic(OUT/'images_ready.json',ready)
print(json.dumps(ready),flush=True)
