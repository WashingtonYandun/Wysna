# Wysna Language Support for VS Code / Cursor / Antigravity

Provides syntax highlighting, comment toggling (`#`), and bracket auto-closing for `.wysna` source files according to `SPEC.md v0.1`. The compiler's stdio LSP also provides diagnostics, completion, definition, hover, and document formatting when connected through an LSP client.

The extension currently contributes syntax support only. To use compiler language features, configure an LSP client to launch `wysna lsp` (or `python -m main lsp` from a source checkout) over stdio.

## Install in 1 Command

From the repository root:

```bash
# VS Code
ln -sfn "$(pwd)/editors/vscode-wysna" ~/.vscode/extensions/washingtonyandun.wysna-lang-0.1.0

# Cursor
ln -sfn "$(pwd)/editors/vscode-wysna" ~/.cursor/extensions/washingtonyandun.wysna-lang-0.1.0

# Antigravity
ln -sfn "$(pwd)/editors/vscode-wysna" ~/.antigravity/extensions/washingtonyandun.wysna-lang-0.1.0
```

Reload the window (`Cmd+Shift+P` -> `Developer: Reload Window`) to activate `.wysna` syntax highlighting.

## Consultar el impacto de un cambio

Abre un archivo Wysna dentro de un proyecto con `wysna.toml`. Coloca el
cursor sobre un símbolo y ejecuta **Wysna: Show Project Impact** desde la
paleta de comandos. El panel **Wysna Project Impact** muestra los destinos
y las pruebas, con las relaciones que justifican cada resultado.

La extensión usa el comando `wysna` del PATH. Si tienes varias instalaciones,
indica el ejecutable en `wysna.executablePath`.
