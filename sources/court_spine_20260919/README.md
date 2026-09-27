# Court spine (courts + courthouse-level locations + identity marks) — built 2026-09-19, offline

`build.py` is re-runnable and uses LOCAL data only (no network, no third-party packages). Inputs are opened read-only
and hashed into `validation.json` (`inputs`). Adapter: `delivery/archive-directory/court_spine.py`
(`listing`, `detail`, `original`; generic view contract, fail-closed hash gate). Spec with real example responses:
`integration_spec.json` (regenerate with `write_spec.py`).

## Files
| File | Rows | What it is |
|---|---|---|
| `courts.jsonl` | 5,413 | 3,361 CourtListener courts (id = CourtListener id) + 56 local-registry entries with no CourtListener court (`reg-<FAMILY>-<key>`) + 1,996 Kentucky/Texas county-registry courts (`cty-...`) |
| `logos.jsonl` | 176 | Distinct raster identity-mark files (png 164, gif 5, jpeg 4, webp 3); bytes copied content-addressed into `assets/<sha256>.<ext>` (14.8 MB) |
| `unresolved.jsonl` | 59 | 56 registry keys without a CourtListener court + 3 state conflicts (reason on every row) |
| `validation.json` | — | Uniform envelope: status, ready, data-file hashes/rows, counts, checks, inputs |

## What the counts mean
- **Court id**: the CourtListener id wherever one exists. Registry keys (`FD:njd`, `LC:pa_philadelphia`, ...) are kept in
  `registry[]` with `crosswalk_kind`: `exact_id` 206 (federal key suffix == CourtListener id), `reviewed_institution_match` 7
  (`FB:azb`->`arb`, `ST:as_state`->`amsamoa`, 5 local courts; the map is in `build.py` and the build re-checks the
  CourtListener name), `unresolved` 56 (55 statewide judiciary *systems*, which are not courts, and `LC:mo_st_louis`,
  for which no CourtListener court was found). Unresolved entries are their own rows and are never attached to a court.
- **System / type**: direct function of the CourtListener jurisdiction code as recorded (federal 408, state, territory 18,
  tribal 42, other 8, unknown 1 = one row with an empty code). Registry rows use `REG:ST`/`REG:LC`; county rows `CR:<recorded type>`.
- **State** (4,784 rows; 2,733 of the 3,361 CourtListener rows): only when a state name is printed in the court name, else in
  the parent court name, else the registry/county record's own state field. Names followed by County/Parish/City/Territory
  are ignored; two different state names, or a name/parent conflict (3 rows, e.g. "Port Washington" under New York), leave
  the state null. Federal appellate, special, military, tribal, committee and international courts never get a state.
- **Dates**: `start_date`/`end_date` are CourtListener's recorded values (437 / 403 rows have them); `temporal.source_as_of`
  is the bulk snapshot date 2026-06-30; registry rows carry the 2026-09-12 retrieval time as `captured_at`; county rows carry
  the county registry `source_as_of` 2026-08-22. Unknown stays null with `*_basis` null or "not recorded".
- **Website**: CourtListener `url` as recorded; registry homepage and up to 8 registry forms pages are kept separately. None re-checked.
- **has_logo** (212 courts): the registry's primary mark when it is a verified raster `court_seal`/`court_logo`/`judiciary_logo`,
  otherwise the best-scored raster mark of that same registry entry. SVG, ICO/favicons, BMP and judge photos are excluded
  (57 registry entries have no servable raster mark). `shared_mark` is true for 39 files (same bytes on more than one registry
  entry, or scope recorded as shared/statewide/state-government). A statewide mark stays on its statewide registry row only.
  `permission_status` is `not_established` for every file.
- **pending_mdl_count**: join of `sources/jpml_mdl_20260919/mdls.jsonl` on `cl_court_id`, status `pending`, as printed in the
  JPML report dated 2026-09-01: 166 pending MDLs across 50 courts. `listed_mdl_count` also includes the 10 terminated rows.
- **locations[]** (2,356 county locations from 2,358 listings; 2 duplicate listings in Harris County are kept as `times_listed: 2`):
  keyed by 5-char county FIPS. Texas `Nth District Court` and Kentucky `Nth Judicial Circuit/District` are one record per
  state + recorded type + name with one location per county listing (190 courts span 2-6 counties); every other listing
  (justice courts, county courts, courts at law, non-numbered names) is one record per county, because the same generic
  name in another county is a different court. The six local-registry courts carry `county_fips` from a reviewed 6-row map.

## Limits (not verified)
- The county registry records no court street addresses (`address` is null on every location) and phone numbers are not
  carried at all. Clerks and judge seats are not part of this layer.
- The statewide grouping of numbered Texas district courts / Kentucky circuits and districts relies on those courts being
  numbered statewide; it was not checked against an official statewide list.
- The state-from-name rule was spot-checked (random sample, id-prefix outliers); it is not a reviewed state assignment for
  every one of the 2,733 rows. 628 CourtListener rows have no state.
- CourtListener `in_use`, dates and urls are publisher values; nothing was re-fetched. Not a census of current courts.
- Judge counts, documents per court and relationship edges are out of scope here; join on `cl_court:<id>` / `fips:<5char>`.

## Run
```
& 'C:/Users/firas/AppData/Local/Programs/Python/Python311/python.exe' sources/court_spine_20260919/build.py
& '...python.exe' -m unittest discover -s sources/court_spine_20260919 -p 'test_build.py'          # 7 tests
& '...python.exe' -m unittest discover -s delivery/archive-directory -p 'test_court_spine.py'      # 13 tests
```
