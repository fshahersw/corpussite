"""Exactness and bounded-failure checks for post-upload catalog verification."""
import unittest
from collections import Counter
from types import SimpleNamespace
from urllib.parse import parse_qs,urlsplit

import import_catalog as subject
from supabase_client import StatementTimeout


class FakeClient:
    def __init__(self,ordinals,timeout_width=None,header=None):
        self.ordinals=ordinals;self.timeout_width=timeout_width;self.header=header;self.calls=[]
    def call(self,method,path,*,headers):
        self.calls.append((method,path,headers))
        query=parse_qs(urlsplit(path).query)
        assert method=='GET' and headers=={'Prefer':'count=exact'}
        assert query['select']==['id'] and query['limit']==['1']
        lower=upper=None
        for value in query.get('ordinal',[]):
            op,n=value.split('.');n=int(n)
            if op=='gte':lower=n
            elif op=='lt':upper=n
            else:raise AssertionError(op)
        if self.timeout_width is not None and lower is not None and upper is not None and upper-lower>self.timeout_width:
            raise StatementTimeout('statement timeout')
        count=sum((lower is None or value>=lower) and (upper is None or value<upper) for value in self.ordinals)
        return SimpleNamespace(headers={'Content-Range':self.header if self.header is not None else ('0-0/'+str(count) if count else '*/0')})


def buckets(values):return dict(Counter(subject.ordinal_bucket(v) for v in values))


class CountTests(unittest.TestCase):
    def test_sparse_signed_ordinals_and_duplicate_ordinals_count_exactly(self):
        values=[-50_001,-1,0,0,1,49_999,50_000,50_001,2_000_000]
        client=FakeClient(values)
        actual,receipt=subject.exact_catalog_count(client,'county, & laws',buckets(values))
        self.assertEqual(actual,len(values));self.assertEqual(receipt['requests'],len(buckets(values))+1)
        self.assertTrue(all(parse_qs(urlsplit(path).query)['dataset']==['eq.county, & laws'] for _,path,_ in client.calls))

    def test_extra_rows_in_lower_tail_gaps_and_upper_tail_fail(self):
        expected=[10,100_001]
        for extra in [-1,75_000,5_000_000]:
            with self.subTest(extra=extra),self.assertRaisesRegex(RuntimeError,'range count mismatch'):
                subject.exact_catalog_count(FakeClient(expected+[extra]),'test',buckets(expected))

    def test_missing_row_cannot_cancel_extra_row_in_another_partition(self):
        with self.assertRaisesRegex(RuntimeError,'range count mismatch'):
            subject.exact_catalog_count(FakeClient([50_001,50_002]),'test',buckets([1,50_001]))

    def test_empty_source_requires_empty_remote(self):
        self.assertEqual(subject.exact_catalog_count(FakeClient([]),'test',{})[0],0)
        with self.assertRaisesRegex(RuntimeError,'range count mismatch'):
            subject.exact_catalog_count(FakeClient([0]),'test',{})

    def test_only_confirmed_timeout_splits_finite_range(self):
        values=[0,49_999,50_000]
        client=FakeClient(values,timeout_width=25_000)
        actual,receipt=subject.exact_catalog_count(client,'test',buckets(values))
        self.assertEqual(actual,3);self.assertEqual(receipt['split_timeouts'],1)
        self.assertEqual(receipt['requests'],5)

    def test_unknown_and_malformed_counts_never_pass(self):
        for header in ['', '0-0/*', '0-0/-1', 'nonsense/1', '0-0/1 estimated']:
            with self.subTest(header=header),self.assertRaisesRegex(RuntimeError,'absent or malformed'):
                subject.exact_catalog_count(FakeClient([],header=header),'test',{})

    def test_request_budget_and_persistent_timeout_fail_closed(self):
        with self.assertRaisesRegex(ValueError,'request budget'):
            subject.exact_catalog_count(FakeClient([0,50_000]),'test',buckets([0,50_000]),max_requests=2)
        client=FakeClient([0,50_000],timeout_width=0)
        with self.assertRaisesRegex(RuntimeError,'request budget'):
            subject.exact_catalog_count(client,'test',buckets([0,50_000]),max_requests=5)
        self.assertEqual(len(client.calls),5)
        with self.assertRaises(StatementTimeout):
            subject.exact_catalog_count(FakeClient([0,50_000],timeout_width=0),'test',buckets([0,50_000]))

    def test_ordinals_require_valid_bigint_and_support_extremes(self):
        for value in [None,True,1.5,'1',subject.MIN_BIGINT-1,subject.MAX_BIGINT+1]:
            with self.subTest(value=value),self.assertRaises(ValueError):subject.ordinal_bucket(value)
        values=[subject.MIN_BIGINT,subject.MAX_BIGINT]
        self.assertEqual(subject.exact_catalog_count(FakeClient(values),'test',buckets(values))[0],2)


if __name__=='__main__':unittest.main()
