"""Human and json-v1 effect reports and diagnostic code explanations without execution."""
import argparse
import json
from pathlib import Path
import re
import sys
from compiler.CompilerService import CompilerService
from error.Error import DIAGNOSTIC_CODES, WysnaError
from tooling.ToolResult import diagnostic_from_error, diagnostic_record, tool_result, write_tool_result


_CODE_DETAILS = {
    'W0001': ('lexer/parser', ['Invalid character or unexpected delimiter token'],
              'let x = @', 'let x = 10'),
    'W0002': ('lexer', ['Numeric literal with leading dot (`.5`), double dot, or malformed base prefix'],
              'let x = .5', 'let x = 0.5'),
    'W0003': ('lexer', ['Missing closing quote on string literal or raw newline inside string'],
              'let s = "hello', 'let s = "hello"'),
    'W0004': ('lexer', ['Unsupported backslash escape inside string literal'],
              'let s = "\\q"', 'let s = "\\\\q"'),
    'W0005': ('lexer', ['Out-of-range or malformed Unicode scalar `\\u{...}` escape'],
              'let s = "\\u{FFFFFF}"', 'let s = "\\u{1F600}"'),
    'W0006': ('parser', ['Unclosed block `{...}`, missing closing parenthesis `)`, or unbalanced brackets'],
              'fn f() { let x = 1', 'fn f() { let x = 1 }'),
    'W1001': ('semantic', ['Typo in variable/function/type name or missing `import` declaration'],
              'print(countt)', 'let count = 0\nprint(count)'),
    'W1002': ('semantic', ['Two declarations share the same name or use reserved `__wysna_` prefix'],
              'let x = 1\nlet x = 2', 'let x = 1\nlet y = 2'),
    'W1003': ('module', ['Import target path cannot be resolved or introduces a dependency cycle'],
              'import "missing.wysna"', 'import "std/json"'),
    'W1004': ('semantic', ['Using `break`/`continue` outside loop, or `this`/`super` outside a class method'],
              'fn f() { break }', 'for i in 0..3 { break }'),
    'W1005': ('graph', ['Multiple declarations match the selector'],
              'wysna graph main.wysna --symbol score', 'Pass a candidate ID from diagnostics to --symbol'),
    'W2001': ('typecheck', ['Expression, argument, or initializer type does not match annotation/expectation'],
              'let n: int = "42"', 'let n: int = 42'),
    'W2002': ('typecheck', ['Assigning to a non-place expression (literal, function call result)'],
              'foo() = 10', 'let mut v = foo()\nv = 10'),
    'W2003': ('typecheck', ['`match` arms do not cover every enum variant, boolean value, or error alternative'],
              'match opt { Some(v) => v }', 'match opt { Some(v) => v, None => 0 }'),
    'W2004': ('typecheck', ['Function misses a `return` path or returns a value incompatible with signature'],
              'fn f(ok: bool) -> int { if ok { return 1 } }', 'fn f(ok: bool) -> int { if ok { return 1 } else { return 0 } }'),
    'W2005': ('typecheck', ['Wrong argument count or attempting to call a non-function value'],
              'fn add(a: int, b: int) -> int { return a + b }\nadd(1)', 'add(1, 2)'),
    'W2006': ('typecheck', ['Using `?` or `else err` on a non-Result/non-Optional expression, or unhandled error channel'],
              'let x = 42?', 'let x = fallible() else err { 0 }'),
    'W2007': ('typecheck', ['Struct literal misses required fields or references an unknown field'],
              'struct P { x: int, y: int }\nlet p = P { x: 1 }', 'let p = P { x: 1, y: 2 }'),
    'W2010': ('typecheck', ['Using float, struct, or collection as dict key (only int, str, bool, enum allowed)'],
              'let d = {1.5: "a"}', 'let d = {"1.5": "a"}'),
    'W2011': ('typecheck', ['Recursive value struct layout without indirection or monomorphization depth exceeded'],
              'struct Node { next: Node }', 'struct Node { next: int? }'),
    'W2012': ('typecheck', ['Type argument fails declared generic interface constraint'],
              'fn show[T: Printable](x: T) {}', 'class Doc implements Printable { ... }\nshow(new Doc())'),
    'W2013': ('target', ['Feature unsupported by the selected compiler profile or target (e.g., WASI networking)'],
              'import "std/http" # on wasm32-wasip1', 'use --backend=c99 native target'),
    'W2014': ('typecheck', ['A worker argument, result, capture or local graph contains a class, Weak, interface or opaque handle'],
              'parallel_map(objects, |x: Object| => x)', 'parallel_map(values, |x: int| => x + 1)'),
    'W2015': ('typecheck', ['A worker callback reads uncaptured globals, reaches host effects, or cannot be statically resolved'],
              'parallel_map(values, |x: int| { print(x) x })', 'let results = parallel_map(values, |x: int| => x + 1)\nprint(results)'),
    'W3001': ('ownership', ['Reassigning a `let` immutable binding (`run wysna fix --apply` can add `mut`)'],
              'let x = 1\nx = 2', 'let mut x = 1\nx = 2'),
    'W3002': ('oop', ['Accessing unknown field/method or referencing a `private` class member outside the class'],
              'u.nme', 'u.name'),
    'W3003': ('oop', ['Class declares `implements Interface` without implementing every required method signature'],
              'class A implements Greeter {}', 'class A implements Greeter { fn greet() -> str { return "hi" } }'),
    'W3004': ('oop', ['Constructor leaves fields uninitialized or fails to invoke `super.init(...)` first'],
              'class C { x: int\n fn init() {} }', 'class C { x: int\n fn init() { this.x = 0 } }'),
    'W4001': ('runtime', ['Integer or modulo division by zero at runtime'],
              'let x = 10 / 0', 'if denom != 0 { 10 / denom } else { 0 }'),
    'W4002': ('runtime', ['List/string index out of bounds or missing dictionary key access'],
              'let xs = [1]\nlet y = xs[5]', 'if len(xs) > 5 { xs[5] }'),
    'W4003': ('runtime', ['Unhandled runtime error, integer overflow, or explicit failure'],
              'exit(1)', 'handle failure with Result T ! E'),
    'W4004': ('runtime', ['Execution hit deterministic `--max-steps` or `--max-heap-mb` budget'],
              'while true {} # with --max-steps=1000', 'bound loop iterations and heap allocations'),
    'W4005': ('runtime', ['Function `requires` precondition or `ensures` postcondition evaluated to false'],
              'fn dec(n: int) -> int requires n > 0 { return n - 1 }\ndec(0)', 'dec(1)'),
    'W5001': ('toolchain', ['Host C compiler (`cc`/`clang`/`gcc`) missing or failed invocation'],
              'CC=missing wysna build app.wysna', 'ensure cc or zig is available on PATH'),
    'W5002': ('toolchain', ['Invalid CLI flag combination or unsupported option'],
              'wysna check --unknown-flag app.wysna', 'run `wysna --help`'),
    'W5003': ('toolchain', ['Input source file does not exist or cannot be decoded as UTF-8'],
              'wysna check missing.wysna', 'verify path exists and is UTF-8 encoded'),
    'W5004': ('toolchain', ['Output artifact path is not writable or read-back failed'],
              'wysna build app.wysna -o /nonexistent/dir/app', 'ensure output parent directory exists'),
    'W5005': ('patch', ['`--expect-graph-hash` does not match current file `graph_hash`'],
              'wysna patch file.wysna --symbol f --expect-graph-hash graph:stale --with ...',
              're-query `wysna graph` to obtain current graph_hash'),
    'W5006': ('capability', ['Program exceeds declared `--allow` / `--deny` static capability gate'],
              'wysna run --deny=network.http client.wysna', 'grant explicit required capabilities or remove blocked calls'),
    'W6001': ('lint', ['Pure expression value computed as a statement and silently discarded'],
              'fn f(x: int) -> int { x + 1\n return x }', 'fn f(x: int) -> int { return x + 1 }'),
    'W6002': ('lint', ['Strong circular references detected between classes (uncollected under ARC)'],
              'class A { b: B? }\nclass B { a: A? }', 'class A { b: B? }\nclass B { a: Weak[A] }'),
    'W6003': ('lint', ['Match arm can never be reached because earlier unguarded patterns cover all of its values'],
              'match x { _ => 0, 1 => 1 }', 'match x { 1 => 1, _ => 0 }'),
    'W6004': ('lint', ['Call to a function marked @deprecated'],
              '@deprecated("use current") fn old() {}\nold()', 'Follow the deprecation message to migrate the call'),
    'W6005': ('lint', ['Discarded result of a function marked @must_use'],
              '@must_use fn answer() -> int { 42 }\nanswer()', 'let value = answer()'),
    'W9998': ('compiler', ['Frontend accepted syntax that backend lowering refused'],
              'unsupported target construct', 'check docs/KNOWN_LIMITS.md or report issue'),
    'W9999': ('compiler', ['Internal compiler invariant violation across IR phase boundaries'],
              'internal compiler assertion', 'report minimal reproduction with `wysna mir`'),
}


