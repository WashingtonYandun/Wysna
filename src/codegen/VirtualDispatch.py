"""Specialized class vtables using one compatible C invocation signature."""


def emit_vtables(class_names, methods, resolve):
    definitions = []
    adapters = set()
    tables = []
    for class_name in class_names:
        slots = []
        for name, arity in methods:
            resolved = resolve(class_name, name)
            if resolved is None or len(resolved[1].params) != arity:
                slots.append('NULL')
                continue
            owner, _ = resolved
            adapter = 'wysna_vinvoke_' + owner + '__' + name
            slots.append(adapter)
            if adapter not in adapters:
                adapters.add(adapter)
                arguments = ', '.join(['receiver'] + [f'argv[{index}]' for index in range(arity)])
                definitions.append(f'static WVal {adapter}(WVal receiver, int32_t argc, const WVal* argv) {{ '
                                   f'(void)argc; (void)argv; return wysna_method_{owner}__{name}({arguments}); }}')
        if slots:
            tables.append(f'static const WInvoke wysna_slots_{class_name}[{len(slots)}] = {{ ' + ', '.join(slots) + ' };')
            tables.append(f'static const WClassVTable wysna_vtable_{class_name} = {{ {len(slots)}, wysna_slots_{class_name} }};')
        else:
            tables.append(f'static const WClassVTable wysna_vtable_{class_name} = {{ 0, NULL }};')
    definitions.extend(tables)
    for slot, (name, arity) in enumerate(methods):
        parameters = ', '.join(['WVal v_self'] + [f'WVal _a{index}' for index in range(arity)])
        arguments = '(WVal[]){ ' + ', '.join(f'_a{index}' for index in range(arity)) + ' }' if arity else 'NULL'
        definitions.append(f'static WVal wysna_dispatch__{name}__{arity}({parameters}) {{ '
                           f'return wysna_virtual_call(v_self, {slot}, {arity}, {arguments}); }}')
    return definitions



def virtual_method_slots(signatures):
    """Canonical slots shared by HIR lowering, verification and emission."""
    return sorted(set(signatures))


def hir_virtual_method_slots(module, interner):
    signatures = [(method.name, len(method.params))
                  for declaration in module.classes for method in declaration.methods
                  if method.name != "init"]
    signatures.extend((name, len(interner.get_entry(type_id).data[0]))
                      for declaration in module.interfaces for name, type_id in declaration.methods)
    return virtual_method_slots(signatures)
