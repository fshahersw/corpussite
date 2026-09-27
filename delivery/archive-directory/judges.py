"""Compact, human-facing judge projections over preserved identity evidence."""
from pathlib import Path
from contextlib import contextmanager
from functools import lru_cache
import datetime, hashlib, json, re, sqlite3, unicodedata
from urllib.parse import urlsplit
from judge_insights import decorate as profile_insights
import judge_report_links

ROOT = Path(__file__).resolve().parents[2]
FOLDER = ROOT / 'sources/judge_presentation_20260918'
DB = FOLDER / 'profiles.sqlite3'
DIRECTORY = ROOT / 'delivery/archive-directory/directory.sqlite3'
PA_FOLDER = ROOT / 'sources/pa_judge_portraits_20260919'
CAPTURE_ROOT = ROOT.parent / 'returnedfiles'
IMAGE_MIMES = {'JPEG':'image/jpeg', 'PNG':'image/png', 'WEBP':'image/webp', 'GIF':'image/gif'}


def sid(value): return hashlib.sha256(value.encode()).hexdigest()[:32]
def encoded(value): return json.dumps(value, ensure_ascii=False, separators=(',', ':'))
def normalize(value): return ''.join(c for c in unicodedata.normalize('NFKD', str(value)).casefold() if not unicodedata.combining(c))


def strings(value):
    if isinstance(value, str): return [value.strip()] if value.strip() else []
    if isinstance(value, list): return [s for item in value for s in strings(item)]
    if isinstance(value, dict):
        for key in ('text', 'value', 'description', 'literal_text', 'name'):
            if value.get(key): return strings(value[key])
    return []


def unique(values):
    found = {}
    for value in values:
        key = re.sub(r'\W+', '', normalize(value))
        if key and key not in found: found[key] = value
    return list(found.values())


def display_entries(entity, field):
    claims = entity.get('field_provenance', {}).get(field) or []
    # A coherent preferred education list avoids repeating every source's wording.
    # All variants remain in the original /api/record evidence representation.
    preferred = [v for claim in claims if claim.get('member_key') == entity.get('best_profile_member') for v in strings(claim.get('value'))]
    values = preferred if field == 'education' and preferred else strings(entity.get(field))
    return [{'text': value} for value in unique(values)]


def file_digest(path):
    with Path(path).open('rb') as source:
        return hashlib.file_digest(source, 'sha256').hexdigest()


def checked_json(path):
    value = json.loads(Path(path).read_text(encoding='utf-8-sig'))
    if not isinstance(value, dict): raise ValueError('Judge source receipt must be an object')
    return value


def bound_file(path, expected_hash, allowed_root):
    path = Path(path).resolve()
    if not path.is_relative_to(Path(allowed_root).resolve()) or not path.is_file():
        raise ValueError('Judge evidence path is outside its source root')
    if not isinstance(expected_hash, str) or not re.fullmatch(r'[0-9a-f]{64}', expected_hash) or file_digest(path) != expected_hash:
        raise ValueError('Judge evidence hash mismatch')
    return path


def validated_image(image, allowed_root):
    """A source identity reference alone never creates an installed photo flag."""
    from PIL import Image
    if (not isinstance(image, dict) or not isinstance(image.get('path'), str) or not isinstance(image.get('id'), str)
            or not re.fullmatch(r'(?:[A-Za-z0-9_-]{1,120}|pa-portrait:[0-9a-f]{24})', image['id'])):
        raise ValueError('Invalid judge image identity')
    path = bound_file(ROOT / image.get('path', ''), image.get('sha256'), allowed_root)
    size = path.stat().st_size
    if not 0 < size <= 20_000_000 or image.get('mime') not in IMAGE_MIMES.values():
        raise ValueError('Invalid judge image media metadata')
    if 'bytes' in image and (type(image['bytes']) is not int or image['bytes'] != size):
        raise ValueError('Judge image byte count mismatch')
    try:
        with Image.open(path) as decoded:
            if (IMAGE_MIMES.get(decoded.format) != image['mime'] or min(decoded.size) < 1
                    or decoded.width * decoded.height > 25_000_000):
                raise ValueError('Judge image format or dimensions mismatch')
            dimensions = decoded.size
            decoded.verify()
        with Image.open(path) as decoded: decoded.load()
    except (OSError, ValueError, Image.DecompressionBombError) as exc:
        raise ValueError('Invalid judge image decoding: ' + str(exc)) from exc
    if any(key in image and image[key] != value for key, value in zip(('width', 'height'), dimensions)):
        raise ValueError('Judge image dimension receipt mismatch')
    return image


