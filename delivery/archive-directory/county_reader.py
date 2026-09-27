"""Source-bound reading view for Trellis county profile pages, without their site shell."""
from __future__ import annotations
import hashlib
import json
import re
from pathlib import Path
from urllib.parse import urlsplit, urljoin
from lxml import html
from readable import provider_body

FIELDS = {'population': 'Population', 'website': 'Website', 'county seat': 'County seat',
          'phone number': 'Phone number', 'administration address': 'Administration address',
          'meaning': 'County name background', 'form of government': 'Form of government',
          'board of supervisors': 'Board of supervisors'}

def _finish(result):
    profile=result['county_profile'];notes=result['notes']
    profile['status'] = 'structured_fields' if profile['fields'] or profile['courthouses'] or profile['overview'] else 'heading_only'
    result['text'] = '\n\n'.join([profile['heading'], *[f'{FIELDS[k.replace("_", " ")]}: {v}' for k,v in profile['fields'].items()]])
    if profile['overview']:result['text']+='\n\nCourt background as published\n\n'+profile['overview']
    if profile['courthouses']:result['text']+='\n\nCourthouse references (linked pages not implied saved)\n\n'+'\n'.join(x['name']+': '+x['url'] for x in profile['courthouses'])
    if profile['website_url']:result['links']=[{'label':'Website reported in county profile','url':profile['website_url']}]
    notes['retained_fields']=len(profile['fields']);notes['output_sha256']=hashlib.sha256(result['text'].encode()).hexdigest()
    return result

def is_county_url(value):
    try:
        u = urlsplit(value or '')
        return u.scheme in ('https', 'http') and u.hostname == 'trellis.law' and not u.username and not u.password and bool(re.fullmatch(r'/coverage/[^/]+/[^/]+/?', u.path))
    except ValueError: return False

def reading_view(source_url, raw_path, expected_sha=None):
    if not is_county_url(source_url): return None
    notes = {'method': 'trellis_county_profile_fields', 'cleaning_is_derived': True,
             'original_preserved': True, 'not_a_currency_or_completeness_assessment': True,
             'scope': 'Known fields in the saved county information section only. Site navigation and case-result links are omitted from this reading copy.'}
    profile = {'heading': None, 'fields': {}, 'website_url': None, 'courthouses': [], 'overview': None, 'status': 'unavailable',
               'qualification': 'Publisher-reported county information. The administration address is not necessarily a courthouse; dates and field currency are unknown unless explicitly stated.'}
    result = {'text': '', 'links': [], 'notes': notes, 'county_profile': profile}
    try:
        path = Path(raw_path) if raw_path else None
        if not path or not path.is_file() or path.stat().st_size > 12_000_000: return result
        raw = path.read_bytes()
        if not expected_sha or hashlib.sha256(raw).hexdigest() != expected_sha:
            notes['warning'] = 'Saved source hash could not be verified; no profile fields were extracted.'
            return result
        notes['parent_raw_sha256'] = expected_sha
        obj=json.loads(raw.decode('utf-8-sig')) if path.suffix.lower()=='.json' else None
        if isinstance(obj,dict) and isinstance(obj.get('dom_profile_headings'),list) and obj.get('source_url')==source_url:
            headings=obj['dom_profile_headings'];h1=[x.get('text','') for x in headings if x.get('tag')=='h1']
            if len(h1)!=1 or not re.search(r'\bCourts Records$',h1[0],re.I):return result
            profile['heading']=h1[0];label=None
            for node in headings:
                if node.get('tag')=='h2':label=' '.join(node.get('text','').split()).casefold()
                elif node.get('tag')=='h3' and label in FIELDS:
                    value=' '.join(node.get('text','').split())
                    if value:profile['fields'].setdefault(label.replace(' ','_'),value)
                    if label=='website':
                        for a in node.get('links') or []:
                            href=a.get('url','');u=urlsplit(href)
                            if u.scheme in ('http','https') and u.hostname and not u.username and not u.password:profile['website_url']=href;break
                    label=None
            notes['method']='saved_rendered_dom_county_fields';return _finish(result)
        candidate = provider_body(obj) if obj is not None else raw.decode('utf-8', errors='replace')
        if not candidate: return result
        doc = html.fromstring(candidate, parser=html.HTMLParser(no_network=True, recover=True))
        blocks = doc.xpath('//*[contains(concat(" ",normalize-space(@class)," ")," top-county-info-block__container ")]')
        blocks = [node for node in blocks if node.xpath('.//h1')]
        if len(blocks) != 1: return result
        block = blocks[0]
        headings = block.xpath('.//h1')
        if len(headings) != 1: return result
        heading = ' '.join(headings[0].text_content().split())
        if not re.search(r'\bCourts Records$', heading, re.I): return result
        profile['heading'] = heading
        background=block.xpath('.//*[contains(concat(" ",normalize-space(@class)," ")," county-coverage-info ")]')
        if len(background)==1:
            value=' '.join(background[0].text_content().split())
            if value:profile['overview']=value
        for node in block.xpath('.//h2'):
            label = ' '.join(node.text_content().split()).casefold()
            if label not in FIELDS: continue
            # Match only the value in this field's own source container.
            values = node.getparent().xpath('./h3')
            if len(values) != 1: continue
            value = ' '.join(values[0].text_content().split())
            if not value: continue
            key = label.replace(' ', '_')
            profile['fields'].setdefault(key, value)
            if label == 'website':
                for a in values[0].xpath('.//a[@href]'):
                    href = urljoin(source_url, a.get('href')); u = urlsplit(href)
                    if u.scheme in ('http', 'https') and u.hostname and not u.username and not u.password:
                        profile['website_url'] = href; break
        for a in doc.xpath('//*[@id="courthouses"]//*[contains(concat(" ",normalize-space(@class)," ")," courthouse-col-main ")]//a[@href]'):
            href=urljoin(source_url,a.get('href'));u=urlsplit(href);label=' '.join(a.text_content().split())
            if label and u.scheme in ('http','https') and u.hostname and not u.username and not u.password:
                item={'name':label,'url':href,'availability':'source_link_only'}
                if item not in profile['courthouses']:profile['courthouses'].append(item)
        return _finish(result)
    except (OSError, ValueError, TypeError, UnicodeError):
        return result
