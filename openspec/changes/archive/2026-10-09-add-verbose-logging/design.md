# Design

## Context

See `proposal.md` (Why) for motivation. Current state and constraints that shape
the approach:

- `amz_download` has no logging at all. Output is split across two `rich`
  consoles (`console` on stdout, `err_console` on stderr) plus a `warn` callback
  that the CLI injects into `sync()` and `execute_plan` for per-item errors.
- Typer 0.27.2 is in use and no longer depends on Click. Flag placement
  behaviour was verified against it rather than assumed; see D3.
- `sync` executes actions on a fixed pool of worker tasks sharing one
  `StateStore`, so diagnostics are emitted from concurrent tasks.
- Runtime dependencies are `httpx`, `typer`, and `rich`; anything else must come
  from the standard library.
- Six commands exist (`login`, `check`, `sync`, `views`, `status`, `repair`),
  each of which needs the flag.

## Goals / Non-Goals

**Goals:**

- Diagnostics that answer "what is it doing right now" during a long run, at
  three levels, with the default level unchanged.
- One place that owns logging configuration, so no command has to remember it.
- Results that stay machine-parseable, on stdout, byte-for-byte as today.
- Diagnostics that never carry credential material.

**Non-Goals:**

- A progress bar, percentages, or estimated completion. Level 1 narrates actions
  but does not compute totals or rates.
- Fixing the duplicate failure line: today a failure is printed once live on
  stderr and again in `print_summary` from `summary.errors`. Removing that is a
  separate, behavior-visible change and is not part of this scope.
- Changing exit codes, the summary format, `--concurrency`, `--limit`,
  `--dry-run`, or `--credentials`.
- Log rotation, file handlers, or any host-level logging configuration.

## Decisions

### D1. Diagnostics go through the standard library `logging`

A single package logger (`amz_download`) with one handler, configured by a new
`log.py`, rather than more `print` calls or an extension of the existing `warn`
callback.

- **Why:** level filtering is exactly the feature requested, it comes from the
  standard library, and records are assertable with pytest's `caplog`, which the
  project already uses in `tests/test_auth.py`. The existing `warn` callback
  only reaches `execute_plan` and `generate_views`, and threading a verbosity
  level through every call site would be more invasive than a logger.
- **Alternatives:** extend the `warn` callback into a general sink (more
  parameters on already long signatures, no level filtering); per-module
  `Console.print` calls (no levels, no single off switch); a third-party logger
  (adds a dependency to solve a solved problem).

### D2. One handler, on stderr, formatted by `rich`

`log.configure(verbose)` attaches a single `rich.logging.RichHandler` bound to
`Console(stderr=True)`, added only when the package logger has no handler yet.

- **Why:** `rich` is already a dependency and the tool already writes errors to
  stderr, so diagnostics land where the existing failures go. Guarding on
  "no handler yet" keeps `configure()` safe to call at the top of every command
  and prevents duplicate output if it is called twice.
