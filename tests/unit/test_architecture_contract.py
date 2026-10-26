"""Executable contracts for compiler responsibilities and failure boundaries."""
import ast
from pathlib import Path
import unittest
from unittest.mock import patch

from checker.ExpressionRules import EXPRESSION_RULES as CHECK_RULES
from checker.Generics import GenericDefinitionChecker
from checker.TypeChecker import TypeChecker
from checker.GenericState import SpecializationState, mangle_generic_name
from checker.WysnaType import IntType, UnitType
from codegen.CCodeGen import CCodeGen
from codegen.ExpressionRules import EXPRESSION_RULES as EMIT_RULES, UNSUPPORTED_EXPRESSIONS
from common.ExpressionDispatch import ExpressionDispatch
from compiler.Bootstrap import SELFHOST_BOOTSTRAP_INPUTS
from error.Error import WysnaError
from node import NodeType as N
from parser.Parser import Parser
from runtime.ExpressionRules import EXPRESSION_RULES as EVALUATE_RULES
from runtime.Interpreter import Interpreter

ROOT = Path(__file__).resolve().parents[2]
PRODUCTION_PACKAGES = ('checker', 'codegen', 'common', 'compiler', 'error',
                       'formatter', 'hir', 'lexer', 'mir', 'node', 'parser',
                       'project', 'runtime', 'stdlib', 'tokens', 'tooling')


