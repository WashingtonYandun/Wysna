# Standard Library

Wysna distributes modules under `std/` imported via canonical paths such as `import "std/text"`.

## Modules Overview

- `std/buffer`: compact mutable byte arrays (`Bytes`) with boundary validation and lifecycle tracking.
  - `buffer.from_text(str) -> Bytes`: create a byte buffer from UTF-8 string data.
  - `buffer.clone(Bytes) -> Bytes ! BufferError`: clone buffer with isolated storage.
  - `Bytes.append(int) -> unit ! BufferError`: append a byte (0–255).
  - `Bytes.get(int) -> int ! BufferError`: access byte by index.
  - `Bytes.size() -> int`: get buffer length in bytes.
  - `Bytes.close() -> int`: release buffer memory explicitly.
- `std/files`: structured block and stream I/O operations returning `FileError`.
  - `files.open(path, mode) -> File ! FileError`: open file with integer mode (`0` read, `1` truncate/write, `2` append).
  - `File.read(max_bytes) -> Bytes ! FileError`: read up to `max_bytes`.
  - `File.write(Bytes) -> unit ! FileError`: write bytes to file.
  - `File.close() -> int`: close handle and release host descriptor.
- `std/fs`: filesystem operations returning `FsError`.
  - `fs.exists(path) -> bool ! FsError`: check if path exists on the host filesystem.
  - `fs.size(path) -> int ! FsError`: get file size in bytes.
  - `fs.mkdir(path) -> unit ! FsError`: create a directory.
  - `fs.remove(path) -> unit ! FsError`: remove a file.
  - `fs.rename(old_path, new_path) -> unit ! FsError`: rename or move a path.
- `std/parse`: recoverable numeric parsing returning `Result` with `ParseError`.
  - `parse.parse_int(text) -> int ! ParseError`: parse 64-bit signed integer with whitespace trimming, optional sign, and overflow checks.
  - `parse.parse_float(text) -> float ! ParseError`: parse IEEE-754 64-bit float with fractional and exponent support.
- `std/json`: bounded JSON parsing and deterministic compact serialization.
  - `json.parse(text) -> Value ! JsonError`: parse one JSON value with defaults of 1,048,576 UTF-8 input bytes, 32 nested arrays/objects, and 100,000 JSON values; error offsets count Unicode scalar values.
  - `json.parse_with_limits(text, max_input_bytes, max_depth, max_nodes) -> Value ! JsonError`: set per-call budgets. Bytes range from 1 to 16,777,216 (the default is 1,048,576); depth and node budgets can be reduced from hard maxima of 32 and 100,000. Invalid budgets return `JsonError` at offset 0.
  - The node budget counts the root and every array, object, and primitive value. Object member names do not count; values from duplicate members count before the last value wins.
  - `json.stringify(value) -> str ! JsonError`: serialize compact JSON with object keys sorted by Unicode scalar value.
  - `Value` supports null, boolean, number, string, array and object values. Numbers preserve their JSON source spelling as `str`, avoiding silent binary64 precision loss.
  - `json.null()`, `boolean()`, `number()`, `string()`, `array()` and `object()` construct values. `number()` validates its input.
  - `json.is_null()`, `as_bool()`, `as_number()`, `as_string()`, `as_array()`, `as_object()`, `get()` and `at()` inspect values; optional accessors return `none` for a type mismatch or missing index/key.
  - Objects use string keys. Repeated keys during parsing keep the last value. Parsing rejects unpaired UTF-16 surrogates, malformed number syntax, trailing content and inputs beyond the selected budgets.
- `std/text`: string manipulation and query operations.
  - `text.join(parts, separator) -> str`: join string list with separator.
  - `text.pad_left(text, width, fill) -> str`: pad text on the left with fill character.
  - `text.lines(text) -> list[str]`: split text by LF or CRLF lines.
  - `text.starts_with(text, prefix) -> bool`: check prefix.
  - `text.ends_with(text, suffix) -> bool`: check suffix.
  - `text.find(haystack, needle) -> int?`: find codepoint offset, returning `none` if absent.
  - `text.split(text, separator) -> list[str]`: split text by delimiter.
  - `text.replace(text, target, replacement) -> str`: replace all occurrences.
  - `text.trim(text) -> str`: trim leading and trailing ASCII whitespace.
- `std/path`: file path utilities.
  - `path.join(base, child) -> str`: join paths with directory separator `/`.
  - `path.basename(path) -> str`: extract filename component.
  - `path.dirname(path) -> str`: extract directory component.
- `std/io`: text file convenience wrappers.
  - `io.read_text(path) -> str ! IoError`: read full UTF-8 file to string.
  - `io.write_text(path, content) -> unit ! IoError`: write full string to file.
- `std/algorithms`: functional collection algorithms.
  - `algorithms.map[T, R]` `(values, fn(T) -> R) -> list[R]`: transform list elements.
  - `algorithms.fold[T, R]` `(values, initial, fn(R, T) -> R) -> R`: fold accumulator over list.
  - `algorithms.sort[T]` `(values, fn(T, T) -> bool) -> list[T]`: stable merge sort with $O(N \log N)$ complexity.
- `std/bytes`: UTF-8 codec operations.
  - `bytes.encode(text) -> list[int]`: encode UTF-8 string into byte values (0–255).
  - `bytes.decode(bytes) -> str ! Utf8Error`: decode byte values into validated UTF-8 string.
