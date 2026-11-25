'use strict';
const assert = require('node:assert/strict');
const test = require('node:test');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const {registerTerminalEnvironment} = require('../terminal-environment');

function fixture() {
    const values = new Map(), calls = [];
    const context = {environmentVariableCollection: {
        persistent: false,
        prepend: (variable,value,options) => { values.set(variable,{value,options}); calls.push(variable); },
        delete: variable => values.delete(variable)
    }};
    return {context,values,calls};
}
const installed = bin => ({schema:1,managed_by:'thor-install',root:'/tools/versions/1',bin});

test('registered commands are added without changing shell files or inherited environment',()=>{
    const {context,values} = fixture();
    const before = process.env.PATH;
    registerTerminalEnvironment(context,{record:installed('/tools/Thor café/bin'),platform:'linux',
        exists:file=>file==='/tools/Thor café/bin/thor'});
    assert.deepEqual(values.get('PATH'),{value:'/tools/Thor café/bin:',
        options:{applyAtProcessCreation:true,applyAtShellIntegration:true}});
    assert.equal(process.env.PATH,before);
    assert.equal(context.environmentVariableCollection.persistent,true);
    assert.match(context.environmentVariableCollection.description,/Thor/);
});

test('Windows terminal PATH uses native separators and accepts installed command wrappers',()=>{
    for (const command of ['thor.cmd','thor.exe']) {
        const {context,values} = fixture();
        registerTerminalEnvironment(context,{record:installed('C:\\Users\\David\\Thor tools\\bin'),platform:'win32',
            exists:file=>file===`C:\\Users\\David\\Thor tools\\bin\\${command}`});
        assert.equal(values.get('PATH').value,'C:\\Users\\David\\Thor tools\\bin;');
    }
});

test('reactivation replaces its own mutation, and upgrades retain the stable directory',()=>{
    const {context,values,calls} = fixture();
    const record=installed('/tools/Thor/bin');
    registerTerminalEnvironment(context,{record,platform:'darwin',exists:()=>true});
    registerTerminalEnvironment(context,{record:{...record,root:'/tools/versions/2'},platform:'darwin',exists:()=>true});
    assert.deepEqual(calls,['PATH','PATH']);
    assert.equal(values.size,1);
    assert.equal(values.get('PATH').value,'/tools/Thor/bin:');
    registerTerminalEnvironment(context,{record:installed('/new prefix/bin'),platform:'darwin',exists:()=>true});
    assert.equal(values.get('PATH').value,'/new prefix/bin:');
});

test('missing, invalid and uninstalled records remove only the extension-owned PATH mutation',()=>{
    for (const record of [null,{},installed('relative/bin'),{...installed('/bin'),schema:2},
        {...installed('/bin'),managed_by:'someone-else'},installed('/missing')]) {
        const {context,values} = fixture();
        values.set('PATH',{value:'/old:'}); values.set('UNRELATED',{value:'keep'});
        registerTerminalEnvironment(context,{record,platform:'linux',exists:()=>record?.bin!=='/missing'});
        assert.equal(values.has('PATH'),false);
        assert.equal(values.get('UNRELATED').value,'keep');
        assert.equal(context.environmentVariableCollection.description,undefined);
    }
});

test('an invalid Windows command directory is not accepted as an absolute path',()=>{
    const {context,values}=fixture();
    registerTerminalEnvironment(context,{record:installed('C:relative\\bin'),platform:'win32',exists:()=>true});
    assert.equal(values.size,0);
});

test('startup leaves Odin-only workspaces to OLS and opening Thor documents starts TLS once',async()=>{
    const events={}, found=[], terminalContexts=[], starts=[], stops=[];
    const workspace={isTrusted:true,textDocuments:[{languageId:'odin',uri:{scheme:'file'}}],
        workspaceFolders:[{uri:{fsPath:'/project'}}],
        onDidOpenTextDocument:callback=>{events.open=callback;return {dispose(){}};},
        onDidGrantWorkspaceTrust:()=>({dispose(){}}),onDidChangeConfiguration:()=>({dispose(){}}),
        getWorkspaceFolder:()=>undefined,getConfiguration:()=>({get:(_key,fallback)=>fallback}),
        createFileSystemWatcher:()=>({dispose(){},onDidCreate:()=>({dispose(){}}),
            onDidChange:()=>({dispose(){}}),onDidDelete:()=>({dispose(){}})})};
    const vscode={workspace,extensions:{getExtension:()=>undefined,onDidChange:()=>({dispose(){}})},
        window:{createOutputChannel:()=>({appendLine(){}})},
        EventEmitter:class {event(){return {dispose(){}};} fire(){} dispose(){}},
        commands:{registerCommand:()=>({dispose(){}})}};
    const source=fs.readFileSync(path.join(__dirname,'../extension.js'),'utf8');
    const module={exports:{}};
    const register=()=>{};
    const dependencies={vscode,'node:path':path,'node:fs':{existsSync:()=>true},
        'vscode-languageclient/node':{LanguageClient:class {
            async start(){starts.push(true);} async stop(){stops.push(true);}
        }},'./workspace-files':{},'./hover-size':{},
        './toolchain':{findToolchain:()=>{found.push(true);return '/toolchain';},
            findCompiler:()=>undefined,findBundledOdin:()=>undefined},
        './terminal-environment':{registerTerminalEnvironment:context=>terminalContexts.push(context)},
        './recommended-colors':{registerRecommendedColors:register},'./bracket-refresh':{registerBracketRefresh:register},
        './constant-colors':{registerConstantColors:register},'./run-extension':{activateRunning:register},
        './debug-extension':{activateDebugging:register}};
    vm.runInNewContext(source,{module,require:name=>{
        if (!(name in dependencies)) throw new Error(`Unexpected dependency: ${name}`);
        return dependencies[name];
    },process});
    const context={subscriptions:[]};
    await module.exports.activate(context);
    assert.deepEqual(terminalContexts,[context]);
    assert.equal(found.length,0);
    const doc={languageId:'thor',uri:{scheme:'file'}};
    workspace.textDocuments.push(doc); events.open(doc);
    events.open(doc);events.open(doc);
    await module.exports.deactivate();
    assert.equal(found.length,1);
    assert.equal(starts.length,1);
    assert.equal(stops.length,1);
    const manifest=require('../package.json');
    assert.ok(manifest.activationEvents.includes('onStartupFinished'));
});
