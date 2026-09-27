"""Bounded source-bound CAND image acquisition using the common crawler controls."""
from pathlib import Path
from urllib.parse import urljoin, urlsplit
from datetime import datetime, timezone
import argparse
import hashlib
import json
import re
import sqlite3
import sys
from lxml import html

ROOT=Path(__file__).resolve().parents[2]
HERE=Path(__file__).resolve().parent/'cand'
CORPUS=HERE/'corpus'
sys.path.insert(0,str(ROOT/'pipeline'))
import corpus_crawler as crawler
sys.path.insert(0,str(Path(__file__).resolve().parent))
from finalize import checked, sha, save_json, save_rows, rel, rows


def text(node): return ' '.join(node.text_content().split())


def profile_evidence(raw, source_url):
    if not re.fullmatch(r'https://cand\.uscourts\.gov/judges/[a-z]+/[a-z0-9-]+',source_url):
        raise ValueError('Outside exact official individual-profile URL shape')
    doc=html.fromstring(raw,parser=html.HTMLParser(encoding='utf-8'))
    canonical=doc.xpath('//link[@rel="canonical"]/@href')
    if canonical!=[source_url]: raise ValueError('Canonical profile URL mismatch')
    heads=doc.xpath('//h1'); articles=doc.xpath('//article[contains(concat(" ",normalize-space(@class)," ")," node--type-cand-judge ")]')
    if len(heads)!=1 or len(articles)!=1: raise ValueError('Not a uniquely named individual court profile')
    heading=text(heads[0]); article=articles[0]
    name=re.sub(r'^(?:(?:Senior|Chief) )?(?:District|Magistrate) Judge\s+','',heading)
    if name==heading or not name: raise ValueError('Unrecognized published judge heading')
    pictures=article.xpath('.//*[contains(concat(" ",normalize-space(@class)," ")," field--name-field-judge-media-image ")]//img[@src]')
    if len(pictures)!=1: raise ValueError('No single explicitly labeled judge-photo field')
    picture=pictures[0]
    def namekey(s): return re.sub(r'\W','',s.casefold())
    exact_alt=namekey(re.sub(r'^Judge\s+','',picture.get('alt',''))) == namekey(name)
    # Manually inspected source-specific exception: labeled judge-photo field,
    # exact individual H1, surname alt and full-name headshot filename agree.
    reviewed_short_alt=(source_url=='https://cand.uscourts.gov/judges/ask/krishnan-ajay-s'
        and name=='Ajay S. Krishnan' and picture.get('alt')=='Judge Krishnan'
        and 'MJ-AjaySKrishnan-Headshot' in picture.get('src',''))
    if not (exact_alt or reviewed_short_alt): raise ValueError('Named portrait alt differs from profile heading')
    image_url=urljoin(source_url,picture.get('src'))
    u=urlsplit(image_url)
    if u.scheme!='https' or u.netloc!='cand.uscourts.gov' or not u.path.startswith('/sites/default/files/'):
        raise ValueError('Unregistered portrait host/path')
    sections=[]
    for button in article.xpath('.//button[@aria-controls]'):
        if text(button).startswith('About '):
            targets=article.xpath('.//*[@id=$ident]',ident=button.get('aria-controls'))
            if len(targets)==1: sections.append(targets[0])
    biography='\n\n'.join(text(section) for section in sections)
    return {'source_url':source_url,'name':name,'heading_as_published':heading,'court':'U.S. District Court for the Northern District of California',
        'image_source_url':image_url,'image_alt':picture.get('alt'),'image_title':picture.get('title'),
        'image_field':'field--name-field-judge-media-image','biography':biography,
        'portrait_context_basis':'Exact full named alt and individual heading' if exact_alt else 'Reviewed labeled judge-photo field, exact individual heading, surname alt and full-name headshot filename',
        'current_service_verified':False,'entity_id':None,'identity_status':'source_profile_verified_entity_join_pending',
        'source_profile_id':'cand-profile:'+sha(source_url.encode())[:24]}


