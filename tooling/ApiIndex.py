"""Machine-readable Wysna standard-library API and installed-tool capabilities."""
import argparse
import json
from pathlib import Path
import sys

from common.Version import WYSNA_VERSION
from compiler.CompilerService import CompilerService
from error.Error import WysnaError
from node.NodeType import (
    ClassDecl,
    EnumDecl,
    FnDecl,
    ImportDecl,
    InterfaceDecl,
    StructDecl,
)
from parser.Parser import Parser
from project.Manifest import ManifestError
from project.Resolver import ProjectResolver
from tooling.ToolResult import diagnostic_from_error, diagnostic_record, tool_result, write_tool_result


_STDLIB_ROOT = Path(__file__).resolve().parents[1] / "stdlib"


class _StrictArgumentParser(argparse.ArgumentParser):
    def error(self, message):
        raise ManifestError(message)


def _type_name(node):
    if node is None:
        return "inferred"
    name = type(node).__name__
    if name == "ResultType":
        return f"{_type_name(node.ok_type)} ! {_type_name(node.err_type)}"
    if name == "ErrorUnionType":
        return " | ".join(_type_name(item) for item in node.error_types)
    if name == "OptionalType":
        return f"{_type_name(node.inner)}?"
    if name == "ListType":
        return f"list[{_type_name(node.element_type)}]"
    if name == "DictType":
        return f"dict[{_type_name(node.key_type)}, {_type_name(node.value_type)}]"
    if name == "TupleType":
        return "(" + ", ".join(_type_name(item) for item in node.element_types) + ")"
    if name == "GenericType":
        return f"{node.name}[" + ", ".join(_type_name(item) for item in node.type_args) + "]"
    if name == "FunctionType":
        return "fn(" + ", ".join(_type_name(item) for item in node.params) + ") -> " + _type_name(node.return_type)
    return str(node)


def _leading_documentation(lines, line_number):
    index = line_number - 2
    comments = []
    while index >= 0 and lines[index].lstrip().startswith("#"):
        comments.append(lines[index].lstrip()[1:].strip())
        index -= 1
    comments.reverse()
    return "\n".join(comments) if comments else None


def _availability(module_id):
    if module_id == "std/http" or module_id.startswith("std/http/"):
        wasm = "unavailable"
        native = "requires-libcurl"
        interpreter = "requires-pycurl-extra"
        selfhost = "requires-libcurl"
    elif module_id == "std/process" or module_id.startswith("std/process/"):
        wasm = "unavailable"
        native = "supported"
        interpreter = "supported"
        selfhost = "unknown"
    elif module_id in ("std/fs", "std/files"):
        wasm = "requires-declared-preopens"
        native = "supported"
        interpreter = "supported"
        selfhost = "unknown"
    else:
        wasm = "supported"
        native = "supported"
        interpreter = "supported"
        selfhost = "unknown"
    return {"interpreter": interpreter, "native": native,
            "selfhost": selfhost, "wasm": wasm}


def _function_record(node, module_id, owner, lines, *, public=True, kind="function"):
    type_parameters = [str(item) for item in getattr(node, "type_params", ())]
    params = [{"name": item.name, "type": _type_name(item.type_ann)} for item in node.params]
    return_type = _type_name(node.return_type)
    error_type = None
    if type(node.return_type).__name__ == "ResultType":
        error_type = _type_name(node.return_type.err_type)
    generic = "[" + ", ".join(type_parameters) + "]" if type_parameters else ""
    signature = f"fn {node.name}{generic}("
    signature += ", ".join(f"{item['name']}: {item['type']}" for item in params)
    signature += f") -> {return_type}"
    return {
        "identity": f"{module_id}#{kind}/{node.name}",
        "name": node.name,
        "kind": kind,
        "visibility": "public" if public else "private",
        "signature": signature,
        "type_parameters": type_parameters,
        "parameters": params,
        "return_type": return_type,
        "error_channel": error_type,
        "effects": "unknown",
        "documentation": _leading_documentation(lines, node.line),
        "ownership": "unknown",
        "availability": _availability(module_id),
        "package": owner,
    }


