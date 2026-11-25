'use strict';
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');

function installationRecord({env = process.env, home = os.homedir(), platform = process.platform,
    read = file => fs.readFileSync(file, 'utf8')} = {}) {
    const file = platform === 'win32'
        ? path.join(env.LOCALAPPDATA || path.join(home, 'AppData', 'Local'), 'Thor', 'installation.json')
        : platform === 'darwin' ? path.join(home, 'Library', 'Application Support', 'Thor', 'installation.json')
        : path.join(env.XDG_CONFIG_HOME || path.join(home, '.config'), 'thor', 'installation.json');
    try {
        const record = JSON.parse(read(file));
        return record.schema === 1 && record.managed_by === 'thor-install' && typeof record.root === 'string'
            && path.isAbsolute(record.root) ? record : undefined;
    } catch { return undefined; }
}

function findRegisteredToolchain(options = {}) {
    const record = installationRecord(options), exists = options.exists || fs.existsSync;
    return record && findCompiler(record.root, options)
        && (options.requireServer ? findServer(record.root, options) : exists(path.join(record.root,'tls'))) ? record.root : undefined;
}

function findServer(root, {platform = process.platform, exists = fs.existsSync} = {}) {
    const binary = path.join(root, 'tls', 'bin', platform === 'win32' ? 'tls.exe' : 'tls');
    return exists(binary) ? binary : undefined;
}

function findToolchain(workspace, options = {}) {
    const {env = process.env, home = os.homedir(), exists = fs.existsSync, requireServer = false} = options;
    const candidates = [env.THOR_ROOT, workspace, findRegisteredToolchain(options), path.join(home, 'Thor')];
    return candidates.filter(Boolean).map(root => path.resolve(root))
        .find(root => findCompiler(root, options)
            && (requireServer ? findServer(root, options) : exists(path.join(root,'tls'))));
}

function findCompiler(root, {platform = process.platform, exists = fs.existsSync} = {}) {
    const names = platform === 'win32'
        ? ['thor.exe', path.join('bin', 'thor.exe'), path.join('rust', 'target', 'release', 'thor-rust.exe')]
        : ['thor'];
    return names.map(name => path.join(root, name)).find(exists);
}

function findBundledOdin(root, {platform = process.platform, exists = fs.existsSync} = {}) {
    if (!root) return undefined;
    const directory = path.join(root, 'odin');
    return exists(path.join(directory, platform === 'win32' ? 'odin.exe' : 'odin')) ? directory : undefined;
}

module.exports = {findToolchain, findCompiler, findBundledOdin, findRegisteredToolchain, installationRecord};
