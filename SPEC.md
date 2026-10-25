# Wysna Language Specification

> **Status:** This specification defines the Wysna `0.5.0` release candidate. Sections 1–11 describe the normative grammar; sections 12–14 describe implementation contracts.
>
> **Implementations:** `Wysna Seed` is the reference implementation that determines whether a program is valid. `Wysna Compiler` is an independent self-hosted compiler; its supported subset is measured against the public release corpus. The parity matrix documents this boundary in [docs/ENGINE_PARITY_MATRIX.md](docs/ENGINE_PARITY_MATRIX.md).
>
> **Thesis:** Wysna is a compact, statically typed language for small native tools, with deterministic diagnostics and inspectable C99 output. This document defines the language syntax and semantics.

---

## 1. The Four Foundational Pillars

1. **Compact:** Maximum semantic intention expressed per token. No unnecessary syntactic ceremony.
2. **Deterministic:** Canonical syntax with zero whitespace ambiguity, zero hidden mutations, and zero implicit nullability.
3. **Statically Typed:** Strict type safety with full local inference and mandatory explicit types on function boundaries.
4. **Self-Hostable:** Minimalist micro-kernel designed to compile its own compiler to standard C without heavy external dependencies.

---

## 2. Lexical Inventory (Keywords)

The language defines the following reserved keywords:

```text
DECLARATIONS (6):
fn        let       mut       struct    enum      import

OBJECT-ORIENTED (9):
class     interface extends   implements new       this      super     public    private

CONTROL FLOW (10):
if        elif      else      for       while     in        match     return    break     continue

PRIMITIVE LITERALS (3):
true      false     none

LOGICAL OPERATORS (3):
and       or        not
```

*Note on built-in types:* Primitive types (`int`, `float`, `bool`, `str`, `unit`) and standard library symbols (`len`, `enumerate`, `bytes`) are prelude identifiers in the global namespace, **not** language keywords.

---

## 3. Lexical Structure

### 3.1 Identifiers
Identifiers must start with an ASCII letter (`a-z`, `A-Z`) or underscore `_`, followed by letters, digits, or underscores. Identifiers cannot match any of the reserved keywords.

### 3.2 Literals, Integer Arithmetic & IEEE-754 Floating-Point Semantics
* **Integers (`int`):** 64-bit signed two's complement integers (`-9_223_372_036_854_775_808` to `9_223_372_036_854_775_807`). Supports optional `_` digit separators:
  `0`, `42`, `-10`, `1_000_000`.
* **Integer Overflow & Division:** Integer addition (`+`), subtraction (`-`), multiplication (`*`), and unary negation (`-`) use **defined 64-bit two's complement wrapping semantics** (via unsigned cast arithmetic in C99, never undefined behavior). Integer division (`/`) truncates toward zero; division or modulo (`%`) by zero triggers a deterministic runtime panic.
* **Floats (`float` — IEEE-754 `binary64`):** IEEE-754 64-bit double-precision floating-point numbers. Literals must include digits both before and after the decimal point (`3.14`, `-0.5`, `100.0`; forms such as `.5` or `1.` are invalid):
  - **Signed Zero:** `+0.0 == -0.0` evaluates to `true`, and `+0.0 != -0.0` evaluates to `false`.
  - **`NaN` Semantics:** Following IEEE-754, `NaN` is unordered and irreflexive: `NaN == x` is always `false` (including `NaN == NaN`, which is `false`), `NaN != x` is always `true` (including `NaN != NaN`, which is `true`), and all relational comparisons (`<`, `<=`, `>`, `>=`) where either operand is `NaN` evaluate to `false`.
  - **Infinities (`+Inf` / `-Inf`):** Overflowing floating-point operations or conversions (`float("inf")`, `float("-inf")`) obey IEEE-754 ordering (`-Inf < finite < +Inf`, `+Inf == +Inf` is `true`, `-Inf == -Inf` is `true`). Doing arithmetic such as `Inf - Inf` yields `NaN`.
  - **Float Division by Zero:** Explicit division by zero (`x / 0.0` or `x / -0.0`) triggers a deterministic runtime panic (`RuntimeError: Division by zero`) rather than silently producing `NaN` or `±Inf`.
* **Booleans (`bool`):** `true` and `false`.
* **None (`none`):** The singular value of an empty optional.

