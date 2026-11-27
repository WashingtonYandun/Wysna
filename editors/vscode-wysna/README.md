# Wysna Language Support for VS Code / Cursor / Antigravity

Syntax highlighting, comment toggling (`#`), bracket matching, LSP integration (`wysna lsp`), and **Wysna: Show Project Impact** support for **Wysna 1.0**.

## Prerequisites

Install the Wysna compiler CLI in one command:

- **Windows (PowerShell):** `irm https://wysna.wysofia.com/install.ps1 | iex`
- **macOS / Linux:** `curl -fsSL https://wysna.wysofia.com/install.sh | sh`

## Package and Install as `.vsix`

Build a deterministic `.vsix` package without external Node/npm tools:

```bash
python3 tools/package_vscode.py --output dist/wysna-lang-1.0.0.vsix
code --install-extension dist/wysna-lang-1.0.0.vsix
```

Or link directly from a repository checkout:

```bash
ln -sfn "$(pwd)/editors/vscode-wysna" ~/.vscode/extensions/washingtonyandun.wysna-lang-1.0.0
```

Reload the window (`Developer: Reload Window`) to activate `.wysna` support.

## Features

- **LSP Diagnostics & Navigation:** Configure your LSP client to launch `wysna lsp` over stdio (or set `wysna.executablePath` if using a custom binary path).
- **Project Impact Panel:** Open a file inside a `wysna.toml` project, place the cursor on a symbol, and run **Wysna: Show Project Impact** from the Command Palette.
