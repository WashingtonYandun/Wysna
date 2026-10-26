"""Human and json-v1 effect reports from the compiler service, without execution."""
import argparse
import json
from pathlib import Path
import sys
from compiler.CompilerService import CompilerService
from error.Error import WysnaError
from tooling.ToolResult import diagnostic_from_error, diagnostic_record, tool_result, write_tool_result


class _Parser(argparse.ArgumentParser):
    def error(self, message):
        raise ValueError(message)


def run_explain_command(arguments):
    parser = _Parser(prog='wysna explain', description=__doc__)
    parser.add_argument('source')
    parser.add_argument('--function', help='Select an exact reported function identity or display name')
    parser.add_argument('--manifest', type=Path)
    parser.add_argument('--frozen', action='store_true')
    parser.add_argument('--output-format', choices=('json-v1',))
    json_output = any(argument == '--output-format=json-v1' for argument in arguments) or '--output-format' in arguments
    service = CompilerService()
    try:
        options = parser.parse_args(arguments)
        json_output = options.output_format == 'json-v1'
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
