"""Tests for normalize_deep_crawl.py. Sample rows below are copied verbatim (as literals) from
C:/Users/firas/Downloads/returnedfiles/deep_crawl_urls.jsonl, one per major `sources` tag plus one
multi-tag row, so the field-mapping rules are checked against real data before the streaming build
runs on the full file."""
import json
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import normalize_deep_crawl as ndc  # noqa: E402
import urlnorm  # noqa: E402

SOURCE_FILE = 'C:/Users/firas/Downloads/returnedfiles/deep_crawl_urls.jsonl'

# Real sample rows (raw JSON text exactly as they appear in the input file).
SAMPLE_FJC = '{"url": "https://1ecb9588-ea6f-4feb-971a-73265dbf079c.filesusr.com/ugd/4344b0_6cc9e7c82ccc4fc0b5d10217af64e31b.pdf", "kind": "pdf", "domain": "1ecb9588-ea6f-4feb-971a-73265dbf079c.filesusr.com", "label": "", "refs": 1, "sources": "fjc"}'
SAMPLE_CA_OAL = '{"url": "https://34c031f8-c9fd-4018-8c5a-4159cdff6b0d-cdn-endpoint.azureedge.net/-/media/bof-website/regulations/current-approved-regulations/2025-0902-03sr---approval.pdf?rev=9b60a44008b04983be44bd81cf769418&hash=23663A1CF756853188F193A3510CB4F5", "kind": "pdf", "domain": "34c031f8-c9fd-4018-8c5a-4159cdff6b0d-cdn-endpoint.azureedge.net", "label": "", "refs": 1, "sources": "ca_oal"}'
SAMPLE_NHTSA = '{"url": "http://aaafoundation.org/wp-content/uploads/2017/12/EvaluationOfDriversInRelationToPerSeReport.pdf", "kind": "pdf", "domain": "aaafoundation.org", "label": "", "refs": 1, "sources": "nhtsa"}'
SAMPLE_NY_DOS = '{"url": "https://ag.ny.gov/sites/default/files/opinions/I_2005-17_pw.pdf", "kind": "pdf", "domain": "ag.ny.gov", "label": "", "refs": 1, "sources": "ny_dos"}'
SAMPLE_CA_COURTS = '{"url": "https://ahea.assembly.ca.gov/sites/ahea.assembly.ca.gov/files/LPS%20Background%20with%20Appendices.pdf", "kind": "pdf", "domain": "ahea.assembly.ca.gov", "label": "", "refs": 1, "sources": "ca_courts"}'
SAMPLE_SUPREMECOURT_LABEL = '{"url": "https://www.supremecourt.gov/opinions/18pdf/18-422_9ol1.Pdf", "kind": "pdf", "domain": "www.supremecourt.gov", "label": "18-422 Rucho v. Common Cause (06/27/2019)", "refs": 4, "sources": "supremecourt"}'
SAMPLE_JPML = '{"url": "https://www.jpml.uscourts.gov/sites/jpml/files/121925%20Rules%20Revision%20-%20Redline.pdf", "kind": "pdf", "domain": "www.jpml.uscourts.gov", "label": "", "refs": 1, "sources": "jpml"}'
SAMPLE_MULTI = '{"url": "https://www.cpsc.gov/s3fs-public/High%20Energy%20Density%20Batteries_Status%20Memo_FY20_1-6bCleared-04012020.pdf?Qj4t_otWKfBZYLpvu4l6sUvx9ZJfFc4f", "kind": "pdf", "domain": "www.cpsc.gov", "label": "", "refs": 3, "sources": "cpsc;osha"}'
SAMPLE_HTML = '{"url": "https://www.osha.gov/laws-regs/regulations/standardnumber/1910", "kind": "html", "domain": "www.osha.gov", "label": "", "refs": 12, "sources": "osha"}'
SAMPLE_UNKNOWN_TAG = '{"url": "https://example.gov/some/page.html", "kind": "html", "domain": "example.gov", "label": "", "refs": 1, "sources": "some_new_job"}'


