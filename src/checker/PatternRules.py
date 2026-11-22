"""Resolve and type-check recursive patterns before any execution backend sees them."""
from node import NodeType as N
from checker.WysnaType import IntType, StructType, TupleType, EnumType, ErrorUnionType
from error.Error import TypeError


def bind_pattern(checker, pattern, expected, bindings):
    def fail(message, code='W2001'):
        raise TypeError(message, line=pattern.line, col=pattern.col, file=checker.file, code=code)

    pattern.inferred_type_id = checker.interner.intern(expected)
    pattern.session_arena_id = checker.interner.arena_id
    if isinstance(pattern, N.OrPattern):
        alternatives = []
        for alternative in pattern.alternatives:
            local = {}
            alternative = resolve_pattern(checker, alternative, expected)
            bind_pattern(checker, alternative, expected, local)
            alternatives.append((alternative, local))
        first = alternatives[0][1]
        if any(local != first for _, local in alternatives[1:]):
            fail('Every alternative of an or-pattern must bind the same names with the same types')
        for name, type_ in first.items():
            if name in bindings:
                fail(f"Duplicate pattern binding '{name}'", 'W1002')
            bindings[name] = type_
        pattern.alternatives = [alternative for alternative, _ in alternatives]
    elif isinstance(pattern, N.RangePattern):
        if expected != IntType:
            fail('Range patterns require an int subject')
        if not all(-(1 << 63) <= value < (1 << 63) for value in (pattern.start, pattern.end)):
            fail('Range pattern bounds must fit int64', 'W0002')
        if pattern.start > pattern.end or (pattern.start == pattern.end and not pattern.inclusive):
            fail('Range pattern must contain at least one integer')
    elif isinstance(pattern, N.StructPattern):
        if not isinstance(expected, StructType):
            fail('Struct patterns require a struct subject')
        owner = checker._lookup_type_name(pattern.struct_name)
        if pattern.type_args:
            owner = checker._resolve_type_node(N.GenericType(pattern.struct_name, pattern.type_args))
        if owner != expected and getattr(owner, 'name', None) != getattr(expected, 'template_name', None):
            fail(f"Struct pattern '{pattern.struct_name}' does not match '{expected}'")
        seen = set()
        for field in pattern.fields:
            if field.name in seen:
                fail(f"Duplicate struct pattern field '{field.name}'", 'W1002')
            seen.add(field.name)
            if field.name not in expected.fields:
                fail(f"Unknown struct pattern field '{field.name}'")
            field.pattern = resolve_pattern(checker, field.pattern, expected.fields[field.name])
            bind_pattern(checker, field.pattern, expected.fields[field.name], bindings)
        if not pattern.has_rest and seen != set(expected.fields):
            fail("Struct pattern must name every field or include '..'")
        pattern.struct_name = expected.name
    elif isinstance(pattern, N.WildcardPattern):
        pass
    elif isinstance(pattern, N.IdentifierPattern):
        if pattern.name in bindings:
            fail(f"Duplicate pattern binding '{pattern.name}'", 'W1002')
        bindings[pattern.name] = expected
    elif isinstance(pattern, N.TuplePattern):
        if not isinstance(expected, TupleType) or len(expected.elements) != len(pattern.elements):
            fail(f"Tuple pattern cannot match '{expected}'")
        pattern.elements = [resolve_pattern(checker, child, type_) for child, type_ in zip(pattern.elements, expected.elements)]
        for child, type_ in zip(pattern.elements, expected.elements):
            bind_pattern(checker, child, type_, bindings)
    elif isinstance(pattern, N.ErrorTypePattern):
        bind_pattern(checker, pattern.binding, pattern.matched_type, bindings)
    elif isinstance(pattern, N.EnumPattern):
        checker._validate_pattern_owner(pattern, expected)
        optional = checker._builtin_arguments(expected, 'Option')
        result = checker._builtin_arguments(expected, 'Result')
        if optional:
            payloads = {'Some': optional, 'None': []}
        elif result:
            payloads = {'Ok': [result[0]], 'Err': [result[1]]}
        elif isinstance(expected, EnumType):
            payloads = expected.variant_payloads
            pattern.enum_name = expected.name
        else:
            fail('Enum pattern requires an enum subject')
        types = payloads.get(pattern.variant)
        if types is None:
            fail('Pattern variant is incompatible with the match subject')
        if len(types) != len(pattern.args):
            fail('Enum pattern payload does not match variant', 'W2005')
        pattern.args = [resolve_pattern(checker, child, type_) for child, type_ in zip(pattern.args, types)]
        for child, type_ in zip(pattern.args, types):
            bind_pattern(checker, child, type_, bindings)
    elif isinstance(pattern, N.LiteralPattern):
        found = checker._check_expr(pattern.literal)
        if not checker.type_relations.is_assignable(found, expected):
            fail(f"Literal pattern type '{found}' incompatible with '{expected}'")
    else:
        fail('Unsupported pattern')


def resolve_pattern(checker, pattern, expected):
    if isinstance(expected, ErrorUnionType) and isinstance(pattern, (N.EnumPattern, N.ErrorTypePattern)):
        return checker._resolve_error_type_pattern(pattern, expected)
    if isinstance(pattern, N.IdentifierPattern):
        optional = checker._builtin_arguments(expected, 'Option')
        result = checker._builtin_arguments(expected, 'Result')
        names = {'Some', 'None'} if optional else {'Ok', 'Err'} if result else set(expected.variants) if isinstance(expected, EnumType) else set()
        if pattern.name in names:
            return N.EnumPattern(None, pattern.name, [], line=pattern.line, col=pattern.col)
    return pattern
