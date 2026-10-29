# Ownership and Lifetime Model

**Status:** Current runtime and compiler ownership contract  
**Scope:** `Wysna Seed Typed HIR` (default), `Wysna Seed AST C99` (oracle), `Wysna Compiler C99` (`selfhost/compiler.wysna`), and C99 Runtime (`runtime/wysna_runtime.h`)

---

## 1. Design Goals

Wysna uses **deterministic single-threaded Reference Counting (RC) + Copy-on-Write (CoW)** without a tracing garbage collector and without exposing user-facing lifetime or borrow annotations.

The compiler enforces deterministic lifetime management across **all control-flow paths**:
1. Normal lexical block exit (`}`).
2. Early and final `return expr` statements (preserving returned values across cleanup).
3. Postfix error/optional propagation (`expr?`) on both `none` and `err` branches.
4. Loop exits (`break` and `continue`) across nested blocks down to the enclosing loop boundary.
5. Temporary expressions (`_tN`) created during left-to-right expression evaluation.
6. Reassignments (`x = expr`, `x[i] = expr`, `x.f = expr`), including self-assignments (`x = x`, `x = x[0]`).

---

## 2. Internal `ValueOwnership` Classification

Although Wysna surface syntax has no ownership annotations, the C99 code generators (Wysna Seed and Wysna Compiler) classify every C expression yielding a `WVal` into one of three internal ownership states:

```text
enum ValueOwnership {
    BORROWED      # Unowned view of an existing binding/parameter/field (e.g., local variable `x`, parameter `p`)
    OWNED         # Freshly allocated or retained WVal bound to a scope-tracked temporary `_tN` (refcount >= 1)
    TRANSFERRED   # Ownership moved out of a temporary (`wysna_move(&_tN)`) or newly cloned (`wysna_clone(expr)`) into a destination
}
```

### Ownership Transition Helper (`_own_expr`)
When a value is stored into a new variable (`let x = ...`), assigned (`x = ...`), or prepared for return (`return ...`):
- If the source expression is an `OWNED` temporary (`_tN`) registered in the current `CleanupScope`, the compiler emits **`wysna_move(&_tN)`** (`OWNED` $\rightarrow$ `TRANSFERRED`). `wysna_move` copies the `WVal` struct and overwrites `_tN` with `wysna_unit()` (`W_UNIT`), transferring the existing +1 reference count in $O(1)$ time with **zero redundant `retain`/`release` traffic**.
- If the source expression is a `BORROWED` reference (e.g., local variable `y`, parameter `p`), the compiler emits **`wysna_clone(y)`** (`BORROWED` $\rightarrow$ `TRANSFERRED`), incrementing `refcount` by +1 so the new binding owns an independent reference while `y` remains valid for its own scope cleanup.

---

## 3. `CleanupScope` Stack & LIFO Destruction Invariant

Both Wysna Seed (`CCodeGen.py`) and Wysna Compiler (`selfhost/compiler.wysna`) maintain an explicit lexical scope stack:

```text
struct CleanupScope {
    kind: str           # "fn" | "loop" | "block"
    vars: list[str]     # C WVal variable/temporary names declared in this scope
}
```

