# Spec Delta

## Purpose

Acquire, store, and validate an Amazon Photos web session so that synchronization can run unattended and fail clearly when the session is no longer usable.

## ADDED Requirements

### Requirement: Supply session from a cookie file

The system SHALL accept an Amazon Photos web session as the set of required cookies supplied by the user through a cookie file, as the baseline authentication method.

#### Scenario: Valid cookies accepted

- **WHEN** the user provides a cookie file containing the required Amazon session cookies
- **THEN** the system stores the session and reports success

#### Scenario: Missing required cookie rejected

- **WHEN** the supplied cookie file lacks one or more required cookies
- **THEN** the system refuses to store the session and names the missing cookies without printing any cookie values

### Requirement: Regional cookie naming accepted

The system SHALL accept the regional variants of the required cookies (such as the per-TLD `at` and `ubid` cookie names) in addition to the default names.

#### Scenario: Regional account cookies accepted

- **WHEN** the user supplies the regional cookie names for their Amazon domain
- **THEN** the system stores the session and reports success

### Requirement: Extract session from a local Firefox profile

The system SHALL provide a convenience path that reads the required Amazon cookies from a local Firefox profile, without requiring the user to copy cookie values manually.

#### Scenario: Cookies found in Firefox profile

- **WHEN** the local Firefox profile contains the required Amazon cookies and the user requests Firefox extraction
- **THEN** the system stores the session and reports success

#### Scenario: Firefox profile unavailable or lacks cookies

- **WHEN** no Firefox profile is found or it contains none of the required Amazon cookies
- **THEN** the system reports the reason and instructs the user to supply cookies manually

### Requirement: Session stored with owner-only permissions

The system SHALL persist the captured session to a per-user configuration location and restrict that file to owner-only access.

#### Scenario: Credentials file permissions

- **WHEN** a session has been stored
- **THEN** the credentials file is readable and writable only by its owner

### Requirement: Session validity checked before use

The system SHALL validate the stored session against Amazon Photos before performing synchronization work, and SHALL treat an expired or rejected session as a distinct, actionable failure.

#### Scenario: Expired session blocks sync

- **WHEN** the stored session is expired or rejected by Amazon Photos
- **THEN** the system aborts before modifying the local library, reports that re-authentication is required, and exits non-zero

#### Scenario: Valid session proceeds

- **WHEN** the stored session is accepted by Amazon Photos
- **THEN** the system proceeds with synchronization

### Requirement: Non-interactive synchronization

The synchronization command SHALL NOT prompt for input; interactive authentication SHALL be confined to the authentication command.

#### Scenario: Sync without a stored session

- **WHEN** synchronization runs with no stored session
- **THEN** the system fails with an instruction to authenticate first and does not prompt for credentials

### Requirement: Secrets kept out of output

The system SHALL NOT write cookie or token values to logs, standard output, or error messages.

#### Scenario: Cookie values never echoed

- **WHEN** authentication succeeds, fails, or is inspected
- **THEN** no cookie value appears in any log or message
