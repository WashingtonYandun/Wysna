"""MIR optimization passes: CFG simplification, constant folding, move coalescing, dead op cleanup, and ownership optimizations."""
from collections import deque
from checker.TypeInterner import TYPE_BOOL, TYPE_INT
from mir.Nodes import Operation, Terminator
from mir.Ownership import optimize_interprocedural_cow, optimize_local_cow_uniqueness, optimize_parameter_reads
from mir.Verifier import Verifier


_PURE_SCALAR_RUNTIME_OPS = frozenset({
    'wysna_add', 'wysna_sub', 'wysna_mul', 'wysna_neg', 'wysna_not',
    'wysna_lt', 'wysna_le', 'wysna_gt', 'wysna_ge', 'wysna_equal', 'wysna_not_equal',
})


def _slot(function, sid):
    if sid is None or sid < 0 or sid >= len(function.slots):
        return None
    slot = function.slots[sid]
    return slot if slot.id == sid else None


def _wrap_i64(value: int) -> int:
    masked = value & 0xFFFFFFFFFFFFFFFF
    return masked - 0x10000000000000000 if masked & 0x8000000000000000 else masked


def _place_slots(place):
    if place is None:
        return ()
    slots = [place.root]
    for proj in getattr(place, 'projections', ()):
        if getattr(proj, 'kind', None) == 'index' and proj.index is not None:
            slots.append(proj.index)
    return tuple(slots)


def _op_mentions_slot(op, target: int) -> bool:
    return (
        op.result == target
        or target in op.args
        or target in _place_slots(op.data.get('place'))
    )


def _collect_slot_facts(function):
    def_counts = {}
    mutated_slots = {slot.id for slot in function.slots if slot.parameter}
    non_release_reads = set()
    release_slots = set()
    for block in function.blocks:
        for op in block.operations:
            if op.result is not None:
                def_counts[op.result] = def_counts.get(op.result, 0) + 1
            if op.data.get('placeholder') and op.result is not None:
                mutated_slots.add(op.result)
            if op.opcode == 'increment':
                mutated_slots.update(op.args)
            place = op.data.get('place')
            if place is not None:
                mutated_slots.add(place.root)
                non_release_reads.update(_place_slots(place))
            if op.opcode == 'release':
                release_slots.update(op.args)
            else:
                non_release_reads.update(op.args)
        term = block.terminator
        if term and term.value is not None:
            non_release_reads.add(term.value)
    return def_counts, mutated_slots, non_release_reads, release_slots


def _eval_pure_scalar_runtime(name, args, result_type, constants):
    if name == 'wysna_not' and len(args) == 1 and result_type == TYPE_BOOL:
        t0, v0 = constants[args[0]]
        if t0 == TYPE_BOOL:
            return not bool(v0)
        return None
    if name == 'wysna_neg' and len(args) == 1 and result_type == TYPE_INT:
        t0, v0 = constants[args[0]]
        if t0 == TYPE_INT and type(v0) is int:
            return _wrap_i64(-v0)
        return None
    if len(args) != 2:
        return None
    t0, v0 = constants[args[0]]
    t1, v1 = constants[args[1]]
    if t0 == TYPE_INT and t1 == TYPE_INT and type(v0) is int and type(v1) is int:
        if result_type == TYPE_INT:
            if name == 'wysna_add':
                return _wrap_i64(v0 + v1)
            if name == 'wysna_sub':
                return _wrap_i64(v0 - v1)
            if name == 'wysna_mul':
                return _wrap_i64(v0 * v1)
        elif result_type == TYPE_BOOL:
            if name == 'wysna_lt':
                return v0 < v1
            if name == 'wysna_le':
                return v0 <= v1
            if name == 'wysna_gt':
                return v0 > v1
            if name == 'wysna_ge':
                return v0 >= v1
            if name == 'wysna_equal':
                return v0 == v1
            if name == 'wysna_not_equal':
                return v0 != v1
    elif t0 == TYPE_BOOL and t1 == TYPE_BOOL and type(v0) is bool and type(v1) is bool and result_type == TYPE_BOOL:
        if name == 'wysna_equal':
            return v0 == v1
        if name == 'wysna_not_equal':
            return v0 != v1
    return None


