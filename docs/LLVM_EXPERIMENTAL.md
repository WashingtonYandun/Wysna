# Experimental LLVM backend

Wysna Seed can emit LLVM IR and native objects directly from verified MIR for
`llvm-scalar-v1`. This is an opt-in research profile. C99 remains the default
backend; the standalone Wysna Compiler SDK continues to use C99 and Zig 0.15.2.
The native SDK does not expose these LLVM commands or acquire a Python dependency.

## Installation and commands

Use Python 3.10 or newer on macOS arm64 or Linux x64. Install the optional tools
from the same checkout or wheel as the compiler:

```sh
python -m pip install '.[llvm]'
# For an installed wheel: python -m pip install 'wysna[llvm]'
wysna check --backend=llvm-experimental hello.wysna
wysna emit-llvm hello.wysna -o hello.ll
wysna build --backend=llvm-experimental --profile=dev hello.wysna -o hello
./hello
```

The extra pins llvmlite 0.46.0, which embeds LLVM 20.1.8. Both versions are checked
at runtime. LLVM verifies the IR before object emission and again after optimization.
A host C99 compiler and linker are still needed to compile the fixed runtime bridge
and link it with the LLVM object. On macOS the Xcode command-line tools, including
`xcrun dsymutil`, are required. `CC` selects the host C toolchain. The compiler
records its command and version with `--stats`; this is not an offline LLVM SDK.

Program functions, arithmetic and control flow are emitted as LLVM IR. The runtime
bridge calls the existing Wysna runtime; no generated C program or automatic C99
fallback is used. A missing toolchain is an error. Unsupported source features
produce `W2013`; malformed MIR produces `W9999` before emission.

## Source and target contract

Supported values are `unit`, `bool`, signed 64-bit `int`, IEEE double `float`, and
UTF-8 `str`. The profile supports direct functions and recursion, concrete generic
specializations over these values, globals, branches, short-circuit expressions,
while loops, direct integer-range loops, break and continue. Runtime operations
include print/eprint, exit, str/int/float/len/ord/chr and scalar operators.

Collections, optional/result/error values, nominal types, closures/function values,
FFI, library exports and runtime operations outside that list are rejected. Imports
are supported when the entire linked module stays within the profile. This profile
is narrower than the native SDK's `native-core-v1` and does not replace it.

The initial host triples are `arm64-apple-macosx13.0.0` and
`x86_64-unknown-linux-gnu`. There is no cross-compilation or Windows/Wasm LLVM claim.
The target data layout comes from the selected LLVM target machine. Runtime ABI 1
uses pointer parameters for boxed values and compile-time checks for its 32-byte
WVal, 48-byte string-literal metadata, 8-byte alignment and 64-bit pointers.
Strings retain the existing ARC ownership, literal caching and cleanup behavior.

Integer addition, subtraction, multiplication and negation wrap at 64 bits.
Division/modulo by zero report `W4001` with the original source position;
`INT64_MIN / -1` returns `INT64_MIN`, and the corresponding remainder is zero.
LLVM instructions do not introduce `nsw`, `nuw` or fast-math assumptions. Floating
comparisons preserve NaN and signed-zero behavior. Existing runtime limits remain.

## Optimization, diagnostics and artifacts

`--profile=dev` selects O0; `release` selects O2; `size` selects O2 with size level 2.
The runtime bridge uses O0/O2/Os respectively, always with debug information.
`emit-llvm` writes verified, unoptimized IR; optimization occurs during object
emission. Use `--stats` to report target/versions, phase timings, peak RSS and host
runtime toolchain. These timings are observations, not performance claims.

`check`, `emit-llvm` and `build` accept `--output-format=json-v1`. Artifact records
identify LLVM IR, native binaries, and the macOS debug-symbol file. Output publication
protects the root source and imported dependencies, including aliases, and preserves
the previous artifact when compilation or linking fails.

`run --backend=llvm-experimental`, C emission with this backend, `--sanitize`, and
foreign link options are rejected explicitly. Execute the built binary directly.
The bridge is covered separately by ASan/UBSan tests; LLVM program instructions are
not yet sanitizer-instrumented, so this is not whole-program sanitizer coverage.

Debug information preserves Wysna source files, line/column locations and function
identities. Linux binaries contain DWARF. macOS builds publish `hello.dSYM` beside
`hello` before removing temporary objects; keep the bundle with the executable.
LLDB can resolve source breakpoints in `dev` builds. Optimization may inline/remove
functions or locations. Original local-variable names and a Wysna debugger expression
evaluator are not implemented; this is not full native debugging parity.

## Validation and promotion

Run the optional suite with:

```sh
python -m tests.unit.test_llvm_backend -v
```

CI runs the suite on Linux x64 and macOS arm64 and compiles from the installed wheel
outside the checkout. Tests compare executable stdout, stderr, exit status and
runtime allocation checks with C99, exercise the three optimization profiles,
check MIR/LLVM rejection, dependency protection and retained debug information.
The existing C99/SDK/Wasm gates remain independent.

This first vertical cut does not complete the LLVM roadmap. Broader language
coverage, full source debugging, sanitizer instrumentation, a pinned standalone
LLVM SDK and a reproducible C99/LLVM performance campaign precede any default
backend decision. No speed, size or AI-usability superiority is claimed here.
