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

function generatedFixture(action) {
    const temporary=fs.mkdtempSync(path.join(os.tmpdir(),'thor-watch-'));
    try {
        const generated=path.join(temporary,'custom-output'),nested=path.join(generated,'package');
        fs.mkdirSync(nested,{recursive:true});
        fs.writeFileSync(path.join(generated,'thor-manifest.json'),'{}');
        const file=path.join(nested,'main.odin');fs.writeFileSync(file,'package main');
        action({temporary,generated,file});
    } finally {fs.rmSync(temporary,{recursive:true,force:true});}
}
test('custom output roots are excluded by their manifest',()=>{
    generatedFixture(({file})=>assert.ok(isBuildOutput(file)));
});
test('directory aliases to generated packages are excluded without symlink privileges',()=>{
    generatedFixture(({temporary,generated})=>{
        const linked=path.join(temporary,'linked-output');
        fs.symlinkSync(generated,linked,process.platform==='win32'?'junction':'dir');
        assert.ok(isBuildOutput(path.join(linked,'package/main.odin')));
    });
});
test('file symlinks to generated files are excluded where supported',t=>{
    generatedFixture(({temporary,file})=>{
        const linked=path.join(temporary,'linked.odin');
        try {fs.symlinkSync(file,linked,'file');}
        catch(error) {
            if(process.platform==='win32'&&['EPERM','EACCES'].includes(error.code)) {
                t.skip('This account cannot create file symlinks; directory junction coverage remains enabled.');return;
            }
            throw error;
        }
        assert.ok(isBuildOutput(linked));
    });
});
