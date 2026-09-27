"""Observed court layouts only. Original HTML and native extraction stay intact."""
import re
from urllib.parse import urljoin, urlsplit
from lxml import html


def _section_links(document, source_url, heading):
    """Snapshot explicit section navigation before chrome removal mutates the tree."""
    if not re.search(r'\b(?:judges?|forms?|rules?|procedures?)\b', heading, re.I):
        return []
    sections = document.xpath("//*[@id='block-menu-block-us-courts-menu-blocks-side-nav'] | //ul[contains(concat(' ',normalize-space(@class),' '),' usa-sidenav ')]")
    result, seen = [], set()
    for section in sections:
        # When the current page has children, siblings can be unrelated sections.
        branches = []
        current = [a for a in section.xpath('.//a[@href]') if urljoin(source_url, a.get('href')) == source_url]
        for anchor in current:
            parent = anchor.getparent()
            if parent is not None and parent.tag == 'li':
                branches.extend(parent.xpath('./ul | ./ol'))
        if not branches:
            # Saved CA11 layout has a section-only menu whose landing URL is
            # /eleventh-circuit-judges while the enclosing page is /judges.
            # Require both observed URL and anchor; never infer this for other
            # generic USWDS navigation menus.
            reviewed_ca11 = (source_url == 'https://www.ca11.uscourts.gov/judges'
                             and bool(section.xpath("./li/a[@href='/eleventh-circuit-judges']")))
            # This observed Drupal block is already the selected menu-level-2 section.
            # Do not turn a generic full-site USWDS sidenav into a section without proof.
            if current or (not reviewed_ca11 and section.get('id') != 'block-menu-block-us-courts-menu-blocks-side-nav'):
                continue
            branches = [section]
        for branch in branches:
            for anchor in branch.xpath('.//a[@href]'):
                raw = anchor.get('href', '')
                label = re.sub(r'\s+', ' ', anchor.text_content()).strip()
                url = urljoin(source_url, raw)
                parsed = urlsplit(url)
                if (not label or not raw or raw.startswith('#') or url == source_url or url in seen
                        or parsed.scheme not in ('http', 'https') or not parsed.hostname
                        or parsed.username is not None or parsed.password is not None):
                    continue
                seen.add(url)
                result.append({'label': label, 'url': url, 'observed_href': raw,
                               'source_location': 'current-page child menu' if current else 'court section side navigation'})
    return result


def page_reading(raw_bytes, source_url, title):
    document = html.fromstring(raw_bytes, parser=html.HTMLParser(encoding='utf-8'))
    selectors = [
        "//*[@id='main-content-wrapper']",
        "//*[@id='block-uswds-base-subtheme-content']",
        "//td[starts-with(@class,'subpage_text_index_')]",
        "//*[@id='main']//*[contains(concat(' ',normalize-space(@class),' '),' main ')]",
        "//*[@id='main_content'][normalize-space()]",
        "//main[normalize-space()]",
    ]
    selected = None
    for selector in selectors:
        nodes = document.xpath(selector)
        if nodes:
            selected = nodes[0]
            break
    if selected is None and '/LocalRules/LocalRules-TOC.html' in source_url:
        selector = '//body (standalone rule table of contents)'
        selected = document.find('body')
    if selected is None:
        raise ValueError('Unreviewed page layout: ' + source_url)
    headings = selected.xpath('.//h1[normalize-space()]')
    heading = headings[0].text_content().strip() if headings else title.split(' | ')[0]
    section_links = _section_links(document, source_url, heading)
    removed = 0
    for node in list(selected.iterdescendants()):
        if not isinstance(node.tag, str):
            continue
        flags = (node.get('class', '') + ' ' + node.get('id', '')).lower()
        if node.tag.lower() in {'script','style','noscript','form','nav','aside','footer','header','input','button','select'} or any(x in flags for x in ['breadcrumb','font-size','text-size','sfsearch','search-form','webworks_company_','element-invisible','visually-hidden','usa-sr-only']):
            if node.getparent() is not None:
                node.drop_tree(); removed += 1
    links = []
    def words(s):
        return re.sub(r'\s+', ' ', s or '')
    def render(node):
        if not isinstance(node.tag, str): return ''
        tag = node.tag.lower()
        if tag == 'br': return '\n'
        body = words(node.text) + ''.join(render(child) + words(child.tail) for child in node)
        body = body.strip()
        if tag == 'a' and body:
            raw = node.get('href', '')
            if body in {'Expand All', 'Contract All', 'Edit link'} or raw.lower().startswith('javascript:'):
                return ''
            url = urljoin(source_url, raw)
            if raw and not raw.startswith('#') and urlsplit(url).scheme in ('https','http'):
                label = words(node.text_content()).strip()
                if not any(item['url'] == url for item in links):
                    links.append({'label': label, 'url': url})
                return '[' + label.replace(']', '\\]') + '](' + url.replace(' ', '%20').replace(')', '%29') + ')'
        if re.fullmatch(r'h[1-6]', tag): return '\n\n' + '#' * min(int(tag[1]), 3) + ' ' + re.sub(r'^#+\s*','',words(body).strip()) + '\n\n' if body else ''
        if tag == 'li': return '\n- ' + body + '\n'
        if tag in {'td','th'}: return body + ' | '
        if tag == 'tr': return '\n' + body.rstrip(' |') + '\n'
        if tag in {'p','div','section','article','main','ul','ol','table'}: return '\n\n' + body + '\n\n' if body else ''
        return body
    text = re.sub(r'\n[ \t]+','\n', render(selected))
    text = re.sub(r'\n{3,}', '\n\n', text).strip()
    related = []
    if len(links) < 2 or re.search(r'links to the left|left side menu', text, re.I):
        seen = {x['url'] for x in links}
        for item in section_links:
            if item['url'] not in seen:
                seen.add(item['url']); links.append(item)
                related.append('- [' + item['label'].replace(']', '\\]') + '](' + item['url'].replace(' ', '%20').replace(')', '%29') + ')')
        if related:
            text += '\n\n## Related links from this court section\n\n' + '\n'.join(related)
    lines = [line.rstrip() for line in text.splitlines() if not re.fullmatch(r'(?:#+\s*)?(?:You are here|Search form|Search this site|Text Size:?)', line.strip(), re.I)]
    text = '\n'.join(lines).strip()
    text = re.sub(r'(?m)^\|\s*', '', text)
    if not re.match(r'^# ', text):
        text = '# ' + title.split(' | ')[0] + '\n\n' + text
    limited = not links and (len(text) < 600 or bool(re.search(r'links to the left|left side menu', text, re.I)))
    if limited:
        text += '\n\n_Reading note: This saved landing page has limited text; no relevant section links could be recovered from its saved HTML._'
    return {'text': text, 'links': links, 'notes': {'method': 'observed_court_content_selector_markdown', 'reader_version': 'section-nav-v2', 'selector': selector, 'removed_navigation_blocks': removed, 'original_preserved': True, 'cleaning_is_derived': True, 'links_preserved': len(links), 'section_navigation_links_preserved': len(related), 'limited_text': limited, 'content_scope': 'Saved directory or index page; linked files and individual judge pages are not implied downloaded.'}}
