"""Integration checks against the running local directory; no source mutations."""
import json, pathlib, unittest, urllib.request, urllib.error, gzip, hashlib, tempfile
import server

BASE='http://127.0.0.1:8769'
def request(path,method='GET',headers=None):
    try:
        response=urllib.request.urlopen(urllib.request.Request(BASE+path,method=method,headers={'Accept-Encoding':'gzip',**(headers or {})}),timeout=30)
    except urllib.error.HTTPError as error:response=error
    with response:
        body=response.read()
        if response.headers.get('Content-Encoding')=='gzip':body=gzip.decompress(body)
        return response.status,dict(response.headers),body
def get(path):
    code,_,body=request(path);assert code==200,(code,path,body[:200]);return json.loads(body)

class DirectoryChecks(unittest.TestCase):
    def test_published_counts_and_inventory(self):
        summary=get('/api/summary');original=json.loads((pathlib.Path(__file__).parent.parent/'focused_legal_corpus/summary.json').read_text())
        self.assertEqual(summary['published']['capture_records'],original['selected_capture_records'])
        self.assertFalse(summary['published']['full_corpus_complete'])
        self.assertEqual(get('/api/counties')['total'],3144)
    def test_real_full_text_search_and_file(self):
        results=get('/api/documents?group=laws&q=due%20process&limit=3')
        self.assertGreater(results['total'],0)
        row=results['items'][0];detail=get('/api/record?id='+row['id'])
        self.assertTrue(detail['has_text']);self.assertGreater(detail['text_characters'],0)
        code,headers,body=request(row['text_url']);self.assertEqual(code,200);self.assertGreater(len(body),0)
        self.assertEqual(headers['X-Content-Type-Options'],'nosniff')
    def test_county_exact_geography(self):
        county=get('/api/counties?q=47029')['items'][0]
        self.assertEqual(county['name'],'Cocke County')
        results=get('/api/documents?group=all&county=47029&state=Tennessee')
        self.assertGreaterEqual(results['total'],3)
        self.assertTrue(all('Cocke County' in x['county'] for x in results['items']))
        self.assertEqual(get('/api/documents?group=counties&county=47029&state=Alaska')['total'],0)
    def test_pagination_and_safe_query(self):
        first=get('/api/documents?limit=3&page=1');second=get('/api/documents?limit=3&page=2')
        self.assertFalse({x['id'] for x in first['items']}&{x['id'] for x in second['items']})
        self.assertLess(get('/api/documents?q=%22%20OR%201%3D1--')['total'],first['total'])
        self.assertEqual(request('/api/documents?page=invalid')[0],400)
    def test_judge_analysis_is_bound_to_observation(self):
        results=get('/api/documents?group=judges&q=Sabraw&view=sources')
        row=next(x for x in results['items'] if x['dataset']=='judge_vendor')
        detail=get('/api/record?id='+row['id'])
        self.assertEqual(len(detail['metadata']['analyses']),449)
        self.assertEqual(detail['state'],'California')
    def test_federal_links_are_not_saved_bodies(self):
        results=get('/api/documents?group=federal&dataset=federal&view=sources&limit=100')
        self.assertEqual(results['total'],591)
        self.assertTrue(any(not x['has_text'] for x in results['items']))
        records=[]
        for page in range(1,7):records.extend(get('/api/documents?group=federal&dataset=federal&view=sources&limit=100&page='+str(page))['items'])
        self.assertEqual(sum(x['has_text'] for x in records),15)
    def test_dataset_catalogue_is_retired_and_html_is_inert(self):
        # The "Data downloads" and "Collection status" pages were removed with the data only they read.
        self.assertEqual(request('/api/datasets')[0],404)
        summary=get('/api/summary');self.assertNotIn('datasets',summary);self.assertNotIn('live',summary)
        result=get('/api/documents?group=counties&county=47029')['items']
        raw=next(x['original_url'] for x in result if 'cockecountytn.gov' in x['source_url'])
        code,headers,_=request(raw);self.assertEqual(code,200)
        self.assertEqual(headers['Content-Type'],'application/octet-stream')
        self.assertIn('attachment',headers['Content-Disposition'])
    def test_server_is_read_only_and_file_ids_are_allowlisted(self):
        for path in ['/.auth/firecrawl_judge.dpapi','/server.py','/files/../../.auth/firecrawl_judge.dpapi','/files/invalid']:
            self.assertEqual(request(path)[0],404,path)
        self.assertEqual(request('/api/summary',headers={'Host':'untrusted.example'})[0],403)
        self.assertEqual(request('/api/summary',method='POST')[0],405)
    def test_new_downloads_remain_separate(self):
        result=get('/api/documents?dataset=pending_publication&view=sources&limit=100')
        receipt=json.loads((pathlib.Path(__file__).resolve().parents[2]/'sources/directory_pending_20260918/validation.json').read_text(encoding='utf-8-sig'))
        self.assertTrue(receipt['valid'])
        self.assertEqual(result['total'],receipt['resource_records'])
        self.assertTrue(all(x['quality']=='Awaiting publication validation' for x in result['items']))
    def test_county_availability_includes_pending_without_publishing_it(self):
        county=get('/api/counties?q=19003')['items'][0]
        self.assertGreater(county['pending_local_resources'],0)
        self.assertEqual(county['local_resources'],county['published_local_resources']+county['pending_local_resources'])
        self.assertFalse(county['complete'])
    def test_browser_county_profiles_keep_dom_qualification_and_exact_geography(self):
        result=get('/api/documents?dataset=trellis_browser_counties&view=sources&limit=100')
        batches=server.load_trellis_browser_batches()
        self.assertEqual(result['total'],sum(b['profile_captures'] for b in batches))
        self.assertEqual({b['folder'] for b in batches},set(server.TRELLIS_BROWSER_BATCHES))
        rows=result['items']
        for page in range(2,(result['total']+99)//100+1):
            rows.extend(get('/api/documents?dataset=trellis_browser_counties&view=sources&limit=100&page='+str(page))['items'])
        self.assertEqual(len({r['source_url'] for r in rows}),result['total'])
        for row in rows:
            detail=get('/api/record?id='+row['id'])
            source=detail['metadata']
            self.assertEqual(source['raw_representation_kind'],'rendered_dom_json_not_http')
            self.assertIn(source['county'],detail['text'])
            self.assertFalse(source['metadata']['paid_document_entitlement_verified'])
            self.assertEqual(len(source['county_geoids']),1)
            county=get('/api/counties?q='+source['county_geoids'][0])['items'][0]
            self.assertEqual(county['name'],source['county'])
            self.assertEqual(county['state'],source['state'])
            self.assertGreater(county['saved_profile_backfill_count'],0)
    def test_secondary_membership_filter_is_available(self):
        result=get('/api/documents?group=judges&kind=needs_content_review&limit=10')
        self.assertGreater(result['total'],0)
        self.assertIn('needs_content_review',result['kinds'])

class BrowserBatchValidationChecks(unittest.TestCase):
    """Small synthetic batches exercise ingestion without building or touching live data."""
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root=pathlib.Path(self.temp.name)
        self.folders=('sources/counties/batch_one','sources/counties/batch_two')
        self.write_batch(self.folders[0],'first','https://trellis.law/coverage/georgia/first')
        self.write_batch(self.folders[1],'second','https://trellis.law/coverage/georgia/second')

    def write_json(self,path,value):
        path.parent.mkdir(parents=True,exist_ok=True)
        path.write_text(json.dumps(value),encoding='utf-8')

    def write_batch(self,folder,key,url):
        base=self.root/folder;base.mkdir(parents=True,exist_ok=True)
        raw_path=folder+'/dom/profile.json';text_path=folder+'/text/profile.txt';metadata_path=folder+'/metadata/profile.json'
        self.write_json(self.root/raw_path,{'source_url':url,'captured_at':'2026-09-18T22:13:00Z','county':'Example County'})
        (self.root/text_path).parent.mkdir(parents=True,exist_ok=True)
        (self.root/text_path).write_text('Example County profile fields',encoding='utf-8')
        sha=lambda path:hashlib.sha256((self.root/path).read_bytes()).hexdigest()
        metadata={'source_url':url,'captured_at':'2026-09-18T22:13:00Z','state':'Georgia','county':'Example County','county_geoid':'13999','source_representation_path':raw_path,'source_representation_sha256':sha(raw_path),'text_path':text_path,'text_sha256':sha(text_path),'paid_document_entitlement_verified':False,'protected_case_fields_scraped':False,'case_lists_included':False}
        self.write_json(self.root/metadata_path,metadata)
        row={'id':key,'title':'Example County profile','source_url':url,'captured_at':metadata['captured_at'],'state':'Georgia','county':'Example County','county_geoids':['13999'],'group':'counties','resource_kind':'coverage_county','raw_representation_kind':'rendered_dom_json_not_http','raw_path':raw_path,'sha256':sha(raw_path),'text_path':text_path,'text_sha256':sha(text_path),'metadata_path':metadata_path,'metadata_sha256':sha(metadata_path),'metadata':metadata}
        self.write_manifest(folder,[row])

    def write_manifest(self,folder,rows):
        base=self.root/folder
        manifest=''.join(json.dumps(r)+'\n' for r in rows).encode('utf-8')
        (base/'resources.jsonl').write_bytes(manifest)
        digest=hashlib.sha256(manifest).hexdigest()
        self.write_json(base/'summary.json',{'profile_captures':len(rows),'resources_sha256':digest,'source_overlap_with_prior_saved_profiles':0})
        self.write_json(base/'validation.json',{'status':'passed','profiles':len(rows),'unique_source_urls':len(rows),'resources_sha256':digest,'directory_integration_ready_with_dom_qualification':True,'county_name_state_and_observed_parent_links_verified':True,'source_overlap_dedup_verified':True})

    def load(self):return server.load_trellis_browser_batches(self.root,self.folders)

    def test_two_valid_batches_preserve_separate_evidence(self):
        batches=self.load()
        self.assertEqual([b['folder'] for b in batches],list(self.folders))
        self.assertEqual(sum(b['profile_captures'] for b in batches),2)
        self.assertEqual([r['id'] for b in batches for r in b['resources']],['first','second'])

    def test_missing_second_receipt_rejected(self):
        (self.root/self.folders[1]/'validation.json').unlink()
        with self.assertRaisesRegex(ValueError,'Incomplete or invalid'):self.load()

    def test_failed_or_changed_second_receipt_rejected(self):
        for field,value in [('status','failed'),('resources_sha256','0'*64),('profiles',2),('directory_integration_ready_with_dom_qualification',False)]:
            with self.subTest(field=field):
                self.write_batch(self.folders[1],'second','https://trellis.law/coverage/georgia/second')
                path=self.root/self.folders[1]/'validation.json'
                receipt=json.loads(path.read_text());receipt[field]=value;self.write_json(path,receipt)
                with self.assertRaisesRegex(ValueError,'Unvalidated or changed'):self.load()

    def test_cross_batch_duplicate_id_or_url_rejected(self):
        for key,url in [('first','https://trellis.law/coverage/georgia/second'),('second','https://trellis.law/coverage/georgia/first/')] :
            with self.subTest(key=key,url=url):
                self.write_batch(self.folders[1],key,url)
                with self.assertRaisesRegex(ValueError,'Duplicate'):self.load()

    def test_changed_artifact_rejected_even_with_valid_receipt(self):
        for name in ('dom/profile.json','text/profile.txt','metadata/profile.json'):
            with self.subTest(artifact=name):
                self.write_batch(self.folders[1],'second','https://trellis.law/coverage/georgia/second')
                path=self.root/self.folders[1]/name;path.write_bytes(path.read_bytes()+b' ')
                with self.assertRaisesRegex(ValueError,'Changed Trellis browser artifact'):self.load()

    def test_artifact_cannot_escape_its_batch(self):
        rows=self.load()[1]['resources']
        rows[0]['text_path']=self.folders[0]+'/text/profile.txt'
        self.write_manifest(self.folders[1],rows)
        with self.assertRaisesRegex(ValueError,'outside its batch'):self.load()

    def test_embedded_metadata_must_equal_saved_metadata(self):
        rows=self.load()[1]['resources'];rows[0]['metadata']['county']='Another County'
        self.write_manifest(self.folders[1],rows)
        with self.assertRaisesRegex(ValueError,'Unbound Trellis browser metadata'):self.load()

if __name__=='__main__':unittest.main(verbosity=2)
