"""Command-line parsing and presentation for the Wysna executable."""
import json
import os
import sys
from typing import Optional
from parser.Parser import Parser
from runtime.ExecutionContext import ExecutionContext
from runtime.Values import WysnaUnit
from error.Error import WysnaError
from tooling.ToolResult import (
    diagnostic_record,
    tool_result,
    write_tool_result,
    write_tool_result_atomic,
)
from common.Version import WYSNA_VERSION
from common.Paths import get_repo_root
from compiler.Bootstrap import ensure_selfhost_compiler_binary
from tooling.SourceCommand import run_pipeline, _bounded_tool_output, _write_json_v1
from tooling.DiagnosticRender import render_diagnostic
from tooling.ReplSession import ReplSession, needs_continuation
from tooling.RunReport import _capture_application_exit, _set_run_report, _write_run_report


def _same_file_path(first, second):
    first = os.path.normcase(os.path.realpath(os.path.abspath(os.fspath(first))))
    second = os.path.normcase(os.path.realpath(os.path.abspath(os.fspath(second))))
    if first == second:
        return True
    try:
        return os.path.samefile(first, second)
    except OSError:
        return False



def repl(show_tokens: bool = False, show_ast: bool = False) -> None:
    print("==================================================")
    print(f"Wysna REPL v{WYSNA_VERSION} (Type 'exit' to quit)")
    print("==================================================")

    parser = Parser(file="<repl>")
    session = ReplSession()
    pending = []

    while True:
        try:
            line = input("...> " if pending else "wysna> ")
        except (EOFError, KeyboardInterrupt):
            print("\nExiting.")
            break

        if not pending and not line.strip():
            continue
        if not pending and line.strip() == "exit":
            break
        if not pending and line.strip() == ":help":
            print("Commands: :help, :reload, :reset, exit")
            print("  :reload  reload imports if the session has no declarations")
            print("  :reset   discard all session state")
            continue
        if not pending and line.strip() == ":reload":
            try:
                print("Reloaded imports." if session.reload() else "Nothing to reload.")
            except WysnaError as err:
                print(render_diagnostic(err, {"<repl>": session.pending_source}))
            continue
        if not pending and line.strip() == ":reset":
            session = ReplSession()
            print("Session reset.")
            continue

        pending.append(line)
        source = "\n".join(pending)
        if needs_continuation(source):
            continue
        pending.clear()

        try:
            if show_ast:
                session.pending_source = source
                ast = parser.parse(source)
                print(json.dumps(ast.to_dict(), indent=2))
            res = session.execute(source)
            if not isinstance(res, WysnaUnit):
                print(res)
        except WysnaError as err:
            print(render_diagnostic(err, {"<repl>": session.pending_source}))
        except Exception as exc:
            print(f"Error: {exc}")



