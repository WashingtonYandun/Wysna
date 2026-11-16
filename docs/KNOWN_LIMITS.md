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

**What a fix requires.** Real sharing needs one allocation per captured mutable
variable that both the enclosing frame and the closure read and write through. That
means the checker must decide which locals become cells (any local captured by a lambda
and mutated anywhere), the interpreter, the HIR and Wysna Compiler must all agree on the
representation, and the ARC/CoW interaction has to be re-verified with the sanitizers.
This is a language change, not a patch.

*Open decision.*

---

## 2. Error unions cover scalar, ADT, class, and generic value channels

**Behavior.** `T ! E1 | E2` allows `?` to propagate multiple error types and `match` to
exhaustively inspect the value bound by `else err`. A source error set can propagate when
it is a subset of the destination function's declared error set. Payloads are not
converted.

Supported alternatives across Seed and LLVM are `int`, `float`, `bool`, `str`, `struct`
(including concrete generic `Struct[T]`), `enum` (including concrete generic `Enum[T]`),
and `class` types (`Selfhost` supports scalars, non-generic `struct`/`enum`, and
non-generic `class`). Patterns use `Type(error)` (including generic `Type[T]` payloads) and bind the
full payload. Duplicate alternatives are canonicalized; a union must contain at least
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
uses one checked-node → verified MIR → C99 pipeline under `native-core-v1`. Constrained parameterized classes, parameterized classes with independently generic methods (`W2013`), parameterized interfaces, unresolved generic nominal values, library-mode exports and WASM targets remain explicit profile limits; see [MIR design](MIR_DESIGN.md). The native SDK driver (`packaging/native/driver.c`) supports native formatting (`wysna fmt`) and multi-file directory `wysna test` discovery with `.expected` / `# RUNTIME_PANIC:` verification without Python, while project manifest management (`wysna.toml`), lockfiles, and Stage-3 LSP services remain in Seed. Compiler does not share Seed's compilation session or type arena. Deterministic bootstrap and corpus tests are evidence for their measured inputs, not universal semantic equivalence.

The [native SDK](NATIVE_SDK.md) packages the native engine, runtime, stdlib and pinned C toolchain. Its commands run without Python. The SDK is a portable development artifact; platform acceptance, signing and release publication are distinct gates. HTTP and other external C dependencies are not included in the offline core.

Installed wheels contain Wysna Seed, its C runtime header and the standard library;
Python remains a prerequisite. Wysna Compiler bootstrap, `wysna test` and `wysna bench`
require a source checkout. The [standalone Seed distribution](SEED_DISTRIBUTION.md)
builds a portable installation with a private CPython and Zig as the C99 toolchain for
macOS arm64, Linux x64 and Windows x64; it needs no virtual environment or host C SDK.
Its launcher verifies the required components before importing Seed, and
`wysna doctor` verifies the complete installed file index. External FFI libraries are
not included. CI validates the Windows, macOS and Linux installer lifecycles on their
respective hosts, including installation and removal. The published 0.6.0 installers
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

**Why it is this way.** ARC alone cannot collect cycles, and a cycle collector would add
pause behaviour and complexity that the "deterministic refcounting" promise in SPEC §8
rules out.

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
| LSP | `initialize`, `didOpen/didChange/didClose` with ordered ranged edits, `definition`, `hover`, `completion`, `references`, workspace-wide cross-file `rename` (refusing collisions and broken dependents), `textDocument/semanticTokens/full` (including nested `/* ... */` block comments), `codeAction` (quickfixes), `signatureHelp`, document formatting and `publishDiagnostics` (errors and `W6001`/`W6002` warnings). |

