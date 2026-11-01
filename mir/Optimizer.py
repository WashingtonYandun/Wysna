"""MIR optimization passes: CFG simplification, dead block pruning, and ownership optimizations."""
from collections import deque
from mir.Nodes import Terminator
from mir.Ownership import optimize_local_cow_uniqueness, optimize_parameter_reads
from mir.Verifier import Verifier


def eliminate_unreachable_blocks(function) -> bool:
    """Traverse CFG from block 0, drop unreachable blocks, and renumber surviving blocks contiguously."""
    blocks_by_id = {block.id: block for block in function.blocks}
    reachable = set()
    pending = [0]
    while pending:
        bid = pending.pop()
        if bid in reachable or bid not in blocks_by_id:
            continue
        reachable.add(bid)
        block = blocks_by_id[bid]
        if block.terminator:
            for target in block.terminator.targets:
                if target in blocks_by_id and target not in reachable:
                    pending.append(target)

    if len(reachable) == len(function.blocks):
        return False

    surviving = [block for block in function.blocks if block.id in reachable]
    id_map = {block.id: idx for idx, block in enumerate(surviving)}
    for block in surviving:
        block.id = id_map[block.id]
        if block.terminator:
            block.terminator.targets = tuple(id_map[t] for t in block.terminator.targets)
    function.blocks = surviving
    return True


def simplify_branches(function) -> bool:
    """Fold branches on known constant booleans or branches whose targets are identical."""
    def_counts = {}
    mutated_slots = set()
    for block in function.blocks:
        for op in block.operations:
            if op.result is not None:
                def_counts[op.result] = def_counts.get(op.result, 0) + 1
            place = op.data.get('place')
            if place is not None:
                mutated_slots.add(place.root)

    constants = {}
    for block in function.blocks:
        for op in block.operations:
            if op.result is not None and def_counts.get(op.result) == 1 and op.result not in mutated_slots:
                if op.opcode == 'const' and isinstance(op.data.get('value'), bool):
                    constants[op.result] = op.data['value']
                elif (op.opcode in ('store', 'clone') and op.args and
                      op.args[0] not in mutated_slots and
                      def_counts.get(op.args[0]) == 1 and op.args[0] in constants):
                    constants[op.result] = constants[op.args[0]]

    changed = False
    for block in function.blocks:
        term = block.terminator
        if not term or term.kind != 'branch':
            continue
        # Case 1: Redundant branch where both arms jump to the same destination
        if len(term.targets) == 2 and term.targets[0] == term.targets[1]:
            term.kind = 'goto'
            term.targets = (term.targets[0],)
            term.value = None
            changed = True
            continue
        # Case 2: Constant boolean condition
        if term.value is not None and term.value in constants:
            val = constants[term.value]
            target = term.targets[0] if val else term.targets[1]
            term.kind = 'goto'
            term.targets = (target,)
            term.value = None
            changed = True

    return changed


def eliminate_empty_trampolines(function) -> bool:
    """Bypass empty blocks that only contain an unconditional goto (trampolines)."""
    blocks_by_id = {block.id: block for block in function.blocks}
    trampolines = {}
    for block in function.blocks:
        if (block.id != 0 and len(block.operations) == 0 and
                block.terminator and block.terminator.kind == 'goto' and
                len(block.terminator.targets) == 1):
            target = block.terminator.targets[0]
            if target != block.id and target in blocks_by_id:
                trampolines[block.id] = target

    if not trampolines:
        return False

    def resolve(target):
        seen = set()
        curr = target
        while curr in trampolines and curr not in seen:
            seen.add(curr)
            curr = trampolines[curr]
        return curr

    changed = False
    for block in function.blocks:
        if block.terminator and block.terminator.targets:
            new_targets = tuple(resolve(t) for t in block.terminator.targets)
            if new_targets != block.terminator.targets:
                block.terminator.targets = new_targets
                changed = True

    return changed


def simplify_function_cfg(function, max_iterations=10) -> bool:
    """Iteratively simplify CFG for a function until reaching a fixed point."""
    any_changed = False
    for _ in range(max_iterations):
        c1 = simplify_branches(function)
        c2 = eliminate_empty_trampolines(function)
        c3 = eliminate_unreachable_blocks(function)
        if not (c1 or c2 or c3):
            break
        any_changed = True
    return any_changed


def optimize_cfg(module, interner=None):
    """Run CFG optimizations across all functions in the module."""
    for function in module.functions:
        simplify_function_cfg(function)
    if interner is not None:
        Verifier(interner).verify(module)
    return module


def optimize(module, interner):
    """Run full verified MIR optimization pipeline."""
    # 1. Parameter copy elimination and local CoW uniqueness optimization
    optimize_parameter_reads(module, interner)
    optimize_local_cow_uniqueness(module, interner)
    # 2. CFG simplification and dead block pruning
    optimize_cfg(module, interner)
    # 3. Final verification
    Verifier(interner).verify(module)
    return module
