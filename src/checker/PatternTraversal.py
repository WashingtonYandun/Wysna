"""Small read-only pattern queries used by coverage, lints and lowering."""
from node import NodeType as N


def binding_names(pattern):
    if isinstance(pattern, N.IdentifierPattern):
        return [] if pattern.name == 'None' else [pattern.name]
    if isinstance(pattern, N.OrPattern):
        return binding_names(pattern.alternatives[0])
    if isinstance(pattern, N.StructPattern):
        children = [field.pattern for field in pattern.fields]
    else:
        children = getattr(pattern, 'elements', getattr(pattern, 'args', []))
        if isinstance(pattern, N.ErrorTypePattern):
            children = [pattern.binding]
    return [name for child in children for name in binding_names(child)]


def integer_interval(pattern):
    if isinstance(pattern, N.RangePattern) or type(pattern).__name__ == 'HirRangePattern':
        return pattern.start, pattern.end if pattern.inclusive else pattern.end - 1
    if isinstance(pattern, N.LiteralPattern) and isinstance(pattern.literal, N.IntLiteral):
        return pattern.literal.value, pattern.literal.value
    if type(pattern).__name__ == 'HirLiteralPattern' and pattern.literal_kind == 'int':
        return pattern.value, pattern.value
    return None


def wildcard(pattern):
    return isinstance(pattern, N.WildcardPattern) or type(pattern).__name__ == 'HirWildcardPattern' or (
        isinstance(pattern, N.IdentifierPattern) or type(pattern).__name__ == 'HirBindingPattern') and pattern.name != 'None'


def subsumed(previous, pattern):
    if any(wildcard(item) for item in previous):
        return True
    expanded = [child for item in previous for child in (item.alternatives if isinstance(item, N.OrPattern) else [item])]
    if isinstance(pattern, N.OrPattern):
        return all(subsumed(expanded, child) for child in pattern.alternatives)
    interval = integer_interval(pattern)
    if interval is not None:
        start, end = interval
        for lower, upper in sorted(value for item in expanded if (value := integer_interval(item)) is not None):
            if lower > start:
                break
            if upper >= start:
                start = upper + 1
            if start > end:
                return True
        return False
    for item in expanded:
        if type(item) is not type(pattern):
            continue
        if isinstance(pattern, N.LiteralPattern) and item.literal.to_dict()['value'] == pattern.literal.to_dict()['value']:
            return True
        if isinstance(pattern, N.EnumPattern) and item.variant == pattern.variant:
            if all(subsumed([left], right) for left, right in zip(item.args, pattern.args)):
                return True
        if isinstance(pattern, N.StructPattern):
            fields = {field.name: field.pattern for field in pattern.fields}
            if all(field.name in fields and subsumed([field.pattern], fields[field.name]) for field in item.fields):
                return True
        if isinstance(pattern, N.TuplePattern) and all(subsumed([left], right) for left, right in zip(item.elements, pattern.elements)):
            return True
    return False
