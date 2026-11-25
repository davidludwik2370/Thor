'use strict';
const assert = require('node:assert/strict');
const path = require('node:path');
const test = require('node:test');
const {findToolchain, findCompiler,findRegisteredToolchain,findBundledOdin,installationRecord} = require('../toolchain');

function installed(...roots) {
    return file => roots.some(root => [path.join(root, 'thor'), path.join(root, 'tls')].includes(file));
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
        const files = [path.join(root, compiler), path.join(root, 'tls')];
        assert.equal(findToolchain('/projects/app', {env: {THOR_ROOT: root}, home: '/home/user', exists: file => files.includes(file)}), root);
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
    const options={env:{},home:'/home/user',platform:'linux',exists:installed(root),
        read:file=>{assert.equal(file,path.resolve('/home/user/.config/thor/installation.json'));
            return JSON.stringify({schema:1,managed_by:'thor-install',root});}};
    assert.equal(findRegisteredToolchain(options),root);
    assert.equal(findToolchain('/projects/app',options),root);
    assert.equal(findToolchain('/projects/app',{...options,read:()=>'{bad'}),undefined);
    assert.equal(findToolchain('/projects/app',{...options,read:()=>JSON.stringify({schema:1,root})}),undefined);
});
test('installation records follow XDG, macOS and Windows locations',()=>{
    for (const [platform,env,file] of [['linux',{XDG_CONFIG_HOME:'/config'},'/config/thor/installation.json'],
        ['darwin',{},'/home/user/Library/Application Support/Thor/installation.json'],
        ['win32',{LOCALAPPDATA:'/local'},'/local/Thor/installation.json']]) {
        installationRecord({platform,env,home:'/home/user',read:p=>{assert.equal(p,path.resolve(file));return '{}';}});
    }
});
test('bundled Odin is selected only when the matching executable exists',()=>{
    const root=path.resolve('/data/Thor');
    assert.equal(findBundledOdin(root,{platform:'linux',exists:p=>p===path.join(root,'odin','odin')}),path.join(root,'odin'));
    assert.equal(findBundledOdin(root,{platform:'win32',exists:p=>p===path.join(root,'odin','odin.exe')}),path.join(root,'odin'));
    assert.equal(findBundledOdin(root,{exists:()=>false}),undefined);
});
