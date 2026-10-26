'use strict';
const {test}=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const path=require('node:path');
const os=require('node:os');
const {isBuildOutput}=require('../workspace-files');

test('TLS watches source files while excluding standard Thor build/debug output',()=>{
    for (const file of ['/project/.vscode/bin/generated/main.odin','/project/.thor-build/project/main.odin',
        '/project/.thor-cache/helper.odin','C:\\project\\.vscode\\bin\\generated\\main.odin']) assert.ok(isBuildOutput(file),file);
    assert.equal(isBuildOutput('/project/library/main.odin'),false);
    assert.equal(isBuildOutput('/project/.vscode/config.thor'),false);
});

test('custom output roots and symlinked generated files are excluded by their manifest',()=>{
    const temporary=fs.mkdtempSync(path.join(os.tmpdir(),'thor-watch-'));
    try {
        const generated=path.join(temporary,'custom-output'),nested=path.join(generated,'package');
        fs.mkdirSync(nested,{recursive:true});
        fs.writeFileSync(path.join(generated,'thor-manifest.json'),'{}');
        const file=path.join(nested,'main.odin');fs.writeFileSync(file,'package main');
        assert.ok(isBuildOutput(file));
        const linked=path.join(temporary,'linked.odin');fs.symlinkSync(file,linked);
        assert.ok(isBuildOutput(linked));
    } finally {fs.rmSync(temporary,{recursive:true,force:true});}
});
