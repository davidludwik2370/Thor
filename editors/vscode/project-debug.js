'use strict';
const path = require('node:path');
const {sourceDirectory} = require('./run-extension');
const {findToolchain, findCompiler, findBundledOdin} = require('./toolchain');

const taskType = 'thor-debug-build';

function buildTask(vscode, definition, folder) {
    const cwd = definition.directory;
    const binary = path.join(cwd, '.vscode', 'bin', process.platform === 'win32' ? 'debug.exe' : 'debug');
    const generated = path.join(cwd, '.vscode', 'bin', 'generated');
    const config = vscode.workspace.getConfiguration('tls', folder.uri);
    const configured = config.get('compiler.path', '');
    const root = findToolchain(cwd) || findToolchain(folder.uri.fsPath);
    const command = configured ? path.resolve(folder.uri.fsPath, configured) : (root && findCompiler(root)) || 'thor';
    const args = ['build', '.', '-debug', '-o:none', '--output-dir', generated, '-out:' + binary];
    const odinRoot = config.get('odinRoot', '') || findBundledOdin(root);
    if (odinRoot) args.push('--odin', path.join(odinRoot, process.platform === 'win32' ? 'odin.exe' : 'odin'));
    const task = new vscode.Task(definition, folder, 'Build Thor: ' + cwd, 'Thor',
        new vscode.ProcessExecution(command, args, {cwd}), []);
    task.group = vscode.TaskGroup.Build;
    return task;
}

function activateProjectDebugging(context, vscode) {
    const tasks = new Map();
    context.subscriptions.push(vscode.tasks.registerTaskProvider(taskType, {
        provideTasks: () => [...tasks.values()],
        resolveTask: task => task.definition.type === taskType && typeof task.definition.directory === 'string'
            && task.scope?.uri ? buildTask(vscode, task.definition, task.scope) : undefined
    }));
    context.subscriptions.push(vscode.commands.registerCommand('thor.debug', async () => {
        if (!vscode.workspace.isTrusted) throw new Error('Thor debugging requires a trusted workspace.');
        const activeUri = vscode.window.activeTextEditor?.document.uri;
        const folder = (activeUri && vscode.workspace.getWorkspaceFolder(activeUri))
            || vscode.workspace.workspaceFolders?.[0];
        if (!folder) throw new Error('Open a Thor project folder first.');
        if (!await vscode.workspace.saveAll(false)) throw new Error('Save your changes before debugging Thor.');
        const cwd = sourceDirectory(vscode) || folder.uri.fsPath;
        const configurations = vscode.workspace.getConfiguration('launch', folder.uri).get('configurations', []);
        const configured = configurations.find(config => config.type === 'thor-lldb');
        // Existing project-root configurations retain their custom build flags,
        // arguments and environment. Nested projects get matching local output.
        if (configured && path.resolve(cwd) === path.resolve(folder.uri.fsPath)) {
            return vscode.debug.startDebugging(folder, configured.name);
        }
        const definition = {type: taskType, directory: cwd};
        const task = buildTask(vscode, definition, folder);
        tasks.set(cwd, task);
        return vscode.debug.startDebugging(folder, {
            ...configured, name: 'Debug Thor: ' + path.basename(cwd), type: 'thor-lldb', request: 'launch',
            cwd, program: path.join(cwd, '.vscode', 'bin', process.platform === 'win32' ? 'debug.exe' : 'debug'),
            manifest: path.join(cwd, '.vscode', 'bin', 'generated', 'thor-manifest.json'),
            preLaunchTask: definition
        });
    }));
}

module.exports = {activateProjectDebugging, buildTask, taskType};