- `std/environment`: runner arguments and environment lookup.
  - `environment.arguments() -> list[str]`: return the runner-defined argument vector; interpreted file and project runs use `"wysna"` at index zero.
  - `environment.get(name) -> str?`: read an environment variable, preserving the difference between an absent value and an empty string.
- `std/time`: host wall-clock access.
  - `time.unix_seconds() -> int`: return seconds since the Unix epoch.
- `std/process`: bounded child-process execution.
  - `process.run(executable, arguments, timeout_ms) -> int ! ProcessError`: invoke an executable directly with an argument vector and no implicit shell. Child stdout/stderr inherit the parent streams. The timeout is 1–600,000 ms; at most 256 arguments are accepted, each up to 1 MiB, with a 16 MiB total encoded-argument limit.
- `std/http`: synchronous HTTP and HTTPS client for bounded JSON API calls.
  - `http.make_header(name, value) -> Header` constructs a request header.
  - `http.request(url, method, headers, body, timeout_ms, max_response_bytes) -> Response ! HttpError`: issue one request using `GET`, `POST`, `PUT`, `PATCH`, `DELETE`, or `HEAD`. `GET` and `HEAD` reject request bodies.
  - `http.get(url) -> Response ! HttpError`: issue a GET with a 30-second total timeout and 1 MiB response limit.
  - `http.post_json(url, headers, payload) -> Response ! HttpError | JsonError`: serialize one JSON value and send it as `application/json`.
  - `http.header(response, name) -> str?`, `http.response_text(response) -> str ! HttpError`, and `http.response_json(response) -> json.Value ! HttpError | JsonError` inspect a response.
  - `Response` preserves the HTTP status, response headers and owned `buffer.Bytes` body. Close `response.body` when it is no longer needed. Statuses such as 401, 429, 302 and 503 are responses, not transport errors.
  - The host enforces an 8 KiB URL limit, 32 KiB and 100 fields for request/response headers, 1 MiB request and response body limits, a 30-second total timeout, and a 5-second maximum connection timeout. A call may lower its timeout and response-body limit.
  - TLS certificate and hostname checks are enabled. Redirects and retries are disabled; a 3xx response is returned as-is. Secrets can be passed in request headers and are never logged by this API. URLs must be ASCII with non-ASCII components percent-encoded; header names and values must be ASCII.
  - The interpreter requires the optional `http` install extra (`pip install 'wysna[http]'`) and libcurl 8.3.0 or newer through PycURL. Native builds require libcurl 8.3.0 or newer, development files and `curl-config`; generated programs link libcurl. `std/http` is unavailable for `wasi-command` and does not sandbox network access. Streaming and an HTTP server are not included.
  - See [`examples/http_json_client.wysna`](../examples/http_json_client.wysna) for a JSON endpoint example. It reads its endpoint and optional bearer token from `WYSNA_AI_ENDPOINT` and `WYSNA_AI_TOKEN`.
- `std/test`: typed assertions for project test entries.
  - `test.expect(condition, message)`, `expect_equal_int(actual, expected, message)`, and `expect_equal_text(actual, expected, message)` terminate the test process when an assertion fails.

## External Resources & Scopes

`std/resources` exports `Resource`, `ResourceError`, `error(message)`, `acquire(handle, dispose)` and `scope[T, E]`.
- `acquire(handle, dispose) -> Resource ! ResourceError` claims an opaque native handle and a disposal callback; duplicate ownership is rejected.
- `borrow()` returns `opaque ! ResourceError` and rejects access after close.
- `close()` provides idempotent destruction.
- `scope(resource, body)` ensures the resource is disposed upon block completion (success or error), returning `(T ! E, close_status)`.

## Standard resource lifetime and errors

`Bytes` and `File` retain trusted host owners. Aliases share identity; losing the last
owner closes the host entry automatically. `Bytes.borrow()` returns a checked,
non-owning ID and does not prolong lifetime. A stale ID fails validation. Explicit
`close() -> int` consumes the host entry once, returns 0 on success and -1 on IO
failure; repeated close returns 0. Closing one alias closes all aliases. A fallback
close at last release is best effort; call close explicitly to observe flush errors.
No arbitrary Wysna finalizer runs during release. Strong ARC cycles are not collected.
Raw FFI `opaque` pointers and `resources.Resource` disposal callbacks still require
explicit close or `resources.scope`; they do not acquire automatic user finalizers.
Scope helpers close after success or a returned error; panic/exit/cancellation is
not a language-level unwinding guarantee.

`FileError` and `FsError` have `operation: str`, `path: str`, `code: int`, and
`domain: int`. Failure codes are positive platform codes: domain 1 is errno,
domain 2 is Win32. Capture occurs before cleanup. File.read accepts 1..16777216
bytes; EOF returns an empty owned Bytes. A closed handle and an invalid read bound
return recoverable errors. File.write checks both short writes and fflush failures;
flush does not guarantee durable storage. Rename replaces a destination according
to host rules and rejects cross-volume copy fallback; it is not a durability promise.

`ProcessError` contains `operation`, `code`, and `domain`. `process.run` never
implicitly invokes a shell; callers that need a shell must name it as the executable
and pass the script as a normal argument. Child output is inherited instead of
captured, and the process runner enforces a per-invocation timeout.

The internal diagnostic ABI exposes `wysna_host_active_handles()` and
`wysna_host_active_bytes()` for persistent workload tests. Handles include buffers,
files and raw resource claims. Bytes measure reserved buffer capacity in C and
payload bytes in the reference engine; neither metric is process RSS.
