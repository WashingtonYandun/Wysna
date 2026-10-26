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
            'program': {key: rows['wysna_program'][key] for key in ('transitive_effects', 'capabilities', 'complete_within_model', 'unresolved_functions')},
            'functions': [rows[name] for name in sorted(rows)]}
