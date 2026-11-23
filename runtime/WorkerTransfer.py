"""Owned worker graphs and immutable declaration registries for the Seed oracle."""
import concurrent.futures
import copy
import os
import threading
import time

from checker.WorkerSafety import PURE_BUILTINS
from error.Error import WysnaRuntimeError
from runtime.Environment import Environment
from runtime.Values import (WysnaInt, WysnaFloat, WysnaBool, WysnaStr, WysnaUnit,
    WysnaNone, WysnaTuple, WysnaList, WysnaDict, WysnaStructInstance, WysnaEnumValue,
    WysnaOk, WysnaErr, WysnaFunction, WysnaNativeFunction, WysnaEnumNamespace)


class WorkerFailure:
    """Keep the first fatal cause without retaining thread tracebacks or value graphs."""
    def __init__(self):
        self.event = threading.Event()
        self.lock = threading.Lock()
        self.descriptor = None

    def fail(self, error):
        with self.lock:
            if self.descriptor is None:
                self.descriptor = {
                    'message': getattr(error, 'message', 'Worker failed with an internal runtime error'),
                    'file': getattr(error, 'file', '<stdin>'),
                    'line': getattr(error, 'line', 1), 'col': getattr(error, 'col', 1),
                    'code': getattr(error, 'code', 'W9999'),
                    'stack': list(getattr(error, 'call_stack', ()) or ()),
                    'data': dict(getattr(error, 'data', {}) or {}),
                }
            self.event.set()

    def check(self):
        if not self.event.is_set():
            return
        with self.lock:
            detail = self.descriptor
            error = WysnaRuntimeError(detail['message'], file=detail['file'], line=detail['line'],
                                      col=detail['col'], code=detail['code'])
            error.call_stack = list(detail['stack'])
            error.with_data(**detail['data'])
        raise error


class ValueSnapshot:
    def __init__(self, interpreter):
        self.interpreter = interpreter
        self.memo = {}

    def value(self, value, depth=0):
        self.interpreter._tick_step()
        if depth > 256:
            raise WysnaRuntimeError('Worker snapshot nesting exceeds 256', code='W4003')
        if isinstance(value, (WysnaInt, WysnaFloat, WysnaBool, WysnaStr)):
            return type(value)(value.value)
        if isinstance(value, (WysnaUnit, WysnaNone)):
            return type(value)()
        identity = (type(value), id(getattr(value, 'storage', value)))
        if identity in self.memo:
            copied = self.memo[identity]
            if copied is None:
                raise WysnaRuntimeError('Worker transfer rejects cyclic value graphs', code='W4003')
            return copied.clone_value()
        self.memo[identity] = None
        self.interpreter._charge_heap_bytes(32)
        if isinstance(value, WysnaList):
            self.interpreter._charge_heap_bytes(len(value.storage.data) * 8)
            copied = WysnaList(elements=[self.value(item, depth + 1) for item in value.storage.data])
        elif isinstance(value, WysnaDict):
            pairs = {}
            for key, item in value.storage.data.items():
                pairs[self.value(key, depth + 1)] = self.value(item, depth + 1)
            self.interpreter._charge_heap_bytes(len(pairs) * 16)
            copied = WysnaDict(pairs=pairs)
        elif isinstance(value, WysnaStructInstance):
            fields = {name: self.value(item, depth + 1) for name, item in value.storage.data.items()}
            self.interpreter._charge_heap_bytes(len(fields) * 8)
            copied = WysnaStructInstance(value.struct_name, fields=fields)
        elif isinstance(value, WysnaTuple):
            copied = WysnaTuple([self.value(item, depth + 1) for item in value.elements])
        elif isinstance(value, WysnaEnumValue):
            copied = WysnaEnumValue(value.enum_name, value.variant, [self.value(item, depth + 1) for item in value.payload])
        elif isinstance(value, WysnaOk):
            copied = type(value)(self.value(value.value, depth + 1))
        elif isinstance(value, WysnaErr):
            copied = WysnaErr(self.value(value.error, depth + 1))
        else:
            raise WysnaRuntimeError('Worker transfer rejects classes, Weak, function data and opaque handles', code='W4003')
        self.memo[identity] = copied
        return copied.clone_value()


