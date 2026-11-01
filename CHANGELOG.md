# Changelog

## 0.6.0 — 2026-11-01

### Agent-First Architecture & Tooling

- Adds `wysna schema`: extracts JSON Schema Draft 2020-12 `$defs` and agent tool definitions with verified MIR effect and capability contracts.
- Adds `wysna fix`: automated zero-turn compiler self-healing applying multi-error repairs guarded by SHA-256 byte-level source hashes.
- Adds `wysna mcp`: in-memory Model Context Protocol daemon over stdio, eliminating cold Python startup overhead (< 10 ms per check).
- Upgrades diagnostics to `json-v1` multi-error envelopes with structured facts (`data`), secondary related spans (`related`), and precise UTF-8 byte offsets (`primary_span`).

### Contracts & Deterministic Execution

- Adds first-class Design-by-Contract (`requires` / `ensures`) in function signatures and struct fields, enforced dynamically via `[W4005]` contract assertions and lowered across both Seed and native Selfhost compilers.
- Adds contextual `result` identifier inside `ensures` postcondition clauses.
- Adds deterministic execution quotas: `--max-steps=<N>` instruction bounds and `--max-heap-mb=<N>` heap allocation budgets with `[W4004]` fail-stop aborts across interpreter, native C99, and LLVM backends.
- Adds effect cassette recording and offline replay (`--record-trace` / `--replay-trace`) under the versioned `wysna.effect.trace/1` format, guaranteeing bit-identical replays without network or filesystem side-effects.

### Concurrency & Memory Specializations

- Adds multi-core structured deterministic concurrency in `std/algorithms` (`parallel_map`, `parallel_batch`, `parallel_filter`, `parallel_reduce`) and `std/http` (`batch_get`) preserving strict input index ordering over thread-safe runtime ABIs.
- Adds scalar list container specialization (`W03`): unboxed integer/boolean/float buffers with $O(1)$ release and `memcpy` copy-on-write detachment.
- Adds selective symbol imports: `from "<path>" use <symbol>, <symbol>` and `import "<path>" as <alias>` alongside canonical namespace imports.

## 0.5.0 — 2026-10-27

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
