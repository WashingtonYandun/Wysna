# Bounded measurement synchronization

This application fetches a bounded JSON batch and imports it into SQLite atomically.
Its two commands separate network acquisition from local validation and persistence.
The accepted input is an array of 1..1000 objects with positive `int64` IDs and
non-negative `int64` values, at most 65,536 UTF-8 bytes. Duplicate IDs, invalid rows
and overflow roll back the complete batch and its import journal.

`ingest.wysna` reuses `../ffi/sqlite/adapter.c` and `adapter.h`. SQLite is an explicit
C dependency; the acceptance harness downloads the already-pinned official SQLite
amalgamation and verifies its SHA3-256 before compiling it with the installed Zig.
It does not need a host C compiler or an installed SQLite development package.

`fetch.wysna` uses the standard HTTP client, a five-second request deadline and a
65,536-byte response limit. It accepts status 200 and bounded JSON arrays before
writing the destination file. It requires declared libcurl development inputs;
HTTP acceptance currently covers Linux and macOS. The local JSON/SQLite command
is accepted on Windows as well. A write failure may leave a partial destination
file; use a new download destination and ingest it only after a successful fetch.

The workflow after building the two commands is:

```sh
measurement-fetch https://example.com/measurements measurements.json
WYSNA_SQLITE_DB=measurements.sqlite measurement-sync measurements.json
```

The synchronizer prints a JSON summary with imported and persisted counts, total,
import journal count and the adapter's live resource count. The database path is
explicit through `WYSNA_SQLITE_DB`; without it, the adapter uses an in-memory database.
Use a dedicated database for the application.

Validate from a complete installed Wysna distribution:

```sh
python tools/validate_installed_application.py \
  --installation /path/to/installed/wysna \
  --report application.json --http
```

The validator copies inputs outside the checkout, uses the installed compiler and
private C toolchain, verifies persisted rows/journal with Python's independent
SQLite reader, tests errors and rollback, and uses a local HTTP fixture. Omit
`--http` to qualify only ingestion on a host without libcurl development inputs.
It also verifies that `explain` retains unknown native FFI effects. This is trusted
native automation with the user's OS authority, not an untrusted-code sandbox.
