# Security Policy

The Wysna team takes security, memory safety, and compiler correctness seriously. This policy explains how to report vulnerabilities, how we respond, and which versions receive support.

## Supported Versions

| Version | Supported | Security Updates |
| :--- | :---: | :--- |
| **`1.0.x`** | ✅ Yes | Active security fixes on `main` and official Native SDK / Seed installers |
| **`< 1.0.0`** | ❌ No | Historical pre-1.0 milestones; upgrade to `1.0.0` or newer |

## Security Boundaries

- **Deterministic ARC + Copy-on-Write:** Pure value semantics (`struct`, `list`, `dict`), bounds checking, explicit error channels, and runtime quotas (`--max-steps`, `--max-heap-mb`) protect memory integrity within Wysna programs.
- **Process Isolation vs. CLI Capabilities:** ARC, execution quotas, trace replay, and `--sandbox` capability filters do not replace OS-level container or VM sandboxing when executing untrusted third-party code. See [Known limits](docs/KNOWN_LIMITS.md) and [Worker safety](docs/WORKER_SAFETY.md).
- **C FFI Boundary:** External C libraries linked via `--include-dir` / `--link` / `--library` operate outside Wysna's checked memory model and require a trusted FFI boundary.

## Reporting a Vulnerability

If you find a vulnerability in the Wysna compiler, runtime, or tools, **do not report it through a public GitHub issue**. Use one of these responsible disclosure channels:

1. **GitHub Security Advisory (preferred):** create a private report at [github.com/WashingtonYandun/Wysna/security/advisories/new](https://github.com/WashingtonYandun/Wysna/security/advisories/new).
2. **Email:** write to [washington.yandun.morales@gmail.com](mailto:washington.yandun.morales@gmail.com) with the subject `[SECURITY]`.

### Information to Include

- Vulnerability description and, if available, severity assessment (CVSS).
- A minimal Wysna (`.wysna`) example or script that reproduces the issue.
- Affected component, such as the C99 runtime (`runtime/wysna_runtime.h`), HIR lowerer, C generator, parser, or self-hosted compiler.
- Exact environment: operating system, architecture, Wysna version (`./wysna version`), and C toolchain (`clang --version` or `gcc --version`).
- Sanitizer trace when applicable (ASan/UBSan with `./wysna run <file>.wysna`).

## Response Process

1. **Acknowledgment:** we will confirm receipt within **48 hours**.
2. **Assessment and triage:** we will investigate and assess the vulnerability within **5 business days**, validating reproduction and impact.
3. **Fix:** we will develop the fix on a private branch and verify it with AddressSanitizer, UndefinedBehaviorSanitizer, and differential tests.
4. **Release and advisory:** we will publish a corrective version and a coordinated GitHub Security Advisory. We will credit the reporter unless they request anonymity.

## In-Scope Areas

- **C99 runtime memory safety:** use-after-free, double-free, dangling references, and reference-count overflow or underflow in `runtime/wysna_runtime.h`.
- **Type confusion:** bypasses in the static checker (`checker/TypeChecker.py`) or typed HIR that allow unchecked memory access.
- **Compiler code injection:** unsanitized literals or identifiers that escape AST lowering and produce arbitrary C execution.
- **Copy-on-Write invariants:** mutations visible across shared values that violate Wysna value semantics.

## Out of Scope

- Denial of service caused by excessively large files or deeply nested expressions that reach parser recursion limits, unless they bypass AST depth protections.
- Vulnerabilities requiring root/administrator access or manipulation of local binaries outside the Wysna toolchain.
