"""Generate lightweight JavaScript runtime and facade loaders for Wysna browser-library artifacts."""
import json
from pathlib import PurePosixPath


def generate_browser_bindings(runtime_module, exports):
    """Return an ES module that hides linear-memory pointers and enforces ABI ownership."""
    runtime_module = PurePosixPath(runtime_module).name
    public_exports = [
        {"name": item["name"], "symbol": item["symbol"],
         "params": list(item["params"]), "return": item["return"]}
        for item in exports
    ]
    encoded_exports = json.dumps(public_exports, ensure_ascii=False, separators=(",", ":"))
    return f'''import createWysnaModule from "./{runtime_module}";

const exportTable = {encoded_exports};
const int64Minimum = -(1n << 63n);
const int64Maximum = (1n << 63n) - 1n;
let nextInstanceId = 1;

function makeError(code, message, instanceId) {{
  const error = new Error(message);
  error.code = code;
  error.instanceId = instanceId;
  return error;
}}

function isWellFormedUtf16(value) {{
  for (let index = 0; index < value.length; index += 1) {{
    const unit = value.charCodeAt(index);
    if (unit >= 0xd800 && unit <= 0xdbff) {{
      const next = value.charCodeAt(index + 1);
      if (!(next >= 0xdc00 && next <= 0xdfff)) return false;
      index += 1;
    }} else if (unit >= 0xdc00 && unit <= 0xdfff) {{
      return false;
    }}
  }}
  return true;
}}

function validateArgument(type, value, instanceId) {{
  if (type === "int") {{
    if (typeof value !== "bigint" || value < int64Minimum || value > int64Maximum) {{
      throw makeError("INVALID_ARGUMENT", "int arguments must be signed 64-bit BigInt values", instanceId);
    }}
    return value;
  }}
  if (type === "float") {{
    if (typeof value !== "number") {{
      throw makeError("INVALID_ARGUMENT", "float arguments must be JavaScript numbers", instanceId);
    }}
    return value;
  }}
  if (type === "bool") {{
    if (typeof value !== "boolean") {{
      throw makeError("INVALID_ARGUMENT", "bool arguments must be JavaScript booleans", instanceId);
    }}
    return value;
  }}
  if (type === "str") {{
    if (typeof value !== "string" || !isWellFormedUtf16(value)) {{
      throw makeError("INVALID_ARGUMENT", "str arguments must be well-formed JavaScript strings", instanceId);
    }}
    return value;
  }}
  throw makeError("INVALID_ABI", `unsupported generated argument type: ${{type}}`, instanceId);
}}

function statusError(status, instanceId) {{
  const errors = {{
    1: ["INVALID_ARGUMENT", "invalid pointer, length, or output buffer"],
    2: ["OUT_OF_MEMORY", "the WebAssembly instance could not allocate memory"],
    3: ["INVALID_STATE", "the WebAssembly instance is not active or is already executing"],
    4: ["ABI_MISMATCH", "the function returned a value outside its declared ABI type"],
    5: ["OUTPUT_TOO_LARGE", "the UTF-8 result exceeds the browser ABI length limit"],
  }};
  const [code, message] = errors[status] || ["WASM_ERROR", `WebAssembly call failed with status ${{status}}`];
  return makeError(code, message, instanceId);
}}

export async function createWysna() {{
  const instanceId = nextInstanceId;
  nextInstanceId += 1;
  let module;
  let state = "initializing";
  let busy = false;
  const encoder = new TextEncoder();
  const decoder = new TextDecoder("utf-8", {{ fatal: true }});

  function invokeWasm(operation, message) {{
    try {{
      return operation();
    }} catch (_error) {{
      state = "failed";
      module = null;
      throw makeError("WASM_TRAP", message, instanceId);
    }}
  }}

  try {{
    module = await createWysnaModule();
    const status = invokeWasm(
      () => module._wysna_web_init(),
      "Wysna initialization trapped; this instance was discarded",
    );
    if (status !== 0) {{
      state = "failed";
      module = null;
      throw statusError(status, instanceId);
    }}
    state = "active";
  }} catch (error) {{
    state = "failed";
    module = null;
    throw error;
  }}

  function requireActive() {{
    if (state !== "active") {{
      throw makeError("INVALID_STATE", `Wysna instance is ${{state}}`, instanceId);
    }}
    if (busy) throw makeError("REENTRANT_CALL", "reentrant browser-library calls are unsupported", instanceId);
  }}

  function call(exported, values) {{
    requireActive();
    if (!Array.isArray(values) || values.length !== exported.params.length) {{
      throw makeError("INVALID_ARGUMENT", `expected ${{exported.params.length}} arguments`, instanceId);
    }}
    const checked = exported.params.map((type, index) => validateArgument(type, values[index], instanceId));
    const wasmModule = module;
    const allocated = [];
    let returnedStringPointer = 0;
    let outputPointer = 0;
    let trapped = false;
    try {{
      busy = true;
      const wasmArguments = [];
      for (let index = 0; index < exported.params.length; index += 1) {{
        const type = exported.params[index];
        const value = checked[index];
        if (type === "str") {{
          const bytes = encoder.encode(value);
          const pointer = invokeWasm(
            () => wasmModule._wysna_web_alloc(bytes.length),
            "Wysna input allocation trapped; this instance was discarded",
          );
          if (!pointer) throw makeError("OUT_OF_MEMORY", "could not allocate an input UTF-8 buffer", instanceId);
          allocated.push(pointer);
          if (bytes.length) wasmModule.HEAPU8.set(bytes, pointer);
          wasmArguments.push(pointer, bytes.length);
        }} else if (type === "bool") {{
          wasmArguments.push(value ? 1 : 0);
        }} else {{
          wasmArguments.push(value);
        }}
      }}

      if (exported.return !== "unit") {{
        const outputSize = exported.return === "str" ? 8 : exported.return === "bool" ? 4 : 8;
        outputPointer = invokeWasm(
          () => wasmModule._wysna_web_alloc(outputSize),
          "Wysna output allocation trapped; this instance was discarded",
        );
        if (!outputPointer) throw makeError("OUT_OF_MEMORY", "could not allocate an output buffer", instanceId);
        allocated.push(outputPointer);
        wasmArguments.push(outputPointer);
      }}

      const status = invokeWasm(
        () => wasmModule[`_${{exported.symbol}}`](...wasmArguments),
        "Wysna call trapped; this instance was discarded",
      );
      if (status !== 0) throw statusError(status, instanceId);

      if (exported.return === "unit") return undefined;
      const view = new DataView(wasmModule.HEAPU8.buffer);
      if (exported.return === "int") return view.getBigInt64(outputPointer, true);
      if (exported.return === "float") return view.getFloat64(outputPointer, true);
      if (exported.return === "bool") return view.getUint32(outputPointer, true) !== 0;
      if (exported.return === "str") {{
        const dataPointer = view.getUint32(outputPointer, true);
        const byteLength = view.getUint32(outputPointer + 4, true);
        if (dataPointer === 0 && byteLength !== 0) {{
          throw makeError("ABI_MISMATCH", "Wysna returned an invalid UTF-8 buffer", instanceId);
        }}
        if (dataPointer + byteLength > wasmModule.HEAPU8.byteLength) {{
          throw makeError("ABI_MISMATCH", "Wysna returned a UTF-8 buffer outside linear memory", instanceId);
        }}
        returnedStringPointer = dataPointer;
        const bytes = wasmModule.HEAPU8.slice(dataPointer, dataPointer + byteLength);
        try {{
          return decoder.decode(bytes);
        }} catch (_error) {{
          throw makeError("ABI_MISMATCH", "Wysna returned a string that is not valid UTF-8", instanceId);
        }}
      }}
      throw makeError("INVALID_ABI", `unsupported generated return type: ${{exported.return}}`, instanceId);
    }} catch (error) {{
      if (error && error.code === "WASM_TRAP") {{
        trapped = true;
      }}
      throw error;
    }} finally {{
      try {{
        if (!trapped && module) {{
          if (returnedStringPointer) {{
            invokeWasm(
              () => wasmModule._wysna_web_free(returnedStringPointer),
              "Wysna string release trapped; this instance was discarded",
            );
          }}
          for (const pointer of allocated) {{
            invokeWasm(
              () => wasmModule._wysna_web_free(pointer),
              "Wysna buffer release trapped; this instance was discarded",
            );
          }}
        }}
      }} finally {{
        busy = false;
        if (state === "failed") module = null;
      }}
    }}
  }}

  const operations = Object.create(null);
  for (const exported of exportTable) {{
    operations[exported.name] = (...values) => call(exported, values);
  }}
  Object.freeze(operations);

  function destroy() {{
    if (state === "destroyed") return;
    if (busy) throw makeError("REENTRANT_CALL", "cannot destroy an executing Wysna instance", instanceId);
    if (state === "failed") {{
      state = "destroyed";
      module = null;
      return;
    }}
    const wasmModule = module;
    try {{
      invokeWasm(
        () => wasmModule._wysna_web_destroy(),
        "Wysna destruction trapped; this instance was discarded",
      );
      state = "destroyed";
      module = null;
    }} catch (error) {{
      state = "failed";
      module = null;
      throw error;
    }}
  }}

  return Object.freeze({{ exports: operations, destroy }});
}}
'''