def load_official_profiles():
    """Verify the small PA source layer without joining any entity or namesake."""
    manifest = PA_FOLDER / 'official_profiles.jsonl'
    if not manifest.exists():
        if PA_FOLDER.exists(): raise ValueError('Incomplete official judge source layer')
        return [], {}, {'profiles': 0, 'images': 0}
    summary = checked_json(PA_FOLDER / 'summary.json')
    receipt = summary.get('files', {}).get('official_profiles.jsonl', {})
    if (Path(receipt.get('path', '')).resolve() != manifest.resolve()
            or summary.get('capture_verification_errors') != []
            or summary.get('accepted_existing_entity_links') != 0):
        raise ValueError('Invalid official judge identity review receipt')
    bound_file(manifest, receipt.get('sha256'), PA_FOLDER)
    if manifest.stat().st_size != receipt.get('bytes'): raise ValueError('Official profile manifest size mismatch')
    rows = [json.loads(line) for line in manifest.read_text(encoding='utf-8-sig').splitlines() if line.strip()]
    if len(rows) != summary.get('eligible_separate_official_source_profiles'):
        raise ValueError('Official profile count differs from review')
    seen_ids = set(); seen_urls = set()
    court_routes = {'/courts/superior-court/superior-court-judges/': 'Superior Court of Pennsylvania',
                    '/courts/commonwealth-court-judges/': 'Commonwealth Court of Pennsylvania'}
    for row in rows:
        if not isinstance(row, dict): raise ValueError('Invalid official profile row')
        url = row.get('source_url', ''); parsed = urlsplit(url)
        route = next((prefix for prefix in court_routes if parsed.path.startswith(prefix)), None)
        expected_id = 'pa-official-profile:' + hashlib.sha256(url.encode()).hexdigest()[:24]
        if (parsed.scheme != 'https' or parsed.netloc != 'www.pacourts.us' or parsed.query or parsed.fragment
                or not route or not re.fullmatch(r'(?:judge|president-judge-emeritus)-[a-z0-9-]+', parsed.path[len(route):])
                or row.get('source_profile_id') != expected_id or expected_id in seen_ids or url in seen_urls
                or row.get('schema_version') != 'official-pa-judge-profile-source.v1'
                or row.get('canonical_identity_basis') != 'exact_official_individual_profile_url'
                or row.get('entity_id') is not None or row.get('existing_entity_merge_performed') is not False
                or row.get('eligible_as_separate_source_profile') is not True or row.get('current_service_verified') is not False
                or row.get('state') != 'Pennsylvania' or row.get('state_code') != 'PA' or row.get('court') != court_routes.get(route)):
            raise ValueError('Invalid or overlapping official profile identity')
        seen_ids.add(expected_id); seen_urls.add(url)
        name = row.get('name'); heading = row.get('profile_heading_as_published')
        if not isinstance(name, str) or not name.strip() or not isinstance(heading, str) or not heading.endswith(' ' + name):
            raise ValueError('Official profile name is not bound to its heading')
        role = heading[:-len(name)].strip()
        if role not in {'Judge', 'President Judge', 'President Judge Emeritus', 'President Judge Emerita'}:
            raise ValueError('Unrecognized published judicial heading')
        if row.get('emeritus_or_emerita_in_source_heading') is not ('Emerit' in role):
            raise ValueError('Historical role flag differs from published heading')
        capture = row.get('capture') or {}
        capture_path = bound_file(capture.get('path', ''), capture.get('sha256'), CAPTURE_ROOT)
        original = checked_json(capture_path); markdown = original.get('markdown') or ''; metadata = original.get('metadata') or {}
        if (not isinstance(markdown, str) or not isinstance(metadata, dict) or metadata.get('sourceURL') != url
                or metadata.get('statusCode') != 200 or '# ' + heading not in markdown
                or capture.get('metadata_source_url') != url):
            raise ValueError('Official profile capture identity mismatch')
        evidence_path = bound_file(row.get('identity_review_evidence_path', ''), row.get('identity_review_evidence_sha256'), PA_FOLDER / 'evidence')
        evidence = checked_json(evidence_path)
        required_checks = ('capture_hash_matches_reference', 'image_url_in_saved_markdown', 'profile_heading_in_saved_markdown',
                           'official_profile_url_in_metadata', 'prior_profile_section_association_verified')
        if (evidence.get('source_profile_id') != expected_id or evidence.get('official_profile_url') != url
                or evidence.get('name_as_published') != name or evidence.get('capture_sha256') != capture['sha256']
                or any(evidence.get('capture_checks', {}).get(key) is not True for key in required_checks)):
            raise ValueError('Official profile evidence is not bound to this identity')
        for field, evidence_field in [('profile_heading_as_published', 'official_role_heading'), ('term_as_published', 'official_term'),
                                      ('education_as_published', 'education_as_published'), ('career_as_published', 'career_as_published')]:
            value = row.get(field)
            if value != evidence.get(evidence_field): raise ValueError('Official profile field differs from reviewed evidence')
            texts = value if isinstance(value, list) else [value]
            if any(not isinstance(text, str) or text not in markdown for text in texts):
                raise ValueError('Official profile field is absent from saved capture')
        image_url = (row.get('image_reference') or {}).get('url', '')
        image_parsed = urlsplit(image_url)
        if (image_parsed.scheme != 'https' or image_parsed.netloc != 'www.pacourts.us'
                or not image_parsed.path.startswith('/Storage/media/images/') or image_url not in markdown
                or evidence.get('image_url') != image_url):
            raise ValueError('Official profile image reference is not bound to its capture')

    images = {}
    ready_path = PA_FOLDER / 'images_ready.json'
    if not ready_path.exists():
        if (PA_FOLDER / 'images.jsonl').exists(): raise ValueError('Official images have not completed validation')
    else:
        ready = checked_json(ready_path)
        if (ready.get('ready') is not True or ready.get('schema_version') != 'official-pa-portrait-publication.v1'
                or ready.get('profile_manifest_sha256') != receipt['sha256']):
            raise ValueError('Official image publication is not ready for these profiles')
        for filename, key in [('images.jsonl', 'images_manifest_sha256'), ('image_validation.json', 'validation_sha256'),
                              ('image_failures.jsonl', 'failures_sha256')]:
            bound_file(PA_FOLDER / filename, ready.get(key), PA_FOLDER)
        validation = checked_json(PA_FOLDER / 'image_validation.json')
        if validation.get('passed') is not True: raise ValueError('Official image validation failed')
        image_rows = [json.loads(line) for line in (PA_FOLDER / 'images.jsonl').read_text(encoding='utf-8-sig').splitlines() if line.strip()]
        failures = [json.loads(line) for line in (PA_FOLDER / 'image_failures.jsonl').read_text(encoding='utf-8-sig').splitlines() if line.strip()]
        if (ready.get('profiles') != len(rows) or ready.get('downloaded') != len(image_rows)
                or ready.get('failed') != len(failures) or ready.get('requested') != len(image_rows) + len(failures)):
            raise ValueError('Official image receipt counts differ')
        profiles_by_id = {row['source_profile_id']: row for row in rows}; image_ids = set()
        for image in image_rows:
            profile = profiles_by_id.get(image.get('source_profile_id')) if isinstance(image, dict) else None
            if (not profile or image.get('source_profile_id') in images or image.get('id') in image_ids
                    or image.get('source_url') != profile['source_url'] or image.get('image_source_url') != profile['image_reference']['url']):
                raise ValueError('Official image has an invalid or duplicate identity link')
            validated_image(image, PA_FOLDER / 'images')
            images[image['source_profile_id']] = image; image_ids.add(image['id'])
    return rows, images, {'profiles': len(rows), 'images': len(images), 'manifest_sha256': receipt['sha256'],
                          'images_manifest_sha256': file_digest(PA_FOLDER / 'images.jsonl') if ready_path.exists() else None}


