'use strict';
const {test} = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const os = require('node:os');
const crypto = require('node:crypto');
const {Framing,DebugMaps,ThorDebugAdapter} = require('../debug-adapter');

test('parallel-map frames hide compiler input mode without hiding user parameter names',()=>{
    const adapter=Object.create(ThorDebugAdapter.prototype);
    adapter.frames=new Map();adapter.sourceAliases=new Map();
    const name='parallel::_pmap_values:proc(jobs:[]int,__thor_pmap_spread:$$false)->(:[]int)';
    const root=path.join(os.tmpdir(),'separate Thor installation','runtime');
    adapter.maps={debugRoots:[root],location:()=>undefined};
    const native=path.join(root,'parallel/pmap.thor');
    const frames=adapter.mapFrames([
        {id:1,name,line:1,source:{path:native}},
        {id:2,name,line:1,source:{path:path.join(os.tmpdir(),'pmap.thor')}}
    ]);
    assert.equal(frames[0].name,'parallel::_pmap_values:proc(jobs:[]int)->(:[]int)');
    assert.equal(frames[1].name,name);
});

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

test('a native adapter exit before connecting settles initialize immediately', {timeout:2000}, async t=>{
    const cp=require('node:child_process');
    const {EventEmitter}=require('node:events');
    const {PassThrough}=require('node:stream');
    t.mock.method(cp,'spawn',()=>{
        const child=new EventEmitter();
        child.stdout=new PassThrough();child.stderr=new PassThrough();
        child.kill=()=>{};
        setImmediate(()=>child.emit('exit',19,null));
        return child;
    });
    const adapter=new ThorDebugAdapter({backend:'/test/codelldb',cwd:os.tmpdir()});
    t.after(()=>adapter.dispose());
    const messages=[];
    adapter.onDidSendMessage(message=>messages.push(message));
    await adapter.handleMessage({seq:1,type:'request',command:'initialize',arguments:{}});
    const response=messages.find(message=>message.type==='response'&&message.request_seq===1);
    assert.equal(response?.success,false,messages);
    assert.match(response.message,/CodeLLDB exited \(code 19\)/);
    assert.equal(adapter.pending.size,0);
});

test('cancelling before the native connection rejects the startup promise', {timeout:2000}, async t=>{
    const cp=require('node:child_process');
    const {EventEmitter}=require('node:events');
    const {PassThrough}=require('node:stream');
    let spawned;
    const ready=new Promise(resolve=>spawned=resolve);
    t.mock.method(cp,'spawn',()=>{
        const child=new EventEmitter();
        child.stdout=new PassThrough();child.stderr=new PassThrough();child.kill=()=>{};
        spawned();
        return child;
    });
    const adapter=new ThorDebugAdapter({backend:'/test/codelldb',cwd:os.tmpdir()});
    t.after(()=>adapter.dispose());
    const starting=adapter.start();
    const rejected=assert.rejects(starting,/Thor debug session closed/);
    await ready;
    await adapter.dispose();
    await rejected;
    assert.equal(adapter.rejectStarting,undefined);
});

test('a native spawn error fails initialize with its original message', {timeout:2000}, async t=>{
    const cp=require('node:child_process');
    const {EventEmitter}=require('node:events');
    const {PassThrough}=require('node:stream');
    t.mock.method(cp,'spawn',()=>{
        const child=new EventEmitter();
        child.stdout=new PassThrough();child.stderr=new PassThrough();child.kill=()=>{};
        setImmediate(()=>child.emit('error',new Error('test adapter unavailable')));
        return child;
    });
    const adapter=new ThorDebugAdapter({backend:'/test/codelldb',cwd:os.tmpdir()});
    t.after(()=>adapter.dispose());
    const starting=adapter.start();
    await assert.rejects(starting,/test adapter unavailable/);
});

test('disconnect during native startup succeeds and settles initialize', {timeout:2000}, async t=>{
    const cp=require('node:child_process');
    const {EventEmitter}=require('node:events');
    const {PassThrough}=require('node:stream');
    let spawned;
    const ready=new Promise(resolve=>spawned=resolve);
    t.mock.method(cp,'spawn',()=>{
        const child=new EventEmitter();
        child.stdout=new PassThrough();child.stderr=new PassThrough();child.kill=()=>{};
        spawned();
        return child;
    });
    const adapter=new ThorDebugAdapter({backend:'/test/codelldb',cwd:os.tmpdir()});
    t.after(()=>adapter.dispose());
    const messages=[];
    adapter.onDidSendMessage(message=>messages.push(message));
    const initialize=adapter.handleMessage({seq:1,type:'request',command:'initialize',arguments:{}});
    await ready;
    await adapter.handleMessage({seq:2,type:'request',command:'disconnect',arguments:{}});
    await initialize;
    assert.equal(messages.find(message=>message.type==='response'&&message.request_seq===2)?.success,true,messages);
    const response=messages.find(message=>message.type==='response'&&message.request_seq===1);
    assert.equal(response?.success,false,messages);
    assert.match(response.message,/Thor debug session closed/);
});
