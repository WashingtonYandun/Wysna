# Known Limits and Deferred Decisions

This file records what Wysna does **not** do, why, and what it would take to change it. It
tracks deferred decisions separately from defects. Items marked *open decision* change
the language surface and need an explicit decision before implementation.

The interactive REPL rechecks the accepted session history on every input, so long
sessions can become slower. Open delimiters continue onto subsequent lines; other
incomplete expressions are reported immediately. A failed input removes bindings
it introduced, but completed mutations and external effects
such as output or file writes are not rolled back. Imported modules cannot be changed
mid-session; `:reload` loads imports into a fresh runtime when no session declarations
exist. Otherwise, use `:reset` and import again. Module initialization runs again.

---

## 1. Closures capture immutable snapshots

**What happens.** A lambda captures by value at creation time. The captured names are
rewritten into a synthetic environment struct and the lambda receives it as a hidden
first parameter (`checker/ClosureConversion.py`). Neither reading a later write nor
writing through the capture is possible:

```wysna
let mut n = 0
let f = |v: int| -> unit { let x = n; print(x + v) }
n = 10
f(1)          # prints 1, not 11 -- `n` was snapshotted at 0
```

```wysna
let mut xs = [1]
let f = |v: int| -> unit { xs.append(v) }
                          # W3001: Cannot call mutating method 'append' on a captured
                          # variable: closures capture values as immutable snapshots
```

Collections and structs are captured as copy-on-write snapshots, so capturing is cheap
and cannot alias the enclosing frame. Classes keep identity and a strong reference, so
mutating a captured *object* is visible to everyone holding it.

**Why it is this way.** A snapshot needs no shared mutable state, which is what makes
the single-threaded ARC model and the copy-on-write guarantees in SPEC §8 simple to
reason about.

**Cost.** The stateful-closure idiom is not expressible:

```wysna
fn make_counter() -> fn() -> int {
    # There is no way to write this. The usual workaround is a class with a private
    # field, or threading an explicit accumulator through every call.
}
```

**Design decision (final for v1.0).** Immutable snapshot capture is the intentional,
permanent semantics of Wysna closures. Keeping closures free of shared mutable cell
aliasing preserves Wysna's Copy-on-Write value semantics (SPEC §8) and deterministic
single-threaded ARC without hidden heap indirections. When shared mutable state is
explicitly needed across calls, declare a `class` (reference type) or return the updated
value; attempting to mutate a captured value binding produces `[W3001]` with an
actionable hint.

*Closed design decision (v1.0 — Option 2A).*

---

## 2. Error unions cover scalar, ADT, class, and generic value channels

**Behavior.** `T ! E1 | E2` allows `?` to propagate multiple error types and `match` to
exhaustively inspect the value bound by `else err`. A source error set can propagate when
it is a subset of the destination function's declared error set. Payloads are not
converted.

Supported alternatives across Seed, LLVM, and native Selfhost are `int`, `float`, `bool`, `str`, `struct`
(including concrete generic `Struct[T]`), `enum` (including concrete generic `Enum[T]`),
and `class` types, including concrete parameterized classes. Patterns use `Type(error)` (including generic `Type[T]` payloads) and bind the
full payload. Distinct specializations such as `Failure[int]` and `Failure[str]` remain
distinct alternatives for exhaustiveness, propagation, and runtime discrimination.
A generic substitution that leaves fewer than two distinct alternatives is rejected
with `W2001` before MIR lowering. Duplicate alternatives are canonicalized; a union must contain at least
two distinct alternatives.

**Limit.** Collections (`list`, `dict`), tuples, function values, built-in `Option`/`Result`
channels, and unresolved generic type parameters remain unsupported as error union
alternatives because `WVal` does not box arbitrary generic collection type arguments for
runtime tag discrimination.

---

## 3. Wysna Compiler support is bounded by the tested corpus

Wysna Compiler is a self-hosted compiler with a narrower supported profile than Wysna
Seed. Its measured coverage is maintained in the [engine parity matrix](ENGINE_PARITY_MATRIX.md); passing
the listed fixtures does not guarantee equivalent behaviour for every Wysna program.

