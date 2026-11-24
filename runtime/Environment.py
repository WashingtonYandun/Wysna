import atexit
import os
import math
import re
import sys
import time
from typing import Any, Dict, List, Optional, Set, TextIO
from error.Error import WysnaRuntimeError
from runtime.Values import (
    WysnaValue, WysnaInt, WysnaFloat, WysnaBool, WysnaStr, WysnaUnit, WysnaNone,
    WysnaList, WysnaDict, WysnaTuple, WysnaStructInstance, WysnaEnumValue, WysnaEnumNamespace,
    WysnaOk, WysnaErr, WysnaWeak, WysnaObjectInstance, WysnaFunction, WysnaNativeFunction, cow_telemetry
)
from runtime.ExecutionContext import ExecutionContext

# ==========================================
# Control Flow Signals
# ==========================================

class ReturnSignal(Exception):
    def __init__(self, value: WysnaValue):
        self.value = value


class BreakSignal(Exception):
    pass


class ContinueSignal(Exception):
    pass


class PropagationSignal(Exception):
    """Signal used by postfix ? unwrapping for early return/propagation."""
    def __init__(self, value: WysnaValue, is_error: bool = False):
        self.value = value
        self.is_error = is_error


# ==========================================
# Execution Environment
# ==========================================

class Environment:
    def __init__(self, parent: Optional['Environment'] = None, is_function_boundary: bool = False):
        self.parent = parent
        self.is_function_boundary = is_function_boundary
        self.bindings: Dict[str, WysnaValue] = {}
        self.mutables: Set[str] = set()
        self._declaration_journal = None

    def declare(self, name: str, value: WysnaValue, is_mut: bool = False) -> None:
        if self._declaration_journal is not None and name not in self._declaration_journal:
            self._declaration_journal[name] = (
                name in self.bindings, self.bindings.get(name), name in self.mutables
            )
        self.bindings[name] = value.clone_value()
        if is_mut:
            self.mutables.add(name)
        else:
            self.mutables.discard(name)

    def begin_declaration_journal(self) -> None:
        if self._declaration_journal is not None:
            raise RuntimeError("A declaration journal is already active")
        self._declaration_journal = {}

    def end_declaration_journal(self, *, rollback: bool) -> None:
        journal = self._declaration_journal
        if journal is None:
            raise RuntimeError("No declaration journal is active")
        self._declaration_journal = None
        if not rollback:
            return
        for name, (existed, value, mutable) in journal.items():
            if existed:
                self.bindings[name] = value
            else:
                self.bindings.pop(name, None)
            if mutable:
                self.mutables.add(name)
            else:
                self.mutables.discard(name)

    def assign(self, name: str, value: WysnaValue) -> None:
        if name in self.bindings:
            if name not in self.mutables:
                raise WysnaRuntimeError(f"Cannot assign to immutable binding '{name}'")
            self.bindings[name] = value.clone_value()
            return
        if self.parent is not None:
            self.parent.assign(name, value)
            return
        raise WysnaRuntimeError(f"Undefined variable '{name}'")

    def lookup(self, name: str) -> WysnaValue:
        if name in self.bindings:
            return self.bindings[name]
        if self.parent is not None:
            return self.parent.lookup(name)
        raise WysnaRuntimeError(f"Undefined variable '{name}'")

    def exists(self, name: str) -> bool:
        if name in self.bindings:
            return True
        if self.parent is not None:
            return self.parent.exists(name)
        return False

    # Backward compatibility aliases
    def declare_var(self, name: str, value: WysnaValue) -> WysnaValue:
        self.declare(name, value, is_mut=True)
        return value

    def assign_var(self, name: str, value: WysnaValue) -> WysnaValue:
        self.assign(name, value)
        return value

    def lookup_var(self, name: str) -> WysnaValue:
        return self.lookup(name)