### 3.3 Strings (`str`) & Unicode Scalar Semantics
* **UTF-8 Storage:** Strings are immutable and stored as UTF-8 in memory.
* **Unicode Scalar Value Indexing:** `len(s)`, `s[i]`, `slice(s, start, end)`, `ord(s)`, and `chr(c)` operate strictly on **Unicode Scalar Values (Code Points)** (`U+0000..=U+D7FF` and `U+E000..=U+10FFFF`), returning a single code-point string (`str`) or code-point count:
  ```wysna
  let s = "Zoë"
  s[3]    # returns "é" (4th Unicode scalar value)
  len(s)  # returns 4
  ```
* **Grapheme Clusters:** Extended grapheme cluster segmentation (e.g., multi-code-point emoji sequences joined by ZWJ) is not performed by core string indexing and is reserved for future Unicode library utilities.
* **Byte Access:** Low-level UTF-8 byte access is explicitly provided via `bytes(s) -> list[int]`.
* **Escape Sequences:** `\n`, `\r`, `\t`, `\\`, `\"`, `\0`, `\u{HHHH}` (1 to 6 hex digits).

### 3.4 Comments and Delimiters
* **Comments:** Only line comments beginning with `#` are supported. Documentation comments begin with `##`.
* **Blocks:** Braces `{}` delimit blocks for `if`, `for`, `while`, `fn`, `match`, `struct`, `class`, and `interface`.
* **Statement separators:** A physical newline (`LF`, `CRLF`, or `CR`) produces a `NEWLINE` separator after a token that can end an expression, provided it occurs outside parentheses and brackets. Literals, identifiers, `this`, `super`, closing delimiters, postfix `?`, `break`, and `continue` can end an expression. Blank lines and newlines after comments follow the same rule. A semicolon (`;`) is an explicit separator and may be used at the top level or inside a block.
* **Continuation:** Newlines inside `(...)` and `[...]` are ignored. A newline after an operator does not end the expression; a binary operator that continues the expression must appear at the end of the preceding line. A newline after `return` also does not end the return: if an expression appears on the next line, it is part of the return.
* **Branches:** A newline is allowed between an `if`/`elif` block and its `elif`/`else`, and between a fallible expression and its `else err` handler; that newline belongs to the same construct.

```wysna
let mut total = 0
let next_value = 3
total = total +
    next_value

process(item)
-offset # a separate unary-negative expression
```

---

## 4. Type System & Nullability

### 4.1 Primitive & Built-in Types
* `int`: 64-bit signed integer.
* `float`: 64-bit floating point.
* `bool`: Boolean truth value.
* `str`: Immutable UTF-8 text with code-point indexing.
* `unit`: Type indicating the absence of a meaningful value for functions that return nothing.

> **`any` is not a user-facing type.** The compiler uses it internally as the wildcard
> parameter type of variadic builtins (`print`, `len`, `enumerate`, ...) and as a
> placeholder during generic inference. It is deliberately unreachable from source:
> `let x: any = ...` is rejected with `W1001`. A bidirectionally-assignable top type
> would let a function declared `-> int` return a string unobserved, and would launder
> the invariance of `list[T]` and `Weak[C]`.

### 4.2 Explicit Nullability (`T?`)
* Types are **non-nullable by default**. Assigning `none` to a non-optional type is a compile-time error:
  ```wysna
  let name: str = none     # COMPILE ERROR
  let name: str? = none    # VALID: explicit optional
  ```
* Functions returning no value return `unit`:
  ```wysna
  fn greet(name: str) -> unit {
      print("Hello, ", name)
      return               # Bare return allowed only in unit functions
  }
  
  # Syntactic sugar: omitting return type defaults to -> unit
  fn log_msg(msg: str) {
      print(msg)
  }
  ```

In block functions and lambdas declared as `unit`—including functions with an omitted return type—the implicit tail expression is evaluated for its effects and discarded. An explicit `return` with an incompatible value is rejected. `unit ! E` preserves errors from its tail; a failed result does not become success when its value is discarded.

A bare `return` is one that is followed by `}`, `)`, `]`, `,`, `else`, `elif` or EOF.
It is **not** decided by line breaks: `return` followed by an expression on the next
line returns that expression, and the value may span lines.

