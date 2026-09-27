"""Adapter tests for court_documents.py (generic view contract). Written before verifying the adapter."""
import hashlib
import importlib.util
import json
import re
import shutil
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location('court_documents_adapter', HERE / 'court_documents.py')
a = importlib.util.module_from_spec(spec)
spec.loader.exec_module(a)

PHONE = re.compile(r'\(?\b\d{3}\)?[-. ]\d{3}[-. ]\d{4}\b')


def walk(value):
    if isinstance(value, dict):
        for k, v in value.items():
            yield k, v
            yield from walk(v)
    elif isinstance(value, list):
        for v in value:
            yield from walk(v)


def assert_public(test, payload):
    for key, value in walk(payload):
        if key in ('local_rel_path',):
            test.fail('filesystem path key leaked: %s' % key)
        if isinstance(value, str):
            test.assertNotIn(':\\', value)
            test.assertNotIn('C:/Users', value)
            test.assertNotIn('SW-BULK', value)
            test.assertNotIn('mailto:', value)
            test.assertIsNone(PHONE.search(value), value)


class GateFailsClosed(unittest.TestCase):
    def test_tampered_hash_returns_not_available(self):
        backup = a.VALIDATION_PATH.read_bytes()
        try:
            gate = json.loads(backup)
            gate['data_files'][0]['sha256'] = '0' * 64
            a.VALIDATION_PATH.write_bytes(json.dumps(gate).encode('utf-8'))
            result = a.listing({})
            self.assertFalse(result['available'])
            self.assertEqual(result['results'], [])
        finally:
            a.VALIDATION_PATH.write_bytes(backup)

    def test_missing_validation_returns_not_available(self):
        backup = a.VALIDATION_PATH.read_bytes()
        try:
            a.VALIDATION_PATH.unlink()
            result = a.listing({})
            self.assertFalse(result['available'])
        finally:
            a.VALIDATION_PATH.write_bytes(backup)


@unittest.skipUnless(Path(a.DATA / 'index.sqlite3').exists(), 'index not built in this environment')
class ListingShape(unittest.TestCase):
    def test_shape_and_total(self):
        d = a.listing({'limit': '5'})
        self.assertTrue(d['available'])
        self.assertGreater(d['total'], 40000)
        self.assertEqual((d['page'], d['limit'], len(d['results'])), (1, 5, 5))
        self.assertTrue(d['qualification'])
        names = [f['name'] for f in d['filters']]
        self.assertEqual(names, ['q', 'state', 'doc_type', 'file_type', 'manifest', 'link_status', 'host'])
        self.assertEqual([c['key'] for c in d['columns']], ['document', 'doc_type', 'jurisdiction', 'manifest', 'size', 'host'])

    def test_host_filter(self):
        con, _ = a._connect()
        try:
            row = con.execute("SELECT host FROM documents WHERE host IS NOT NULL LIMIT 1").fetchone()
        finally:
            con.close()
        d = a.listing({'host': row['host'], 'limit': '5'})
        self.assertTrue(d['available'])
        self.assertGreater(d['total'], 0)
        for r in d['results']:
            self.assertEqual(r['cells']['host'], row['host'])
        for r in d['results']:
            self.assertTrue({'id', 'title', 'subtitle', 'cells', 'badges', 'links'} <= set(r))
        assert_public(self, d)

    def test_doc_type_filter(self):
        d = a.listing({'doc_type': 'local_rule', 'limit': '5'})
        self.assertTrue(d['available'])
        self.assertGreater(d['total'], 0)
        for r in d['results']:
            self.assertEqual(r['cells']['doc_type'], 'Local rule')

    def test_state_filter(self):
        d = a.listing({'state': 'ct', 'limit': '5'})
        self.assertTrue(d['available'])
        self.assertGreater(d['total'], 0)
        for r in d['results']:
            self.assertEqual(r['cells']['jurisdiction'], 'CT')

    def test_bad_params_never_raise(self):
        d = a.listing({'limit': 'not-a-number', 'page': '-9', 'doc_type': 'nonsense', 'unknown_param': 'x'})
        self.assertTrue(d['available'])

    def test_pagination_capped_at_100(self):
        d = a.listing({'limit': '99999'})
        self.assertEqual(d['limit'], 100)


@unittest.skipUnless(Path(a.DATA / 'index.sqlite3').exists(), 'index not built in this environment')
class DetailShape(unittest.TestCase):
    def test_detail_roundtrip(self):
        first = a.listing({'limit': '1'})['results'][0]
        d = a.detail(first['id'])
        self.assertIsNotNone(d)
        self.assertTrue({'id', 'title', 'subtitle', 'qualification', 'facts', 'sections', 'links'} <= set(d))
        assert_public(self, d)

    def test_unknown_id_returns_none(self):
        self.assertIsNone(a.detail('court-expansion-artifact:doesnotexist0000000000'))

    def test_malformed_id_returns_none(self):
        self.assertIsNone(a.detail('../../etc/passwd'))
        self.assertIsNone(a.detail(''))
        self.assertIsNone(a.detail(None))


