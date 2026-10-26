# Native LLVM backend

Wysna Seed emits LLVM IR directly from verified MIR for `llvm-native-v1`.
LLVM is an explicit option; C99 remains the default. Wysna Compiler and its
Python-free native SDK continue to use C99. The separate [Seed LLVM SDK](LLVM_SDK.md)
bundles pinned native LLVM tools and Zig and explicitly requires host Python.

## Installation and commands

For source or wheel development, use Python 3.10+ on macOS arm64 or Linux x64:

```sh
python -m pip install '.[llvm]'
wysna check --backend=llvm-experimental hello.wysna
wysna emit-llvm hello.wysna -o hello.ll
wysna build --backend=llvm-experimental --profile=release hello.wysna -o hello
wysna run --backend=llvm-experimental hello.wysna --timeout-ms 10000 -- "two words"
```

The extra pins llvmlite 0.46.0 / LLVM 20.1.8. Both versions are checked. This
mode also requires a host C99 compiler/linker for the runtime and external adapters;
macOS needs `xcrun dsymutil`. `CC` selects that compiler. The installed LLVM SDK
uses its own verified `opt`, `llc`, `dsymutil` and Zig instead and does not import
llvmlite. Missing or incompatible tools produce an error, without another backend.

`check`, `emit-llvm` and `build` support `--output-format=json-v1`. Native `run`
preserves arguments, environment, working directory, streams and application exit
status. Use `--report-json PATH` for its separate structured execution result.
Timeout or interruption terminates the launched process tree, including descendants
that created their own process groups.

## Source, ABI and target contract

The native MIR profile includes scalars, UTF-8 strings, globals/imports, lists,
dictionaries, tuples and CoW mutation, ranges, optional/Result/error-union channels,
structs/enums, classes/inheritance/interfaces, guarded patterns, snapshot closures,
function values, concrete generic specializations, weak references and the existing
scalar/opaque C FFI. Standard-library calls use the existing runtime contracts.
HTTP and external libraries retain their explicit development/link dependencies.
Browser-library exports belong to their dedicated target and are rejected here.

The supported host triples are `arm64-apple-macosx13.0.0` and
`x86_64-unknown-linux-gnu`. There is no cross-compilation, Windows or Wasm LLVM
claim. The provider supplies and verifies the target data layout. ABI 2 uses
pointer-based boxed-value boundaries; callback adapters implement each target's
actual C `WInvoke` calling convention. Compile-time checks enforce WVal size 32,
payload offset 8, string-literal metadata size 48, alignment 8, vtable layout and
64-bit pointers. A single runtime translation unit owns runtime state and FFI
wrappers. Program function bodies are LLVM IR; generated C program bodies and
silent C99 fallback are forbidden.

Independent MIR verification runs before emission. Invalid representations,
ownership, CFGs and contracts produce `W9999`. LLVM verifies emitted IR and verifies
optimized IR again. Integer arithmetic wraps at 64 bits; division/modulo checks and
`INT64_MIN / -1` semantics remain explicit. LLVM does not add fast-math, `nsw` or
`nuw` assumptions. ARC, CoW, allocation checks and runtime limits are unchanged.

## Optimization, instrumentation and debugging

`dev` uses O0, `release` O2, and `size` size-oriented LLVM optimization (Oz for native
tools, size level 2 for the binding). Runtime C uses O0/O2/Os. Debug information and
language checks remain enabled. C99 also accepts these explicit profiles so a
comparison does not accidentally compare its default O2 build against LLVM O0.
`emit-llvm` publishes verified unoptimized IR.

`build` and `run` accept `--sanitize` and `--link-lib`. Instrumentation requires an
explicit host Clang for the SDK; ordinary SDK builds remain offline. Clang compiles
the verified LLVM program instructions and the C runtime/adapters with matching
ASan instrumentation/runtime, with UBSan also applied where C operations retain
type information. This avoids mixing upstream and Apple ASan ABIs. LLVM IR has no
high-level C types for UBSan to recover; Wysna's own scalar guards remain active.
An intentionally invalid LLVM stack access must fail under ASan in qualification.
`--stats` records the instrumentation compiler, provider, target, cache hits and
phase timings. Sanitized builds are not used for performance measurements.

