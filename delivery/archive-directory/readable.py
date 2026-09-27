"""Conservative derived reading views. Original text and artifacts stay unchanged."""
from __future__ import annotations
import copy, hashlib, json, re
from pathlib import Path
from urllib.parse import urljoin, urlsplit
from lxml import html, etree

VERSION='reading-view-1.2'
LEGAL=re.compile(r'§|\b(?:section|sec\.|rule|article|chapter|amendment)\s+[\dIVXLC]',re.I)
LEGAL_CONTEXT=re.compile(r'\b(?:statutes?|constitution|civil procedure|(?:local |court )rules?|legal notes?|legislative history)\b',re.I)
EDITION_CONTEXT=re.compile(r'\b(?:effective|amended|superseded|repealed|edition|revised|revision|adopted|enacted|last updated)\b',re.I)
CONTROL=re.compile(r'^(?:skip to (?:main )?content|skip to footer|open menu|close menu|menu|search|sign in|log in|subscribe|accept (?:all )?cookies|cookie (?:policy|preferences)|select (?:a )?state|select all|back to top|share|print|facebook|twitter|linkedin|instagram)$',re.I)
NOISE=re.compile(r'(?:^|[\s_-])(?:sidebar|navbar|navigation|menu|footer|cookie|social|newsletter|breadcrumb|advertisement|share-tools|related-posts)(?:$|[\s_-])',re.I)

def digest(s):return hashlib.sha256(s.encode('utf-8')).hexdigest()

def provider_body(value,depth=0):
    if depth>7:return None
    if isinstance(value,dict):
        for field in ['rawHtml','html','markdown','node_text','text_content','body_text']:
            child=value.get(field)
            if isinstance(child,str) and child.strip():return child
        for field in ['data','result','tool_result','content']:
            child=value.get(field)
            if child is not None:
                found=provider_body(child,depth+1)
                if found:return found
        if value.get('type')=='text' and isinstance(value.get('text'),str):return provider_body(value['text'],depth+1)
    elif isinstance(value,list):
        for child in value:
            found=provider_body(child,depth+1)
            if found:return found
    elif isinstance(value,str):
        try:return provider_body(json.loads(value),depth+1)
        except (ValueError,TypeError):
            if '<html' in value.lower() or '<body' in value.lower():return value
    return None

def html_body(source,url):
    parser=html.HTMLParser(encoding='utf-8',remove_comments=True,no_network=True,recover=True)
    doc=html.fromstring(source.encode('utf-8'),parser=parser)
    links=[]
    for a in doc.xpath('//a[@href]'):
        # A malformed href (e.g. a bracketed pseudo-host) makes urllib raise; such a link is skipped, never fatal.
        try:href=urljoin(url,a.get('href',''));scheme=urlsplit(href).scheme
        except ValueError:continue
        label=' '.join(a.text_content().split())
        if scheme in {'http','https'} and label:links.append({'label':label[:250],'url':href})
    candidates=doc.xpath('//main|//*[@role="main"]|//*[@id="bodyWrapper" or @id="main-content" or @id="maincontent" or @id="content"]')
    candidates=[x for x in candidates if len(x.text_content().strip())>80]
    root=max(candidates,key=lambda x:len(x.text_content())) if candidates else doc
    external_context_count=0
    if candidates:
        # A semantic main often excludes the publisher's title and effective-date
        # header. Retain relevant outside blocks in document order, without
        # duplicating the main or its descendants or importing a whole site shell.
        document_nodes=list(doc.iter());order={node:i for i,node in enumerate(document_nodes)}
        selected=[root]
        for node in document_nodes:
            if not isinstance(node.tag,str) or node is root:continue
            ancestors=list(node.iterancestors())
            if any(parent in selected for parent in ancestors):continue
            if any(node in item.iterancestors() for item in selected):continue
            if any(parent.get('hidden') is not None or parent.get('aria-hidden')=='true' for parent in [node,*ancestors]):continue
            tag=node.tag.lower()
            if tag not in {'header','h1','h2','aside','p','time'} and not (tag=='div' and node.get('role')=='note'):continue
            body=' '.join(node.text_content().split())
            legal=bool(LEGAL.search(body) or LEGAL_CONTEXT.search(body))
            edition=bool(EDITION_CONTEXT.search(body))
            if body and ((tag in {'header','h1','h2','aside'} and legal) or edition):selected.append(node)
        if len(selected)>1:
            wrapper=html.Element('div')
            for node in sorted(selected,key=order.get):
                clone=copy.deepcopy(node);clone.tail=None;wrapper.append(clone)
            external_context_count=len(selected)-1;root=wrapper
    removed=0
    for node in list(root.iter()):
        if node is root or not isinstance(node.tag,str):continue
        tag=node.tag.lower(); attrs=' '.join([node.get('id',''),node.get('class',''),node.get('role','')])
        # Court sites often put the whole page in an ASP.NET form; legal dates,
        # captions and historical notes also occur in headers/asides/footers.
        body=' '.join(node.text_content().split())
        legal_context=bool(LEGAL.search(body) or LEGAL_CONTEXT.search(body) or EDITION_CONTEXT.search(body))
        anchors=node.xpath('.//a')
        linked=sum(len(' '.join(a.text_content().split())) for a in anchors)
        menu_like=bool(body) and linked/len(body)>.75
        drop=tag in {'script','style','noscript','button','svg','iframe','dialog','template','input','select'} or (tag=='textarea' and node.get('readonly') is None) or node.get('hidden') is not None or node.get('aria-hidden')=='true'
        if not legal_context and (tag=='nav' or (NOISE.search(attrs) and menu_like)):drop=True
        if tag=='footer' and re.fullmatch(r'cookies?|sign in|log in|back to top',body,re.I):drop=True
        if drop and node.getparent() is not None:
            node.drop_tree();removed+=1
    # Keep unlabelled link lists: they may be the site's legal document index.
    for node in root.iter():
        if not isinstance(node.tag,str):continue
        tag=node.tag.lower()
        if tag in {'h1','h2','h3','h4','h5','h6'}:node.text='\n\n'+('#'*min(int(tag[1]),3))+' '+(node.text or '')
        elif tag=='li':node.text='\n• '+(node.text or '')
        if tag in {'p','div','section','article','main','li','tr','h1','h2','h3','h4','h5','h6','br','textarea'}:node.tail='\n\n'+(node.tail or '')
        elif tag in {'td','th'}:node.tail=' | '+(node.tail or '')
    return root.text_content(),links,{'semantic_main_selected':bool(candidates),'removed_dom_blocks':removed,'external_legal_context_blocks':external_context_count}

