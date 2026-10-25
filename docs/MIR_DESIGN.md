# Wysna MIR: Verified Intermediate Representation

Wysna Seed's default native pipeline is checked AST → typed and verified HIR → verified MIR → C99 → system compiler. `wysna emit-c` and `wysna build` use this pipeline; `--backend=ast` selects the secondary AST emitter. Wysna Compiler has its own frontend, a direct C99 route, and an experimental MIR producer. `wysna mir path.wysna` prints a session-local debug dump. `wysna emit-mir` produces the self-contained, versioned interchange described below.

## Interchange and the Compiler profile

```sh
wysna emit-mir --implementation=seed program.wysna -o seed.mir.json
wysna emit-mir --implementation=compiler program.wysna -o compiler.mir.json
```

The default implementation is `seed`. Both commands produce `wysna.mir` schema 1,
semantic revision `0.5.0`, runtime ABI 2, and a complete type arena. The importer in
`mir/Exchange.py` reconstructs an independent arena and verifies the module before
`mir/CCodeGen.py` can emit C99. The two producers need equivalent behavior; their
temporary identities and operation sequences need not be identical. Files are
published atomically after verification. Source-file destinations are rejected.

Compiler's `result-cfg-v1` profile checks source with its own semantic checker,
produces a session-owned checked node program, builds typed slots, operations and
basic blocks, verifies them, then serializes the interchange. The implementation
separates checked nodes and their boundary verifier (`08_checked.wysna`), the MIR
model and serialization (`08_mir.wysna`), lowering (`08_mir_lowering.wysna`), and
independent structure and dataflow verification (`08_mir_verifier.wysna`) under
`selfhost/src/`.
A prebuilt binary can run without Seed:

```sh
.wysna_cache/stage2 --emit-mir program.wysna > compiler.mir.json
```

The `wysna` wrapper uses Python for bootstrap orchestration and independent IR
verification. This is an incremental Compiler migration; distribution without
Python and a Compiler MIR-to-C99 consumer are future work. Native `build` and
`--backend=selfhost` retain their existing paths.

The supported source profile contains:

- Single-file command programs; `int`, finite `float`, `bool`, `unit`, and managed `str` values.
- Non-generic functions, typed parameters, explicit returns and tail expressions,
  forward/direct calls, immutable and mutable local bindings, and command-local
  top-level bindings. Assignment expressions preserve left-to-right snapshots.
- Scalar arithmetic, comparisons, equality, unary negation and `not`, plus
  `print`, `eprint`, `int`, and `float` calls. Arguments evaluate left to right.
- String literals (including Unicode and embedded NUL), concatenation, equality,
  indexing, `str`, `len`, `ord`, `chr`, and `slice`. String indices are codepoint
  offsets; negative indices are rejected. Slice bounds retain the existing
  clamping behavior.
- Basic results with scalar/string success values and `str` errors, spelled
  `T ! str` or `Result[T, str]`. `Ok`/`Err` constructors require a concrete result
  context. Checked success lifting works in bindings, arguments and returns.
- Result propagation with `?` and `else` handlers, optionally binding the error.
  Handlers may return, break or continue. Unit-result tails discard successful
  payloads while preserving errors, including nested conditional tails.
- `if`/`elif`/`else` statements and expressions, `while`, nested loops, `break`,
  `continue`, early returns, lexical scopes, and short-circuit `and`/`or`.
- Signed int64 wrapping at runtime, division/remainder checks with source paths,
  and the `INT64_MIN / -1` and remainder semantics of the runtime.
- Implicit `main()` when the file contains only declarations, and explicit entry
  calls. Other top-level statements suppress implicit entry invocation as in Seed.

For example, both producers accept:

```wysna
fn label(prefix: str, value: int) -> str { prefix + str(value) }
let message = label("total=", 30)
print(message)
```

Imports, shared global reads inside functions, `for`/ranges, collections,
non-string error channels, nested or context-free results, binary result
operators, optional channels, closures, generic functions, nominal types, FFI, and library exports are
outside this profile. Statements after a terminating path, terminating conditions,
and expressions with unreachable operands remain explicit profile limits.
Unsupported constructs produce `W2013` at an explicit profile boundary before
artifact publication. Guarded syntax is rejected before semantic checking;
remaining feature checks happen during lowering. Source inside the profile retains the semantic
checker's diagnostics. Numeric literals outside
signed int64 or finite binary64 produce `W0002`. The normal C99 route has its own
broader measured coverage.

