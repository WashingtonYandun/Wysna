# Wysna Seed Typed High-Level IR (HIR)

HIR is Wysna Seed's typed intermediate representation and the input to its default C99 backend.
It carries canonical types, resolved symbols and fields, and explicit control-flow and
assignment semantics from the checker. The verifier rejects malformed internal
representations before lowering to verified MIR. MIR makes control flow, ownership
operations, and storage layouts explicit before C99 emission;
see [MIR design](MIR_DESIGN.md).

## 1. Compiler pipeline

Seed's batch CLI and language server use `compiler.CompilerService` to check source
through a `CompilationSession`. A successful check returns a `CheckedProgram` with an
immutable source snapshot, module/declaration metadata and tooling queries. Its owned
`parse`/`check_parsed` boundary keeps the normal CLI to one scan and parse; inspection
trees cannot be supplied as executable checked input. The service
rejects stale generations and results from a different session before linking, lowering
or emission. The checker remains an internal implementation detail of this facade.

Public syntax views contain frozen symbol/type summaries and no mutable type arena.
Executable consumers use the service's interpretation, lowering and emission methods.
Linking qualifies module references in a
separate tree, preserving the checked module's member expressions for editor queries.
`lower_hir`, `lower_mir`, `export_mir` and `emit_c` reuse checked modules and their canonical type arena;
they do not read or check dependencies again. The AST emitter and interpreter remain
independent execution paths. The REPL retains its interactive checker state.

This boundary is an adapter over the existing checked AST. It does not yet remove HIR's
type recovery paths, consolidate all type relations or check unused generic bodies.
The MIR debug dump is session-local. `wysna emit-mir` exports the versioned,
self-contained interchange, including a type arena; see [MIR design](MIR_DESIGN.md).

```mermaid
flowchart TD
    A["Wysna source"] --> B["Lexer and parser"]
    B --> C["AST"]
    C --> D["Declaration collection and type checking"]
    D --> E["Typed HIR"]
    E --> F["HIR verification"]
    F --> I["MIR lowering and verification"]
    I --> G["MIR C99 emission (default)"]
    G --> H["Native executable"]
```

The checker resolves overloads, constructors, modules, and generic specializations before
HIR reaches code generation. HIR preserves expression order and CoW assignment paths.
MIR lowering makes control flow, storage layout, and ownership transitions
explicit before C99 emission.

## 2. Core HIR Data Model

### 2.1 Identifiers & Metadata
```text
struct Span {
    file_id: u32
    line: u32
    col: u32
}

type SymbolId = u32   # Unique per lexical binding (local, param, global fn, module)
type TypeId   = u32   # Interned canonical type index (see TYPE_SYSTEM_DESIGN.md)
```

### 2.2 Top-Level Module & Declarations (`HirModule`)
```text
struct HirModule {
    file_path: str
    types: list[HirTypeDecl]       # Structs, Enums, Interfaces, Classes
    functions: list[HirFnDecl]     # Top-level functions and lowered methods
}

enum HirTypeDecl {
    Struct(TypeId, fields: list[HirFieldDef])
    Enum(TypeId, variants: list[str])
    Interface(TypeId, methods: list[HirMethodSig])
    Class(
        type_id: TypeId,
        superclass: TypeId?,
        interfaces: list[TypeId],
        fields: list[HirFieldDef],
        methods: list[HirMethodImpl]
    )
}

struct HirFnDecl {
    symbol_id: SymbolId
    name: str
    type_params: list[TypeId]      # Empty after v0.2 monomorphization
    params: list[HirParam]
    return_type: TypeId
    body: HirBlock
    span: Span
}
```

### 2.3 Statements (`HirStmt`)
| HIR Statement Node | Purpose |
| :--- | :--- |
| `HirLet(symbol_id, type_id, is_mut, init_expr, span)` | Local variable declaration with resolved `SymbolId` and `TypeId` |
| `HirBindTemp(temp_id, type_id, init_expr, span)` | Compiler-generated temporary enforcing strict `L→R` evaluation |
| `HirAssign(lvalue_path, value_expr, span)` | Assignment to a mutable local, index, struct field, or object field |
| `HirWhile(cond_expr, body_block, span)` | Loop with boolean-verified condition |
| `HirForList(elem_bindings, list_expr, body_block, span)` | Desugared list iteration over a cloned/retained `ListType` value |
| `HirReturn(value_expr, span)` | Explicit function/method return |
| `HirBreak(span)` / `HirContinue(span)` | Loop control transfer (statically verified `loop_depth > 0`) |
| `HirExprStmt(expr, span)` | Expression evaluated for side effects |