DWARF preserves Wysna files, lines/columns, functions, parameters, lexical scopes,
shadowed local variables and globals. MIR owns an optional debug sidecar; it carries
no AST nodes and does not alter the semantic interchange schema. Native scalar
variables have scalar debugger types; managed variables expose their WVal tag and
payload. This does not provide a Wysna debugger expression evaluator or custom
collection pretty-printers. Optimization may remove locations or values. Use `dev`
for source stepping and variable inspection.

Linux binaries retain DWARF. On macOS the matching `program.dSYM` is generated before
temporary objects are removed; keep it with the executable. Failed compilation,
verification, linking or debug publication preserves the previous LLVM artifact.
Source files, imported dependencies and SDK contents are protected against output
replacement, including aliases.

## Cache and qualification

Explicit C99 profiles and LLVM share the content-addressed artifact-cache policy.
Keys include source, compiler executable hash/version/command, flags, target and
freshly resolved transitive-header hashes. Checksums reject corrupted objects.
Headers are resolved even on a hit so moving an identical C adapter beside a
different local header cannot reuse stale code. Publication is locked and atomic.
Use `WYSNA_COMPILER_CACHE_HOME` to relocate the cache or
`WYSNA_COMPILER_CACHE=off` to bypass it; installed SDK files are not a cache.

```sh
python -m tests.unit.test_llvm_backend -v
python tools/qualify_llvm.py --output /tmp/llvm-qualified
python tools/qualify_llvm.py --sanitize --output /tmp/llvm-sanitized
python tools/validate_sqlite_ffi.py --llvm --sanitize --samples 1
python tools/benchmark_llvm.py --qualification /tmp/llvm-qualified/summary.json --output /tmp/llvm-comparison
```

General discovery skips the optional LLVM tests without its tools. Explicit module
execution fails on missing tools. CI qualifies every positive corpus case at all
three profiles and every negative case, with zero unsupported exclusions, then
repeats under sanitizers. Additional tests cover FFI resources, actual debugger
breakpoints/variables, malformed MIR/IR, dependency protection, corruption and
process cleanup. Both native-tool SDK providers are separately qualified, measured
and consumed outside a checkout. Existing C99, bootstrap, native SDK and Wasm gates
remain required.

## Measurement and default decision

`bench/llvm_policy.json` declares budgets before measurement. The campaign binds
qualification and measurements to the same commit and exact source/fixture hashes;
missing, duplicate, stale or failed qualification rows reject measurement. It keeps
all raw samples and regressions, records compiler phases, cold/warm compile times,
parent/compiler-child RSS separately, runtime RSS, executable/debug sizes, SDK
manifest/installed size and toolchain provenance. Both routes use the same C toolchain
when the LLVM SDK is selected. Cold means a new Wysna object cache; OS pages are not
flushed. Warm measurements reuse dependency-checked objects. Runtime includes process
launch, which can dominate small workloads. The sustained-work fixture also records
an internal monotonic duration through the same measurement-only C adapter in both
backends, separately from process launch; raw timing output is retained.

CI runs ten workloads plus separate startup and sustained-work fixtures, all three profiles, five cold and five warm compile samples,
and fifteen runtime samples after warmups. Raw reports are published as
`llvm-measurements-OS-PROFILE` artifacts of the relevant CI commit. A failed budget does not
silently disappear when LLVM remains opt-in.

The implementation decision is to retain C99 by default and expose qualified LLVM
explicitly. Promotion requires accepted measurements on both hosts, equivalent
installed dependency contracts and an explicit later decision. Seed LLVM requires
Python while Wysna Compiler does not; successful LLVM qualification alone cannot
satisfy that distribution requirement. No speed, size or AI-usability superiority
is asserted.
