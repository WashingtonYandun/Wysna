"""Stackless CPS basic-block splitting plan for async MIR functions."""
from dataclasses import dataclass
from typing import Any, Optional, Tuple


@dataclass(frozen=True)
class CpsSuspendPoint:
    state_id: int
    await_arg: int
    await_res: int
    resume_label: str
    continue_label: str
    source: Any


@dataclass(frozen=True)
class CpsSegment:
    label: str
    loop_head: bool
    operations: Tuple[Any, ...]
    suspend: Optional[CpsSuspendPoint]
    terminator: Any


@dataclass(frozen=True)
class CpsPlan:
    segments: Tuple[CpsSegment, ...]
    suspend_points: Tuple[CpsSuspendPoint, ...]


def is_await_operation(operation) -> bool:
    return (
        getattr(operation, "opcode", None) == "runtime"
        and getattr(operation, "data", {}).get("name") == "wysna_await"
    )


def build_cps_plan(function) -> CpsPlan:
    """Split MIR basic blocks at every `wysna_await` boundary into a resumable CPS graph."""
    back_edge_targets = {
        target
        for block in function.blocks
        if block.terminator is not None
        for target in block.terminator.targets
        if target <= block.id
    }
    segments = []
    suspend_points = []
    next_state = 1

    for block in function.blocks:
        await_indices = [i for i, op in enumerate(block.operations) if is_await_operation(op)]
        is_loop_head = block.id == 0 or block.id in back_edge_targets
        if not await_indices:
            segments.append(
                CpsSegment(
                    label=f"bb{block.id}",
                    loop_head=is_loop_head,
                    operations=tuple(block.operations),
                    suspend=None,
                    terminator=block.terminator,
                )
            )
            continue

        start = 0
        cur_label = f"bb{block.id}"
        cur_loop = is_loop_head
        for seg_idx, aw_pos in enumerate(await_indices):
            op = block.operations[aw_pos]
            state_id = next_state
            next_state += 1
            cont_label = f"cps_cont_{block.id}_{seg_idx + 1}"
            sp = CpsSuspendPoint(
                state_id=state_id,
                await_arg=op.args[0],
                await_res=op.result,
                resume_label=f"cps_resume_{state_id}",
                continue_label=cont_label,
                source=op.source,
            )
            suspend_points.append(sp)
            segments.append(
                CpsSegment(
                    label=cur_label,
                    loop_head=cur_loop,
                    operations=tuple(block.operations[start:aw_pos]),
                    suspend=sp,
                    terminator=None,
                )
            )
            start = aw_pos + 1
            cur_label = cont_label
            cur_loop = False

        segments.append(
            CpsSegment(
                label=cur_label,
                loop_head=False,
                operations=tuple(block.operations[start:]),
                suspend=None,
                terminator=block.terminator,
            )
        )

    return CpsPlan(segments=tuple(segments), suspend_points=tuple(suspend_points))
