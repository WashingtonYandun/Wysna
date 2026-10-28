# Engine Support and Corpus Coverage

Wysna Seed (`seed`) is the reference implementation in Python. Its default native pipeline is checked AST → typed HIR →
verified MIR → C99 → system compiler. The public backend name remains `hir`.
The interpreter and AST C99 backend are execution oracles; Wysna Compiler (`selfhost`) is an independent
self-hosted compiler. MIR is an intermediate representation, not a fifth engine. The read-only
[effect report](EFFECTS.md) is a static analysis of verified MIR, not another execution engine, and is
not part of the engine parity counts.
Wysna Compiler's `native-core-v1` profile uses verified MIR for its normal C99 route and interchange. Focused parity, native phase isolation and installed SDK acceptance have separate gates. Profile bounds are defined in [MIR design](MIR_DESIGN.md).

## Measured corpus

The table records the native MIR measurement at `f4c142183ec531fe973858a2c84da54bc538a5f4`, with the published versioned compiler and unchanged source inputs. It comes from an actual `tools/ci_conformance.py --profile=release` run. It covers
the public runtime examples and negative fixtures, with no unsupported exclusions. The
separate sanitizer gate checks memory safety; neither gate establishes universal language
parity or measures code coverage.

<!-- GENERATED TABLE START -->

| Feature Category | Fixtures | Wysna Seed (Interpreter) | Wysna Seed (AST C99) | Wysna Seed (HIR C99) | Wysna Compiler (Self-Hosted) | Status / Milestone |
| :--- | :---: | :---: | :---: | :---: | :---: | :--- |
| **`core`** | 158 | ✅ 158/158 | ✅ 158/158 | ✅ 158/158 | ✅ 158/158 | Declared supported |
| **`generics`** | 15 | ✅ 15/15 | ✅ 15/15 | ✅ 15/15 | ✅ 15/15 | Declared supported |
| **`checked_modules`** | 1 | ✅ 1/1 | ✅ 1/1 | ✅ 1/1 | ✅ 1/1 | Declared supported |
| **`error_unions`** | 8 | ✅ 8/8 | ✅ 8/8 | ✅ 8/8 | ✅ 8/8 | Declared supported |
| **`functions`** | 5 | ✅ 5/5 | ✅ 5/5 | ✅ 5/5 | ✅ 5/5 | Declared supported |
| **`stdlib`** | 3 | ✅ 3/3 | ✅ 3/3 | ✅ 3/3 | ✅ 3/3 | Declared supported |
| **`regressions`** | 13 | ✅ 13/13 | ✅ 13/13 | ✅ 13/13 | ✅ 13/13 | Declared supported |
| **Total Verified** | **203** | **203/203 (100.0%)** | **203/203 (100.0%)** | **203/203 (100.0%)** | **203/203 (100.0%)** | _measured_ |

<!-- GENERATED TABLE END -->

Regenerate after running the corpus with `python3 tools/ci_conformance.py --matrix`.
The generator reads `summary.json`; it reports missing or stale measurements rather
than inventing success from `tests/engine_profiles.json`.

The core category includes primitive operations, control flow, tuples, classes,
copy-on-write, ownership, diagnostics, nested patterns, Result closures and cyclic
formatting. Panic fixtures require a nonzero exit and their declared diagnostic code.
Generics include functions, structs, recursive ADTs and rejected invalid layouts.
Checked modules cover imported specialization. Error unions cover subset propagation,
nominal error identity, exhaustive handlers and managed payload cleanup.

## Expansion boundaries

Function values, captures, FFI, generic OOP, the standard library and LSP have separate
contracts in `tests/unit/test_expansion_contracts.py`. A green core corpus does not
imply Wysna Compiler support for every expansion combination. Fields-only parameterized
classes are covered separately by the native MIR pipeline; parameterized classes with
methods, inheritance or interfaces remain outside that profile. Scoped resources have a separate
four-engine gate in `tests/unit/test_audit_readiness.py`. The SQLite adapter/application
also has dedicated checks; see [C interoperability](FFI_VALIDATION.md).

Wysna Seed shares a checked compilation session and canonical type arena across modules.
Wysna Compiler expands and qualifies imports before its own semantic pass. The checked-module
fixture does not prove equivalence of the two module implementations.

## Bootstrap Fixed-Point vs. Core Corpus Parity

Seed AST builds a separately recorded initial engine. Three successive native MIR emissions of `selfhost/compiler.wysna` must have identical C0, C1 and C2 hashes. This demonstrates the recorded deterministic fixed point, not semantic correctness. Corpus parity separately checks output, exit status and rejection diagnostics across four engines. The native SDK additionally requires installed acceptance without Python or a discovered host C toolchain.

## Execution profiles

`--profile=integration` permits only exclusions declared with an actionable reason in
`tests/engine_profiles.json`. `--profile=release` rejects all exclusions and requires
every engine to pass every fixture. `--sanitize` instruments native programs with
ASan/UBSan; Linux additionally enables LSan.

`wysna build --backend=selfhost` invokes native Wysna Compiler directly, with no Wysna Seed semantic pre-check.
Unsupported syntax or semantics must be diagnosed by Wysna Compiler. See [known limits](KNOWN_LIMITS.md)
for runtime and platform boundaries.
