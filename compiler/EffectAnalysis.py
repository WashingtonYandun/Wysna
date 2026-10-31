"""Explain verified MIR through a finite call-graph fixed point; never execute source."""
from dataclasses import asdict
import hashlib
import re
from compiler.EffectModel import CAPABILITIES, HOST_EFFECTS, MODEL_VERSION, OPERATION_EFFECTS, RUNTIME_EFFECTS
from common.Version import WYSNA_VERSION
from mir.Verifier import Verifier


def display_name(name):
    value = name.removeprefix('wysna_fn_')
    match = re.match(r'^__wysna_module_([0-9a-f]+)__(.+)$', value)
    if match:
        return bytes.fromhex(match[1]).decode('utf-8') + '::' + match[2]
    return value


def may_finalize_resource(type_id, interner):
    """Opaque owners, dynamic classes and captures may run host finalizers on release."""
    pending, seen = [type_id], set()
    while pending:
        current = pending.pop()
        if current in seen:
            continue
        seen.add(current)
        entry = interner.get_entry(current)
        if entry.tag in ('class', 'interface', 'function', 'any', 'type_param', 'error'):
            return True
        if entry.tag == 'primitive' and entry.data == 'opaque':
            return True
        if entry.tag in ('optional', 'list'):
            pending.append(entry.data)
        elif entry.tag in ('tuple', 'dict', 'result', 'error_union'):
            pending.extend(entry.data)
        elif entry.tag == 'struct':
            pending.extend(interner.struct_defs[entry.data].fields.values())
        elif entry.tag == 'enum':
            pending.extend(value for payload in interner.enum_defs[entry.data].variant_payloads.values() for value in payload)
    return False


