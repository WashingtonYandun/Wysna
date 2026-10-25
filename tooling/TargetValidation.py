"""Preflight checks for capabilities required by a selected project target."""
from pathlib import Path

from error.Error import WysnaError
from checker.TypeInterner import TYPE_BOOL, TYPE_FLOAT, TYPE_INT, TYPE_STR, TYPE_UNIT
from node.NodeType import CallExpr, IdentExpr, Node
from project.Targets import target_spec


def validate_checked_target(checked, target):
    """Reject source capabilities unavailable to a target before its toolchain runs."""
    spec = target_spec(target.profile)
    repository_root = Path(__file__).resolve().parents[1]
    standard_library = (repository_root / "stdlib").resolve()
    module_paths = {Path(path).resolve() for path in checked.modules}
    http_module = (standard_library / "http.wysna").resolve()
    if target.profile == "wasi-command" and http_module in module_paths:
        raise WysnaError(
            "the wasi-command profile does not support std/http; use a native-cli target",
            file=checked.source.file, code="W2013", stage="target",
        )
    if target.profile == "browser-library":
        _validate_browser_library(checked, module_paths, standard_library)
    if target.profile != "wasi-command":
        return spec

    process_module = (standard_library / "process.wysna").resolve()
    file_modules = {
        (standard_library / "files.wysna").resolve(),
        (standard_library / "fs.wysna").resolve(),
    }
    if process_module in module_paths:
        raise WysnaError(
            "the wasi-command profile does not support std/process; use a native-cli target",
            file=checked.source.file, code="W2013", stage="target",
        )
    if module_paths.intersection(file_modules) and not target.preopens:
        raise WysnaError(
            "this program uses filesystem APIs but the wasi-command target declares no preopens",
            file=checked.source.file, code="W2013", stage="target",
        )

    for declaration in checked.declarations.values():
        if declaration.kind != "ExternFnDecl":
            continue
        path = Path(declaration.file).resolve()
        try:
            path.relative_to(standard_library)
        except ValueError:
            raise WysnaError(
                f"native C function '{declaration.name}' has no declared WASI host binding",
                line=declaration.line, col=declaration.col, file=declaration.file,
                code="W2013", stage="target",
            )
    return spec


def _validate_browser_library(checked, module_paths, standard_library):
    """Require a declaration-only module and a public function set that fits the generated ABI."""
    unsupported_modules = {
        "buffer.wysna", "environment.wysna", "files.wysna", "fs.wysna",
        "http.wysna", "io.wysna", "process.wysna", "resources.wysna",
        "time.wysna",
    }
    for path in module_paths:
        try:
            path.relative_to(standard_library)
        except ValueError:
            continue
        if path.name in unsupported_modules:
            raise WysnaError(
                f"the browser-library profile does not provide std/{path.stem}",
                file=checked.source.file, code="W2013", stage="target",
            )

    for declaration in checked.declarations.values():
        if declaration.kind == "ExternFnDecl":
            raise WysnaError(
                f"native C function '{declaration.name}' has no browser host binding",
                line=declaration.line, col=declaration.col, file=declaration.file,
                code="W2013", stage="target",
            )

    linked = checked._session.link(checked._unit.program)
    declaration_kinds = {"FnDecl", "ExternFnDecl", "StructDecl", "EnumDecl",
                         "InterfaceDecl", "ClassDecl"}
    for declaration in linked.body:
        if type(declaration).__name__ not in declaration_kinds:
            raise WysnaError(
                "browser-library modules cannot run top-level initialization",
                line=declaration.line, col=declaration.col, file=checked.source.file,
                code="W2013", stage="target",
            )

    unsupported_builtins = {"args", "env", "unix_time", "exit", "read_file", "write_file",
                            "print", "eprint"}
    for node in _nodes(linked):
        if (isinstance(node, CallExpr) and isinstance(node.callee, IdentExpr)
                and node.callee.name in unsupported_builtins):
            raise WysnaError(
                f"the browser-library profile does not provide builtin '{node.callee.name}'",
                line=node.line, col=node.col, file=checked.source.file,
                code="W2013", stage="target",
            )

    supported_types = {TYPE_INT, TYPE_FLOAT, TYPE_BOOL, TYPE_STR, TYPE_UNIT}
    public_functions = [node for node in checked._unit.program.body
                        if type(node).__name__ == "FnDecl" and getattr(node, "is_exported", False)]
    if not public_functions:
        raise WysnaError(
            "browser-library requires at least one top-level 'pub fn' export",
            file=checked.source.file, code="W2013", stage="target",
        )
    for function in public_functions:
        if getattr(function, "is_fallible", False):
            raise WysnaError(
                f"fallible function '{function.name}' cannot cross the browser ABI",
                line=function.line, col=function.col, file=checked.source.file,
                code="W2013", stage="target",
            )
        if function.type_params:
            raise WysnaError(
                f"generic function '{function.name}' cannot cross the browser ABI",
                line=function.line, col=function.col, file=checked.source.file,
                code="W2013", stage="target",
            )
        for parameter in function.params:
            type_id = getattr(parameter.type_ann, "resolved_type_id", None)
            if type_id not in supported_types or type_id == TYPE_UNIT:
                raise WysnaError(
                    f"exported parameter '{parameter.name}' must use int, float, bool, or str",
                    line=parameter.line, col=parameter.col, file=checked.source.file,
                    code="W2013", stage="target",
                )
        return_type = TYPE_UNIT if function.return_type is None else getattr(
            function.return_type, "resolved_type_id", None)
        if return_type not in supported_types:
            raise WysnaError(
                f"exported function '{function.name}' must return int, float, bool, str, or unit",
                line=function.line, col=function.col, file=checked.source.file,
                code="W2013", stage="target",
            )


def _nodes(root):
    pending = [root]
    visited = set()
    while pending:
        node = pending.pop()
        if not isinstance(node, Node) or id(node) in visited:
            continue
        visited.add(id(node))
        yield node
        for child in vars(node).values():
            if isinstance(child, Node):
                pending.append(child)
            elif isinstance(child, list):
                pending.extend(item for item in child if isinstance(item, Node))