### 4.3 Composite Collections
* `list[T]`: Dynamically sized contiguous homogeneous array (`[1, 2, 3]`).
* `dict[K, V]`: Associative key-value table (`{"key": 42}`).
  - **Reads:** `d[k]` yields `V` and panics with `W4002` when the key is absent. The
    optional form is `d.get(k)`, which returns `V?` and never panics. Wysna has no
    auto-narrowing, so a single optional-deducing operator for the common case would
    force every dictionary read through unwrapping ceremony.
  - **Key Domain & `float` Restriction (`W2010`):** Dictionary keys `K` must belong to Wysna's predictable key domain (`int`, `str`, `bool`, or `enum`). While some languages permit floating-point dictionary keys by defining custom canonicalization rules for `NaN` bit patterns and signed zeros (`+0.0` vs `-0.0`), Wysna deliberately restricts the dictionary key model for predictability and rejects `float` (and any composite type containing `float`) as a key type `K` at compile time with diagnostic `W2010` (`INVALID_DICT_KEY_TYPE`).
* `tuple[T1, T2, ...]`: Fixed-size heterogeneous immutable product type (`(1, "ok", true)`).

### 4.4 Sum Types (`enum`)
Closed enumerations for modeling variant states:
```wysna
enum TokenKind {
    Ident
    Number
    String
    Keyword
    Op
    Eof
}
```

### 4.5 Value-Type Records (`struct`) with Mandatory Named Initialization
`struct` defines pure Copy-on-Write value records:
```wysna
struct Token {
    kind: TokenKind
    value: str
    line: int
    col: int
}

# Named initialization is strictly required:
let tok = Token {
    kind: TokenKind.Ident,
    value: "foo",
    line: 1,
    col: 10,
}
```

### 4.6 Object-Oriented Programming (`class` & `interface`)
Alongside value-type `struct` and `enum`, Wysna supports Java-style reference-type classes, single inheritance (`extends`), multiple interface contracts (`implements`), visibility modifiers (`public` / `private`), constructors (`fn init`), and runtime polymorphism:

```wysna
interface Describable {
    fn describe() -> str
}

class Animal {
    public name: str
    private energy: int

    fn init(name: str, energy: int) {
        this.name = name
        this.energy = energy
    }

    public fn speak() -> str {
        return this.name + " makes a sound"
    }

    public fn get_energy() -> int {
        return this.energy
    }
}

class Dog extends Animal implements Describable {
    public breed: str

    fn init(name: str, breed: str) {
        super.init(name, 100)
        this.breed = breed
    }

    public fn speak() -> str {
        return this.name + " (" + this.breed + ") barks: Woof!"
    }

    public fn describe() -> str {
        return this.speak()
    }
}

let d: Describable = Dog("Rex", "Husky")
print(d.describe())
```
* **Reference Semantics vs Value Semantics:** `struct` values use Copy-on-Write (`W_STRUCT`), whereas `class` instances allocated via `ClassName(...)` (or `new ClassName(...)`) have reference identity (`W_OBJECT`) managed via deterministic reference counting.
* **Constructor Syntax:** Classes are constructed with `ClassName(...)`.
* **Functional Programming Synergy:** Interfaces with a single method (`interface`) and `fn(...) -> R` signatures are designed to interoperate seamlessly with functional programming constructs.

---

## 5. Expressions, Operators, Precedence & Evaluation Order

Evaluation follows an explicit 12-level precedence hierarchy:

| Level | Operator | Description | Associativity |
| :---: | :--- | :--- | :---: |
| **1** | `()` | Grouping, function calls | Left |
| **2** | `.` `[]` | Member access, indexing | Left |
| **3** | `-` `not` | Unary negation, logical NOT | Right |
| **4** | `*` `/` `%` | Multiplicative | Left |
| **5** | `+` `-` | Additive, string concatenation | Left |
| **6** | `..` `..=` | Exclusive range, inclusive range | Left (chained ranges are accepted; a range is a value, not a comparator) |
| **7** | `<` `<=` `>` `>=` | Relational comparisons | Left |
| **8** | `==` `!=` | Equality comparisons | Left |
| **9** | `and` | Short-circuit logical AND | Left |
| **10** | `or` | Short-circuit logical OR | Left |
| **11** | `??` | Null-coalescing (`a ?? fallback`) | Right |
| **12** | `=` | Assignment | Right |

