'use strict';
const path = require('node:path');

async function runProject(vscode) {
    if (!vscode.workspace.isTrusted) throw new Error('Thor execution requires a trusted workspace.');
    let terminal = vscode.window.activeTerminal;
    let cwd;
    if (!terminal || terminal.exitStatus !== undefined) {
        const document = vscode.window.activeTextEditor?.document;
        const folder = (document && vscode.workspace.getWorkspaceFolder(document.uri))
            || vscode.workspace.workspaceFolders?.[0];
        cwd = folder?.uri.fsPath || (document?.uri.scheme === 'file' ? path.dirname(document.uri.fsPath) : undefined);
        if (!cwd) throw new Error('Open a project folder, saved file, or terminal before running Thor.');
    }
    if (!await vscode.workspace.saveAll(false)) throw new Error('Save your changes before running Thor.');
    if (cwd) terminal = vscode.window.createTerminal({name:'Thor', cwd});
    terminal.show();
    terminal.sendText('thor run .', true);
    return terminal;
}

function activateRunning(context, vscode) {
    context.subscriptions.push(vscode.commands.registerCommand('thor.run', () => runProject(vscode)));
}

module.exports = {activateRunning, runProject};
