'use strict';
const {test}=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const path=require('node:path');
const vm=require('node:vm');

test('Thor vet is opt-in per workspace and forwards its compiler flag',()=>{
    const manifest=JSON.parse(fs.readFileSync(path.join(__dirname,'../package.json'),'utf8'));
    const setting=manifest.contributes.configuration.properties['tls.vet'];
    assert.equal(setting.type,'boolean');
    assert.equal(setting.default,false);
    assert.equal(setting.scope,'resource');
    const source=fs.readFileSync(path.join(__dirname,'../extension.js'),'utf8');
    assert.match(source,/config\.get\('vet', false\).*initializationOptions\.checker_args = '--vet'/);
});

test('unverified visibility is explicit, resource scoped, and only enables with vet',()=>{
    const manifest=require('../package.json');
    const setting=manifest.contributes.configuration.properties['tls.unverified'];
    assert.deepEqual(setting.enum,['summary','show','error']);
    assert.equal(setting.default,'summary');
    assert.equal(setting.scope,'resource');
    const source=fs.readFileSync(path.join(__dirname,'../extension.js'),'utf8');
    const fragment=source.slice(source.indexOf("    if (config.get('vet', false))"),source.indexOf("    const compilerPath ="));
    for(const unverified of setting.enum)for(const vet of [false,true])for(const comptime of ['normal','required','aggressive','override']){
        const actual=vm.runInNewContext(`const initializationOptions={};${fragment};initializationOptions`,{
            config:{get:(key,fallback)=>({unverified,vet,comptime})[key]??fallback}
        });
        const expected=[vet?'--vet':'',vet&&unverified!=='summary'?`--unverified=${unverified}`:'',comptime!=='normal'?`--comptime=${comptime}`:''].filter(Boolean).join(' ');
        assert.equal(actual.checker_args,expected||undefined);
    }
});