### 5.1 Deterministic Left-to-Right Evaluation Order
Wysna guarantees **strict left-to-right ($L \rightarrow R$) evaluation order** across all composite expressions, independent of target C compiler argument ordering:
* **Function and Method Calls:** In `foo(a(), b(), c())`, the receiver (if any) is evaluated first, followed by `a()`, `b()`, and `c()` in strict left-to-right order before invoking `foo`.
* **Binary Expressions:** In `f() + g() * h()`, sub-expressions evaluate left-to-right within each operator node (`f()` before the product `g() * h()`, and `g()` before `h()`). Short-circuit operators (`and`, `or`, `??`) evaluate their left operand first and evaluate their right operand only when required.
* **Assignments:** In indexed or member assignments (`a()[b()] = c()` or `obj().field = val()`), the target container, index/member, and right-hand value are evaluated deterministically.
* **Composite Literals:** Elements of `list`, `tuple`, `dict`, and fields of `struct` literals are evaluated in lexical left-to-right appearance order.

Assignments produce the assigned value, including inside another expression. Target
indices and class receivers are evaluated once, before the right-hand side. The mutable
slot is resolved afterward; no pointer is kept to an element that RHS evaluation could
invalidate by growing a list. A class receiver preserves its identity even if the RHS
reassigns the variable that named it. Evaluated operands and arguments retain snapshots
of value types; classes retain identity. Literal fields are evaluated in source order,
while their layout follows declaration order.

A partial `match` used as a statement has a normal exit when no pattern matches. That
exit participates in return and constructor-initialization checks. Guards do not count
as exhaustive coverage. HIR also verifies functions without a final expression.

### 5.2 Equality Semantics (`==` and `!=`)
Equality is partitioned cleanly along the value vs reference boundary:
* **Primitives & Enums (`int`, `float`, `bool`, `str`, `unit`, `none`, `enum`):** Value equality (`"abc" == "abc"` is `true`).
* **Composite Value Types (`list[T]`, `dict[K, V]`, `tuple[...]`, `struct`):** Recursive structural value equality (`[1, 2] == [1, 2]` is `true`; `Point { x: 1, y: 2 } == Point { x: 1, y: 2 }` is `true`).
* **Reference Types (`class`):** Reference identity equality (`obj1 == obj2` is `true` if and only if both bindings point to the exact same allocated instance in memory).

### 5.3 Formal Desugaring of `?` (`T?` and `T ! E`)
1. **Type Level (`T?`):** Defines an optional type (official Wysna optional representation).
2. **Expression Level (`expr?`):** Postfix unwrapping and early-return propagation operator defined by explicit semantic desugaring:
   - **For Optional (`expr: T?`) inside a function returning `U?`:**
     ```wysna
     let x = expr?
     # Semantically equivalent to:
     # if expr is none -> return none; else -> unwrap value
     ```
   - **For Fallible Result (`expr: T ! E`) inside a function returning `U ! E`:**
     ```wysna
     let x = expr?
     # Semantically equivalent to:
     # if expr is err(e) -> return err(e); else -> unwrap ok value
     ```

---

## 6. Control Flow & Pattern Matching

### 6.1 Variables: Immutability by Default
* `let x = 42`: Immutable binding with local type inference.
* `let mut count = 0`: Mutable binding.
* Function parameters are strictly typed and immutable within the body. There is no
  `mut` modifier on a parameter; copy into a `let mut` local to mutate one. Value
  semantics make that copy cheap (copy-on-write) and unambiguous.

### 6.2 Loops
* **Element iteration:**
  ```wysna
  for item in items {
      print(item)
  }
  ```
* **Indexed iteration via tuple destructuring with `enumerate`:**
  ```wysna
  for i, item in enumerate(items) {
      print(i, item)
  }
  ```
* **Range iteration:**
  ```wysna
  for i in 0..10 {    # 0 to 9 (exclusive)
      print(i)
  }
  for i in 1..=10 {   # 1 to 10 (inclusive)
      print(i)
  }
  ```
  Bounds are evaluated once, from left to right. Direct loops use counters without
  allocating a list; a range used as a value remains `list[int]`. The 100-million-element
  limit and the overflow error for an inclusive `INT64_MAX` endpoint remain in effect,
  even if the body exits immediately.
* **While loops:**
  ```wysna
  while condition {
      # ...
  }
  ```

