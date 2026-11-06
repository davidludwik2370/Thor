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
    require('./project-debug').activateProjectDebugging(context, vscode);
}
module.exports = {activateDebugging};