### 2.4 Expressions (`HirExpr`)
Every `HirExpr` contains `{ kind: HirExprKind, type_id: TypeId, span: Span }`:

| `HirExprKind` Variant | Lowered Semantics |
| :--- | :--- |
| `ConstInt(i64)` / `ConstFloat(f64)` / `ConstBool(bool)` / `ConstStr(str)` / `ConstNone` / `ConstUnit` | Typed scalar constants |
| `LocalRef(SymbolId)` / `ThisRef(TypeId)` | Direct reference to a local variable, parameter, or method receiver |
| `UnaryOp(UnaryKind, operand)` | `NegInt` (wrapping), `NegFloat`, `NotBool` |
| `BinaryOp(BinaryKind, left, right)` | Type-specialized operator (`AddInt`, `AddFloat`, `ConcatStr`, `DivIntTrunc`, `ModIntTrunc`, `EqVal`, `EqRef`, etc.) |
| `Sequence(statements, value)` | Ordered effects embedded at the expression site, followed by a value; preserves conditional evaluation for `and`, `or`, and `??` |
| `ConstructStruct(TypeId, fields: list[(FieldIdx, HirExpr)])` | Value-type `struct` initialization in canonical field declaration order |
| `ConstructEnum(TypeId, variant_idx: u32)` | Enum variant construction |
| `ConstructObject(TypeId, args: list[HirExpr])` | Unified heap-allocated `class` construction + `init` invocation (replaces both `Class(...)` and `new Class(...)`) |
| `ConstructList(elem_type: TypeId, elems: list[HirExpr])` | Homogeneous list literal construction |
| `ConstructDict(key_type: TypeId, val_type: TypeId, entries)` | Homogeneous dictionary literal construction (`key_type` verified non-float) |
| `ConstructTuple(elem_types: list[TypeId], elems: list[HirExpr])` | Tuple value construction |
| `DirectCall(fn_symbol: SymbolId, type_args: list[TypeId], args: list[HirExpr])` | Static function call |
| `BuiltinCall(BuiltinId, args: list[HirExpr])` | Typed runtime intrinsic (`Print`, `LenStr`, `LenList`, `LenDict`, `SliceStr`, `Ord`, `Chr`, `Bytes`, `ReadFile`, `WriteFile`) |
| `MethodCall(dispatch: DispatchKind, receiver: HirLValueOrExpr, method_name: str, args: list[HirExpr])` | Dispatched method call (`ListAppend`, `ListPop`, `ListClear`, `DictGet`, `ClassMethod`, `SuperMethod`, `InterfaceMethod`) |
| `FieldRead(kind: FieldAccessKind, object: HirExpr, field_name: str)` | Disambiguated `StructField` vs `ClassField` read |
| `IndexRead(kind: IndexKind, object: HirExpr, index: HirExpr)` | Disambiguated `ListIndex`, `StrScalarIndex`, or `DictLookup` (returns `V?`) |
| `TryPropagate(kind: TryKind, operand: HirExpr)` | Explicit early-return unwrap for `T?` (`TryOptional`) or `T ! E` (`TryResult`) |
| `Coalesce(opt_expr: HirExpr, fallback_expr: HirExpr)` | Short-circuiting optional unwrap (`??`) |
| `ElseHandle(operand: HirExpr, err_symbol: SymbolId?, handler: HirBlock)` | In-place `else err { ... }` recovery |
| `If(cond: HirExpr, then_block: HirBlock, elifs, else_block: HirBlock?)` | Typed conditional expression/statement |
| `Match(subject: HirExpr, arms: list[HirMatchArm])` | Exhaustiveness-checked pattern match |

---

### 2.5 Resolved error patterns

`HirErrorTypePattern(error_type_id, binding, type_id, span, type_name)` represents one
alternative in an error union. The alternative's `TypeId` controls runtime comparison,
and the binding receives that same type. `type_name` preserves source text for dumps; it
does not participate in nominal identity.

