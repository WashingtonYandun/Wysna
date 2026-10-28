# Contributing to Wysna

Thank you for your interest in contributing to the **Wysna Programming Language**! Whether you are reporting a bug, proposing language features, improving documentation, or submitting compiler patches, your contributions are welcome.

---

## Table of Contents

- [Code of Conduct](#code-of-conduct)
- [Architecture Overview](#architecture-overview)
- [Development Environment Setup](#development-environment-setup)
- [Testing & Quality Verification](#testing--quality-verification)
- [Self-Hosting Bootstrap Invariant](#self-hosting-bootstrap-invariant)
- [Coding & Commit Standards](#coding--commit-standards)
- [Pull Request Workflow](#pull-request-workflow)
- [Adding New Tests](#adding-new-tests)

---

## Code of Conduct

All contributors and participants are expected to adhere to our [Code of Conduct](CODE_OF_CONDUCT.md). Please report any violations to [washington.yandun.morales@gmail.com](mailto:washington.yandun.morales@gmail.com).

---

## Architecture Overview

The Wysna toolchain consists of two primary compiler engines and a unified C99 runtime.
Seed's batch CLI and LSP share `compiler/CompilerService.py`, which returns a
session-bound `CheckedProgram`. Consumers get isolated syntax trees; linking and lowering
reject stale or foreign checked results before using their type arena.

```text
Source (.wysna)
   │
   ▼
[ Lexer ] ────────▶ [ TokenStream ]
   │
   ▼
[ Parser ] ───────▶ Canonical AST (node/NodeType.py)
   │
   ▼
[ CompilerService / CompilationSession ]
   │
   ▼
[ TypeChecker ] ──▶ CheckedProgram / Canonical TypeId
   │
   ├──────────────────────────────┐
   ▼                              ▼
[ Interpreter / AST oracle ]  [ HirBuilder / HIR verifier ]
                                  │
                                  ▼
                             [ MIR / verifier / C99 ]
                                  │
                                  ▼
                             [ NativeToolchain ]

Independent Wysna Compiler: selfhost/src/ → direct C99 → NativeToolchain
```

- **Wysna Seed (`main.py`, `wysna`):** Reference implementation in Python 3. Contains the two-pass type checker, monomorphization engine, Typed HIR middle-end, and C99 native code generator.
- **Wysna Compiler (`selfhost/src/`):** modular sources written in Wysna, bundled into `selfhost/compiler.wysna`.
- **C99 Runtime (`runtime/wysna_runtime.h`):** Single-header runtime with single-threaded ARC, copy-on-write value graphs, length-aware UTF-8 strings, and explicit weak references.

---

## Development Environment Setup

### Prerequisites

- **Python:** 3.10 or higher.
- **C Compiler:** Any standard C99 compiler (`clang` recommended, or `gcc`).
- **Make:** Optional, for shortcut commands.
- **Git:** Standard git version control.

### Cloning & Verification

```bash
git clone https://github.com/WashingtonYandun/wysna.git
cd wysna

# Install pytest and the optional HTTP/service test dependencies
python3 -m pip install -e '.[dev]'

# Verify toolchain CLI
./wysna --version

# Run the full conformance test suite
./wysna test

# Run the complete Python unit suite
python3 -m pytest -q
```

---

## Testing & Quality Verification

Wysna adheres to strict conformance and correctness invariants. **All conformance tests must pass before submitting code.**

### 1. Conformance Test Suite
Runs the conformance checks for the lexer, parser, type checker, formatter, HIR C99 backend, runtime, and modular self-hosted compiler:
```bash
./wysna test
```

### 2. Memory Safety with Sanitizers (Clang ASan / UBSan)
Compile and test with AddressSanitizer and UndefinedBehaviorSanitizer enabled:
```bash
./wysna test --sanitize
# or via environment variable:
WYSNA_SANITIZE=1 ./wysna test
```

### 3. Four-Engine Differential Oracle
Validates interpreter, AST C99, HIR C99 and standalone Wysna Compiler outputs and diagnostics.
Feature support is declared in `tests/engine_profiles.json`; unsupported rows are reported separately:
```bash
./wysna test --differential 20
```

The CI corpus also verifies every runtime/example output and standalone negative fixture:
```bash
python3 tools/ci_conformance.py
CC=clang python3 tools/ci_conformance.py --sanitize
python3 tools/ci_conformance.py --profile=release
```

The integration profile reports explicitly unsupported engine/fixture combinations; the release profile requires every fixture to pass on every engine. CI reports coverage by engine and feature and retains source, generated C, commands, diagnostics, signals, timeouts, and toolchain versions for failures. See the [engine parity matrix](docs/ENGINE_PARITY_MATRIX.md) for the current tested boundary.

### 4. Modular Split Verification
Ensures the modular compiler files in `selfhost/src/` combine loss-lessly to exactly match `selfhost/compiler.wysna`:
```bash
python3 -m tests.unit.test_selfhost_split_conformance
```

### 5. Performance Benchmark
Runs the standard micro-benchmark harness:
```bash
./wysna bench
```

---

## Self-Hosting Bootstrap Invariant

Wysna guarantees a 3-stage cryptographic bootstrap fixed-point:
$$\text{SHA256}(\text{Stage}_1) == \text{SHA256}(\text{Stage}_2) == \text{SHA256}(\text{Stage}_3)$$

When modifying compiler frontend, parser, or code generation logic that affects Wysna Compiler:
1. Re-bundle the self-hosted compiler:
   ```bash
   python3 tools/bundle_selfhost.py
   ```
2. Verify the bootstrap fixed-point:
   ```bash
   python3 -m tests.unit.test_bootstrap_conformance
   ```

---

## Coding & Commit Standards

- **Documentation Scope:** Keep `SPEC.md` and `docs/` focused on current public contracts and usage. Maintain ADRs, audit reports, implementation histories, operational roadmaps, and session notes outside the source repository. Update the relevant existing manual instead of adding a completion report. Store generated verification output in local caches or CI artifacts.
- **Language Policy:**
  - Project explanations, specifications, and documentation are written in **English**.
  - Code, identifiers, types, code comments, and commit messages are written in **English**.
- **Code Formatting:**
  - Wysna source code: Run `./wysna fmt <file>.wysna` or format whole directories with `./wysna fmt examples/`.
  - Python codebase: Follow PEP 8 guidelines.
- **Commit Messages:**
  - Follow [Conventional Commits](https://www.conventionalcommits.org/):
    - `feat:` New features or language capabilities
    - `fix:` Bug fixes in compiler, runtime, or tools
    - `docs:` Public documentation or specification updates
    - `test:` Adding or improving test cases
    - `chore:` Maintenance, build setup, or repo chores
    - `refactor:` Code restructuring without behavioral changes
    - `perf:` Performance optimizations

---

## Pull Request Workflow

1. **Branching Model:**
   - `main` is the repository's default integration branch.
   - Create a short-lived working branch from the latest `main` and submit a pull request targeting `main`:
     ```bash
     git checkout main
     git pull origin main
     git checkout -b feat/your-feature-name
     ```
2. **Commit Your Changes:**
   - Keep commits small, atomic, and well-described.
3. **Verify Locally:**
   - Ensure `./wysna test` passes 100%.
   - Ensure `./wysna test --sanitize` passes without memory errors or leaks.
4. **Submit Pull Request:**
   - Open a PR from your branch to `main`.
   - Fill out the [Pull Request Template](.github/pull_request_template.md).
   - Ensure all GitHub Actions CI checks pass.

---

## Adding New Tests

When adding a feature or fixing a bug, include corresponding regression tests in `tests/`:

- `tests/lexer/valid/` & `tests/lexer/invalid/`: Lexer token tests.
- `tests/parser/valid/` & `tests/parser/invalid/`: AST parsing and syntax error tests.
- `tests/types/valid/` & `tests/types/invalid/`: Static type checking and error code tests.
- `tests/runtime/`: End-to-end execution tests (`.wysna` file and expected output in `.expected`).
- `tests/differential/`: Cross-engine differential test programs.

Thank you for helping make Wysna a robust, memory-safe, and performant systems language!
