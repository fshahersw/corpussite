# Judge portrait refocus — September 19, 2026

Next scheduled work is specified in [NEXT_PORTRAIT_BATCH.md](NEXT_PORTRAIT_BATCH.md)
and the machine-readable `next_portrait_batch.json`; no new requests were made
while preparing that plan.

This bounded pass saved **15 new original court portrait images** and prepared
**6 evidence-supported links to existing judge entities**. The other **9 images
are held without an entity assignment**. The package changes no presentation
database, live portrait manifest, application source, or upstream source data.

Root subsequently installed the six reviewed images and rebuilt the judge-only
projection at 09:22:23 UTC. Its separate integration receipt is
[`reports/trellis_refocus_20260919/portrait_integration/receipt.json`](../../reports/trellis_refocus_20260919/portrait_integration/receipt.json).
Fresh live HTTP checks verified all six served image hashes and decodes, and
the application now returns 49 profiles with verified local portraits. The
frozen acquisition summaries below remain an account of the staging handoff.

The six approved links are Susan Illston, Haywood S. Gilliam Jr., Phyllis J.
Hamilton, Araceli Martinez-Olguin, Edward J. Davila, and Noël Wise. Each has a
named official portrait field, exact official profile URL and court, plus two or
three matching education, commission or career facts in the existing entity.
All considered candidates and literal evidence are preserved. Names/initials
alone and facial appearance were not used to establish identity.

Before this pass, all **43 previously verified images were already installed**:
14 existing entities and 29 separate Pennsylvania official profiles. After root
installs the six accepted links, the expected displayed count is **49**, with
the judge-profile count unchanged. There are **58 saved verified portrait
assets in these combined packages**, nine of which remain unlinked. These are
known-asset counts, not a national portrait-completeness percentage.

## Integration handoff

`cand/validation.json` is the final hash gate. Verify its listed files before
integration. The useful files are:

- `cand/accepted_links.jsonl`: six exact entity joins, original local image
  paths, proposed presentation paths and identity-evidence paths/hashes.
- `cand/portraits.append.jsonl`: six rows in the existing presentation schema:
  `entity_id`, `id`, `path`, `sha256`, `mime`, `provenance`.
- `cand/images.jsonl`: all 15 source-bound originals and decode/receipt evidence.
- `cand/held_images.jsonl`: nine verified images lacking reviewed entity joins.
- `cand/evidence/`: complete accepted identity proof, retained entity row and
  source-field provenance, source HTML/image receipts and literal biography.
- `cand/profile_gaps.json`: four requests ended with `RemoteDisconnected`; one
  profile's image was a decorative gavel and was rejected.

Root should copy each accepted record's `path` to `proposed_presentation_path`,
verify the copied SHA-256, reject existing entity/image IDs, append only the six
rows, and run the existing judge presentation build. The generated append paths
are **proposals until root performs that copy**. No new image root needs to be
registered if root uses the existing presentation image folder. All 15 staged
images already passed the current `judges.validated_image` validator when given
this package's exact image folder.

The nine held images have **zero existing entity candidates** under a review
of all 10,669 entity display names and retained aliases using normalized first
and last names. That bounded candidate test is not proof that no variant could
ever be linked. They are not ambiguous/conflicting accepted matches and should
not be assigned to a similar name. Future separate official profiles may be
appropriate after reviewing the distinct CourtListener source layer. No such
profiles were created here.

## Acquisition and remaining scope

The exact observed CAND roster URL supplied 36 individual profile links. Fourteen
already had installed portraits. Of the remaining 22, this pass requested 20:
16 saved complete HTML responses, four connection failures. Fifteen successful
pages had source-bound portraits; the sixteenth had a decorative gavel.

All 15 images downloaded and decoded. There were 16 image attempts because one
connection ended without a response and was retried once at the same exact URL;
its original failed receipt is retained. There was no authentication/access
denial, host unblock, alternate-host rerouting or paid call. Existing crawler
robots rules, two-second shared host pacing, OS host locks, source scopes and
hash receipts remained enforced. The isolated image configuration allows only
the frozen exact image URLs. No linked court filings were downloaded as part of
this portrait batch. Two newly listed profiles were outside the 20-profile cap.

The initial FJC discovery was stopped at its five-minute checkpoint. It saved
nine official biographies and nine unique native-NID links, but no portrait
images. Its published 30-second crawl delay was respected; 41 of 50 frozen seeds
remain unattempted. `resources.jsonl`, `identity_links.jsonl`, `reading/`,
`summary.json` and `validation.json` preserve that useful biographical evidence.
The empty top-level `portraits.jsonl` belongs to that FJC sub-batch; the six CAND
integration rows are specifically `cand/portraits.append.jsonl`.

`trellis_sample_audit.json` separately reviews ten original provider captures,
one from each of ten recorded states. Their actual HTML, lazy-image attributes,
Markdown image references and inline CSS contained only four common interface
graphics (logo, spinner, PDF icon and generic dashboard preview). No individual
portrait was found in that sample. This does not establish that all 1,300 saved
Trellis profiles lack photographs. CourtListener `has_photo` flags were also
kept distinct from actual available image URLs or installed files.

## Verification

Eighteen tests passed, covering native-ID ambiguity, source/canonical mismatch,
logos and decorative-gavel rejection, frozen image scope, name/court-only and
ambiguous identity rejection, image corruption/decoding, and final artifact/
identity-evidence hash bindings. All 15 images also passed the existing
presentation validator. Validation receipts are written last; all network
workers have stopped. Collection timestamps describe these snapshots, not
current judicial service, photo licensing, matter assignments, or endorsement.

Reproduce the offline tests:

```powershell
C:/Users/firas/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/python.exe -m unittest discover -s sources/judge_portrait_refocus_20260919 -p test_*.py -v
```
