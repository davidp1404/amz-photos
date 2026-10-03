# Proposal

## Why

Amazon Photos offers no supported public API and no command-line tool, so getting media out of it means clicking through the web UI or trusting a closed desktop app. Users who want an ongoing, verifiable local copy of their photos and videos have no durable option, and the existing browser-automation scripts are fragile and non-incremental. This change builds a maintained CLI that keeps a local library in sync with Amazon Photos.

## What Changes

- Introduce a uv-managed Python package `amz-download` (installed as a console script) implementing a one-way, incremental backup from Amazon Photos to the local filesystem.
- Authenticate using stored Amazon session cookies, captured either by manually supplying a cookie file or by auto-reading a local Firefox profile; accept both default and regional cookie names; detect session expiry and tell the user to re-authenticate.
- Enumerate the remote library completely: media nodes (photos and videos) and folder nodes to reconstruct the tree, plus album listings and album membership, so every media node resolves to a tree location and album memberships.
- Maintain sync state in a SQLite database keyed by Amazon `node_id`, enabling a three-way diff: moved media becomes a local file move (no re-download), new media is fetched, missing local files are healed, and media deleted remotely is archived (retained locally) rather than deleted.
- Rebuild sync state from an existing local library and a fresh remote listing (a repair mode), so a lost or corrupted state database never forces re-downloading media that is already present and verified.
- Report library status — counts by node status, last run outcome, and recorded failures — from recorded state, without contacting Amazon.
- Store media canonically in a chronological tree `<dest>/YYYY/MM/<contentDate>_<name>.<ext>`, with md5 verification, resumable atomic downloads, and file mtime set to the original content date; regenerate symlink views (by Amazon tree path and by album) at the end of every completed sync, and on demand.
- Scope media to photos and videos; non-media Amazon nodes are ignored.
- Include a validation spike that confirms the undocumented Amazon web endpoints still respond — media and folder listing, album membership, and content download — and measures session cookie lifetime; the recorded outcome is a design gate for unattended scheduling (see `design.md`, D10).

## Capabilities

### New Capabilities
- `amazon-photos-auth`: acquiring, storing, and validating an Amazon Photos web session so that sync can run non-interactively, including regional cookie naming.
- `amazon-photos-sync`: incremental one-way synchronization of remote media to a local library, covering identity, diffing, download integrity, the archive-on-delete contract, status reporting, and state rebuild from an existing library.
- `media-library-layout`: the canonical on-disk layout for synced media and the regenerable organizational views derived from it.

### Modified Capabilities
- None.

## Impact

- New package `src/amz_download/` with modules for CLI, auth, HTTP client, tree building, state, sync, and views; replaces the `main.py` placeholder.
- `pyproject.toml` gains a `[project.scripts]` entry point and runtime dependencies (`httpx`, `typer`, `rich`); Firefox cookie extraction reads the profile's `cookies.sqlite` with the standard library, avoiding a third-party cookie-extraction dependency. A dev dependency group covers tests.
- Depends on undocumented Amazon Photos web endpoints, so behavior may break when Amazon changes them; the endpoint/cookie-lifetime spike is the first implementation task and its outcome is a design gate.
- Automates an undocumented web surface using the user's primary Amazon account, which carries throttling, lockout, and terms-of-service enforcement risk; the client is read-only, and concurrency and request rates are bounded to mitigate this.
- The first synchronization of a large library may run for hours or days; runs are resumable, interruptible, and observable (`--dry-run`, progress, item limits).
- On-disk views use symlinks, making the default experience Linux/macOS-first (Windows symlinks require elevated mode).
- Requires the user to be signed in to Amazon Photos in a browser or to supply cookies before the first sync.
