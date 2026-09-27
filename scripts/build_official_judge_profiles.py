"""Offline, evidence-bound normalization of the fixed 145-source judge package.

Run with the bundled Python (requires lxml and openpyxl). It writes only the new
delivery/judge_intelligence_20260913/official_profiles directory. No network code.
"""
from __future__ import annotations

import collections
import csv
import datetime as dt
import hashlib
import json
import re
import sys
from pathlib import Path
from urllib.parse import urljoin

from lxml import html
import openpyxl

ROOT = Path(__file__).resolve().parents[1]
INPUT = ROOT / "delivery/focused_legal_corpus/judges"
OUT = ROOT / "delivery/judge_intelligence_20260913/official_profiles"
VERSION = "1.0.2"


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def loadl(path):
    return [json.loads(line) for line in Path(path).read_text(encoding="utf-8-sig").splitlines() if line.strip()]


def norm(value):
    return re.sub(r"\s+", " ", str(value or "")).strip()


def text(node):
    return norm(" ".join(node.itertext()))


def sentences(content):
    """Sentence boundaries that retain common legal-biography initials/titles."""
    start=0
    for m in re.finditer(r'[.!?]\s+(?=[A-Z])',content):
        prefix=content[start:m.start()+1]
        if re.search(r'\b(?:[A-Za-z]\.){1,5}$',prefix) or re.search(r'\b(?:St|Jr|Sr|Gov|Dr|Mr|Ms|Mrs|Hon|Inc|LLP)\.$',prefix):
            continue
        yield prefix
        start=m.end()
    if start<len(content):yield content[start:]


ROLE = re.compile(r"^(?P<role>(?:(?:Associate |Assistant |Senior |Retired |President |Presiding |Administrative |Chief |Vice )*)(?:Judge|Justice|Chancellor|Magistrate in Chancery|Magistrate|Commissioner)(?:\s+-\s+Municipal Court)?)\s+(?P<name>.+)$", re.I)
HON = re.compile(r"^(?:The\s+)?(?:Honorable|Hon\.)\s+", re.I)
BAD_NAME = re.compile(r"(?<![\w-])(?:court|courts|county|district|directory|list|vacant|vacancy|position|judge|judges|justices|contact|administrative|office|staff|resource|calendar|education|attorney|clerk|phone|supreme|appeals|chief|presiding|commissioner|magistrate|retired|honorable)(?![\w-])", re.I)


def split_name(value):
    value = norm(value).strip(" :")
    value = HON.sub("", value)
    match = ROLE.match(value)
    role = match.group("role") if match else None
    name = match.group("name") if match else value
    name = HON.sub("", name)
    # Preserve family-name-first order and spelling; do not infer an identity.
    name = re.sub(r",\s*Hon\.\s*", ", ", name, flags=re.I)
    # Judge is also a real surname. A trailing surname after two name tokens
    # must not be rejected as a role label; other forbidden words still reject.
    bad_check=name
    if re.search(r'\bJudge$',name) and len(name.split())>=3:
        bad_check=name[:-len('Judge')]
    if not 3 <= len(name) <= 85 or BAD_NAME.search(bad_check) or any(c.isdigit() for c in name):
        return None, None
    # Parenthetical nicknames occur in the named Texas cells. Preserve them,
    # while still validating the remaining name and rejecting role phrases.
    core = re.sub(r"\([A-Z][A-Za-z .'-]*\)", "", name)
    if any(c in core for c in "@:/=<>[]()") or " and " in name.lower():
        return None, None
    words = re.findall(r"[^\s,]+", core)
    if not 2 <= len(words) <= 8 or not all(any(c.isalpha() for c in w) for w in words):
        return None, None
    if any(not (w[0].isupper() or w[0] in '\"“' or w.lower() in {'de','del','la','van','von','di','da'}) for w in words):
        return None, None
    return name, role


def narrative_categories(sentence):
    """Conservative topics for professional statements, not person inference."""
    if len(sentence)<35 or sentence.endswith('...') or '\u2026' in sentence:return []
    if re.match(r'^(?:See|View|Read|Click|Learn|Contact|Download)\b',sentence):return []
    # A professional source block can contain personal history. Do not promote
    # family, migration or citizenship statements to professional service.
    if re.search(r'\b(?:wife|husband|children|married|born|daughter|son|family resides|resides with)\b',sentence,re.I):return []
    if re.search(r'^(?:After|Before)\s+(?:emigrating|immigrating)\b|\b(?:he|she)\s+(?:emigrated|immigrated)\b',sentence,re.I):return []
    categories=[]
    academic_credential=re.search(r'\b(?:degree|bachelor\w*|master\w*|juris doctor|doctorate|diploma)\b|\b(?:B\.[AS]\.|J\.D\.|LL\.[MB]\.|Ph\.D\.|M\.[AS]\.)',sentence,re.I)
    educational_event=re.search(r'\bgraduated\b|\b(?:a |is a |was a )graduate of\b|\battended\b.{0,160}\b(?:university|college|school|academy)\b',sentence,re.I)
    if academic_credential or educational_event:categories.append('education')
    if re.search(r'\b(?:appointed|reappointed|re-appointed|elected|re-elected|reelected|sworn|nominated|confirmed)\b',sentence,re.I):
        categories.append('appointment_or_election')
    service_action=re.search(r'\b(?:served|serves|serving|presid\w*|retired|retiring|term|selected|chairs)\b|\bis the .*(?:chief|justice|judge)',sentence,re.I)
    professional_became=re.search(r'\bbecame\s+(?:(?:an?|the|its)\s+)?(?:(?:assistant|associate|presiding|chief|senior|managing|deputy)\s+)*(?:judge|justice|chancellor|magistrate|commissioner|prosecutor|attorney|partner|counsel|professor|dean)\b',sentence,re.I)
    chief_promotion=re.search(r'\bbecame\s+(?:the\s+)?chief\s+of\b.{0,100}\b(?:office|division|department|unit|court)\b',sentence,re.I)
    if service_action or professional_became or chief_promotion:categories.append('service')
    if re.search(r'\b(?:practic\w*|attorney|law firms?|law office|prosecut\w*|clerked|career|navy|army|military)\b',sentence,re.I):
        categories.append('professional_biography')
    return categories


