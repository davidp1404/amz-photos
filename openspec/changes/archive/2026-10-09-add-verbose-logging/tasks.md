# Tasks

## 1. Logging module and level mapping

- [x] 1.1 Add `src/amz_download/log.py` exposing `configure(verbose)` that maps the flag count to a level (0 → WARNING, 1 → INFO, 2 or more → DEBUG), attaches a single `rich` stderr handler with `markup=False`, and is safe to call more than once. Verify unit tests assert the resulting logger level for 0, 1, 2, and 3 flags and that repeated calls leave exactly one handler attached.
- [x] 1.2 Add `tests/test_log.py` covering the level mapping, the idempotent handler attachment, and that no records are emitted at the default level. Verify `uv run pytest tests/test_log.py` passes.

## 2. CLI flag wiring

- [x] 2.1 Declare a shared `VerboseOption` alias (`int`, `count=True`, `--verbose`/`-v`) and add it plus a leading `log.configure(verbose)` call to all six commands (`login`, `check`, `sync`, `views`, `status`, `repair`). Verify `--help` for each of the six commands lists the flag.
- [x] 2.2 Verify the per-command placement from design D3: `amz-download sync -v` and `amz-download sync -vv` parse and run, and the flag still works when placed before the subcommand. Verify a `tests/test_cli.py` case invokes each form and exits as expected.
- [x] 2.3 Verify stream separation at the CLI: a test capturing stdout and stderr separately asserts stdout holds the same result lines as an unflagged run and every diagnostic lands on stderr. Verify the test fails if a diagnostic is ever written to stdout.

## 3. Client diagnostics

- [x] 3.1 Add a DEBUG record per request attempt in `_request` covering method, path, parameters, and response status, and an INFO record when a retryable failure triggers another attempt with its attempt number and delay. Verify `caplog` tests against `FakeAmazon` cover a plain request and a fault-injected 503 that then succeeds, asserting both the failed and succeeding attempts appear.
- [x] 3.2 Add an INFO record when a download starts, naming the node id and target, and a DEBUG record with the byte count and computed content hash when it finishes. Verify a `caplog` test asserts both records and that the hash matches the bytes served by the fake.
- [x] 3.3 Add an INFO record naming each listing as it starts and a DEBUG record per pagination window with its offset and item count. Verify a test that forces a `page_size` of at least two pages shows a window record per page.
- [x] 3.4 Verify diagnostics never carry credentials: a test running a full client exchange at the DEBUG level scans captured records for every cookie value, the `x-amzn-sessionid` value, and the contents of a credentials file. Verify no captured record contains any of them.
- [x] 3.5 Verify records survive `rich` markup parsing: with `markup=False` configured, a node or album name containing brackets appears in a record exactly as written. Verify a test with such a name asserts the bracket characters are preserved.

## 4. Sync engine diagnostics

- [x] 4.1 Add INFO records marking the phases of a run (`listing`, `reconciled`, and the planned action counts by type). Verify a `caplog` test asserts the phase records appear in order with the expected counts.
- [x] 4.2 Add an INFO record in `_apply_action` naming the action, node id, reason, and canonical path, emitted while the run is still in flight. Verify a test asserts exactly one such record per remote node and that each names its canonical path.
- [x] 4.3 Add a DEBUG record in `execute_plan` reporting the worker pool size. Verify a test asserts it appears at the DEBUG level and not at the INFO level.
- [x] 4.4 Verify the requirement that verbosity does not change what the tool does: run the same sync at each of the three levels and compare the resulting files, their bytes, the recorded state, and the exit code. Verify the test asserts all three runs are identical.

## 5. State and views diagnostics

- [x] 5.1 Add a DEBUG record on node upsert in `state.py`, naming the node id. Verify a `caplog` test at DEBUG asserts one record per upsert and none at INFO.
- [x] 5.2 Add DEBUG records in `views.generate_views` for each link created, updated, or removed. Verify a test that regenerates views after a change asserts a record per changed link.

## 6. Documentation and integration

- [x] 6.1 Document the `-v` and `-vv` levels in `README.md`, stating which stream each kind of output goes to and what each level reports. Verify every documented command runs as written and produces the output the documentation claims.
- [x] 6.2 Add an end-to-end test that runs a `-v` sync against `FakeAmazon` and asserts the emitted narrative covers the listing, each action, and the summary, in that order, with no records at all when the flag is absent. Verify the test asserts the ordering rather than only the presence of lines.
- [x] 6.3 Run the full suite and `openspec validate add-verbose-logging --strict`. Verify both pass and the change's `openspec status` shows every required artifact done.
