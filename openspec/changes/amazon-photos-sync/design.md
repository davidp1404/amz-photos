# Design

## Context

See `proposal.md` for motivation. Current state and constraints that shape the approach:

- `amz_download` is a fresh `uv init --app` project (Python 3.13) containing only a `main.py` placeholder, an empty `README.md`, `pyproject.toml` with no dependencies, and an initialized `openspec/` root with no specs.
- Amazon Photos exposes **no supported API**. The only viable surface is the authenticated web session against undocumented `drive/v1` endpoints, the same surface the community `amazon-photos` package and old browser-automation scripts use. Those endpoints are unversioned and may change without notice.
- The user chose a **uv project** (console script, testable modules) rather than a PEP 723 single file, and chose a **Linux-first** environment where symlinks work normally.
- We already decided the product contracts: one-way incremental sync, identity by `node_id`, archive-on-remote-delete, canonical chronological layout with symlink views, media (photos + videos) only.

## Goals / Non-Goals

**Goals:**

- A dependency-light, async Python client that lists media and streams content without pulling in `pandas`/`pyarrow`.
- A durable, transactional local state store that makes re-runs cheap and enables move detection.
- Non-interactive `sync` suitable for a timer, with interactive `login` kept separate.
- Clear, testable module boundaries so the undocumented-endpoint risk is isolated to one place.

**Non-Goals:**

- Any write/delete/upload operation against Amazon Photos (read-only client).
- Two-way sync or local-change detection beyond "file missing."
- Non-media files (documents, archives, audio) and the `Documents` branch.
- A Windows-first symlink experience; Windows is a degraded-mode target only.
- Being a general Amazon Drive client.

## Decisions

### D1. Transport: own thin async `httpx` client

Reimplement the call groups needed on `httpx.AsyncClient` — media and folder listing via `search`, album listing and album membership, and content download — rather than depend on `amazon-photos`.

- **Why:** avoids `pandas` + `pyarrow` in a tool whose core is "POST a query and stream bytes"; gives explicit control over concurrency, retries, backoff, md5 verification, and temp-file/atomic-rename semantics; keeps the module testable behind a small interface.
- **Alternatives:** depend on `amazon-photos` (proven, but heavy and its `download()` abstraction hides the tree/identity control we need); hybrid (still pulls pandas).
- **Mitigation:** the `amazon-photos` source is treated as the endpoint reference; endpoint shapes live only in `client.py` so breakage is localized; a spike validates the calls before the rest is built.

### D2. State: stdlib SQLite keyed by `node_id`

- **Why:** transactional, crash-safe, stdlib (no dependency), and efficient for libraries with 10^5+ rows. Keying on `node_id` is what makes move detection and hash comparison possible.
- **Alternatives:** JSON manifest (simple but no transactions, rewrites whole file); Parquet (extra dependency, poor for row updates).
- **Sketch:**

```
nodes(node_id PK, name, md5, size, content_date, media_type,
      tree_path, canonical_path, status, last_seen, downloaded_at)
albums(album_id PK, name)
album_members(album_id, node_id)
runs(run_id PK, started_at, ended_at, listed, downloaded, skipped,
     failed, archived, bytes)
```

### D3. Identity and reconciliation by `node_id`

- **Why:** Amazon's tree is mutable (device renames, moves), so path-keyed sync would re-download on every reorganization. Identity by `node_id` turns a move into a local `rename`, and a three-way diff (remote set vs. state vs. filesystem) yields exactly one action per node.
- **Alternatives:** path-keyed (simplest, but churns); mtime-keyed (unreliable across uploads).

### D4. Deletion contract: archive, never delete local

- **Why:** for a personal backup, a remote deletion is more likely a mistake or account event than a signal to destroy the only local copy. A bad sync run must be non-destructive.
- **Alternatives:** mirror (destructive); quarantine to `.trash/` (extra state, more moving parts).

### D5. Layout: chronological canonical store + symlink views

- **Why:** canonical paths depend only on content date + name, so they never churn when Amazon reorganizes; bytes are stored once; tree and album organization is preserved as state and materialized as relative symlinks that can be rebuilt at any time.
- **Alternatives:** mirror the Amazon tree (mutable, often machine-generated); album folders (duplicate bytes for multi-album media); flat (unbrowsable at scale).
- **Undated media:** nodes without a content date go under `_unsorted/` with the same collision rule, so a missing date still yields a stable, deterministic path.
- **Sketch:**

```
dest/
├── 2023/08/2023-08-14_1692626817154.jpg          canonical bytes (mtime = contentDate)
├── _by-album/Summer Vacation/1692626817154.jpg -> ../../2023/08/...
├── _by-tree/Pictures/iPhone/1692626817154.jpg  -> ../../2023/08/...
└── .amz-download/state.sqlite                     portable with dest
```

### D6. Auth: cookie file baseline + stdlib Firefox read, expiry is explicit

