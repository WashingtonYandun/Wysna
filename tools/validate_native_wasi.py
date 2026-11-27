#!/usr/bin/env python3
"""Execute a WASI command built with the native SDK's bundled Zig."""

import argparse
import json
import os
from pathlib import Path
import subprocess
import tempfile

from wasmtime import Engine, Linker, Module, Store, WasiConfig

from validate_native_sdk import isolated_environment, require


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--sdk', type=Path, required=True)
    sdk = parser.parse_args().sdk.resolve()
    driver = sdk / 'bin' / ('wysna.exe' if os.name == 'nt' else 'wysna')
    with tempfile.TemporaryDirectory(prefix='wysna-native-wasi-') as name:
        root = Path(name)
        environment = isolated_environment(root)
        source = root / 'command.wysna'
        source.write_text(
            'import "std/algorithms" as algorithms\n'
            'fn double(value: int) -> int { value * 2 }\n'
            'print(algorithms.parallel_map([1, 2, 3], double))\n', encoding='utf-8',
        )
        target = root / 'command.wasm'
        command = [str(driver), 'build', '--target=wasi', str(source), '-o', str(target),
                   '--output-format=json-v1']
        compiled = subprocess.run(command, cwd=root, env=environment, capture_output=True,
                                  text=True, encoding='utf-8', timeout=180)
        require(compiled.returncode == 0, (compiled.stdout, compiled.stderr))
        report = json.loads(compiled.stdout)
        require(report['artifacts'][0]['kind'] == 'wasm_module', report)

        engine = Engine()
        module = Module.from_file(engine, str(target))
        require(all(entry.module == 'wasi_snapshot_preview1' for entry in module.imports),
                [entry.module for entry in module.imports])
        stdout = root / 'stdout.txt'
        wasi = WasiConfig()
        wasi.argv = ['wysna']
        wasi.stdout_file = str(stdout)
        store = Store(engine)
        store.set_wasi(wasi)
        linker = Linker(engine)
        linker.define_wasi()
        linker.instantiate(store, module).exports(store)['_start'](store)
        require(stdout.read_text(encoding='utf-8') == '[2, 4, 6]\n', 'incorrect WASI worker fallback')

        prior = target.read_bytes()
        source.write_text('import "std/process"\nlet _ = process.run("child", [], 100)\n', encoding='utf-8')
        denied = subprocess.run(command, cwd=root, env=environment, capture_output=True,
                                text=True, encoding='utf-8', timeout=30)
        require(denied.returncode != 0 and 'W5006' in denied.stdout + denied.stderr, denied)
        require(target.read_bytes() == prior, 'rejected build replaced the valid WASI artifact')

        web_source = root / 'web_app.wysna'
        web_source.write_text(
            'import "std/environment" as env\n'
            'let argv = env.arguments()\n'
            'let who = if len(argv) > 1 { argv[1] } else { "world" }\n'
            'let greeting = env.get("WYSNA_GREETING") ?? "Hello"\n'
            'print(f"{greeting}, {who}")\n',
            encoding='utf-8',
        )
        web_out = root / 'web_bundle'
        web_cmd = [str(driver), 'build', '--target=web', str(web_source), '-o', str(web_out), '--output-format=json-v1']
        web_built = subprocess.run(web_cmd, cwd=root, env=environment, capture_output=True, text=True, encoding='utf-8', timeout=180)
        require(web_built.returncode == 0, (web_built.stdout, web_built.stderr))
        web_report = json.loads(web_built.stdout)
        require(web_report['target'] == {'profile': 'web-wasm', 'triple': 'wasm32-wasip1'}, web_report)
        require([a['kind'] for a in web_report['artifacts']] == ['wasm_module', 'javascript_module', 'typescript_declarations'], web_report)
        require((root / 'web_bundle.wasm').is_file() and (root / 'web_bundle.mjs').is_file() and (root / 'web_bundle.d.ts').is_file(), 'missing web artifacts')
    print('Native WASI: built and executed WASI Preview 1 + --target=web bundle')


if __name__ == '__main__':
    main()
