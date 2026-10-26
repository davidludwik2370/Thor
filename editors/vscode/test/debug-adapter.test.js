'use strict';
const {test} = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const os = require('node:os');
const crypto = require('node:crypto');
const {Framing,DebugMaps} = require('../debug-adapter');

test('DAP framing preserves fragmented UTF-8 and coalesced messages',()=>{
    const received=[];
    const framing=new Framing(message=>received.push(message));
    const first={seq:1,type:'event',event:'output',body:{output:'é😀界'}};
    const second={seq:2,type:'response',request_seq:4,success:true};
    const bytes=Buffer.concat([Framing.encode(first),Framing.encode(second)]);
    for (const byte of bytes) framing.feed(Buffer.from([byte]));
    assert.deepEqual(received,[first,second]);
});

test('backend framing requires its private session token and rejects invalid lengths',()=>{
    const message=Framing.encode({seq:1,type:'event',event:'initialized'});
    assert.throws(()=>new Framing(()=>{},'private').feed(message),/authentication/);
    let accepted=false;
    new Framing(()=>accepted=true,'private').feed(Buffer.concat([Buffer.from('Auth-Token: private\r\n'),message]));
    assert.ok(accepted);
    assert.throws(()=>new Framing(()=>{}).feed(Buffer.from('Content-Length: -1\r\n\r\n')),/length/);
    assert.throws(()=>new Framing(()=>{}).feed(Buffer.from('Content-Length: 999999999\r\n\r\n')),/length/);
});

test('debug maps require a matching debug build and reject stale original text',()=>{
    const directory=fs.mkdtempSync(path.join(os.tmpdir(),'thor-debug-map-'));
    try {
        const original=path.join(directory,'main.thor'),generated=path.join(directory,'main.odin');
        const text='package main\nmain::proc(){_="😀"}\n',length=Buffer.byteLength(text);
        fs.writeFileSync(original,text);fs.writeFileSync(generated,text);
        fs.writeFileSync(generated+'.map.json',JSON.stringify({version:1,generated,sources:{[original]:text},
            generated_sha256:crypto.createHash('sha256').update(text).digest('hex'),
            segments:[[0,length,original,0,length,'source',true]],regions:[]}));
        const manifest=path.join(directory,'thor-manifest.json');
        const executable=path.join(directory,'program');fs.writeFileSync(executable,'native executable');
        const data={debug_map_version:1,odin_flags:['-debug','-o:none'],generated:['main.odin','main.odin.map.json'],
            debug_program:{path:executable,sha256:crypto.createHash('sha256').update('native executable').digest('hex')}};
        fs.writeFileSync(manifest,JSON.stringify(data));
        const maps=new DebugMaps(manifest);
        assert.equal(maps.originals.size,1);
        maps.verifyExecutable(executable);
        fs.writeFileSync(executable,'another build');
        assert.throws(()=>maps.verifyExecutable(executable),/does not match/);
        fs.writeFileSync(original,text+'// edited\n');
        assert.throws(()=>new DebugMaps(manifest),/Source changed/);
        fs.writeFileSync(original,text);
        fs.writeFileSync(generated,text+'// changed generated file\n');
        assert.throws(()=>new DebugMaps(manifest),/Generated source changed/);
        fs.writeFileSync(manifest,JSON.stringify({...data,odin_flags:[]}));
        assert.throws(()=>new DebugMaps(manifest),/Rebuild/);
    } finally {fs.rmSync(directory,{recursive:true,force:true});}
});