- **Why:** manual cookie supply always works and is portable; auto-reading a Linux Firefox profile needs no keyring decryption and smooths the common case. Both funnel into one stored credentials file.
- **How (Firefox):** read the profile's `cookies.sqlite` with the standard library — on Linux the cookie values are stored unencrypted — which removes the unmaintained `browser_cookie3` dependency and its Chrome-focused failure modes.
- **Regional naming:** accept both default cookie names (`at_main`, `ubid_main`) and regional variants (`at-acbxx`, `ubid-acbxx` per Amazon TLD), so non-US accounts authenticate without guessing.
- **Alternatives:** Playwright automated login (downloads a whole browser — overkill for four cookie values); Chrome cookie extraction (keyring failure modes for marginal gain); `browser_cookie3` (fragile and unmaintained, and it solves a problem Linux does not have).
- **Behaviour:** `login` is interactive; `sync` never prompts and exits with a re-authenticate instruction on 401. The credentials file is owner-only (0600). Whether a token-refresh flow is needed is decided by the spike gate (D10), not assumed.

### D7. Packaging: uv project with a console script

- **Why:** the tool runs on a schedule, needs tests, and benefits from managed dependencies. `[project.scripts] amz-download = "amz_download.cli:main"`, installable via `uv tool install .`.
- **Alternative:** PEP 723 single file — rejected by the user in favour of a real project.

### D8. Download strategy: per-node async, not batch zip

- **Why:** per-node transfers give per-file resumability, hash verification, deterministic naming, and (per the reference implementation's own measurements) are faster than the server-side zip for comparable batches.
- **Alternative:** `drive/v1/batchLink` server-side zip — fewer requests, but coarser, harder to resume, and couples us to zip extraction. Left as a possible future optimization for very large initial runs.

### D9. CLI framework: `typer`

- **Why:** subcommands (`login`, `sync`, `views`, `status`, `repair`) and typed options map cleanly; small dependency.
- **Alternative:** stdlib `argparse` (zero deps, more boilerplate).

### D10. Spike as a feasibility gate, not an informational note

- **Why:** session-token lifetime decides whether the non-interactive-sync requirement is satisfiable unattended, and dead album/tree endpoints decide whether layout views are deliverable at all. Treating those as "add later" open questions bakes an unstated assumption into the specs.
- **Rule:** the gate outcome is recorded in `spike-findings.md` (task 1.1) before any group 3+ task is implemented, and any outcome that changes a spec goes back through the specs artifact first.

```
outcome                          consequence
------------------------------   ----------------------------------------
all endpoints OK, token ~weeks   proceed as specced
all endpoints OK, token ~hours   auth needs a refresh flow, or the
                                 non-interactive requirement is downgraded
                                 (spec change either way)
album/tree endpoints dead        transport redesign; view scope shrinks
                                 (spec change)
all endpoints dead              change is unimplementable as specced -
                                 stop and report
```

## Risks / Trade-offs

- **[Undocumented endpoints change or block us] →** isolate all endpoint knowledge in `client.py`, validate with the spike as the first task, fail loudly and non-destructively, and keep `amazon-photos` as a documented fallback for endpoint shapes.
- **[Session token expires too quickly for unattended sync] →** sync detects expiry, aborts before touching the library, and tells the user to re-login; the spike gate (D10) decides whether a refresh flow is needed before unattended scheduling is relied upon.
- **[Request volume triggers throttling or account risk] →** cap concurrency, apply backoff on retryable errors, rate-limit listing and downloads, and never perform writes against Amazon.
- **[md5 verification is expensive on huge libraries] →** compare against stored hashes and size/state first; only hash when metadata is missing or a change is suspected, rather than rehashing the whole local library every run.
- **[State database lost or corrupted] →** the repair mode rebuilds state from the existing local library and a fresh remote listing, matching by content hash, without re-downloading verified media.
- **[Local file edits are not preserved] →** refresh and heal replace a local file when remote content changes or the file is missing; the tool does not detect local modifications (a non-goal), and this is documented rather than silent.
- **[Symlinks unavailable (Windows)] →** skip view generation with a clear warning; canonical media is unaffected.
- **[Nodes with multiple or missing parents] →** choose a deterministic primary tree path (stable ordering), record all memberships, and never let view generation fail the run.
- **[Name/date collisions] →** append a short `node_id` suffix deterministically so the same node always resolves to the same path.
- **[Very large first run] →** rely on resumable state, progress reporting, `--dry-run`, and an optional item limit so the first sync is observable and interruptible.

## Migration Plan

Greenfield: no data or API migration. The implementation replaces the `main.py` placeholder with the `amz_download` package and a console entry point. Rollback is discarding the change; no existing behavior depends on this project.

## Open Questions

- Should view generation also cover people/location aggregations, or only tree and album? Additive, does not change the approach or task breakdown.
- Is the server-side batch zip worth adding for the initial bulk import of very large libraries? A performance optimization only.
