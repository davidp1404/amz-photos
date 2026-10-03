# Spec Delta

## Purpose

Define where synchronized media is stored locally and the regenerable views that expose Amazon's organizational metadata without duplicating media bytes.

## ADDED Requirements

### Requirement: Canonical chronological layout

The system SHALL store each media file under a destination root at a path derived from its content date and original name: `<root>/YYYY/MM/<contentDate>_<name>.<ext>`.

#### Scenario: Media placed by content date

- **WHEN** a media node with a content date is downloaded
- **THEN** it is stored at `<root>/YYYY/MM/<contentDate>_<name>.<ext>` using its original name and extension

### Requirement: Stable canonical paths

The system SHALL derive canonical paths only from content date and name, so that changes to Amazon's organizational metadata never change a file's canonical path.

#### Scenario: Reorganization does not move canonical files

- **WHEN** a node's Amazon tree location or album membership changes but its content date and name do not
- **THEN** its canonical file path is unchanged

### Requirement: Deterministic collision disambiguation

The system SHALL disambiguate canonical paths that would otherwise collide by appending a short node identifier, and SHALL apply the same rule deterministically on every run.

#### Scenario: Two files share a name and date

- **WHEN** two media nodes resolve to the same canonical path
- **THEN** the system distinguishes them with a short node identifier suffix and keeps them distinct on subsequent runs

### Requirement: Deterministic fallback for missing content date

The system SHALL assign media with no content date a deterministic fallback path that does not collide with dated media and resolves identically on every run.

#### Scenario: Undated media stored deterministically

- **WHEN** a media node has no content date
- **THEN** it is stored under the fallback location for undated media and resolves to the same path on subsequent runs

### Requirement: Preserve original capture time

The system SHALL set each stored file's modification time to the media's content date.

#### Scenario: Modification time reflects capture

- **WHEN** a media file is downloaded
- **THEN** its filesystem modification time equals its content date

### Requirement: Organizational views as symlinks

The system SHALL generate views that present media grouped by Amazon tree path and by album, implemented as symbolic links to the canonical files so that no media bytes are duplicated.

#### Scenario: Views reference canonical files

- **WHEN** views are generated
- **THEN** each view entry is a symbolic link that resolves to the canonical file for that media node

### Requirement: Views are regenerable and self-pruning

The system SHALL rebuild views from recorded state so that re-running view generation removes entries that no longer apply and adds new ones, without modifying canonical media.

#### Scenario: Stale view entries removed

- **WHEN** a node leaves an album or tree location and views are regenerated
- **THEN** the corresponding old view link is removed and the canonical file is untouched

#### Scenario: New view entries added

- **WHEN** a node joins an album and views are regenerated
- **THEN** a corresponding view link is created

### Requirement: Views refreshed after synchronization

The system SHALL regenerate views at the end of every completed synchronization run, and SHALL also support regenerating them on demand without a synchronization.

#### Scenario: Views current after sync

- **WHEN** a synchronization run completes after album or tree changes
- **THEN** the views already reflect those changes without a separate command

#### Scenario: On-demand regeneration

- **WHEN** views are regenerated without a synchronization
- **THEN** the result is identical to regeneration at the end of a sync

### Requirement: Graceful degradation without symlink support

The system SHALL, where symbolic links are unavailable, avoid failing the run and SHALL inform the user that views could not be created.

#### Scenario: Symlinks unsupported

- **WHEN** the platform or filesystem does not support symbolic links
- **THEN** the system completes synchronization, reports that views were skipped, and leaves canonical media intact
