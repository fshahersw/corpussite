"""Exact native-contract integration check against a bounded local export."""
import importlib.util
import json
import pathlib
import tempfile
import unittest

HERE=pathlib.Path(__file__).resolve().parent
spec=importlib.util.spec_from_file_location('federal_export',HERE/'federal.py')
export=importlib.util.module_from_spec(spec);spec.loader.exec_module(export)

class FederalExportTests(unittest.TestCase):
    def test_native_agency_and_regulation_details_equal(self):
        import agency_safety
        import federal_regulations
        with tempfile.TemporaryDirectory() as tmp:
            folder=pathlib.Path(tmp)
            with (folder/'contexts.jsonl').open('w',encoding='utf-8') as contexts:
                export.export_regulations(folder,contexts,2)
                export.export_agency(folder,contexts,2)
            checked=0
            for path in folder.glob('*.jsonl'):
                if path.name=='contexts.jsonl':continue
                for line in path.read_text(encoding='utf-8').splitlines():
                    row=json.loads(line);dataset=row['dataset']
                    if dataset.startswith('agency_safety_'):native=agency_safety.record(dataset[len('agency_safety_'):],row['id'])
                    elif dataset=='federal_regulations_sections':native=federal_regulations.section(row['id'])
                    elif dataset=='federal_regulations_documents':native=federal_regulations.document(row['id'])
                    else:continue
                    self.assertEqual(row['detail'],native,(dataset,row['id']));checked+=1
            self.assertEqual(checked,28)
            contexts={r['key']:r['data'] for r in map(json.loads,(folder/'contexts.jsonl').read_text(encoding='utf-8').splitlines())}
            for citation in ('870','870.3610'):
                native=agency_safety.for_cfr(citation,limit=100)
                saved=contexts['agency:cfr:21:'+citation]
                self.assertEqual(native['classifications'],saved['classifications'][:100])
                self.assertEqual(native['total'],len(saved['classifications']))
                self.assertEqual(native['related_pma_rows'],saved['related_pma_rows'])

    def test_observed_publisher_range_identifier_is_preserved(self):
        import federal_regulations as mod
        raw={'title':'21','part':'112','section':'112.48-112.49'}
        detail=export.regulation_detail(mod,raw)
        self.assertEqual(detail['id'],'cfr:21:112.48-112.49')
        self.assertEqual(detail['section'],raw['section'])
        self.assertIsNone(mod._parse_ref(detail['id']))

if __name__=='__main__':unittest.main()
