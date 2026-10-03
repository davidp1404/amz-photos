# Tasks

## 1. Validation spike and project scaffolding

- [x] 1.1 Run the endpoint/lifetime spike against a live Amazon Photos session: confirm which `drive/v1` calls still respond — media and folder listing via `search`, album listing and album membership, and content download — and measure how long the stored cookies remain valid; record results in `openspec/changes/amazon-photos-sync/spike-findings.md`. Verify the file states, per endpoint, working/blocked, the observed session lifetime, and which spike-gate outcome (design D10) applies. [No live session was available in this environment; endpoint shapes are recorded from the reference implementation and the lifetime measurement is explicitly marked UNVERIFIED, with the D10 outcome recorded as "cannot confirm unattended". See spike-findings.md.]
- [x] 1.2 Add runtime dependencies (`httpx`, `typer`, `rich`) and a dev dependency group, and configure `[project.scripts] amz-download = "amz_download.cli:main"`. Verify `uv sync` succeeds and `uv run amz-download --help` prints usage.
- [x] 1.3 Create the `src/amz_download/` package with modules `cli`, `auth`, `client`, `tree`, `state`, `sync`, `views` as importable stubs, and retire `main.py`. Verify `uv run python -c "import amz_download"` succeeds and each module imports.

## 2. Authentication (`amazon-photos-auth`)

- [x] 2.1 Implement cookie-file parsing and validation for the required Amazon cookies, accepting both default and regional cookie names. Verify a unit test accepts a valid set including regional names and rejects a set missing a cookie, naming the missing cookie without printing values.
- [x] 2.2 Implement Firefox-profile cookie extraction by reading the profile's `cookies.sqlite` with the standard library. Verify unit tests read from a fixture profile database and report a clear fallback error when no profile or no matching cookies are found.
- [x] 2.3 Implement credential storage in the per-user config location with owner-only permissions. Verify a unit test round-trips the stored session and asserts the file mode is `0600`.
- [x] 2.4 Implement session validation and the expiry error path used by sync. Verify a unit test where a rejected session produces a non-zero, re-authentication error and performs no library writes.
- [x] 2.5 Guarantee secrets never appear in output. Verify a unit test scans captured logs and messages for cookie values across success and failure paths.
- [x] 2.6 Document authentication (cookie file, regional names, and Firefox extraction) in `README.md`. Verify the documented commands run as written.

## 3. Amazon web API client

- [x] 3.1 Define the media/node and album models and parsing. Verify unit tests parse representative payloads, including correct exclusion of non-media nodes.
- [x] 3.2 Implement the search listing calls with pagination for media nodes and folder nodes (`kind:FOLDER`). Verify a test with a mocked transport returns all pages for both node kinds and stops at the end of the library.
- [x] 3.3 Implement album listing and album-membership calls. Verify tests with a mocked transport return every album and its member node identifiers, paginating where the endpoint does.
- [x] 3.4 Implement streaming content download. Verify a test streams bytes to a file and independently computes the expected md5.
- [x] 3.5 Implement concurrency limiting, retry with backoff, and rate limiting for listing and downloads. Verify tests confirm transient errors are retried and in-flight requests never exceed the configured cap.

## 4. Sync state store

- [x] 4.1 Implement the SQLite schema (`nodes`, `albums`, `album_members`, `runs`) with initialization and migration. Verify a test creates a fresh database and reports the expected tables and columns.
- [x] 4.2 Implement node, album, and run upsert/lookup operations. Verify unit tests cover insert, update, and read-back of each entity.
- [x] 4.3 Implement the queries the reconciliation needs (remote vs. recorded vs. filesystem) and marking of remotely deleted nodes. Verify unit tests cover each diff outcome and the archive/reappear transitions.
- [x] 4.4 Implement state rebuild (repair): match existing local files to remote nodes by content hash and record them without re-downloading. Verify tests rebuild state over a populated library, confirm a subsequent sync transfers nothing for adopted nodes, and confirm unmatched local files are reported, not moved or deleted.

## 5. Library layout and views (`media-library-layout`)

- [x] 5.1 Implement the canonical path builder from content date and name with deterministic collision suffixes and a deterministic fallback location for undated media. Verify unit tests cover normal paths, same-name/same-date collisions, and nodes with missing dates.
- [x] 5.2 Set the stored file's modification time to the content date when finalizing a download. Verify a test asserts the resulting mtime.
- [x] 5.3 Implement by-tree and by-album symlink views with relative targets derived from state. Verify tests create views and confirm each link resolves to its canonical file.
- [x] 5.4 Implement view regeneration and pruning from state. Verify a test removes a stale link and creates a new one while leaving canonical files byte-identical.
- [x] 5.5 Implement graceful degradation when symlinks are unsupported. Verify a test that forces a symlink failure completes without error, warns, and leaves canonical media intact.
- [x] 5.6 Regenerate views automatically at the end of every completed sync run. Verify a test syncs an album change and asserts the views reflect it without invoking the views command separately.

## 6. Incremental sync engine (`amazon-photos-sync`)

- [x] 6.1 Build the remote tree from listed media and folder nodes, choosing a deterministic primary path and recording all memberships. Verify tests cover single-parent, multi-parent, and missing-parent nodes.
- [x] 6.2 Implement the three-way reconciliation producing one action per node (`download`, `refresh`, `skip`, `heal`, `move`, `archive`). Verify table-driven tests cover every outcome.
- [x] 6.3 Implement move detection by `node_id`. Verify a test confirms a remote path change relocates the local file and issues no content request.
- [x] 6.4 Implement the download pipeline: temp file, md5 verification, atomic finalize, and resume from recorded state. Verify tests cover a hash mismatch (partial discarded, item failed) and resumption without re-downloading completed items.
- [x] 6.5 Implement archive-on-remote-delete and clearing of the mark when media reappears. Verify tests confirm the local file is retained and reappearance does not re-download.
- [x] 6.6 Implement dry-run mode. Verify a test confirms planned actions are reported and neither the filesystem nor state is modified.
- [x] 6.7 Implement failure isolation and the run summary with a non-zero exit on failures. Verify a test with one failing item continues the run and reports it.
- [x] 6.8 Confirm idempotency. Verify a test runs sync twice with no remote changes and asserts zero transfers and zero file changes on the second run.

## 7. Command-line interface

- [x] 7.1 Wire the `login`, `sync`, `views`, `status`, and `repair` subcommands with options for destination, dry-run, concurrency, and item limits. Verify `--help` works for each subcommand.
- [x] 7.2 Implement non-interactive sync and the exit-code contract. Verify a test with no stored session returns non-zero with a re-authenticate instruction and does not prompt.
- [x] 7.3 Implement progress and end-of-run summary output. Verify a mocked run prints counts for downloaded, skipped, moved, archived, and failed items.
- [x] 7.4 Implement status reporting from recorded state only. Verify a test asserts reported counts match recorded state and that no network request is made.

## 8. Integration and documentation

- [x] 8.1 Add an end-to-end integration test against a fake Amazon server covering login, first sync with albums, view generation, re-sync with a move and a remote deletion, state rebuild (repair), and a no-op run. Verify it asserts the expected canonical tree, view links, archive marking, repair without re-download, and idempotency.
- [x] 8.2 Document usage, layout, scheduling (cron/systemd), and limitations — including that refresh and heal replace local files (local edits are not preserved) and that automation carries throttling, lockout, and terms-of-service risk on the user's Amazon account. Verify the documented commands and example timer run as written.
- [x] 8.3 Run the full test suite and `openspec validate --changes amazon-photos-sync --strict`. Verify both pass.
