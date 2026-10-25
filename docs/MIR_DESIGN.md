# Wysna MIR: Verified Intermediate Representation

Wysna Seed's default pipeline is checked AST → typed and verified HIR → verified MIR → C99 → C compiler. The public backend name remains `hir`; `--backend=ast` selects the independent AST oracle. Wysna Compiler uses its own frontend and a single productive pipeline: checked nodes → verified MIR → native C99 emitter. Its positional CLI and MIR commands use the same implementation.

## Interchange and native-core-v1

The Seed CLI selects a producer explicitly:

```sh
wysna emit-mir --implementation=seed program.wysna -o seed.mir.json
wysna emit-mir --implementation=compiler program.wysna -o compiler.mir.json
```

Both producers emit `wysna.mir` schema 1, semantic revision `0.5.0`, runtime ABI 2 and a self-contained type arena. `mir/Exchange.py` reconstructs an independent arena and verifies it before the Python MIR consumer can emit C99. Producer IDs and operation sequences are local implementation details; behavior, ownership and diagnostics are the compatibility contract.

The standalone [native SDK](NATIVE_SDK.md) supplies `check`, `emit-mir`, `emit-c`, `build` and `run` without Python. Its native engine checks MIR before serialization or C emission. The Python wrapper remains available for Seed and independent interchange validation; it is not required by the SDK.

The `native-core-v1` source profile contains:

- Scalars, UTF-8 strings including embedded NUL, globals, typed functions, explicit and inferred lambda returns, conditionals, loops, early returns and short-circuit operators.
- Lists, dictionaries and tuples, collection operations, copy-on-write mutation, direct ranges, nested irrefutable tuple loop bindings and preserved left-to-right evaluation.
- Optional and Result channels, nested/context-free builtin channel constructors, success lifting, propagation, handlers and closed error unions.
- Structs, enums, classes, inheritance, interfaces and virtual/super calls; pattern matching with guards and explicit payload witnesses.
- Function values, snapshot closures, indirect calls, concrete generic functions and concrete generic structs/enums. Generic definitions retain symbolic obligations and bounded specialization.
- Relative/standard-library imports through one module loader, physical module identity, and the declared scalar/opaque C ABI.

Parameterized classes/interfaces, generic nominal values with unresolved type arguments, library-mode exports and WebAssembly targets are outside this native profile. An unresolved nominal value requires an explicit concrete type context. Seed's formatter, package/project and LSP commands remain outside the SDK. Unsupported source features report `W2013`; there is no Python fallback. This is a declared, tested profile, not universal Seed parity.

## Phase ownership and verification

`ModuleLoader` owns import expansion. `SemanticContext` owns checking and generic specialization; each call/initializer owns a separate `SemUnification` so nested expressions cannot replace outer substitutions. `CheckedProgram` contains typed nodes, bindings, resolved signatures/records, declarative generic templates and function bodies. It contains no expanded token stream, emitter context or source-position lookup tables. `MirBuilder` lowers that detached value; the emitter only accepts verified `MirModule` values. Mutable compiler state belongs to each invocation.

The implementation under `selfhost/src/` separates:

- `08_checked.wysna`, `08_checked_records.wysna` and `08_checked_verify.wysna`: detached checked data, nominal resolution and boundary verification.
- `08_generics.wysna`: symbolic obligations, concrete specialization queue and budgets.
- `08_mir.wysna`, `08_mir_lowering.wysna` and `08_mir_verifier.wysna`: MIR model/interchange, lowering and independent structure/dataflow verification.
- `08_mir_channels`, `08_mir_patterns`, `08_mir_functions`, `08_mir_places`, `08_mir_collections`, `08_mir_nominals`, `08_mir_classes` and `08_mir_foreign`: focused lowering and emission policies.
- `08_mir_c99.wysna`: the single native C99 consumer, with chunked output assembly.

Checked verification rejects invalid type, record, signature, binding, pattern, capture and indirect-call references before lowering indexes them. Native and Python MIR verifiers check arity, types, ownership, CFG initialization and dominating channel/pattern witnesses. Reassigning the tested value invalidates its proof. Verification does not prove runtime helpers correct or manage external opaque resources automatically.

Snapshots use copy-on-write lists: consumers cannot mutate one another's checked programs or modules. Node and binding IDs remain stable under trivia changes. Generic nominal identities use a reserved compiler namespace; user-facing formatting hides specialization suffixes without changing identity.

## Bootstrap provenance

`compiler/Bootstrap.py` is the authoritative bootstrap builder. Seed's AST oracle emits `bootstrap.c`, which builds the initial native engine. That engine emits native C0; C0 builds Stage 1, which emits C1; C1 builds Stage 2, which emits C2. Acceptance requires **C0 = C1 = C2** and unchanged source/runtime/build inputs. The Seed emission hash is recorded separately. Comparing Seed AST text to native MIR text is not the fixed-point contract.

