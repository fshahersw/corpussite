# Judge portraits

Portraits from Free Law Project judge-pics, attached where its index names the same CourtListener person id as the saved biography; judge profiles reach the same portrait through the existing native-id bridge (FJC nid -> FJC jid -> CourtListener person id).

- Adapter: `delivery/archive-directory/judge_portraits.py`
- In the app: judge profiles, judge cards and historical biographies; files at `/supplement-files/judge_portraits/<portrait id>`
- Gate: `validation.json` (status `passed`); the adapter closes if the data file no longer matches its recorded SHA-256.

## Rules

- Never attached by name or by face.
- Only portraits the index marks "Work of Federal Government" are shown; state works and portraits with no licence recorded are listed as held.
- Originals come from the repository at its pinned commit and are reduced to 256 px; the index hash equals the original for only some files (it often refers to a processed copy), so each row records whether it matched.
- A portrait may be historical and is not evidence of current service.

## Counts (from validation.json, 2026-09-20)

- index rows: 1,249
- published: 1,179
- held licence not federal: 68
- held no person id: 1
- held person not in saved people: 0
- held image not downloaded: 0
- held unreadable image: 0
- duplicate index rows: 1
- people with a portrait: 1,174
- from originals at the pinned commit: 815
- of which equal to the index sha256: 88
- from project 256px renditions: 364
- judge entities with a portrait: 1,155

## Rebuild

    python build.py
