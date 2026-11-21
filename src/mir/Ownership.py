"""Remove redundant copies of immutable string parameters with caller-owned lifetimes."""
from collections import Counter
from checker.TypeInterner import TYPE_STR
from mir.Verifier import Verifier


def optimize_parameter_reads(module, interner, *, verify_input=True, verify_output=True):
    """Substitute stable borrowed parameters only when no use consumes the copy."""
    verifier = Verifier(interner) if (verify_input or verify_output) else None
    if verify_input:
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
    if verify_output:
        verifier.verify(module)
    return module


def optimize_local_cow_uniqueness(module, interner, *, verify_input=True, verify_output=True):
    """Release single-block temporary clones immediately after their last read before CoW mutation."""
    verifier = Verifier(interner) if (verify_input or verify_output) else None
    if verify_input:
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

    if verify_output:
        verifier.verify(module)
    return hoisted_count


_READONLY_RUNTIME_ARGS = {
    'wysna_builtin_len': (0,),
    'wysna_contains': (0, 1),
    'wysna_index_get_owned_at': (0,),
    'wysna_dict_get': (0,),
    'wysna_dict_keys': (0,),
    'wysna_dict_values': (0,),
    'wysna_builtin_dict_items': (0,),
    'wysna_builtin_enumerate': (0,),
    'wysna_builtin_slice': (0,),
    'wysna_builtin_str': (0,),
    'wysna_str_trim': (0,),
    'wysna_str_to_lower': (0,),
    'wysna_str_to_upper': (0,),
    'wysna_str_split': (0, 1),
    'wysna_str_starts_with': (0, 1),
    'wysna_str_ends_with': (0, 1),
    'wysna_str_replace': (0, 1, 2),
    'wysna_builtin_ord': (0,),
    'wysna_builtin_bytes': (0,),
    'wysna_equal': (0, 1),
    'wysna_not_equal': (0, 1),
    'wysna_lt': (0, 1),
    'wysna_le': (0, 1),
    'wysna_gt': (0, 1),
    'wysna_ge': (0, 1),
    'wysna_neg': (0,),
    'wysna_not': (0,),
    'wysna_builtin_print': None,
    'wysna_builtin_eprint': None,
}


def _op_reads_readonly(op, target_slot, readonly_params):
    """Return True if every use of target_slot inside op is a non-mutating, non-escaping borrow."""
    kind = op.opcode
    if kind == 'release':
        return True
    place = op.data.get('place')
    if place is not None and place.root == target_slot:
        if kind in ('mutate', 'store_place'):
            return False
    for idx, arg in enumerate(op.args):
        if arg != target_slot:
            continue
        if idx in op.moves:
            return False
        if kind == 'runtime':
            rt_name = op.data.get('name')
            if rt_name not in _READONLY_RUNTIME_ARGS:
                return False
            allowed = _READONLY_RUNTIME_ARGS[rt_name]
            if allowed is not None and idx not in allowed:
                return False
        elif kind == 'call':
            callee_ro = readonly_params.get(op.data.get('name'))
            if callee_ro is None or idx not in callee_ro:
                return False
        elif kind in ('load_field', 'tuple_get', 'iterate_test', 'iterate_value'):
            continue
        else:
            return False
    return True


def _compute_readonly_params(module, interner):
    """Compute a fixed-point map of function_name -> set of provably read-only parameter slot ids."""
    readonly = {}
    for function in module.functions:
        eligible = set()
        for pid in function.params:
            entry = interner.get_entry(function.slots[pid].type_id)
            if entry is not None and entry.tag != 'class':
                eligible.add(pid)
        readonly[function.name] = eligible

    changed = True
    while changed:
        changed = False
        for function in module.functions:
            cand = readonly[function.name]
            if not cand:
                continue
            all_ops = [op for block in function.blocks for op in block.operations]
            definitions = Counter(op.result for op in all_ops if op.result is not None)
            returned = {
                block.terminator.value
                for block in function.blocks
                if block.terminator is not None and block.terminator.value is not None
            }
            next_cand = set(cand)
            for pid in cand:
                # Build closure of derived single-def non-mutated temporary clones from pid.
                group = {pid}
                grew = True
                while grew:
                    grew = False
                    for op in all_ops:
                        if (op.opcode == 'clone' and op.result is not None
                                and definitions[op.result] == 1 and op.args[0] in group
                                and op.result not in group):
                            slot = function.slots[op.result]
                            if slot.storage == 'value' and slot.ownership == 'owned' and slot.name == '':
                                group.add(op.result)
                                grew = True
                safe = True
                for s in group:
                    if s in returned:
                        safe = False
                        break
                    for op in all_ops:
                        if op.result == s and not (op.opcode == 'clone' and s != pid):
                            safe = False
                            break
                        if op.opcode == 'clone' and op.result in group:
                            continue
                        if s in op.args or (op.data.get('place') is not None and op.data['place'].root == s):
                            if not _op_reads_readonly(op, s, readonly):
                                safe = False
                                break
                    if not safe:
                        break
                if not safe:
                    next_cand.discard(pid)
            if next_cand != cand:
                readonly[function.name] = next_cand
                changed = True
    return readonly


def _slot_kills(function, src):
    """Return dict block_id -> set of op indices in block that overwrite, mutate, move, or release src."""
    kills = {}
    for block in function.blocks:
        idxs = set()
        for idx, op in enumerate(block.operations):
            place = op.data.get('place')
            if op.result == src:
                idxs.add(idx)
            elif place is not None and place.root == src and op.opcode in ('mutate', 'store_place'):
                idxs.add(idx)
            elif op.opcode == 'release' and op.args and op.args[0] == src:
                idxs.add(idx)
            elif any(op.args[m] == src for m in op.moves):
                idxs.add(idx)
        if idxs:
            kills[block.id] = idxs
    return kills


