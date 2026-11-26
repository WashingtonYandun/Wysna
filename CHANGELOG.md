# Changelog

## 0.9.5 — 2026-11-24

### Agent-First Waves 1 & 2 (Protocol, Graph, Workspace Patch & MCP)
- Resolves `wysna graph`, `wysna inspect`, `wysna patch`, and MCP lookups by canonical declaration identity, returning `[W1005]` candidates on ambiguity and surfacing `call_resolution` plus `analysis_boundaries` (`virtual-dispatch`, `indirect-call`, `c-ffi`).
- Enables multi-error recovery (up to 100 unique diagnostics per pass with `error_type` cascade suppression) in the self-hosted compiler (`check`, `project check`, `patch`, `mcp`) and emits structured `data` facts + `related` declaration spans for `W1002`, `W2001`, `W2002`, `W2003`, `W2004`, `W2005`, and `W3001`, backed by strict `schemas/wysna-tool-result.v1.schema.json`.
- Adds `--max-bytes` bounded context with resumable `omitted[].ref` handles, batch inspection (`--symbols`, repeatable `--symbol`, MCP `symbols`), and full Seed/Selfhost `wysna.inspect/1` schema parity.
- Adds atomic multi-file `workspace patch` across Seed, Selfhost, and MCP supporting `source`, `symbol` + `replacement`, `hunks`, `create: true`, and `delete: true` with `--expect-plan-hash`, virtual overlay confinement, and automatic rollback on validation/publication failure.
- Expands MCP to 13 tools by adding read-only `wysna_project_check` and opt-in `wysna_project_test` (gated behind `--allow-exec`, wired via `WYSNA_SDK_DRIVER` and `--diag-out` in `driver.c`).
- Adds checked architecture contracts (`#196`): static role attributes on top-level classes, declarative `wysna.toml` policies enforced in `project check`, `workspace patch`, and MCP with structured `W6101`/`W6102` diagnostics and `wysna graph --role` queries.

### Runtime, JSON, MIR & Compiler Scalability
- Accelerates C99 runtime JSON serialization/extraction, single-thread ARC fast paths, ABI hotpaths, small-struct pooling, and 2-buffer stable sort.
- Improves compiler scaling across module/import reuse, path normalization, signature indexing, dead-function elimination, MIR read-clone elision, hoisted/borrowed `for`-loops, and lazy diagnostic span hashing in Selfhost.

## 0.9.0 — 2026-11-22

### Arquitectura híbrida y tooling nativo

- Mantiene cuatro motores como oráculo del lenguaje hasta v1.0 y delega `project`, `packages`, `mcp`, `lsp` y `schema` a Selfhost. `packages` es exclusivamente nativo; los proyectos WASI/Browser conservan Seed.
- Añade build dev incremental por unidad con caché SHA-256, fuzzing diferencial multiarchivo, sandbox nativo `W5006` y lints nativos `W6001`/`W6002`.
- Añade paquetes USTAR v1 fijados por SHA-256, restauración offline, 19 regresiones de seguridad y el flujo `OPS-A01`; el SDK instalado comprueba restauración y ejecución con lock congelado.

### Cambios del lenguaje que requieren migración

- Retira `new Class(...)`, el opcional vacío `none` y los comodines `* =>`/`else =>`: usar `Class(...)`, `None` y `_ =>`. Los patrones de tupla requieren paréntesis.
- Formaliza capturas de closures como snapshots inmutables con diagnóstico accionable `W3001` al intentar mutarlas.
- Añade patrones alternativos `|`, rangos Int64 `..`/`..=`, patrones de struct anidados y advertencia de ramas inalcanzables `W6003`.
- Añade `@deprecated("mensaje")` y `@must_use` en funciones superiores, con advertencias `W6004`/`W6005` en CLI JSON y LSP. Los esquemas `struct` permiten anotaciones propias como metadatos tipados, sin ejecutar código durante la compilación.
- Expone los atributos en `api`, `inspect` y `schema`; W2003 informa familias pendientes de patrones como datos JSON. Corrige calificación de patrones genéricos y bindings de patrones que ocultan globals.
- Centraliza versión, comandos y guías de agentes; aísla diagnósticos por hilo y elimina intrínsecos basados en números de línea.
- Rechazo explícito de locks obsoletos y regeneración controlada de `wysna.lock`.

## 0.8.0 — 2026-11-20

### Native SDK Autonomy, Parameterized Interfaces & 11/11 MCP
- Adds parameterized interface declarations (`interface Foo[T]`, constrained `interface Foo[T: Show]`) and parameterized `implements` clauses across Selfhost (`native-core-v1`), supporting cross-module generic interfaces, generic call unification against `Collection[T]`, inherited generic implementations, and checked MIR virtual dispatch parity with Seed.
- Completes full Python-free Agent-First and project autonomy in `selfhost/` (33 modules, 15,795 bundled lines) and `packaging/native/driver.c`: adds native `wysna schema` (`--all`, `--function`, `--emit-codecs`, Draft 2020-12 contract bounds), `wysna fix` (`W3001`/`W1001`, `--apply`, `--include-maybe`, non-overlapping byte-span verification), `wysna skills`, `wysna capabilities`, native `wysna api` (`wysna.api-index/1`, stdlib and `--manifest` project symbols), native `wysna.toml` v1 `wysna project check|build|run|test` (including explicit C FFI flags `--include-dir`/`--link`/`--library`), and 11/11 static tools in the stdio JSON-RPC 2.0 MCP server (`wysna mcp --root` with canonical path confinement including `wysna_api`).