def official_profile_view(row, image=None):
    url = row['source_url']; key = sid('official-profile:' + url)
    name = row['name']; heading = row['profile_heading_as_published']; role = heading[:-len(name)].strip()
    education = [{'text': value} for value in row['education_as_published']]
    career = [{'text': value} for value in row['career_as_published']]
    term = row.get('term_as_published') or ''
    service = [{'text': 'Term as published: ' + term}] if term else []
    card = {'id': key, 'entity_id': None, 'source_profile_id': row['source_profile_id'], 'profile_layer': 'official_source',
            'source_record_type': 'official_source_profile', 'source_url': url, 'name': name, 'courts': [row['court']],
            'states': ['Pennsylvania'], 'state': 'Pennsylvania', 'location': 'Pennsylvania', 'system': 'State', 'systems': ['state'],
            'role': role, 'profile_heading_as_published': heading, 'term_as_published': term,
            'emeritus_or_emerita_in_source_heading': row['emeritus_or_emerita_in_source_heading'],
            'current_service_verified': False, 'photo_url': '/judge-images/' + image['id'] if image else '',
            'biography_excerpt': '', 'has_biography': False, 'has_details': True, 'analysis_count': 0,
            'education_count': len(education), 'career_count': len(career) + len(service)}
    profile = card | {'biography': '', 'education': education, 'appointments': [], 'service': service,
        'professional_career': career, 'analyses': [], 'documents': [],
        'career_note': 'Role, term and career wording are preserved from the saved official profile. Current service has not been independently verified. This source profile has not been merged with the consolidated judge identities.',
        'sources': [{'title': heading, 'url': url, 'publisher': row['source_name']}],
        'photo_provenance': image.get('provenance') if image else None,
        'source_provenance': {'capture_sha256': row['capture']['sha256'], 'identity_review_sha256': row['identity_review_evidence_sha256'],
                              'identity_basis': row['canonical_identity_basis'], 'existing_entity_merge_performed': False}}
    return card, profile