def _source_unmodified_until_uses(function, def_pos, use_positions, kills_by_block):
    """Return True if along every CFG path from def_pos to any use_pos, src is never killed."""
    blocks_by_id = {block.id: block for block in function.blocks}
    preds = {block.id: [] for block in function.blocks}
    for block in function.blocks:
        if block.terminator:
            for target in block.terminator.targets:
                if target in preds:
                    preds[target].append(block.id)

    def_bid, def_idx = def_pos
    for u_bid, u_idx in use_positions:
        # Walk backwards from just before (u_bid, u_idx) until hitting (def_bid, def_idx).
        visited_entries = set()
        work = []
        if u_bid == def_bid:
            if u_idx <= def_idx:
                return False
            blocked = False
            for k in range(def_idx + 1, u_idx):
                if k in kills_by_block.get(u_bid, ()):
                    blocked = True
                    break
            if blocked:
                return False
            # Also check loop edge: if def_pos can reach back through predecessors?
            # In SSA-like single block use after def without leaving block, def dominates directly.
            continue
        k_in_u = kills_by_block.get(u_bid, ())
        if any(0 <= k < u_idx for k in k_in_u):
            return False
        for p_id in preds.get(u_bid, ()):
            work.append(p_id)
        while work:
            bid = work.pop()
            if bid in visited_entries:
                continue
            visited_entries.add(bid)
            k_in_b = kills_by_block.get(bid, ())
            if bid == def_bid:
                if any(k > def_idx for k in k_in_b):
                    return False
                continue
            if bid == 0:
                return False
            if k_in_b:
                return False
            for p_id in preds.get(bid, ()):
                if p_id not in visited_entries:
                    work.append(p_id)
    return True


def optimize_interprocedural_cow(module, interner, *, verify_input=True, verify_output=True):
    """Eliminate read-only/non-escaping clones across callees and interprocedural call sites."""
    verifier = Verifier(interner) if (verify_input or verify_output) else None
    if verify_input:
        verifier.verify(module)
    readonly_params = _compute_readonly_params(module, interner)
    eliminated = 0

    for function in module.functions:
        # Fixed-point inside function so nested clones (e.g. a=clone(p); b=clone(a)) collapse cleanly.
        for _ in range(4):
            all_ops = [op for block in function.blocks for op in block.operations]
            definitions = Counter(op.result for op in all_ops if op.result is not None)
            protected = {
                block.terminator.value
                for block in function.blocks
                if block.terminator is not None and block.terminator.value is not None
            }
            for op in all_ops:
                protected.update(op.args[i] for i in op.moves)
                if op.opcode in ('test', 'narrow'):
                    protected.update(op.args)
                    if 'witness' in op.data:
                        protected.add(op.data['witness'])

            uses_by_slot = {}
            for block in function.blocks:
                for idx, op in enumerate(block.operations):
                    if op.opcode == 'release':
                        continue
                    reads = set(op.args)
                    place = op.data.get('place')
                    if place is not None:
                        reads.add(place.root)
                        reads.update(proj.index for proj in place.projections if proj.kind == 'index')
                    for sid in reads:
                        uses_by_slot.setdefault(sid, []).append((block.id, idx, op))

            replacements = {}
            kills_cache = {}
            blocks_by_id = {block.id: block for block in function.blocks}
            for block in function.blocks:
                for idx, op in enumerate(block.operations):
                    t = op.result
                    if op.opcode != 'clone' or t is None or t < 0 or t in protected or definitions[t] != 1:
                        continue
                    slot = function.slots[t]
                    if slot.name != '' or slot.parameter or slot.storage != 'value' or slot.ownership != 'owned':
                        continue
                    src = op.args[0]
                    if src < 0 or src >= len(function.slots):
                        continue
                    src_slot = function.slots[src]
                    if src_slot.ownership not in ('owned', 'borrowed') or src_slot.storage != 'value':
                        continue
                    src_entry = interner.get_entry(src_slot.type_id)
                    if src_entry is None or src_entry.tag == 'class':
                        continue
                    uses = uses_by_slot.get(t, [])
                    if not uses:
                        replacements[t] = src
                        continue
                    if not all(_op_reads_readonly(u_op, t, readonly_params) for _, _, u_op in uses):
                        continue
                    if any(u_op.data.get('place') is not None and any(proj.kind == 'index' and proj.index == t for proj in u_op.data['place'].projections) for _, _, u_op in uses):
                        continue
                    if src not in kills_cache:
                        kills_cache[src] = _slot_kills(function, src)
                    use_pos = [(u_bid, u_idx) for u_bid, u_idx, _ in uses]
                    if _source_unmodified_until_uses(function, (block.id, idx), use_pos, kills_cache[src]):
                        # Ensure replacing chain targets root source already active in this pass
                        replacements[t] = src

            if not replacements:
                break
            # Collapse chains within one pass cleanly (only apply non-chaining roots first).
            safe_replacements = {
                k: v for k, v in replacements.items() if v not in replacements
            }
            if not safe_replacements:
                break
            for block in function.blocks:
                new_ops = []
                for op in block.operations:
                    if op.opcode == 'clone' and op.result in safe_replacements:
                        eliminated += 1
                        continue
                    if op.opcode == 'release' and op.args and op.args[0] in safe_replacements:
                        continue
                    op.args = tuple(safe_replacements.get(a, a) for a in op.args)
                    new_ops.append(op)
                block.operations = new_ops

    if verify_output:
        verifier.verify(module)
    return eliminated