### 6.3 Exhaustive `match`
`match` must be exhaustive when switching on `enum` variants:
```wysna
match tok.kind {
    TokenKind.Number => {
        return parse_number()
    }
    TokenKind.String => {
        return parse_string()
    }
    TokenKind.Ident => {
        return parse_ident()
    }
    TokenKind.Keyword => {
        return parse_keyword()
    }
    TokenKind.Op => {
        return parse_operator()
    }
    TokenKind.Eof => {
        return done()
    }
}
```
* If an `enum` case is missing and no `else =>` fallback is provided, compilation fails with an exhaustiveness error.

---

## 7. Error Handling (`T ! E`)

Wysna does not use non-local stack-unwinding exceptions. Fallible functions declare their error type explicitly:

```wysna
fn read_file(path: str) -> str ! IoError {
    # ...
}
```

If `main` returns a `Result` and that result is an `Err`, the runtime reports it on
`stderr` as `W4003` and terminates with a non-zero status. Wysna has no stack unwinding, so
this is the only point where an error becomes visible to the process that started the
program; discarding it would make a failing program indistinguishable from a success
for `make`, CI or a shell script.

Handling strategies:
1. **Propagation via `?`:**
   ```wysna
   let content = read_file("src/main.wysna")?
   ```
2. **In-place Handling via `else err`:**
   ```wysna
   let content = read_file("src/main.wysna") else err {
       print("Failed to read file: ", err.message)
       return
   }
   ```

### Error union composition

A function signature can declare a closed set of error types after `!`:

```wysna
fn parse() -> Config ! ParseError | IoError
```

The union exists only in the error channel of `T ! E`; it is not a general union type.
It must contain at least two distinct alternatives. Supported alternatives are `int`,
`float`, `bool`, `str`, and non-generic `struct` or `enum` types. Classes, generic types,
and composite types whose concrete identity cannot be distinguished in the runtime value
are rejected. Duplicate alternatives are canonicalized.

`?` propagates the original error value without conversion when every source alternative
belongs to the destination set. For example, `ParseError` can propagate to
`ParseError | IoError`, but a function that declares only `ParseError` cannot propagate
a result that may also contain `IoError`. Errors are not implicitly converted.

`else err` binds `err` to the static error-channel set. A value-producing `match` must
cover every alternative with type patterns, such as `ParseError(problem) => ...` and
`IoError(problem) => ...`. A pattern accepts a binding or `_`; the full error value is
preserved and checked by runtime type identity. Qualified names from checked modules are
valid in signatures and patterns, for example `storage.ParseError(problem)`.

---

### Nested optional and result semantics

Parenthesized types group constructors: `(T?) ! E` is Result[Option[T],E], `(T ! E)?` is Option[Result[T,E]], and `(T ! E1) ! E2` is Result[Result[T,E1],E2]. Ungrouped `T ! E1 ! E2` associates to the right. Formatting preserves these distinctions.

Postfix `?` handles exactly the outer constructor. A second `?` on an optional payload is invalid inside a function whose outer return type is Result; use an explicit branch/recovery value instead. Error propagation requires the source error set to be a subset of the declared destination set; it never converts or flattens error payloads. A complete `Result[T, E1]` value can be assigned to or returned as `Result[T, E2]` only when `T` is identical and every `E1` alternative is present in `E2`; this preserves the tagged value without wrapping it in another `Ok`. Nested Result layers are never collapsed. Returning an inner Result as a success payload requires preserving its outer success layer. Bare values denote success, including when success and error types coincide.

Optional/Option and fallible/Result spellings share canonical type identity. Optional display is payload-transparent; explicit Some values follow the same display policy as sugar. Some(None) differs from None. Implicit conversions do not add/remove nullable depth; use explicit Some for nested optionality. Guarded match arms do not contribute unconditional coverage. Nested variant payload cases must be exhaustive.

## 8. Memory Model: Deterministic Reference Counting + Copy-on-Write

> **Core Value Semantics Guarantee:** *"Copying a Wysna value produces a semantically independent value. Internal storage may be shared through copy-on-write as an implementation optimization, but mutations must never be observable through another value alias."*

1. **Pure & Recursive Value Semantics:** To the programmer, collections (`list`, `dict`, `str`, `tuple`) and `struct` records behave as independent values, including when nested (`list[list[T]]`, `struct` containing `list[T]`, etc.). Mutating `b` or any nested component inside `b` never affects `a`:
   ```wysna
   let a = [[1], [2]]
   let mut b = a
   b[0].append(99)
   # a is still [[1], [2]]
   # b is [[1, 99], [2]]
   ```