Wysna Compiler has modular source files under `selfhost/src/`, bundled into
`selfhost/compiler.wysna`. It parses and checks source using its own implementation and
uses one checked-node → verified MIR → C99 pipeline under `native-core-v1`. Parameterized
classes and interfaces with constrained generics, independently generic methods (`Box[T].transform[R]`), and
parameterized class inheritance
are supported in both Seed and Selfhost (`native-core-v1`). The native SDK driver
(`packaging/native/driver.c`) supports `check` (including `--output-format=json-v1`),
`fmt` (`--write`, `--output-format=json-v1`), `test`, `graph`, `inspect`, `patch` (`W5005`),
`explain`, `schema` (`--emit-codecs`), `fix` (`--apply`, `--include-maybe`), `skills`, `capabilities`,
`api` (`wysna.api-index/1`), `project new|lock|check|build|run|test` (`wysna.toml` v1 + `wysna.lock`), stdio MCP (`wysna mcp --root`, 11 static tools), and Stage-3 native LSP (`wysna lsp`, backed by `selfhost/src/09_lsp.wysna`)
without Python. Unresolved generic nominal values, library-mode exports, and WASM targets remain outside `native-core-v1`; see [MIR design](MIR_DESIGN.md). Compiler does not share Seed's compilation session or type arena, though Seed compiles use an interface-digest incremental module cache. Deterministic bootstrap and corpus tests are evidence for their measured inputs, not universal semantic equivalence.

The [native SDK](NATIVE_SDK.md) packages the native engine, runtime, stdlib and pinned C toolchain. Its commands run without Python. The SDK is a portable development artifact; platform acceptance, signing and release publication are distinct gates. HTTP and other external C dependencies are not included in the offline core.

Installed wheels contain Wysna Seed, its C runtime header and the standard library;
Python remains a prerequisite. Wysna Compiler bootstrap, `wysna test` and `wysna bench`
require a source checkout. The [standalone Seed distribution](SEED_DISTRIBUTION.md)
builds a portable installation with a private CPython and Zig as the C99 toolchain for
macOS arm64, Linux x64 and Windows x64; it needs no virtual environment or host C SDK.
Its launcher verifies the required components before importing Seed, and
`wysna doctor` verifies the complete installed file index. External FFI libraries are
not included. CI validates the Windows, macOS and Linux installer lifecycles on their
respective hosts, including installation and removal. The published 0.7.0 installers
are unsigned; CI acceptance does not establish manual acceptance on every machine.
Missing Wysna Compiler sources produce `W2013` before any bootstrap-cache write. Wysna
Seed native builds require a host C99 toolchain; an unavailable or invalid compiler command produces
`W5001` with stage `toolchain`, rather than an internal compiler error. This checks
command availability, not the completeness of a host SDK or external FFI libraries.


## 4. Native runtime panic locations have a defined supported scope

**What happens.**

Native AST C99, HIR C99, and Wysna Compiler builds now report exact source locations for the
operational panic cases `W4001` (division/modulo by zero) and `W4002` (invalid list,
string, or dictionary reads; invalid list index writes; and `list.pop()` on an empty
list). The location is attached to the runtime operation itself, so nested expressions
do not accidentally report the location of a later operand.

The runtime still falls back to `[Wxxxx] RuntimeError: ...` for calls made without a Wysna
source span and for other runtime failures, especially internal `W4003` memory/type
invariant failures. This is deliberate: those failures are not reliably attributable to
one user expression. The interpreter continues to report its existing source spans.

---

## 5. Strong reference cycles leak unless detected explicitly

**What happens.** An acyclic heap cannot be guaranteed, because a value container can
hold a class reference and the class can hold that same container:

```w
class Node { public name: str, public children: list[Node] ... }
let n = Node("root")
n.children.append(n)           # value storage now reaches back to the object
```

Nothing is reclaimed. `WYSNA_MEM_CHECK=1` reports it at exit:

```
[W4003] RuntimeError: Memory leak detected at exit (alloc=3, free=0, live=3)
```

Without that environment variable the program exits 0 in silence.

**Why it is this way.** ARC reclaims acyclic structures deterministically without stop-the-world
pauses. The runtime provides an internal trial-deletion collector (`wysna_collect_cycles()`,
triggered under heap quota pressure or when `WYSNA_COLLECT_CYCLES=1` at exit cleanup),
while `Weak[C]` remains recommended for deterministic immediate release in normal execution.

**Policy.** `WYSNA_MEM_CHECK` is opt-in and checks logical runtime allocation counters during
normal cleanup. An explicit Wysna `exit(code)` terminates with active frames and skips
the zero-live-object check; it preserves the requested exit status. It does not unwind
those frames. The check does not collect cycles, run on panic-driven `exit()`, or replace
ASan/LSan byte-level checks in development and CI. Keeping it opt-in avoids changing
stderr or exit status for production programs that intentionally retain process-lifetime
objects.

**Cost.** `Weak[C]` is the sanctioned escape hatch and works, but nothing forces its use
and a strong cycle is silent unless the memory check or an external leak detector is on.

**Decision.** Keep `WYSNA_MEM_CHECK` explicitly opt-in in every execution mode. Runtime
defaults must not change stderr or exit status based on build mode. Native runtime diagnostics remain opt-in through `WYSNA_MEM_CHECK`.