def build():
    official_rows, official_images, official_receipt = load_official_profiles()
    portrait_file = FOLDER / 'portraits.jsonl'
    portraits = {}
    if portrait_file.exists():
        for line in portrait_file.read_text(encoding='utf-8-sig').splitlines():
            image = json.loads(line)
            if not image.get('entity_id') or image['entity_id'] in portraits: raise ValueError('Duplicate or missing portrait entity')
            portraits[image['entity_id']] = validated_image(image, FOLDER / 'images')
    FOLDER.mkdir(parents=True, exist_ok=True)
    temporary = FOLDER / 'profiles.building.sqlite3'
    if temporary.exists(): temporary.unlink()
    db = sqlite3.connect(temporary)
    try:
        summary = _populate_projection(db, portraits, official_rows, official_images, official_receipt)
        db.commit()
        integrity = db.execute('PRAGMA quick_check').fetchone()[0]
        if integrity != 'ok': raise ValueError('Judge projection integrity check failed')
    except Exception:
        db.close()
        temporary.unlink(missing_ok=True)
        raise
    else:
        db.close()
    temporary.replace(DB)
    (FOLDER / 'summary.json').write_text(json.dumps(summary, indent=2), encoding='utf-8')
    return summary


def _populate_projection(db, portraits, official_rows, official_images, official_receipt):
    db.executescript('''CREATE TABLE profiles(id TEXT PRIMARY KEY, entity_id TEXT UNIQUE, name TEXT,
        search TEXT, has_details INTEGER, has_photo INTEGER, has_biography INTEGER, analysis_count INTEGER,
        score INTEGER, card TEXT, profile TEXT);
        CREATE TABLE states(id TEXT,state TEXT,PRIMARY KEY(id,state));
        CREATE TABLE systems(id TEXT,system TEXT,PRIMARY KEY(id,system));
        CREATE TABLE courts(id TEXT,court TEXT,PRIMARY KEY(id,court));
        CREATE INDEX state_filter ON states(state,id); CREATE INDEX system_filter ON systems(system,id);
        CREATE INDEX court_filter ON courts(court COLLATE NOCASE,id);
        CREATE INDEX profile_order ON profiles(score DESC,name COLLATE NOCASE,id);
        CREATE TABLE images(id TEXT PRIMARY KEY,path TEXT,sha256 TEXT,mime TEXT);
    ''')
    for image in [*portraits.values(), *official_images.values()]:
        db.execute('INSERT INTO images VALUES(?,?,?,?)', (image['id'], image['path'], image['sha256'], image['mime']))
    count = detailed = pictured = analyses = 0
    original = ROOT / 'sources/judge_entities_20260918/entities.jsonl'
    with original.open(encoding='utf-8-sig') as entity_stream:
        for line in entity_stream:
            entity = json.loads(line); key = sid('entity:' + entity['entity_id'])
            image = portraits.get(entity['entity_id']); name = entity['name']
            education = display_entries(entity, 'education')
            appointments = display_entries(entity, 'appointments')
            service = display_entries(entity, 'service')
            career = display_entries(entity, 'professional_career')
            biography = entity.get('biography') or ''
            courts = unique(entity.get('courts') or [])
            # Prefer an official-style court label for the header when variants exist;
            # court name selection never establishes a current appointment.
            courts.sort(key=lambda value: (not value.startswith('U.S.'), len(value)))
            states = entity.get('state_labels') or []
            systems = [s for s in entity.get('judge_systems', []) if s not in {'unknown', ''}]
            analysis_rows = []
            for a in entity.get('analyses') or []:
                fields = ['analysis_id','analysis_type','label','metric','value','value_as_reported','unit',
                          'outcome','motion_type','period','numerator','denominator','cohort','sample_size',
                          'scale_minimum','scale_maximum','limitations','captured_at','source_url',
                          'methodology_url','native_chart_heading','native_chart_context','record_class',
                          'source_reported','independently_computed','category_overlap','completeness','component']
                clean = {k: a[k] for k in fields if a.get(k) is not None}
                clean['publisher'] = {'context': 'Lexis Context', 'lex_machina': 'Lex Machina'}.get(a.get('component'), a.get('publisher') or a.get('source_name') or 'Published analysis')
                analysis_rows.append(clean)
            has_details = bool(biography or education or appointments or service or career or analysis_rows or image)
            system = ', '.join(s.replace('_', ' ').title() for s in systems)
            card = {'id': key, 'entity_id': entity['entity_id'], 'profile_layer': 'consolidated_entity', 'name': name, 'courts': courts,
                    'states': states, 'state': '; '.join(states), 'location': ', '.join(states),
                    'system': system, 'systems': systems, 'role': 'Judicial profile',
                    'photo_url': '/judge-images/' + image['id'] if image else '',
                    'biography_excerpt': re.sub(r'\s+', ' ', biography)[:220],
                    'has_biography': bool(biography), 'has_details': has_details,
                    'analysis_count': len(analysis_rows), 'education_count': len(education),
                    'career_count': len(appointments) + len(service) + len(career)}
            documents = []
            seen_documents = set()
            for member in entity.get('members') or []:
                url = member.get('source_url') or ''
                parsed = urlsplit(url)
                if parsed.scheme in {'https','http'} and re.search(r'\.(pdf|docx?|rtf)$',parsed.path,re.I) and url not in seen_documents:
                    seen_documents.add(url)
                    documents.append({'id':sid(url),'title':'Judicial performance evaluation report' if member.get('source_class')=='state_evaluations' else 'Court document',
                                      'url':url,'source_url':url,'external':True,'availability':'publisher_link',
                                      'description':'Report referenced by this profile. It may cover multiple judges.'})
            profile = card | {'biography': biography, 'education': education, 'appointments': appointments,
                              'service': service, 'professional_career': career, 'analyses': analysis_rows,
                              'documents': documents, 'record_id': key,
                              'career_note': 'Career dates reflect saved records. Current office has not been independently verified.',
                              'current_service_verified': bool(entity.get('current_service_verified')),
                              'sources': [{'title': m.get('name') or name, 'url': m.get('source_url'),
                                           'publisher': m.get('source_class', '').replace('_', ' '),
                                           'captured_at': m.get('captured_at')} for m in entity.get('members') or []],
                              'photo_provenance': image.get('provenance') if image else None,
                              'provenance_url': '/api/record?id=' + key}
            score = 100 * bool(image) + 30 * bool(biography) + 25 * bool(analysis_rows) + 10 * bool(education) + 10 * bool(appointments or service or career)
            search = normalize(' '.join([name, *courts, *states, *[x.get('name','') for x in entity.get('aliases') or []]]))
            db.execute('INSERT INTO profiles VALUES(?,?,?,?,?,?,?,?,?,?,?)',
                       (key, entity['entity_id'], name, search, has_details, bool(image), bool(biography), len(analysis_rows), score, encoded(card), encoded(profile)))
            db.executemany('INSERT OR IGNORE INTO states VALUES(?,?)', [(key, s) for s in states])
            db.executemany('INSERT OR IGNORE INTO systems VALUES(?,?)', [(key, s) for s in systems])
            db.executemany('INSERT OR IGNORE INTO courts VALUES(?,?)', [(key, court) for court in courts])
            count += 1; detailed += has_details; pictured += bool(image); analyses += len(analysis_rows)
    consolidated_count = count; consolidated_pictured = pictured
    for source_row in official_rows:
        image = official_images.get(source_row['source_profile_id'])
        card, profile = official_profile_view(source_row, image); key = card['id']
        search = normalize(' '.join([card['name'], *card['courts'], *card['states'], card['role']]))
        score = 100 * bool(image) + 10 * bool(card['education_count']) + 10 * bool(card['career_count'])
        db.execute('INSERT INTO profiles VALUES(?,?,?,?,?,?,?,?,?,?,?)',
                   (key, None, card['name'], search, 1, bool(image), 0, 0, score, encoded(card), encoded(profile)))
        db.execute('INSERT INTO states VALUES(?,?)', (key, 'Pennsylvania'))
        db.execute('INSERT INTO systems VALUES(?,?)', (key, 'state'))
        db.execute('INSERT INTO courts VALUES(?,?)', (key, source_row['court']))
        count += 1; detailed += 1; pictured += bool(image)
    summary = {'built_at': datetime.datetime.now(datetime.timezone.utc).isoformat(), 'profiles': count,
               'detailed_profiles': detailed, 'profiles_with_photos': pictured, 'analysis_records': analyses,
               'consolidated_profiles': consolidated_count, 'official_source_profiles': len(official_rows),
               'consolidated_profiles_with_photos': consolidated_pictured, 'official_source_profiles_with_photos': len(official_images),
               'official_source_receipt': official_receipt,
               'source_entities_sha256': hashlib.sha256(original.read_bytes()).hexdigest(),
               'originals_modified': False, 'identity_merges_added': 0}
    return summary