class WorkerContext:
    def __init__(self, interpreter, function):
        if getattr(interpreter, '_in_worker', False):
            raise WysnaRuntimeError('Worker callbacks cannot dispatch nested workers', code='W4003')
        if not isinstance(function, WysnaFunction):
            raise WysnaRuntimeError('Worker callback must be a checked function', code='W4003')
        self.interpreter = copy.copy(interpreter)
        self.interpreter._quota_owner = getattr(interpreter, '_quota_owner', interpreter)
        self.interpreter._tls = threading.local()
        self.interpreter._in_worker = True
        self.interpreter.file = getattr(function.decl, 'source_file', None) or interpreter.file
        self.interpreter.struct_decls = dict(interpreter.struct_decls)
        environment = Environment()
        self.interpreter.global_env = environment
        # Data globals, class registries and FFI bindings never enter a worker.
        for name, value in list(interpreter.global_env.bindings.items()):
            if isinstance(value, WysnaFunction) and value.decl.name == name:
                environment.declare(name, WysnaFunction(value.decl, environment))
            elif isinstance(value, WysnaNativeFunction) and name in PURE_BUILTINS and value.worker_safe:
                environment.declare(name, value)
            elif isinstance(value, WysnaEnumNamespace):
                environment.declare(name, WysnaEnumNamespace(value.name, value.variants))
        self.snapshot = ValueSnapshot(interpreter)
        self.function = WysnaFunction(function.decl, environment)
        if hasattr(function, 'captured_environment'):
            self.function.captured_environment = self.snapshot.value(function.captured_environment)

    def invoke(self, arguments):
        try:
            result = self.interpreter.call_function(self.function, arguments)
            return ValueSnapshot(self.interpreter).value(result)
        except BaseException as error:
            self.interpreter._worker_failure.fail(error)
            raise


def prepare_spawn(interpreter, function):
    context = WorkerContext(interpreter, function)
    context.snapshot.memo.clear()
    return lambda: context.invoke([])


_current_group = threading.local()


class TaskGroupState:
    def __init__(self, deadline_ms, fail_fast):
        self.deadline = None if deadline_ms < 0 else time.monotonic_ns() + deadline_ms * 1_000_000
        self.fail_fast = fail_fast
        self.reason = 0
        self.lock = threading.Lock()

    def status(self):
        with self.lock:
            if self.reason == 0 and self.deadline is not None and time.monotonic_ns() >= self.deadline:
                self.reason = 2
            return self.reason

    def cancel(self):
        with self.lock:
            if self.reason == 0:
                self.reason = 1
            return self.reason == 1

    def invoke(self, context, item):
        result = WysnaNone()
        status = self.status()
        if status == 0:
            value = context.invoke([item])
            status = self.status()
            if status == 0:
                if self.fail_fast and isinstance(value, WysnaErr):
                    self.cancel()
                result = WysnaEnumValue('Option', 'Some', [value])
        return WysnaTuple([WysnaInt(status), result])


def group_cancelled():
    group = getattr(_current_group, 'state', None)
    return group is not None and group.status() != 0


def cancel_group():
    group = getattr(_current_group, 'state', None)
    return group is not None and group.status() != 2 and group.cancel()


def dispatch(interpreter, function, items, *, limit=4, group=None):
    maximum = 4
    try:
        maximum = max(1, min(4, int(os.environ.get('WYSNA_PARALLEL_THREADS', '4'))))
    except ValueError:
        pass
    count = min(maximum, max(1, limit), len(items)) if items else 1
    contexts = []
    for index in range(count):
        start = len(items) * index // count
        end = len(items) * (index + 1) // count
        context = WorkerContext(interpreter, function)
        arguments = [context.snapshot.value(item) for item in items[start:end]]
        context.snapshot.memo.clear()
        contexts.append((context, arguments))

    def run(partition):
        context, arguments = partition
        if group is None:
            return [context.invoke([item]) for item in arguments]
        previous = getattr(_current_group, 'state', None)
        _current_group.state = group
        try:
            return [group.invoke(context, item) for item in arguments]
        finally:
            _current_group.state = previous

    if count == 1:
        partitions = [run(contexts[0])]
    else:
        with concurrent.futures.ThreadPoolExecutor(max_workers=count) as executor:
            futures = [executor.submit(run, context) for context in contexts]
            partitions = [future.result() for future in futures]
    return [item for partition in partitions for item in partition]
