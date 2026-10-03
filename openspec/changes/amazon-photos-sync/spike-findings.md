# Spike findings — Amazon Photos `drive/v1` endpoints and session lifetime

Task: 1.1 (`openspec/changes/amazon-photos-sync/tasks.md`).
Design gate: D10 (`design.md`).

## Status

**LIVE EXECUTION PERFORMED on 2026-10-03** against a real Amazon Photos account
on **amazon.es** (the `.com` domain returns `403 Account Not Found`; the TLD is
derived correctly from the regional cookie names `at-acbes` / `ubid-acbes`).

Session: captured from a local Firefox profile via `amz-download login --firefox`.
Endpoints below were confirmed with a full live listing (17,494 media items) and
a small real download. Session **lifetime is still being measured** (see below).

## Live results (2026-10-03, amazon.es)

Shared query parameters: `asset=ALL`, `tempLink=false`, `resourceVersion=V2`,
`ContentType=JSON`.

| Capability | Call | Live result |
| --- | --- | --- |
| Session probe | `GET /account/usage` | ✅ 200 |
| Root / owner id | `GET /nodes?filters=isRoot:true` | ✅ 200 |
| Media listing | `GET /search?filters=type:(PHOTOS OR VIDEOS)&limit=200&offset=N&searchContext=customer&lowResThumbnail=true&sort=['createdDate DESC']` → `{count,data[]}` | ✅ 200; 17,494 media nodes paginated to the end |
| Folder listing | `GET /nodes/{id}/children?filters=kind:FOLDER` traversed from the root | ✅ 200 (see correction below) |
| Album listing | `GET /nodes?filters=kind:VISUAL_COLLECTION` | ✅ 200; 8 albums |
| Album membership | `GET /nodes/{album_id}/children?limit=200&offset=N` | ✅ 200 |
| Content download | `GET /nodes/{node_id}/contentRedirection?download=true&ownerId={ownerId}` (streaming, md5 verified) | ✅ 200; 3 files, 755,153 bytes, hashes matched |

## Corrections vs. the reference implementation

Two documented assumptions from the community reference did **not** hold and were
fixed in `client.py`:

1. **`/search` requires `searchContext`.** Omitting it returns
   `400 {"message":"1 validation error detected: Value null at 'searchContext' ..."}`.
   The client now always sends `searchContext=customer` (plus
   `lowResThumbnail=true` and `sort`).
2. **`/search` rejects `kind:FOLDER`** (`400 Invalid fieldName: kind`). Folder
   nodes cannot be listed through `/search`; they must be discovered by walking
   `GET /nodes/{id}/children?filters=kind:FOLDER` from the root. `list_folders`
   now does a breadth-first traversal instead.

## Session lifetime

**Still unverified.** Cookies were confirmed valid on 2026-10-03; the TTL
requires re-checking the same session over time. Record the date the session
stops authenticating to obtain the lifetime, then replace this section.

## D10 gate outcome

Updating the D10 table with what is now known:

| Dimension | Outcome |
| --- | --- |
| Media / folder / album endpoints | **All OK** (live) |
| Content download | **OK** (live) |
| Session lifetime | **Pending** |

So the gate is at *"all endpoints OK, token lifetime not yet measured"*. The
implementation proceeds as specced and degrades safely on `401`
(`sync` aborts before writing and tells the user to re-authenticate). Unattended
scheduling should be treated as best-effort until the lifetime is measured; if
it turns out to be short, a refresh flow or a downgraded non-interactive
requirement is a spec change that must go back through the specs artifact.

## How to finish the lifetime measurement

1. Re-run `amz-download status` / a dry-run periodically:
   `amz-download sync --dry-run --dest /tmp/amz-check`.
2. When it starts failing with the re-authentication error, the elapsed time
   since 2026-10-03 is the observed lifetime.
3. Replace this section and pick the D10 outcome.