class ArchitectureContractTests(unittest.TestCase):
    def test_every_expression_has_an_explicit_engine_policy(self):
        expressions = {value for value in vars(N).values()
                       if isinstance(value, type) and value is not N.Expr
                       and issubclass(value, N.Expr)}
        self.assertEqual(set(CHECK_RULES.handlers), expressions)
        self.assertEqual(set(EVALUATE_RULES.handlers), expressions)
        self.assertEqual(set(EMIT_RULES.handlers) | UNSUPPORTED_EXPRESSIONS, expressions)
        self.assertFalse(set(EMIT_RULES.handlers) & UNSUPPORTED_EXPRESSIONS)

    def test_dispatch_tables_are_read_only_and_detached(self):
        handlers = {N.IntLiteral: lambda *args: 1}
        dispatch = ExpressionDispatch('test', handlers)
        handlers.clear()
        self.assertEqual(dispatch.handler_for(N.IntLiteral(1), '<test>')(), 1)
        with self.assertRaises(TypeError):
            dispatch.handlers[N.NoneLiteral] = lambda *args: None
        with self.assertRaises(AttributeError):
            dispatch.handlers = {}
        with self.assertRaises(ValueError):
            ExpressionDispatch('test', {N.Node: lambda *args: None})

    def test_new_expression_subclasses_cannot_inherit_silent_semantics(self):
        class FutureInt(N.IntLiteral):
            pass
        for rules in (CHECK_RULES, EVALUATE_RULES, EMIT_RULES):
            with self.subTest(stage=rules.stage), self.assertRaises(WysnaError) as caught:
                rules.handler_for(FutureInt(7, line=8, col=3), 'future.wysna')
            self.assertEqual((caught.exception.code, caught.exception.file,
                              caught.exception.line, caught.exception.col),
                             ('W9999', 'future.wysna', 8, 3))

    def test_engines_reject_unknown_expressions_and_statements(self):
        checker, interpreter, emitter = TypeChecker(), Interpreter(), CCodeGen()
        expression, statement = N.Expr(4, 2), N.Stmt(5, 3)
        actions = (
            lambda: checker._check_expr(expression),
            lambda: interpreter.eval_expr(expression, interpreter.global_env),
            lambda: emitter.emit_expr(expression, []),
            lambda: checker._check_stmt(statement),
            lambda: interpreter.eval_stmt(statement, interpreter.global_env),
            lambda: emitter.emit_stmt(statement, []),
        )
        for action in actions:
            with self.subTest(action=action), self.assertRaises(WysnaError) as caught:
                action()
            self.assertEqual(caught.exception.code, 'W9999')

    def test_source_provenance_survives_unknown_node_diagnostics(self):
        expression = N.Expr(3, 9)
        expression.source_file = 'dependency.wysna'
        with self.assertRaises(WysnaError) as caught:
            CHECK_RULES.handler_for(expression, 'root.wysna')
        self.assertEqual(caught.exception.file, 'dependency.wysna')

    def test_production_modules_do_not_import_the_entry_point(self):
        for package in PRODUCTION_PACKAGES:
            for path in (ROOT / package).rglob('*.py'):
                if '_vendor' in path.parts:
                    continue
                for node in ast.walk(ast.parse(path.read_text(encoding='utf-8'))):
                    if isinstance(node, ast.ImportFrom):
                        modules = [node.module or '']
                    elif isinstance(node, ast.Import):
                        modules = [alias.name for alias in node.names]
                    else:
                        continue
                    self.assertNotIn('main', modules, str(path.relative_to(ROOT)))

    def test_core_modules_do_not_depend_on_tool_presentations(self):
        for package in ('checker', 'codegen', 'common', 'compiler', 'hir', 'mir', 'runtime', 'project'):
            for path in (ROOT / package).glob('*.py'):
                for node in ast.walk(ast.parse(path.read_text(encoding='utf-8'))):
                    modules = ([node.module or ''] if isinstance(node, ast.ImportFrom)
                               else [alias.name for alias in node.names]
                               if isinstance(node, ast.Import) else [])
                    self.assertFalse(any(module == 'tooling' or module.startswith('tooling.')
                                         for module in modules), str(path.relative_to(ROOT)))

    def test_oracles_keep_independent_expression_algorithms(self):
        for name, rules in [('checker', CHECK_RULES), ('runtime', EVALUATE_RULES), ('codegen', EMIT_RULES)]:
            self.assertTrue(all(handler.__module__.startswith(name + '.')
                                for handler in rules.handlers.values()))
        self.assertFalse(set(EVALUATE_RULES.handlers.values()) & set(EMIT_RULES.handlers.values()))

    def test_bootstrap_tracks_all_runtime_headers_and_semantic_domains(self):
        expected = {str(path.relative_to(ROOT)) for path in (ROOT / 'runtime').glob('*.h')}
        expected.update(str(path.relative_to(ROOT)) for path in (ROOT / 'checker').glob('*.py'))
        self.assertLessEqual(expected, set(SELFHOST_BOOTSTRAP_INPUTS))
        self.assertEqual(len(SELFHOST_BOOTSTRAP_INPUTS), len(set(SELFHOST_BOOTSTRAP_INPUTS)))

    def test_browser_lowering_does_not_mutate_reusable_hir(self):
        from compiler.CompilerService import CompilerService
        from hir.HirCCodeGen import HirCCodeGen

        service = CompilerService()
        checked = service.check('pub fn answer() -> int { return 42 }\nfn main() {}')
        module = service.lower_hir(checked)
        native = HirCCodeGen(checked._interner).generate(module)
        self.assertTrue(module.call_entrypoint)
        HirCCodeGen(checked._interner).generate(module, library_mode=True)
        self.assertTrue(module.call_entrypoint)
        self.assertEqual(HirCCodeGen(checked._interner).generate(module), native)

    def test_bundled_toml_parser_is_available_without_tooling(self):
        from common._vendor import tomli
        parsed = tomli.loads('[package]\nname = "example"\n')
        self.assertEqual(parsed['package']['name'], 'example')

    def test_public_convenience_apis_do_not_bypass_semantic_boundaries(self):
        from codegen import compile_to_c
        from runtime.Interpreter import evaluate

        with self.assertRaises(WysnaError) as caught:
            compile_to_c('let value: int = "wrong"', 'convenience.wysna')
        self.assertEqual(caught.exception.code, 'W2001')
        self.assertEqual(Path(caught.exception.file), (ROOT / 'convenience.wysna').resolve())
        self.assertIn('#include "runtime/wysna_runtime.h"', compile_to_c('print(42)'))
        with self.assertRaises(WysnaError) as caught:
            evaluate(object(), Interpreter().global_env)
        self.assertEqual(caught.exception.code, 'W9999')

    def test_runtime_facade_includes_each_internal_domain_once(self):
        source = (ROOT / 'runtime/wysna_runtime.h').read_text()
        internal = {path.name for path in (ROOT / 'runtime').glob('wysna_runtime_*.h')}
        self.assertTrue(internal)
        for name in internal:
            self.assertEqual(source.count('#include "' + name + '"'), 1, name)
        self.assertIn('runtime = ["*.h"]', (ROOT / 'pyproject.toml').read_text())

    def test_dispatchers_preserve_stack_depth_and_instantiation_owns_algorithms(self):
        import sys
        from runtime.Values import WysnaInt

        owners = ((TypeChecker(), '_check_expr', '_check_expr_impl', 'checker.TypeChecker', IntType),
                  (Interpreter(), 'eval_expr', '_eval_expr_impl', 'runtime.Interpreter', WysnaInt(7)),
                  (CCodeGen(), 'emit_expr', '_emit_expr_impl', 'codegen.CCodeGen', 'wysna_int(7)'))
        for owner, method, redundant, module, value in owners:
            callers = []

            def handle(*arguments):
                callers.append(sys._getframe(1).f_code.co_name)
                return value

            dispatch = ExpressionDispatch('test', {N.IntLiteral: handle})
            arguments = (() if isinstance(owner, TypeChecker) else
                         (owner.global_env,) if isinstance(owner, Interpreter) else ([],))
            with patch(module + '.EXPRESSION_RULES', dispatch):
                getattr(owner, method)(N.IntLiteral(7), *arguments)
            self.assertEqual(callers, [method], module)
            self.assertFalse(hasattr(owner, redundant), module)
        for name in ('_monomorphize_struct_impl', '_monomorphize_fn_impl', '_monomorphize_enum_impl'):
            self.assertFalse(hasattr(TypeChecker, name))

    def test_json_depth_budget_does_not_exhaust_the_host_stack(self):
        import io
        import sys
        from compiler.CompilerService import CompilerService

        recursion_limit = sys.getrecursionlimit()
        service = CompilerService(project_root=str(ROOT))
        checked = service.check_file(ROOT / 'tests/stdlib/json.wysna')
        output = io.StringIO()
        service.interpret(checked, stdout=output)
        self.assertEqual(output.getvalue(), (ROOT / 'tests/stdlib/json.expected').read_text())
        self.assertEqual(sys.getrecursionlimit(), recursion_limit)

    def test_specialization_failure_preserves_original_diagnostic_and_context(self):
        checker = TypeChecker()
        checker._pass1_collect_declarations(Parser().parse(
            'struct Box[T] { value: T }\nenum Choice[T] { Item(T) }'))
        scope = checker.current_scope
        active_substitution = checker._active_type_subst
        for template, specialize in (
            (checker.generic_struct_templates['Box'], checker._monomorphize_struct),
            (checker.generic_enum_templates['Choice'], checker._monomorphize_enum),
        ):
            original = WysnaError('Injected type resolution failure', code='W2001')
            with patch.object(checker, '_resolve_type_node', side_effect=original):
                with self.assertRaises(WysnaError) as caught:
                    specialize(template, [IntType], 1, 1)
            self.assertIs(caught.exception, original)
            self.assertIs(checker.current_scope, scope)
            self.assertIs(checker._active_type_subst, active_substitution)
            self.assertEqual(checker._instantiation_stack, [])
            self.assertEqual(checker._monomorphize_depth, 0)
            self.assertEqual(checker.monomorphized_struct_decls, [])
            self.assertEqual(checker.monomorphized_enum_decls, [])
            self.assertEqual(checker.monomorphized_structs, {})
            self.assertEqual(checker.monomorphized_enums, {})
            self.assertIsNone(checker.global_scope.lookup_type(mangle_generic_name(template.name, [IntType])))
            self.assertTrue(all(entry.state is SpecializationState.FAILED
                                for entry in checker.specializations.values()))

    def test_symbolic_definition_state_restores_on_success_and_failure(self):
        for fail in (False, True):
            checker = TypeChecker()
            checker._pass1_collect_declarations(Parser().parse('struct Box[T] { value: T }'))
            template = checker.generic_struct_templates['Box']
            symbols = dict(checker.global_scope.symbols)
            types = dict(checker.global_scope.types)
            scope, expected = checker.current_scope, checker.expected_type
            telemetry = checker.telemetry.to_dict()

            def mutate(substitutions):
                checker.global_scope.types['Temporary'] = UnitType
                checker.global_scope.symbols.clear()
                checker.expected_type = IntType
                checker.loop_depth = 9
                checker.telemetry.specialized_structs += 1
                checker.monomorphized_struct_decls.append(object())
                if fail:
                    raise WysnaError('Injected symbolic failure', code='W2001')

            if fail:
                with self.assertRaises(WysnaError):
                    GenericDefinitionChecker(checker)._check_isolated(template, mutate)
            else:
                GenericDefinitionChecker(checker)._check_isolated(template, mutate)
            self.assertEqual(checker.global_scope.symbols, symbols)
            self.assertEqual(checker.global_scope.types, types)
            self.assertIs(checker.current_scope, scope)
            self.assertIs(checker.expected_type, expected)
            self.assertEqual(checker.loop_depth, 0)
            self.assertEqual(checker.telemetry.to_dict(), telemetry)
            self.assertEqual(checker.monomorphized_struct_decls, [])


if __name__ == '__main__':
    unittest.main()
