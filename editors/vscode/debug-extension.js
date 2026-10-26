'use strict';
const vscode = require('vscode');
const {ThorDebugAdapter} = require('./debug-adapter');

function activateDebugging(context) {
    context.subscriptions.push(vscode.debug.registerDebugAdapterDescriptorFactory('thor-lldb', {
        createDebugAdapterDescriptor(session) {
            const backend = vscode.extensions.getExtension('vadimcn.vscode-lldb');
            if (!backend) throw new Error('Install or enable the CodeLLDB extension (vadimcn.vscode-lldb) to debug Thor programs.');
            const adapter = new ThorDebugAdapter({backend: backend.extensionPath,
                library: vscode.workspace.getConfiguration('lldb',session.workspaceFolder?.uri).get('library'),
                cwd: session.configuration.cwd || session.workspaceFolder?.uri.fsPath});
            return new vscode.DebugAdapterInlineImplementation(adapter);
        }
    }));
    context.subscriptions.push(vscode.debug.registerDebugConfigurationProvider('thor-lldb', {
        resolveDebugConfiguration(folder, config) {
            if (!vscode.workspace.isTrusted) throw new Error('Thor debugging requires a trusted workspace.');
            if (!folder) throw new Error('Open the Thor project folder before starting the debugger.');
            if (!config.type) {
                Object.assign(config, {name: 'Debug Thor (LLDB)', type: 'thor-lldb', request: 'launch',
                    program: '${workspaceFolder}/.vscode/bin/debug', cwd: '${workspaceFolder}',
                    manifest: '${workspaceFolder}/.vscode/bin/generated/thor-manifest.json', preLaunchTask: 'build-thor-debug'});
            }
            return config;
        }
    }));
    context.subscriptions.push(vscode.commands.registerCommand('thor.debug', async () => {
        const activeUri = vscode.window.activeTextEditor?.document.uri;
        const folder = (activeUri && vscode.workspace.getWorkspaceFolder(activeUri))
            || vscode.workspace.workspaceFolders?.[0];
        if (!folder) throw new Error('Open a Thor project folder first.');
        await vscode.workspace.saveAll(false);
        const configurations = vscode.workspace.getConfiguration('launch',folder.uri).get('configurations',[]);
        const config = configurations.find(config => config.type === 'thor-lldb');
        if (!config) {
            void vscode.window.showErrorMessage('This folder has no Thor debug configuration. Create a project with mkthor, or add a thor-lldb launch configuration.');
            return;
        }
        return vscode.debug.startDebugging(folder,config.name);
    }));
}
module.exports = {activateDebugging};
