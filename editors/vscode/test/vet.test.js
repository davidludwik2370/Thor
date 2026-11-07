'use strict';
const {test}=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const path=require('node:path');

test('Thor vet is opt-in per workspace and forwards its compiler flag',()=>{
    const manifest=JSON.parse(fs.readFileSync(path.join(__dirname,'../package.json'),'utf8'));
    const setting=manifest.contributes.configuration.properties['tls.vet'];
    assert.equal(setting.type,'boolean');
    assert.equal(setting.default,false);
    assert.equal(setting.scope,'resource');
    const source=fs.readFileSync(path.join(__dirname,'../extension.js'),'utf8');
    assert.match(source,/config\.get\('vet', false\).*initializationOptions\.checker_args = '--vet'/);
});