def fold_scalar_constants(function) -> bool:
    """Fold SSA and straight-line block integer/boolean expressions into MIR constants."""
    def_counts, mutated_slots, _, _ = _collect_slot_facts(function)
    global_consts = {}
    for _ in range(8):
        added = False
        for block in function.blocks:
            for op in block.operations:
                r = op.result
                slot = _slot(function, r)
                if slot is None or def_counts.get(r) != 1 or r in mutated_slots:
                    continue
                if slot.type_id not in (TYPE_INT, TYPE_BOOL) or slot.ownership != 'owned':
                    continue
                if op.opcode == 'const' and not op.data.get('placeholder'):
                    val = op.data.get('value')
                    expected_py = bool if slot.type_id == TYPE_BOOL else int
                    if type(val) is expected_py and r not in global_consts:
                        global_consts[r] = (slot.type_id, val)
                        added = True
                elif op.opcode in ('clone', 'store') and len(op.args) == 1 and op.args[0] in global_consts:
                    src_tid, src_val = global_consts[op.args[0]]
                    if src_tid == slot.type_id and op.args[0] not in mutated_slots and r not in global_consts:
                        global_consts[r] = (src_tid, src_val)
                        added = True
        if not added:
            break

    changed = False
    for block in function.blocks:
        local_consts = dict(global_consts)
        for idx, op in enumerate(block.operations):
            place = op.data.get('place')
            if place is not None:
                local_consts.pop(place.root, None)
            if op.opcode == 'increment':
                for arg in op.args:
                    local_consts.pop(arg, None)

            r = op.result
            slot = _slot(function, r)
            if slot is None or slot.ownership != 'owned' or slot.type_id not in (TYPE_INT, TYPE_BOOL):
                if r is not None:
                    local_consts.pop(r, None)
                continue

            if op.opcode == 'const' and not op.data.get('placeholder'):
                val = op.data.get('value')
                expected_py = bool if slot.type_id == TYPE_BOOL else int
                if type(val) is expected_py:
                    local_consts[r] = (slot.type_id, val)
                else:
                    local_consts.pop(r, None)
            elif op.opcode in ('clone', 'store') and len(op.args) == 1 and op.args[0] in local_consts:
                src_tid, src_val = local_consts[op.args[0]]
                if src_tid == slot.type_id and not op.data.get('placeholder'):
                    local_consts[r] = (src_tid, src_val)
                else:
                    local_consts.pop(r, None)
            elif op.opcode == 'runtime' and not op.moves and all(arg in local_consts for arg in op.args):
                folded = _eval_pure_scalar_runtime(op.data.get('name', ''), op.args, slot.type_id, local_consts)
                if folded is not None:
                    block.operations[idx] = Operation('const', r, (), {'value': folded}, (), op.source)
                    local_consts[r] = (slot.type_id, folded)
                    if def_counts.get(r) == 1 and r not in mutated_slots:
                        global_consts[r] = (slot.type_id, folded)
                    changed = True
                else:
                    local_consts.pop(r, None)
            else:
                local_consts.pop(r, None)

        term = block.terminator
        if term and term.kind == 'branch' and term.value in local_consts:
            tid, val = local_consts[term.value]
            if tid == TYPE_BOOL and type(val) is bool and len(term.targets) == 2:
                term.kind = 'goto'
                term.targets = (term.targets[0] if val else term.targets[1],)
                term.value = None
                changed = True
    return changed