def analyze_effects(module, interner, source_texts, trusted_externs=None):
    """Return an overapproximation conditional on explicitly listed runtime contracts.

    Unknown indirect/virtual calls and arbitrary FFI always propagate uncertainty.
    Memory effects include local state and are not a proof of observable impurity.
    """
    Verifier(interner).verify(module)
    trusted_externs = trusted_externs or {}
    rows = {}
    for function in module.functions:
        slots = {slot.id: slot.type_id for slot in [*module.globals, *function.slots]}
        effects, calls, witnesses, unknown = set(), set(), [], []
        for block in function.blocks:
            for operation in block.operations:
                selected, trust, target = set(), 'mir-model', None
                reason = None
                if operation.opcode == 'runtime':
                    target = operation.data['name']
                    if target in RUNTIME_EFFECTS:
                        selected.update(RUNTIME_EFFECTS[target])
                        trust = 'runtime-contract'
                    else:
                        reason = 'runtime operation has no effect contract'
                elif operation.opcode == 'call':
                    target = operation.data['name']
                    if target in module.externs:
                        native = module.externs[target]['native']
                        if trusted_externs.get(target) == native and native in HOST_EFFECTS:
                            selected.update(HOST_EFFECTS[native])
                            trust = 'bundled-host-contract'
                        else:
                            selected.add('ffi.native')
                            reason = 'external implementation has no trusted effect contract'
                    else:
                        calls.add(target)
                elif operation.opcode in ('indirect', 'virtual'):
                    reason = operation.opcode + ' call target is not resolved by this model'
                elif operation.opcode in OPERATION_EFFECTS:
                    selected.update(OPERATION_EFFECTS[operation.opcode])
                else:
                    reason = 'MIR operation has no effect contract'
                if ('memory.release' in selected or operation.opcode in ('store', 'store_place', 'mutate')):
                    affected = list(operation.args)
                    place = operation.data.get('place')
                    if place is not None:
                        affected.append(place.root)
                    if operation.result is not None:
                        affected.append(operation.result)
                    if any(slot in slots and may_finalize_resource(slots[slot], interner) for slot in affected):
                        selected.add('resource.finalize')
                        reason = reason or 'managed value may invoke a host resource finalizer'
                if any(isinstance(argument, int) and argument < 0 for argument in operation.args):
                    selected.add('global.read')
                place = operation.data.get('place')
                if (operation.result is not None and operation.result < 0
                        or place is not None and getattr(place, 'root', 0) < 0):
                    selected.add('global.write')
                witness = {'block': block.id, 'operation': operation.opcode,
                           'source': asdict(operation.source), 'target': target,
                           'effects': sorted(selected), 'trust': trust}
                if reason:
                    selected.add('unknown')
                    witness.update(effects=sorted(selected), trust='unknown', reason=reason)
                    unknown.append(witness)
                effects.update(selected)
                if selected or target or reason:
                    witnesses.append(witness)
        rows[function.name] = {'identity': function.name, 'name': display_name(function.name),
                               'source': asdict(function.source), 'direct_effects': sorted(effects),
                               'calls': sorted(calls), 'witnesses': witnesses,
                               'unknown_calls': unknown, 'transitive_effects': set(effects),
                               'unresolved_functions': {function.name} if unknown else set()}
    # Reverse edges and a worklist keep recursion and long chains bounded by the
    # finite effect/function lattice rather than Python recursion depth.
    parents = {name: set() for name in rows}
    for name, row in rows.items():
        for callee in row['calls']:
            parents[callee].add(name)
    from collections import deque
    pending = deque(rows)
    queued = set(rows)
    while pending:
        name = pending.popleft()
        queued.remove(name)
        row = rows[name]
        effects = set(row['direct_effects'])
        unresolved = {name} if row['unknown_calls'] else set()
        for callee in row['calls']:
            effects.update(rows[callee]['transitive_effects'])
            unresolved.update(rows[callee]['unresolved_functions'])
        if effects != row['transitive_effects'] or unresolved != row['unresolved_functions']:
            row['transitive_effects'], row['unresolved_functions'] = effects, unresolved
            for parent in parents[name] - queued:
                pending.append(parent)
                queued.add(parent)
    for row in rows.values():
        row['transitive_effects'] = sorted(row['transitive_effects'])
        row['unresolved_functions'] = sorted(row['unresolved_functions'])
        row['capabilities'] = sorted({CAPABILITIES[effect] for effect in row['transitive_effects'] if effect in CAPABILITIES})
        row['complete_within_model'] = not row['unresolved_functions']
    inputs = {path: hashlib.sha256(source.encode('utf-8')).hexdigest() for path, source in sorted(source_texts.items())}
    _compute_resolved_effects(module, interner, rows, trusted_externs)
    return {'schema_version': 'wysna.effect-report/1', 'compiler_version': WYSNA_VERSION,
            'analysis_scope': 'lowered-concrete-MIR',
            'model': MODEL_VERSION, 'entry_point': 'wysna_program', 'source_inputs': inputs,
            'mode': 'conservative-static-overapproximation',
            'trust': {'runtime': 'compiler runtime contract', 'host': 'bundled stdlib declarations only',
                      'external_ffi': 'unknown', 'indirect_and_virtual_calls': 'unknown'},
            'limitations': ['No program execution, FFI loading or native compilation is performed.',
                            'Uninstantiated generic templates have no function summary; instantiate them before analysis.',
                            'Reports depend on the explicit runtime and host effect contracts.',
                            'Unreachable branches may be included; unknowns prevent completeness claims.',
                            'This is not a security proof, sandbox, capability enforcement or performance estimate.'],
            'program': {key: rows['wysna_program'][key] for key in (
                'transitive_effects', 'capabilities', 'complete_within_model', 'unresolved_functions',
                'resolved_calls', 'resolved_effects', 'resolved_capabilities', 'resolved_complete',
            )},
            'functions': [rows[name] for name in sorted(rows)]}


