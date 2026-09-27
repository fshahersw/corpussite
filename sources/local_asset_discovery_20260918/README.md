# Local legal asset discovery

The ready MVP addition is **five judge portrait links** supported by exact full
displayed name and explicit court-service evidence. `portrait_links.jsonl` has
the requested entity ID, absolute local image path, SHA-256, MIME type, official
profile URL, identity basis and evidence metadata. The five matches are Charles
R. Breyer, Jacqueline Scott Corley, James Donato, Maxine M. Chesney, and Yvonne
Gonzalez Rogers. They are all Northern District of California profiles.

`judge_portraits.jsonl` inventories all 14 official portraits. Nine are in
`portrait_unmatched.jsonl`: the available names contain initials or omit given
names/suffixes that differ from our current entity names and aliases. No initials
were expanded to force a match. William H. Orrick is especially ambiguous because
our existing corpus contains both William Horsley Orrick III and Jr. associated
with the same district. Stronger profile/native-ID linkage is needed before
attaching the remaining photographs to entities.

Every original image's size and SHA-256 was checked against the curated source
manifest. All 14 portrait candidates decode as WEBP. Identity comes from the
official named profile, its heading/alt evidence, and the exact court match;
photographs were not used for facial identification. The matched entity row and
FJC service provenance are retained for review.

## Other immediate assets

- `court_links.jsonl`: 269 explicit court/judiciary entries, 238 with a primary
  identity image. Five of the six selected local court entries have images; the
  St. Louis entry retains its explicit text fallback.
- `image_files.jsonl`: 295 unique hash-verified files, 21,514,352 bytes.
- `asset_associations.jsonl`: 493 accepted source associations, preserving shared
  image relationships, original names and sources.
- `source_exclusions.json`: three prior excluded associations, retained as
  exclusions rather than reintroduced from the old collection folder.

Root can copy only approved files into workspace-controlled content-addressed
paths and serve by allowlisted ID. Do not expose an arbitrary-path asset endpoint.
The portrait links are raster images; SVG court marks require a separate safe
serving decision. A statewide judiciary mark must not become the identity of
every county court. Preserve `permission_status: not_established`, retrieval-era
roles and source URLs; neither current service nor case assignment is asserted.

## Useful buried structured data

`collection_candidates.jsonl` is a lightweight list of local source pointers,
sizes, metadata hashes, observed counts and integration suggestions. The main
additional opportunity is LAWONTOLOGY's MDL-3080 corpus: 1,612 documents and
21,357 native-text pages, with 922 observed docket entries and an evidence graph.
It could support a matter-specific document/citation view. Filename assertions,
native-text evidence, rule-based candidate outcomes and unresolved references
must remain separate. This is not a general judge-analysis dataset or proof of a
complete docket. Its database was not opened and its large files were not copied.

Other pointers include 56 statewide/DC/territory court-source records, 207 federal
jurisdiction/special entries, six local court registries and an 848-record
settlement-reference catalog. Their boundaries and prior review flags remain
explicit; none was silently imported as verified judicial analytics.

`folder_inventory.json` records the bounded scan of the eleven requested legal
project folders, plus top-level relevant-name checks of Documents and Desktop.
Application code, screenshots and authentication/transfer tooling were not
promoted as corpus evidence. No credentials or browser history were read, no
network request was made, and no external originals were changed.