def explain_diagnostic_code(code: str) -> dict:
    """Return structured explanation and fix guidance for a Wxxxx diagnostic code."""
    normalized = code.strip().upper()
    if normalized not in DIAGNOSTIC_CODES:
        raise ValueError(f"Unknown diagnostic code '{code}'. Valid codes start with W (e.g. W2001).")
    stage, causes, bad_ex, good_ex = _CODE_DETAILS.get(
        normalized,
        ('compiler', [DIAGNOSTIC_CODES[normalized]], '', ''),
    )
    return {
        'schema_version': 'wysna.explain-code/1',
        'code': normalized,
        'title': DIAGNOSTIC_CODES[normalized],
        'stage': stage,
        'description': DIAGNOSTIC_CODES[normalized],
        'common_causes': list(causes),
        'bad_example': bad_ex,
        'good_example': good_ex,
    }


class _Parser(argparse.ArgumentParser):
    def error(self, message):
        raise ValueError(message)


def run_explain_command(arguments):
    parser = _Parser(prog='wysna explain', description=__doc__)
    parser.add_argument('source', nargs='?', help='Source file or Wxxxx diagnostic code')
    parser.add_argument('--code', help='Explain a specific diagnostic code (e.g. W2001)')
    parser.add_argument('--function', help='Select an exact reported function identity or display name')
    parser.add_argument('--manifest', type=Path)
    parser.add_argument('--frozen', action='store_true')
    parser.add_argument('--output-format', choices=('json-v1',))
    json_output = any(argument == '--output-format=json-v1' for argument in arguments) or '--output-format' in arguments
    service = CompilerService()
    try:
        options = parser.parse_args(arguments)
        json_output = options.output_format == 'json-v1'
        code_arg = options.code
        if (code_arg is None and options.source and re.fullmatch(r'W\d{4}', options.source.strip(), re.I)
                and not Path(options.source).exists()):
            code_arg = options.source

        if code_arg is not None:
            if options.source and options.source != code_arg:
                raise ValueError('Provide either a source file or --code, not both')
            report = explain_diagnostic_code(code_arg)
            if json_output:
                result = tool_result('explain', 'ok', 0)
                result['result'] = report
                write_tool_result(result, sys.stdout)
            else:
                print(f"[{report['code']}] ({report['stage']}) {report['title']}")
                for cause in report['common_causes']:
                    print(f"  - {cause}")
                if report['bad_example']:
                    print("Bad:\n  " + report['bad_example'].replace('\n', '\n  '))
                if report['good_example']:
                    print("Good:\n  " + report['good_example'].replace('\n', '\n  '))
            return 0

        if not options.source:
            raise ValueError('Provide a source file path or --code Wxxxx')
        resolver = None
        if options.manifest:
            from project.Resolver import ProjectResolver
            resolver = ProjectResolver.load(options.manifest)
            if options.frozen:
                resolver.verify_lock()
            if not Path(options.source).resolve().is_relative_to(resolver.manifest.source_root.resolve()):
                raise ValueError('Explain source must belong to the project source root')
            service = CompilerService(project_root=resolver.manifest.root, project_resolver=resolver)
        elif options.frozen:
            raise ValueError('--frozen requires --manifest')
        try:
            checked = service.check_file(Path(options.source))
        except UnicodeDecodeError as error:
            raise WysnaError('Source is not valid UTF-8', file=str(Path(options.source).resolve()),
                             code='W1001', stage='lexer') from error
        report = service.explain(checked)
        if resolver:
            report['project'] = resolver.lock_data()
        if options.function:
            selected = [row for row in report['functions'] if options.function in (row['identity'], row['name'])]
            if len(selected) != 1:
                raise ValueError('Function selector must match exactly one reported identity or display name')
            report['selected_function'] = selected[0]['identity']
        if json_output:
            result = tool_result('explain', 'ok', 0)
            result['result'] = report
            write_tool_result(result, sys.stdout)
        else:
            print('Conservative effect report (' + report['model'] + ')')
            print('Program capabilities: ' + (', '.join(report['program']['capabilities']) or 'none modeled'))
            for row in report['functions']:
                if options.function and row['identity'] != report['selected_function']:
                    continue
                print(row['name'] + ': ' + (', '.join(row['transitive_effects']) or 'none modeled'))
                for witness in row['unknown_calls']:
                    source = witness['source']
                    print('  unknown: ' + witness['reason'] + ' at ' + source['file'] + ':' + str(source['line']))
            print('Completeness is conditional on the model; this report is not a security proof.')
        return 0
    except (WysnaError, OSError, UnicodeError, ValueError) as error:
        semantic = isinstance(error, WysnaError)
        filesystem = isinstance(error, OSError)
        code = 1 if semantic else 3 if filesystem else 2
        if json_output:
            diagnostic = diagnostic_from_error(error, service.source_texts()) if semantic else diagnostic_record('W5004' if filesystem else 'W5002', 'filesystem' if filesystem else 'explain', str(error))
            write_tool_result(tool_result('explain', 'diagnostic_error' if semantic else 'toolchain_error' if filesystem else 'usage_error', code,
                                          diagnostics=[diagnostic]), sys.stdout)
        else:
            print(str(error), file=sys.stderr)
        return code