2. **Deterministic Single-Threaded Reference Counting + CoW:**
   - Backing buffers use lightweight single-threaded reference counting (`int32_t refcount`) without atomic synchronization overhead.
   - Buffers are shared on assignment or parameter passing by incrementing `refcount`.
   - When a mutating operation occurs (`append`, `pop`, `clear`, indexed assignment, or field assignment on a `struct`), any buffer along the access path with `refcount > 1` is detached (`wysna_cow_detach`) before applying the mutation.
3. **Reference Types (`class`):**
   - Unlike `struct`, instances of `class` (`W_OBJECT`) have reference identity semantics: assigning `let b = a` shares the same underlying instance without CoW detachment on field mutation.
4. **Acyclic Value-Storage Subgraph:**
   - **The subgraph formed exclusively by value storage (`struct`, `enum`, `list`, `dict`, `tuple`) cannot contain cycles.**
   - Copy-on-Write by itself is an internal storage-sharing optimization and does not inherently guarantee acyclicity in arbitrary languages. Rather, pure Wysna value storage is guaranteed to form a directed acyclic graph (DAG) because:
     1. Wysna provides no raw pointers, addresses, or shared mutable references to value containers;
     2. Every composite literal construction and every mutating value operation (`xs[i] = v`, `xs.append(v)`, `s.field = v`) captures an independent semantic snapshot of `v` (detaching any shared value container along the target lvalue path before writing); and
     3. Consequently, no value container can ever be mutated to hold a direct or indirect value-storage path back to itself.
   - Note that a value (such as a `struct` or `list`) may contain a reference to a `class` instance without losing its own value semantics. If a `class` instance holds a field containing a `struct` that references that `class` instance (`Node(class) -> Wrapper(struct) -> Node(class)`), the cycle crosses a reference-type (`class`) identity boundary; the subgraph formed solely by value storage remains strictly acyclic.

---

### Weak class references

`Weak[C]` is an invariant value handle for a concrete class `C`. `weak(object)`
creates a non-owning reference; `upgrade(handle)` returns `C?` and acquires strong
ownership on success. Expired handles return None. Weak values cannot access
fields or methods directly and cannot be dictionary keys. Weak handles compare by
stable identity, even after expiration; copies preserve identity, while handles
for different objects remain distinct. Display is `weak(alive)` or `weak(expired)`.

Default non-optional weak class fields are independent empty expired handles;
optional weak fields default to None. Empty handles compare equal to their copies
and never upgrade. There is no implicit Weak[Derived]-to-Weak[Base] conversion.

Strong references still form unreclaimed cycles. Use weak back-references or
explicitly clear strong edges. The native control block outlives object destruction
until the last weak handle is released, and invalidates the object before field
cleanup. This runtime is single-threaded.

## 9. Module System

Modules are imported using file path strings. The imported module automatically binds to a namespace derived from the file's base name:

```wysna
import "lexer.wysna"
import "parser.wysna"

fn compile(source: str) {
    let tokens = lexer.tokenize(source)
    let ast = parser.parse(tokens)
}
```
* No auxiliary keywords (`as`, `from`) are required.

---

## 10. AI & Tooling Contract

1. **Canonical Formatter (`wysna fmt`):**
   - Every valid Wysna program has exactly one canonical formatted representation.
   - The formatter is idempotent: `fmt(fmt(source)) == fmt(source)`.