String and result parameters remain borrowed; reads clone them into owned slots,
and stores move owned values. `Ok`/`Err` consume owned payloads; channel extraction
borrows the box and returns an owned payload. Lowering and verification share the storage classification for
cleanup. Escaping string values are moved to protected slots before lexical
cleanup on normal exits, returns, `break`, and `continue`. Compiler currently emits
conservative parameter clones; Seed may remove read-only clones in its optimization
pass. These operation sequences need not match when their lifetimes and results do.

The checker builds `CheckedProgram` with typed nodes, resolved binding and call
target IDs, function bodies and explicit unit-result tail markers for the MIR
profile. Node IDs belong to that checked value; whitespace and comments do not
renumber them. Lowering traverses this tree and never reparses source tokens,
searches token-position facts or queries a live semantic checker. Its type mapping
produces a self-contained arena with canonical, deduplicated result entries;
semantic TypeIds are not reused as interchange identities. Payload extraction
requires a dominating channel test in both MIR verifiers. Rewriting the tested
value or predicate invalidates the proof; mutable predicates with multiple
definitions cannot establish one.

## Compiler phase ownership

Lexical scopes use checked variable-count/depth checkpoints rather than copying
and restoring variable arrays in each construct. Module expansion is the single
import loader; the obsolete loader in C emission has been removed.

Every invocation owns its mutable state: `ModuleLoader` owns import expansion,
`SemanticContext` owns semantic checking, `CEmitter` owns the direct C99 route,
and `MirBuilder` owns MIR construction. There are no mutable module globals in
`selfhost/src/`. Diagnostics receive their source file explicitly.

`CheckedProgram` and `MirModule` are value snapshots. Their lists use the language's
copy-on-write semantics, so a consumer's modified copy does not mutate another
consumer. The checker context is not part of the lowering interface. The checked
boundary validates type/node references, acyclic node edges, binding/call IDs,
function signatures and bodies before lowering indexes them. MIR verification and
serialization receive a module value, independently of a builder's current state.

The direct C99 route consumes the checked snapshot's `CEmissionMetadata` with
expanded tokens and C-specific semantic annotations. Shared `CheckedSignature`
records contain resolved declarations without parser body offsets. Its source parser remains broader than the MIR
profile. It does not yet consume the checked node tree: completing that migration
requires extending the declared MIR profile and implementing its native consumer.
Normal C99 checking explicitly returns metadata without MIR nodes or binding IDs; passing
that result to MIR lowering is rejected. This boundary does not claim that all
Compiler language constructs already have typed node lowering.

`tests/unit/test_selfhost_mir.py` compares both producers' executable results,
evaluation order, CFG joins/backedges, loop transfers, cleanup, scalar boundaries
and runtime locations. A native fault-injection harness also checks the independent
Compiler verifier against malformed targets, conditions, parameters, operations,
uninitialized joins, consumed operands and owned-value leaks. It also checks semantic
rejection, unsupported features, stage1/stage2 IR determinism, and corrupted
interchange rejection. String parity cases run with ASan/UBSan and logical
allocation checks; Linux also enables LeakSanitizer. The three-stage bootstrap
checks the compiler itself. Result cases also cover payload ownership, handler
control flow, checked-program isolation, type-arena canonicalization and forged or
stale channel proofs.

`tests/unit/test_selfhost_compilation_context.py` exercises interleaved builders,
multiple checked programs and C emitters in one process, import graph isolation,
source-specific diagnostics, copy-on-write consumer isolation, stable node/binding
IDs under trivia changes, and lowering after removing source tokens. It also
rejects malformed checked programs with ASan/UBSan enabled where available.

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

Wysna Compiler recognizes direct ranges in its parser. A parenthesized range expression may use the materialized-list path. Public semantics are the same, but this optimization is not applied uniformly to equivalent syntax.

## Verification and limits

`tests/unit/test_mir.py` mutates valid MIR to confirm that the verifier rejects invalid control flow, references, layouts, storage, runtime signatures, effects, and error-union narrowing proofs. The conformance suite covers range bounds and overflow across supported engines. CI runs the compiler matrix described in [Engine Support and Corpus Coverage](ENGINE_PARITY_MATRIX.md).

MIR verification covers the compiler-generated representation and its modeled ownership flow. It does not prove runtime helper correctness, external `opaque` resource ownership, or behavior outside the tested corpus. Performance measurements for the executable compiler backends are workload and platform dependent; see the repository benchmark tooling for reproducible runs.

`wysna build --stats` reports MIR lowering, C emission, C compilation, and linking separately. C adapters supplied as source are compiled before linking. The historical `codegen` field aggregates lowering and emission; do not add it to those subphases again. `total` includes compilation and linking.