def coalesce_temporary_stores(function) -> bool:
    """Eliminate temporary -> store(moves=(0,)) hops when destination is an unaliased local."""
    def_counts, mutated_slots, _, _ = _collect_slot_facts(function)
    non_release_reads = {}
    terminator_reads = set()
    place_reads = set()
    for block in function.blocks:
        for bid_op_idx, op in enumerate(block.operations):
            if op.opcode != 'release':
                for arg in op.args:
                    non_release_reads.setdefault(arg, []).append((block.id, bid_op_idx))
            place_reads.update(_place_slots(op.data.get('place')))
        if block.terminator and block.terminator.value is not None:
            terminator_reads.add(block.terminator.value)

    changed = False
    retired_temps = set()
    for block in function.blocks:
        idx = 0
        while idx < len(block.operations):
            op = block.operations[idx]
            tmp = op.result
            slot_tmp = _slot(function, tmp)
            if (
                slot_tmp is None
                or op.opcode in ('increment', 'release')
                or op.data.get('placeholder')
                or def_counts.get(tmp) != 1
                or tmp in mutated_slots
                or tmp in terminator_reads
                or tmp in place_reads
            ):
                idx += 1
                continue
            reads = non_release_reads.get(tmp, [])
            if len(reads) != 1 or reads[0][0] != block.id or reads[0][1] <= idx:
                idx += 1
                continue
            store_idx = reads[0][1]
            store_op = block.operations[store_idx]
            dst = store_op.result
            slot_dst = _slot(function, dst)
            if (
                slot_dst is None
                or store_op.opcode != 'store'
                or store_op.data.get('placeholder')
                or store_op.args != (tmp,)
                or store_op.moves != (0,)
                or dst == tmp
                or dst in op.args
                or def_counts.get(dst) != 1
                or slot_dst.parameter
                or slot_dst.ownership != 'owned'
                or slot_tmp.ownership != 'owned'
                or slot_tmp.type_id != slot_dst.type_id
                or slot_tmp.storage != slot_dst.storage
            ):
                idx += 1
                continue
            if any(_op_mentions_slot(block.operations[k], dst) for k in range(idx, store_idx)):
                idx += 1
                continue
            op.result = dst
            block.operations.pop(store_idx)
            retired_temps.add(tmp)
            for read_list in non_release_reads.values():
                for pos, (bid, ridx) in enumerate(read_list):
                    if bid == block.id and ridx > store_idx:
                        read_list[pos] = (bid, ridx - 1)
            changed = True

    if retired_temps:
        for block in function.blocks:
            block.operations = [
                op for op in block.operations
                if not (op.opcode == 'release' and len(op.args) == 1 and op.args[0] in retired_temps)
            ]
    return changed


def _is_pure_eliminable_op(function, op) -> bool:
    if op.opcode == 'const':
        return True
    if op.opcode == 'clone':
        return len(op.args) == 1 and not op.moves
    if op.opcode == 'store':
        res_slot = _slot(function, op.result)
        arg_slot = _slot(function, op.args[0]) if len(op.args) == 1 else None
        return res_slot is not None and arg_slot is not None and res_slot.storage != 'value' and arg_slot.storage != 'value'
    if op.opcode == 'runtime' and op.data.get('name') in _PURE_SCALAR_RUNTIME_OPS and not op.moves:
        res_slot = _slot(function, op.result)
        if res_slot is None or res_slot.storage == 'value':
            return False
        return all((arg_slot := _slot(function, a)) is not None and arg_slot.storage != 'value' for a in op.args)
    return False


