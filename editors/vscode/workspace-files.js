'use strict';
const fs=require('node:fs');
const path=require('node:path');

function isBuildOutput(file) {
    if (/(?:^|[/\\])(?:\.thor-build|\.thor-cache)(?:[/\\]|$)|(?:^|[/\\])\.vscode[/\\]bin(?:[/\\]|$)/.test(file)) return true;
    try {file=fs.realpathSync(file);} catch {}
    for (let directory=path.dirname(file);;) {
        if (fs.existsSync(path.join(directory,'thor-manifest.json'))) return true;
        const parent=path.dirname(directory);
        if (parent===directory) return false;
        directory=parent;
    }
}
module.exports={isBuildOutput};