`HirEnumPattern` represents enum variants and Option/Result constructors. It cannot
represent error-union alternatives. The verifier rejects mixed representations and
unsupported error patterns before C emission.

## 3. Explicit Copy-on-Write (`HirCowPath`) Lowering

One of Wysna's defining semantic guarantees is that **mutating a nested value container never mutates aliased value bindings**, whereas mutating a field through a `class` instance mutates the shared reference object.

In Typed HIR, any mutating target (`a[i][j] = x`, `b.rows[0].append(1)`, `s.inner.count = 2`) is lowered to a structured `HirLValuePath`:

```text
struct HirLValuePath {
    root: HirLValueRoot              # MutableLocal(SymbolId) | ClassObjectExpr(HirExpr)
    steps: list[HirPathStep]         # Ordered outer-to-inner projection steps
}

enum HirPathStep {
    StructField(field_name: str, struct_type: TypeId)   # Detach struct if refcount > 1
    ListIndex(index_expr: HirExpr, elem_type: TypeId)   # Detach list if refcount > 1
    DictKey(key_expr: HirExpr, val_type: TypeId)        # Detach dict if refcount > 1
    ClassField(field_name: str, class_type: TypeId)     # Reference boundary: NO CoW detach
}
```

### Lowering Rule at Class Reference Boundaries
When the TypeChecker / HIR Builder constructs an lvalue path from an expression $E$:
1. It walks $E$ from innermost projection to outermost base.
2. If it encounters a `ClassField` projection on a `ClassType` receiver, **that class instance becomes the `ClassObjectExpr` root** of the mutable path (no `let mut` required on the variable holding the class reference, and no CoW detachment on the class instance itself).
3. Any `StructField`, `ListIndex`, or `DictKey` steps *inside* that class field still perform CoW detachment on the value containers stored inside the object.
4. If no `ClassType` boundary is crossed, the root must be a `MutableLocal(SymbolId)` declared with `let mut` (otherwise `W3001` is emitted), and every `StructField` / `ListIndex` / `DictKey` step detaches its container when `refcount > 1`.

---

## 4. Evaluation Order Lowering (`L→R` Sequence Points)

Per `SPEC.md` Section 5.4, Wysna guarantees strict left-to-right evaluation of all sub-expressions. Because C99 leaves function argument and binary operand evaluation order unspecified between sequence points, HIR lowering normalizes compound expressions into ordered statements. Effects that belong to an expression remain in a `Sequence(statements, value)` node and are emitted at that exact expression site. This is required for right operands of `and`, `or`, and `??`: the backend emits those sequences inside the conditional branch that evaluates the operand, so skipped operands cannot mutate state.

Ordinary call arguments and strict binary operands are still emitted left-to-right as ordered C statements before the consuming operation:

### Example
Surface Wysna code:
```wysna
let y = add( bump(x), bump(x) * 2 )
```
Lowered HIR block statements:
```text
HirBindTemp(_t1: int = DirectCall(bump, [LocalRef(x)]))
HirBindTemp(_t2: int = DirectCall(bump, [LocalRef(x)]))
HirBindTemp(_t3: int = BinaryOp(MulInt, TempRef(_t2), ConstInt(2)))
HirBindTemp(_t4: int = DirectCall(add, [TempRef(_t1), TempRef(_t3)]))
HirLet(y: int = TempRef(_t4))
```
Both `gcc` and `clang` are forced by `;`-terminated C99 variable declarations to execute `_t1`, `_t2`, `_t3`, and `_t4` in exact deterministic sequence.

---

## 5. Current role

Wysna Seed lowers verified typed HIR to verified MIR for native builds and C emission. The AST backend remains available as a differential oracle (`--backend=ast`). `wysna mir <file.wysna>` prints the verified MIR representation as JSON.


## 6. Verification boundary

HIR validation checks interned type IDs, declarations, parameters, blocks, expression types,
control-flow exits, lexical references, calls, constructors, enum payloads, and match
patterns. Generic constraints and synthesized constructors are checked before lowering.
Invalid internal nodes produce a structured W9999 compiler diagnostic with stage and
source location; unsupported nodes never silently become unit values.