def _module_records(source, path, module_id, owner):
    program = Parser(file=str(path)).parse(source)
    lines = source.splitlines()
    symbols = []
    imports = []
    for declaration in program.body:
        if isinstance(declaration, ImportDecl):
            imports.append(declaration.path)
            continue
        if isinstance(declaration, FnDecl) and getattr(declaration, "is_exported", False):
            symbols.append(_function_record(declaration, module_id, owner, lines))
        elif isinstance(declaration, StructDecl) and getattr(declaration, "is_exported", False):
            symbols.append({
                "identity": f"{module_id}#struct/{declaration.name}",
                "name": declaration.name,
                "kind": "struct",
                "visibility": "public",
                "signature": f"struct {declaration.name}",
                "fields": [{"name": field.name, "type": _type_name(field.type_ann)} for field in declaration.fields],
                "effects": "unknown",
                "documentation": _leading_documentation(lines, declaration.line),
                "ownership": "unknown",
                "availability": _availability(module_id),
                "package": owner,
            })
        elif isinstance(declaration, EnumDecl) and getattr(declaration, "is_exported", False):
            symbols.append({
                "identity": f"{module_id}#enum/{declaration.name}",
                "name": declaration.name,
                "kind": "enum",
                "visibility": "public",
                "signature": f"enum {declaration.name}",
                "variants": [{"name": variant.name, "payload": [_type_name(item) for item in variant.payload_types]} for variant in declaration.variants],
                "effects": "unknown",
                "documentation": _leading_documentation(lines, declaration.line),
                "ownership": "unknown",
                "availability": _availability(module_id),
                "package": owner,
            })
        elif isinstance(declaration, InterfaceDecl) and getattr(declaration, "is_exported", False):
            methods = [_function_record(
                method, module_id + "#interface/" + declaration.name, owner, lines,
                kind="method",
            ) for method in declaration.methods]
            symbols.append({
                "identity": f"{module_id}#interface/{declaration.name}",
                "name": declaration.name,
                "kind": "interface",
                "visibility": "public",
                "signature": f"interface {declaration.name}",
                "methods": methods,
                "effects": "unknown",
                "documentation": _leading_documentation(lines, declaration.line),
                "ownership": "unknown",
                "availability": _availability(module_id),
                "package": owner,
            })
        elif isinstance(declaration, ClassDecl) and getattr(declaration, "is_exported", False):
            methods = [_function_record(method, module_id + "#class/" + declaration.name,
                                        owner, lines, kind="method")
                       for method in declaration.methods if getattr(method, "is_public", False)]
            symbols.append({
                "identity": f"{module_id}#class/{declaration.name}",
                "name": declaration.name,
                "kind": "class",
                "visibility": "public",
                "signature": f"class {declaration.name}",
                "methods": methods,
                "effects": "unknown",
                "documentation": _leading_documentation(lines, declaration.line),
                "ownership": "unknown",
                "availability": _availability(module_id),
                "package": owner,
            })
    symbols.sort(key=lambda symbol: (symbol["kind"], symbol["name"]))
    return symbols, imports


def _stdlib_module_id(path):
    relative = path.resolve().relative_to(_STDLIB_ROOT.resolve()).with_suffix("")
    return "std/" + relative.as_posix()


def _stdlib_owner(_path):
    return "wysna-stdlib@" + WYSNA_VERSION


def _resolve_stdlib_module(relative):
    if (not relative or relative.startswith("/") or "\\" in relative or ":" in relative
            or any(part in ("", ".", "..") for part in relative.split("/"))):
        raise ManifestError(f"invalid standard-library module path: '{relative}'")
    requested = Path(str(_STDLIB_ROOT / relative))
    if not str(requested).endswith(".wysna"):
        requested = Path(str(requested) + ".wysna")
    try:
        requested = requested.resolve(strict=True)
        requested.relative_to(_STDLIB_ROOT.resolve())
    except (OSError, ValueError) as error:
        raise ManifestError(f"standard-library module not found: '{relative}'") from error
    if not requested.is_file():
        raise ManifestError(f"standard-library module not found: '{relative}'")
    return requested


def _load_stdlib_index(module_filter=None):
    files = sorted(_STDLIB_ROOT.glob("*.wysna")) if module_filter is None else []
    if module_filter is not None:
        relative = module_filter[4:] if module_filter.startswith("std/") else module_filter
        files = [_resolve_stdlib_module(relative)]
    modules, visited = [], set()

    def collect(path):
        path = path.resolve()
        if path in visited:
            return
        visited.add(path)
        source = path.read_text(encoding="utf-8")
        module_id = _stdlib_module_id(path)
        symbols, imports = _module_records(source, path, module_id, _stdlib_owner(path))
        modules.append({"identity": module_id, "symbols": symbols})
        for import_path in imports:
            if import_path.startswith("std/"):
                collect(_resolve_stdlib_module(import_path[4:]))

    for path in files:
        collect(path)
    modules.sort(key=lambda module: module["identity"])
    return modules


