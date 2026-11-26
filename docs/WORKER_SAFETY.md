# Worker safety contract

Status: post-v0.9.0 candidate contract. The v0.9.0 release tag and its published artifacts remain unchanged; shipping updates requires fixed-point bootstrap, differential conformance, and green CI on the delivered commit.

## Verified boundary

`std/algorithms.parallel_map`, `parallel_batch`, `parallel_filter`, the mapper argument of `parallel_reduce`, and `std/async.spawn` accept closed, statically checkable callbacks: named functions, lambdas, or immutable aliases. Seed and Selfhost both inspect callback parameters, return types, captures, bodies, contracts, and transitive helpers. Empty lists and single-worker fallbacks are also verified.

`W2014` (`data.kind = worker_transfer`) identifies the `path` and `value_type` that cannot cross the worker boundary. Allowed transferable values are `int`/`float`/`bool`/`str`/`unit`/`none` and composites built strictly from transferable values (`list`, `dict`, `tuple`, `struct`, `enum`, optionals, and results including their error payloads). Classes, interfaces, `Weak`, opaque handles, and functions stored as values are rejected, even inside composites. A callback cannot construct local classes either: the cycle collector graph stays confined to the main owner.

`W2015` (`data.kind = worker_effect`) reports the rejection `reason` and boundary coordinates (`boundary_file`, `boundary_line`, `boundary_col`). Uncaptured global reads, host effects, FFI calls, `async` code, scheduler access, nested workers, mutable or unresolved callbacks, and indirect references to dispatch functions themselves are rejected. Explicit value captures are copied. Helper functions keep their own scope and cannot borrow caller captures to mask a global read.

This contract does not support effect polymorphism: a wrapper receiving an unknown callback and forwarding it to a worker is rejected. Builtins passed as callbacks require an inspectable lambda adapter. There are no attributes that bypass these checks and no macros that grant permissions.

## Ownership and costs

Before starting work, complete input and environment graphs are deep-copied per worker. A memo preserves internal aliasing within each copied graph; ARC buffers, CoW storage, and strings are never shared across owners. Results are copied again before publishing back to the caller, while normal CoW behavior continues inside each worker.

Snapshots enforce a depth limit of 256 and also reject cycles and non-transferable categories at runtime (`W4003`) as defense-in-depth for C consumers. UTF-8 literals inside a worker use independent storage so lazy Unicode indexing never mutates the main owner's static descriptor. Explicit byte lengths and embedded NUL bytes are preserved.

Transfer time and memory scale proportionally with the traversed graph; there is no guarantee of constant-cost capture, zero allocation, or bounded-latency destruction. `WVal` preserves ABI 3 and 8 bytes: `int` has Int64 semantics, a signed 48-bit immediate range, and boxed representation outside that range.

## Shared state inventory

- **Diagnostics, stack, and worker depth:** Native thread-local storage (TLS); each child interpreter owns separate TLS and execution context. Concurrent fatal failures print completely once via the diagnostic lock.
- **Value buffers and Unicode caches:** Confined to their owner through boundary snapshots. Type metadata, static names, and declarations are immutable. Instrumented hashes and counters use atomic operations; interpreter CoW telemetry uses a lock.
- **Quotas and accounting:** Limits are initialized before spawning threads, using atomic native counters and the owner lock in the interpreter. Boundary copies count toward instrumented allocation budgets.
- **Spawned tasks:** Registration and completion publishing are lock-protected; the function is copied before launch and the result before marking the task complete. Active worker counts are atomic. `parallel_map` outputs are written to disjoint slots and read after joins.
- **Class cycles and `Weak`:** Confined to the main owner. The cycle collector never runs on a worker thread, and static rules prevent constructing class graphs inside workers.
- **Scheduler, HTTP, filesystem, clock, process, FFI, and effect traces:** Not exposed to CPU callbacks. They follow cooperative scheduling and host policy paths and are not claimed safe for arbitrary concurrent access. Intrinsic identities depend on declaration, arity, and canonical library path, never on user filenames.

## Ordering and failures

`parallel_map`, `parallel_batch`, and `parallel_filter` preserve input element order. `parallel_reduce` maps in parallel and runs the combiner as a sequential left fold, without reassociating floating-point operations. This does not grant effect ordering guarantees: host effects are forbidden inside CPU callbacks.

