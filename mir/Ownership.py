"""Remove redundant copies of immutable string parameters with caller-owned lifetimes."""
from collections import Counter
from checker.TypeInterner import TYPE_STR
from mir.Verifier import Verifier


def optimize_parameter_reads(module, interner):
    """Substitute stable borrowed parameters only when no use consumes the copy."""
    verifier = Verifier(interner)
    verifier.verify(module)
    for function in module.functions:
        operations = [operation for block in function.blocks for operation in block.operations]
        definitions = Counter(operation.result for operation in operations if operation.result is not None)
        parameters = {index for index in function.params if function.slots[index].type_id == TYPE_STR}
        replacements = {
            operation.result: operation.args[0]
            for operation in operations
            if operation.opcode == 'clone' and operation.args[0] in parameters
            and definitions[operation.result] == 1
        }
        # A move, return, or mutable place needs its own protected value. Parameters
        # themselves cannot be overwritten or consumed under the verified MIR ABI.
        protected = {block.terminator.value for block in function.blocks}
        for operation in operations:
            protected.update(operation.args[index] for index in operation.moves)
            place = operation.data.get('place')
            if place is not None:
                protected.add(place.root)
                protected.update(projection.index for projection in place.projections if projection.kind == 'index')
        replacements = {result: parameter for result, parameter in replacements.items() if result not in protected}
        if not replacements:
            continue
        for block in function.blocks:
            remaining = []
            for operation in block.operations:
                if operation.opcode == 'clone' and operation.result in replacements:
                    continue
                if operation.opcode == 'release' and operation.args[0] in replacements:
                    continue
                operation.args = tuple(replacements.get(argument, argument) for argument in operation.args)
                remaining.append(operation)
            block.operations = remaining
    verifier.verify(module)
    return module


def optimize_local_cow_uniqueness(module, interner):
    """Release single-block temporary clones immediately after their last read before CoW mutation."""
    verifier = Verifier(interner)
    verifier.verify(module)
    hoisted_count = 0
    for function in module.functions:
        all_ops = [op for block in function.blocks for op in block.operations]
        definitions = Counter(op.result for op in all_ops if op.result is not None)
        mutated_roots = set()
        for op in all_ops:
            if op.opcode in ('mutate', 'store_place'):
                place = op.data.get('place')
                if place is not None and place.root >= 0 and place.root not in function.params:
                    mutated_roots.add(place.root)
        if not mutated_roots:
            continue

        protected = {
            block.terminator.value
            for block in function.blocks
            if block.terminator is not None and block.terminator.value is not None
        }
        for op in all_ops:
            protected.update(op.args[idx] for idx in op.moves)
            if op.opcode in ('test', 'narrow'):
                protected.update(op.args)
                if 'witness' in op.data:
                    protected.add(op.data['witness'])
            place = op.data.get('place')
            if place is not None:
                protected.add(place.root)

        use_blocks = {}
        for block in function.blocks:
            for idx, op in enumerate(block.operations):
                if op.opcode == 'release':
                    continue
                reads = list(op.args)
                place = op.data.get('place')
                if place is not None:
                    reads.append(place.root)
                    reads.extend(proj.index for proj in place.projections if proj.kind == 'index')
                for slot_id in reads:
                    use_blocks.setdefault(slot_id, []).append((block.id, idx))

        to_hoist = {}
        for block in function.blocks:
            for def_idx, op in enumerate(block.operations):
                t = op.result
                if (op.opcode != 'clone' or t is None or t < 0
                        or t in protected or definitions[t] != 1):
                    continue
                slot = function.slots[t]
                if (slot.name != '' or slot.parameter or slot.storage != 'value'
                        or slot.ownership != 'owned'):
                    continue
                src = op.args[0]
                if src not in mutated_roots:
                    continue
                uses = use_blocks.get(t, [])
                if not uses or any(b_id != block.id or u_idx <= def_idx for b_id, u_idx in uses):
                    continue
                last_use = max(u_idx for _, u_idx in uses)
                scan = last_use + 1
                already_hoisted = False
                while scan < len(block.operations) and block.operations[scan].opcode == 'release':
                    if block.operations[scan].args == (t,):
                        already_hoisted = True
                        break
                    scan += 1
                if already_hoisted:
                    continue
                to_hoist[t] = (block.id, last_use, block.operations[last_use].source)

        if not to_hoist:
            continue

        from mir.Nodes import Operation
        hoisted_slots = set(to_hoist)
        for block in function.blocks:
            new_ops = []
            for idx, op in enumerate(block.operations):
                if op.opcode == 'release' and op.args and op.args[0] in hoisted_slots:
                    continue
                new_ops.append(op)
                for t, (target_bid, last_use, src_loc) in to_hoist.items():
                    if block.id == target_bid and idx == last_use:
                        new_ops.append(Operation('release', None, (t,), {}, (), src_loc))
                        hoisted_count += 1
            block.operations = new_ops

    verifier.verify(module)
    return hoisted_count
