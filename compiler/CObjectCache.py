"""C object caching with compiler identity and freshly resolved transitive headers."""
import os
import re
from pathlib import Path
import shlex
import shutil
import subprocess
import tempfile
from compiler.ArtifactCache import file_digest
from compiler.NativeToolchain import NativeCompilationError


def compiler_identity(command):
    try:
        # Zig's version-only Clang path rejects GNU ABI versions that real builds accept.
        zig_driver = (Path(command[0]).name.lower() in ('zig', 'zig.exe')
                      and command[1:2] in (['cc'], ['c++']))
        version_command = command[:2] if zig_driver else command
        process = subprocess.run(version_command + ['--version'], capture_output=True, text=True, timeout=30)
    except (OSError, subprocess.TimeoutExpired) as error:
        raise NativeCompilationError('configuration', 'C Compiler Error', str(error), 127) from error
    if process.returncode or not process.stdout.strip():
        raise NativeCompilationError('configuration', 'C Compiler Error', process.stderr, process.returncode or 1)
    path = shutil.which(command[0])
    if path is None:
        raise NativeCompilationError('configuration', 'C Compiler Error', 'Compiler executable is unavailable', 127)
    return {'command': command, 'version': process.stdout.splitlines()[0],
            'executable_sha256': file_digest(Path(path).resolve())}


def compile_cached_c(cache, source, output, flags, identity, run, *, extra_contract=None):
    source, output = Path(source), Path(output)
    source_hash = file_digest(source)

    def headers(dependencies):
        text = dependencies.read_text(encoding='utf-8').replace('\\\n', ' ')
        paths = shlex.split(re.split(r':\s', text, maxsplit=1)[1])
        return {str(Path(path).resolve()): file_digest(path) for path in paths
                if Path(path).resolve() != source.resolve()}

    # Resolve again even on a hit: an identical source in another directory can
    # include a different local header while the previous header remains intact.
    with tempfile.TemporaryDirectory(prefix='wysna-c-dependencies-') as temporary:
        dependencies = Path(temporary) / 'headers.d'
        run(flags + ['-M', '-MF', str(dependencies), str(source)], 'dependency_scan')
        before = headers(dependencies)
    environment = {name: os.environ.get(name) for name in ('SDKROOT', 'CPATH', 'C_INCLUDE_PATH', 'MACOSX_DEPLOYMENT_TARGET')}
    contract = {'source_sha256': source_hash, 'flags': flags, 'compiler': identity,
                'environment': environment, 'headers': before, 'extra': extra_contract}

    def produce(pending):
        dependencies = pending / 'headers.d'
        compiled = pending / 'object.o'
        run(flags + ['-MD', '-MF', str(dependencies), '-c', str(source), '-o', str(compiled)], 'c_compile')
        if before != headers(dependencies) or source_hash != file_digest(source):
            raise NativeCompilationError('dependencies', 'C Compiler Error',
                                         'Source or headers changed during compilation; retry with stable inputs', 1)
        return compiled.read_bytes(), before.keys()

    data, hit, elapsed = cache.obtain('c-objects', contract, produce)
    output.write_bytes(data)
    return hit, elapsed
