# operation-logging Specification

## Purpose

Describe how the tool reports on its own work while it runs, so a user can tell
a working sync from a stalled one and diagnose a failing one, without changing
what the tool does to the library.

## Requirements

### Requirement: Verbosity is selected by a repeatable flag

The system SHALL accept a repeatable `-v` / `--verbose` flag on every command
and SHALL default to no extra output when the flag is absent, so existing
invocations and scripts behave exactly as before.

#### Scenario: No flag means no extra output

- **WHEN** any command runs without the verbosity flag
- **THEN** the system emits no informational or debug records and only reports
  warnings and errors as it does today

#### Scenario: Single flag adds a run narrative

- **WHEN** a command runs with the flag given once
- **THEN** the system reports which work it is doing as it proceeds

#### Scenario: Repeated flag adds request detail

- **WHEN** a command runs with the flag given twice
- **THEN** the system additionally reports detail about each request it makes

### Requirement: The run is narrated as it happens

At the informational level the system SHALL report each listing it fetches and,
for each media node, the action being applied, the reason for it, and the target
path, so the user can see progress while a long run is still in flight.

#### Scenario: Each action is reported as it is applied

- **WHEN** a sync run downloads, refreshes, heals, moves, archives, or skips a
  media node
- **THEN** the system reports the action, the node identifier, and the canonical
  path it applies to, while the run is still running

#### Scenario: A failing item is reported when it fails

- **WHEN** an individual media item fails during a run
- **THEN** the system reports that failure at the moment it happens rather than
  waiting for the run to end

### Requirement: Request detail is available on demand

At the repeated-flag level the system SHALL report the method, path, parameters,
and outcome status of each request it makes, the window of each paginated
response, and each retry with the delay before the next attempt.

#### Scenario: Retried transfer shows its attempts

- **WHEN** a transfer is retried after a transient failure and later succeeds
- **THEN** the reported detail includes the failed attempt, the wait, and the
  succeeding attempt

#### Scenario: Transferred bytes and content hash are reported

- **WHEN** a media item is transferred at the repeated-flag level
- **THEN** the reported detail includes the number of bytes received and the
  computed content hash

### Requirement: Results and diagnostics use separate streams

The system SHALL write diagnostics to standard error and SHALL keep results,
including the dry-run report, the status report, and the sync summary, on
standard output unchanged, so a caller can capture results without capturing
diagnostics.

#### Scenario: Standard output carries results only

- **WHEN** a command runs with the verbosity flag while its standard output is
  captured separately from its standard error
- **THEN** the captured standard output contains the same result lines as an
  unflagged run, and all diagnostics appear in the captured standard error

### Requirement: Diagnostics never expose credentials

The system SHALL NOT write cookie values, authorization or session header
values, or the contents of the credentials file to diagnostics, including at the
repeated-flag level.

#### Scenario: Debug output omits cookie and header values

- **WHEN** a run reports request detail
- **THEN** no cookie value and no session header value appears in any diagnostic

#### Scenario: Credentials file contents are never logged

- **WHEN** a session is loaded from the credentials file
- **THEN** the diagnostics name the file and its load outcome without logging
  anything the file contains

### Requirement: Verbosity does not change what the tool does

The system SHALL leave the library, the recorded state, and the exit code of a
run identical whether or not diagnostics are requested, so requesting output
can never alter the outcome of a synchronization.

#### Scenario: Same outcome at every verbosity

- **WHEN** a synchronization run is repeated at each verbosity level against an
  unchanged account
- **THEN** each run transfers the same media, writes the same files and state,
  and exits with the same code
