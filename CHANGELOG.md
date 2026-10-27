# Changelog

## 0.5.0 — 2026-10-27

### Language and Compilers

- Consolidates **Wysna** as the language identity and removes token aliases and
  internal prefixes inherited from experimental names.
- Adds newline and `;` statement separators. The rule covers LF, CRLF, and CR;
  newlines inside parentheses and brackets remain part of expressions.
- Unifies negative integer handling: the lexer emits a unary operator and the parser
  recognizes the `INT64_MIN` boundary.
- Aligns the self-hosted compiler's parser and semantic validator with the new
  separators and with newlines before `else`/`elif` and `else err` handlers.
- Adds a runtime regression covering statement boundaries, returns, branches, error
  handling, and multiline lists.

### Tooling and Distribution

- Adds `wysna explain`, a read-only effect and capability report over verified MIR.
  It uses a versioned model (`wysna.effects-model/1`) and report schema
  (`wysna.effect-report/1`), computes a fixed point over direct calls, and marks
  indirect calls, uncontrolled FFI and missing contracts as `unknown`.
- Adds a relocatable Seed distribution builder with a private CPython and pinned Zig
  toolchain for macOS arm64, Linux x64 and Windows x64. The installation records file
  checksums and component provenance; `wysna doctor` verifies the complete file index.
- Adds graphical EXE/PKG/DEB/RPM installer candidates, native lifecycle acceptance,
  and a JSON/HTTP/SQLite reference application built with the installed private toolchain.
- Keeps installed project test subprocesses isolated and free of installation writes.
- Reuses one validated LLVM provider per command and reduces runtime debug/unused
  section overhead while preserving program debugging and full qualification gates.

### Documentation and Scope

- Clarifies the roles of Wysna Seed and Wysna Compiler. Seed remains the reference
  AST/HIR/MIR pipeline; the self-hosted compiler uses its own checked-node/MIR/C99 pipeline and
  measured profile in the parity matrix.
- Records the audit findings that remain open in [docs/KNOWN_LIMITS.md](docs/KNOWN_LIMITS.md):
  `WVal` representation, CoW cost, checking of uninstantiated generic functions,
  benchmarks, and parser recovery.
- Preserves the existing C ABI in this version; its documented types and names remain
  part of the current contract.

### Verification

The official release page records the source commit, CI run, installer acceptance
reports and SHA-256 manifests for the published Windows, macOS and Linux packages.
Wysna 0.5.0 is pre-1.0; its installers are unsigned and the macOS package is not
notarized.
