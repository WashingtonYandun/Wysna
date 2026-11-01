"""Public typed-HIR backend: verify/lower to MIR, then emit C99 from MIR only."""
from common.Telemetry import CodegenTelemetry
from error.Error import WysnaError


class HirCCodeGen:
    def __init__(self, interner=None, file_path='<stdin>'):
        self.interner = interner
        self.file_path = file_path
        self.telemetry = CodegenTelemetry()
        self.mir = None

    def generate(self, module, *, library_mode=False):
        from mir.Lowering import Lowering
        from mir.CCodeGen import CCodeGen
        if self.interner is None:
            raise WysnaError('HIR emission requires a type interner', file=self.file_path,
                         code='W9999', stage='hir-verification')
        import time
        started = time.perf_counter()
        self.mir = Lowering(self.interner, self.file_path).lower(
            module, call_entrypoint=False if library_mode else None,
        )
        self.mir.library_mode = bool(library_mode)
        lowered = time.perf_counter()
        generator = CCodeGen(self.interner)
        result = generator.generate(self.mir)
        self.abi_exports = tuple(generator.abi_exports)
        self.phase_ms = {'mir': (lowered - started) * 1000,
                         'c_emit': (time.perf_counter() - lowered) * 1000}
        self.telemetry = generator.telemetry
        return result

    def _unsupported(self, node):
        span = node.span
        raise WysnaError('Direct HIR emission is unsupported; lower a verified module to MIR',
                     file=span.file_path or self.file_path, line=span.line, col=span.col,
                     code='W9999', stage='codegen')

    def emit_expr(self, node, out):
        self._unsupported(node)

    def emit_stmt(self, node, out):
        self._unsupported(node)
