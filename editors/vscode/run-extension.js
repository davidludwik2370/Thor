'use strict';
const path = require('node:path');
const {findRegisteredToolchain,findCompiler} = require('./toolchain');

function sourceDirectory(vscode) {
    const document = vscode.window.activeTextEditor?.document;
    if (document?.uri.scheme === 'file') return path.dirname(document.uri.fsPath);
    return vscode.workspace.workspaceFolders?.[0]?.uri.fsPath;
}

function invocation(vscode,terminal,compiler='thor') {
    if (compiler === 'thor') return 'thor run .';
    const shell=path.basename(terminal?.creationOptions?.shellPath || vscode.env?.shell || process.env.SHELL || '').toLowerCase();
    if (/^(pwsh|powershell)(\.exe)?$/.test(shell)) return "& '"+compiler.replace(/'/g,"''")+"' run .";
    if (shell === 'cmd.exe') {
        if (/[\r\n%!\"]/.test(compiler)) throw new Error('Use PowerShell to run a Thor installation whose path contains cmd expansion characters.');
        return '"'+compiler+'" run .';
    }
    return "'"+compiler.replace(/'/g,"'\\''")+"' run .";
}

function runCommand(vscode, terminal, cwd, compiler='thor') {
    const run=invocation(vscode,terminal,compiler);
    const current = terminal.shellIntegration?.cwd;
    if (!cwd || current?.scheme === 'file' && path.resolve(current.fsPath) === path.resolve(cwd)) return run;
    const shell = path.basename(terminal.creationOptions?.shellPath || vscode.env?.shell || process.env.SHELL || '').toLowerCase();
    if (/^(pwsh|powershell)(\.exe)?$/.test(shell)) {
        const quoted = "'" + cwd.replace(/'/g, "''") + "'";
        return `& { if ($PWD.Path -ne ${quoted}) { Set-Location -LiteralPath ${quoted} -ErrorAction Stop }; ${run} }`;
    }
    if (shell === 'cmd.exe') {
        // cmd expands percent variables even in quotes. Use a fresh terminal
        // for paths which cannot be represented literally in that shell.
        if (/[\r\n%!"]/.test(cwd)) return undefined;
        return `if /I "%CD%"=="${cwd}" (${run}) else (cd /d "${cwd}" && ${run})`;
    }
    const quoted = "'" + cwd.replace(/'/g, "'\\''") + "'";
    if (shell === 'fish') return `if test "$PWD" != ${quoted}; cd -- ${quoted}; end; and ${run}`;
    return `if [ "$PWD" != ${quoted} ]; then cd -- ${quoted}; fi && ${run}`;
}

async function runProject(vscode) {
    if (!vscode.workspace.isTrusted) throw new Error('Thor execution requires a trusted workspace.');
    let terminal = vscode.window.activeTerminal;
    const cwd = sourceDirectory(vscode);
    const uri=vscode.window.activeTextEditor?.document.uri;
    const configured=vscode.workspace.getConfiguration('tls',uri).get('compiler.path','');
    const root=findRegisteredToolchain();
    const compiler=configured ? path.resolve(vscode.workspace.getWorkspaceFolder(uri)?.uri.fsPath || cwd || '.',configured)
        : (root && findCompiler(root)) || 'thor';
    const live = terminal && terminal.exitStatus === undefined;
    if (!cwd && !live) throw new Error('Open a project folder, saved file, or terminal before running Thor.');
    if (!await vscode.workspace.saveAll(false)) throw new Error('Save your changes before running Thor.');
    let command = live ? runCommand(vscode, terminal, cwd,compiler) : undefined;
    if (command === undefined) {
        terminal = vscode.window.createTerminal({name:'Thor', cwd});
        command = invocation(vscode,terminal,compiler);
    }
    terminal.show();
    terminal.sendText(command, true);
    return terminal;
}

function activateRunning(context, vscode) {
    context.subscriptions.push(vscode.commands.registerCommand('thor.run', () => runProject(vscode)));
}

module.exports = {activateRunning, runProject, sourceDirectory, runCommand,invocation};
