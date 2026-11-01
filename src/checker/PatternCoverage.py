"""Pure exhaustiveness rules shared by checked AST and HIR consumers."""
from node.NodeType import (WildcardPattern, IdentifierPattern, ErrorTypePattern,
                           EnumPattern, TuplePattern, LiteralPattern, NoneLiteral, BoolLiteral)
from checker.WysnaType import (OptionalType, ResultType, EnumType, ErrorUnionType,
                           TupleType, BoolType)


def builtin_arguments(type_, name):
    if name == "Option" and isinstance(type_, OptionalType):
        return [type_.inner]
    if name == "Result" and isinstance(type_, ResultType):
        return [type_.ok, type_.err]
    if isinstance(type_, EnumType) and getattr(type_, "template_name", None) == name:
        return type_.type_args or []
    return []


def error_pattern_name(type_):
    return getattr(type_, "name", str(type_))


def pattern_matrix_exhaustive(types, rows, interner=None):
    def recurse(next_types, next_rows):
        return pattern_matrix_exhaustive(next_types, next_rows, interner)
    if not types:
        return bool(rows)
    if not rows:
        return False
    from hir.HirNodes import (
        HirWildcardPattern, HirBindingPattern, HirLiteralPattern, HirEnumPattern, HirErrorTypePattern, HirTuplePattern
    )
    def wildcard(pattern):
        if isinstance(pattern, (WildcardPattern, HirWildcardPattern)):
            return True
        if isinstance(pattern, (IdentifierPattern, HirBindingPattern)) and pattern.name not in ("None", "none"):
            return True
        return False
    defaults = [row[1:] for row in rows if wildcard(row[0])]
    if defaults and recurse(types[1:], defaults):
        return True
    first = types[0]
    optional = builtin_arguments(first, "Option")
    result = builtin_arguments(first, "Result")
    if optional:
        constructors = {"Some": optional, "None": []}
    elif result:
        constructors = {"Ok": [result[0]], "Err": [result[1]]}
    elif isinstance(first, ErrorUnionType):
        constructors = {
            error_pattern_name(member): [member]
            for member in first.alternatives
        }
    elif isinstance(first, EnumType):
        constructors = {name: first.variant_payloads.get(name, []) for name in first.variants}
    elif isinstance(first, TupleType):
        constructors = {"tuple": first.elements}
    elif first == BoolType:
        constructors = {"true": [], "false": []}
    else:
        return False
    for name, payload in constructors.items():
        specialized = []
        for row in rows:
            pattern = row[0]
            if wildcard(pattern):
                specialized.append([WildcardPattern() for _ in payload] + row[1:])
            elif (
                isinstance(first, ErrorUnionType)
                and isinstance(pattern, ErrorTypePattern)
                and error_pattern_name(pattern.matched_type) == name
            ):
                specialized.append([pattern.binding] + row[1:])
            elif (
                isinstance(first, ErrorUnionType)
                and isinstance(pattern, HirErrorTypePattern)
                and error_pattern_name(interner.to_type(pattern.error_type_id)) == name
            ):
                specialized.append([pattern.binding] + row[1:])
            elif isinstance(pattern, EnumPattern) and pattern.variant == name:
                specialized.append(pattern.args + row[1:])
            elif isinstance(pattern, HirEnumPattern) and pattern.variant_name == name:
                specialized.append(pattern.subpatterns + row[1:])
            elif isinstance(pattern, TuplePattern) and name == "tuple":
                specialized.append(pattern.elements + row[1:])
            elif isinstance(pattern, HirTuplePattern) and name == "tuple":
                specialized.append(pattern.elements + row[1:])
            elif name == "None" and (
                (isinstance(pattern, (IdentifierPattern, HirBindingPattern)) and pattern.name in ("None", "none")) or
                (isinstance(pattern, LiteralPattern) and isinstance(pattern.literal, NoneLiteral)) or
                (isinstance(pattern, HirLiteralPattern) and pattern.literal_kind == "none")
            ):
                specialized.append(row[1:])
            elif (
                (isinstance(pattern, LiteralPattern) and isinstance(pattern.literal, BoolLiteral) and name == ("true" if pattern.literal.value else "false")) or
                (isinstance(pattern, HirLiteralPattern) and pattern.literal_kind == "bool" and name == ("true" if pattern.value else "false"))
            ):
                specialized.append(row[1:])
        if not recurse(payload + types[1:], specialized):
            return False
    return True