@contextmanager
def connect():
    db = sqlite3.connect(DB.as_uri() + '?mode=ro', uri=True); db.row_factory = sqlite3.Row
    try: yield db
    finally: db.close()


def listing(params):
    if not DB.exists(): return {'items': [], 'total': 0, 'page': 1, 'limit': 24, 'states': [], 'systems': [], 'courts': []}
    try: page = max(1, int(params.get('page', 1))); limit = max(1, min(60, int(params.get('limit', 24))))
    except ValueError: page, limit = 1, 24
    where = []; args = []
    text_where = []; text_args = []
    for token in normalize(params.get('q', '')[:200]).split():
        text_where.append("search LIKE ? ESCAPE '\\'"); text_args.append('%' + token.replace('\\','\\\\').replace('%','\\%').replace('_','\\_') + '%')
    if text_where:
        # Source-printed aliases (e.g. JPML "M. Casey Rodgers") resolved by native ids; a missing/closed layer changes nothing.
        try:
            import judge_aliases
            alias_ids = sorted(judge_aliases.entity_ids_for_query(params.get('q', '')[:200]))[:500]
        except Exception:
            alias_ids = []
        expression = '(' + ' AND '.join(text_where) + ')'
        if alias_ids:
            expression = '(' + expression + ' OR entity_id IN (' + ','.join('?' for _ in alias_ids) + '))'
        where.append(expression); args.extend(text_args + alias_ids)
    for key, table, field in [('state','states','state'), ('system','systems','system')]:
        if params.get(key):
            expression=f'id IN (SELECT id FROM {table} WHERE {field}=? COLLATE NOCASE)'
            where.append(expression); args.append(params[key])
    court_facet_where = list(where); court_facet_args = list(args)
    if params.get('court'):
        where.append('id IN (SELECT id FROM courts WHERE court=? COLLATE NOCASE)'); args.append(params['court'])
    feature = {'details': 'has_details', 'photo': 'has_photo', 'biography': 'has_biography', 'analysis': 'analysis_count'}.get(params.get('has'))
    if feature:
        where.append(feature + '>0')
        court_facet_where.append(feature + '>0')
    report_links = judge_report_links.index()
    if params.get('has') == 'reports':
        keys = sorted(report_links)
        expression = 'entity_id IN ('+','.join('?' for _ in keys)+')' if keys else '0'
        where.append(expression);args.extend(keys)
        court_facet_where.append(expression);court_facet_args.extend(keys)
    # Evidence filters from the structured overlay (FJC-reported status, normalised role, appointing president).
    structured_facets = {}
    try:
        import judge_structured
        structured_facets = judge_structured.facets()
        chosen = {k: params[k] for k in ('status', 'role', 'president') if params.get(k)}
        if chosen:
            keys = sorted(judge_structured.ids_for(chosen))
            expression = 'entity_id IN ('+','.join('?' for _ in keys)+')' if keys else '0'
            where.append(expression);args.extend(keys)
            court_facet_where.append(expression);court_facet_args.extend(keys)
    except Exception:
        structured_facets = {}
    clause = ' WHERE ' + ' AND '.join(where) if where else ''
    order = 'name COLLATE NOCASE,id' if params.get('sort') == 'name' else 'score DESC,name COLLATE NOCASE,id'
    with connect() as db:
        facet_clause=' WHERE '+' AND '.join(court_facet_where) if court_facet_where else ''
        court_options=[{'value':r['court'],'label':r['court'],'count':r['n']} for r in db.execute(
            'SELECT court,count(DISTINCT id) n FROM courts WHERE id IN (SELECT id FROM profiles'+facet_clause+') GROUP BY court ORDER BY court COLLATE NOCASE',court_facet_args)]
        if params.get('court') and not any(r['value']==params['court'] for r in court_options):
            court_options.append({'value':params['court'],'label':params['court'],'count':0})
        total = db.execute('SELECT count(*) FROM profiles' + clause, args).fetchone()[0]
        rows = db.execute('SELECT card FROM profiles' + clause + ' ORDER BY ' + order + ' LIMIT ? OFFSET ?', args + [limit, (page-1)*limit]).fetchall()
        cards = [json.loads(r[0]) for r in rows]
        for card in cards: card['report_link_count'] = len(report_links.get(card.get('entity_id'), []))
        return {'items': cards, 'total': total, 'page': page, 'limit': limit,
                'states': [r[0] for r in db.execute('SELECT DISTINCT state FROM states ORDER BY state')],
                'systems': [r[0] for r in db.execute('SELECT DISTINCT system FROM systems ORDER BY system')],
                'courts': court_options, 'structured_facets': structured_facets}


