# Source-directory links to existing saved archive records

This supplement attaches records that are **already saved in this archive** to the 9,348
references of the public-law source directory. It is a link layer, not a download batch:
no network request is made, no file is copied, and no primary database is changed.

`build.py` joins three validated inputs by **exact URL equality only** (no normalization
of scheme, host case, trailing slash, query or fragment):

- the source directory catalog (`sources/public_law_directory_20260919`, hash-gated);
- the local directory database (`delivery/archive-directory/directory.sqlite3`), for
  published, imported, federal-supplement and awaiting-publication records that have a
  saved original, saved text or indexed content;
- the canonical capture index (`catalog/documents.sqlite3`), for successful captures that
  never reached the directory ("retained captures").

## Target kinds

- `directory_record`: a saved record in the local directory. The original file is hashed
  afresh against the hash recorded at capture/import time; the text artifact is checked
  for non-empty content; titles matching soft-404/challenge/login patterns are rejected.
  `publication` says which release the record belongs to. A record may also match on
  the address a capture was redirected to (`match_basis: exact_final_url`); the requested
  address is kept as `recorded_source_url`.
- `judge_entity`: a consolidated judge profile whose source observation was recorded from
  the referenced page (for example a court's judge directory). Observation rows roll up to
  the profile through the directory's display membership; the count is observations, not a
  claim about current service.
- `retained_capture`: a successful canonical capture confined to the registered roots in
  `validation.json` (`asset_roots`). Raw and text bytes are re-hashed here and again at
  serve time. Captures with empty indexed text or an empty original are excluded.

Every exclusion is written to `excluded.jsonl` with its reason. `validation.json` binds the
`links.jsonl` hash, the source-catalog hash, the directory build receipt and the canonical
index validation that were current when the links were built.

## Rebuild

```powershell
& 'C:/Users/firas/AppData/Local/Programs/Python/Python311/python.exe' sources/source_archive_links_20260919/build.py
& 'C:/Users/firas/AppData/Local/Programs/Python/Python311/python.exe' sources/source_archive_links_20260919/test_build.py
```

Rebuild after a directory rebuild (`server.py --build`) or a canonical index rebuild so the
receipt binds the current state. The live adapter (`delivery/archive-directory/
source_archive_links.py`) re-checks every directory-backed target against the live database
on each detail request, so a stale manifest drops links rather than showing wrong ones.

## What the counts mean

Linked references are source entries with at least one validated link. They are not new
downloads, do not establish that the publisher page is currently available, and unlinked
references are not claimed absent from the corpus. Saved dates record collection time;
legal currency is never inferred. The pilot captures in
`sources/public_law_acquisition_20260919` remain a separate availability ("Saved content
attached") and are not duplicated here.
