"""Deterministic host-effect recording and replay cassette runtime for Wysna."""

import json
import os
from typing import Any, Dict, List, Optional, Tuple
from error.Error import WysnaRuntimeError


TRACE_SCHEMA = "wysna.effect.trace/1"


class EffectTrace:
    """Records or replays non-deterministic host I/O, clock, env, process, and HTTP effects."""

    def __init__(
        self,
        mode: str,
        path: str,
        events: Optional[List[Dict[str, Any]]] = None,
    ) -> None:
        if mode not in ("record", "replay"):
            raise ValueError(f"Invalid EffectTrace mode: {mode}")
        self.mode = mode
        self.path = path
        self.events: List[Dict[str, Any]] = list(events) if events is not None else []
        self.cursor: int = 0
        self._consumed: List[bool] = [False] * len(self.events)

    @classmethod
    def recorder(cls, path: str) -> "EffectTrace":
        trace = cls("record", path, [])
        trace.flush()
        return trace

    @classmethod
    def replayer(cls, path: str) -> "EffectTrace":
        try:
            with open(path, "r", encoding="utf-8") as handle:
                payload = json.load(handle)
        except OSError as exc:
            raise WysnaRuntimeError(
                f"Unable to read effect replay trace '{path}': {exc}",
                code="W4003",
            )
        except ValueError as exc:
            raise WysnaRuntimeError(
                f"Malformed effect replay trace JSON in '{path}': {exc}",
                code="W4003",
            )
        if isinstance(payload, dict):
            schema = payload.get("schema")
            if schema != TRACE_SCHEMA:
                raise WysnaRuntimeError(
                    f"Unsupported effect trace schema '{schema}' in '{path}' (expected '{TRACE_SCHEMA}')",
                    code="W4003",
                )
            events = payload.get("events", [])
        elif isinstance(payload, list):
            events = payload
        else:
            raise WysnaRuntimeError(
                f"Invalid effect replay trace structure in '{path}'",
                code="W4003",
            )
        if not isinstance(events, list):
            raise WysnaRuntimeError(
                f"Invalid effect replay events list in '{path}'",
                code="W4003",
            )
        return cls("replay", path, events)

    @property
    def is_recording(self) -> bool:
        return self.mode == "record"

    @property
    def is_replaying(self) -> bool:
        return self.mode == "replay"

    def flush(self) -> None:
        if self.mode != "record" or not self.path:
            return
        parent = os.path.dirname(os.path.abspath(self.path))
        if parent:
            os.makedirs(parent, exist_ok=True)
        lines = [
            "{",
            f'  "schema": "{TRACE_SCHEMA}",',
            '  "events": [',
        ]
        for idx, event in enumerate(self.events):
            comma = "," if idx + 1 < len(self.events) else ""
            lines.append("    " + json.dumps(event, sort_keys=True) + comma)
        lines.append("  ]")
        lines.append("}\n")
        with open(self.path, "w", encoding="utf-8") as handle:
            handle.write("\n".join(lines))

    def record(
        self,
        op: str,
        args: List[Any],
        *,
        ok: bool = True,
        result: Any = None,
        error: Optional[str] = None,
    ) -> None:
        event: Dict[str, Any] = {
            "op": op,
            "args": list(args),
            "ok": bool(ok),
        }
        if ok:
            event["result"] = result
        else:
            event["error"] = error if error is not None else "recorded failure"
        self.events.append(event)
        self.flush()

    def replay(self, op: str, args: List[Any]) -> Tuple[bool, Any]:
        norm_args = list(args)
        # 1. Check current cursor first for exact sequential match.
        if self.cursor < len(self.events):
            candidate = self.events[self.cursor]
            if (
                not self._consumed[self.cursor]
                and candidate.get("op") == op
                and candidate.get("args") == norm_args
            ):
                self._consumed[self.cursor] = True
                self.cursor += 1
                return self._unpack_event(candidate)

        # 2. Scan remaining unconsumed events for matching (op, args).
        for idx in range(self.cursor, len(self.events)):
            if self._consumed[idx]:
                continue
            candidate = self.events[idx]
            if candidate.get("op") == op and candidate.get("args") == norm_args:
                self._consumed[idx] = True
                while self.cursor < len(self.events) and self._consumed[self.cursor]:
                    self.cursor += 1
                return self._unpack_event(candidate)

        # 3. For zero-arg clock queries (e.g. unix_time), allow sequential match on op alone.
        if not norm_args:
            for idx in range(self.cursor, len(self.events)):
                if not self._consumed[idx] and self.events[idx].get("op") == op:
                    self._consumed[idx] = True
                    while self.cursor < len(self.events) and self._consumed[self.cursor]:
                        self.cursor += 1
                    return self._unpack_event(self.events[idx])

        raise WysnaRuntimeError(
            f"Deterministic replay trace mismatch for '{op}' with args {json.dumps(norm_args)}: "
            f"no matching recorded event in '{self.path}'",
            code="W4003",
        )

    @staticmethod
    def _unpack_event(event: Dict[str, Any]) -> Tuple[bool, Any]:
        if event.get("ok", True):
            return True, event.get("result")
        return False, event.get("error", "replayed failure")