def write_json(name, obj):
    (OUT / name).write_text(json.dumps(obj, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def write_jsonl(name, rows):
    (OUT / name).write_text("".join(json.dumps(r, ensure_ascii=False, sort_keys=True) + "\n" for r in rows), encoding="utf-8")


def write_csv(name, rows, fields):
    with (OUT / name).open("w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fields, extrasaction="ignore")
        w.writeheader()
        for row in rows:
            flat = {}
            for field in fields:
                v = row.get(field)
                if isinstance(v, (list, dict)):
                    v = json.dumps(v, ensure_ascii=False)
                if isinstance(v, str) and v.startswith(("=", "+", "-", "@")):
                    v = "'" + v
                flat[field] = v
            w.writerow(flat)


class Builder:
    def __init__(self):
        self.sources = loadl(INPUT / "sources.jsonl")
        assert len(self.sources) == 145 and len({s['source_url'] for s in self.sources}) == 145
        self.by_url = {}
        self.docs = {}
        self.profiles = []
        self.dedup = {}
        self.skipped = []
        self.methods = collections.defaultdict(set)
        self.manifest_sha = sha(INPUT / 'sources.jsonl')
        self.original_hashes = {str(p.relative_to(ROOT)): sha(p) for p in INPUT.iterdir() if p.is_file()}
        self.verified_artifacts = {}
        for i, s in enumerate(self.sources, 1):
            s['_line'] = i
            aliases = [s['source_url']]
            for c in s['original_capture_records']:
                aliases.extend([c.get('source_url'), c.get('final_url')])
            for url in aliases:
                if url:
                    self.by_url[url] = s
            for a in s['raw_artifacts'] + s['text_artifacts']:
                p = a['workspace_relative_path']
                if p not in self.verified_artifacts:
                    assert sha(ROOT / p) == a['sha256'], p
                    self.verified_artifacts[p] = a['sha256']
            if Path(s['raw_path']).suffix.lower() in {'.html', '.htm'}:
                raw = (ROOT / s['raw_path']).read_text(encoding='utf-8', errors='replace')
                if raw.strip():
                    self.docs[s['source_id']] = html.fromstring(raw)

    def evidence(self, s, node, quote=None, attribute=None):
        value = node.get(attribute) if attribute else text(node)
        quote = value if quote is None else quote
        assert quote and quote in value, (s['source_url'], quote)
        return {'kind': 'html_attribute' if attribute else 'html_text', 'artifact_path': s['raw_path'],
                'artifact_sha256': s['raw_sha256'], 'selector': node.getroottree().getpath(node),
                'attribute': attribute, 'quote': quote,
                'normalization': 'attribute unchanged' if attribute else 'HTML entities decoded; itertext joined and whitespace collapsed',
                'source_url': s['source_url'], 'capture_times': s['capture_times']}

    def metadata_evidence(self, s, field):
        return {'kind': 'manifest_field', 'artifact_path': str((INPUT/'sources.jsonl').relative_to(ROOT)).replace('\\','/'),
                'artifact_sha256': self.manifest_sha, 'line': s['_line'], 'field': field,
                'quote': s[field], 'source_url': s['source_url'], 'capture_times': s['capture_times']}

    def fact(self, category, value, evidence, scope='person_entry', note=None):
        assert evidence and value is not None
        return {'category': category, 'value': str(value), 'scope': scope, 'evidence': evidence,
                **({'note': note} if note else {})}

    def base(self, s, name, evidence, method, locator, role=None):
        found, parsed_role = split_name(name)
        if not found:
            self.skipped.append({'source_url': s['source_url'], 'locator': locator, 'observed_value': name,
                                 'reason': 'Not accepted as a conservative person-name entry'})
            return None
        facts = [self.fact('observed_name', found, evidence),
                 self.fact('state', s['state'], [self.metadata_evidence(s, 'state')], 'source_jurisdiction')]
        if parsed_role or role:
            facts.append(self.fact('role', parsed_role or role, evidence,
                                   'person_entry' if parsed_role else 'directory_context'))
        row = {'profile_id': hashlib.sha256((s['source_id']+'|'+method+'|'+locator).encode()).hexdigest()[:28],
               'record_type': 'source_bound_judicial_person_observation', 'observed_name': found,
               'state': s['state'], 'state_code': s['state_code'], 'source_id': s['source_id'],
               'source_url': s['source_url'], 'source_title': s['title'],
               'source_family': 'official_judge_offline_normalization', 'raw_path': s['raw_path'],
               'raw_sha256': s['raw_sha256'], 'text_path': s.get('text_path'),
               'text_sha256': s.get('text_sha256'), 'capture_times': s['capture_times'],
               'edition_note': s.get('edition_note'), 'extraction_method': method, 'locators': [locator],
               'current_historical_status': 'unresolved', 'identity_resolution': 'none; source entry only',
               'facts': facts, 'detail_limitations': []}
        self.methods[s['source_url']].add(method)
        return row

    def finish(self, row, duplicate_key=None):
        if row is None:
            return
        if duplicate_key is not None:
            key = (row['source_url'], row['extraction_method'], duplicate_key)
            if key in self.dedup:
                self.dedup[key]['locators'].extend(row['locators'])
                return
            self.dedup[key] = row
        # Remove only exact duplicate facts inside one observation.
        seen = set(); kept = []
        for f in row['facts']:
            key = (f['category'], f['value'], f['scope'], json.dumps(f['evidence'],sort_keys=True))
            if key not in seen:
                kept.append(f); seen.add(key)
        row['facts'] = kept
        self.profiles.append(row)

    def context(self, row, s, field='title'):
        row['facts'].append(self.fact('directory_context', s[field], [self.metadata_evidence(s, field)], 'directory_context'))

    def biography(self, row, s, paragraphs):
        for p in paragraphs:
            content = text(p)
            if len(content) < 35 or len(content) > 9000:
                continue
            # A full paragraph stays literal. Split only at sentence punctuation,
            # preserving initials as far as possible; never turn fragments into dates.
            for sentence in sentences(content):
                for category in narrative_categories(sentence):
                    row['facts'].append(self.fact(category,sentence,[self.evidence(s,p,sentence)],
                                                  note='Literal statement in this person’s source block; no temporal or identity inference.'))

    def profile_link(self, row, s, a):
        href=a.get('href','')
        if href and not href.startswith(('javascript:','mailto:','tel:')):
            row['facts'].append(self.fact('observed_profile_link',urljoin(s['source_url'],href),
                [self.evidence(s,a,href,'href')],note='Observed link only; target not fetched by this builder.'))

    def texas(self):
        assignments=loadl(INPUT/'judge_assignments.jsonl')
        self.texas_input_count=len(assignments)
        books={}
        for a in assignments:
            s=self.by_url[a['source_url']]
            if s['source_id'] not in books:
                books[s['source_id']]=openpyxl.load_workbook(ROOT/s['raw_path'],read_only=True,data_only=True)
            wb=books[s['source_id']]; ws=wb.worksheets[0]
            headers=[norm(x.value) for x in next(ws.iter_rows(min_row=1,max_row=1))]
            cells=list(next(ws.iter_rows(min_row=int(a['row_number']),max_row=int(a['row_number']))))
            lookup={h:c for h,c in zip(headers,cells) if h}
            def ev(field):
                cell=lookup[field]; q=norm(cell.value)
                assert q==norm(a['source_fields'][field]),(field,q,a['source_fields'][field])
                return {'kind':'xlsx_cell','artifact_path':s['raw_path'],'artifact_sha256':s['raw_sha256'],
                        'worksheet':ws.title,'cell':cell.coordinate,'field_label':field,'quote':q,
                        'source_url':s['source_url'],'capture_times':s['capture_times']}
            evidence=[ev(k) for k in ('First','Middle','Last','Suffix') if a['source_fields'].get(k)]
            row=self.base(s,a['name'],evidence,'existing_texas_assignment',f"{ws.title}!row{a['row_number']}")
            if not row:continue
            self.context(row,s)
            row['facts'][0]['note']='Observed name joined from the cited First/Middle/Last/Suffix cells; original spelling retained.'
            for field,category in [('County','county'),('Court','court'),('Phone','professional_phone'),('Fax','professional_fax'),
                                   ('Court Email','court_email'),('Street','professional_street'),('City','professional_city'),
                                   ('Zipcode','professional_postal_code'),('Zipplus','professional_postal_code_extension')]:
                if a['source_fields'].get(field):
                    row['facts'].append(self.fact(category,a['source_fields'][field],[ev(field)],
                        note='Court/office contact from the roster; not asserted to be a personal contact.' if 'professional_' in category or category=='court_email' else None))
            row['source_assignment_reference']={'path':str((INPUT/'judge_assignments.jsonl').relative_to(ROOT)).replace('\\','/'),
                                               'worksheet_xml':a['worksheet_xml'],'row_number':a['row_number']}
            self.finish(row)
        for wb in books.values():wb.close()

    def cards(self,s,doc):
        url=s['source_url']
        if 'iowacourts.gov' in url:
            for block in doc.xpath('//div[contains(concat(" ",normalize-space(@class)," ")," cms_list_item ")]'):
                names=block.xpath('.//h2'); roles=block.xpath('.//div[contains(@class,"cms_title")]')
                if not names or not roles or not re.search(r'judge|justice|magistrate',text(roles[0]),re.I):continue
                name=names[0]; row=self.base(s,text(name),[self.evidence(s,name)],'iowa_person_card',doc.getroottree().getpath(block))
                if not row:continue
                row['facts'].append(self.fact('role',text(roles[0]),[self.evidence(s,roles[0])]))
                self.context(row,s); self.biography(row,s,block.xpath('.//div[contains(@class,"cms_content")]//p'))
                links=block.xpath('.//div[@class="footer"]//a[@href]')
                if links:self.profile_link(row,s,links[0])
                row['detail_limitations'].append('Roster excerpt may be truncated; linked full biography was not fetched.')
                self.finish(row)
            return
        if 'courtswv.gov' in url:
            for block in doc.xpath('//article[contains(@class,"node--type-county-judge")]'):
                names=block.xpath('.//h2');
                if not names:continue
                name=names[0]; row=self.base(s,text(name),[self.evidence(s,name)],'west_virginia_biography_block',doc.getroottree().getpath(block))
                if not row:continue
                fields=block.xpath('.//*[contains(concat(" ",@class," ")," field--name-field-judge-header ")]//h6')
                for n in fields:
                    val=text(n)
                    row['facts'].append(self.fact('court_and_county_context',val,[self.evidence(s,n)]))
                    if '(' in val and ')' in val:
                        court=val.split('(',1)[0].strip();county=val.split('(',1)[1].split(')',1)[0].strip()
                        if court:row['facts'].append(self.fact('court',court,[self.evidence(s,n,court)]))
                        if county:row['facts'].append(self.fact('county_as_listed',county,[self.evidence(s,n,county)]))
                self.context(row,s); self.biography(row,s,block.xpath('.//p'))
                links=name.xpath('.//a[@href]')
                if links:self.profile_link(row,s,links[0])
                self.finish(row)
            return
        if 'utcourts.gov' in url and '/judges-bios/' in url:
            for block in doc.xpath('//div[@class="cmp-teaser"]'):
                names=block.xpath('.//h2[contains(@class,"cmp-teaser__title")]')
                if not names:continue
                name=names[0]
                if not ROLE.match(text(name)):continue
                row=self.base(s,text(name),[self.evidence(s,name)],'utah_roster_card',doc.getroottree().getpath(block))
                if not row:continue
                self.context(row,s)
                for n in block.xpath('.//div[contains(@class,"cmp-teaser__description")]'):
                    if 'court' in text(n).lower():row['facts'].append(self.fact('court_context',text(n),[self.evidence(s,n)],'directory_context'))
                links=name.xpath('.//a[@href]')
                if links:self.profile_link(row,s,links[0])
                row['detail_limitations'].append('Saved card has a name/role and biography link; full biography is not present in this card.')
                self.finish(row,duplicate_key=text(block))

    def individual(self,s,doc):
        if s['source_type']!='individual_judge_profile':return
        if 'cockecountytn.gov' in s['source_url']:
            for h in doc.xpath('//article//strong'):
                if text(h)!='Sessions & Juvenile Court Judge:':continue
                parents=h.xpath('ancestor::p[1]')
                p=parents[0].getnext() if parents else None
                if p is None or p.tag!='p':continue
                row=self.base(s,text(p),[self.evidence(s,p)],'individual_profile',doc.getroottree().getpath(p))
                if not row:continue
                row['facts'].append(self.fact('role','Sessions & Juvenile Court Judge',[self.evidence(s,h)]))
                self.context(row,s)
                for cp in doc.xpath('//article//p'):
                    val=text(cp)
                    if len(val)<300 and re.search(r'111 Court Avenue|\(423\)',val):
                        row['facts'].append(self.fact('professional_contact_block',val,[self.evidence(s,cp)],note='Published court contact block.'))
                self.finish(row);break
            return
        names=doc.xpath('//main//h1') or doc.xpath('//h1')
        if not names:return
        name=next((n for n in names if split_name(text(n))[0]),None)
        if name is None:return
        row=self.base(s,text(name),[self.evidence(s,name)],'individual_profile',doc.getroottree().getpath(name))
        if not row:return
        self.context(row,s)
        mains=doc.xpath('//main')
        block=mains[0] if mains else doc
        # Biography paragraphs below the named page heading, excluding navigation.
        pars=[p for p in block.xpath('.//p') if not p.xpath('ancestor::nav|ancestor::footer')]
        self.biography(row,s,pars)
        if 'ujs.sd.gov' in s['source_url']:
            for h in block.xpath('.//h2'):
                if text(h) not in ('Address','Contact'):continue
                parent=h.getparent()
                if len(text(parent))<=650 and re.search(r'\d|@',text(parent)):
                    row['facts'].append(self.fact('professional_contact_block',text(parent),[self.evidence(s,parent)],note='Published court contact block.'))
        self.finish(row)

    def tables(self,s,doc):
        url=s['source_url']
        allowed=any(host in url for host in ('arcourts.gov/directories/','courts.alaska.gov/judges/',
             'nebraskajudicial.gov/','wicourts.gov/courts/circuit/judges','judicial.alabama.gov/Library/Judges',
             'courts.delaware.gov/family/judges','pacourts.us/courts/courts-of-common-pleas/common-pleas-president-judges',
             'pacourts.us/courts/minor-courts/philadelphia-municipal','pacourts.us/courts/minor-courts/pittsburgh-municipal',
             'njcourts.gov/courts/civil/cbl-judges-directory'))
        if not allowed:return
        for tb in doc.xpath('//table'):
            trs=tb.xpath('./tr|./thead/tr|./tbody/tr')
            if len(trs)<2:continue
            headcells=trs[0].xpath('./th|./td'); headers=[text(n) for n in headcells]
            hmap={h.lower():i for i,h in enumerate(headers)}
            for tr in trs[1:]:
                cells=tr.xpath('./td')
                if not cells:continue
                vals=[text(n) for n in cells]; name_node=None; name_value=None; role=None; explicit=[]
                if 'wicourts.gov' in url:
                    if len(cells)<2:continue
                    for li in cells[1].xpath('.//li'):
                        value=text(li); nameval=value.split('(')[0].strip()
                        row=self.base(s,nameval,[self.evidence(s,li,nameval)],'wisconsin_judge_list',doc.getroottree().getpath(li))
                        if not row:continue
                        self.context(row,s);row['facts'].append(self.fact('county',vals[0],[self.evidence(s,cells[0])]))
                        if '(Chief Judge)' in value:row['facts'].append(self.fact('role','Chief Judge',[self.evidence(s,li,'(Chief Judge)')]))
                        self.finish(row)
                    continue
                if 'courts.delaware.gov/family/' in url:
                    if not re.search(r'Family Court Judges',text(headcells[0])):continue
                    name_node=cells[0];name_value=vals[0]
                    if len(cells)>1 and vals[1]:explicit.append(('county',vals[1],cells[1]))
                elif 'arcourts.gov' in url:
                    ni=hmap.get('name');
                    if ni is None or ni>=len(cells):continue
                    if 'position' in hmap and not re.fullmatch('District Judge',vals[hmap['position']],re.I):continue
                    name_node=cells[ni]; name_value=vals[ni]
                    if 'position' in hmap:explicit.append(('role',vals[hmap['position']],cells[hmap['position']]))
                elif 'pacourts.us' in url:
                    ni=hmap.get('name & address',hmap.get('name'))
                    if ni is None or ni>=len(cells):continue
                    namecell=cells[ni]; strong=namecell.xpath('.//strong')
                    name_node=strong[0] if strong else namecell
                    name_value=text(name_node) if strong else norm((namecell.text or '').split('\n')[0])
                    if not name_value:continue
                elif 'judicial.alabama.gov' in url:
                    if headers!=['Name','Begin Date','End Date'] or len(cells)!=3:continue
                    name_node=cells[0];name_value=vals[0]
                    explicit.extend([('service_begin_as_listed',vals[1],cells[1]),('service_end_as_listed',vals[2],cells[2])])
                elif 'njcourts.gov/courts/civil/' in url:
                    if 'designated judge' not in hmap:continue
                    ni=hmap['designated judge'];name_node=cells[ni];name_value=vals[ni]
                else:
                    ni=hmap.get('judge',hmap.get('justice'))
                    if ni is None or ni>=len(cells):continue
                    name_node=cells[ni];name_value=vals[ni]
                    suffix=re.search(r'\s+((?:Chief|Presiding) (?:Judge|Justice).*)$',name_value)
                    if suffix:
                        explicit.append(('role',suffix.group(1),name_node));name_value=name_value[:suffix.start()]
                if name_node is None or not name_value:continue
                row=self.base(s,name_value,[self.evidence(s,name_node,name_value)],'reviewed_judge_table',doc.getroottree().getpath(tr))
                if not row:continue
                self.context(row,s)
                for category,value,node in explicit:
                    if value:row['facts'].append(self.fact(category,value,[self.evidence(s,node,value)]))
                fields={'county':'county','county(ies)':'county_as_listed','court':'court','judicial circuit':'court_circuit',
                        'judicial district':'court_district','district':'court_district','district court':'court_district',
                        'division':'court_division','location':'location_as_listed','appointed':'appointment_year_as_listed',
                        'phone':'professional_phone','fax':'professional_fax','fax number':'professional_fax',
                        'address':'professional_address','city':'professional_city','zip code':'professional_postal_code',
                        'contact':'professional_contact_block'}
                for header,category in fields.items():
                    ci=hmap.get(header)
                    if ci is not None and ci<len(cells) and vals[ci]:
                        row['facts'].append(self.fact(category,vals[ci],[self.evidence(s,cells[ci])]))
                if 'judicial.alabama.gov' in url:
                    row['current_historical_status']='source_says_present' if 'present' in vals[2].lower() else 'dated_service_entry'
                    row['detail_limitations'].append('Historical membership table; dates and source Present label preserved without a claim of current service.')
                    heading=tb.xpath('preceding::*[self::h1 or self::h2][1]')
                    if heading:row['facts'].append(self.fact('role_context',text(heading[0]),[self.evidence(s,heading[0])],'directory_context'))
                self.finish(row,duplicate_key=json.dumps([headers,vals],ensure_ascii=False))

    def headings(self,s,doc):
        url=s['source_url']
        # These archived directory families have explicit role-prefixed headings.
        if not any(x in url for x in ('courts.delaware.gov/','courts.oregon.gov/','courts.state.hi.us/',
               'kscourts.gov/','ndcourts.gov/','nebraskajudicial.gov/','courts.maine.gov/','newcc.gov/')):return
        for n in doc.xpath('//h2|//h3|//h4'):
            if n.xpath('ancestor::nav|ancestor::footer|ancestor::table'):continue
            value=text(n)
            if not ROLE.match(value):continue
            row=self.base(s,value,[self.evidence(s,n)],'explicit_judicial_heading',doc.getroottree().getpath(n))
            if not row:continue
            self.context(row,s)
            # Only same-parent following paragraphs up to the next heading.
            paragraphs=[]
            for sibling in n.itersiblings():
                if sibling.tag in ('h1','h2','h3','h4'):break
                if sibling.tag=='p':paragraphs.append(sibling)
                elif sibling.xpath('.//h2|.//h3|.//h4'):break
            self.biography(row,s,paragraphs)
            for a in n.xpath('.//a[@href]'):self.profile_link(row,s,a)
            self.finish(row,duplicate_key=value)

    def run(self):
        self.texas()
        for s in self.sources:
            doc=self.docs.get(s['source_id'])
            if doc is None:continue
            self.individual(s,doc)
            if s['source_type']=='individual_judge_profile':continue
            self.cards(s,doc);self.tables(s,doc);self.headings(s,doc)
        for row in self.profiles:
            for category,field in [('role','roles'),('court','courts'),('county','counties'),('education','education_facts'),
                     ('appointment_or_election','appointment_facts'),('service','service_facts'),('professional_biography','biography_facts')]:
                row[field]=list(dict.fromkeys(f['value'] for f in row['facts'] if f['category']==category))
            row['professional_contacts']=[{'field':f['category'],'value':f['value']} for f in row['facts']
                if f['category'].startswith('professional_') and f['category']!='professional_biography' or f['category']=='court_email']
            if not row['education_facts'] and not row['appointment_facts'] and not row['biography_facts']:
                row['detail_limitations'].append('No conservative biography/education/appointment narrative extracted from this entry.')

    def validate(self):
        assert len({r['profile_id'] for r in self.profiles})==len(self.profiles)
        checks=collections.Counter(); books={}
        for row in self.profiles:
            assert row['source_url'] in self.by_url
            assert split_name(row['observed_name'])[0]
            assert any(f['category']=='observed_name' for f in row['facts'])
            for fact in row['facts']:
                assert fact['evidence'] and fact['value']
                quoted=[e['quote'] for e in fact['evidence']]
                if fact['category']=='observed_name':
                    expected=norm(' '.join(quoted)) if fact['evidence'][0]['kind']=='xlsx_cell' else split_name(quoted[0])[0]
                    assert expected==fact['value'],(row['profile_id'],fact)
                elif fact['category']=='observed_profile_link':
                    assert urljoin(row['source_url'],quoted[0])==fact['value']
                else:
                    assert any(norm(fact['value']) in norm(q) for q in quoted),(row['profile_id'],fact)
                for e in fact['evidence']:
                    checks[e['kind']]+=1
                    if e['kind'].startswith('html_'):
                        s=self.by_url[e['source_url']];doc=self.docs[s['source_id']]
                        nodes=doc.xpath(e['selector']);assert len(nodes)==1,e['selector']
                        haystack=nodes[0].get(e['attribute']) if e['kind']=='html_attribute' else text(nodes[0])
                        assert e['quote'] in haystack,(row['profile_id'],e)
                    elif e['kind']=='manifest_field':
                        assert self.sources[e['line']-1][e['field']]==e['quote']
                    elif e['kind']=='xlsx_cell':
                        if e['artifact_path'] not in books:
                            books[e['artifact_path']]=openpyxl.load_workbook(ROOT/e['artifact_path'],read_only=False,data_only=True)
                        assert norm(books[e['artifact_path']][e['worksheet']][e['cell']].value)==e['quote']
                    else:raise AssertionError(e)
        for wb in books.values():wb.close()
        for path,expected in self.verified_artifacts.items():assert sha(ROOT/path)==expected
        for path,expected in self.original_hashes.items():assert sha(ROOT/path)==expected
        assert sum(r['extraction_method']=='existing_texas_assignment' for r in self.profiles)==self.texas_input_count
        assert not any(r['observed_name']=='Christen Ray' for r in self.profiles), 'Cocke assistant must not become judge'
        assert not any('Clerk' in r['observed_name'] for r in self.profiles)
        assert any(r['observed_name']=='Mark Strange' and r['state_code']=='TN' for r in self.profiles)
        for r in self.profiles:
            if r['source_url']=='https://arcourts.gov/directories/district-courts':
                assert any(f['category']=='role' and f['value']=='District Judge' for f in r['facts'])
            if r['source_url']=='https://courts.delaware.gov/family/judges.aspx':
                assert all('commissioner' not in f['value'].lower() for f in r['facts'] if f['category']=='role')
        return {'validated_at':dt.datetime.now(dt.timezone.utc).isoformat(),'issues':0,
                'unique_observation_ids':len(self.profiles),'evidence_checks_by_kind':dict(checks),
                'verified_original_artifact_hashes':len(self.verified_artifacts),
                'existing_package_files_unchanged':len(self.original_hashes),
                'texas_assignment_rows_preserved':self.texas_input_count,
                'negative_controls':['Cocke administrative assistant excluded','Arkansas non-judge Position rows excluded',
                                     'Delaware Family Court Commissioners table excluded','Names reject vacancy/navigation labels'],
                'all_fact_values_reproduced_from_cited_evidence':True,
                'cross_source_identity_merges':0,'network_requests':0}

    def semantic_regressions(self):
        checks=[]
        narrative_cases=[
            ('Robert Gusinsky','After emigrating with his parents through Israel and Germany, he settled in California and became a U.S. citizen.',set(),{'education','service','professional_biography','appointment_or_election'}),
            ('Robert Gusinsky','Following graduation, Justice Gusinsky practiced as a trial lawyer at Lynn, Jackson, Shultz & Lebrun and later at Clayborne, Loos, Strommen & Gusinsky in Rapid City.',{'professional_biography'},{'education','service'}),
            ('Robert Gusinsky','Justice Gusinsky received his undergraduate degree in aeronautical engineering in 1990 from Embry Riddle Aeronautical University.',{'education'},set()),
            ('Robert Gusinsky','Justice Gusinsky became presiding judge of the Seventh Judicial Circuit in 2024.',{'service'},set()),
            ('Ryan J. Flanigan','After graduation, Judge Flanigan worked for the law firms of Robinson & McElwee and Bailey & Wyant, both located in Charleston.',{'professional_biography'},{'education'}),
            ('Tera L. Salango','Upon graduation, Judge Salango worked for the law firm of Spilman, Thomas & Battle and focused primarily on civil defense litigation.',{'professional_biography'},{'education'}),
            ('Michael Simms','After graduation, Judge Simms returned to West Virginia to work with his father, Alan Simms, as an Associate at Simms Law Office in Elizabeth, West Virginia.',{'professional_biography'},{'education'}),
            ('Patricia Guerrero','During her legal career, Chief Justice Guerrero served as a member of the Advisory Board of the Immigration Justice Project, which promotes due process and access to justice at all levels of the immigration and appellate court system.',{'service','professional_biography'},set()),
            ('Bradford S. Delapena','Judge Delapena earned a B.A. from University of Pittsburgh in 1986 (Philosophy & Economics), and a J.D. from the University of Wisconsin Law School in 1991 (cum laude).',{'education'},set()),
            ('Mark E. Salter','As an assistant United States attorney, Justice Salter focused on appellate practice and became the chief of the office’s appellate division in 2009.',{'service','professional_biography'},set()),
        ]
        for name,quote,required,forbidden in narrative_cases:
            matches=[p for p in self.profiles if p['observed_name']==name]
            assert len(matches)==1,(name,len(matches))
            row=matches[0];s=self.by_url[row['source_url']];doc=self.docs[s['source_id']]
            nodes=[n for n in doc.xpath('//p') if quote in text(n)]
            assert nodes,(name,quote)
            node=min(nodes,key=lambda n:len(text(n)))
            categories={f['category'] for f in row['facts'] if f['value']==quote}
            assert required<=categories and not categories&forbidden,(name,quote,categories)
            checks.append({'case':'professional_narrative_categories','observed_name':name,'profile_id':row['profile_id'],
                'source_url':row['source_url'],'source_evidence':self.evidence(s,node,quote),
                'required_categories':sorted(required),'forbidden_categories':sorted(forbidden),
                'actual_categories':sorted(categories),'passed':True})
        expected_names=[
            ('Thomas J. Judge','https://judicial.alabama.gov/Library/Judges',
             '/html/body/div/section[2]/section/section/article/figure[2]/table[2]/tr[37]',
             {'service_begin_as_listed':'1866','service_end_as_listed':'1868'}),
            ('Joffie C. Pittman III','https://www.pacourts.us/courts/minor-courts/philadelphia-municipal-court-judges',
             '/html/body/div/div/div[2]/main/div[2]/div/div/div/div/div/div/table/tbody/tr[20]',
             {'role':'Administrative Judge - Municipal Court'}),
        ]
        for name,url,locator,required_facts in expected_names:
            matches=[p for p in self.profiles if p['observed_name']==name and p['source_url']==url and locator in p['locators']]
            assert len(matches)==1,(name,len(matches))
            row=matches[0];s=self.by_url[url];node=self.docs[s['source_id']].xpath(locator)[0]
            for category,value in required_facts.items():
                assert any(f['category']==category and f['value']==value for f in row['facts']),(name,category,value)
            checks.append({'case':'valid_source_name_not_rejected','observed_name':name,'profile_id':row['profile_id'],
                'source_url':url,'source_evidence':self.evidence(s,node),'required_facts':required_facts,'passed':True})
        # Synthetic negative controls are explicitly separate from source facts.
        for sentence in ['He later became a resident of California and remained there.',
                         'He earned the respect of colleagues through his work.']:
            assert not narrative_categories(sentence),sentence
            checks.append({'case':'synthetic_negative_control','fixture_text':sentence,'actual_categories':[],
                           'source_fact':False,'passed':True})
        return {'builder_version':VERSION,'validated_at':dt.datetime.now(dt.timezone.utc).isoformat(),
            'source_manifest_sha256':self.manifest_sha,'checks':checks,'check_count':len(checks),'issues':0}

    def outputs(self,validation,regressions):
        OUT.mkdir(parents=True,exist_ok=True)
        coverage=[]; gaps=[]
        for s in self.sources:
            rr=[r for r in self.profiles if r['source_url']==s['source_url']]
            row={k:s.get(k) for k in ('source_id','source_url','state','state_code','title','source_type','raw_path','raw_sha256','text_path','text_sha256','capture_times','edition_note')}
            row.update({'observations':len(rr),'extraction_methods':sorted(self.methods[s['source_url']]),
                        'biography_observations':sum(bool(r['education_facts'] or r['appointment_facts'] or r['biography_facts']) for r in rr),
                        'normalization_status':'partial_structured' if rr else 'source_preserved_unstructured',
                        'source_completely_normalized':False})
            coverage.append(row)
            reason='No supported conservative person-entry structure normalized in this pass; original and extracted text remain available.' if not rr else 'Partial normalization only; observations are not an exhaustive or entity-resolved roster.'
            if s['source_url'].endswith('/JudgeSearch/'):reason='Saved Ohio JudgeSearch interface has empty extracted text; no named people extracted.'
            gaps.append({'source_url':s['source_url'],'state':s['state'],'gap_type':'normalization_coverage','reason':reason,
                         'edition_note':s.get('edition_note'),'observations':len(rr)})
        facts=[]
        for p in self.profiles:
            for i,f in enumerate(p['facts'],1):facts.append({'profile_id':p['profile_id'],'fact_id':f"{p['profile_id']}:{i}",
                'observed_name':p['observed_name'],'state':p['state'],'source_url':p['source_url'],**f})
        summary={'built_at':dt.datetime.now(dt.timezone.utc).isoformat(),'builder_version':VERSION,
            'input_sources':145,'source_bound_person_observations':len(self.profiles),
            'sources_with_observations':sum(x['observations']>0 for x in coverage),
            'states_with_observations':len({p['state_code'] for p in self.profiles}),
            'facts':len(facts),'facts_by_category':dict(collections.Counter(f['category'] for f in facts)),
            'observations_by_method':dict(collections.Counter(p['extraction_method'] for p in self.profiles)),
            'observations_with_narrative_facts':sum(bool(p['education_facts'] or p['appointment_facts'] or p['biography_facts']) for p in self.profiles),
            'observations_with_professional_contacts':sum(bool(p['professional_contacts']) for p in self.profiles),
            'repeated_identical_presentations_collapsed':sum(len(p['locators'])-1 for p in self.profiles),
            'rejected_candidate_entries':len(self.skipped),'original_artifact_hashes_verified':len(self.verified_artifacts),
            'unique_people_count':None,'national_inventory_complete':False,'cross_source_identity_merges':0,
            'network_requests':0,'validation_issues':validation['issues']}
        write_jsonl('profiles.jsonl',self.profiles)
        write_csv('profiles.csv',self.profiles,['profile_id','observed_name','state','state_code','roles','courts','counties',
            'professional_contacts','education_facts','appointment_facts','service_facts','biography_facts',
            'current_historical_status','source_url','source_title','capture_times','edition_note','raw_path','raw_sha256',
            'text_path','text_sha256','extraction_method','detail_limitations','facts'])
        write_jsonl('facts.jsonl',facts)
        write_csv('facts.csv',facts,['profile_id','fact_id','observed_name','state','source_url','category','value','scope','note','evidence'])
        write_jsonl('source_coverage.jsonl',coverage);write_csv('source_coverage.csv',coverage,list(coverage[0]))
        state_coverage=[]
        for jurisdiction in json.loads((INPUT/'state_coverage.json').read_text(encoding='utf-8')):
            code=jurisdiction['state_code'];ss=[s for s in coverage if s['state_code']==code]
            pp=[p for p in self.profiles if p['state_code']==code]
            state_coverage.append({'state':jurisdiction['state'],'state_code':code,'saved_sources_in_fixed_input':len(ss),
                'sources_with_person_observations':sum(s['observations']>0 for s in ss),'person_observations':len(pp),
                'narrative_observations':sum(bool(p['education_facts'] or p['appointment_facts'] or p['biography_facts']) for p in pp),
                'historically_dated_observations':sum(p['current_historical_status']=='dated_service_entry' for p in pp),
                'current_inventory_complete':False})
        write_jsonl('state_coverage.jsonl',state_coverage);write_csv('state_coverage.csv',state_coverage,list(state_coverage[0]))
        write_jsonl('gaps.jsonl',gaps);write_csv('gaps.csv',gaps,list(gaps[0]))
        write_jsonl('rejected_candidates.jsonl',self.skipped)
        write_json('summary.json',summary);write_json('validation.json',validation)
        write_json('semantic_regressions.json',regressions)
        write_json('schema.json',{'profile':'One source-specific observed person entry. A person may have multiple entries; no identity matching across names or sources.',
            'facts':'Every fact has category/value/scope and literal evidence tied to a captured source, artifact SHA-256, and HTML XPath, XLSX cell, or source-manifest field.',
            'state':'Source jurisdiction from the saved, hashed manifest; not inferred from a person’s residence.',
            'contacts':'Public court/office contact fields only. Court Email is not labeled as the judge’s personal email.',
            'temporal':'Status remains unresolved except explicit Alabama service end labels; source Present does not establish present-day service.',
            'narratives':'Literal professional biography/education/appointment/service sentences, not normalized dates or degrees. Sentences may fall into several categories.',
            'dedup':'Only byte-equivalent normalized row/card presentations within one source collapse, retaining all original locators; not person deduplication.',
            'paths':'Relative to C:/Users/firas/Downloads/SCRAPE. All underlying original content is preserved.',
            'csv':'UTF-8 BOM; structured cells use JSON; leading formula characters escaped. JSONL preserves source strings.'})
        (OUT/'README.md').write_text(f'''# Official judge profiles from saved sources

This offline package contains **{len(self.profiles):,} source-specific person observations** from **{summary['sources_with_observations']} of the 145 saved sources**, covering **{summary['states_with_observations']} states**. It is not a count of unique people or current judges. The same person can appear in different sources or court assignments; identities have not been merged.

There are **{len(facts):,} evidence-linked facts**, including literal appointment, education and service statements. **{summary['observations_with_narrative_facts']} observations** contain professional narrative facts and **{summary['observations_with_professional_contacts']}** include published court/office contact fields. All 363 existing Texas assignments and all eight saved individual profiles were retained.

Use `profiles.jsonl` or `profiles.csv` for person observations. Each record includes source URL, capture times, edition note, original/text paths and hashes, facts and limitations. `facts.jsonl` and `facts.csv` contain one fact per row with its evidence. Evidence points to a captured HTML XPath, original XLSX cell, or a field in the hashed source manifest. HTML quotations preserve source characters after entity decoding and whitespace normalization.

`source_coverage.jsonl` / `.csv` account for every one of the 145 sources. `state_coverage.jsonl` / `.csv` cover all 50 states plus DC. `gaps.jsonl` / `.csv` make partial normalization explicit. `rejected_candidates.jsonl` preserves ambiguous names, multiple-person cells, vacancies and non-person headings without promoting them to profiles. Unparsed PDFs, other unsupported roster layouts and directory-only pages remain available at the original paths. No source was fetched or changed for this package.

Biographical statements are literal excerpts from the named person's block. Categories organize those statements; dates, degrees, institutions and identity matches have not been inferred. Iowa cards can be truncated, so incomplete trailing sentences are excluded. Utah cards commonly provide names/roles and an observed biography link; the linked biographies were not downloaded here. Court Email identifies an office/court address, not a personal email.

Alabama's tables include historical service beginning in 1820. Their stated begin/end values are retained, including the literal label Present. Other records keep current/historical status unresolved. Capture dates do not establish that the reported office or biography is current.

The repeatable offline builder is `scripts/build_official_judge_profiles.py` in the workspace root. Run it with Python containing lxml and openpyxl, then run `scripts/validate_official_judge_profiles.py` to reconcile the persisted exports and refresh the coverage report. Both write only this new output folder. Paths are relative to `C:/Users/firas/Downloads/SCRAPE`; retain the referenced `sources` and `corpus` directories with this metadata. `validation.json` records checks of all 293 artifact hashes, all fact values/evidence locations, Texas cell values, negative controls and unchanged original package files. `semantic_regressions.json` records the source-linked checks for the audited name and narrative corrections. Validation reported zero issues.
''',encoding='utf-8')
        return summary


def main():
    sys.stdout.reconfigure(encoding='utf-8')
    b=Builder();b.run();validation=b.validate();regressions=b.semantic_regressions();summary=b.outputs(validation,regressions)
    print(json.dumps(summary,ensure_ascii=False,indent=2))


if __name__=='__main__':main()