@unittest.skipUnless(Path(a.DATA / 'index.sqlite3').exists(), 'index not built in this environment')
class OriginalServing(unittest.TestCase):
    def test_matching_hash_serves_bytes(self):
        con, _ = a._connect()
        try:
            row = con.execute("SELECT id, sha256 FROM documents WHERE local_rel_path IS NOT NULL LIMIT 1").fetchone()
        finally:
            con.close()
        result = a.original(row['id'])
        if result is None:
            self.skipTest('sample file not present on disk in this environment')
        blob, mime, filename = result
        self.assertEqual(hashlib.sha256(blob).hexdigest(), row['sha256'])
        self.assertTrue(filename)

    def test_unknown_id_returns_none(self):
        self.assertIsNone(a.original('court-expansion-artifact:doesnotexist0000000000'))

    def test_path_traversal_id_returns_none(self):
        self.assertIsNone(a.original('../../../../windows/system32'))


@unittest.skipUnless(Path(a.DATA / 'index.sqlite3').exists(), 'index not built in this environment')
class EmbeddingHooks(unittest.TestCase):
    def test_for_state_shape(self):
        payload = a.for_state('CT')
        self.assertIsNotNone(payload)
        self.assertLessEqual(len(payload['results']), 25)
        self.assertIn('total', payload)
        assert_public(self, payload)

    def test_for_state_unknown_returns_none(self):
        self.assertIsNone(a.for_state('ZZ'))
        self.assertIsNone(a.for_state('california'))

    def test_for_state_includes_court_state_only_rows(self):
        """Repair review defect: for_state filtered only on `state`, missing rows that are
        link_status='matched' to a court whose own state matches but whose manifest jurisdiction_codes
        is null/ambiguous. Find such a row directly and confirm for_state(that state) surfaces it."""
        con, _ = a._connect()
        try:
            row = con.execute(
                "SELECT id, court_state FROM documents WHERE court_state IS NOT NULL AND (state IS NULL OR state != court_state) LIMIT 1"
            ).fetchone()
        finally:
            con.close()
        if row is None:
            self.skipTest('no court_state-only row present in this build')
        payload = a.for_state(row['court_state'])
        self.assertIsNotNone(payload)
        ids = [r['id'] for r in payload['results']]
        # the row may not be in the top 25 by date, but the total count must reflect it via a basis-labelled count
        self.assertIn('counts_by_basis', payload)
        self.assertGreater(payload['counts_by_basis']['court_of_matched_court'], 0)

    def test_for_court_shape_or_none(self):
        con, _ = a._connect()
        try:
            row = con.execute("SELECT court_id FROM documents WHERE link_status='matched' LIMIT 1").fetchone()
        finally:
            con.close()
        payload = a.for_court(row['court_id'])
        self.assertIsNotNone(payload)
        self.assertLessEqual(len(payload['results']), 25)
        assert_public(self, payload)

    def test_for_court_unknown_returns_none(self):
        self.assertIsNone(a.for_court('not-a-real-court-id-xyz'))

    def test_for_court_shared_host_bucket_for_ambiguous_court(self):
        """Repair review defect: for_court('txsd') returned None for courts that only appear as
        ambiguous candidates (shared host). Must now return a shared_host bucket, never a bare None,
        when the court has no matched rows but does have candidate rows."""
        con, _ = a._connect()
        try:
            row = con.execute(
                "SELECT id, candidate_court_ids FROM documents WHERE link_status='ambiguous' LIMIT 1"
            ).fetchone()
        finally:
            con.close()
        if row is None:
            self.skipTest('no ambiguous row present in this build')
        candidate_id = json.loads(row['candidate_court_ids'])[0]
        con, _ = a._connect()
        try:
            has_matched = con.execute(
                "SELECT COUNT(*) FROM documents WHERE court_id = ? AND link_status='matched'", (candidate_id,)
            ).fetchone()[0]
        finally:
            con.close()
        payload = a.for_court(candidate_id)
        if has_matched:
            self.assertIsNotNone(payload)
        else:
            self.assertIsNotNone(payload)
            self.assertIn('shared_host', payload)
            self.assertGreater(payload['shared_host']['total'], 0)
            self.assertIn('link', payload['shared_host'])
            assert_public(self, payload)


if __name__ == '__main__':
    unittest.main()