| Capability policy | `wysna run --allow` and `--deny` reject modeled capability violations and incomplete effect analyses before interpretation (`W5006`). This is a static gate, not an OS sandbox; native builds, `project run` and trusted runtime/FFI behavior are outside it. |
| Dictionary keys | `int`, `str`, `bool`, `enum`, and hashable `tuple` composites; `float`, collections, functions, classes, and composites containing non-hashable elements are rejected with `W2010`. |
| Standard library scope | Includes text (with Unicode 16.0.0 UAX #15 `NFC`/`NFD`), paths, filesystem, file/buffer I/O, stdin/stdout streams, resources, algorithms (incl. parallel pools), UTF-8, JSON, `std/crypto` (SHA-256/HMAC + ChaCha20-Poly1305 AEAD + CSPRNG), `std/time` (calendar/ISO-8601), `std/environment`, `std/async` (timers + OS-thread `spawn`), process execution, and HTTP client; see [standard library](STDLIB.md) for contracts and platform limits. |

LSP cross-file `rename` resolves all workspace `.wysna` files when `rootUri`/`workspaceFolders` are configured and refuses collisions or broken dependent modules; when no workspace root is configured, rename refuses exported symbols safely.

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

## 8. Audit Status and Open Scope in 0.7.0

Closed items on `feat/close-language-gaps-v0.7` vs remaining open architectural work:

| Finding | Current State | Remaining work |
| :--- | :--- | :--- |
| W01 · Compiler architecture | Seed supports nested `/* ... */` comments, independent generic class methods (`Box[T].transform[R]`), class/generic error unions, and tuple dict keys across AST/HIR/MIR/LLVM; Selfhost (`native-core-v1`) adds nested comments, non-generic class error unions, tuple dict keys, `--fmt`, and multi-file native `wysna test` discovery in `driver.c`. | Extend Compiler (`selfhost/`) to parameterized class inheritance, parameterized class generic methods (`W2013`), parameterized interfaces, `wysna.toml` manifests, library-mode exports, WASM targets, and Stage-3 native LSP. |
| W03 · Value size | **Resolved in ABI v3 (`v0.7.0`):** `WVal` is natively unified with 8-byte NaN-boxed `WVal64` (`W_RUNTIME_ABI_VERSION = 3`, `ABI_VERSION = 3`, `RUNTIME_ABI_VERSION = 3`, `sizeof(WVal) == 8`) across runtime, AST/MIR C99 Seed, Selfhost, and `llvm-native-v1` (4× density, 8 slots/cache line, 0..6B inline SSO, inline 48-bit ints + `WIntBox` full-64-bit fallback, direct `WEnumBuf` payload). Scalar list buffers also use unboxed MIR access. | Expand cross-OS CI matrix verification (`x86_64-pc-windows-msvc` & `x86_64-unknown-linux-gnu`) for native LLVM ABI v3 `i64` callback adapters. |
| W04 · CoW cost | `optimize_local_cow_uniqueness` and interprocedural `optimize_interprocedural_cow` (fixed-point read-only parameter & caller clone elimination) elide detach copies statically before CoW writes. | Measure large multi-module workloads if cross-translation-unit link-time CoW summaries become necessary. |
| W05 · Generics | Generic templates, independent generic methods in classes, interface bounds, and cross-module generic inheritance are verified at definition time and monomorphized uniformly in Seed and LLVM. | Extend definition-time verification and specialization to parameterized interfaces and complex trait-style retroactive conformance. |
| W06 · Concurrency | Bounded OS-thread worker pools (`std/algorithms.parallel_*`), background `std/async.spawn` tasks (`pthread`/`CreateThread`), non-blocking `curl_multi` HTTP, timers (`sleep_ms`), and `all`/`race`/`wait`/`await` work across all 5 engines. | Replace eager lowering on user `async fn` declarations with full stackless CPS state-machine MIR transformation when required. |
| W07 · Benchmarks | `bench/run_bench.py` adds `--extended` coverage (`bench/memory_pressure.wysna`, `bench/dict_hash_cache.wysna`, and `bench/wval64_density.c`) alongside the 10 core suite workloads (`BENCHMARK_ORDER`). | Run multi-hour stress telemetry campaigns on Linux/macOS/Windows CI runners before tuning default allocator pools. |
| W08 · Syntax recovery | Recovery gathers up to 100 diagnostics for `check` and LSP using heuristic synchronization and does not produce an executable recovered tree. | Document and test recovery limits if it becomes a public contract. |

The engine matrix records verified behavior differences. Benchmark figures describe only
the workloads that were run; they are not a general performance guarantee.