2. **Stable Diagnostic Codes & Structured JSON Diagnostics (`wysna check --json`):**
   - Every compiler and runtime error carries a stable `Wxxxx` diagnostic code categorized by phase:
     - **`W0xxx` — Lexical & Syntactic Diagnostics:**
       - `W0001`: Unexpected token or invalid character
       - `W0002`: Malformed numeric literal
       - `W0003`: Unterminated string literal
       - `W0004`: Invalid or unterminated string escape sequence
       - `W0005`: Invalid Unicode scalar escape sequence
       - `W0006`: Invalid syntax or missing delimiter
     - **`W1xxx` — Name Resolution, Scope & Module Diagnostics:**
       - `W1001`: Unknown identifier, symbol, or type
       - `W1002`: Duplicate declaration
       - `W1003`: Module resolution or circular import error
       - `W1004`: Invalid control-flow or lexical context (`break`/`continue`/`return`/`this`/`super`)
     - **`W2xxx` — Type System, Exhaustiveness & Error Channel Diagnostics:**
       - `W2001`: Type mismatch
       - `W2002`: Invalid assignment target
       - `W2003`: Non-exhaustive `match` expression
       - `W2004`: Invalid return type or missing `return` statement
       - `W2005`: Invalid call arity or non-callable target
       - `W2006`: Invalid optional (`?`) or fallible (`T ! E` / `else err`) propagation
       - `W2007`: Missing or invalid `struct` initialization field
       - `W2010`: Invalid dictionary key type
     - **`W3xxx` — Mutability, Ownership & OOP Encapsulation Diagnostics:**
       - `W3001`: Mutation of immutable value binding (`let` vs `let mut`)
       - `W3002`: Invalid member access/mutation or `private` visibility violation
       - `W3003`: Invalid class inheritance or unimplemented `interface` contract
       - `W3004`: Constructor safety violation (uninitialized fields or invalid `super` invocation)
     - **`W4xxx` — Runtime Execution Diagnostics:**
       - `W4001`: Division or modulo by zero
       - `W4002`: Index or key out of bounds
       - `W4003`: Runtime execution error
     - **`W5xxx` — Host Toolchain Diagnostics:**
       - `W5001`: C compiler command unavailable or invalid. This is an environment error, not a source type error or compiler invariant failure.
       - `W5002`: Invalid command usage or option.
       - `W5003`: Source file could not be read.
       - `W5004`: Requested output artifact could not be written or read back.
   - `wysna check --json <file.wysnaysna>` outputs deterministic machine-readable JSON:
   ```json
   {
     "code": "W2001",
     "severity": "error",
     "stage": "type",
     "message": "Type mismatch: cannot assign value of type 'none' to variable 'name' of type 'str'",
     "file": "example.wysna",
     "line": 12,
     "column": 9,
     "span": {
       "file": "example.wysna",
       "line": 12,
       "column": 9
     }
   }
   ```
3. **Versioned Tool Result (`--output-format=json-v1`):**
   - The opt-in v1 protocol emits exactly one JSON document on stdout. The legacy
     `--json` format remains unchanged and cannot be combined with v1.
   - `check`, `fmt`, `emit-c`, and `build` support v1. Unsupported commands and
     incompatible flags produce a `usage_error` envelope instead of silently falling
     back to legacy output.
   - The envelope has `schema_version`, `command`, `status`, `invocation_id`, tool and
     process exit codes, execution metadata, toolchain and target metadata, diagnostics,
     artifacts, and optional telemetry.
   - Successful `fmt` returns formatted source inline unless `--write` was requested;
     `emit-c` and `build` return the path and SHA-256 of the artifact they created.
     An artifact path must not overwrite its source file. Failed commands never list an
     existing output file as a newly produced artifact.
   - Native compiler/linker stderr is capped at 8,192 characters in a diagnostic and
     marked with `[tool output truncated]` when the cap is reached. Captured subprocess
     stdout is not mixed into the control stream.
   - A diagnostic uses stable code, severity, stage, message, primary span, related
     locations, typed data, and fixes. The primary span contains the exact source hash
     and UTF-8 byte offsets when its source and position are known. Existing point-only
     diagnostics use a zero-width range; unavailable locations are `null`.
   - File read failures use `W5003`, output artifact failures use `W5004`, and usage
     failures use `W5002`. Successful commands have `status: "ok"` and empty diagnostics.
     Type and syntax failures use
     `status: "diagnostic_error"`.

   ```text
   wysna check --output-format=json-v1 src/main.wysna
   wysna fmt --output-format=json-v1 src/main.wysna
   wysna emit-c --output-format=json-v1 src/main.wysna -o build/main.c
   wysna build --output-format=json-v1 src/main.wysna -o build/main
   ```

   ```json
   {
     "schema_version": "wysna.tool-result/1",
     "command": "check",
     "status": "ok",
     "invocation_id": "generated-unique-id",
     "tool_exit_code": 0,
     "process_exit_code": 0,
     "execution": null,
     "toolchain": {"compiler": "0.5.0"},
     "target": {"profile": "native-cli", "triple": null},
     "diagnostics": [],
     "artifacts": [],
     "telemetry": null
   }
   ```