A typed `Err` is a normal transferable result whenever its payload is transferable. `int("hola")`, `ord("")`, and `chr(-1)` raise `W4003`; `pop()` on an empty list raises `W4002`. Division by zero, invalid indices, budget exhaustion, and failed contracts are fatal traps (`W4001`–`W4005`), not recoverable values for `else err`. With active workers, C99 prints the diagnostic and terminates the process without joining threads that might block. The interpreter propagates the first fatal failure and stops workers at evaluation checkpoints; it does not run application cleanup after a trap or promise cancellation of blocking FFI calls.

Field contracts are validated at declaration scope, including across generics, imports, and struct spread updates, and field expressions evaluate once.

<a id="cpu-task-groups"></a>

## CPU task groups

`std/async.run_group` executes one callback per input with bounded concurrency and returns outcomes indexed by position, waiting for all workers to finish before returning. It shares the input snapshot, capture, and result boundaries of `parallel_map` and enforces `W2014` and `W2015` on callbacks.

```wysna
import "std/async" as tasks

fn square(value: int) -> int ! str {
    if value < 0 { return Err("negative input") }
    return value * value
}

let outcomes = tasks.run_group([2, -1, 3], 2, 1000, false, square) else error { [] }
print(outcomes)
```

The second argument caps active callbacks; the worker pool uses up to four threads (or fewer depending on platform limits or `WYSNA_PARALLEL_THREADS`). The third argument is a deadline in milliseconds from group start: `0` expires before starting callbacks and `-1` disables the deadline. Concurrency below `1` or a deadline below `-1` returns `TaskGroupError`.

Each index settles as `Completed(T)`, `Failed(E)`, `Cancelled`, or `DeadlineExceeded`. With `fail_fast = false`, typed errors from each callback are preserved; with `fail_fast = true`, the first observed error is kept and requests cancellation of remaining work. A task can also request group shutdown via `cancel_group()`. Deadlines and cancellation propagate to both the queue and active callbacks; long callbacks should poll `group_cancelled()` at safe points and return. Outcomes already published are retained, while callbacks exiting after shutdown record the stop reason for their index.

Output order matches input order; how many callbacks finish before shutdown depends on when an error or deadline is observed. Fatal traps remain process-fatal. Cooperative callbacks preserve isolated ownership semantics, and the [isolated Linux SDK](ISOLATED_SDK.md) profile can impose an external sandbox limit on the whole program.

### HTTP groups in the scheduler

`std/http.batch_get_group` applies the same per-index outcome contract to `GET` requests, keeping up to four transfers in flight (or the caller's smaller limit). A deadline or the first error under `fail_fast` cancels pending transfers and classifies queued entries. Received HTTP status codes count as successful responses (`Completed(Response)`), while transport or validation failures remain `Failed(HttpError)`.

HTTP groups run on the cooperative HTTP scheduler. Once completed, the body returned in `Completed(Response)` belongs to the caller and must be closed with `body.close()`. Loopback acceptance tests verify concurrency limits with two requests held on a barrier, result ordering, deadlines, and that queued requests cancelled before dispatch are never sent. Timers and coroutines preserve the `Task` interface.

## Isolation and remaining scope

These checks cover compiler-verified Wysna programs and instrumented runtime paths. MIR interchange is an internal trusted artifact that must be regenerated across compiler versions; its verifier does not replace source type checking or an OS sandbox. Handwritten C, arbitrary FFI, and external native binaries are not isolated by quotas, replay, ARC, or `W2014`/`W2015`. Executing untrusted code requires an OS-level process and containment boundary.

[CPU task groups](#cpu-task-groups) build on this transfer boundary with bounded concurrency, per-index outcomes, and cooperative cancellation. New numeric widths and mobile target profiles remain out of scope (see [Known limits](KNOWN_LIMITS.md)).

## Reproducible verification

```sh
python -m pytest -q tests/unit/test_worker_safety.py tests/unit/test_worker_transfer.py tests/unit/test_security_contracts.py
WYSNA_SANITIZE=1 python -m pytest -q tests/unit/test_worker_transfer.py
clang -std=c99 -O1 -g -pthread -I. -fsanitize=thread tests/runtime/worker_transfer.c -o /tmp/worker-transfer-tsan
WYSNA_MEM_CHECK=1 /tmp/worker-transfer-tsan
python tools/ci_conformance.py --profile=release
```

The C runtime fixture verifies deep copies, internal aliases, memo growth, embedded UTF-8/NUL handling, fallbacks, nested rejections, and simultaneous traps. Language tests run across all engines and pin `W4001`–`W4005`. TSan covers the runtime fixture on supported toolchains without guaranteeing universal race freedom.