def _project_index(manifest_path):
    resolver = ProjectResolver.load(manifest_path)
    manifest = resolver.manifest
    target = manifest.targets.get("app")
    if target is None:
        raise ManifestError("project API index requires a target named 'app'")
    service = CompilerService(project_root=manifest.root, project_resolver=resolver)
    checked = service.check_file(manifest.root / target.entry)
    modules = []
    for path, snapshot in checked.sources.items():
        source_path = Path(path).resolve()
        source_root = _STDLIB_ROOT.resolve()
        if source_path.is_relative_to(source_root):
            module_id = _stdlib_module_id(source_path)
            owner = _stdlib_owner(source_path)
        else:
            package = resolver.package_for(source_path)
            relative = source_path.relative_to(package.manifest.source_root).with_suffix("").as_posix()
            module_id = package.package_id + "/" + relative
            owner = package.package_id
        symbols, _imports = _module_records(snapshot.text, source_path, module_id, owner)
        modules.append({"identity": module_id, "symbols": symbols})
    modules.sort(key=lambda module: module["identity"])
    return {
        "schema_version": "wysna.api-index/1",
        "compiler_version": WYSNA_VERSION,
        "stdlib_version": WYSNA_VERSION,
        "project": {"name": manifest.name, "version": manifest.version},
        "modules": modules,
    }


def _emit(command, payload=None, error=None):
    if error is not None:
        diagnostic = diagnostic_from_error(error) if isinstance(error, WysnaError) else diagnostic_record("W5002", command, str(error))
        result = tool_result(command, "diagnostic_error" if isinstance(error, WysnaError) else "usage_error",
                             1 if isinstance(error, WysnaError) else 2, diagnostics=[diagnostic])
    else:
        result = tool_result(command, "ok", 0)
        result["result"] = payload
    write_tool_result(result, sys.stdout)
    return result["tool_exit_code"]


def run_api_command(argv):
    arguments = list(argv)
    parser = _StrictArgumentParser(prog="wysna api")
    parser.add_argument("module", nargs="?")
    parser.add_argument("--manifest")
    parser.add_argument("--output-format", choices=("json-v1",))
    try:
        options = parser.parse_args(arguments)
        if options.manifest:
            if options.module:
                raise ManifestError("a project API index includes its resolved module graph; omit the module name")
            payload = _project_index(options.manifest)
        else:
            payload = {"schema_version": "wysna.api-index/1", "compiler_version": WYSNA_VERSION,
                       "stdlib_version": WYSNA_VERSION, "modules": _load_stdlib_index(options.module)}
        return _emit("api", payload=payload)
    except (ManifestError, OSError, ValueError, WysnaError) as error:
        return _emit("api", error=error)


def run_capabilities_command(argv):
    parser = _StrictArgumentParser(prog="wysna capabilities")
    parser.add_argument("--output-format", choices=("json-v1",))
    try:
        parser.parse_args(argv)
        payload = {
            "schema_version": "wysna.capabilities/1",
            "compiler_version": WYSNA_VERSION,
            "commands": ["run", "check", "fmt", "ast", "mir", "tokens", "emit-c", "build",
                         "project.lock", "project.check", "project.build", "project.run", "project.test",
                         "api", "capabilities", "lsp", "repl"],
            "machine_output": {"format": "json-v1", "commands": ["check", "fmt", "emit-c", "build",
                                                                          "project.check", "project.build",
                                                                          "api", "capabilities"]},
            "profiles": ["native-cli", "wasi-command", "browser-library", "c99-source"],
            "targets": ["native", "wasm32-wasip1", "wasm32-unknown-emscripten"],
            "runtimes": {"wasi-command": "wasmtime==50.0.1",
                         "browser-library": "emscripten==5.0.1 and browser or Node.js"},
            "capabilities": {
                "native-cli": ["stdio", "filesystem", "environment", "processes", "network", "native-ffi"],
                "wasi-command": ["stdio", "arguments", "clock", "random",
                                 "manifest-declared-preopens"],
                "browser-library": ["linear-memory", "generated-utf8-abi", "bigint-int64"],
            },
            "requirements": {
                "std/http": {
                    "interpreter": "install wysna[http] with libcurl 8.3.0 or newer",
                    "native": "install libcurl 8.3.0 or newer development files and expose curl-config on PATH",
                    "wasi-command": "unavailable",
                    "browser-library": "unavailable",
                },
            },
            "limitations": ["native-cli capabilities describe host access and are not a sandbox",
                            "WASI Preview 1 does not provide networking, child processes, or native FFI",
                            "browser-library exposes only generated scalar and UTF-8 exports; WVal and pointers remain internal",
                            "project dependencies are local paths only",
                            "project lockfiles do not claim bit-for-bit binary reproducibility"],
        }
        return _emit("capabilities", payload=payload)
    except (ValueError, ManifestError) as error:
        return _emit("capabilities", error=error)