def _may_finalize_opaque_resource(type_id, interner, module):
    """Return True only when releasing ``type_id`` can invoke an opaque host finalizer."""
    pending, seen = [type_id], set()
    while pending:
        current = pending.pop()
        if current in seen:
            continue
        seen.add(current)
        entry = interner.get_entry(current)
        if entry.tag == 'primitive' and entry.data == 'opaque':
            return True
        if entry.tag in ('any', 'type_param', 'error'):
            return True
        if entry.tag in ('optional', 'list', 'weak'):
            pending.append(entry.data)
        elif entry.tag in ('tuple', 'dict', 'result', 'error_union'):
            pending.extend(entry.data)
        elif entry.tag == 'struct':
            if current in module.layouts:
                pending.extend(ftype for _, ftype in module.layouts[current].fields)
            elif entry.data in interner.struct_defs:
                pending.extend(interner.struct_defs[entry.data].fields.values())
        elif entry.tag == 'class':
            if current in module.layouts:
                pending.extend(ftype for _, ftype in module.layouts[current].fields)
            else:
                for layout in module.layouts.values():
                    if layout.kind == 'class':
                        pending.extend(ftype for _, ftype in layout.fields)
        elif entry.tag == 'interface':
            for layout in module.layouts.values():
                if layout.kind == 'class':
                    pending.extend(ftype for _, ftype in layout.fields)
        elif entry.tag == 'enum':
            pending.extend(value for payload in interner.enum_defs[entry.data].variant_payloads.values() for value in payload)
        elif entry.tag == 'function':
            for layout in module.layouts.values():
                if layout.kind == 'struct' and layout.name.startswith('__wysna_closure_env'):
                    pending.extend(ftype for _, ftype in layout.fields)
    return False


