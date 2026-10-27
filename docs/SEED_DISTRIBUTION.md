# Standalone Seed distribution

`tools/package_distribution.py` assembles a relocatable Wysna Seed installation from a
built wheel. It bundles a private CPython and the pinned Zig 0.15.2 C toolchain, so the
installed command does not need host Python, a virtual environment, or a system C
compiler. It is separate from the Python-free [native SDK](NATIVE_SDK.md) and from the
[Seed LLVM SDK](LLVM_SDK.md).

## Supported hosts

| Host | Private runtime | C toolchain | Launcher |
| :--- | :--- | :--- | :--- |
| macOS arm64 | CPython 3.14.10 | Zig 0.15.2, `aarch64-macos.13.0-none` | `bin/wysna` |
| Linux x64 | CPython 3.14.10 | Zig 0.15.2, `x86_64-linux-gnu.2.28` | `bin/wysna` |
| Windows x64 | CPython 3.14.8 embeddable | Zig 0.15.2, `x86_64-windows-gnu` | `wysna.exe` |

The builder rejects an unpinned host. Windows packaging reuses
`tools/package_windows.py`; Unix packaging extracts the wheel, the pinned CPython
archive and the pinned Zig archive into one directory.

## Build

Build the wheel first, then assemble into a new directory:

```sh
python -m build --wheel --outdir dist/wheel
python tools/package_distribution.py \
  --wheel dist/wheel/wysna-0.5.0-py3-none-any.whl \
  --output dist/wysna-seed \
  --source-commit "$(git rev-parse HEAD)"
```

`--cache` selects the component download cache; the default is
`.wysna_cache/installation-downloads`. `--source-commit` requires that exact clean
checkout, binding an installation to reviewable source. Bit-for-bit rebuilds are
not claimed. Without it, the
builder records the actual commit and whether the checkout was dirty.

The builder verifies pinned SHA-256 checksums before extraction, rejects absolute or
traversal archive members, rejects unsafe Python symlinks, requires a new output
directory, and never replaces an existing installation.

## Installed layout

```text
wysna-seed/
  bin/wysna              # wysna.exe on Windows
  wysna_entry.py
  installation.json
  main.py
  compiler/ tooling/ standard packages
  python/
  toolchain/zig/
  runtime/ stdlib/
  LICENSE THIRD_PARTY.txt
```

On Windows the bundle also contains `open-terminal.cmd` for a terminal-local `PATH`.

`installation.json` uses `wysna.installation/1` and records the implementation, Wysna
version, platform, source commit and dirty flag, component URLs/checksums, launcher,
Python and compiler paths, target triple, creation time, wheel SHA-256, and a SHA-256
for every installed file. It also lists source-only commands and optional external
dependencies.

## Running and integrity

```sh
./bin/wysna --version
./bin/wysna check hello.wysna
./bin/wysna doctor
./bin/wysna doctor --output-format=json-v1
```

`wysna doctor` exists only in the installed distribution; a source checkout has no
`doctor` subcommand. The launcher verifies a required component set before importing
Seed, and `doctor` verifies the complete file index. A missing or modified component
produces `W5001` and exit status `3`; it never falls back to host Python or the host
compiler.

`doctor` reports `wysna.installation-health/1`, including the installation version,
platform, source commit, root, verified file count, and that host Python and a host C
compiler are not required.

By default the bootstrap sets `CC` to the bundled Zig compiler with the recorded
target. A `CC` already present in the environment takes precedence; unset it to force
the bundled toolchain.

## Scope and limits

- `wysna test`, `wysna bench`, and `--backend=selfhost` require a source checkout and
  are not part of this distribution.
- HTTP requires separately installed libcurl development files and `curl-config`.
  Declared C FFI libraries and their headers remain explicit dependencies.
  Apple's system libcurl uses a macOS SDK; `SDKROOT` can select a compatible SDK.
  A separately installed libcurl must expose headers/libraries compatible with
  the private compiler target. Core native commands do not require these inputs.
  The private Python does not bundle PycURL: use native HTTP builds for this
  distribution. Interpreter HTTP extras described elsewhere apply to source/wheel
  environments and are not installed by the graphical package.
- Project, formatter and LSP commands come from the bundled Seed wheel.
- WASI execution still needs the documented Wasmtime dependency and wasi-sdk;
  browser-library builds need Emscripten. These optional target toolchains are not
  bundled with the native installation. LLVM also requires its separate optional
  tools or experimental SDK.
- The assembled installation is a portable directory. Updating means building beside the old
  directory and switching the command or `PATH`; it is not an in-place package
  manager.
- CI's `windows-installer` and `seed-installers` gates build native installers and
  test installation, repair, upgrade from an explicitly synthetic previous build,
  installed tools/application, and uninstallation. RPM is exercised in Fedora 44;
  Debian packages use the Ubuntu runner. Qualification belongs to each CI commit.
  The public [0.5.0 release](https://github.com/WashingtonYandun/Wysna/releases/tag/v0.5.0)
  attaches the accepted Windows, macOS and Linux installers, SHA-256 manifests and
  provenance records for its exact source commit. The installers are unsigned; the
  macOS package is not notarized. Operating systems may display security warnings.

## Graphical installer release packages

`tools/package_installers.py --bundle PATH --output NEW_DIRECTORY` wraps a verified
distribution in the native package format: Inno Setup `.exe` on Windows, system
Installer `.pkg` on macOS arm64, and `.deb`/`.rpm` on Linux x64. Linux builders may
select `--formats deb` or `--formats rpm`. Building requires the corresponding
packaging tools on that host; end users do not need them.

The macOS package keeps versioned directories under
`/Library/Application Support/Wysna/versions`, activates the checked version through
`/usr/local/bin/wysna`, and provides **Uninstall Wysna** in **Applications / Wysna
Tools**. Its uninstaller validates ownership receipts before removing anything,
preserves changed and unmanaged files, and leaves user projects outside the
installation untouched. Linux integrates with the distribution's package manager;
Windows retains its per-user wizard and registered uninstaller.

`installers.json` and `SHA256SUMS` record the exact package artifacts and source
provenance. `--signing-identity` signs only the macOS Installer package; it does not
notarize the package or sign every embedded binary. The 0.5.0 release does not
include signed or notarized installers.

Run the standalone acceptance tool outside the checkout:

```sh
python tools/validate_distribution.py --installation PATH --report acceptance.json
```

It removes host development tools from `PATH` and verifies integrity, reference and
native execution, project lock/check/run/test, formatter, API/effect reports, LSP
stdio, relocation and damaged-component rejection. `--source-commit SHA` additionally
requires a clean bundle from that exact commit. This validates the installed bundle;
it does not replace native installer install/update/uninstall acceptance on each OS.

CI runs `validate_unix_lifecycle.py` only on disposable systems (`CI=true`), using
the same package payload/scripts through the native package manager. It checks the
generated macOS graphical uninstaller and invokes its shared implementation without
automating administrator dialogs. Windows uses Inno's silent lifecycle. GUI clicks
are not claimed as automated acceptance. Signature/notarization status stays explicit.

The [reference application](../examples/automation-sync/README.md) validates the
installed native JSON/SQLite workflow on all three hosts and its HTTP acquisition
command on Linux/macOS with declared libcurl inputs.