- **Alternatives:** `logging.StreamHandler(sys.stderr)` (works, but loses the
  formatting `rich` gives for free); a handler on stdout (rejected: diagnostics
  would pollute captured results, breaking D4's stream separation).

### D3. The flag lives on each command, not on a group callback

A shared `VerboseOption = Annotated[int, typer.Option(..., count=True)]` alias
declared on all six commands, each calling `log.configure(verbose)` first.

- **Why:** with a group callback the option must precede the subcommand
  (`amz-download -v sync`); verified against typer 0.27.2, where
  `amz-download sync -v` fails with "No such option: -v". Users type the
  subcommand first, and a per-command flag also shows up in each command's
  `--help`.
- **Alternative:** a single `@app.callback()` option (one declaration, but the
  `amz-download -v sync` ordering and no per-command help entry).
- **Cost:** the flag is repeated on six commands, which is six lines.

### D4. Three levels, mapped from the flag count

`verbose == 0` → `WARNING` (today's behavior), `1` → `INFO`, `>= 2` → `DEBUG`.

- **Why:** matches the flag's counter semantics and needs no extra option. The
  default level is deliberately the current one, so an unflagged run is
  unchanged.
- **Alternative:** a `--log-level` string option (more flexible, but invites
  inventing level names and parsing them for a tool that only needs three).

### D5. Quiet by default, and testable

`log.configure(verbose)` sets the level and attaches the handler; it does not
set `propagate = False`.

- **Why:** leaving propagation on lets pytest's `caplog` observe records, which
  the secret-scanning test depends on. Level computation is a single expression
  so there is no path that leaves warnings invisible.
- **Risk mitigation:** an existing test already asserts a cookie-file login
  prints to stderr and exits 2, which fails if the default level ever swallows a
  warning.

### D6. Records are formatted lazily and must survive `rich` markup parsing

Every call site passes arguments to the logger (`logger.info("... %s", value)`),
never an f-string, and `RichHandler` is constructed with `markup=False`.

- **Why:** lazy formatting means a quiet run pays nothing for records never
  emitted, and `markup=False` matters because node names, album names, and paths
  legitimately contain `[` and `]`, which `rich` would otherwise read as markup
  and either eat or invert.

### D7. Level assignment per call site

| Module | Level | Records |
| --- | --- | --- |
| `client._request` | DEBUG | method, path, params, status per attempt |
| `client._request` | INFO | retry after backoff, with attempt number |
| `client.download` | INFO | transfer start with node id and target |
| `client.download` | DEBUG | bytes received, computed content hash |
| `client` listing / `_paginate` | INFO / DEBUG | which listing starts; each pagination window |
| `sync.sync` | INFO | listing complete, planned action counts |
| `sync._apply_action` | INFO | action, node id, reason, canonical path |
| `execute_plan` | DEBUG | worker pool size |
| `state` | DEBUG | node upsert by id |
| `views.generate_views` | DEBUG | link created, updated, removed |
| `cli` | INFO | destination, session file loaded |

The load-bearing record is `_apply_action`'s: it answers "what is it doing" per
node. Everything else is supporting detail.

### D8. Diagnostics carry paths and parameters, never credentials

Records are built from the URL path and its query parameters only. Cookie
values, the `x-amzn-sessionid` header, and credentials file contents are never
passed to a log call.

- **Why:** the `amazon-photos-auth` spec already forbids cookie and token values
  in logs; this adds the new `-vv` surface to that obligation, so it needs a test
  rather than an assumption.

## Risks / Trade-offs

- **[`-vv` produces one record per node per action plus one per request, which is
  a lot of lines for a large library] →** every record at or above DEBUG is
  reachable only through the repeated flag, so the volume is opt-in; INFO stays
  at one line per action, which is the level the requirement asks for.
- **[Concurrent workers emit records concurrently and lines interleave] →** each
  record names the node id and action, so an interleaved stream is still
  readable; `RichHandler` writes whole lines, so records are never truncated
  into each other.
- **[A redirect to a single file interleaves stderr diagnostics with stdout
  results, and failures still appear twice] →** acknowledged as a known cosmetic
  defect and a non-goal here; stream separation keeps the separate-capture case
  correct, which is the case scripts depend on.
- **[`markup=False` is a silent-behavior landmine if someone removes it] →** the
  tests exercise node names and album names containing brackets at the DEBUG
  level.
- **[Configure logic could regress and hide warnings] →** D5 keeps it to one
  expression, and the existing login/check CLI tests assert on stderr text.
- **[Six commands each calling `configure` invites drift if a seventh is added]
  →** the flag is a type alias, so a new command copies one parameter and one
  call; `tests/test_log.py` asserts the level mapping for all three levels
  centrally.

## Migration Plan

Additive and behavior-preserving at the default level. No state, schema, or
output-format change, so there is nothing to migrate. Rollback is reverting the
commit; existing invocations and scheduled timers are unaffected.

## Open Questions

- Should a periodic "still working, N items done" line be added later so that a
  long first sync reports liveness without narration? Additive, and it would
  need its own capability decision; deferred out of this change.
- Should view link churn be reported at INFO rather than DEBUG? A matter of
  taste that does not change the approach or the task breakdown.
