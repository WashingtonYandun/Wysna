# Reusable SQLite FFI Package (`examples/packages/sqlite`)

Provides a single reusable C bridge (`native/sqlite_bridge.c`) and a pure-Wysna
typesafe API (`src/sqlite.wysna`) with parameterized queries, transactions
(`begin`, `commit`, `rollback`), and automatic `std/resources` handle tracking.

Users consuming `[dependencies.sqlite]` write zero C code.

## Verify & Run
```sh
./wysna build examples/packages/sqlite/src/main.wysna \
  --link-lib examples/packages/sqlite/native/sqlite_bridge.c \
  --link-lib sqlite3 \
  -o /tmp/wysna_sqlite_demo && /tmp/wysna_sqlite_demo
```
