"""Extract page text from the saved JPML PDFs into text/<name>.txt (offline, pypdf). Pages separated by form feed."""
import hashlib
import json
from pathlib import Path

from pypdf import PdfReader

HERE = Path(__file__).resolve().parent
RAW = HERE / 'raw'
TEXT = HERE / 'text'


def extract(pdf_path):
    reader = PdfReader(str(pdf_path))
    pages = []
    for page in reader.pages:
        try:
            pages.append(page.extract_text() or '')
        except Exception as exc:  # keep going; record the failure in the manifest
            pages.append('[[extract_error: %s]]' % type(exc).__name__)
    return pages


def main():
    TEXT.mkdir(exist_ok=True)
    manifest = []
    for pdf in sorted(RAW.glob('*.pdf')):
        out = TEXT / (pdf.stem + '.txt')
        pages = extract(pdf)
        body = '\f'.join(pages)
        out.write_text(body, encoding='utf-8')
        manifest.append({'raw': 'raw/' + pdf.name, 'raw_sha256': hashlib.sha256(pdf.read_bytes()).hexdigest(),
                         'text': 'text/' + out.name, 'text_sha256': hashlib.sha256(body.encode('utf-8')).hexdigest(),
                         'pages': len(pages), 'chars': len(body), 'empty_pages': sum(1 for p in pages if not p.strip())})
        print(pdf.name, len(pages), 'pages', len(body), 'chars')
    (TEXT / 'manifest.json').write_text(json.dumps(manifest, indent=1), encoding='utf-8')


if __name__ == '__main__':
    main()
