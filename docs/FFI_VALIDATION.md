# Validating Wysna's C Interoperability

- **Status:** The SQLite pilot and CSV import flow are implemented; a production profile has not been approved.

## Pilot workflow

`examples/ffi/sqlite/import_csv.wysna` imports measurements into a persistent local database. It validates columns, 64-bit ASCII integers, and bounds; uses a transaction; returns data or SQLite errors; and closes its resources. `import_reference.c` independently implements the same contract with the same C adapter and library. There is not yet a real user workload or a measured time saving; product validation remains open.

The input format requires the exact header `id,value` and accepts up to 100,000 rows and an input of at most 8 MiB. Both columns are non-negative decimal integers in `[0, INT64_MAX]`. The parser accepts LF, CRLF, and a final line without a newline. It rejects quotes, empty fields, signs, Unicode digits, NUL, and extra columns; this is not a general CSV parser. The identifier is a primary key, so duplicates in the batch or database abort the whole batch. The aggregate sum must also fit in int64; it is checked before commit, and SQLite overflow rolls back the import.

A valid import persists its rows and an `imports(row_count, input_bytes, imported_at)` journal entry in the same transaction. An invalid import preserves both the rows and journal that existed before the batch. Data and SQLite errors return exit status 1; usage or read errors return 2; success returns 0. `--version` prints the linked SQLite version.

## Ownership and adapter costs

The FFI carries scalars and `opaque` handles, not Wysna strings or buffers. The database path comes from `WYSNA_SQLITE_DB`. `adapter.c` owns fixed SQL statements, the connection, and prepared statements; this adapter is product code that must be maintained. `pilot_close` closes statements and the connection, and `pilot_dispose` frees the control block. Repeated close is valid; use after close returns `SQLITE_MISUSE`. Using a handle after destruction or destroying it twice remains an unsafe error that Wysna does not prevent.

The Wysna helper that acquires the resource routes every later exit through one cleanup point. Errors returned by `populate` are handled before leaving. This pilot does not require a new `defer/with` construct. External counters check that connections, control blocks, and statements are no longer live. `WYSNA_MEM_CHECK` checks only Wysna allocations on normal exit; `exit()` bypasses that check. Sanitizers and external counters are needed for this CLI, which returns process exit codes.

## Reproducing the checks

A C99 compiler, SQLite headers, and `libsqlite3` are required. Linux CI installs `libsqlite3-dev`; macOS uses the system SDK and library.

```sh
python3 tools/validate_sqlite_ffi.py --sanitize
python3 tools/validate_sqlite_import.py --sanitize --rows 1000 --samples 1
python3 tools/validate_sqlite_import.py --rows 10000 --samples 3 --report .wysna_cache/sqlite-import/optimized.json
python3 tools/measure_tokenizer.py
```

To run the importer from the repository root:

```sh
mkdir -p .wysna_cache/sqlite-import
cc -std=c99 -O2 -c examples/ffi/sqlite/adapter.c -o .wysna_cache/sqlite-import/adapter.o
./wysna build examples/ffi/sqlite/import_csv.wysna --link-lib .wysna_cache/sqlite-import/adapter.o --link-lib sqlite3 -o .wysna_cache/sqlite-import/import_csv
WYSNA_SQLITE_DB=.wysna_cache/sqlite-import/measurements.sqlite .wysna_cache/sqlite-import/import_csv measurements.csv
```

The runner tests C, AST, and HIR, then invokes Wysna Compiler directly without running the Wysna Seed checker over the input. An independent Python connection compares persisted rows rather than checking output alone. The test appends data in a second run and exercises 14 failures, including an error after an earlier valid row has been inserted. It requires rollback, the expected exit status, no live external resources, and no sanitizer output. Linux enables LSan; macOS does not support it. The system SQLite library is not rebuilt with sanitizers. The earlier `measurements.wysna` pilot retains C/AST/HIR checks for repeated close and use after close.

## Measurement and interpretation

Each JSON report records the compiler, platform, linked SQLite version, flags, commands, build time for each program, samples, binary size, and peak resident memory. The shared adapter build is excluded from each program's runtime; the Wysna Compiler bootstrap is reported separately. One warm-up run is discarded.

`tools/measure_process.py` runs exactly one child in a fresh measurement process, so `ru_maxrss` does not inherit peaks from previous runs. Darwin reports bytes and Linux reports KiB; both are converted to bytes. Time includes process startup, SQLite, and I/O, but excludes launching the Python helper. RSS and binary size depend on platform, flags, and SDK. These numbers describe the full workflow, not the isolated cost of the FFI.

Wysna provides typed errors and control flow, but this pilot still needs a manual adapter and has higher representation costs than C. It validates one concrete integration path; it does not establish a general performance advantage or production readiness.

## Application commands

Python builds and launches the native Wysna application. Data commands require an explicit
SQLite path; no data is sent to a service.

```sh
python3 tools/measurement_ingest.py build --output build/measurement-ingest
python3 tools/measurement_ingest.py --database measurements.sqlite check measurements.csv
python3 tools/measurement_ingest.py --database measurements.sqlite import measurements.csv
python3 tools/measurement_ingest.py --database measurements.sqlite status
```

`check` runs the insertion validation in a transaction, then rolls it back. It persists
neither rows nor journal entries; opening a new database can create its file and schema.
`status` reports row count, sum and import count. Duplicate imports fail rather than
silently deduplicating. `--json` returns structured command output, and
`--engine=seed|compiler` selects the compiler implementation; `--backend=c99` selects
the default output target. AST emission is restricted to the differential test harness.
Exported binaries have a build
manifest; the build cache includes sources, backend and C toolchain identity.

Native use: `WYSNA_SQLITE_DB=measurements.sqlite build/measurement-ingest [--check] file.csv`
or `--status`. On Windows use the shell's environment syntax and the `.exe` filename;
the SQLite pilot also requires C headers and a linkable library on that host.

The wrapper checks the byte cap before execution; the native program checks text length
after loading UTF-8. There is no streaming import, schema migration or record update.
The application is ready for a scoped trial; external adoption and productivity remain
unverified. Raw opaque ownership and panic/exit cleanup retain their documented limits.

The Windows CI job builds a standalone x64 pilot and uploads
`measurement-pilot-windows-x64`. The ZIP contains the executable, sample/error CSVs,
trial instructions, source/toolchain manifest, checksums and its automated smoke result.
SQLite and the Microsoft C runtime are linked statically; running this package requires
neither Python nor a C toolchain. The executable is experimental and unsigned.

`tools/package_measurement_pilot.py` builds the package from a pinned official SQLite
amalgamation, verifies its SHA3-256, and exercises the exported binary in paths containing
spaces. Its database oracle checks dry run, partial-batch rollback, overflow and journal
consistency. Automated CI evidence is distinct from a trial performed on a user's machine.
