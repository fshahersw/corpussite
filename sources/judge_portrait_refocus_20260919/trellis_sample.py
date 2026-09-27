"""Offline, stratified portrait-reference review of ten normalized Trellis profiles."""
from pathlib import Path
from collections import Counter
from datetime import datetime, timezone
import hashlib
import json
import re
from lxml import html

ROOT = Path(__file__).resolve().parents[2]
HERE = Path(__file__).resolve().parent


def sha(data): return hashlib.sha256(data).hexdigest()


def main():
    source = ROOT/'delivery/judge_intelligence_20260913/trellis_profiles/profiles.jsonl'
    normalized = [json.loads(line) for line in source.read_text(encoding='utf-8').splitlines()]
    picked, states = [], set()
    for p in normalized:
        state = str(p.get('reported_state'))
        if state not in states:
            picked.append(p); states.add(state)
        if len(picked) == 10: break
    results = []
    for p in picked:
        path = (ROOT/p['source_path']).resolve()
        if not path.is_relative_to(ROOT): raise ValueError('Unregistered capture path')
        data = path.read_bytes()
        if sha(data) != p['source_sha256']: raise ValueError('Capture hash mismatch')
        payload = json.loads(data); payload = payload.get('data',payload)
        source_url = payload.get('metadata',{}).get('sourceURL')
        if source_url != p['source_url']: raise ValueError('Source URL mismatch')
        markup = payload.get('html') or payload.get('rawHtml') or ''
        document = html.fromstring(markup) if markup else None
        candidates, image_elements, style_urls = [], [], []
        if document is not None:
            for img in document.xpath('//img | //source[@srcset]'):
                attrs = {k:img.get(k) for k in ('src','data-src','srcset','data-srcset','alt','class') if img.get(k)}
                image_elements.append(attrs)
                for key in ('src','data-src','srcset','data-srcset'):
                    value = attrs.get(key,'')
                    if value and not all(token in value for token in ('static-web-assets.','/static-assets/')):
                        candidates.append({'evidence':'image attribute requires manual review','attribute':key,'value':value,'alt':attrs.get('alt')})
            for n in document.xpath('//*[@style]'):
                for value in re.findall(r'url\(\s*[\'\"]?([^\)\'\"]+)',n.get('style','')):
                    style_urls.append(value)
                    if 'static-web-assets.' not in value:
                        candidates.append({'evidence':'inline CSS URL requires manual review','value':value})
        markdown_images = re.findall(r'!\[([^\]]*)\]\(([^)]+)\)',payload.get('markdown',''))
        for label,url in markdown_images:
            if 'static-web-assets.' not in url:
                candidates.append({'evidence':'Markdown image requires manual review','value':url,'alt':label})
        results.append({'source_url':p['source_url'],'reported_name':p.get('reported_name'),'reported_state':p.get('reported_state'),
            'source_path':path.relative_to(ROOT).as_posix(),'source_sha256':sha(data),'embedded_html_sha256':sha(markup.encode()),
            'html_characters':len(markup),'all_image_elements':image_elements,'inline_style_urls':style_urls,
            'markdown_images':markdown_images,'non_static_asset_candidates':candidates,
            'raw_image_elements':len(image_elements),'unique_image_urls':sorted({x.get('src') for x in image_elements if x.get('src')}),
            'review':'No nonstatic portrait URL found' if not candidates else 'Manual review required; not a portrait assignment'})
    report={'checked_at':datetime.now(timezone.utc).isoformat(),'normalized_profiles':len(normalized),
        'sample_size':len(results),'sampling':'First saved profile from each distinct recorded state, up to10. Not a census of all1300raw pages.',
        'normalized_manifest_sha256':sha(source.read_bytes()),'states':sorted(states),'items':results,
        'potential_image_references':sum(len(r['non_static_asset_candidates']) for r in results),'network_requests':0,
        'image_downloads':0,'new_portrait_assignments':0,'all1300_profiles_proven_imageless':False}
    (HERE/'trellis_sample_audit.json').write_text(json.dumps(report,indent=2,ensure_ascii=False)+'\n',encoding='utf-8')
    print(json.dumps({k:report[k] for k in ('sample_size','states','potential_image_references','new_portrait_assignments')}))


if __name__=='__main__': main()
