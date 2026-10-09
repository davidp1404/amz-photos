# Proposal

## Why

`sync` prints nothing until the run is over. Measured against a local fake
server, a nine-item run 1.77 s long wrote every byte of its output in the final
0.03 s, and a successful twelve-item run wrote nothing at all until its summary
line. A first sync of a large library, which the README presents as a supported
use, is therefore indistinguishable from a hung process. The only in-flight
signal that exists today is a failure message on stderr, and that message is
printed a second time in the summary, so a log captured with `2>&1` shows each
failure twice. The package has no logging at all, so when a run does go wrong
the only evidence available is an error string and an exit code.

## What Changes

- Add a repeatable `-v` / `--verbose` flag to every command, with three levels.
  No flag keeps the current behavior (warnings only), `-v` adds a narrative of
  what the run is doing, and `-vv` adds per-request detail.
- At the `-v` level, report which listing is being fetched and which action is
  being applied to which node, with the reason and target path.
- At the `-vv` level, also report each request's method, path, parameters and
  status, each pagination window, retries with their backoff, and the byte count
  and content hash of each transfer.
- Add a single logging setup module that owns one handler for the package
  logger and writes to standard error, so no configuration of the host
  interpreter's logging is required or assumed.
- Log from the client, the sync engine, the state store, the views generator,
  and the CLI at the levels above.
- Keep results on standard output and logs on standard error, so the dry-run
  report, `status` output, and the sync summary stay machine-parseable exactly
  as they are today.
- Add a test that a `-vv` run emits no cookie value, header value, or
  credentials file contents.
- Leave exit codes, the summary format, and the `--concurrency`, `--limit`,
  `--dry-run`, and `--credentials` flags unchanged.

## Capabilities

### New Capabilities

- `operation-logging`: what the tool narrates about its own work, how the
  verbosity level is selected and defaulted, which stream each kind of output
  is written to, and the constraint that log records carry no credentials.

### Modified Capabilities

None. The requirement in `amazon-photos-auth` that secrets never reach logs or
messages already exists; this change must satisfy it and does not alter it. The
CLI gains a flag, but no requirement of `amazon-photos-sync` changes, since
reporting and exit codes stay as specified.

## Impact

- `src/amz_download/log.py` (new): `configure(verbose)` sets the package logger
  level and attaches one stderr handler.
- `src/amz_download/cli.py`: a shared `VerboseOption` alias and one
  `log.configure(verbose)` call in each of the six commands.
- `src/amz_download/client.py`: request, pagination, retry, and download
  records, with cookies and headers excluded.
- `src/amz_download/sync.py`: phase, action, and worker-pool records.
- `src/amz_download/state.py`, `views.py`: debug-level records.
- `tests/test_log.py` (new) plus additions to `tests/test_cli.py` and
  `tests/test_sync.py`.
- No new runtime dependencies: `logging` is standard library and `rich` is
  already a dependency.