def prepare():
    seeds={r['url']:r for r in rows(HERE/'profile_seeds.jsonl')}
    db=sqlite3.connect((CORPUS/'corpus.sqlite3').as_uri()+'?mode=ro',uri=True);db.row_factory=sqlite3.Row
    candidates=[]; gaps=[]
    for url,seed in seeds.items():
        r=dict(db.execute('SELECT * FROM resources WHERE url=?',(url,)).fetchone())
        if r['status']!='downloaded':
            gaps.append({'source_url':url,'status':r['status'],'error':r['error']});continue
        raw_path,raw=checked(CORPUS,r['raw_path'],r['sha256']);receipt_path,receipt_data=checked(CORPUS,r['metadata_path'])
        receipt=json.loads(receipt_data)
        if receipt.get('requested_url')!=url or receipt.get('http_status')!=200 or receipt.get('raw_complete') is not True or receipt.get('sha256')!=sha(raw):
            raise ValueError('Source receipt mismatch')
        try: item=profile_evidence(raw,url)
        except ValueError as error:
            gaps.append({'source_url':url,'status':'no_verified_profile_portrait','error':str(error)});continue
        item.update({'raw_path':rel(raw_path),'raw_sha256':sha(raw),'captured_at':receipt['fetched_at'],
            'access_receipt_path':rel(receipt_path),'access_receipt_sha256':sha(receipt_data),'roster_evidence':seed['source_evidence']})
        candidates.append(item)
    save_rows(HERE/'image_candidates.jsonl',candidates);save_json(HERE/'profile_gaps.json',{'items':gaps})
    save_rows(HERE/'image_seeds.jsonl',[{'url':r['image_source_url'],'source_family':'official_cand_profile_portrait',
        'category':'judge_portrait_image','jurisdiction':{'state':'California','system':'federal'},
        'scope':{'host':'cand.uscourts.gov','path_prefixes':[urlsplit(r['image_source_url']).path]},
        'source_evidence':r} for r in candidates])
    db.close();print(json.dumps({'verified_source_profile_image_references':len(candidates),'profile_gaps':len(gaps)}))


class ExactImageConfig(crawler.Config):
    """Allow only the frozen observed image set; retain crawler robots, locks and pauses."""
    exact_urls=frozenset()
    def allowed(self,url,scope=None):
        p=urlsplit(url)
        if url not in self.exact_urls or p.scheme!='https' or p.netloc!='cand.uscourts.gov' or not p.path.startswith('/sites/default/files/'):
            return False,'outside_frozen_image_manifest'
        if scope and (scope.get('host')!=p.netloc or scope.get('path_prefixes')!=[p.path]):
            return False,'outside_exact_profile_image_scope'
        return True,'observed_portrait_image'


def acquire():
    candidates=rows(HERE/'image_candidates.jsonl')
    if not 1<=len(candidates)<=20: raise ValueError('Image batch outside approved bound')
    # Revalidate page-to-image binding immediately before network work.
    for item in candidates:
        _,raw=checked(ROOT,item['raw_path'],item['raw_sha256'])
        if profile_evidence(raw,item['source_url'])['image_source_url']!=item['image_source_url']:
            raise ValueError('Image not in exact named official profile')
    cfgdata=json.loads((HERE/'config.json').read_text())
    cfgdata['allow']=[{'host':'cand.uscourts.gov','path_prefixes':['/sites/default/files/']}]
    cfgdata['max_response_bytes']=20*1024*1024
    cfg=ExactImageConfig.from_dict(cfgdata)
    cfg.exact_urls=frozenset(r['image_source_url'] for r in candidates)
    target=HERE/'image_corpus';target.mkdir(exist_ok=True)
    with crawler.run_lock(target):
        store=crawler.Store(target,cfg);artifacts=crawler.Artifacts(target,cfg)
        save_json(target/'config.json',cfgdata)
        try:
            store.ingest(HERE/'image_seeds.jsonl')
            result=crawler.run_crawl(store,artifacts,max_pages=len(candidates),max_seconds=120)
            print(json.dumps(result))
        finally: store.db.close()


if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('action',choices=['prepare','acquire']);args=parser.parse_args()
    prepare() if args.action=='prepare' else acquire()
