# Open-source repositories used in the 2026-09-20 enrichment pass

Pinned copies of the files actually used, with provenance. `fetch.py survey` recorded each repository's licence, head commit and
file list (`repos/<owner>__<name>/meta.json`, `tree.json`); `fetch.py get` downloaded chosen files at that commit and recorded each
file's SHA-256 (`manifest.json`). `fetch_pypi.py` extracted one data file from a published wheel after checking the wheel against
the digest PyPI publishes. Nothing from these repositories is executed.

| Repository | Licence | Commit | What was used | Layer built from it |
|---|---|---|---|---|
| freelawproject/courts-db | BSD-2-Clause | be4fe57b51 | `courts.json` (2,809 courts), `variables.json` | `sources/court_reference_flp_20260920` |
| freelawproject/seal-rookery | software public domain; federal seals are US Government works; state seal status not recorded | 784c3238ba | `seals.json`, 232 PNG originals (22 SVG-only seals taken as the project's PNG renditions) | `sources/court_reference_flp_20260920` |
| freelawproject/reporters-db | BSD-2-Clause | e095e6bf91 | reporters, laws, journals, case-name and state abbreviations | `sources/citation_reference_flp_20260920` |
| freelawproject/judge-pics | software public domain; per-image licence in its index | a5cf4252a2 (+ PyPI wheel 2.0.5 for `people.json`) | index keyed by CourtListener person id; 1,179 federal-government portraits | `sources/judge_portraits_flp_20260920` |
| freelawproject/eyecite | BSD-2-Clause | installed package 2.7.8 | citation extraction over saved documents | `sources/citation_index_20260920` |
| Vaquill-AI/open-us-law | Apache-2.0 code, CC BY 4.0 data | 2f7aeb85a4 | `coverage.yml` (publisher's completeness audit). The data snapshot itself (v2026.08) was already saved and is current | `sources/law_snapshot_audit_20260920` |
| TechnoOptics/legal-data | MIT code, CC BY 4.0 data | f87c2f24bf | `statute-of-limitations.json` (51 jurisdictions x 9 claim types) | `sources/limitation_periods_20260920` |
| LegalQuants/lq-skills | Apache-2.0 | 884cf40294 | five skills, installed unmodified in `.claude/skills/` (see `PROVENANCE.md` there) | project skills |
| mayhewsw/legal-linking | **no licence stated** | fd86a27b09 | README only | none (see below) |

## Reviewed and not used

- **mayhewsw/legal-linking** (NAACL NLLP 2019, linking Supreme Court text to constitutional provisions): 3.3 GB, almost all of it
  scraped opinion text used as training data for a 2019 AllenNLP model. The repository states no licence, so nothing may be
  redistributed, and the useful idea (resolve a citation to the text it cites) is what `citation_index_20260920` now does
  with eyecite against the law text already saved here. The U.S. Constitution it includes is already in the saved snapshot.
- **TechnoOptics/legal-data `templates.json`**: five consumer letter templates; not research data.
- **judge-pics portraits not marked "Work of Federal Government"** (68: state works or no licence recorded): listed as held in the
  portrait index with the reason, not shown.
- **open-us-law scraper scripts**: not needed; the published snapshot is already saved and is the current release.

## Rebuild

    python fetch.py survey
    python fetch.py get freelawproject/reporters-db "reporters_db/data/*.json"      # and the other patterns recorded in each manifest
    python fetch_pypi.py judge-pics "*.json"
    python install_skills.py                                                       # only after re-reading any changed skill file

`show_tree.py` and `peek.py` are small read-only viewers for the surveyed file lists and JSON files.
