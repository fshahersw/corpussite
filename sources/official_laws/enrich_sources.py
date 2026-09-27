"""Normalize saved links and merge separately retained rendered evidence.

This never substitutes a browser fetch for a blocked direct source. Only previously
fetched JavaScript application shells are eligible for render enrichment.
"""
import json, re
from pathlib import Path
from urllib.parse import urljoin, urldefrag
from bs4 import BeautifulSoup
import collect_official_laws as c

def run():
    for path in (c.ROOT/'metadata').glob('*.json'):
        data=json.loads(path.read_text(encoding='utf-8'))
        if data.get('format')!='html' or data.get('verification_status','').startswith('blocked_'):
            continue
        soup=BeautifulSoup((c.ROOT/data['evidence_path']).read_bytes(),'lxml')
        base=soup.find('base',href=True)
        resolution=urljoin(data.get('final_url',data['source_url']),base['href'].strip()) if base else data.get('final_url',data['source_url'])
        links=[]
        for a in soup.select('a[href]'):
            try:
                url=urldefrag(urljoin(resolution,a['href'].strip()))[0]
            except ValueError:
                continue
            if url.startswith(('http://','https://')):
                links.append({'url':url,'label':a.get_text(' ',strip=True)})
        data['links']=links
        if data.get('text_path') and 'please wait while we validate your browser' in (c.ROOT/data['text_path']).read_text(encoding='utf-8').lower():
            data['verification_status']='challenge_or_soft_error'
        data['link_normalization']='HTML base href honored; surrounding href whitespace stripped.'
        c.savejson(path,data)
    for path in (c.ROOT/'discovery').glob('render_*.json'):
        wrapper=json.loads(path.read_text(encoding='utf-8'))
        payload=json.loads(next(x['text'] for x in wrapper.get('content',[]) if x['type']=='text'))
        md=payload.get('markdown','')
        meta=payload.get('metadata',{})
        source=meta.get('sourceURL') or meta.get('url')
        if not source:
            continue
        target=c.ROOT/'metadata'/(c.key(source)+'.json')
        if target.exists():
            data=json.loads(target.read_text(encoding='utf-8'))
            if data.get('verification_status','').startswith('blocked_') or data.get('verification_status')=='challenge_or_soft_error':
                continue
        else:
            data=c.fetch(source,'supplemental_source')
        data['rendered_evidence_path']=str(path.relative_to(c.ROOT))
        mdpath=c.ROOT/'text'/(c.key(source)+'.rendered.md')
        mdpath.write_text(md,encoding='utf-8')
        data['rendered_markdown_path']=str(mdpath.relative_to(c.ROOT))
        data['rendered_http_status']=meta.get('statusCode')
        data['rendered_text_characters']=len(md)
        if meta.get('statusCode')==200 and len(md)>350:
            data['direct_verification_status']=data['verification_status']
            data['verification_status']='retrieved_rendered'
            # Markdown labels preserve legal-document identity for the manifest.
            labels={u:t for t,u in re.findall(r'\[([^\]]+)\]\((https?://[^\s\)]+)\)',md)}
            existing={x['url'] for x in data.get('links',[])}
            for url in payload.get('links',[]):
                url=urldefrag(url)[0]
                if url.startswith(('https://','http://')) and url not in existing:
                    data.setdefault('links',[]).append({'url':url,'label':labels.get(url,'')})
                    existing.add(url)
        c.savejson(target,data)

if __name__=='__main__': run()
