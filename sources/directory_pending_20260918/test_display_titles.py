import unittest
from build_pending import source_link_display_title


def context(url, label, reproduced=True):
    return {'seed': {'provenance': [{'target_url':url,'anchor_text':label,'link_reproduced_from_raw_html':reproduced}]}}


class DisplayTitles(unittest.TestCase):
    def test_technical_titles_use_exact_publisher_anchor(self):
        url = 'https://county.example/2026-11-Resolution.pdf'
        for title in (url, r'c:\bis\scanning\F202600043.tif'):
            clean, basis = source_link_display_title(title, url, [context(url, 'Resolution 2026-11')])
            self.assertEqual(clean, 'Resolution 2026-11')
            self.assertEqual(basis, 'exact_observed_source_link_label')

    def test_draft_and_version_metadata_are_not_replaced_by_generic_anchor(self):
        url = 'https://county.example/Noise-Ordinance.pdf'
        for title in ('10/2/2003 DRAFT #8', 'Administrative Order No. 1.02-v4'):
            self.assertEqual(source_link_display_title(title, url, [context(url, 'Noise Ordinance')]), (title, 'capture_metadata'))

    def test_wrong_unverified_generic_or_ambiguous_labels_do_not_replace_title(self):
        url = 'https://county.example/doc.pdf'
        for contexts in ([context(url+'x','Another document')], [context(url,'Some document',False)],
                         [context(url,'Download')], [context(url,'One'),context(url,'Two')]):
            self.assertEqual(source_link_display_title(url,url,contexts), (url,'source_title_fallback'))

    def test_remove_only_producer_prefix(self):
        title = 'Microsoft Word - COUNTY HEALTH ORDINANCE PART VI'
        self.assertEqual(source_link_display_title(title,'https://county.example/doc.pdf',[]),
                         ('COUNTY HEALTH ORDINANCE PART VI','producer_prefix_removed'))

    def test_scanner_path_uses_filename_when_publisher_anchor_is_generic(self):
        url = 'https://county.example/2026-11-Resolution.pdf'
        self.assertEqual(source_link_display_title(r'c:\bis\scanning\F202600043.tif',url,[context(url,'View/Download')]),
                         ('2026-11 Resolution','normalized_source_filename'))

    def test_filename_revision_is_not_lost_when_anchor_omits_it(self):
        url = 'https://county.example/Title-I_No-02a-Revised-2006-05-09.pdf'
        self.assertEqual(source_link_display_title(url,url,[context(url,'General Assistance')]),
                         ('General Assistance [filename: Revised 2006-05-09]','source_link_label_with_filename_revision'))

    def test_month_only_anchor_does_not_invent_document_identity_or_year(self):
        url = 'https://court.example/Goerner/August.pdf'
        self.assertEqual(source_link_display_title(url,url,[context(url,'Aug')]),(url,'source_title_fallback'))


if __name__ == '__main__':
    unittest.main()