def eliminate_dead_pure_ops(function) -> bool:
    """Remove unread pure computations, placeholders, overwritten local consts, and dead value+release pairs."""
    def_counts, _, non_release_reads, release_slots = _collect_slot_facts(function)
    dead_op_ids = set()
    pair_dead = set()
    for block in function.blocks:
        last_unconsumed_write = {}
        for idx, op in enumerate(block.operations):
            for arg in op.args:
                if op.opcode != 'release':
                    last_unconsumed_write.pop(arg, None)
            for ps in _place_slots(op.data.get('place')):
                last_unconsumed_write.pop(ps, None)
            r = op.result
            slot = _slot(function, r)
            if slot is None:
                continue
            if r in last_unconsumed_write:
                prev_op = last_unconsumed_write[r]
                if slot.storage != 'value' and _is_pure_eliminable_op(function, prev_op):
                    dead_op_ids.add(id(prev_op))
                last_unconsumed_write.pop(r, None)
            if _is_pure_eliminable_op(function, op) and slot.storage != 'value' and not op.data.get('placeholder'):
                last_unconsumed_write[r] = op

            if r in non_release_reads or not _is_pure_eliminable_op(function, op):
                continue
            if r not in release_slots and (slot.storage != 'value' or op.data.get('placeholder')):
                dead_op_ids.add(id(op))
            elif (
                slot.storage == 'value'
                and slot.ownership == 'owned'
                and def_counts.get(r) == 1
                and not op.moves
                and ((op.opcode == 'const' and op.data.get('value') is None) or not slot.name)
            ):
                pair_dead.add(r)

    if not dead_op_ids and not pair_dead:
        return False

    changed = False
    for block in function.blocks:
        filtered = []
        for op in block.operations:
            if id(op) in dead_op_ids:
                changed = True
                continue
            if op.opcode == 'release' and len(op.args) == 1 and op.args[0] in pair_dead:
                changed = True
                continue
            if op.result is not None and op.result in pair_dead and _is_pure_eliminable_op(function, op):
                changed = True
                continue
            filtered.append(op)
        block.operations = filtered
    return changed


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
        if len(term.targets) == 2 and term.targets[0] == term.targets[1]:
            term.kind = 'goto'
            term.targets = (term.targets[0],)
            term.value = None
            changed = True
            continue
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


def merge_linear_blocks(function) -> bool:
    """Merge B into A when A ends in goto(B) and B has A as its sole predecessor."""
    pred_counts = {block.id: 0 for block in function.blocks}
    blocks_by_id = {block.id: block for block in function.blocks}
    for block in function.blocks:
        if block.terminator:
            for target in block.terminator.targets:
                if target in pred_counts:
                    pred_counts[target] += 1

    changed = False
    merged_away = set()
    for block in function.blocks:
        if block.id in merged_away:
            continue
        while (
            block.terminator
            and block.terminator.kind == 'goto'
            and len(block.terminator.targets) == 1
        ):
            target = block.terminator.targets[0]
            if target == 0 or target == block.id or target in merged_away:
                break
            if pred_counts.get(target, 0) != 1:
                break
            succ = blocks_by_id.get(target)
            if succ is None:
                break
            block.operations.extend(succ.operations)
            block.terminator = succ.terminator
            succ.operations = []
            succ.terminator = Terminator('goto', (target,))
            merged_away.add(target)
            changed = True
    return changed


def simplify_function_cfg(function, max_iterations=16) -> bool:
    """Iteratively simplify CFG for a function until reaching a fixed point."""
    any_changed = False
    for _ in range(max_iterations):
        c1 = simplify_branches(function)
        c2 = eliminate_empty_trampolines(function)
        c3 = merge_linear_blocks(function)
        c4 = eliminate_unreachable_blocks(function)
        if not (c1 or c2 or c3 or c4):
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
    optimize_parameter_reads(module, interner)
    optimize_local_cow_uniqueness(module, interner)
    optimize_interprocedural_cow(module, interner)
    for function in module.functions:
        for _ in range(24):
            c1 = fold_scalar_constants(function)
            c2 = coalesce_temporary_stores(function)
            c3 = eliminate_dead_pure_ops(function)
            c4 = simplify_function_cfg(function)
            if not (c1 or c2 or c3 or c4):
                break
    Verifier(interner).verify(module)
    return module
