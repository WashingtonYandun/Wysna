# Service foundations and acceptance contracts

The synchronous toolkit supports bounded file tools and an HTTP/HTTPS client. An
experimental local JSON server now supervises isolated native Wysna worker processes.
It is a pilot for application shape and failure boundaries, not a general-purpose
production server. The runtime still has no language-level scheduler, asynchronous
sockets or cancellation. The HTTP client and service host are not network or OS
sandboxes; host permissions and deployment isolation remain outside their contracts.

## Implemented resource boundary

Standard File and Bytes handles have trusted runtime owners. Returning an owner,
storing it in a container or capturing it in a lambda retains its lifetime. Explicit
close invalidates the checked ID for every alias. Borrowing does not create an owner.
Raw FFI disposal remains explicit; arbitrary callbacks never run from ARC release.
There is no strong-cycle collector and no language-level panic unwinding.

Run `python3 -m tests.unit.test_foundations` to execute README examples, unit-result
parity and 10,000/100,000 request iterations in each engine. Every completed request
must restore host handles and buffer storage to baseline. Logical object counts,
host counts and RSS are distinct; stable counters are necessary but not a measured
RSS or latency SLO. Production admission additionally needs RSS plateau and latency
measurements with a fixed working set on each deployment platform.

## JSON pilot contract

`std/json` provides a bounded parser for the first service pilot. `json.parse` limits
input to 1 MiB, nesting to 32 arrays/objects, and the parsed tree to 100,000 values.
`json.parse_with_limits` permits per-call budgets up to hard ceilings of 16 MiB,
32 levels and 100,000 values. Wysna `str` values are valid UTF-8 by the language
contract; JSON escapes additionally require paired UTF-16 surrogates. Errors carry
Unicode-scalar offsets, trailing input is rejected, duplicate keys keep the last
value, and numeric lexemes remain text until callers choose a conversion.

The standard-toolkit gate compares parser acceptance and decoded values with Python's
independent `json` implementation using exact decimal parsing and last-value duplicate
semantics. Its corpus includes malformed inputs, surrogate policy, depth limits and
custom byte/node budgets. Any HTTP integration must retain explicit response-size and
JSON parsing budgets.

## HTTP client pilot

`std/http` is synchronous and uses libcurl in native output and PycURL in the
interpreter. Certificate and hostname verification stay enabled. Requests have a
30-second total timeout and a 5-second connection ceiling; request/response bodies
are limited to 1 MiB and each header block to 32 KiB/100 fields. The caller may
lower the timeout and response limit. Redirects, retries and streaming are off.
HTTP error statuses remain ordinary responses. Both adapters require libcurl 8.3.0
or newer; native output also requires development files, while interpreted programs
require the optional `http` extra. The minimum version avoids a historical
unbounded-response-header issue in older libcurl releases.

The interpreter and native adapters share the public limits and status/error
contract. Their underlying host libraries can still differ by version and platform;
the integration corpus exercises both with a loopback fixture. This pilot is not
evidence of a production server, an SLO, or an enforced network capability.

## Local service pilot

`wysna service serve` uses the maintained `aiohttp` HTTP server adapter from the
optional `wysna[service]` extra. It binds only to a literal loopback IP address.
The adapter owns HTTP parsing; Wysna does not implement its own HTTP parser. Defaults
are four isolated workers, a queue of 32 requests, 64 open connections, a 32-connection
listen backlog, an 8 KiB request line, at most 100 headers, a 32 KiB aggregate header
limit, a 320-byte maximum header line, 1 MiB request and response bodies, a 15-second
request budget, a 5-second initial-request and keep-alive timeout, and a 10-second
shutdown budget. The per-line limit ensures the maintained adapter cannot allocate
unbounded header totals before the host checks the aggregate limit. Worker and queue
counts can be lowered or raised only to fixed ceilings (8 workers and 32 queued
requests). Saturation returns `503`.