def _compute_resolved_effects(module, interner, rows, trusted_externs):
    """Interprocedural closure and virtual call target propagation over verified MIR."""
    from collections import defaultdict, deque

    functions_by_name = {fn.name: fn for fn in module.functions}
    slot_targets = defaultdict(set)
    field_targets = defaultdict(set)

    def skey(fn_name, sid):
        return (fn_name, sid) if sid >= 0 else ('<global>', sid)

    def copy_slot(src_key, dst_key):
        changed = False
        src_set = slot_targets.get(src_key)
        if src_set:
            dst_set = slot_targets[dst_key]
            before = len(dst_set)
            dst_set.update(src_set)
            if len(dst_set) != before:
                changed = True
        for (owner, f_idx), targets in list(field_targets.items()):
            if owner == src_key and targets:
                dst_f = field_targets[(dst_key, f_idx)]
                before = len(dst_f)
                dst_f.update(targets)
                if len(dst_f) != before:
                    changed = True
        return changed

    for fn in module.functions:
        for block in fn.blocks:
            for op in block.operations:
                if op.opcode == 'function' and op.result is not None:
                    captured = bool(op.data.get('captured'))
                    env_key = skey(fn.name, op.args[0]) if (captured and op.args) else None
                    slot_targets[skey(fn.name, op.result)].add((op.data['name'], captured, env_key))

    changed = True
    iterations = 0
    max_iterations = max(32, len(module.functions) * 8)
    while changed and iterations < max_iterations:
        changed = False
        iterations += 1
        for fn in module.functions:
            for block in fn.blocks:
                for op in block.operations:
                    if op.opcode in ('store', 'clone', 'narrow') and op.result is not None and op.args:
                        if copy_slot(skey(fn.name, op.args[0]), skey(fn.name, op.result)):
                            changed = True
                    elif op.opcode == 'construct' and op.result is not None:
                        dst = skey(fn.name, op.result)
                        for idx, arg in enumerate(op.args):
                            src_set = slot_targets.get(skey(fn.name, arg))
                            if src_set:
                                for f_key in ((dst, idx), (dst, '*')):
                                    dst_f = field_targets[f_key]
                                    before = len(dst_f)
                                    dst_f.update(src_set)
                                    if len(dst_f) != before:
                                        changed = True
                    elif op.opcode in ('load_field', 'tuple_get') and op.result is not None and op.args:
                        src = skey(fn.name, op.args[0])
                        idx = op.data.get('index')
                        found = field_targets.get((src, idx), set()) | field_targets.get((src, '*'), set())
                        if found:
                            dst_set = slot_targets[skey(fn.name, op.result)]
                            before = len(dst_set)
                            dst_set.update(found)
                            if len(dst_set) != before:
                                changed = True
                    elif (op.opcode == 'runtime' and op.data.get('name') == 'wysna_index_get_owned_at'
                          and op.result is not None and op.args):
                        src = skey(fn.name, op.args[0])
                        found = field_targets.get((src, '*'), set())
                        if found:
                            dst_set = slot_targets[skey(fn.name, op.result)]
                            before = len(dst_set)
                            dst_set.update(found)
                            if len(dst_set) != before:
                                changed = True
                    elif op.opcode == 'iterate_value' and op.result is not None and op.args:
                        src = skey(fn.name, op.args[0])
                        found = field_targets.get((src, '*'), set())
                        if found:
                            dst_set = slot_targets[skey(fn.name, op.result)]
                            before = len(dst_set)
                            dst_set.update(found)
                            if len(dst_set) != before:
                                changed = True
                    elif op.opcode == 'store_place' and op.args:
                        place = op.data.get('place')
                        if place is not None:
                            root_key = skey(fn.name, place.root)
                            src_key = skey(fn.name, op.args[0])
                            if not place.projections:
                                if copy_slot(src_key, root_key):
                                    changed = True
                            elif len(place.projections) == 1 and place.projections[0].kind == 'field':
                                src_set = slot_targets.get(src_key)
                                if src_set:
                                    dst_f = field_targets[(root_key, place.projections[0].index)]
                                    before = len(dst_f)
                                    dst_f.update(src_set)
                                    if len(dst_f) != before:
                                        changed = True
                            elif any(p.kind == 'index' for p in place.projections):
                                src_set = slot_targets.get(src_key)
                                if src_set:
                                    dst_f = field_targets[(root_key, '*')]
                                    before = len(dst_f)
                                    dst_f.update(src_set)
                                    if len(dst_f) != before:
                                        changed = True
                    elif op.opcode == 'mutate' and op.args and op.data.get('method') == 'append':
                        place = op.data.get('place')
                        if place is not None:
                            root_key = skey(fn.name, place.root)
                            src_set = slot_targets.get(skey(fn.name, op.args[0]))
                            if src_set:
                                dst_f = field_targets[(root_key, '*')]
                                before = len(dst_f)
                                dst_f.update(src_set)
                                if len(dst_f) != before:
                                    changed = True
                    elif op.opcode == 'call':
                        callee = functions_by_name.get(op.data['name'])
                        if callee is not None:
                            for arg_sid, param_sid in zip(op.args, callee.params):
                                if copy_slot(skey(fn.name, arg_sid), (callee.name, param_sid)):
                                    changed = True
                            if op.result is not None:
                                for b in callee.blocks:
                                    if b.terminator and b.terminator.kind == 'return' and b.terminator.value is not None:
                                        if copy_slot((callee.name, b.terminator.value), skey(fn.name, op.result)):
                                            changed = True
                    elif op.opcode == 'indirect' and op.args:
                        targets = list(slot_targets.get(skey(fn.name, op.args[0]), ()))
                        for target_name, is_captured, env_key in targets:
                            callee = functions_by_name.get(target_name)
                            if callee is None:
                                continue
                            if is_captured and callee.params:
                                if env_key is not None and copy_slot(env_key, (callee.name, callee.params[0])):
                                    changed = True
                                user_params = callee.params[1:]
                            else:
                                user_params = callee.params
                            for arg_sid, param_sid in zip(op.args[1:], user_params):
                                if copy_slot(skey(fn.name, arg_sid), (callee.name, param_sid)):
                                    changed = True
                            if op.result is not None:
                                for b in callee.blocks:
                                    if b.terminator and b.terminator.kind == 'return' and b.terminator.value is not None:
                                        if copy_slot((callee.name, b.terminator.value), skey(fn.name, op.result)):
                                            changed = True

    direct_resolved_effects = {}
    resolved_edges = {}
    has_unresolved_local = {}

    for fn in module.functions:
        slots = {slot.id: slot.type_id for slot in [*module.globals, *fn.slots]}
        effs = set()
        calls = set(rows[fn.name]['calls'])
        unresolved = False
        for block in fn.blocks:
            for op in block.operations:
                selected = set()
                op_unresolved = False
                if op.opcode == 'runtime':
                    target = op.data['name']
                    if target in RUNTIME_EFFECTS:
                        selected.update(RUNTIME_EFFECTS[target])
                    else:
                        op_unresolved = True
                elif op.opcode == 'call':
                    target = op.data['name']
                    if target in module.externs:
                        native = module.externs[target]['native']
                        if trusted_externs.get(target) == native and native in HOST_EFFECTS:
                            selected.update(HOST_EFFECTS[native])
                        else:
                            selected.add('ffi.native')
                            op_unresolved = True
                    else:
                        calls.add(target)
                elif op.opcode == 'indirect':
                    targets = {t_name for t_name, _, _ in slot_targets.get(skey(fn.name, op.args[0]), ()) if t_name in rows}
                    if targets:
                        calls.update(targets)
                    else:
                        op_unresolved = True
                elif op.opcode == 'virtual':
                    slot_idx = op.data.get('slot', -1)
                    v_targets = set()
                    if 0 <= slot_idx < len(module.virtual_slots):
                        m_name, m_arity = module.virtual_slots[slot_idx]
                        for layout in module.layouts.values():
                            if layout.kind == 'class' and m_name in layout.methods and len(layout.methods[m_name][1]) == m_arity:
                                owner = layout.methods[m_name][0]
                                candidate = f'wysna_method_{owner}__{m_name}'
                                if candidate in rows:
                                    v_targets.add(candidate)
                    if v_targets:
                        calls.update(v_targets)
                    else:
                        op_unresolved = True
                elif op.opcode in OPERATION_EFFECTS:
                    selected.update(OPERATION_EFFECTS[op.opcode])
                else:
                    op_unresolved = True

                if ('memory.release' in selected or op.opcode in ('store', 'store_place', 'mutate')):
                    affected = list(op.args)
                    place = op.data.get('place')
                    if place is not None:
                        affected.append(place.root)
                    if op.result is not None:
                        affected.append(op.result)
                    if any(slot in slots and _may_finalize_opaque_resource(slots[slot], interner, module) for slot in affected):
                        selected.add('resource.finalize')
                        op_unresolved = True
                if any(isinstance(argument, int) and argument < 0 for argument in op.args):
                    selected.add('global.read')
                place = op.data.get('place')
                if (op.result is not None and op.result < 0
                        or place is not None and getattr(place, 'root', 0) < 0):
                    selected.add('global.write')
                if op_unresolved:
                    selected.add('unknown')
                    unresolved = True
                effs.update(selected)
        direct_resolved_effects[fn.name] = effs
        resolved_edges[fn.name] = calls
        has_unresolved_local[fn.name] = unresolved

    parents = {name: set() for name in rows}
    for name, callees in resolved_edges.items():
        for callee in callees:
            if callee in parents:
                parents[callee].add(name)

    trans_effs = {name: set(direct_resolved_effects[name]) for name in rows}
    trans_unres = {name: ({name} if has_unresolved_local[name] else set()) for name in rows}
    pending = deque(rows)
    queued = set(rows)
    while pending:
        name = pending.popleft()
        queued.remove(name)
        effs = set(direct_resolved_effects[name])
        unres = {name} if has_unresolved_local[name] else set()
        for callee in resolved_edges[name]:
            if callee in trans_effs:
                effs.update(trans_effs[callee])
                unres.update(trans_unres[callee])
        if effs != trans_effs[name] or unres != trans_unres[name]:
            trans_effs[name], trans_unres[name] = effs, unres
            for parent in parents[name] - queued:
                pending.append(parent)
                queued.add(parent)

    for name, row in rows.items():
        row['resolved_calls'] = sorted(resolved_edges[name])
        row['resolved_effects'] = sorted(trans_effs[name])
        row['resolved_capabilities'] = sorted({CAPABILITIES[e] for e in trans_effs[name] if e in CAPABILITIES})
        row['resolved_complete'] = not trans_unres[name]
