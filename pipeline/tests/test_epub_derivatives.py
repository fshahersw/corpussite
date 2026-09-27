"""Offline safety/order fixtures for the single saved EPUB derivative pass."""
import hashlib
import importlib.util
from pathlib import Path
import tempfile
import unittest
import zipfile

MODULE = Path(__file__).resolve().parents[1] / "extract_iowa_epub.py"
spec = importlib.util.spec_from_file_location("epub_derivatives", MODULE)
epub = importlib.util.module_from_spec(spec)
spec.loader.exec_module(epub)
TMP = Path(__file__).resolve().parent / "_tmp"
TMP.mkdir(exist_ok=True)


def xhtml(text):
    return ('<?xml version="1.0" encoding="UTF-8"?>'
            '<!DOCTYPE html PUBLIC "-//W3C//DTD XHTML 1.1//EN" "http://www.w3.org/TR/xhtml11/DTD/xhtml11.dtd">'
            '<html xmlns="http://www.w3.org/1999/xhtml"><head><title>Not body</title></head>'
            '<body><p>' + text + '</p><script>NEVER EXECUTE</script><p>Last &amp; kept</p></body></html>').encode("utf-8")


class EpubFixtures(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(dir=TMP)
        self.root = Path(self.temp.name).resolve()
        self.assertTrue(self.root.is_relative_to(TMP.resolve()))

    def tearDown(self):
        self.assertTrue(self.root.is_relative_to(TMP.resolve()))
        self.temp.cleanup()

    def make_epub(self, *, href="a.xhtml", extra=None, body=None):
        path = self.root / "input.epub"
        with zipfile.ZipFile(path, "w") as z:
            z.writestr("mimetype", "application/epub+zip")
            z.writestr("META-INF/container.xml", '<container xmlns="urn:oasis:names:tc:opendocument:xmlns:container"><rootfiles><rootfile full-path="OEBPS/test.opf" media-type="application/oebps-package+xml"/></rootfiles></container>')
            z.writestr("OEBPS/test.opf", '<package xmlns="http://www.idpf.org/2007/opf" version="2.0"><metadata/><manifest><item id="a" href="' + href + '" media-type="application/xhtml+xml"/><item id="b" href="b.xhtml" media-type="application/xhtml+xml"/><item id="css" href="style.css" media-type="text/css"/></manifest><spine><itemref idref="b"/><itemref idref="a"/></spine></package>')
            z.writestr("OEBPS/a.xhtml", body if body is not None else xhtml("Alpha café § “smart”"))
            z.writestr("OEBPS/b.xhtml", xhtml("Beta first"))
            z.writestr("OEBPS/style.css", "p { color: red; }")
            if extra:
                z.writestr(extra, "must not be written")
        return path

    def test_spine_order_unicode_source_and_part_hashes(self):
        raw = self.make_epub()
        sha = epub.digest(raw)
        before = raw.read_bytes()
        result = epub.extract_epub(raw, self.root / "output", sha)
        text = Path(result["text_path"]).read_text(encoding="utf-8")
        self.assertLess(text.index("Beta first"), text.index("Alpha café"))
        self.assertIn("§ “smart”", text)
        self.assertNotIn("NEVER EXECUTE", text)
        self.assertNotIn("Not body", text)
        self.assertEqual(result["text_sha256"], epub.digest(result["text_path"]))
        self.assertEqual(raw.read_bytes(), before)
        self.assertEqual(result["extracted_spine_items"], 2)
        parts = {p["package_part"]: p for p in result["package_parts"]}
        self.assertEqual(parts["OEBPS/b.xhtml"]["spine_index"], 1)
        self.assertEqual(parts["OEBPS/a.xhtml"]["spine_index"], 2)
        self.assertEqual(parts["OEBPS/style.css"]["status"], "skipped_non_spine")
        self.assertEqual(parts["OEBPS/a.xhtml"]["unsupported_subtrees_not_rendered"], {"script": 1})
        self.assertTrue(parts["OEBPS/a.xhtml"]["external_doctype_suppressed_not_loaded"])
        with zipfile.ZipFile(raw) as z:
            for name, part in parts.items():
                self.assertEqual(part["sha256"], hashlib.sha256(z.read(name)).hexdigest())
        self.assertFalse((self.root / "output" / "OEBPS").exists())

    def test_archive_traversal_is_rejected_without_writes(self):
        raw = self.make_epub(extra="../../escape.txt")
        with self.assertRaisesRegex(ValueError, "Unsafe archive member"):
            epub.extract_epub(raw, self.root / "out", epub.digest(raw))
        self.assertFalse((self.root / "escape.txt").exists())
        self.assertFalse(list((self.root / "out").glob("text/*.txt")))

    def test_encoded_manifest_traversal_is_rejected(self):
        raw = self.make_epub(href="%2e%2e/escape.xhtml")
        with self.assertRaisesRegex(ValueError, "Unsafe OPF"):
            epub.extract_epub(raw, self.root / "out", epub.digest(raw))

    def test_mimetype_order_deviation_is_explicit(self):
        raw = self.make_epub()
        with zipfile.ZipFile(raw) as z:
            content = [(i.filename, z.read(i)) for i in z.infolist()]
        with zipfile.ZipFile(raw, "w") as z:
            for name, value in content[1:] + content[:1]:
                z.writestr(name, value)
        result = epub.extract_epub(raw, self.root / "out", epub.digest(raw))
        self.assertEqual(result["status"], "extracted")
        self.assertEqual(result["mimetype_zip_index"], len(content))
        self.assertIn("not first", result["container_validation_warnings"][0])

    def test_external_entity_and_internal_subset_are_rejected(self):
        payload = b'<!DOCTYPE html [<!ENTITY steal SYSTEM "file:///secret">]><html xmlns="http://www.w3.org/1999/xhtml"><body>&steal;</body></html>'
        raw = self.make_epub(body=payload)
        with self.assertRaisesRegex(ValueError, "entity declarations"):
            epub.extract_epub(raw, self.root / "out", epub.digest(raw))
        self.assertFalse(list((self.root / "out").glob("text/*.txt")))

    def test_unknown_dtd_entity_and_utf16_entity_are_rejected(self):
        for data in (
            b'<!DOCTYPE html SYSTEM "https://untrusted.invalid/dtd"><html/>',
            b'<html xmlns="http://www.w3.org/1999/xhtml"><body>&unknown;</body></html>',
            '<!DOCTYPE html [<!ENTITY x "bomb">]><html/>'.encode("utf-16"),
        ):
            with self.subTest(payload=data[:30]):
                with self.assertRaises((ValueError, UnicodeError, epub.etree.XMLSyntaxError)):
                    epub.safe_xml(data, xhtml=True)


if __name__ == "__main__":
    unittest.main()
