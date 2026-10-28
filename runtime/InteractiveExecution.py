"""Execute only new checked statements in a persistent interactive environment."""

from error.Error import WysnaError
from node.NodeType import ClassDecl, EnumDecl, FnDecl, InterfaceDecl, Program, StructDecl


_DECLARATIONS = (ClassDecl, EnumDecl, FnDecl, InterfaceDecl, StructDecl)


def interpret_incremental(linked, interpreter, root_file, first_line, loaded_sources):
    """Execute newly accepted source without replaying earlier side effects.

    Newly created bindings are discarded if evaluation fails. Mutations and host
    I/O already performed by an input are not rolled back.
    """
    environment = interpreter.global_env
    selected = []
    for node in linked.body:
        origin = getattr(node, "source_file", None)
        if origin is None:
            raise WysnaError("Interactive statement has no source provenance",
                             code="W9999", stage="interactive")
        name = getattr(node, "name", "")
        is_new_root = origin == root_file and node.line >= first_line
        is_new_module = origin != root_file and origin not in loaded_sources
        missing_declaration = (isinstance(node, _DECLARATIONS) and name
                               and not environment.exists(name))
        if is_new_root or is_new_module or missing_declaration:
            selected.append(node)

    program = Program(selected)
    program.interner = linked.interner
    program.session_linked = True
    program.call_entrypoint = False
    environment.begin_declaration_journal()
    try:
        value = interpreter.interpret(program)
    except BaseException:
        environment.end_declaration_journal(rollback=True)
        raise
    environment.end_declaration_journal(rollback=False)
    return value