### 3.1 Registration Rules
- **Function Parameters:** Remain `BORROWED` for the duration of the call (owned by the caller's frame) and are **not** registered in the callee's `CleanupScope`. If a parameter is mutated in-place (or via `this`), CoW or object mutation operates on the target slot.
- **Local Bindings (`let x = ...`):** Initialized via `_own_expr(rhs)` and registered in the innermost `CleanupScope`.
- **Heap-Producing Temporaries (`_tN`):** Function calls, collection literals (`wysna_make_list`, `wysna_make_dict`, `wysna_make_tuple`), `struct` literals (`wysna_make_struct`), `class` instantiations (`wysna_new_Class`), string operations, and `wysna_unwrap_ok` are bound to `_tN` and immediately registered in the innermost `CleanupScope`.
  - *Note:* Auxiliary C initializer arrays use distinct prefixes (`_arrN`, `_kN`, `_vN`) so that `_tN` exclusively denotes scope-managed `WVal` temporaries.

### 3.2 Four Deterministic Exit Rules (LIFO Order)

1. **Normal Scope Exit (`}`):**
   When closing a C lexical block (`if`, `elif`, `else`, `while`, `for`, `match` arm, or function body), the top `CleanupScope` is popped and `wysna_release(v);` is emitted for each registered variable in **reverse declaration order (LIFO)**.

2. **`return expr` (Return Transfer Protocol):**
   To prevent releasing a local variable before returning it (e.g., `let x = [1, 2, 3]; return x`), `return` executes a 4-step protocol:
   ```c
   WVal _ret0 = wysna_clone(x);   /* or wysna_move(&_tN) if returning a temporary */
   /* Emit LIFO wysna_release(...) for ALL active scopes down to "fn" (without popping for subsequent unreachable code) */
   wysna_release(x);
   return _ret0;              /* or return wysna_ok(_ret0); in fallible T ! E functions */
   ```
   Because `_ret0` holds its own +1 reference (or moved reference from `_tN`), releasing `x` and all active temporaries in LIFO order safely decrements the scope's references while `_ret0` survives intact with `refcount >= 1`.

3. **Postfix Propagation (`expr?`):**
   Evaluating `expr?` binds the operand to a scope-tracked temporary `_t0 = _own_expr(op);`:
   - **On `W_NONE` (`T?` propagation):** Emits LIFO `wysna_release` across all active scopes down to `"fn"` (which includes `_t0` itself) and returns `wysna_none()`.
   - **On `W_ERR` (`T ! E` propagation):** Clones `_err = wysna_clone(_t0);`, emits LIFO `wysna_release` across all active scopes down to `"fn"`, and returns `_err`.
   - **On `W_OK` / non-`none`:** Extracts the payload (`_unw = wysna_unwrap_ok(_t0)` or `_t0`), registers `_unw` in the current `CleanupScope`, and continues execution.

4. **`break` and `continue` (Loop-Bounded Cleanup):**
   Emits LIFO `wysna_release` for every `CleanupScope` from the innermost scope outward **up to and including the nearest `"loop"` scope**, then emits `break;` or `continue;`. Variables in scopes enclosing the loop remain untouched.

---

## 4. Safe Reassignment & Slot Mutation (`wysna_assign` / `wysna_slot_assign`)

Reassigning a mutable local variable (`x = rhs`) or container/struct slot (`a[i] = rhs`, `s.f = rhs`) must release the old value stored in the destination while remaining safe under self-assignment (`x = x`, `x = x[0]`):
1. The right-hand side is evaluated and converted to an owned value **first** (`_own_expr(rhs)`).
2. `wysna_assign(&x, owned_rhs)` releases the previous value in `*dst` (`wysna_release(*dst)`) and writes `owned_rhs` into `*dst`.

---

## 5. Runtime RC Instrumentation & Leak Verification (`WYSNA_MEM_CHECK=1`)

In `runtime/wysna_runtime.h`, reference-counted payloads (`WStringBuf`, `WListBuf`, `WDictBuf`, `WTupleBuf`, `WStructBuf`, `WBoxBuf`, `WFunctionBuf`, and `WPointerOwner`) use signed 32-bit strong-reference counts. `W_OBJECT` uses the separate strong count in `WWeakControl`; that control block also tracks weak references. Immortal string literals use a `-1` sentinel and bypass retain/release accounting. Heap allocations participate in global telemetry counters:
- `wysna_g_alloc_count`: Total heap buffer allocations (`refcount = 1`).
- `wysna_g_free_count`: Total heap buffer deallocations when `refcount` reaches `0`.
- `wysna_g_retain_count`: Total `wysna_clone` increments on heap buffers.
- `wysna_g_release_count`: Total `wysna_release` decrements on heap buffers.
- `wysna_g_live_objects`: Currently live heap buffers (`wysna_g_alloc_count - wysna_g_free_count`).

### Safety Invariants
1. **Refcount Underflow / Double-Free Detection:** Both `wysna_clone` and `wysna_release` assert `refcount > 0`. Upon deallocation, `refcount` is poisoned to `-1` before `free()`. Any subsequent `wysna_clone` or `wysna_release` on a freed buffer immediately aborts with `[W4003] Runtime error: memory safety violation (refcount underflow / double free)`.
2. **Retain Overflow Detection:** Retain operations reject a count of `INT32_MAX` before incrementing it, including the strong and weak fields in `WWeakControl`. The runtime exits with `[W4003]` instead of invoking signed-overflow undefined behavior. Releases do not use this guard because releasing a valid maximum count is safe.
3. **Zero-Leak Exit Verification (`WYSNA_MEM_CHECK=1`):** At the end of `main()`, after releasing top-level and global bindings, `wysna_runtime_cleanup()` checks `getenv("WYSNA_MEM_CHECK")`. When set to `"1"`, if `wysna_g_live_objects != 0` or `wysna_g_alloc_count != wysna_g_free_count`, the process aborts with `[W4003]` and prints exact allocation/free/live counters.

This check is intentionally opt-in. It reports logical Wysna runtime buffers only, runs during
normal cleanup, and does not collect strong cycles. A runtime panic calls `exit()` and
does not reach this check. Development and CI use ASan/LSan separately for byte-level
memory diagnostics; `WYSNA_MEM_CHECK` is not a substitute for those tools.

---

## 6. Ownership across HIR and MIR

HirCCodeGen is the default native emitter; the AST emitter and Wysna Compiler are independent
implementations. Each uses lexical cleanup scopes to release owned temporaries on normal
exits. MIR makes retain, transfer, and release operations explicit, verifies them, and feeds
the default C99 emitter.

## Managed strings and constructor contracts

The runtime ownership contract for strings is: `wysna_str` borrows static storage; `wysna_owned_str` consumes a heap buffer and creates its RC owner; `wysna_copy_str` copies borrowed input such as argv. Dynamic strings participate in clone/release and allocation counters. `wysna_add` and `wysna_index_get_owned` results are registered owned temporaries in both native backends and Wysna Compiler. The borrowed `wysna_index_get` remains restricted to internal projection paths; dictionary value queries use the owned optional path.

`wysna_ok`, `wysna_err` and `wysna_enum_payload` consume their payload. Generated constructor calls move registered temporaries or clone borrowed arguments. `wysna_unwrap_optional`, `wysna_unwrap_ok`, `wysna_unwrap_err` and `wysna_unwrap_success` return owned values; peek helpers borrow. Class fields and collection slots own their stored values. Leak counters include string owners; LSan remains necessary to find allocations outside the instrumented ownership model.

## Runtime helper inventory and evaluation contract

The following contracts supersede older blanket claims in this document. Ownership
is an emitter convention, not a dedicated HIR ownership type or a formal proof.
A borrowed argument stays valid for the whole helper call. An owned result carries
one release obligation, including when it happens to be an immediate scalar.

### Helpers that consume ownership

- `wysna_release` and `wysna_release_array` consume one reference per value/array element.
- `wysna_move` transfers the slot's ownership and resets it to unit.
- `wysna_assign` consumes its owned source, releases the old destination, stores the
  source, and returns a **borrowed** view of that destination. The caller must
  protect a self-aliasing source before calling it.
- `wysna_ok`, `wysna_err`, `wysna_enum_payload` consume their payload; `wysna_wrap_result` consumes
  its input and returns it or a new success wrapper. They do not clone payloads.
- `wysna_owned_str` consumes a malloc buffer; `wysna_copy_str` borrows bytes and copies them.

### Borrowed input, owned output

- `wysna_clone` retains managed storage. Immediate values and borrowed static strings
  require no retain, and release is a no-op for them.
- `wysna_unwrap_optional`, `wysna_unwrap_ok`, `wysna_unwrap_err`, `wysna_unwrap_success` retain their
  returned payload; the source wrapper may be released independently.
- `wysna_index_get_owned` and `wysna_dict_get` retain their result. Present dictionary
  values are wrapped, including present None. String indexing returns an owned value: a single ASCII character uses static storage; other characters use an independently managed buffer. The caller releases both the same way.
- `wysna_make_list`, `wysna_make_tuple`, `wysna_make_dict`, `wysna_make_struct`, `wysna_make_object`
  borrow their initializer arrays and clone the stored values. `wysna_make_range`
  creates an owned list. Field/variant/type names come from static generated C.
- `wysna_dict_keys`, `wysna_dict_values`, `wysna_builtin_bytes`, `wysna_builtin_enumerate`,
  `wysna_builtin_dict_items` create owned containers and retain contained values.
- `wysna_add` borrows operands; string addition returns owned storage. Numeric results
  from add/sub/mul/div/mod/neg/lt/le/gt/ge are immediate values.
- `wysna_builtin_str`, `wysna_builtin_chr`, `wysna_builtin_slice` return owned strings.
  `str(existing_string)` retains its buffer. `wysna_builtin_read_file` and
  `wysna_builtin_write_file` return owned results, including owned error payloads.
- `wysna_builtin_args` returns an owned list of copied strings; `wysna_init_args` only
  borrows process argv until copying. `wysna_to_cstr` returns malloc storage requiring
  `free`, not `wysna_release`; print/eprint and string conversion consume that buffer.

### Short-lived borrows and mutable slots

- `wysna_peek_optional`, `wysna_peek_ok`, `wysna_peek_err`, `wysna_member_get` return borrowed
  payloads. Keep the owner alive and unchanged until cloning/consuming the borrow.
- Legacy `wysna_index_get` borrows list/dict elements but owns a string-index result.
  Generated value reads use `wysna_index_get_owned`; Wysna Compiler retains the legacy expression
  only as lvalue metadata, without executing a discarded value read.
- `wysna_index_ref` and `wysna_member_ref` borrow the root slot, may detach value storage,
  and return a mutable slot pointer. Indices are evaluated once into stable values.
  Slot pointers cannot be cached across unrelated mutations of their container.
- `wysna_list_append` borrows the item and clones it into the destination, detaching
  as needed. `wysna_list_pop` transfers the removed element's ownership to its caller.
  `wysna_list_clear` releases removed elements. All three borrow the destination slot.
- `wysna_list_detach`, `wysna_dict_detach`, `wysna_struct_detach` replace the borrowed slot's
  buffer and clone shared children; callers retain ownership of the root slot.
- `wysna_unit`, `wysna_none`, `wysna_int`, `wysna_float`, `wysna_bool`, `wysna_enum` produce immediate or
  payload-free values. `wysna_str` borrows static bytes. `wysna_eq`, `wysna_is_none`,
  `wysna_builtin_name`, UTF-8 helpers, len/int/float/ord borrow inputs and return
  scalars. Print/eprint borrow arguments and return unit.

### Generated calls, evaluation and exits

Function/method arguments and `this` are borrowed from the caller, which retains
snapshots through the call. Parameters are immutable bindings; class identity
remains shared. Callees return owned values. Dispatchers forward that contract;
constructors return owned objects and clone stored fields.

Before evaluating later operands, emitters materialize earlier borrowed or lazy
values into registered temporaries. This covers arguments, binary left operands,
read receivers, collection entries and struct fields. Field reads retain their
value immediately. Collection initialization evaluates key then value per entry.
Already-owned temporaries are reused. These snapshots can increase RC/CoW traffic;
This cost should be measured before changing temporary lifetimes.

Assignments evaluate indices and class receivers exactly once, before the RHS. They then protect the RHS and resolve the mutable slot. A list may reallocate while evaluating the RHS, so the compiler must not keep a pointer to an element. Class receivers are retained before the RHS and preserve identity. The expression returns the assigned value, including in composed paths. AST, HIR, and Wysna Compiler follow this contract.

Return and propagation protect the escaping value before LIFO cleanup. Break and
continue clean through the nearest loop; the for iterable belongs to its outer
scope. Lazy and/or/?? branches allocate and release only when executed. Empty
constructors and discarded ranges are registered just like nonempty constructors;
a fresh empty list stored directly into a global can transfer without a temporary.

### Reproducible acceptance

`python3 -m tests.unit.test_ownership_control_flow` compares the interpreter with AST C, HIR C
and standalone Wysna Compiler emission. All three native binaries run under ASan/UBSan and
WYSNA_MEM_CHECK; Linux additionally enables LSan. The fixture covers retained fields
across later mutation, argument/collection order, local field/index returns,
temporary receivers, self-assignment, append of an existing element, nested loop
exits, optional/result propagation in match, failing assignment targets, handler
payloads, short-circuit effects, coalesce and discarded constructors.

Strong reference cycles remain unsupported. Explicit process exit and runtime
panics terminate the process rather than unwinding lexical scopes; they are not
zero-leak acyclic success cases. Allocation failure and concurrent RC are outside
this contract. Wysna Compiler support remains limited to the fixtures documented in the [engine parity matrix](ENGINE_PARITY_MATRIX.md).

## Weak references and class control blocks

Class ownership is recorded in a control block with strong and weak counts and a
nullable object pointer. One implicit weak reference protects the block while the
object is alive. `wysna_clone`/`wysna_release` update strong counts for objects and weak
counts for handles. At strong zero, invalidate the object pointer, release fields,
destroy object storage and drop the implicit weak reference. A self-stored weak
handle therefore cannot free its control block during object teardown.

`wysna_builtin_weak` borrows a class reference and returns one owned weak handle.
`wysna_builtin_weak_owned` consumes the evaluated language-call snapshot immediately,
so weak creation does not accidentally keep its target alive until scope cleanup.
`wysna_builtin_upgrade` borrows a weak handle and returns a retained strong object or
None. `wysna_empty_weak` owns a new expired control block for a default weak field;
constructor initializer arrays release their temporary defaults after storing
clones. Weak identity remains stable until the final handle is released.

Memory instrumentation counts control blocks as managed allocations. Class
objects therefore have separate object/body and control-block obligations. CoW
byte accounting uses the current ABI and includes the changed struct header size.

`test_weak_references.py` isolates the unbroken strong cycle in `tests/memory/`:
its leak is expected only for that subprocess. Broken cycles and weak back-reference
cases must have zero leaks. Linux keeps LSan enabled and additionally requires its
leak report for the negative case; no suite-wide leak suppression is introduced.
The interpreter uses Python weak references and identity sentinels. Python GC may
collect strong cycles; native RC behavior is the authority for the cycle test.

The isolated Linux cycle negative sets LSAN_OPTIONS=use_stacks=0:use_registers=0
to ignore stale machine roots after create_cycle returns. Success cases retain
normal root scanning and leak detection. This changes conservative reachability
for the expected-leak process only; it does not suppress leaked allocations.

---

## Runtime memory-safety validation

The runtime safety checks require:
1. **Continuous Sanitizer Matrix:** The test suite is exercised with Clang AddressSanitizer (ASan), UndefinedBehaviorSanitizer (UBSan), and LeakSanitizer (LSan) in CI.
2. **Zero Unintended Leaks:** All acyclic data structures, CoW path-copying mutations, monomorphized generic containers, and weak-reference broken cycles must exhibit zero memory leaks.
3. **Deterministic Scope Cleanup:** The `CleanupScope` LIFO unwinding guarantees that memory is released immediately at scope exits across the supported execution engines.



## Cyclic formatting and runtime ABI

Formatting uses an iterative traversal. A class already active on the current ancestor
path renders as `<cycle>`; aliases reached in independent branches render fully. This
prevents recursive formatting from failing to terminate, but does not collect ARC cycles.

Runtime ABI v2 places string data, owner and byte length inside the string variant.
WVal occupies 32 bytes on the checked arm64 ABI. Generated C and adapters that depend
on its internal layout must be rebuilt with the matching header. The scalar/opaque
external C ABI keeps its declared signatures.

`std/resources` provides checked borrowing, idempotent close and explicit scopes.
Raw aliases remain unsafe after close. Panic, exit and process termination do not unwind
resource scopes. See [Standard library](STDLIB.md).

## Trusted standard host owners

File and Bytes store an owned opaque value: address plus a shared runtime owner
with a trusted close function. Native clone/release retains/releases that owner;
last release closes the checked host ID. The reference engine retains the same
owner with WysnaPointer references. No user finalizer is invoked. Raw external opaque
values keep their original non-owning scalar ABI. Bytes.borrow explicitly strips
ownership, so retaining a borrowed ID does not extend the allocation lifetime.

Explicit close is idempotent and affects every alias. Automatic close is a fallback;
observe explicit close status when a write/flush error matters. Strong cycles and
raw FFI disposal callbacks remain subject to their documented explicit discipline.
Host handle/storage accounting is separate from WVal allocation accounting.
