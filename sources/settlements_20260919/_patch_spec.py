"""One-off: bring integration_spec.json in line with the settlement-specific phrase split (repair 2026-09-19)."""
import json
from pathlib import Path

path = Path(__file__).resolve().parent / "integration_spec.json"
spec = json.loads(path.read_text(encoding="utf-8"))
block = spec["court_docket_evidence_2026_09_19"]
block["what"] = ("8 rows with record_layer 'mdl_court_docket_activity', id 'mdl-docket-<mdl number>', title 'Settlement-phrase docket search "
                 "for MDL <n>' (suffix ': no settlement-specific entry found' when no captured entry matched a settlement-specific phrase: "
                 "MDL 2738 and 3060), family 'mdl_court_docket' (label 'MDL / mass tort (court docket evidence)'). Docket-entry evidence only; "
                 "never a settlement record or amount, and a row with 0 settlement-specific entries is not evidence of settlement activity.")
block["data_files"] = ["court_documents.jsonl (92 docket-entry references; 55 with a settlement-specific phrase, 37 non-specific only; "
                       "fields settlement_specific, matched_settlement_specific_terms, matched_non_specific_terms)",
                       "edges.jsonl (6 edges settlement:mdl-docket-<n> -> mdl:<n>, relation docket_phrase_search_for, evidence carries "
                       "entries_total and settlement_specific_entries; NO edge for MDL 2738 and 3060, listed in validation.json counts "
                       "edges_withheld_no_settlement_specific_entry)"]
block["detail"] = ("detail('mdl-docket-2873') returns facts (record layer, 'Entries matching a settlement-specific phrase' = 'N of M captured "
                   "entries', 'Finding' only when N = 0, MDL, master docket, settlement-specific and non-specific search phrases, search coverage, "
                   "CourtListener searched at) and the sections 'Court docket entries matching a settlement-specific phrase (N; ...)' and, when any, "
                   "'Court docket entries matching only a non-specific phrase (K; ...; not settlement evidence)'; items are titled 'Date filed "
                   "<YYYY-MM-DD> - docket entry <n> - <type label>' with one CourtListener link each. No originals are served for these rows.")
block["listing_labels"] = ("Row badge 'Settlement-specific phrase: N of M entries' or 'No settlement-specific entry found (matched only: ...)'; "
                           "subtitle carries 'entries matching a settlement-specific phrase: N of M'; documents cell repeats the count.")
block["caveats"][0] = ("Eight search phrases are settlement-specific; 'common benefit' and 'order approving' are not and also match unrelated entries "
                       "(e.g. 'ORDER Approving proposed schedule', common-benefit fee protocols). Types come from the description text only and 60 of "
                       "92 entries stay unclassified; a case-management order without a settlement-specific phrase is typed 'case_management_order'.")
spec["edges"] = ("edges.jsonl: 6 edges for the court-docket rows that have at least one settlement-specific entry (relation docket_phrase_search_for, "
                 "basis verified_master_docket_id). None for MDL 2738 and 3060, and none for the publisher/page rows (no native court, MDL or judge "
                 "identifiers exist in that source; the 9 reviewed rows carry court/case text only).")
path.write_text(json.dumps(spec, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
print("ok")
