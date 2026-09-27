# Seeger text recovery derivatives

This offline pass recovers text for the frozen 385 original Seeger records that
had no native text in the released import: 379 Word binary documents, four RTF
documents, and two one-page scanned PDFs. It creates no new legal-document
identities. Each `resources.jsonl` ID is the original Seeger resource ID, so a
consumer can overlay a verified derivative without adding a duplicate record.

Do not publish a running or pilot pass. Integration requires `validation.json`
with `status: "passed"`, `overlay_ready: true`, and a matching
`resources_sha256`. Each derivative is bound to the original raw SHA-256, its
own text SHA-256, and a hashed metadata sidecar. Unresolved records are explicit
in `unresolved.jsonl`; errors are retained in `failures.jsonl` and the append-only
`events.jsonl`. `summary.json` distinguishes recovered records from gaps.

## Word/RTF method

The installed Microsoft Word COM parser reads verified scratch copies in a
dedicated hidden `DispatchEx` instance. Macros are forced disabled; link updates
at open, field updates at print, and link updates at print are disabled. Files
are opened read-only, not added to recent files, and closed without saving.
The source original is never passed to Word. Original SHA-256 values are checked
before and after extraction, then all 385 originals are checked at validation.

The application process is bound to a new blank document's window PID before
source copies are opened. A PID present before the worker started is never
terminated. Each document has a 45-second deadline; batches use at most 30
documents per dedicated instance. Only the recorded new instance may be stopped
if unresponsive. There is no blanket `WINWORD.EXE` process termination.

Main body, footnotes, endnotes, comments, text-frame stories, and headers/footers
are labeled separately. Table boundaries and Word cell-end markers are labeled;
merged-cell geometry is not guessed. Word control markers are rendered as
explicit labels. Existing field results are read; fields are not recalculated.
Images and other embedded objects are not OCRed by the Word pass. The original
file remains the authority for visual layout and any missing embedded content.

## PDF method

The two PDF originals use the existing local PyMuPDF screening/rendering code,
Tesseract.js worker, and hash-verified English model. `ocr/` preserves page
images, page text, confidence, TSV output, screening evidence, and the complete
OCR manifest. No OCR model or source content is downloaded for this pass.
Confidence is an engine estimate rather than a correctness guarantee.

The civil cover sheet initially produced interleaved column text. Its reviewed
replacement uses nine observed page regions with explicit labels. The region
coordinates, per-region text/TSV/confidence, original whole-page derivative, and
image hashes remain available. Column order is improved; residual character
errors are still flagged. The regional and whole-page confidence averages use
different scopes and are not directly comparable accuracy measurements.

## Scope and schema

`resources.jsonl` fields: `id`, `raw_sha256`, `text_path`, `text_sha256`,
`characters`, `method`, `metadata_path`, `metadata_sha256`, `status`, and
`quality_notes`. Paths are relative to the SCRAPE workspace. Status `recovered`
means a nonempty derivative passed its artifact checks; it does not establish
legal accuracy, applicability, or currency.

The earlier 111 PDFs with some blank native-text pages and the truncated XML
case are not repaired by this bounded empty-text pass. The two fully empty PDFs
overlap the earlier blank-page inventory; totals must not be added blindly.
Existing import records, their review flags, original captures, and published
databases remain unchanged by this script. Original source date and jurisdiction
metadata must be preserved when a consumer overlays these new text paths.

Run: `python scripts/recover_seeger_text.py --all`.
Validation only: `python scripts/recover_seeger_text.py --validate`.
The tool uses installed local parsers; no installation or network acquisition is
part of the workflow.