def profile(key):
    if not DB.exists(): return None
    with connect() as db:
        row = db.execute('SELECT profile FROM profiles WHERE id=? OR entity_id=?', (key,key)).fetchone()
        if not row and DIRECTORY.exists():
            directory = sqlite3.connect(DIRECTORY.as_uri() + '?mode=ro', uri=True)
            try: group = directory.execute('SELECT preferred_id FROM display_groups WHERE id=?', (key,)).fetchone()
            finally: directory.close()
            if group: row = db.execute('SELECT profile FROM profiles WHERE id=?', (group[0],)).fetchone()
        if not row: return None
        result = json.loads(row[0])
        result['analysis_references'] = judge_report_links.references(result.get('entity_id'))
        try:
            import judge_aliases
            result['aliases'] = judge_aliases.aliases_for(result.get('entity_id'))
        except Exception:
            result['aliases'] = []
        for item in result.get('analyses', []):
            if urlsplit(item.get('source_url') or '').hostname == 'judicialperformance.colorado.gov':
                item['publisher'] = 'Colorado Judicial Performance Evaluation'
        return profile_insights(result)


@lru_cache(maxsize=256)
def _image_verified(path_string, expected_hash, size, modified_ns, changed_ns):
    with Path(path_string).open('rb') as source:
        return hashlib.file_digest(source,'sha256').hexdigest()==expected_hash


def image_file(key):
    if not DB.exists(): return None
    with connect() as db: row = db.execute('SELECT path,mime,sha256 FROM images WHERE id=?', (key,)).fetchone()
    if not row: return None
    path = (ROOT / row[0]).resolve()
    if not any(path.is_relative_to(root.resolve()) for root in (FOLDER / 'images', PA_FOLDER / 'images')): return None
    try:
        stat=path.stat()
        valid=path.is_file() and row[1] in IMAGE_MIMES.values() and _image_verified(str(path),row[2],stat.st_size,stat.st_mtime_ns,stat.st_ctime_ns)
        return (path,row[1]) if valid else None
    except OSError: return None


if __name__ == '__main__': print(json.dumps(build()))