def print_help(prog: Optional[str] = None) -> None:
    if prog is None:
        prog = "wysna"
    print(f"Wysna Language Compiler & Toolchain v{WYSNA_VERSION}")
    print("Usage:")
    print("Core tools (native C99 builds by default):")
    print(f"  {prog} lsp                                       # LSP over stdio")
    print(f"  {prog} explain <file.wysna> [--function NAME] [--output-format=json-v1] # Conservative effects and capability report")
    print(f"  {prog} project <new|lock|check|build|run|test> [options] # Reproducible local projects")
    print(f"  {prog} api [std/module] [--output-format=json-v1] # Discover standard-library APIs")
    print(f"  {prog} capabilities [--output-format=json-v1]   # Discover target availability and requirements")
    print(f"  {prog} repl                                    # Start interactive REPL")
    print(f"  {prog} [run] [--stats] [--report-json <path>] [--invocation-id <id>] <file.wysna> [-- args...] # Execute Wysna source file")
    print(f"  {prog} run --deny=<cap,...>|--allow=<cap,...> <file.wysna> # Static capability gate before interpretation")
    print(f"  {prog} check [--json|--output-format=json-v1] [--warn-cycles] [--stats] <file.wysna>   # Static typecheck without execution")
    print(f"  {prog} fmt [--write] [--output-format=json-v1] <file.wysna> # Canonical deterministic formatter")
    print(f"  {prog} build [--engine=seed|compiler] [--backend=llvm-experimental] [--profile=dev|release|size] [--output-format=json-v1] <file.wysna> [-o <bin>] # Compile a native binary")
    print(f"  {prog} version                                   # Show compiler version")
    print("Compiler inspection and verification:")
    print(f"  {prog} ast [--json] <file.wysna>               # Emit canonical JSON AST (v0.1)")
    print(f"  {prog} mir <file.wysna>                        # Emit verified MIR as JSON")
    print(f"  {prog} emit-mir [--implementation=seed|compiler] <file.wysna> [-o <out.json>] # Emit portable MIR")
    print(f"  {prog} tokens <file.wysna>                     # Emit lexical token stream")
    print(f"  {prog} emit-c [--engine=seed|compiler] [--output-format=json-v1] <file.wysna> [-o <out.c>] # Emit C99")
    print("Experimental tools (see docs/SCOPE.md for their contracts):")
    print(f"  {prog} service serve --worker <bin> --database <db> # Local bounded HTTP/JSON pilot")
    print(f"  {prog} emit-llvm <file.wysna> [-o <out.ll>] # Native-command LLVM IR (Seed)")
    print(f"  {prog} run [--backend=c99|llvm] [--sanitize] [--timeout-ms <N>] <file.wysna> [-- args...]")
    print(f"  {prog} build [--backend=c99|llvm] [--profile=dev|release|size] <file.wysna> [-o <bin>]")
    print("  Engines: seed (full Seed profile, default) or compiler (native-core-v1; emit-c/build only here).")
    print("  Backends: c99 (default) or llvm (alias: llvm-experimental). The AST emitter is a test oracle, not a CLI backend.")
    print("Source-checkout maintenance:")
    print(f"  {prog} test [--differential [N]] [--fuzz [N]]            # Run conformance, differential, or fuzz suite")
    print(f"  {prog} bench [--update-baseline]                         # Run Seed/Compiler performance benchmark harness")



