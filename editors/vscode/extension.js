'use strict';

const vscode = require('vscode');
const path = require('node:path');
const fs = require('node:fs');
const { LanguageClient } = require('vscode-languageclient/node');

let client;
let output;
let watcher;
let restarting = Promise.resolve();

async function start(context) {
    if (client) {
        const previous = client;
        client = undefined;
        await previous.stop();
    }
    watcher?.dispose();
    watcher = undefined;
    if (!vscode.workspace.isTrusted) return;
    const folder = vscode.workspace.getWorkspaceFolder(vscode.window.activeTextEditor?.document.uri)
        || vscode.workspace.workspaceFolders?.[0];
    const config = vscode.workspace.getConfiguration('tls', folder?.uri);
    if (!folder || !config.get('enabled', true)) return;
    const configured = config.get('server.path', '');
    const executable = configured
        ? path.resolve(folder.uri.fsPath, configured)
        : path.join(folder.uri.fsPath, 'tls', 'bin', process.platform === 'win32' ? 'tls.exe' : 'tls');
    if (!fs.existsSync(executable)) {
        output.appendLine(`TLS not found at ${executable}. Syntax highlighting is available. Build TLS with python3 tools/build_tls.py, or set tls.server.path.`);
        return;
    }
    const builtin = path.resolve(path.dirname(executable), '..', 'builtin');
    const options = { env: { ...process.env, OLS_BUILTIN_FOLDER: builtin }, cwd: folder.uri.fsPath };
    const initializationOptions = {
        enable_format: false,
        enable_parser_errors: true,
        enable_semantic_tokens: true,
        enable_hover: true,
        enable_document_symbols: true,
        enable_references: true,
        enable_completions: true,
        enable_unused_imports_reporting: false,
        enable_auto_import: false,
        enable_checker_workspace_diagnostics: false
    };
    const compilerPath = config.get('compiler.path', '');
    const compiler = compilerPath ? path.resolve(folder.uri.fsPath, compilerPath)
        : path.resolve(path.dirname(executable), '..', '..', 'thor');
    if (fs.existsSync(compiler)) initializationOptions.thor_command = compiler;
    else output.appendLine(`Thor compiler not found at ${compiler}; set tls.compiler.path to enable compiler diagnostics.`);
    const odinRoot = config.get('odinRoot', '');
    if (odinRoot) {
        initializationOptions.odin_root_override = path.resolve(folder.uri.fsPath, odinRoot);
        initializationOptions.odin_command = path.join(initializationOptions.odin_root_override,
            process.platform === 'win32' ? 'odin.exe' : 'odin');
    }
    watcher = vscode.workspace.createFileSystemWatcher('**/*.{thor,odin}');
    const next = new LanguageClient('tls', 'Thor Language Server',
        { command: executable, args: [], options }, {
            documentSelector: [{ scheme: 'file', language: 'thor' }],
            initializationOptions,
            outputChannel: output,
            traceOutputChannel: output,
            synchronize: { fileEvents: watcher }
        });
    client = next;
    try {
        await next.start();
    } catch (error) {
        if (client === next) client = undefined;
        output.appendLine(`TLS failed to start: ${error.message}`);
        void vscode.window.showErrorMessage('Thor: TLS failed to start. See the Thor Language Server output channel.');
    }
}

function activate(context) {
    output = vscode.window.createOutputChannel('Thor Language Server');
    context.subscriptions.push(output);
    const restart = () => {
        restarting = restarting.then(() => start(context)).catch(error => output.appendLine(error.stack || String(error)));
        return restarting;
    };
    context.subscriptions.push(vscode.commands.registerCommand('tls.restart', restart));
    context.subscriptions.push(vscode.workspace.onDidGrantWorkspaceTrust(restart));
    context.subscriptions.push(vscode.workspace.onDidChangeConfiguration(event => {
        if (event.affectsConfiguration('tls')) void restart();
    }));
    return restart();
}

async function deactivate() {
    await restarting;
    if (client) await client.stop();
    watcher?.dispose();
}

module.exports = { activate, deactivate };