def generate_zig_browser_runtime(wasm_filename, exports):
    """Return a lightweight WASI-reactor ES module loader compatible with generate_browser_bindings."""
    wasm_name = PurePosixPath(wasm_filename).name
    symbol_names = sorted({item["symbol"] for item in exports})
    forwarded = ",\n    ".join(
        f"_{sym}: (...args) => exports.{sym}(...args)" for sym in symbol_names
    )
    extra_bindings = f",\n    {forwarded}" if forwarded else ""
    return f'''const wasmFilename = {json.dumps(wasm_name)};

export default async function createWysnaModule() {{
  const wasmUrl = new URL(`./${{wasmFilename}}`, import.meta.url);
  let bytes;
  if (typeof process !== "undefined" && process.versions && process.versions.node) {{
    const fs = await import("node:fs/promises");
    bytes = await fs.readFile(wasmUrl);
  }} else {{
    const response = await fetch(wasmUrl);
    if (!response.ok) {{
      throw new Error(`Failed to fetch WebAssembly module ${{wasmFilename}}: ${{response.status}}`);
    }}
    bytes = new Uint8Array(await response.arrayBuffer());
  }}

  let instanceExports = null;
  const decoder = new TextDecoder("utf-8");
  const getView = () => new DataView(instanceExports.memory.buffer);
  const wasi = {{
    fd_write(fd, iovsPtr, iovsLen, nwrittenPtr) {{
      if (!instanceExports || !instanceExports.memory) return 8;
      const mem = new Uint8Array(instanceExports.memory.buffer);
      const view = getView();
      let written = 0;
      let text = "";
      for (let i = 0; i < iovsLen; i += 1) {{
        const ptr = view.getUint32(iovsPtr + i * 8, true);
        const len = view.getUint32(iovsPtr + i * 8 + 4, true);
        written += len;
        text += decoder.decode(mem.subarray(ptr, ptr + len), {{ stream: true }});
      }}
      text += decoder.decode();
      if (text) {{
        if (fd === 2) console.error(text.replace(/\\n$/, ""));
        else console.log(text.replace(/\\n$/, ""));
      }}
      view.setUint32(nwrittenPtr, written, true);
      return 0;
    }},
    fd_close: () => 0,
    fd_seek: () => 0,
    fd_fdstat_get: () => 0,
    environ_sizes_get(countPtr, bufSizePtr) {{
      const view = getView();
      view.setUint32(countPtr, 0, true);
      view.setUint32(bufSizePtr, 0, true);
      return 0;
    }},
    environ_get: () => 0,
    args_sizes_get(argcPtr, argvBufSizePtr) {{
      const view = getView();
      view.setUint32(argcPtr, 0, true);
      view.setUint32(argvBufSizePtr, 0, true);
      return 0;
    }},
    args_get: () => 0,
    clock_time_get(_id, _precision, timePtr) {{
      getView().setBigUint64(timePtr, BigInt(Date.now()) * 1000000n, true);
      return 0;
    }},
    random_get(bufPtr, bufLen) {{
      if (instanceExports && instanceExports.memory && typeof crypto !== "undefined" && crypto.getRandomValues) {{
        crypto.getRandomValues(new Uint8Array(instanceExports.memory.buffer, bufPtr, bufLen));
      }}
      return 0;
    }},
    proc_exit(code) {{
      throw new Error(`WASI proc_exit(${{code}})`);
    }},
  }};

  const {{ instance }} = await WebAssembly.instantiate(bytes, {{ wasi_snapshot_preview1: wasi }});
  instanceExports = instance.exports;
  const exports = instanceExports;
  if (typeof exports._initialize === "function") {{
    exports._initialize();
  }}

  return {{
    get HEAPU8() {{
      return new Uint8Array(exports.memory.buffer);
    }},
    _wysna_web_init: () => exports.wysna_web_init(),
    _wysna_web_destroy: () => exports.wysna_web_destroy(),
    _wysna_web_alloc: (size) => exports.wysna_web_alloc(size),
    _wysna_web_free: (ptr) => exports.wysna_web_free(ptr){extra_bindings}
  }};
}}
'''
