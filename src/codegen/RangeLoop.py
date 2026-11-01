"""Allocation-free direct range iteration for the AST C emitter."""


def emit_range_loop(generator, start, end, inclusive, body, bind, out):
    out.append('{')
    generator._push_scope('block')
    outer = []
    first = generator.emit_value(start, outer)
    last = generator.emit_value(end, outer)
    count = generator.new_aux('_count')
    index = generator.new_aux('_idx')
    item = generator.new_aux('_item')
    outer.append(f'int32_t {count} = wysna_range_count({first}, {last}, {str(inclusive).lower()});')
    outer.append(f'for (int32_t {index} = 0; {index} < {count}; {index}++) {{')
    generator._push_scope('loop')
    inner = [f'WVal {item} = wysna_int(({first}).as.i + (int64_t){index});']
    bind(item, inner)
    value = generator.emit_block_value(body, inner)
    inner.append(f'(void){value};')
    generator._pop_scope(inner)
    outer.extend('    ' + line for line in inner)
    outer.append('}')
    generator._pop_scope(outer)
    out.extend('    ' + line for line in outer)
    out.append('}')
