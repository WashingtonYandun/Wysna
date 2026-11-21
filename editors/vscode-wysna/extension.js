const { execFile } = require("node:child_process");
const path = require("node:path");
const { promisify } = require("node:util");
const vscode = require("vscode");

const execute = promisify(execFile);

async function findManifest(document) {
    if (document.uri.scheme !== "file") {
        return undefined;
    }
    const workspace = vscode.workspace.getWorkspaceFolder(document.uri);
    let directory = path.dirname(document.uri.fsPath);
    const boundary = workspace?.uri.fsPath ?? directory;
    while (true) {
        const manifest = vscode.Uri.file(path.join(directory, "wysna.toml"));
        try {
            const stat = await vscode.workspace.fs.stat(manifest);
            if (stat.type & vscode.FileType.File) {
                return manifest.fsPath;
            }
        } catch (error) {
            if (!(error instanceof vscode.FileSystemError) || error.code !== "FileNotFound") {
                throw error;
            }
        }
        const parent = path.dirname(directory);
        if (directory === boundary || parent === directory) {
            return undefined;
        }
        directory = parent;
    }
}

function activate(context) {
    const output = vscode.window.createOutputChannel("Wysna Project Impact");
    context.subscriptions.push(output, vscode.commands.registerCommand("wysna.projectImpact", async () => {
        if (!vscode.workspace.isTrusted) {
            vscode.window.showInformationMessage("Project impact requires a trusted workspace.");
            return;
        }
        const editor = vscode.window.activeTextEditor;
        if (!editor || editor.document.languageId !== "wysna") {
            return;
        }
        try {
            const manifest = await findManifest(editor.document);
            if (!manifest) {
                vscode.window.showInformationMessage("No wysna.toml found for the current document.");
                return;
            }
            const range = editor.selection.isEmpty
                ? editor.document.getWordRangeAtPosition(editor.selection.active) : editor.selection;
            const symbol = await vscode.window.showInputBox({
                title: "Wysna Project Impact",
                prompt: "Enter a symbol name or qualified symbol ID",
                value: range ? editor.document.getText(range) : "",
                validateInput: value => value.trim() ? null : "Enter a symbol name.",
            });
            if (!symbol) {
                return;
            }
            const executable = vscode.workspace.getConfiguration("wysna").get("executablePath")
                || (process.platform === "win32" ? "wysna.exe" : "wysna");
            const { stdout } = await execute(executable, [
                "graph", "--manifest", manifest, "--symbol", symbol.trim(),
                "--rel", "impact", "--output-format=json-v1",
            ], { cwd: path.dirname(manifest), timeout: 30000, maxBuffer: 4 * 1024 * 1024 });
            const envelope = JSON.parse(stdout);
            if (envelope.result?.schema_version !== "wysna.project-impact/1") {
                throw new Error("The installed Wysna compiler did not return a project impact report.");
            }

            output.clear();
            output.appendLine(envelope.result.symbol.name);
            for (const entry of envelope.result.entries) {
                const status = entry.impact_status?.replaceAll("-", " ") ?? (entry.affected ? "affected" : "no static path");
                output.appendLine(`${entry.kind} ${entry.name} (${entry.entry}) — ${status}`);
                for (const evidence of entry.paths) {
                    const chain = evidence.chain.map(edge => ` --${edge.kind}--> ${edge.to}`).join("");
                    output.appendLine(`  ${evidence.entry_symbol}${chain}`);
                }
                if (entry.unanalyzed_boundaries.length) {
                    output.appendLine(`  External boundaries: ${entry.unanalyzed_boundaries.join(", ")}`);
                }
            }
            output.show(true);
        } catch (error) {
            let message = error.message;
            if (error.stdout) {
                try {
                    message = JSON.parse(error.stdout).diagnostics?.[0]?.message || message;
                } catch {
                    // Keep the process error when the compiler did not emit JSON.
                }
            }
            vscode.window.showErrorMessage(`Wysna project impact: ${message}`);
        }
    }));
}

module.exports = { activate };
