'use strict';
const fs = require('node:fs');
const path = require('node:path');
const {findToolchain} = require('./toolchain');

async function runProject(vscode, {toolchain = findToolchain, exists = fs.existsSync, platform = process.platform} = {}) {
    if (!vscode.workspace.isTrusted) throw new Error('Thor execution requires a trusted workspace.');
    const document = vscode.window.activeTextEditor?.document;
    const folder = (document && vscode.workspace.getWorkspaceFolder(document.uri))
        || vscode.workspace.workspaceFolders?.[0];
    if (!folder && (!document || document.uri.scheme !== 'file' || document.languageId !== 'thor')) {
        throw new Error('Open a Thor project folder or save a .thor file before running.');
    }
    if (!await vscode.workspace.saveAll(false)) throw new Error('Save your changes before running Thor.');
    if (folder) {
        const tasks = await vscode.tasks.fetchTasks();
        const configured = tasks.find(task => task.name === 'run-thor' && task.scope?.uri?.toString() === folder.uri.toString());
        if (configured) return vscode.tasks.executeTask(configured);
    }
    const cwd = folder?.uri.fsPath || path.dirname(document.uri.fsPath);
    const config = vscode.workspace.getConfiguration('tls', folder?.uri || document.uri);
    const explicit = config.get('compiler.path', '');
    const server = config.get('server.path', '');
    const root = toolchain(cwd);
    const compiler = explicit ? path.resolve(cwd, explicit)
        : server ? path.resolve(cwd, path.dirname(server), '..', '..', 'thor')
        : root ? path.join(root, 'thor') : undefined;
    if (!compiler || !exists(compiler)) throw new Error('Thor compiler not found. Set tls.compiler.path or install Thor in ~/Thor.');
    const args = ['run', folder ? '.' : document.uri.fsPath];
    if (!folder) {
        // VS Code does not execute tasks in an empty window. A real shell keeps
        // stdout visible after exit and supplies a terminal for interactive input.
        // Select the shell explicitly so every argument has known quoting rules.
        const windows = platform === 'win32';
        const quote = value => windows ? `'${value.replace(/'/g,"''")}'` : `'${value.replace(/'/g,"'\\''")}'`;
        const terminal = vscode.window.createTerminal({name:'Thor: '+path.basename(document.uri.fsPath), cwd,
            shellPath:windows ? 'powershell.exe' : '/bin/sh', shellArgs:windows ? ['-NoLogo','-NoProfile'] : [], isTransient:true});
        const command = [compiler,...args].map(quote).join(' ');
        terminal.show();
        terminal.sendText(windows ? `Set-Location -LiteralPath ${quote(cwd)} -ErrorAction Stop; & ${command}`
                                  : `cd ${quote(cwd)} && ${command}`,true);
        return terminal;
    }
    const task = new vscode.Task({type: 'process', command: compiler, args}, folder,
        'run-thor', 'Thor', new vscode.ProcessExecution(compiler, args, {cwd}), []);
    task.presentationOptions = {reveal: vscode.TaskRevealKind.Always, panel: vscode.TaskPanelKind.Shared,
        clear: true, focus: true, showReuseMessage: false};
    task.runOptions = {instanceLimit: 1};
    return vscode.tasks.executeTask(task);
}

function activateRunning(context, vscode) {
    context.subscriptions.push(vscode.commands.registerCommand('thor.run', () => runProject(vscode)));
}

module.exports = {activateRunning, runProject};
