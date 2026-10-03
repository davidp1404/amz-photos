# amazon-photos-sync Specification

## Purpose

Keep a local media library incrementally synchronized with an Amazon Photos account, downloading only what is new or changed and never destroying local copies.

## Requirements

### Requirement: Enumerate remote media

The system SHALL retrieve every photo and video node in the account — with its identifier, name, content date, size, content hash, tree location, and album membership — following pagination until the whole library has been read, and SHALL retrieve the folder nodes and album structures needed to resolve tree locations and album memberships.

#### Scenario: Full library listing

- **WHEN** synchronization lists the account and the account contains more items than a single response returns
- **THEN** the system retrieves every page and records all media nodes

#### Scenario: Non-media excluded

- **WHEN** the account contains non-media nodes such as documents or archives
- **THEN** the system does not download them and does not treat them as media to synchronize

#### Scenario: Folder and album structure resolved

- **WHEN** a listed media node references parent folders or albums
- **THEN** the system resolves their names so the node's tree location and album memberships are complete

### Requirement: Track media by stable identity

The system SHALL track each local file by its Amazon node identifier rather than by path, so that remote reorganization never causes an unnecessary re-download.

#### Scenario: Moved media is relocated locally

- **WHEN** a previously synced node's canonical path changes (for example it is renamed) while its identifier and content are unchanged
- **THEN** the system moves the existing local file to the new canonical location without downloading it again

### Requirement: Reconcile remote and local state

The system SHALL compare the remote node set against recorded state and the local filesystem, then take the action implied by the difference.

#### Scenario: New media downloaded

- **WHEN** a remote media node has no local record
- **THEN** the system downloads it into the canonical layout

#### Scenario: Changed media refreshed

- **WHEN** a remote media node's recorded content hash differs from its current content hash
- **THEN** the system re-downloads it and replaces the local file

#### Scenario: Unchanged media skipped

- **WHEN** a remote media node is recorded, its content hash matches, and its local file exists
- **THEN** the system skips the transfer

#### Scenario: Missing local file healed

- **WHEN** a remote media node is recorded and unchanged but its local file is absent
- **THEN** the system re-downloads it

### Requirement: Downloaded media is verified and resumable

The system SHALL write downloads to a temporary location and finalize them atomically only after verifying the content hash, and SHALL be able to resume interrupted work.

#### Scenario: Hash mismatch not committed

- **WHEN** a downloaded file's content hash does not match the remote metadata
- **THEN** the system discards the partial file and records the item as failed

#### Scenario: Interrupted run resumes

- **WHEN** a synchronization run is interrupted and later re-run
- **THEN** the system continues from recorded state without re-downloading completed, verified items

#### Scenario: Transient failure retried

- **WHEN** a download fails due to a transient network or server error
- **THEN** the system retries with backoff before recording the item as failed

### Requirement: Remote deletion does not delete local media

The system SHALL retain local media when its remote counterpart is no longer present, mark it as remotely deleted, and clear that mark without downloading when the media reappears.

#### Scenario: Remote deletion archived

- **WHEN** a recorded media node is absent from a fresh remote listing
- **THEN** the system keeps the local file and records that it is no longer present remotely

#### Scenario: Reappearing media restored to normal

- **WHEN** a node marked as remotely deleted appears again in a remote listing with unchanged content
- **THEN** the system clears the mark and does not re-download the file

### Requirement: Synchronization is idempotent

The system SHALL leave the local library and recorded state unchanged when a synchronization run finds no remote changes.

#### Scenario: No-op re-run

- **WHEN** synchronization runs twice with no remote changes between the runs
- **THEN** the second run transfers no media and changes no files

### Requirement: Dry run reports without modifying

The system SHALL support a dry-run mode that reports the actions a synchronization would take without modifying the library or recorded state.

#### Scenario: Dry run performs no writes

- **WHEN** synchronization runs in dry-run mode
- **THEN** it reports planned downloads, moves, and archives and writes nothing to disk or recorded state

### Requirement: Failures are isolated and reported

The system SHALL continue processing remaining media when an individual item fails, and SHALL report the failures with a non-zero exit status.

#### Scenario: One item fails, run continues

- **WHEN** a single media item cannot be downloaded or verified
- **THEN** the system continues with the remaining items and reports the failure at the end

### Requirement: State rebuild from an existing library

The system SHALL be able to rebuild recorded state from an existing local library and a fresh remote listing, matching local files to remote nodes by content hash, without re-downloading media that is already present and verified.

#### Scenario: Adopted media not re-downloaded

- **WHEN** state is rebuilt over a library whose files already match the remote content hashes
- **THEN** a subsequent sync transfers no media for those nodes

#### Scenario: Unmatched local files reported

- **WHEN** a local file has no matching remote node after a rebuild
- **THEN** the system reports it without deleting or moving it

### Requirement: Library status reported locally

The system SHALL report the recorded state of the library — counts by node status, the outcome of the last run, and recorded failures — using recorded state only, without contacting Amazon Photos.

#### Scenario: Status without network access

- **WHEN** the user requests library status
- **THEN** the system reports counts by node status and the last run outcome and makes no request to Amazon Photos
