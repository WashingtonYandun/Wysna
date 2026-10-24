# Compiler architecture

## Semantic authority and ownership

The Seed pipeline is:

```text
SourceSnapshot -> AST -> CheckedProgram -> typed HIR -> verified MIR -> C99
```

`CompilationSession` owns source snapshots, dependency identity, declarations and
the canonical type arena. `CompilerService` is the consumer-facing API for parsing,
checking, interpretation, lowering, interchange and emission. Checked results are
bound to their originating session and generation. Consumers receive detached
syntax; lowering must not reread dependencies or invoke a checker again.

`TypeRelations` is the authority for assignability over canonical type identities.
`TypeChecker` coordinates declaration collection, symbolic definition checking,
body checking and specialization. Declaration collection has four ordered phases:
validate names, register nominal shells, resolve signatures and validate class
contracts. Publishing shells before resolving signatures preserves forward names.

## Semantic rule domains

Expression rules live in `checker/ScalarRules.py`, `AccessRules.py`, `ResultRules.py`,
`CollectionRules.py`, `ControlFlowRules.py` and `CallRules.py`. Each receives the
invoking checker explicitly. All mutable state remains in that checker's session;
rule modules contain no mutable compilation state.

`CallRules` distinguishes a resolved callable target from an already checked
constructor result. Imported generic calls, named calls, enum constructors,
builtin contracts and argument checking are separate steps. A constructor's result
must not accidentally flow into function argument validation.

`GenericState` defines specialization identities, naming and telemetry.
`Specialization.Instantiator` implements concrete specialization and failure cleanup;
it does not delegate algorithms back to `TypeChecker`. Symbolic definition checks
restore temporary scope, specialization and lexical state on success and failure.
A failed concrete specialization preserves its original diagnostic and records a
failed cache entry rather than publishing a partial declaration.

## Independent execution oracles

The interpreter and AST C99 backend retain independent expression algorithms in
`runtime/ExpressionRules.py` and `codegen/ExpressionRules.py`. Their shared
`ExpressionDispatch` supplies only exact-class lookup and invariant diagnostics.
It does not implement evaluation, typing, ownership or emission. The semantic
wrapper invokes its handler directly: a separate forwarding frame per expression
would reduce the supported recursion budget on Python 3.11. Stack-depth and bounded
JSON regressions enforce this without increasing Python's recursion limit.

Dispatch tables are read-only snapshots. A new AST class, including a subclass of
an existing expression, requires an explicit engine policy. Unknown nodes raise
`W9999` with source provenance; they cannot silently become `unit`. `SuperExpr` is
an AST C99 receiver marker consumed by method-call emission; standalone emission
is explicitly rejected. Missing checked superclass targets also raise `W9999`.
Valid statement-position control flow can still produce `unit` by language design.

HIR and MIR retain independent verifiers. The native Wysna Compiler remains an
independent implementation: its checked-node MIR producer supports the declared
`result-cfg-v1` profile, while its broader C99 route uses checked emission metadata.
This organization does not enlarge that MIR profile or imply a standalone native
distribution or a direct LLVM backend.

## Tool and target boundaries

`main.py` is the executable entry point and a compatibility facade for existing
Python consumers. Productive modules must not import it.

- `tooling/CLI.py`: command parsing, command selection and terminal presentation.
- `tooling/SourceCommand.py`: source command orchestration and diagnostics.
- `tooling/RunReport.py`: application exit and JSON run-report protocol.
- `compiler/Bootstrap.py`: locked bootstrap cache and compiler construction.
- `compiler/*Toolchain.py`: target-specific build and link execution.
- `common/WasmModule.py`: target-independent WebAssembly inspection.
- `codegen/BrowserBindings.py`: generated browser ABI facade.

Compiler, codegen and runtime modules must not depend on tool presentation modules.
The installed package root comes from `common.Paths`, independently of `main.py`.

## Runtime implementation domains

`runtime/wysna_runtime.h` is the public C99 umbrella. It includes ordered internal
headers for core representation/context, constructors, memory and CoW, equality,
collections, UTF-8, access, operators, formatting and builtins, followed by the host
adapter. Internal headers are not standalone APIs. Include the umbrella first.

This split preserves `WVal`, tag numbers, layout, calling convention and ownership.
The runtime context remains per generated program/translation unit; the split does
not introduce thread safety, concurrency, a tracing collector or a new ABI.
Sibling includes work from both a checkout and an installed wheel. All runtime
headers are packaged and included in bootstrap cache invalidation.

## Enforced contracts

Run `python -m tests.unit.test_architecture_contract`. CI checks node policy coverage,
exact dispatch, read-only tables, source diagnostics, import direction, independent
oracles, runtime header inclusion and generic rollback. Existing conformance,
three-stage bootstrap, four-engine differential tests, ownership sanitizers and
installed-wheel checks remain necessary; structural checks do not replace them.


CI gives compiler/tooling contracts and executable conformance separate job budgets
on the same platform/compiler matrix. Required conformance statuses reject failed,
cancelled or skipped contract jobs explicitly; the split preserves every test,
release fixture, bootstrap stage and sanitizer check. Workflow contracts enforce
coverage and failure propagation without extending the execution time limit.