class TestTagInfo(unittest.TestCase):
    def test_known_tags_map_to_legend(self):
        self.assertEqual(ndc.tag_info('fjc'), ('federal_agency', 'federal', None, 'Federal Judicial Center'))
        self.assertEqual(ndc.tag_info('ca_oal'), ('state_agency', 'state', 'CA', 'California Office of Administrative Law'))
        self.assertEqual(ndc.tag_info('ca_courts')[1:3], ('state', 'CA'))
        self.assertEqual(ndc.tag_info('supremecourt')[0], 'federal_court')
        self.assertEqual(ndc.tag_info('jpml')[0], 'federal_court')

    def test_unknown_tag_falls_back_to_other(self):
        self.assertEqual(ndc.tag_info('some_new_job'), ('other', None, None, None))
        self.assertEqual(ndc.tag_info(None), ('other', None, None, None))

    def test_split_tags(self):
        self.assertEqual(ndc.split_tags('cpsc;osha'), ['cpsc', 'osha'])
        self.assertEqual(ndc.split_tags('fjc'), ['fjc'])
        self.assertEqual(ndc.split_tags(''), [])
        self.assertEqual(ndc.split_tags(None), [])


class TestBuildRow(unittest.TestCase):
    def build(self, sample_json, doc_index=None):
        raw = json.loads(sample_json)
        return ndc.build_row(raw, SOURCE_FILE, doc_index or {})

    def test_fjc_row(self):
        row = self.build(SAMPLE_FJC)
        self.assertEqual(row['layer'], 'federal_agency')
        self.assertEqual(row['org_name'], 'Federal Judicial Center')
        self.assertEqual(row['jurisdiction_level'], 'federal')
        self.assertIsNone(row['state'])
        self.assertEqual(row['doc_kind'], 'pdf')
        self.assertEqual(row['topics'], ['fjc'])
        self.assertEqual(row['host'], '1ecb9588-ea6f-4feb-971a-73265dbf079c.filesusr.com')
        for field in urlnorm.FIELDS:
            self.assertIn(field, row)

    def test_ca_oal_row_is_state_agency_in_ca(self):
        row = self.build(SAMPLE_CA_OAL)
        self.assertEqual(row['layer'], 'state_agency')
        self.assertEqual(row['state'], 'CA')
        self.assertEqual(row['org_name'], 'California Office of Administrative Law')
        self.assertEqual(row['doc_kind'], 'pdf')  # from .pdf extension even with a query string

    def test_nhtsa_row(self):
        row = self.build(SAMPLE_NHTSA)
        self.assertEqual(row['layer'], 'federal_agency')
        self.assertEqual(row['org_name'], 'National Highway Traffic Safety Administration')

    def test_ny_dos_row(self):
        row = self.build(SAMPLE_NY_DOS)
        self.assertEqual(row['layer'], 'state_agency')
        self.assertEqual(row['state'], 'NY')

    def test_ca_courts_row(self):
        row = self.build(SAMPLE_CA_COURTS)
        self.assertEqual(row['layer'], 'state_court')
        self.assertEqual(row['state'], 'CA')

    def test_supremecourt_row_keeps_source_label_as_title(self):
        row = self.build(SAMPLE_SUPREMECOURT_LABEL)
        self.assertEqual(row['layer'], 'federal_court')
        self.assertEqual(row['title'], '18-422 Rucho v. Common Cause (06/27/2019)')

    def test_jpml_row(self):
        row = self.build(SAMPLE_JPML)
        self.assertEqual(row['layer'], 'federal_court')
        self.assertEqual(row['org_name'], 'Judicial Panel on Multidistrict Litigation')

    def test_multi_tag_row_keeps_all_tags_uses_first_for_layer(self):
        row = self.build(SAMPLE_MULTI)
        self.assertEqual(row['topics'], ['cpsc', 'osha'])
        self.assertEqual(row['layer'], 'federal_agency')  # cpsc is first
        self.assertEqual(row['org_name'], 'Consumer Product Safety Commission')

    def test_html_row_doc_kind_page(self):
        row = self.build(SAMPLE_HTML)
        self.assertEqual(row['doc_kind'], 'page')

    def test_unknown_tag_row(self):
        row = self.build(SAMPLE_UNKNOWN_TAG)
        self.assertEqual(row['layer'], 'other')
        self.assertIsNone(row['org_name'])
        self.assertEqual(row['topics'], ['some_new_job'])

    def test_document_index_join_enriches_title_without_new_row(self):
        raw = json.loads(SAMPLE_FJC)
        doc_index = {raw['url']: {'kind': 'pdf', 'label': 'Joined Title'}}
        row = ndc.build_row(raw, SOURCE_FILE, doc_index)
        self.assertEqual(row['title'], 'Joined Title')

    def test_missing_url_raises(self):
        with self.assertRaises(ValueError):
            ndc.build_row({'kind': 'pdf', 'sources': 'fjc'}, SOURCE_FILE, {})

    def test_non_http_url_rejected_by_urlnorm(self):
        with self.assertRaises(ValueError):
            ndc.build_row({'url': 'ftp://example.com/file.pdf', 'sources': 'fjc'}, SOURCE_FILE, {})