Caches are versioned by implementation fingerprint, locked while constructing, and validate the compiler binary hash before reuse. `bootstrap.json` records provenance. A fixed point demonstrates deterministic self-hosting; independent behavior and rejection tests establish the measured semantic evidence.

## Validation boundaries

`test_native_mir_pipeline` compares native MIR C99, the independent Python MIR consumer and Seed HIR execution, including sanitizers and logical allocation checks. `test_selfhost_mir` covers deterministic interchange and adversarial MIR mutations. `test_selfhost_compilation_context` verifies detached compilation, interleaved state, import/source isolation and malformed checked programs. `test_native_sdk` tests package boundaries; `tools/validate_native_sdk.py` exercises installed commands outside the checkout with a tool PATH that cannot discover Python or a host C compiler.

The [engine matrix](ENGINE_PARITY_MATRIX.md) identifies corpus measurements and their limits. Native SDK consumer CI is separate from compiler/bootstrap CI; producing an archive is not sufficient installation evidence.

## Representation

`mir/Nodes.py` defines modules, functions, typed slots, physical layouts, operations, basic blocks, and `goto`, `branch`, `return`, and `unreachable` terminators. MIR uses mutable slots; it is **not SSA**. Its operands are resolved identities and projections, not AST/HIR nodes or C fragments. Every operation preserves its source location.

Fields use offsets in the complete layout, including inherited fields. Mutable paths capture indices and class identity before evaluating the RHS, then resolve the slot afterward; they do not retain element pointers that a reallocation could invalidate. Patterns become predicates and branches. Narrowing an error union requires a successful check that dominates its use.

## Effects and ownership

`clone` retains, `store` moves, and `release` frees. Constructors, calls, reads, writes, and checks have explicit effect categories. Calls declare consumed operands. Managed parameters are borrowed; results are owned. Lowering inserts LIFO cleanup on normal exits, returns, propagation, `break`, and `continue`. Escaping values are protected before cleanup.

`mir/Verifier.py` checks identities, the type arena, signatures, offsets, storage, arity, effects, and successors. Dataflow intersects definitely initialized slots and joins possibly live references at joins and backedges. It rejects reads from consumed values, moves from borrowed values, returns with owned references still pending, and result payload extraction without a current channel proof. The default native emitter consumes this verified representation. Verification does not prove the C helpers correct or track external `opaque` handles. Runtime helpers have explicit arity and operand/result relationships; compatibility uses canonical TypeIds, and enum payload reads retain the resolved variant.

Before emission, an ownership pass removes copies of immutable `str` parameters when the temporary has a single definition and no consuming use, return, or mutable-place use. Read-only uses then borrow the parameter whose lifetime is protected by the caller. Copies that escape through moves keep their ownership; globals, locals, and other parameter types are excluded. MIR is verified before and after this pass, and `wysna mir` shows the optimized representation.

## Storage and ABI

Local variables, parameters, and returns of type `int`, `float`, and `bool` use `int64_t`, `double`, and `bool`. Internal counters and predicates are scalars as well. Globals, strings, lists, tuples, structs, objects, optionals, and results still use `WVal`. Adapters convert at function-value, closure, and virtual-dispatch boundaries; runtime helpers use `WVal`; the external scalar/opaque C ABI uses its declared C widths.

There are no specialized lists or unboxed payload layouts. Runtime ABI v2 stores string metadata inside its variant. The tagged value occupies 32 bytes in the measured arm64 ABI; converting a scalar to a collection element boxes it again. This is a known memory-density cost retained by the current runtime ABI. Effect classification is conservative and does not perform interprocedural purity analysis.

## Ranges

`for item in start..end` and `..=` capture bounds once, from left to right, validate the range size, and use a counter without creating a list. The 100-million-element limit applies even when the loop body immediately executes `break`. An inclusive range ending at `INT64_MAX` keeps the existing error behavior. Ranges used as values remain lists with CoW semantics; Wysna does not add a range type.

Both MIR lowerers recognize range nodes directly, including grouping that preserves the range expression. A range stored in a variable is a materialized collection.

## Verification and limits

`tests/unit/test_mir.py` mutates valid MIR to confirm that the verifier rejects invalid control flow, references, layouts, storage, runtime signatures, effects, and error-union narrowing proofs. The conformance suite covers range bounds and overflow across supported engines. CI runs the compiler matrix described in [Engine Support and Corpus Coverage](ENGINE_PARITY_MATRIX.md).

MIR verification covers the compiler-generated representation and its modeled ownership flow. It does not prove runtime helper correctness, external `opaque` resource ownership, or behavior outside the tested corpus. Performance measurements for the executable compiler backends are workload and platform dependent; see the repository benchmark tooling for reproducible runs.

`wysna build --stats` reports MIR lowering, C emission, C compilation, and linking separately. C adapters supplied as source are compiled before linking. The historical `codegen` field aggregates lowering and emission; do not add it to those subphases again. `total` includes compilation and linking.
