'use strict';
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');

function findToolchain(workspace, {env = process.env, home = os.homedir(), exists = fs.existsSync} = {}) {
    const candidates = [env.THOR_ROOT, workspace, path.join(home, 'Thor')];
    return candidates.filter(Boolean).map(root => path.resolve(root))
        .find(root => exists(path.join(root, 'thor')) && exists(path.join(root, 'tls')));
}

module.exports = {findToolchain};
