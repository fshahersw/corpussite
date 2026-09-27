# Moving the legal archive to another machine

The project is about 130 GB and 360,000 files. GitHub refuses any git file above 100 MB (35 files here, one of 28 GB), so the
repository is split in two, and both halves live in `fshahersw/externalcorpus`:

| Half | Where | What |
|---|---|---|
| Code | git history (about 5 MB, 1,070 files) | the server and its adapters, the front end, every build script, the verifier, the project skills, the write-ups |
| Data | assets of the release `data-20260920` | every other file: databases, saved pages, PDFs, snapshots, per-collection `validation.json` files |

Every data file is in exactly one archive unit. A unit is one collection folder (`sources/<name>`, `corpus/<name>`,
`delivery/<name>`, `reports/<name>`), another top-level folder, the loose top-level files, or one of 16 buckets of court-document
originals that the collecting machine keeps outside the project. `data_manifest.json` (a release asset, refreshed after every
unit) lists the units with their size, licence line, parts and SHA-256 values, so single units can be deleted later.

## On the new machine

    git clone https://github.com/fshahersw/externalcorpus.git
    cd externalcorpus
    py -3.11 -m pip install -r requirements.txt        # the server itself needs only lxml, courts-db and pillow
    gh auth login                                      # or set GH_TOKEN; not needed while the repository is public
    python bootstrap.py list                           # what exists, how large, what is installed
    python bootstrap.py pull --skip-optional           # everything except the 43 GB of court-document originals
    python bootstrap.py pull                           # later: the rest
    python delivery/archive-directory/server.py --serve        # http://127.0.0.1:8769
    python reports/corpus_upgrade_20260919/verify_round2.py    # expect {"status": "passed", "checks": 83}

`pull` is resumable. It checks each part against its SHA-256 before unpacking, hashes every file while writing it, compares it
with the listing carried inside the archive, and restores modification times to the nanosecond (the state-code outline checks the
28 GB law catalogue by size and time instead of re-hashing it on every start). `python bootstrap.py verify` re-hashes what is installed.
Disk needed: about 135 GB for everything, 90 GB with `--skip-optional`. Scratch space stays below one part (128 MB).

Windows: enable long paths once (`git config --system core.longpaths true`, and the LongPathsEnabled policy) because some saved
files sit deeper than 260 characters. `.gitattributes` turns off end-of-line conversion; keep it that way, since several small files
are hash-checked at start-up.

## Using it from another application

Treat the archive as a read-only HTTP service and keep its address in configuration (for example `LEGAL_ARCHIVE_URL`).
The endpoints and their ground rules are written down in `.claude/skills/legal-archive-api/SKILL.md`. The server listens on
127.0.0.1 only and refuses requests whose Host header is not localhost; that is deliberate, and putting it behind anything other
than a same-machine caller needs an authenticating proxy in front of it.

## What is not transferred, and why

- `.auth/`: an encrypted credential that only the original Windows account can decrypt. The server needs no keys at run time.
- `devvvv/`: a separate project with its own repository. `.tools/`, `node_modules/`, `__pycache__/`: re-creatable.
- Logs, lock files, SQLite `-shm` files, `server.json`, `.digest_cache.json`: machine state. SQLite `-wal` files ARE transferred.
- Anything else under `SW-BULK` on the collecting machine. Only the court-document originals that the index
  `sources/court_document_library_20260919` names are packaged (restored under `external/`, where the adapter looks first).

## Licences

Units keep the licence line of their `validation.json`. Units marked `restricted` hold pages and reports of a commercial docket
publisher whose terms forbid redistribution; `tools/push_data.py` uploads them only while the repository is private, and
`bootstrap.py pull --skip-restricted` leaves them out. The folders labelled seeger / SW-BULK are, per the owner, their own
collections from public sources; the older "private firm work product" wording inside those `validation.json` files is unchanged
because start-up checks read it. The national corpus is not complete, and nothing here should be presented as complete.

## Refreshing the data later (collecting machine)

    python tools/scan_secrets.py git                   # before every push of code
    python tools/push_data.py plan                     # units and sizes
    python tools/push_data.py push                     # only units whose files changed are packaged again
    powershell -File tools/start_push.ps1              # the same, hidden, logging to _transfer_scratch/push.log
