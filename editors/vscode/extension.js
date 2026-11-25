'use strict';

const vscode = require('vscode');
const path = require('node:path');
const fs = require('node:fs');
const { LanguageClient } = require('vscode-languageclient/node');
const { isBuildOutput } = require('./workspace-files');
const { findToolchain, findCompiler, findBundledOdin } = require('./toolchain');
const { formatHoverSize } = require('./hover-size');

let client;
let output;
let watcher;
let restarting = Promise.resolve();

function hasThorDocuments() {
    return vscode.workspace.textDocuments.some(document => document.languageId === 'thor'
        && document.uri.scheme === 'file');
}

function sourceWatcher() {
    const raw=vscode.workspace.createFileSystemWatcher('**/*.{thor,odin}');
    const subscriptions=[raw],events={};
    for (const name of ['onDidCreate','onDidChange','onDidDelete']) {
        const emitter=new vscode.EventEmitter();
        subscriptions.push(emitter,raw[name](uri=>{if (!isBuildOutput(uri.fsPath)) emitter.fire(uri);}));
        events[name]=emitter.event;
    }
    return {...events,ignoreCreateEvents:false,ignoreChangeEvents:false,ignoreDeleteEvents:false,
        dispose:()=>subscriptions.forEach(item=>item.dispose())};
}

async function start(context) {
    if (client) {
        const previous = client;
        client = undefined;
        await previous.stop();
    }
    watcher?.dispose();
    watcher = undefined;
    if (!vscode.workspace.isTrusted) return;
    if (!hasThorDocuments()) return;
    const activeUri = vscode.window.activeTextEditor?.document.uri;
    const folder = (activeUri && vscode.workspace.getWorkspaceFolder(activeUri))
        || vscode.workspace.workspaceFolders?.[0];
    const config = vscode.workspace.getConfiguration('tls', folder?.uri);
    if (!folder || !config.get('enabled', true)) return;
    const configured = config.get('server.path', '');
    const toolchain = findToolchain(folder.uri.fsPath, {requireServer:true}) || folder.uri.fsPath;
    const executable = configured
        ? path.resolve(folder.uri.fsPath, configured)
        : path.join(toolchain, 'tls', 'bin', process.platform === 'win32' ? 'tls.exe' : 'tls');
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
        enable_hover_size: config.get('hoverSize', true),
        enable_document_symbols: true,
        enable_references: true,
        enable_completions: true,
        enable_unused_imports_reporting: true,
        enable_unused_imports_on_change: true,
        enable_auto_import: false,
        enable_checker_workspace_diagnostics: false
    };
    if (config.get('vet', false)) initializationOptions.checker_args = '--vet';
    const unverified = config.get('unverified', 'summary');
    if (config.get('vet', false) && unverified !== 'summary') initializationOptions.checker_args =
        [initializationOptions.checker_args, `--unverified=${unverified}`].filter(Boolean).join(' ');
    const comptime = config.get('comptime', 'normal');
    if (comptime !== 'normal') initializationOptions.checker_args =
        [initializationOptions.checker_args, `--comptime=${comptime}`].filter(Boolean).join(' ');
    const compilerPath = config.get('compiler.path', '');
    const compiler = compilerPath ? path.resolve(folder.uri.fsPath, compilerPath)
        : findCompiler(path.resolve(path.dirname(executable), '..', '..'));
    if (compiler && fs.existsSync(compiler)) initializationOptions.thor_command = compiler;
    else output.appendLine(`Thor compiler not found; set tls.compiler.path to enable compiler diagnostics.`);
    const odinRoot = config.get('odinRoot', '') || findBundledOdin(toolchain);
    if (odinRoot) {
        initializationOptions.odin_root_override = path.resolve(folder.uri.fsPath, odinRoot);
        initializationOptions.odin_command = path.join(initializationOptions.odin_root_override,
            process.platform === 'win32' ? 'odin.exe' : 'odin');
    }
    watcher = sourceWatcher();
    const next = new LanguageClient('tls', 'Thor Language Server',
        { command: executable, args: [], options }, {
            documentSelector: [{ scheme: 'file', language: 'thor' }],
            initializationOptions,
            middleware: {
                provideHover: async (document, position, token, next) =>
                    formatHoverSize(await next(document, position, token), vscode)
            },
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
    require('./terminal-environment').registerTerminalEnvironment(context);
    require('./recommended-colors').registerRecommendedColors(context, vscode);
    require('./bracket-refresh').registerBracketRefresh(context, vscode);
    require('./constant-colors').registerConstantColors(context, vscode);
    require('./run-extension').activateRunning(context, vscode);
    require('./debug-extension').activateDebugging(context);
    output = vscode.window.createOutputChannel('Thor Language Server');
    context.subscriptions.push(output);
    const queueStart = onlyWhenAbsent => {
        restarting = restarting.then(() => {
            if (!onlyWhenAbsent || !client) return start(context);
        }).catch(error => output.appendLine(error.stack || String(error)));
        return restarting;
    };
    const restart = () => queueStart(false);
    context.subscriptions.push(vscode.commands.registerCommand('tls.restart', restart));
    context.subscriptions.push(vscode.workspace.onDidGrantWorkspaceTrust(restart));
    context.subscriptions.push(vscode.workspace.onDidOpenTextDocument(document => {
        if (document.languageId === 'thor' && document.uri.scheme === 'file') void queueStart(true);
    }));
    context.subscriptions.push(vscode.workspace.onDidChangeConfiguration(event => {
        if (event.affectsConfiguration('tls')) void restart();
    }));
    return queueStart(true);
}

async function deactivate() {
    await restarting;
    if (client) await client.stop();
    watcher?.dispose();
}

module.exports = { activate, deactivate };
