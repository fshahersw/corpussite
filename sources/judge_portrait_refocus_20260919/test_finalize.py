import unittest
from finalize import member_index, parse_profile

URL='https://www.fjc.gov/history/judges/example-jane'
MEMBER={'source_class':'federal_biographies','source_observation_id':'fjc:nid:123:csv:abcdef',
        'entity_id':'judge-entity-123','member_key':'dataset|native-123'}


def sample(canonical=URL, shortlink='https://www.fjc.gov/node/123', body_url=URL, extra=''):
    return f'''<html><head><link rel="canonical" href="{canonical}"><link rel="shortlink" href="{shortlink}"></head>
    <body><img src="/logo.png" alt="Home"><h1>Example, Jane</h1>
    <div class="node node--judge" about="{body_url}"><div class="field field--name-judge-record-display">
    Born 1960<br><b>Education:</b><br>Example University, J.D., 1985<br><b>Professional Career:</b>
    Private practice, Example City, 1985-1990. Appointed to Example Court in 1990.</div>{extra}</div></body></html>'''.encode()


class IdentityAndReadingTests(unittest.TestCase):
    def test_native_identity_not_name(self):
        result=parse_profile(sample(),URL,member_index([MEMBER]))
        self.assertEqual(result['entity_id'],'judge-entity-123')
        self.assertEqual(result['source_observation_ids'],[MEMBER['source_observation_id']])

    def test_site_name_heading_not_judge_heading(self):
        raw=sample().replace(b'<body>',b'<body><h1 class="site-name">Federal Judicial Center</h1>')
        self.assertEqual(parse_profile(raw,URL,member_index([MEMBER]))['heading_as_published'],'Example, Jane')

    def test_foreign_native_link_rejected(self):
        with self.assertRaises(ValueError): parse_profile(sample(shortlink='https://example.com/node/123'),URL,member_index([MEMBER]))

    def test_canonical_and_body_must_match(self):
        for kw in ({'canonical':URL+'2'},{'body_url':URL+'2'}):
            with self.assertRaises(ValueError): parse_profile(sample(**kw),URL,member_index([MEMBER]))

    def test_ambiguous_native_id_cannot_merge(self):
        other={**MEMBER,'entity_id':'judge-entity-namesake'}
        with self.assertRaises(ValueError): parse_profile(sample(),URL,member_index([MEMBER,other]))

    def test_name_without_native_id_cannot_merge(self):
        with self.assertRaises(ValueError): parse_profile(sample(shortlink='https://www.fjc.gov/node/999'),URL,member_index([MEMBER]))

    def test_logo_is_not_portrait(self):
        result=parse_profile(sample(),URL,member_index([MEMBER]))
        self.assertEqual(len(result['all_image_elements']),1)
        self.assertEqual(result['profile_media_candidates'],[])
        self.assertFalse(result['portrait_assigned'])

    def test_body_image_is_only_candidate(self):
        result=parse_profile(sample(extra='<img src="/some-photo.jpg" alt="Jane Example">'),URL,member_index([MEMBER]))
        self.assertTrue(result['profile_media_candidates'][0]['candidate_only'])
        self.assertFalse(result['portrait_assigned'])

    def test_reading_excludes_chrome_preserves_year_and_paragraphs(self):
        text=parse_profile(sample(),URL,member_index([MEMBER]))['reading']
        self.assertIn('Education:\nExample University, J.D., 1985',text)
        self.assertNotIn('Home',text)


if __name__=='__main__': unittest.main()