The host writes a bounded UTF-8 JSON envelope to a unique temporary directory,
then starts a new native Wysna process for that one request. The worker reads the path
from `args()[1]` and writes one JSON object to stdout with exactly `status` and `body`
fields. The host caps stdout while it is being read, validates the response, and maps
worker timeout, crash, malformed output, and output overflow to bounded HTTP errors.
Each process receives only a small environment allowlist plus
`WYSNA_SERVICE_DATABASE`; request headers forwarded to the worker are limited to
`accept`, `content-type`, and `x-request-id`. A panic or failed transaction ends that
process. The next request starts with a new process and does not reuse possibly damaged
runtime state. SQLite commits are explicit and use a busy timeout.

The adapter applies the five-second timeout to connections waiting for their initial
request. The request budget then begins after the adapter has parsed the request line
and headers, and covers body reading, queue wait, and worker execution. The HTTP adapter
owns response writes; this pilot does not impose a total response-write deadline.
Header count, individual field size, aggregate size, connection count and idle time are
bounded by the adapter plus the host. The worker process has the current user's filesystem
and OS privileges. The host provides process-failure isolation, not a security sandbox.
It has no TLS, authentication, deployment controls, request streaming, or measured SLO.
This pilot is for local development only and has no supported network deployment mode.

The example worker in `examples/service/sqlite_worker.wysna` supports `GET /health`,
`GET /measurements`, and `POST /measurements`. It inserts one measurement per SQLite
transaction, returns `409` on a duplicate ID and `400` on invalid JSON or values. Its
adapter uses parameterized statements and obtains the database path from the host
environment; it does not expose arbitrary SQL to requests.

`tools/measure_service.py` builds this worker, seeds a deterministic 1,000-row SQLite
dataset and alternates read-only `GET /health` and `GET /measurements` requests. It records
warm-up and steady-state metadata, source hashes, toolchain details, raw latency samples,
throughput, p50/p95/p99 latency, `503` saturation responses, one-second RSS samples across
the service process tree, and whether observed child processes remain after shutdown.
Linux uses `/proc`; macOS uses `ps`. RSS sampling may miss short peaks, and summing process
RSS can count shared pages more than once. The service contract tests also run on macOS;
the full PR campaign runs on Ubuntu and saves JSON as an artifact. It uses a 60-second
warm-up, three 10-minute repetitions and at least 10,000 latency samples per repetition.
It checks shutdown, the worker process bound, zero client errors, and the provisional
SPEC-006 RSS growth budget (the larger of 5% or 8 MiB) between the first and last
steady-load windows. Each repetition collects at least 10,000 latency observations and
requires p99 to remain within the configured 15-second request budget; the sample floor
is not a throughput SLO, and the request budget is not a latency target. Those
measurements expose a repeatable baseline for this fixed workload; they are not an SLO,
a representative application benchmark, or evidence for network deployment.

## IO, scheduling and failure boundaries

- Each task owns a request context with deadline, cancellation state and resource
  budget. Cancellation is cooperative and returns a typed operational error; it
  cannot silently skip cleanup. Define completion-versus-cancellation races.
- Socket operations expose partial reads/writes and bounded buffers. The scheduler
  polls readiness without blocking the entire worker. Backpressure must propagate
  to the producer; unbounded queues are not permitted.
- Begin with isolated task ownership. Moving handles invalidates the sender;
  aliases and mutable objects cannot cross workers without an enforced protocol.
  Atomic refcounts alone do not make mutable storage thread-safe.
- Recoverable request errors return Result. A runtime panic currently terminates
  the process: do not claim per-request recovery. Until an isolation boundary exists,
  a supervisor must treat a crash as a worker failure.
- Keep flush, close and durable commit separate. Automatic release is best effort;
  explicit close reports IO errors. Rename cannot silently fall back to copying
  across volumes. Crash durability needs a separately tested filesystem protocol.

These contracts define the next pilot's gates. They do not justify introducing a
second backend or a concurrent runtime before semantic and ownership parity passes.
