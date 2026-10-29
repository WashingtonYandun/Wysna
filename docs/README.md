# Documentation

Start with [product scope and maintenance boundaries](SCOPE.md) to distinguish the
recommended installation, supported workflows, experiments and security limits.

[The language specification](../SPEC.md) defines Wysna syntax and semantics. The documents below describe compiler and runtime contracts.

## Compiler

- [Compiler architecture](COMPILER_ARCHITECTURE.md): session ownership, semantic rule domains, tool boundaries, and runtime headers.
- [Type system](TYPE_SYSTEM_DESIGN.md): canonical types, assignability, variance, and generic substitution.
- [HIR](HIR_DESIGN.md): typed representation, verification boundary, and default C99 backend.
- [MIR](MIR_DESIGN.md): verified intermediate representation and current implementation boundary.
- [Ownership and lifetimes](OWNERSHIP_LIFETIME.md): ARC, CoW, weak references, and cleanup.
- [Effect and capability reports](EFFECTS.md): read-only `wysna explain`, model, trust, schema, and limits.
- [Engine support](ENGINE_PARITY_MATRIX.md): tested fixture coverage across Wysna Seed and Wysna Compiler.
- [Known limits](KNOWN_LIMITS.md): supported boundaries and deferred language decisions.

## Tooling and distribution

- [AI agent workflow](AI_AGENT_GUIDE.md): deterministic project workflow, target discovery, and diagnostics.
- [Projects](PROJECTS.md): manifests, local dependencies, lockfiles, targets, and user tests.
- [Seed distribution](SEED_DISTRIBUTION.md): relocatable Seed installation with private CPython and Zig.
- [Native SDK](NATIVE_SDK.md): Python-free native compiler SDK, commands, and provenance.
- [Experimental LLVM](LLVM_EXPERIMENTAL.md): opt-in Seed LLVM backend and qualification.
- [Seed LLVM SDK](LLVM_SDK.md): relocatable Seed installation with pinned LLVM tools.
- [Service foundations](SERVICE_FOUNDATIONS.md): bounded loopback service contracts and limits.

## Runtime and libraries

- [C interoperability](FFI_VALIDATION.md): scalar ABI, opaque handles, and SQLite pilot.
- [Standard library](STDLIB.md): packages and their current contracts.
