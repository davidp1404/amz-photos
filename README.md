# amz-download

A command-line tool that keeps a local library incrementally synchronized with
Amazon Photos. It is a **one-way, read-only** backup: it downloads photos and
videos, never uploads, and never deletes local media.

Amazon Photos has no supported public API. This tool uses the authenticated web
session against Amazon's undocumented `drive/v1` endpoints, the same surface the
web app uses. It can break without notice when Amazon changes those endpoints
(see [Limitations](#limitations) and `openspec/changes/amazon-photos-sync/spike-findings.md`).

## Install

Requires Python 3.13+ and [uv](https://docs.astral.sh/uv/).

```sh
uv sync
uv run amz-download --help
```

Or install it as a tool:

```sh
uv tool install .
amz-download --help
```

## Authentication

The tool needs three Amazon cookies: `at_main`, `ubid_main`, and `session-id`
(regional accounts use names such as `at-acbde` / `ubid-acbde`; both forms are
accepted). Capture them once with `login`; the session is stored in the per-user
config directory (`~/.config/amz-download/credentials.json`, or
`$XDG_CONFIG_HOME`) with owner-only (`0600`) permissions. `login` is the only
interactive command.

### From a cookie file

Export the cookies from your browser in Netscape `cookies.txt` format, a JSON
object, or `name=value` lines, then:

```sh
amz-download login --cookie-file ~/cookies.txt
```

A cookie file missing a required cookie is rejected by name, and no cookie value
is ever printed. A file that cannot be read or parsed is reported by path and
exits 2, not as a traceback.

### From Firefox

On Linux, Firefox stores cookie values unencrypted, so the tool can read them
directly from the profile's `cookies.sqlite` using the standard library:

```sh
amz-download login --firefox
```

Use `--profile PATH` to point at a specific profile directory (or its
`cookies.sqlite`). If no profile is found, or it holds no Amazon cookies, the
command reports the reason and tells you to supply cookies manually.

### Re-authentication

Sessions expire. `sync` never prompts; when Amazon rejects the session it aborts
before touching the library and exits non-zero with an instruction to
re-authenticate. Check the stored session at any time with one request:

```sh
amz-download check          # exit 0 valid, 2 missing/rejected, 3 network error
```

Then refresh it:

```sh
amz-download login --firefox
amz-download sync
```

## Usage

```sh
# First sync into ./amz-library (or --dest/-d elsewhere)
amz-download sync

# Report what would happen without writing anything
amz-download sync --dry-run

# Bound concurrency and the number of transfers (useful for a first, large run)
amz-download sync --dest /data/photos --concurrency 4 --limit 500

# Regenerate the organizational views from recorded state (no network)
amz-download views --dest /data/photos

# Confirm the stored session still works (one request; for timers/health checks)
amz-download check

# Report recorded status without contacting Amazon
amz-download status --dest /data/photos

# Rebuild state from an existing library and a fresh remote listing
amz-download repair --dest /data/photos
```

`sync` exits `0` on success, `1` when some items failed (the rest are still
processed), and `2` when authentication is required.

## Verbosity

Every command takes a repeatable `-v` / `--verbose` flag. Without it nothing
changes: warnings still appear and nothing else is printed. The flag belongs to
the subcommand, so it goes after it (`amz-download sync -v`, not
`amz-download -v sync`).

- `-v` narrates the run: the destination and session file in use, each listing as
  it is fetched, the reconciliation against recorded state, and one line per node
  naming the action, the node id, the canonical path, and the reason.
- `-vv` adds request detail: every request's method, URL, parameters, and
  response status, each pagination window, retries with the delay before the
  next attempt, and the byte count and content hash of each transfer.

```sh
amz-download sync -v --dest /data/photos
amz-download sync -vv --dest /data/photos
```

Diagnostics go to standard error and results to standard output, so the summary,
the `status` report, and the dry-run output stay exactly as they are and can be
captured on their own:

```sh
amz-download sync -v 2>/var/log/amz-download.diag >/var/log/amz-download.out
```

Cookie and session values never appear in diagnostics, at any level. See
`openspec/changes/add-verbose-logging` for the behavior this implements and
`tests/test_log.py` and `tests/test_client.py` for the tests that hold it in
place.

## Layout

Media is stored once, canonically, under `<dest>/YYYY/MM/<contentDate>_<name>.<ext>`
with the file's modification time set to its content date. Undated media goes to
`<dest>/_unsorted/`. Same-name/same-date collisions are disambiguated with a
short node-id suffix, applied deterministically.

Amazon's organizational metadata is preserved as symlink views, rebuilt from
recorded state at the end of every completed sync (or with `views`):

```
<dest>/
├── 2023/08/2023-08-14_1692626817154.jpg     canonical bytes (mtime = capture time)
├── _by-tree/Pictures/iPhone/1692626817154.jpg -> ../../2023/08/...
├── _by-album/Summer Vacation/1692626817154.jpg -> ../../2023/08/...
└── .amz-download/state.sqlite               recorded state (portable with dest)
```

Only one canonical copy of each media item exists; the views are links, so
organizing by album and by folder never duplicates bytes.

## Scheduling

Because `sync` is non-interactive and resumable, it can run from a timer.
`amz-download check` is a cheap companion health check: exit `0` means the
session is still valid, `2` means it needs a fresh local `login` and re-copy.

Cron:

```cron
0 3 * * * /path/to/amz-download sync --dest /data/photos >> /var/log/amz-download.log 2>&1
```

systemd (`~/.config/systemd/user/amz-download.service` and `.timer`):

```ini
# amz-download.service
[Unit]
Description=Sync Amazon Photos

[Service]
Type=oneshot
ExecStart=%h/.local/bin/amz-download sync --dest %h/amz-library
```

```ini
# amz-download.timer
[Unit]
Description=Daily Amazon Photos sync

[Timer]
OnCalendar=daily
Persistent=true

[Install]
WantedBy=timers.target
```

```sh
systemctl --user enable --now amz-download.timer
```

## Limitations

- **Local edits are not preserved.** `refresh` and `heal` replace a local file
  when the remote content changes or the file is missing. The tool does not
  detect local modifications; do not edit files in place inside the library.
- **Automation carries account risk.** This uses the user's primary Amazon
  account against an undocumented web surface. Throttling, lockout, and
  terms-of-service enforcement are possible. The client is read-only and bounds
  concurrency and request rate, but these risks remain yours.
- **Session lifetime is still being measured.** The `drive/v1` endpoints were
  confirmed live on 2026-10-03 (`spike-findings.md`), including a full media
  listing and a real download, but the cookie lifetime has not yet been
  observed over time. Until it is, treat scheduled runs as best-effort: they
  fail safely and tell you to re-authenticate, but may need it frequently.
- **Windows is a degraded target.** Views use symbolic links; where symlinks are
  unavailable the run completes, reports that views were skipped, and leaves
  canonical media intact.
- **Read-only.** No upload, rename, or delete is ever performed against Amazon
  Photos.

## Development

```sh
uv sync
uv run pytest
openspec validate --specs --strict
```

### Build a wheel

This project uses the hatchling backend with a `src/` layout, so `uv build`
produces a pure-Python wheel:

```sh
uv build --wheel    # wheel only -> dist/amz_download-<version>-py3-none-any.whl
uv build            # both sdist (.tar.gz) and wheel
uv build --sdist    # sdist only
```

The wheel contains only the `amz_download` package and declares the
`amz-download` console script, so it installs anywhere Python ≥ 3.13 runs:

```sh
uv pip install dist/amz_download-*.whl     # into the active virtualenv
uv tool install dist/amz_download-*.whl    # or as an isolated tool
```

### Releasing: bumping the version

The version has a **single source of truth**: the `__version__` constant in
`src/amz_download/__init__.py`. `pyproject.toml` does not hold a literal
version; hatchling reads it from that file at build time
(`[tool.hatch.version]`), so the packaging metadata and `amz-download version`
can never disagree.

To cut a release:

1. Edit the one line in `src/amz_download/__init__.py`:

   ```python
   __version__ = "0.1.2"
   ```

   Follow [semantic versioning](https://semver.org/): `MAJOR.MINOR.PATCH`.

2. Rebuild the wheel (the version is baked into the artifact and its filename):

   ```sh
   uv build
   ```

3. Verify both the artifact and the runtime report the new version:

   ```sh
   ls dist/amz_download-*.whl     # -> dist/amz_download-0.1.2-py3-none-any.whl
   uv run amz-download version    # -> 0.1.2
   ```

Do not edit the version in `pyproject.toml`; there is nothing to edit there.
Build output in `dist/` is gitignored, so delete stale wheels before publishing.