---

## 6. Structured Deterministic Concurrency vs. Arbitrary Multithreading

The runtime provides bounded, index-preserving multi-core worker dispatch for data-parallel
algorithms (`std/algorithms.parallel_map`, `parallel_batch`, `parallel_filter`, `parallel_reduce`)
and batch I/O (`std/http.batch_get`), protected by internal runtime host and quota locks.
Arbitrary preemptive threads with shared mutable memory remain out of scope to preserve
deterministic single-threaded ARC lifetime guarantees and avoid data races.

**Decision: closed for the current release line.** Maintain structured deterministic
concurrency over CoW values and worker pools; do not introduce arbitrary preemptive
threads with shared mutable state into the core language model.

---

## 7. Smaller deferrals

| Item | State |
| :--- | :--- |
| Chained assignment | HIR uses `HirAssignment`; the value is preserved and each index is evaluated once. The artificial depth limit was removed. |
| Parser error recovery | Check and LSP recover up to 100 syntax diagnostics. JSON preserves the first error and adds `diagnostics`; recovered programs never execute. |
| `wysna fmt` coverage | The gate checks idempotence and AST preservation across fixtures, runtime programs, recursive examples, the standard library, and self-hosted sources. It also runs generated trees before and after formatting. |
| LSP | Seed y el SDK nativo admiten ediciones incrementales UTF‑16, diagnósticos, navegación, autocompletado, formato, tokens semánticos, información de firma, correcciones rápidas y renombrado entre archivos. Seed incluye además avisos `W6001`/`W6002`. |

