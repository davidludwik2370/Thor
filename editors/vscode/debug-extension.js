'use strict';
const vscode = require('vscode');
const {ThorDebugAdapter} = require('./debug-adapter');
const {prepareCodeLLDB} = require('./debug-backend');

function activateDebugging(context) {
    context.subscriptions.push(vscode.debug.registerDebugAdapterDescriptorFactory('thor-lldb', {
        async createDebugAdapterDescriptor(session) {
            const backend = await prepareCodeLLDB(vscode);
            const adapter = new ThorDebugAdapter({backend,
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