### Compiler Performance, Multi-OS LLVM CI & Dogfooding
- Tightens MIR temporary-scope cleanup around `if` conditions, `let` initializers, non-tail expression statements, `and`/`or` RHS branches, and `for` direct ranges in both `src/mir/Lowering.py` and `selfhost/src/{08_mir_lowering,08_mir_collections}.wysna`, eliminating quadratic CoW buffer detaches in loops (`O(N^2)` -> `O(N)`, keeping 33-module Stage-2 `--check` at ~5.51s on Apple Silicon).
- Expands `llvm-smoke` CI in `.github/workflows/ci.yml` across both `ubuntu-latest` and `macos-latest` (`fail-fast: false`), and records verified Darwin arm64 C99 vs LLVM (`release` profile) benchmark measurements in `docs/LLVM_EXPERIMENTAL.md`.
- Ships `examples/wysofia-ops/`: a real operational JSON & SQLite audit CLI built with contracts (`requires`/`ensures`), `from ... use`, `T ! E`, and C FFI, verified end-to-end with the Python-free native SDK.

## 0.7.0 — 2026-11-17

### Runtime, ABI & Concurrency
- Unifies `WVal == WVal64` (`8`-byte NaN-boxed representation, Runtime ABI v3) across C99 (`runtime/`), Seed AST/HIR/MIR, Selfhost Stage-2, MIR Exchange (`runtime_abi_version: 3`), and LLVM (`i64` register adapters), with `<=6B` inline Small String Optimization (SSO) and $O(1)$ `WStringBuf.hash_cache`.
- Adds lock-free atomic ARC, deferred Bacon-Rajan cycle collection (`WYSNA_COLLECT_CYCLES=1`), OS-thread pools for `std/algorithms.parallel_*` and `std/async.spawn`, and monotonic cooperative timers.
- Replaces single-shot `async fn` threading with stackless CPS state-machine lowering (`src/mir/AsyncLowering.py`, Seed C99, LLVM, and `selfhost/src/08_mir_c99.wysna`) backed by a cooperative scheduler, deferred lazy start, fixed `await f()?` / `await f() else err` precedence, and unboxed `wysna_range_count_i64` / `wysna_div_i64_at` / `wysna_div_f64_at` / `wysna_mod_i64_at` runtime helpers plus idempotent MIR scalar/store/block passes.
- Adds compiled C99 runtime capability sandbox guards (`--sandbox` with optional `--allow`/`--deny` -> `#define WYSNA_DENY_CAP_*`, failing with `[W5006]`) alongside `CompilationSession` interface-digest incremental early cutoff.

### Selfhost Parity, Agent-First CLI & Stage-3 Native LSP
- Adds nested block comments (`/* /* ... */ */`), ChaCha20-Poly1305 AEAD (`std/crypto`), Unicode 16.0.0 UAX #15 normalization (`std/text`), `std/time` / `std/io` / `std/environment` expansions, `ClassError` in `T ! E`, and hashable `tuple` dictionary keys across engines.
- Closes Selfhost OOP generics (`class Box[T: Show]`, `transform[R: Show]`, `ChildBox[T: Show] extends Box[T]`) and `from ... use` selective/aliased imports (`interface Foo[T]` remains bounded with `W2013` in Selfhost).
- Ships Python-free native Agent-First tooling and editor services directly inside `selfhost/` and `packaging/native/driver.c`: `check`/`fmt --output-format=json-v1`, multi-file `test`, semantic `graph` (`--rel impact`), `inspect` (`--with-deps`), optimistic hash-guarded `patch` (`W5005`), `explain`, and Stage-3 native LSP 3.17 (`wysna lsp` via `selfhost/src/09_lsp.wysna`).

### Release Readiness, Tooling & Application Pilots
- Unwraps present optionals inside `else` expressions across runtime evaluation and strengthens hash-verified, file-locked atomic CLI/MCP writes (`src/tooling/SourceWrite.py`).
- Adds borrowed `buffer.decode_utf8` views, sandboxed exclusive file creation (`files.create_exclusive`), atomic temporary download publish, and installed wheel/libcurl/SQLite transactional retry validation.
- Shares pure quote validation across native CLI and WebAssembly builds (`examples/shared-browser-app/`), backed by SQLite `POST/GET /quotes` persistence and end-to-end UI reload telemetry.
- Consolidates conformance execution into `src/run_conformance.py`, adds attributable `wysna.ai-eval/2` metrics plus paired A/B cohort evaluation and partial-persistence rollback detection (`bench/ai_eval/ab_cohort.py`), and generalizes tag-driven installer release publishing (`.github/workflows/publish-release.yml`).

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