| Capability policy | Capability policies are enforced across BOTH interpreted Seed (`wysna run --allow=...` / `--deny=...`, `W5006` static pre-execution gate) AND Seed-compiled C99 binaries (`wysna build --sandbox` / `wysna emit-c --sandbox` with optional `--allow=...` or `--deny=...`, compiling `#define WYSNA_DENY_CAP_*` compile-time guards directly into the C99 runtime ABI and tripping `[W5006]` on disallowed host operations). This provides in-process capability enforcement; it is not an OS kernel/container sandbox (seccomp/pledge/chroot) for external C libraries or raw FFI (`--link-lib` cannot be combined with `--sandbox`). |
| Dictionary keys | `int`, `str`, `bool`, `enum`, and hashable `tuple` composites; `float`, collections, functions, classes, and composites containing non-hashable elements are rejected with `W2010`. |
| Standard library scope | Includes text (with Unicode 16.0.0 UAX #15 `NFC`/`NFD`), paths, filesystem, file/buffer I/O, stdin/stdout streams, resources, algorithms (incl. parallel pools), UTF-8, JSON, CSV (`std/csv`), `std/crypto` (SHA-256/HMAC + ChaCha20-Poly1305 AEAD + CSPRNG), `std/time` (calendar/ISO-8601), `std/environment`, `std/async` (timers + OS-thread `spawn`), process execution (`run` and `capture`), and HTTP client; see [standard library](STDLIB.md) for contracts and platform limits. |
| Frozen experimental surfaces | `--backend=llvm-experimental` (and `LLVM SDK`) and `wysna service serve` are **FROZEN (`FREEZE`, ADR-00)**: preserved as bounded experimental verification/harness paths without planned expansion to Windows/Wasm LLVM targets or custom HTTP server frameworks. |

El renombrado nativo cubre funciones de nivel superior dentro de `rootUri` o
`workspaceFolders`, incluidos archivos cerrados e importaciones selectivas.
Mantiene los alias explícitos, comprueba las referencias con el compilador y
valida las ediciones en una copia de trabajo antes de devolverlas al editor.
Las correcciones rápidas llevan la versión del documento y se ofrecen para
la revisión del texto que produjo el diagnóstico.

The CLI has POSIX and Windows adapters for cache locks, compiler processes, executable
names and RSS telemetry. The Windows smoke job must pass on that host before support
is announced. A successful macOS run does not establish Windows compatibility.

The legacy `NetError` prelude type remains available for compatibility. Its `message`
field and the corresponding `IoError` field are strings; both support checked
record literals such as `NetError { message: "offline" }`.

Class cycles format finitely as `<cycle>` but still require Weak or explicit breaking
to be reclaimed. `std/resources` does not make raw opaque aliases safe or unwind panic.

Standard File/Bytes owners now release host storage at last reference; they do not
extend that guarantee to arbitrary FFI pointers or strong cycles. IO remains
synchronous. Bounded JSON is available; task isolation, cancellation and nonblocking
networking require the acceptance contracts in [Service foundations](SERVICE_FOUNDATIONS.md).

---

## 8. Audit Status and Open Scope in 0.8.0

Closed items through `0.8.0` vs remaining open architectural work:

| Finding | Current State | Remaining work |
| :--- | :--- | :--- |
| W01 · Compiler architecture | Seed supports nested `/* ... */` comments, `0x`/`0b` ints, `"""..."""`, `f"..."` interpolation, `+=`/`-=`/`*=`, `Struct { ..base }`, shorthand `(T1, T2)` tuple types, `.0`/`.1` tuple indexing, `let (a, b) = pair` destructuring, `.contains()`/`.is_empty()`/`x in container`, `for k, v in dict` / `d.items()`, bidirectional empty container inference, independent generic class methods (`Box[T].transform[R]`), parameterized interfaces, class/generic error unions, tuple dict keys, `wysna fix` machine repairs (`W3001`, `W3004`, `W2001`, `W1001`), and incremental module/interface-digest caching; Selfhost (`native-core-v1`, 34 modular files under `selfhost/src/`) matches these language ergonomics and supports constrained generic classes and interfaces (`interface Foo[T]`), generic class methods, parameterized class inheritance, dot/relative/submodule `from ... use` imports, Agent-First CLI (`graph`, `inspect`, `patch`, `explain`, `schema`, `fix`, `skills`, `capabilities`, `api`, `project new\|lock\|check\|build\|run\|test`, `check/fmt --output-format=json-v1`), 11-tool stdio MCP server (`wysna mcp`), and Stage-3 native LSP (`wysna lsp`) via `driver.c` without Python. | Extend Selfhost (`selfhost/`) to library-mode exports and WASM targets if full Seed replacement is targeted. |
| W03 · Value size | **Resolved in ABI v3 (`v0.7.0`):** `WVal` is natively unified with 8-byte NaN-boxed `WVal64` (`W_RUNTIME_ABI_VERSION = 3`, `ABI_VERSION = 3`, `RUNTIME_ABI_VERSION = 3`, `sizeof(WVal) == 8`) across runtime, AST/MIR C99 Seed, Selfhost, and `llvm-native-v1` (4× density, 8 slots/cache line, 0..6B inline SSO, inline 48-bit ints + `WIntBox` full-64-bit fallback, direct `WEnumBuf` payload). Scalar list buffers also use unboxed MIR access. | Expand cross-OS CI matrix verification (`x86_64-pc-windows-msvc` & `x86_64-unknown-linux-gnu`) for native LLVM ABI v3 `i64` callback adapters. |
| W04 · CoW cost | `optimize_local_cow_uniqueness`, interprocedural `optimize_interprocedural_cow`, `fold_scalar_constants`, `coalesce_temporary_stores`, `eliminate_dead_pure_ops`, and unboxed `range_count_i64`/`div_i64_at`/`mod_i64_at` elide redundant clones, temporaries, and boxing across loops. | Cross-translation-unit link-time CoW summaries for separately compiled libraries. |
| W05 · Generics | Generic templates, constrained parameterized classes and interfaces (`interface Foo[T]`), independent generic methods in classes, and cross-module generic inheritance are monomorphized uniformly across Seed, Selfhost, and LLVM. | Explicit opt-in trait/typeclass system for built-in types only if language scope changes (currently nominal class interfaces by design). |
| W06 · Concurrency | Full stackless CPS state-machine MIR transformation (`src/mir/AsyncLowering.py` + `08_mir_c99.wysna:mir_c_async_function` + `LlvmCodeGen.py`) with cooperative `WysnaCoroTask` scheduling in C99/LLVM/Interpreter (`async fn`, lazy coroutine start, loop `await`, nested/fallible `await ... ?`/`else`), plus bounded worker pools (`std/algorithms.parallel_*`), `std/async.spawn`, timers, and `curl_multi` HTTP across engines. | Extend native LLVM coroutine callback qualification on Windows/Linux CI matrices. |
| W07 · Benchmarks | `bench/run_bench.py` adds `--extended` coverage (`bench/memory_pressure.wysna`, `bench/dict_hash_cache.wysna`, and `bench/wval64_density.c`) alongside the 10 core suite workloads (`BENCHMARK_ORDER`). | Run multi-hour stress telemetry campaigns on Linux/macOS/Windows CI runners before tuning default allocator pools. |
| W08 · Syntax recovery | Recovery gathers up to 100 diagnostics for `check` and LSP using heuristic synchronization and does not produce an executable recovered tree. | Document and test recovery limits if it becomes a public contract. |

The engine matrix records verified behavior differences. Benchmark figures describe only
the workloads that were run; they are not a general performance guarantee.