def clean_text(text,title=''):
    links=[]
    def anchor(match):
        label,url=match.group(1),match.group(2)
        try:scheme=urlsplit(url).scheme
        except ValueError:scheme=''
        if scheme in {'http','https'}:links.append({'label':label,'url':url})
        return label
    text=re.sub(r'!\[[^\]]*\]\([^\n]*\)','',text)
    text=re.sub(r'\[([^\]\n]+)\]\((https?://[^\s)]+)(?:\s+"[^"\n]*")?\)',anchor,text)
    text=text.replace('\r\n','\n').replace('\r','\n').replace('\u00a0',' ')
    lines=[];removed=0
    for raw in text.splitlines():
        line=re.sub(r'[ \t]+',' ',raw).strip()
        if CONTROL.fullmatch(line) or re.fullmatch(r'=+\s*(?:PAGE\s+\d+|DOCX PART:.*?)\s*=+',line,re.I):removed+=1;continue
        if line and lines and line==lines[-1] and line==title and not LEGAL.search(line):removed+=1;continue
        lines.append(line)
    text=re.sub(r'\n{3,}','\n\n','\n'.join(lines)).strip()
    return text,links,removed

def reading_view(text,*,title='',source_url='',raw_path=None):
    original=text or '';candidate=None;method='plain_text_spacing';notes={};links=[]
    if raw_path:
        path=Path(raw_path)
        if path.is_file() and path.stat().st_size<=12_000_000:
            if path.suffix.lower() in {'.html','.htm'}:
                candidate=path.read_text(encoding='utf-8',errors='replace')
            elif path.suffix.lower()=='.json':
                try:candidate=provider_body(json.loads(path.read_text(encoding='utf-8-sig')))
                except (ValueError,OSError):pass
    if candidate and re.search(r'<(?:html|body|main|article|div|p|table)\b',candidate,re.I):
        try:
            body,links,notes=html_body(candidate,source_url)
            if len(body.strip())>=80:text=body;method='html_main_with_navigation_removed'
        except (etree.ParserError,ValueError,TypeError):notes['html_fallback']=True
    elif candidate and not candidate.lstrip().startswith(('{','[')):
        text=candidate;method='provider_markdown_cleanup'
    if (text or '').lstrip().startswith('{'):
        try:
            p=json.loads(text);body=provider_body(p)
            if body:text=body;method='structured_body_field'
            else:text='';method='metadata_without_readable_body';notes['metadata_available_separately']=True
        except ValueError:pass
    cleaned,more_links,removed=clean_text(text or '',title)
    links+=more_links
    unique={}
    for link in links:
        unique.setdefault((link['url'],link['label']),link)
    notes.update({'version':VERSION,'method':method,'input_text_sha256':digest(original),'input_characters':len(original),'output_sha256':digest(cleaned),'output_characters':len(cleaned),'removed_control_lines':removed,'cleaning_is_derived':True,'original_preserved':True,'not_a_currency_or_completeness_assessment':True})
    return {'text':cleaned,'links':list(unique.values()),'notes':notes}

def plain_profile(p):
    """A readable source observation when no resolved entity is available."""
    native=p.get('native_record') or p
    lines=[p.get('name') or native.get('name') or 'Judge source observation']
    fields=[('publisher','Publisher'),('courts','Courts'),('state_code','State'),('judge_system','Court system')]
    for key,label in fields:
        value=p.get(key)
        if value:lines.append(label+': '+(', '.join(map(str,value)) if isinstance(value,list) else str(value)))
    def values(value):
        if isinstance(value,str):return [value]
        if isinstance(value,list):return [v for item in value for v in values(item)]
        if isinstance(value,dict):
            preferred=[value[k] for k in ['value','text','description','name','institution','degree','court','role','date','start','end','start_date','end_date'] if value.get(k) is not None]
            return [' — '.join(str(v) for v in preferred)] if preferred else []
        return []
    for key,label in [('biography','Biography'),('education','Education'),('appointments','Appointments'),('service','Service'),('roles','Roles')]:
        entries=values(native.get(key))
        if entries:lines.extend(['\n## '+label,*entries])
    lines.append('\nThis is a source observation. Current office and cross-source identity are not established unless separately stated.')
    return '\n\n'.join(lines)