4. **Stable AST Representation (`wysna ast file.wysna --json`):**
   - Emits a standardized JSON AST containing `"wysna_ast_version": "0.1"` for reliable agentic code analysis.

---

## 11. Standard Prelude & Built-in Methods

### 11.1 Global Prelude Functions
| Function | Signature | Description |
| :--- | :--- | :--- |
| `print(...)` | `(...) -> unit` | Prints space-separated arguments followed by a newline |
| `len(x)` | `(str \| list[T] \| dict[K, V] \| tuple[...]) -> int` | Returns code-point count for `str` or element count for collections |
| `enumerate(xs)` | `(list[T]) -> list[tuple[int, T]]` | Returns `(index, element)` pairs for indexed iteration |
| `dict_items(d)` | `(dict[K, V]) -> list[tuple[K, V]]` | Returns `(key, value)` pairs for dictionary iteration |
| `int(x)` | `(int \| float \| str \| bool) -> int` | Converts value to 64-bit integer |
| `float(x)` | `(int \| float \| str \| bool) -> float` | Converts value to 64-bit float |
| `str(x)` | `(any) -> str` | Converts any runtime value to its canonical string representation |
| `ord(s)` | `(str) -> int` | Returns the Unicode code point of the first character in `s` |
| `chr(c)` | `(int) -> str` | Returns a single-code-point UTF-8 string for code point `c` |
| `slice(x, a, b)` | `(str \| list[T], int, int) -> str \| list[T]` | Returns code-point substring or sub-list in `[a, b)`. Preserves the container kind; out-of-range bounds are clamped and an empty result is returned when `b <= a` |
| `join_strings(parts, separator)` | `(list[str], str) -> str` | Joins strings with one result allocation and preserves NUL; O(output bytes + parts) |
| `bytes(s)` | `(str) -> list[int]` | Returns raw UTF-8 unsigned bytes (`0..=255`) of `s` |
| `args()` | `() -> list[str]` | Returns the runner name followed by command-line arguments passed to the program |
| `read_file(p)` | `(str) -> str ! IoError` | Reads a UTF-8 file from disk or returns `IoError` |
| `write_file(p, c)` | `(str, str) -> unit ! IoError` | Writes UTF-8 content `c` to path `p` or returns `IoError` |

`read_file` preserves LF/CRLF line endings and NUL bytes in UTF-8 content. Converting
bool to a number produces 0 or 1. `float(str)` accepts ASCII decimals, signs, exponents,
leading and trailing ASCII whitespace, and case-insensitive `nan`, `inf`, or `infinity`.
It rejects Unicode digits, hexadecimal input, suffixes, and NUL with `W4003`; parsing does
not depend on Python's grammar.

For the interpreter, `wysna run <file.wysna> -- <args...>` makes `args()[0]` equal to
`"wysna"` and places each value after `--` after it. Relative paths used by `read_file`
and `write_file` are resolved from the process working directory that launched Wysna.
Native binaries receive the operating system's argument vector, including its executable
name in `args()[0]`.

### 11.2 Built-in Collection Methods
* **`list[T]`:**
  - `xs.append(item: T) -> unit` (mutating; triggers CoW detach if shared)
  - `xs.pop() -> T` (mutating; removes and returns last element)
  - `xs.clear() -> unit` (mutating; removes all elements)
* **`dict[K, V]`:**
  - `d.get(key: K) -> V?` (returns `Some(V)` if present, or `none`)
  - `d.keys() -> list[K]` (returns list of keys in insertion order)
  - `d.values() -> list[V]` (returns list of values in insertion order)

---

## Pattern bindings, cyclic display and recovered diagnostics

Bindings in one pattern must be unique at every depth. An enum pattern's nominal owner
is checked within tuples and payloads as well as at the root. Grouping does not change
compatibility. Formatting preserves tuple-pattern grouping.

`str` and printing render an active ancestor class as `<cycle>`. An alias encountered
in independent branches renders fully. This finite display rule does not collect cycles.

`wysna check --json` may report multiple syntax errors in `diagnostics`, preserving the
existing fields of the first error. Recovery is diagnostic only: no recovered tree is
checked or executed. The diagnostic budget is 100 errors per parse.