def main(argv=None) -> None:
    prog = "wysna"
    args = list(sys.argv[1:] if argv is None else argv)
    cli_args = args[:args.index("--")] if "--" in args else args
    json_v1 = "--output-format=json-v1" in cli_args
    report_json_path = None
    requested_invocation_id = None
    command_for_output = args[0] if args and args[0] in (
        "run", "check", "fmt", "ast", "mir", "tokens", "emit-c", "emit-llvm", "build",
        "test", "bench", "version", "repl",
    ) else "run"

    def usage_error(message: str) -> None:
        if json_v1 and report_json_path is None:
            diagnostic = diagnostic_record("W5002", "usage", message)
            write_tool_result(
                tool_result(command_for_output, "usage_error", 2,
                            diagnostics=[diagnostic]),
                sys.stdout,
            )
            raise SystemExit(2)
        print(f"{prog}: {message}", file=sys.stderr)
        print(f"Try '{prog} --help' for usage.", file=sys.stderr)
        raise SystemExit(2)

    if args and args[0] == "project":
        from tooling.ProjectCLI import run_project_command
        raise SystemExit(run_project_command(args[1:]))
    if args and args[0] == "explain":
        from tooling.ExplainCLI import run_explain_command
        raise SystemExit(run_explain_command(args[1:]))
    if args and args[0] == "emit-mir":
        from tooling.MirCLI import run_mir_command
        raise SystemExit(run_mir_command(args[1:]))
    if args and args[0] == "service":
        from tooling.ServiceCLI import run_service_command
        raise SystemExit(run_service_command(args[1:]))
    if args and args[0] == "api":
        from tooling.ApiIndex import run_api_command
        raise SystemExit(run_api_command(args[1:]))
    if args and args[0] == "capabilities":
        from tooling.ApiIndex import run_capabilities_command
        raise SystemExit(run_capabilities_command(args[1:]))
    if args == ["lsp"]:
        from tooling.LanguageServer import serve
        serve()
        return
    show_tokens = False
    show_ast = False
    check_only = False
    warn_cycles = False
    json_diagnostics = False
    show_stats = False
    fmt_only = False
    fmt_write = False
    backend = "c99"
    backend_explicit = False
    engine = "seed"
    engine_explicit = False
    emit_c_path: Optional[str] = None
    build_bin_path: Optional[str] = None
    emit_llvm_path: Optional[str] = None
    optimization_profile = None
    run_timeout_ms = None
    capability_policy = None
    show_mir = False
    subcmd: Optional[str] = None
    file_to_run: Optional[str] = None
    sanitize = False
    link_libraries = []

    if args and args[0] in ("run", "check", "fmt", "ast", "mir", "tokens", "emit-c", "emit-llvm", "build", "test", "bench", "version", "repl"):
        subcmd = args[0]
        command_for_output = subcmd
        args = args[1:]
        if subcmd == "bench":
            if any(arg not in ("--update-baseline", "--help", "-h") for arg in args):
                usage_error("bench accepts only --update-baseline")
            if args in (["--help"], ["-h"]):
                print_help()
                return
            if not os.path.isfile(os.path.join(get_repo_root(), "bench", "run_bench.py")):
                print(f"{prog} bench requires a source checkout", file=sys.stderr)
                sys.exit(1)
            update_baseline = "--update-baseline" in args
            repo_root = get_repo_root()
            selfhost_bin = ensure_selfhost_compiler_binary(repo_root)
            from bench.run_bench import run_benchmark_suite
            ok = run_benchmark_suite(repo_root, selfhost_bin, update_baseline=update_baseline)
            sys.exit(0 if ok else 1)
        elif subcmd == "test":
            if args in (["--help"], ["-h"]):
                print_help()
                return
            if not os.path.isdir(os.path.join(get_repo_root(), "tests", "unit")):
                print(f"{prog} test requires a source checkout", file=sys.stderr)
                print(f"To run project tests, use '{prog} project test'.", file=sys.stderr)
                sys.exit(1)
            if args and args[0] == "--differential":
                gen_count = 25
                base_seed = 1000
                idx = 1
                count_seen = False
                seed_seen = False
                while idx < len(args):
                    if args[idx] == "--seed" and not seed_seen and idx + 1 < len(args) and args[idx + 1].isdigit():
                        base_seed = int(args[idx + 1])
                        seed_seen = True
                        idx += 2
                    elif args[idx].isdigit() and not count_seen:
                        gen_count = int(args[idx])
                        count_seen = True
                        idx += 1
                    else:
                        usage_error(f"invalid test option '{args[idx]}'")
                repo_root = get_repo_root()
                selfhost_bin = ensure_selfhost_compiler_binary(repo_root)
                from tests.differential.differential_test import run_differential_suite
                ok = run_differential_suite(repo_root, selfhost_bin, generated_count=gen_count, base_seed=base_seed)
                sys.exit(0 if ok else 1)
            elif args and args[0] == "--fuzz":
                fuzz_count = 200
                base_seed = 9000
                idx = 1
                count_seen = False
                seed_seen = False
                while idx < len(args):
                    if args[idx] == "--seed" and not seed_seen and idx + 1 < len(args) and args[idx + 1].isdigit():
                        base_seed = int(args[idx + 1])
                        seed_seen = True
                        idx += 2
                    elif args[idx].isdigit() and not count_seen:
                        fuzz_count = int(args[idx])
                        count_seen = True
                        idx += 1
                    else:
                        usage_error(f"invalid test option '{args[idx]}'")
                repo_root = get_repo_root()
                from tests.differential.differential_test import run_fuzz_suite
                ok = run_fuzz_suite(fuzz_count=fuzz_count, base_seed=base_seed, repo_root=repo_root)
                sys.exit(0 if ok else 1)
            else:
                if any(arg not in ("--sanitize", "-s") for arg in args):
                    usage_error(f"invalid test option '{next(arg for arg in args if arg not in ('--sanitize', '-s'))}'")
                if args and ("--sanitize" in args or "-s" in args):
                    os.environ["WYSNA_SANITIZE"] = "1"
                from run_conformance import run_suite
                run_suite()
                return
        elif subcmd == "version":
            print(f"Wysna Language Compiler v{WYSNA_VERSION} (Wysna Seed + Wysna Compiler Self-Hosted C99)")
            return
        elif subcmd == "emit-llvm":
            backend = "llvm-experimental"
        elif subcmd == "check":
            check_only = True
        elif subcmd == "fmt":
            fmt_only = True
        elif subcmd == "ast":
            show_ast = True
        elif subcmd == "mir":
            show_mir = True
        elif subcmd == "tokens":
            show_tokens = True

    program_args = []
    if "--" in args:
        separator = args.index("--")
        if subcmd not in (None, "run"):
            usage_error("program arguments after -- are supported only by run")
        if separator == 0:
            usage_error("a source file must appear before --")
        program_args = args[separator + 1:]
        args = args[:separator]

    i = 0
    while i < len(args):
        arg = args[i]
        if arg in ("--tokens", "-t"):
            show_tokens = True
        elif arg == "--ast":
            usage_error("--ast is not a compiler backend; use 'wysna ast <file>' to inspect syntax")
        elif arg in ("--check", "-c"):
            check_only = True
        elif arg == "--warn-cycles":
            warn_cycles = True
        elif arg == "--json":
            json_diagnostics = True
        elif arg == "--output-format=json-v1":
            json_v1 = True
        elif arg == "--report-json":
            if report_json_path is not None:
                usage_error("--report-json may be specified only once")
            i += 1
            if i >= len(args) or not args[i] or args[i].startswith("-"):
                usage_error("--report-json requires a file path")
            report_json_path = args[i]
        elif arg == "--invocation-id":
            if requested_invocation_id is not None:
                usage_error("--invocation-id may be specified only once")
            i += 1
            if i >= len(args) or args[i].startswith("-"):
                usage_error("--invocation-id requires a value")
            requested_invocation_id = args[i]
        elif arg in ("--stats", "--metrics"):
            show_stats = True
        elif arg == "--fmt":
            fmt_only = True
        elif arg in ("--write", "-w"):
            fmt_write = True
        elif arg in ("--selfhost", "--hir"):
            usage_error(f"{arg} is no longer a backend selector; use --engine=compiler or --engine=seed")
        elif arg.startswith("--engine="):
            if engine_explicit:
                usage_error("--engine may be specified only once")
            engine = arg.split("=", 1)[1]
            if engine not in ("seed", "compiler"):
                usage_error("Unknown engine. Valid engines: seed, compiler")
            engine_explicit = True
        elif arg.startswith("--backend="):
            if backend_explicit:
                usage_error("--backend may be specified only once")
            b_val = arg.split("=", 1)[1]
            if b_val in ("hir", "ast", "selfhost"):
                usage_error(f"--backend={b_val} mixes compiler engine selection with output targets; "
                            "use --engine=seed|compiler. The AST oracle is test-only.")
            if b_val in ("c99", "llvm", "llvm-experimental"):
                backend = "llvm-experimental" if b_val == "llvm" else b_val
                backend_explicit = True
            else:
                usage_error(f"Unknown backend '{b_val}'. Valid backends: c99, llvm, llvm-experimental")
        elif arg == "--timeout-ms":
            i += 1
            if i >= len(args) or not args[i].isdigit() or not 1 <= int(args[i]) <= 600000:
                usage_error("--timeout-ms requires an integer from 1 to 600000")
            run_timeout_ms = int(args[i])
        elif arg.startswith("--profile="):
            optimization_profile = arg.split("=", 1)[1]
            if optimization_profile not in ("dev", "release", "size"):
                usage_error("--profile must be dev, release, or size")
        elif arg in ("--version", "-v"):
            print(f"Wysna Language Compiler v{WYSNA_VERSION} (Wysna Seed + Wysna Compiler Self-Hosted C99)")
            sys.exit(0)
        elif arg == "--emit-c":
            i += 1
            if i >= len(args) or args[i].startswith("-"):
                usage_error("--emit-c requires an output path")
            emit_c_path = args[i]
        elif arg == "--build":
            i += 1
            if i >= len(args) or args[i].startswith("-"):
                usage_error("--build requires an output path")
            build_bin_path = args[i]
        elif arg == "-o":
            i += 1
            if i >= len(args) or args[i].startswith("-"):
                usage_error("-o requires an output path")
            if subcmd == "emit-c":
                emit_c_path = args[i]
            elif subcmd == "emit-llvm":
                emit_llvm_path = args[i]
            elif subcmd == "build":
                build_bin_path = args[i]
            else:
                build_bin_path = args[i]
        elif arg.startswith("--deny=") or arg.startswith("--allow="):
            if capability_policy is not None:
                usage_error("--allow and --deny may be specified only once")
            mode, value = arg[2:].split("=", 1)
            from tooling.CapabilityPolicy import parse_capabilities
            try:
                capability_policy = (mode, parse_capabilities(value))
            except ValueError as error:
                usage_error(str(error))
        elif arg == "--link-lib":
            i += 1
            if i >= len(args) or args[i].startswith("-"):
                usage_error("--link-lib requires a library name or path")
            link_libraries.append(args[i])
        elif arg in ("--sanitize", "-s"):
            sanitize = True
        elif arg in ("--help", "-h"):
            if json_v1:
                usage_error("--help cannot be combined with --output-format=json-v1")
            print_help()
            sys.exit(0)
        elif arg.startswith("-"):
            usage_error(f"unrecognized option '{arg}'")
        else:
            if file_to_run is not None:
                usage_error("only one source file may be specified")
            file_to_run = arg
        i += 1

    prog_cmd = "wysna"

    if capability_policy is not None:
        if link_libraries:
            usage_error("--link-lib cannot be combined with a capability policy")
        if (subcmd not in (None, "run") or backend_explicit or engine_explicit
                or backend != "c99" or engine != "seed"
                or check_only or fmt_only or show_tokens or show_ast or show_mir
                or emit_c_path is not None or build_bin_path is not None
                or emit_llvm_path is not None):
            usage_error("--allow and --deny are supported only by interpreted run")

    if subcmd == "emit-llvm" and backend != "llvm-experimental":
        usage_error("emit-llvm requires --backend=llvm-experimental")
    if backend_explicit and backend == "c99" and subcmd not in ("emit-c", "build"):
        usage_error("--backend=c99 applies only to emit-c and build; check, fmt, and run use Seed directly")
    if engine == "compiler":
        if subcmd not in ("emit-c", "build"):
            usage_error("--engine=compiler in the Seed CLI supports emit-c and build only; use the standalone Compiler SDK for native check/run commands")
        if backend != "c99":
            usage_error("--engine=compiler currently supports only the C99 output backend")
        if (show_tokens or show_ast or show_mir or fmt_only or fmt_write or check_only):
            usage_error("--engine=compiler does not support Seed inspection or formatting options")
    if optimization_profile is not None and backend != "llvm-experimental" and subcmd != "build":
        usage_error("--profile is supported by native builds and LLVM source commands")
    if run_timeout_ms is not None and (subcmd not in (None, "run") or backend != "llvm-experimental"):
        usage_error("--timeout-ms is supported by run --backend=llvm-experimental")
    if backend == "llvm-experimental":
        if engine != "seed":
            usage_error("llvm-experimental currently supports only the Seed engine")
        if subcmd not in (None, "run", "check", "emit-llvm", "build"):
            usage_error("llvm-experimental supports run, check, emit-llvm, and build")
        if (show_tokens or show_ast or show_mir or fmt_only or fmt_write
                or emit_c_path is not None or json_diagnostics):
            usage_error("llvm-experimental does not support these diagnostic, C generation, or build options")

    if requested_invocation_id is not None and report_json_path is None:
        usage_error("--invocation-id requires --report-json")

    if report_json_path is not None:
        if subcmd not in (None, "run"):
            usage_error("--report-json is supported only by run")
        if json_v1 or json_diagnostics:
            usage_error("run --report-json cannot be combined with JSON output modes")
        if (show_tokens or show_ast or check_only or fmt_only or fmt_write
                or emit_c_path is not None or build_bin_path is not None or show_mir):
            usage_error("run --report-json supports program execution only")
        if requested_invocation_id == "":
            usage_error("--invocation-id must not be empty")

    if json_v1:
        supported_commands = ("check", "fmt", "emit-c", "emit-llvm", "build")
        if subcmd not in supported_commands:
            usage_error("--output-format=json-v1 is supported only by check, fmt, emit-c, emit-llvm, and build")
        if json_diagnostics or show_stats or show_ast or show_tokens or show_mir:
            usage_error("--output-format=json-v1 cannot be combined with legacy JSON, stats, or diagnostic output modes")
        if subcmd == "check":
            if (not check_only or fmt_only or fmt_write or emit_c_path is not None
                    or build_bin_path is not None):
                usage_error("check --output-format=json-v1 supports type checking only")
            if sanitize or link_libraries:
                usage_error("check --output-format=json-v1 does not accept build options")
        elif subcmd == "fmt":
            if not fmt_only or check_only or emit_c_path is not None or build_bin_path is not None:
                usage_error("fmt --output-format=json-v1 supports formatting only")
            if sanitize or link_libraries or backend != "c99" or engine != "seed":
                usage_error("fmt --output-format=json-v1 does not accept backend or build options")
        elif subcmd == "emit-c":
            if check_only or fmt_only or fmt_write or build_bin_path is not None:
                usage_error("emit-c --output-format=json-v1 supports C generation only")
            if sanitize or link_libraries:
                usage_error("emit-c --output-format=json-v1 does not accept build options")
        elif subcmd == "emit-llvm":
            if check_only or fmt_only or fmt_write or build_bin_path is not None:
                usage_error("emit-llvm supports LLVM generation only")
        elif subcmd == "build":
            if check_only or fmt_only or fmt_write or emit_c_path is not None:
                usage_error("build --output-format=json-v1 supports native compilation only")

    if file_to_run:
        if file_to_run.endswith(".w"):
            if json_v1:
                usage_error("'.w' files are no longer supported; rename the source to use the .wysna extension")
            print(f"error: '.w' files are no longer supported. Rename '{file_to_run}' to '{file_to_run[:-2]}.wysna'", file=sys.stderr)
            sys.exit(1)

        # Resolve file with extension fallback if omitted
        if not os.path.isfile(file_to_run) and os.path.isfile(file_to_run + ".wysna"):
            file_to_run = file_to_run + ".wysna"

        def _strip_src_ext(p: str) -> str:
            if p.endswith(".wysna"):
                return p[:-6]
            return p

        if subcmd == "emit-c" and emit_c_path is None:
            emit_c_path = _strip_src_ext(file_to_run) + ".c"
        elif subcmd == "emit-llvm" and emit_llvm_path is None:
            emit_llvm_path = _strip_src_ext(file_to_run) + ".ll"
        elif subcmd == "build" and build_bin_path is None:
            build_bin_path = _strip_src_ext(file_to_run) if file_to_run.endswith(".wysna") else file_to_run + ".bin"

        report_state = None
        invocation_id = requested_invocation_id
        if report_json_path is not None:
            if _same_file_path(report_json_path, file_to_run):
                usage_error("the report path must not overwrite the source file")
            pending = tool_result(
                "run",
                "pending",
                None,
                process_exit_code=None,
                invocation_id=invocation_id,
            )
            invocation_id = pending["invocation_id"]
            try:
                write_tool_result_atomic(pending, report_json_path)
            except OSError as error:
                print(
                    f"{prog}: unable to initialize JSON report '{report_json_path}': {error}",
                    file=sys.stderr,
                )
                raise SystemExit(3)
            report_state = {
                "status": "pending",
                "tool_exit_code": None,
                "process_exit_code": None,
                "execution": None,
                "diagnostics": [],
            }

        if json_v1 and subcmd in ("emit-c", "emit-llvm", "build"):
            output_path = {"emit-c": emit_c_path, "emit-llvm": emit_llvm_path, "build": build_bin_path}[subcmd]
            canonical_output = os.path.normcase(os.path.realpath(output_path))
            canonical_source = os.path.normcase(os.path.realpath(file_to_run))
            try:
                same_file = os.path.samefile(output_path, file_to_run)
            except OSError:
                same_file = False
            if canonical_output == canonical_source or same_file:
                usage_error("the output path must not overwrite the source file")

        try:
            with open(file_to_run, "r", encoding="utf-8",
                      newline="" if json_v1 or report_state is not None else None) as f:
                source = f.read()
        except (OSError, UnicodeError) as e:
            if report_state is not None:
                diagnostic = diagnostic_record(
                    "W5003", "filesystem",
                    f"Unable to read source file '{file_to_run}': {e}",
                    filename=file_to_run,
                )
                _set_run_report(report_state, "toolchain_error", 3, 3,
                                diagnostics=[diagnostic])
                print(f"Error reading file '{file_to_run}': {e}", file=sys.stderr)
                try:
                    _write_run_report(report_json_path, invocation_id, report_state)
                except OSError as report_error:
                    print(f"{prog}: unable to finalize JSON report: {report_error}", file=sys.stderr)
                raise SystemExit(3)
            if json_v1:
                diagnostic = diagnostic_record(
                    "W5003", "filesystem", f"Unable to read source file '{file_to_run}': {e}",
                    filename=file_to_run,
                )
                write_tool_result(
                    tool_result(command_for_output, "toolchain_error", 3,
                                diagnostics=[diagnostic]),
                    sys.stdout,
                )
                sys.exit(3)
            print(f"Error reading file '{file_to_run}': {e}", file=sys.stderr)
            sys.exit(1)

        code = run_pipeline(
            source_code=source,
            file_name=file_to_run,
            check_only=check_only,
            show_tokens=show_tokens,
            show_ast=show_ast,
            show_mir=show_mir,
            json_diagnostics=json_diagnostics,
            fmt_only=fmt_only,
            fmt_write=fmt_write,
            emit_c_path=emit_c_path,
            build_bin_path=build_bin_path,
            emit_llvm_path=emit_llvm_path,
            optimization_profile=optimization_profile,
            run_timeout_ms=run_timeout_ms,
            capability_policy=capability_policy,
            backend=backend,
            engine=engine,
            sanitize=sanitize,
            show_stats=show_stats,
            link_libraries=link_libraries,
            execution_context=ExecutionContext.for_program(
                prog_cmd,
                program_args,
                **({"terminate": _capture_application_exit} if report_state is not None else {}),
            ),
            json_v1=json_v1,
            warn_cycles=warn_cycles,
            tool_command=command_for_output,
            report_state=report_state,
        )
        if report_state is not None:
            if report_state["status"] == "pending":
                _set_run_report(
                    report_state,
                    "ok",
                    0,
                    0,
                    execution={"kind": "application_exit", "exit_code": 0, "signal": None},
                )
            try:
                _write_run_report(report_json_path, invocation_id, report_state)
            except OSError as report_error:
                print(f"{prog}: unable to finalize JSON report: {report_error}", file=sys.stderr)
                raise SystemExit(3)
            sys.exit(report_state["process_exit_code"])
        sys.exit(code)

    # No file argument. Dropping into the REPL silently made `w check` in a Makefile exit
    # 0 without checking anything, so the REPL now needs to be asked for explicitly and
    # every other subcommand is a usage error.
    if report_json_path is not None:
        usage_error("--report-json requires a source file")
    if program_args:
        usage_error("program arguments require a source file")
    if subcmd is None or subcmd == "repl":
        if json_v1:
            usage_error("--output-format=json-v1 requires a supported command and a source file")
        repl(show_tokens=show_tokens, show_ast=show_ast)
        return
    if json_v1:
        usage_error(f"{subcmd} requires a source file")
    print(f"{prog_cmd} {subcmd}: no input file given", file=sys.stderr)
    print(f"usage: {prog_cmd} {subcmd} <file.wysna> [options]", file=sys.stderr)
    print(f"       {prog_cmd} repl        # start the interactive REPL", file=sys.stderr)
    sys.exit(2)
