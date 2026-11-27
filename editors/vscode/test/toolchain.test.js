'use strict';
const assert = require('node:assert/strict');
const path = require('node:path');
const test = require('node:test');
const {findToolchain, findCompiler,findRegisteredToolchain,findBundledOdin,installationRecord} = require('../toolchain');

function installed(...roots) {
    return file => roots.some(root => [path.join(root, 'thor'),path.join(root,'thor.exe'),
        path.join(root, 'tls'),path.join(root,'tls/bin/tls'),path.join(root,'tls/bin/tls.exe')].includes(file));
}

test('ordinary projects use the shared Thor installation', () => {
    const root = path.resolve('/home/user/Thor');
    assert.equal(findToolchain('/projects/app', {env: {}, home: '/home/user', exists: installed(root)}), root);
});
test('explicit installation and development checkouts remain selectable', () => {
    const home = path.resolve('/home/user/Thor'), checkout = path.resolve('/projects/thor'), custom = path.resolve('/tools/thor');
    const exists = installed(home, checkout, custom);
    assert.equal(findToolchain(checkout, {env: {}, home: '/home/user', exists}), checkout);
    assert.equal(findToolchain(checkout, {env: {THOR_ROOT: custom}, home: '/home/user', exists}), custom);
});
test('missing installations are reported rather than invented', () => {
    assert.equal(findToolchain('/projects/app', {env: {}, home: '/home/user', exists: () => false}), undefined);
});

test('native Windows installations need no POSIX launcher', () => {
    const root = path.resolve('/tools/native Thor');
    for (const compiler of ['thor.exe', path.join('rust', 'target', 'release', 'thor-rust.exe'), path.join('bin', 'thor.exe')]) {
        const files = [path.join(root, compiler), path.join(root, 'tls'),path.join(root,'tls/bin/tls.exe')];
        assert.equal(findToolchain('/projects/app', {platform:'win32',env: {THOR_ROOT: root}, home: '/home/user', exists: file => files.includes(file)}), root);
    }
});

test('Windows compiler discovery selects native executables even with a shell launcher present', () => {
    const root = path.resolve('/tools/native Thor');
    for (const name of ['thor.exe', path.join('bin', 'thor.exe'), path.join('rust', 'target', 'release', 'thor-rust.exe')]) {
        const binary = path.join(root, name);
        assert.equal(findCompiler(root, {platform: 'win32', exists: file => [path.join(root, 'thor'), binary].includes(file)}), binary);
    }
    assert.equal(findCompiler(root, {platform: 'win32', exists: file => file === path.join(root, 'thor')}), undefined);
    assert.equal(findCompiler(root, {platform: 'darwin', exists: file => file === path.join(root, 'thor')}), path.join(root, 'thor'));
});

test('installed bundles are discoverable from GUI environments without shell variables',()=>{
    const root=path.resolve('/data/Thor/versions/0.41.6-abcdef');
    const options={env:{},home:path.resolve('/home/user'),platform:'linux',exists:installed(root),
        read:file=>{assert.equal(file,path.resolve('/home/user/.config/thor/installation.json'));
            return JSON.stringify({schema:1,managed_by:'thor-install',root});}};
    assert.equal(findRegisteredToolchain(options),root);
    assert.equal(findToolchain('/projects/app',options),root);
    assert.equal(findToolchain('/projects/app',{...options,read:()=>'{bad'}),undefined);
    assert.equal(findToolchain('/projects/app',{...options,read:()=>JSON.stringify({schema:1,root})}),undefined);
});
test('unbuilt source checkouts do not shadow the installed TLS server',()=>{
    const checkout=path.resolve('/Users/user/Thor'),root=path.resolve('/Users/user/Library/Application Support/Thor/versions/current');
    for(const platform of ['darwin','linux','win32']) {
        const compiler=platform==='win32'?'thor.exe':'thor',server=platform==='win32'?'tls.exe':'tls';
        const files=new Set([path.join(checkout,'thor'),path.join(checkout,'tls'),path.join(checkout,'tls/src'),
            path.join(root,compiler),path.join(root,'tls'),path.join(root,'tls/bin',server)]);
        const options={platform,home:'/Users/user',env:{},requireServer:true,exists:file=>files.has(file),
            read:()=>JSON.stringify({schema:1,managed_by:'thor-install',root})};
        const selected=findToolchain(checkout,options);
        assert.equal(selected,root);
        const withStaleEnvironment=findToolchain(checkout,{...options,env:{THOR_ROOT:checkout}});
        assert.equal(withStaleEnvironment,root);
        files.delete(path.join(root,'tls/bin',server));
        const missing=findRegisteredToolchain(options);
        assert.equal(missing,undefined);
    }
});
test('a registered record without its TLS binary does not hide a built checkout',()=>{
    const checkout=path.resolve('/projects/Thor'),root=path.resolve('/data/Thor');
    const complete=installed(checkout);
    const options={platform:'linux',env:{},home:'/Users/user',requireServer:true,
        exists:file=>complete(file)||file===path.join(root,'thor')||file===path.join(root,'tls'),
        read:()=>JSON.stringify({schema:1,managed_by:'thor-install',root})};
    const registered=findRegisteredToolchain(options),selected=findToolchain(checkout,options);
    assert.equal(registered,undefined);assert.equal(selected,checkout);
});
test('installation records follow XDG, macOS and Windows locations',()=>{
    for (const [platform,env,file] of [['linux',{XDG_CONFIG_HOME:'/config'},'/config/thor/installation.json'],
        ['linux',{XDG_CONFIG_HOME:'relative'},'/home/user/.config/thor/installation.json'],
        ['darwin',{},'/home/user/Library/Application Support/Thor/installation.json'],
        ['win32',{LOCALAPPDATA:'/local'},'/local/Thor/installation.json']]) {
        let requested;
        installationRecord({platform,env,home:'/home/user',read:p=>{requested=p;return '{}';}});
        assert.equal(path.resolve(requested),path.resolve(file));
    }
});
test('bundled Odin is selected only when the matching executable exists',()=>{
    const root=path.resolve('/data/Thor');
    assert.equal(findBundledOdin(root,{platform:'linux',exists:p=>p===path.join(root,'odin','odin')}),path.join(root,'odin'));
    assert.equal(findBundledOdin(root,{platform:'win32',exists:p=>p===path.join(root,'odin','odin.exe')}),path.join(root,'odin'));
    assert.equal(findBundledOdin(root,{exists:()=>false}),undefined);
});