class TestRejectReason(unittest.TestCase):
    def test_empty_url(self):
        self.assertEqual(ndc.reject_reason('{}', {}, ValueError('x')), 'empty_url')

    def test_not_http_url(self):
        raw = {'url': 'ftp://example.com/a'}
        self.assertEqual(ndc.reject_reason('...', raw, ValueError('x')), 'not_http_url')

    def test_malformed_json(self):
        self.assertEqual(ndc.reject_reason('not json', None, ValueError('x')), 'malformed_json')

    def test_malformed_row_not_a_dict(self):
        self.assertEqual(ndc.reject_reason('[]', [], ValueError('x')), 'malformed_row')


class TestRunOnTinyFixture(unittest.TestCase):
    """End-to-end streaming run on a tiny fixture (not the full input) to check determinism and
    the rejected-row paths without needing 178,961 real lines."""

    def test_run_produces_deterministic_counts(self):
        with tempfile.TemporaryDirectory() as tmp:
            urls_path = os.path.join(tmp, 'urls.jsonl')
            docs_path = os.path.join(tmp, 'docs.csv')
            out_dir = os.path.join(tmp, 'out')

            lines = [
                SAMPLE_FJC,
                SAMPLE_CA_OAL,
                '',  # blank line -> rejected 'empty'
                'not json at all',  # -> rejected 'malformed_json'
                json.dumps({'kind': 'pdf', 'sources': 'fjc'}),  # missing url -> rejected 'empty_url'
                json.dumps({'url': 'ftp://example.com/a', 'sources': 'fjc'}),  # -> rejected 'not_http_url'
                SAMPLE_UNKNOWN_TAG,
            ]
            with open(urls_path, 'w', encoding='utf-8') as f:
                f.write('\n'.join(lines) + '\n')
            with open(docs_path, 'w', encoding='utf-8', newline='') as f:
                f.write('kind,domain,refs,label,url,sources\n')

            report1 = ndc.run(urls_path, docs_path, out_dir)
            self.assertEqual(report1['rows_in'], len(lines))
            self.assertEqual(report1['rows_out'], 3)
            self.assertEqual(report1['rejected'], 4)
            self.assertEqual(report1['by_layer'].get('federal_agency'), 1)  # fjc
            self.assertEqual(report1['by_layer'].get('state_agency'), 1)  # ca_oal
            self.assertEqual(report1['by_layer'].get('other'), 1)  # unknown tag

            with open(os.path.join(out_dir, 'deep_crawl.jsonl'), encoding='utf-8') as f:
                out_rows = [json.loads(l) for l in f if l.strip()]
            self.assertEqual(len(out_rows), 3)

            with open(os.path.join(out_dir, 'deep_crawl.rejected.jsonl'), encoding='utf-8') as f:
                rej_rows = [json.loads(l) for l in f if l.strip()]
            self.assertEqual(len(rej_rows), 4)
            reasons = sorted(r['reason'] for r in rej_rows)
            self.assertEqual(reasons, ['empty', 'empty_url', 'malformed_json', 'not_http_url'])

            # re-run is byte-identical (deterministic, re-runnable)
            report2 = ndc.run(urls_path, docs_path, out_dir)
            self.assertEqual(report1['rows_out'], report2['rows_out'])
            self.assertEqual(report1['by_layer'], report2['by_layer'])


if __name__ == '__main__':
    unittest.main()