def create_global_environment(stdout: Optional[TextIO] = None,
                              execution_context: Optional[ExecutionContext] = None) -> Environment:
    """Pre-populates the top-level execution environment with Wysna standard built-ins."""
    env = Environment()
    context = execution_context or ExecutionContext.from_process(stdout=stdout)
    out = stdout if stdout is not None else context.stdout

    # 1. I/O: print
    def print_fn(args: List[WysnaValue]) -> WysnaValue:
        rendered: List[str] = []
        for arg in args:
            if isinstance(arg, WysnaStr):
                rendered.append(arg.value)
            else:
                rendered.append(str(arg))
        out.write(" ".join(rendered) + "\n")
        out.flush()
        return WysnaUnit()

    def eprint_fn(args: List[WysnaValue]) -> WysnaValue:
        rendered: List[str] = []
        for arg in args:
            if isinstance(arg, WysnaStr):
                rendered.append(arg.value)
            else:
                rendered.append(str(arg))
        msg = " ".join(rendered)
        if (context.env.get("WYSNA_COW_METRICS") == "1" and len(args) == 1
                and isinstance(args[0], WysnaStr) and msg.startswith("__COW_CHECKPOINT__:")):
            label = msg[19:]
            context.stderr.write(
                f"[WYSNA_COW_CHECKPOINT:{label}] detach_list={cow_telemetry.detach_list} "
                f"detach_dict={cow_telemetry.detach_dict} detach_struct={cow_telemetry.detach_struct} "
                f"copied_slots={cow_telemetry.copied_slots}\n"
            )
        else:
            context.stderr.write(msg + "\n")
        context.stderr.flush()
        return WysnaUnit()

    def exit_fn(args: List[WysnaValue]) -> WysnaValue:
        code = int(args[0].value) if args and isinstance(args[0], WysnaInt) else 0
        context.terminate(code)

    env.declare("print", WysnaNativeFunction("print", print_fn))
    env.declare("eprint", WysnaNativeFunction("eprint", eprint_fn))
    env.declare("exit", WysnaNativeFunction("exit", exit_fn))

    # 2. Collections: len
    def len_fn(args: List[WysnaValue]) -> WysnaValue:
        if len(args) != 1:
            raise WysnaRuntimeError("len() takes exactly 1 argument")
        arg = args[0]
        if isinstance(arg, WysnaList):
            return WysnaInt(len(arg))
        if isinstance(arg, WysnaDict):
            return WysnaInt(len(arg))
        if isinstance(arg, WysnaTuple):
            return WysnaInt(len(arg.elements))
        if isinstance(arg, WysnaStr):
            return WysnaInt(len(arg.value))
        raise WysnaRuntimeError(f"len() not supported for '{type(arg).__name__}'")

    # 3. enumerate: list -> list of (int, T)
    def enumerate_fn(args: List[WysnaValue]) -> WysnaValue:
        if len(args) != 1:
            raise WysnaRuntimeError("enumerate() takes exactly 1 argument")
        arg = args[0]
        if not isinstance(arg, WysnaList):
            raise WysnaRuntimeError("enumerate() expects list argument")
        tuples = [WysnaTuple([WysnaInt(i), elem]) for i, elem in enumerate(arg.storage.data)]
        return WysnaList(elements=tuples)

    # 4. dict_items: dict -> list of (K, V)
    def dict_items_fn(args: List[WysnaValue]) -> WysnaValue:
        if len(args) != 1:
            raise WysnaRuntimeError("dict_items() takes exactly 1 argument")
        arg = args[0]
        if not isinstance(arg, WysnaDict):
            raise WysnaRuntimeError("dict_items() expects dict argument")
        tuples = []
        for k, v in arg.storage.data.items():
            k_val = k.clone_value()
            tuples.append(WysnaTuple([k_val, v]))
        return WysnaList(elements=tuples)

    # 5. Type conversions
    def int_fn(args: List[WysnaValue]) -> WysnaValue:
        if len(args) != 1:
            raise WysnaRuntimeError("int() takes exactly 1 argument")
        arg = args[0]
        if isinstance(arg, WysnaBool):
            return WysnaInt(1 if arg.value else 0)
        if isinstance(arg, WysnaInt):
            return arg
        if isinstance(arg, WysnaFloat):
            if not math.isfinite(arg.value) or not -9223372036854775808 <= arg.value < 9223372036854775808:
                raise WysnaRuntimeError("Invalid int conversion", code="W4003")
            return WysnaInt(int(arg.value))
        if isinstance(arg, WysnaStr):
            text = arg.value.strip(" \t\n\r\v\f")
            if re.fullmatch(r"[+-]?[0-9]+", text) is None:
                raise WysnaRuntimeError("Invalid int conversion", code="W4003")
            significant = text.lstrip('+-').lstrip('0') or '0'
            if len(significant) > 19:
                raise WysnaRuntimeError("Invalid int conversion", code="W4003")
            value = int(significant) * (-1 if text.startswith('-') else 1)
            if not -9223372036854775808 <= value <= 9223372036854775807:
                raise WysnaRuntimeError("Invalid int conversion", code="W4003")
            return WysnaInt(value)
        raise WysnaRuntimeError(f"Cannot convert '{type(arg).__name__}' to int")

    def u8_fn(args: List[WysnaValue]) -> WysnaValue:
        if len(args) != 1 or not isinstance(args[0], WysnaInt):
            raise WysnaRuntimeError("u8() expects one int", code="W4003")
        value = args[0]
        if 0 <= value.value <= 255:
            return WysnaEnumValue("Option", "Some", [value])
        return WysnaNone()

    def float_fn(args: List[WysnaValue]) -> WysnaValue:
        if len(args) != 1:
            raise WysnaRuntimeError("float() takes exactly 1 argument")
        arg = args[0]
        if isinstance(arg, WysnaBool):
            return WysnaFloat(1.0 if arg.value else 0.0)
        if isinstance(arg, WysnaFloat):
            return arg
        if isinstance(arg, WysnaInt):
            return WysnaFloat(float(arg.value))
        if isinstance(arg, WysnaStr):
            try:
                if "\0" in arg.value or "_" in arg.value:
                    raise ValueError()
                text = arg.value.strip(" \t\n\r\v\f")
                grammar = r"[+-]?(?:(?:[0-9]+(?:\.[0-9]*)?|\.[0-9]+)(?:[eE][+-]?[0-9]+)?|inf(?:inity)?|nan)"
                if re.fullmatch(grammar, text, re.IGNORECASE | re.ASCII) is None:
                    raise ValueError()
                return WysnaFloat(float(text))
            except ValueError:
                raise WysnaRuntimeError("Invalid float conversion", code="W4003")
        raise WysnaRuntimeError(f"Cannot convert '{type(arg).__name__}' to float")

    def str_fn(args: List[WysnaValue]) -> WysnaValue:
        if len(args) != 1:
            raise WysnaRuntimeError("str() takes exactly 1 argument")
        arg = args[0]
        if isinstance(arg, WysnaStr):
            return arg
        return WysnaStr(str(arg))

    def ord_fn(args: List[WysnaValue]) -> WysnaValue:
        if len(args) != 1:
            raise WysnaRuntimeError("ord() takes exactly 1 argument")
        arg = args[0]
        if not isinstance(arg, WysnaStr) or len(arg.value) == 0:
            raise WysnaRuntimeError("ord() expects non-empty str argument")
        return WysnaInt(ord(arg.value[0]))

    def chr_fn(args: List[WysnaValue]) -> WysnaValue:
        if len(args) != 1:
            raise WysnaRuntimeError("chr() takes exactly 1 argument")
        arg = args[0]
        if not isinstance(arg, WysnaInt):
            raise WysnaRuntimeError("chr() expects int argument")
        if arg.value < 0 or arg.value > 0x10ffff or 0xd800 <= arg.value <= 0xdfff:
            raise WysnaRuntimeError("chr() expects a Unicode scalar", code="W4003")
        return WysnaStr(chr(arg.value))

    def slice_fn(args: List[WysnaValue]) -> WysnaValue:
        if len(args) != 3:
            raise WysnaRuntimeError("slice() takes exactly 3 arguments (value, start, end)")
        s_arg, start_arg, end_arg = args[0], args[1], args[2]
        if not isinstance(start_arg, WysnaInt) or not isinstance(end_arg, WysnaInt):
            raise WysnaRuntimeError("slice() expects (str | list, int, int)")
        if isinstance(s_arg, WysnaList):
            start = max(0, start_arg.value)
            end = min(len(s_arg.storage.data), end_arg.value)
            return WysnaList(elements=list(s_arg.storage.data[start:end]) if end > start else [])
        if isinstance(s_arg, WysnaStr):
            start = max(0, start_arg.value)
            end = min(len(s_arg.value), end_arg.value)
            return WysnaStr(s_arg.value[start:end] if end > start else "")
        raise WysnaRuntimeError("slice() expects str or list")

    def bytes_fn(args: List[WysnaValue]) -> WysnaValue:
        if len(args) != 1:
            raise WysnaRuntimeError("bytes() takes exactly 1 argument")
        arg = args[0]
        if not isinstance(arg, WysnaStr):
            raise WysnaRuntimeError("bytes() expects str argument")
        return WysnaList(elements=[WysnaInt(b) for b in arg.value.encode('utf-8')])

    def join_strings_fn(args: List[WysnaValue]) -> WysnaValue:
        parts, separator = args
        strings = [part.value for part in parts.storage.data]
        return WysnaStr(separator.value.join(strings))

    env.declare("join_strings", WysnaNativeFunction("join_strings", join_strings_fn))

    # 6. File I/O
    def read_file_fn(args: List[WysnaValue]) -> WysnaValue:
        if len(args) != 1:
            raise WysnaRuntimeError("read_file() takes exactly 1 argument")
        arg = args[0]
        if not isinstance(arg, WysnaStr):
            raise WysnaRuntimeError("read_file() expects str path")
        trace = getattr(context, "effect_trace", None)
        if trace is not None and trace.is_replaying:
            ok, payload = trace.replay("builtin.read_file", [arg.value])
            if ok:
                return WysnaOk(WysnaStr(str(payload)))
            return WysnaErr(WysnaStructInstance("IoError", fields={"message": WysnaStr(str(payload))}))
        try:
            with open(context.resolve_path(arg.value), 'r', encoding='utf-8', newline='') as f:
                content = f.read()
            if trace is not None and trace.is_recording:
                trace.record("builtin.read_file", [arg.value], ok=True, result=content)
            return WysnaOk(WysnaStr(content))
        except Exception as e:
            if trace is not None and trace.is_recording:
                trace.record("builtin.read_file", [arg.value], ok=False, error=str(e))
            return WysnaErr(WysnaStructInstance("IoError", fields={"message": WysnaStr(str(e))}))

    def write_file_fn(args: List[WysnaValue]) -> WysnaValue:
        if len(args) != 2:
            raise WysnaRuntimeError("write_file() takes exactly 2 arguments (path, content)")
        p_arg, c_arg = args[0], args[1]
        if not isinstance(p_arg, WysnaStr) or not isinstance(c_arg, WysnaStr):
            raise WysnaRuntimeError("write_file() expects str path and str content")
        trace = getattr(context, "effect_trace", None)
        if trace is not None and trace.is_replaying:
            ok, payload = trace.replay("builtin.write_file", [p_arg.value, c_arg.value])
            if ok:
                return WysnaOk(WysnaUnit())
            return WysnaErr(WysnaStructInstance("IoError", fields={"message": WysnaStr(str(payload))}))
        try:
            with open(context.resolve_path(p_arg.value), 'w', encoding='utf-8') as f:
                f.write(c_arg.value)
            if trace is not None and trace.is_recording:
                trace.record("builtin.write_file", [p_arg.value, c_arg.value], ok=True, result=True)
            return WysnaOk(WysnaUnit())
        except Exception as e:
            if trace is not None and trace.is_recording:
                trace.record("builtin.write_file", [p_arg.value, c_arg.value], ok=False, error=str(e))
            return WysnaErr(WysnaStructInstance("IoError", fields={"message": WysnaStr(str(e))}))

    def args_fn(args_list: List[WysnaValue]) -> WysnaValue:
        return WysnaList(elements=[WysnaStr(a) for a in context.argv])

    def env_fn(args_list: List[WysnaValue]) -> WysnaValue:
        if len(args_list) != 1 or not isinstance(args_list[0], WysnaStr):
            raise WysnaRuntimeError("env() expects one str variable name")
        key = args_list[0].value
        trace = getattr(context, "effect_trace", None)
        if trace is not None and trace.is_replaying:
            _, value = trace.replay("builtin.env", [key])
        else:
            value = context.env.get(key)
            if trace is not None and trace.is_recording:
                trace.record("builtin.env", [key], ok=True, result=value)
        return WysnaEnumValue("Option", "Some", [WysnaStr(value)]) if value is not None else WysnaNone()

    def unix_time_fn(args_list: List[WysnaValue]) -> WysnaValue:
        if args_list:
            raise WysnaRuntimeError("unix_time() takes no arguments")
        trace = getattr(context, "effect_trace", None)
        if trace is not None and trace.is_replaying:
            _, ts = trace.replay("builtin.unix_time", [])
            return WysnaInt(int(ts))
        ts = int(time.time())
        if trace is not None and trace.is_recording:
            trace.record("builtin.unix_time", [], ok=True, result=ts)
        return WysnaInt(ts)

    def contract_assert_fn(args_list: List[WysnaValue]) -> WysnaValue:
        if len(args_list) != 2 or not isinstance(args_list[0], WysnaBool) or not isinstance(args_list[1], WysnaStr):
            raise WysnaRuntimeError("__wysna_contract_assert() expects (bool, str)")
        if not args_list[0].value:
            raise WysnaRuntimeError(args_list[1].value, code="W4005")
        return WysnaUnit()

    def json_parse_fn(args_list: List[WysnaValue]) -> WysnaValue:
        if len(args_list) != 6 or not isinstance(args_list[0], WysnaStr):
            raise WysnaRuntimeError("__wysna_json_parse() expects (str, int, int, int, Value, JsonError)")
        source = args_list[0].value
        max_bytes = args_list[1].value
        max_depth = args_list[2].value
        max_nodes = args_list[3].value
        proto_null = args_list[4]
        proto_err = args_list[5]
        enum_name = proto_null.enum_name if isinstance(proto_null, WysnaEnumValue) else "Value"
        err_name = proto_err.struct_name if isinstance(proto_err, WysnaStructInstance) else "JsonError"

        def make_err(offset: int, message: str) -> WysnaErr:
            return WysnaErr(WysnaStructInstance(
                err_name,
                fields={"offset": WysnaInt(offset), "message": WysnaStr(message)},
            ))

        if max_bytes < 1 or max_bytes > 16777216:
            return make_err(0, "max_input_bytes must be between 1 and 16777216")
        if max_depth < 1 or max_depth > 32:
            return make_err(0, "max_depth must be between 1 and 32")
        if max_nodes < 1 or max_nodes > 100000:
            return make_err(0, "max_nodes must be between 1 and 100000")

        if len(source.encode("utf-8")) > max_bytes:
            total_bytes = 0
            for idx, ch in enumerate(source):
                w = len(ch.encode("utf-8"))
                if total_bytes + w > max_bytes:
                    return make_err(idx, "maximum JSON input byte limit exceeded")
                total_bytes += w

        n = len(source)
        pos = 0
        depth = 0
        node_count = 0

        class _JsonFail(Exception):
            def __init__(self, offset: int, message: str):
                self.offset = offset
                self.message = message

        def skip_space() -> None:
            nonlocal pos
            while pos < n and source[pos] in (" ", "\t", "\r", "\n"):
                pos += 1

        def take() -> str:
            nonlocal pos
            if pos >= n:
                return ""
            ch = source[pos]
            pos += 1
            return ch

        def reserve_node() -> None:
            nonlocal node_count
            if node_count >= max_nodes:
                raise _JsonFail(pos, "maximum JSON value node limit exceeded")
            node_count += 1

        def read_hex4() -> int:
            val = 0
            for _ in range(4):
                ch = take()
                if not ch or ch not in "0123456789abcdefABCDEF":
                    raise _JsonFail(pos, "expected four hexadecimal digits")
                val = val * 16 + int(ch, 16)
            return val

        def read_string() -> str:
            nonlocal pos
            if take() != '"':
                raise _JsonFail(pos, "expected string")
            out: List[str] = []
            while pos < n and source[pos] != '"':
                ch = take()
                if ch == "\\":
                    esc = take()
                    simple = {'"': '"', "\\": "\\", "/": "/", "b": "\b", "f": "\f", "n": "\n", "r": "\r", "t": "\t"}
                    if esc in simple:
                        out.append(simple[esc])
                    elif esc == "u":
                        first = read_hex4()
                        cp = first
                        if 0xD800 <= first <= 0xDBFF:
                            if take() != "\\" or take() != "u":
                                raise _JsonFail(pos, "high surrogate must be followed by a low surrogate")
                            second = read_hex4()
                            if second < 0xDC00 or second > 0xDFFF:
                                raise _JsonFail(pos, "high surrogate must be followed by a low surrogate")
                            cp = 0x10000 + ((first - 0xD800) << 10) + (second - 0xDC00)
                        elif 0xDC00 <= first <= 0xDFFF:
                            raise _JsonFail(pos, "unexpected low surrogate")
                        out.append(chr(cp))
                    else:
                        raise _JsonFail(pos, "invalid string escape")
                else:
                    if ord(ch) < 32:
                        raise _JsonFail(pos, "unescaped control character in string")
                    out.append(ch)
            if pos >= n:
                raise _JsonFail(pos, "unterminated string")
            pos += 1
            return "".join(out)

        def read_number() -> WysnaEnumValue:
            nonlocal pos
            start = pos
            if pos < n and source[pos] == "-":
                pos += 1
            if pos < n and source[pos] == "0":
                pos += 1
                if pos < n and "0" <= source[pos] <= "9":
                    raise _JsonFail(pos, "leading zero in number")
            elif pos < n and "1" <= source[pos] <= "9":
                while pos < n and "0" <= source[pos] <= "9":
                    pos += 1
            else:
                raise _JsonFail(pos, "expected integer part")
            if pos < n and source[pos] == ".":
                pos += 1
                if pos >= n or not ("0" <= source[pos] <= "9"):
                    raise _JsonFail(pos, "expected digit after decimal point")
                while pos < n and "0" <= source[pos] <= "9":
                    pos += 1
            if pos < n and source[pos] in ("e", "E"):
                pos += 1
                if pos < n and source[pos] in ("+", "-"):
                    pos += 1
                if pos >= n or not ("0" <= source[pos] <= "9"):
                    raise _JsonFail(pos, "expected exponent digits")
                while pos < n and "0" <= source[pos] <= "9":
                    pos += 1
            return WysnaEnumValue(enum_name, "Number", [WysnaStr(source[start:pos])])

        def read_array() -> WysnaEnumValue:
            nonlocal pos, depth
            pos += 1
            depth += 1
            if depth > max_depth:
                msg = "maximum JSON nesting depth is 32" if max_depth == 32 else "maximum JSON nesting depth limit exceeded"
                raise _JsonFail(pos, msg)
            skip_space()
            items: List[WysnaValue] = []
            if pos < n and source[pos] == "]":
                pos += 1
                depth -= 1
                return WysnaEnumValue(enum_name, "Array", [WysnaList(elements=items)])
            while True:
                items.append(read_value())
                skip_space()
                if pos < n and source[pos] == "]":
                    pos += 1
                    depth -= 1
                    return WysnaEnumValue(enum_name, "Array", [WysnaList(elements=items)])
                if pos >= n or source[pos] != ",":
                    raise _JsonFail(pos, "expected ',' or ']'")
                pos += 1
                skip_space()

        def read_object() -> WysnaEnumValue:
            nonlocal pos, depth
            pos += 1
            depth += 1
            if depth > max_depth:
                msg = "maximum JSON nesting depth is 32" if max_depth == 32 else "maximum JSON nesting depth limit exceeded"
                raise _JsonFail(pos, msg)
            skip_space()
            fields: Dict[Any, WysnaValue] = {}
            if pos < n and source[pos] == "}":
                pos += 1
                depth -= 1
                return WysnaEnumValue(enum_name, "Object", [WysnaDict(pairs=fields)])
            while True:
                if pos >= n or source[pos] != '"':
                    raise _JsonFail(pos, "object key must be a string")
                key = read_string()
                skip_space()
                if take() != ":":
                    raise _JsonFail(pos, "expected ':' after object key")
                skip_space()
                fields[key] = read_value()
                skip_space()
                if pos < n and source[pos] == "}":
                    pos += 1
                    depth -= 1
                    return WysnaEnumValue(enum_name, "Object", [WysnaDict(pairs=fields)])
                if pos >= n or source[pos] != ",":
                    raise _JsonFail(pos, "expected ',' or '}'")
                pos += 1
                skip_space()

        def read_value() -> WysnaValue:
            nonlocal pos
            skip_space()
            if pos >= n:
                raise _JsonFail(pos, "expected JSON value")
            cur = source[pos]
            if cur == '"':
                reserve_node()
                return WysnaEnumValue(enum_name, "String", [WysnaStr(read_string())])
            if cur == "{":
                reserve_node()
                return read_object()
            if cur == "[":
                reserve_node()
                return read_array()
            if cur == "t":
                reserve_node()
                if source[pos:pos + 4] != "true":
                    raise _JsonFail(pos, "invalid literal")
                pos += 4
                return WysnaEnumValue(enum_name, "Bool", [WysnaBool(True)])
            if cur == "f":
                reserve_node()
                if source[pos:pos + 5] != "false":
                    raise _JsonFail(pos, "invalid literal")
                pos += 5
                return WysnaEnumValue(enum_name, "Bool", [WysnaBool(False)])
            if cur == "n":
                reserve_node()
                if source[pos:pos + 4] != "null":
                    raise _JsonFail(pos, "invalid literal")
                pos += 4
                return WysnaEnumValue(enum_name, "Null")
            if cur == "-" or ("0" <= cur <= "9"):
                reserve_node()
                return read_number()
            raise _JsonFail(pos, "expected JSON value")

        try:
            skip_space()
            root = read_value()
            skip_space()
            if pos != n:
                raise _JsonFail(pos, "trailing characters after JSON value")
            return WysnaOk(root)
        except _JsonFail as exc:
            return make_err(exc.offset, exc.message)

    def json_parse_int_fn(args_list: List[WysnaValue]) -> WysnaValue:
        if len(args_list) != 1 or not isinstance(args_list[0], WysnaStr):
            raise WysnaRuntimeError("__wysna_json_parse_int() expects (str)")
        raw = args_list[0].value
        # Only ASCII text can match the integer grammar below.
        if len(raw) > 1048576:
            return WysnaErr(WysnaStr("maximum JSON input byte limit exceeded"))
        text = raw.strip(" \t\r\n")
        if not text:
            return WysnaErr(WysnaStr("number value must contain one JSON number"))
        if re.fullmatch(r"-?(?:0|[1-9][0-9]*)", text):
            digits = len(text) - (1 if text.startswith("-") else 0)
            if digits > 19:
                return WysnaErr(WysnaStr("integer overflow"))
            val = int(text)
            if val < -9223372036854775808 or val > 9223372036854775807:
                return WysnaErr(WysnaStr("integer overflow"))
            return WysnaOk(WysnaInt(val))
        if re.fullmatch(r"-?(?:0|[1-9][0-9]*)(?:\.[0-9]+)?(?:[eE][+-]?[0-9]+)?", text):
            return WysnaErr(WysnaStr("expected JSON integer, found JSON number with a fractional or exponent part"))
        return WysnaErr(WysnaStr("number value must contain one JSON number"))

    def some_fn(args: List[WysnaValue]) -> WysnaValue:
        if len(args) != 1:
            raise WysnaRuntimeError("Some() takes exactly 1 argument")
        return WysnaEnumValue("Option", "Some", [args[0]])

    def ok_fn(args: List[WysnaValue]) -> WysnaValue:
        if len(args) != 1:
            raise WysnaRuntimeError("Ok() takes exactly 1 argument")
        return WysnaOk(args[0])

    def err_fn(args: List[WysnaValue]) -> WysnaValue:
        if len(args) != 1:
            raise WysnaRuntimeError("Err() takes exactly 1 argument")
        return WysnaErr(args[0])

    def weak_fn(args: List[WysnaValue]) -> WysnaValue:
        if len(args) != 1 or not isinstance(args[0], WysnaObjectInstance):
            raise WysnaRuntimeError("weak expects one class object")
        return WysnaWeak(args[0])

    def upgrade_fn(args: List[WysnaValue]) -> WysnaValue:
        if len(args) != 1 or not isinstance(args[0], WysnaWeak):
            raise WysnaRuntimeError("upgrade expects one weak reference")
        value = args[0].reference()
        return value if value is not None else WysnaNone()

    # Register builtins
    env.declare("print", WysnaNativeFunction("print", print_fn), is_mut=False)
    env.declare("len", WysnaNativeFunction("len", len_fn), is_mut=False)
    env.declare("enumerate", WysnaNativeFunction("enumerate", enumerate_fn), is_mut=False)
    env.declare("dict_items", WysnaNativeFunction("dict_items", dict_items_fn), is_mut=False)
    env.declare("int", WysnaNativeFunction("int", int_fn), is_mut=False)
    env.declare("u8", WysnaNativeFunction("u8", u8_fn), is_mut=False)
    env.declare("float", WysnaNativeFunction("float", float_fn), is_mut=False)
    env.declare("str", WysnaNativeFunction("str", str_fn), is_mut=False)
    env.declare("ord", WysnaNativeFunction("ord", ord_fn), is_mut=False)
    env.declare("chr", WysnaNativeFunction("chr", chr_fn), is_mut=False)
    env.declare("slice", WysnaNativeFunction("slice", slice_fn), is_mut=False)
    env.declare("bytes", WysnaNativeFunction("bytes", bytes_fn), is_mut=False)
    env.declare("read_file", WysnaNativeFunction("read_file", read_file_fn), is_mut=False)
    env.declare("write_file", WysnaNativeFunction("write_file", write_file_fn), is_mut=False)
    env.declare("args", WysnaNativeFunction("args", args_fn), is_mut=False)
    env.declare("env", WysnaNativeFunction("env", env_fn), is_mut=False)
    env.declare("unix_time", WysnaNativeFunction("unix_time", unix_time_fn), is_mut=False)
    env.declare("__wysna_contract_assert", WysnaNativeFunction("__wysna_contract_assert", contract_assert_fn), is_mut=False)
    env.declare("__wysna_json_parse", WysnaNativeFunction("__wysna_json_parse", json_parse_fn), is_mut=False)
    env.declare("__wysna_json_parse_int", WysnaNativeFunction("__wysna_json_parse_int", json_parse_int_fn), is_mut=False)

    env.declare("weak", WysnaNativeFunction("weak", weak_fn), is_mut=False)
    env.declare("upgrade", WysnaNativeFunction("upgrade", upgrade_fn), is_mut=False)
    env.declare("Some", WysnaNativeFunction("Some", some_fn), is_mut=False)
    env.declare("Ok", WysnaNativeFunction("Ok", ok_fn), is_mut=False)
    env.declare("Err", WysnaNativeFunction("Err", err_fn), is_mut=False)
    env.declare("None", WysnaEnumValue("Option", "None"), is_mut=False)
    env.declare("Option", WysnaEnumNamespace("Option", ["Some", "None"]), is_mut=False)
    env.declare("Result", WysnaEnumNamespace("Result", ["Ok", "Err"]), is_mut=False)

    from checker.WorkerSafety import PURE_BUILTINS
    for name in PURE_BUILTINS:
        value = env.bindings.get(name)
        if isinstance(value, WysnaNativeFunction):
            value.worker_safe = True
    return env


def _dump_cow_metrics():
    if os.environ.get("WYSNA_COW_METRICS") == "1":
        sys.stderr.write(
            f"[WYSNA_COW_METRICS] detach_list={cow_telemetry.detach_list} "
            f"detach_dict={cow_telemetry.detach_dict} detach_struct={cow_telemetry.detach_struct} "
            f"copied_slots={cow_telemetry.copied_slots}\n"
        )
        sys.stderr.flush()

atexit.register(_dump_cow_metrics)

