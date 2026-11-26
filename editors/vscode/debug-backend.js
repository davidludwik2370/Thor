'use strict';
const fs = require('fs');
const path = require('path');

// Marketplace CodeLLDB initially contains only JavaScript. Its activation starts
// the upstream platform-package installer asynchronously, so activation alone
// does not mean the native adapter is ready to spawn.
async function prepareCodeLLDB(vscode, options = {}) {
    const platform = options.platform || process.platform;
    const exists = options.exists || fs.existsSync;
    const delay = options.delay || (ms => new Promise(resolve => setTimeout(resolve, ms)));
    const now = options.now || Date.now;
    const timeout = options.timeout ?? 120000;
    const extension = () => vscode.extensions.getExtension('vadimcn.vscode-lldb');
    const ready = backend => backend && exists(path.join(backend.extensionPath, 'platform.ok')) &&
        exists(path.join(backend.extensionPath, 'adapter', platform === 'win32' ? 'codelldb.exe' : 'codelldb'));
    let backend = extension();
    if (!backend) throw new Error('Install or enable the CodeLLDB extension (vadimcn.vscode-lldb) to debug Thor programs.');
    if (ready(backend)) return backend.extensionPath;
    await backend.activate();
    return vscode.window.withProgress({location: vscode.ProgressLocation.Notification,
        title: 'Preparing CodeLLDB for Thor debugging', cancellable: true}, async (_progress, token) => {
        const deadline = now() + timeout;
        do {
            if (token.isCancellationRequested) throw new Error('CodeLLDB preparation cancelled.');
            // Installing the native VSIX can replace the extension directory.
            backend = extension();
            if (ready(backend)) return backend.extensionPath;
            await delay(200);
        } while (now() < deadline);
        throw new Error('CodeLLDB has not finished installing its native debugger. Check the CodeLLDB Output channel for download errors, then reload VS Code and try again.');
    });
}
module.exports = {prepareCodeLLDB};
