'use strict';
const {test} = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const os = require('node:os');
const crypto = require('node:crypto');
const {Framing,DebugMaps,ThorDebugAdapter} = require('../debug-adapter');

test('statement stepping leaves source-less native routines and instruction stepping remains visible',async()=>{
    for (const granularity of ['statement','instruction']) {
        const adapter=Object.create(ThorDebugAdapter.prototype);
        adapter.step={command:'stepIn',args:{granularity},count:0,
            start:{source:{path:'/project/main.thor'},line:7}};
        adapter.maps={debugRoots:[]};
        adapter.references=new Map();
        adapter.frames=new Map([[1,{point:{internal:false},generated:false}]]);
        adapter.mapFrames=frames=>frames;
        const calls=[],events=[];
        adapter.request=async(command)=>{
            calls.push(command);
            return {stackFrames:[{id:1,line:4,name:'__memset_zva64',
                source:{name:'@__memset_zva64',sourceReference:1003}}]};
        };
        adapter.emit=message=>events.push(message);
        const message={body:{threadId:1,reason:'step'}};
        await adapter.stopped(message);
        assert.deepEqual(calls,granularity==='statement'?['stackTrace','stepOut']:['stackTrace']);
        assert.equal(events.length,granularity==='statement'?0:1);
    }
});

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
