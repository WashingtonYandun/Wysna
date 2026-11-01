# Changelog

## Unreleased

### Compiler architecture and migration

- Separates `--engine=seed|compiler` from `--backend=c99|llvm-experimental`.
  C99 remains the supported default; LLVM remains experimental. The Seed CLI's
  Compiler engine supports file-based `emit-c` and `build` for `native-core-v1`.
- Removes the public AST-to-C route and the mixed `--backend=hir|ast|selfhost`
  selectors. Use `--engine=seed|compiler`; use `wysna ast <file>` only to inspect
  syntax. AST-to-C emission is reserved for the differential test harness.
- Allows `WYSNA_CACHE_HOME` to relocate the native bootstrap cache for read-only
  source checkouts.

### Native Compiler

- Allows native SDK builds and runs to search explicit library directories with
  `--library-dir`, alongside `--library` and existing explicit link files.
- Rejects standard-library imports that escape their configured root or fall back
  to a project-controlled path; empty standard modules remain valid.

- Supports concrete parameterized classes with fields and ordinary methods, including
  multiple instantiations such as `Box[int]` and `Box[str]`. Constraints, inheritance,
  interfaces and independently generic methods remain explicit `W2013` profile limits.
- Supports environment arguments and variables and `std/time.unix_seconds()` in the
  native profile. An automation consumer can invoke a child process and propagate
  its exit status.
- Resolves the bundled standard library explicitly when the compiler bootstrap cache
  is relocated outside the source checkout.
- Preserves explicit `exit(code)` status with memory checking enabled; immediate exit
  does not unwind active frames or perform the normal zero-live-object check.

### Tooling and Interactive Use

- Adds `wysna project new NAME`, which creates a manifest, source entry point and
  test without replacing existing paths. `--output-format=json-v1` returns the new
  files as hashed artifacts.
- Makes `wysna repl` retain definitions and values between inputs, continue across
  open delimiters, recover from failed declarations, and recheck accepted history
  without replaying earlier effects or output.
- Adds `:help`, `:reload` and `:reset` to the REPL. `:reload` reinitializes imports
  in a fresh runtime when no session declarations exist; otherwise use `:reset`.
- Applies ordered, ranged `didChange` edits in the language server, resolves
  declarations as well as uses, and adds references across open modules. Rename
  is restricted to verified, non-exported symbols in the current document and
  refuses collisions or incomplete references. Signature help and semantic tokens
  remain unsupported.
- Renders human diagnostics with bounded source excerpts and precise spans, including
  an optional `data.help` hint. Terminal control sequences in diagnostic text are
  removed before printing.
- Points native compiler diagnostics at the offending argument, expression, member, or
  declaration for type, mutability, pattern and arity errors instead of the end of the
  enclosing construct.
- Preserves the `NetError` prelude type and supports native compilation of explicit
  `NetError` and `IoError` struct values.
- Reports `Wysna` as the language-server diagnostic source and directs users of an
  installed distribution to `wysna project test`.

### Security

- Adds `wysna run --deny=CAP,...` and `--allow=CAP,...`, a static capability gate
  over the conservative effect report. It rejects modeled violations and refuses
  incomplete analyses before interpretation (`W5006`). This is not an OS sandbox and
  does not cover native builds, `project run`, or trusted runtime/FFI behavior.

### Distribution and Development

- Retries bounded transient HTTP errors while downloading pinned components; every
  downloaded or cached component still requires its pinned SHA-256 checksum.

- Adds a `dev` extra installing pytest and the optional HTTP/service dependencies,
  and documents it in the README and contribution guide.
- Adds macOS Installer signing and `notarytool` notarization options to
  `tools/package_installers.py`. Notarization requires a signed package, submits it,
  staples the ticket and validates the result; the 0.5.0 release remains unsigned.
- Raises the native SDK compilation deadline from two to ten minutes so selfbuilds
  complete on slower CI runners without changing the application timeout contract.

### Verification

- Runs native compiler diagnostics on Ubuntu, macOS and Windows instead of only
  Linux, and adds language-server coverage to the compiler-contract CI jobs.
- Adds cross-platform product-contract CI coverage for the REPL, project scaffold,
  human diagnostics and tool results, plus installer lifecycle validation for
  `project new` and multiline REPL input.

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
