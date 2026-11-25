'use strict';

const fs = require('node:fs');
const path = require('node:path');
const {installationRecord} = require('./toolchain');

function registerTerminalEnvironment(context, {
    record = installationRecord(), platform = process.platform, exists = fs.existsSync
} = {}) {
    // Only use the user-owned installer record, never a project's compiler or
    // THOR_ROOT override. Keep the stable command directory across upgrades.
    const paths = platform === 'win32' ? path.win32 : path.posix;
    const valid = record?.schema === 1 && record.managed_by === 'thor-install'
        && typeof record.bin === 'string' && paths.isAbsolute(record.bin)
        && (platform === 'win32' ? ['thor.cmd','thor.exe'] : ['thor'])
            .some(name => exists(paths.join(record.bin, name)));
    const collection = context.environmentVariableCollection;
    collection.persistent = true;
    if (!valid) {
        collection.delete('PATH'); // Also remove a cached, uninstalled prefix.
        collection.description = undefined;
        return;
    }
    collection.description = 'Make the installed Thor commands available in terminals.';
    collection.prepend('PATH', record.bin + paths.delimiter, {
        applyAtProcessCreation: true,
        applyAtShellIntegration: true
    });
}

module.exports = {registerTerminalEnvironment};
