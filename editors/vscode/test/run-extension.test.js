'use strict';
const assert = require('node:assert/strict');
const path = require('node:path');
const test = require('node:test');
const {activateRunning, runProject} = require('../run-extension');

function fixture({workspace = '/projects/my project', file = '/projects/my project/my-project.thor', saved = true, settings = {}} = {}) {
    const uri = name => ({fsPath:name, scheme:'file', toString:()=>`file://${name}`});
    const folder = workspace ? {uri:uri(workspace), name:'my project'} : undefined;
    const calls = {saved:0, executed:[], tasks:[], terminals:[]};
    const vscode = {
        window:{activeTextEditor:file ? {document:{uri:uri(file), languageId:'thor'}} : undefined,
            createTerminal:options=>{const terminal={options,show:()=>{},sendText:(text)=>{terminal.text=text;}}; calls.terminals.push(terminal);return terminal;}},
        workspace:{isTrusted:true, workspaceFolders:folder ? [folder] : [], getWorkspaceFolder:()=>folder,
            getConfiguration:()=>({get:(key, fallback)=>settings[key] ?? fallback}),
            saveAll:async()=>{calls.saved++; return saved;}},
        tasks:{fetchTasks:async()=>calls.tasks, executeTask:async task=>{calls.executed.push(task); return task;}},
        Task:class {constructor(definition, scope, name, source, execution, matchers) {Object.assign(this,{definition,scope,name,source,execution,matchers});}},
        ProcessExecution:class {constructor(command,args,options) {Object.assign(this,{command,args,options});}},
        TaskScope:{Global:1,Workspace:2}, TaskRevealKind:{Always:1}, TaskPanelKind:{Shared:2}
    };
    return {vscode,calls,folder, dependencies:{toolchain:()=>'/tools/Thor',exists:()=>true}};
}

test('run saves changes and honors the current workspace configured task',async()=>{
    const {vscode,calls,folder,dependencies}=fixture();
    const other={name:'run-thor',scope:{uri:{toString:()=>'/another/project'}}};
    const configured={name:'run-thor',scope:folder,execution:{args:['run','.','-define:MODE=2']}};
    calls.tasks=[other,configured];
    await runProject(vscode,dependencies);
    assert.equal(calls.saved,1);
    assert.deepEqual(calls.executed,[configured]);
});

test('existing projects run the whole package through an interactive process task',async()=>{
    const {vscode,calls,folder,dependencies}=fixture({file:'/projects/my project/lib/helper.thor'});
    await runProject(vscode,dependencies);
    const [task]=calls.executed;
    assert.equal(task.scope,folder);
    assert.equal(task.execution.command,path.join('/tools/Thor','thor'));
    assert.deepEqual(task.execution.args,['run','.']);
    assert.deepEqual(task.execution.options,{cwd:'/projects/my project'});
    assert.equal(task.presentationOptions.focus,true);
    assert.equal(task.runOptions.instanceLimit,1);
});

test('standalone files use a visible terminal with safely quoted paths',async()=>{
    const {vscode,calls,dependencies}=fixture({workspace:null,file:'/loose files/test file.thor'});
    await runProject(vscode,dependencies);
    assert.deepEqual(calls.executed,[]);
    const [terminal]=calls.terminals;
    assert.equal(terminal.options.cwd,'/loose files');
    assert.equal(terminal.options.shellPath,'/bin/sh');
    assert.equal(terminal.text,"cd '/loose files' && '/tools/Thor/thor' 'run' '/loose files/test file.thor'");
});

test('standalone shell arguments escape apostrophes and never interpolate dollar expressions',async()=>{
    for(const platform of ['darwin','win32']){
        const {vscode,calls,dependencies}=fixture({workspace:null,file:"/loose files/O'Brien $(echo bad).thor"});
        await runProject(vscode,{...dependencies,platform});
        const [terminal]=calls.terminals;
        assert.equal(terminal.options.shellPath,platform==='win32' ? 'powershell.exe' : '/bin/sh');
        assert.ok(terminal.text.includes(platform==='win32' ? "O''Brien $(echo bad).thor'" : "O'\\''Brien $(echo bad).thor'"));
    }
});

test('run respects the configured compiler and server installation',async()=>{
    for(const [settings,expected] of [
        [{'compiler.path':'../my tools/thor'},path.resolve('/projects/my tools/thor')],
        [{'server.path':'/custom/Thor/tls/bin/tls'},path.resolve('/custom/Thor/thor')]
    ]){
        const {vscode,calls,dependencies}=fixture({settings});
        await runProject(vscode,dependencies);
        assert.equal(calls.executed[0].execution.command,expected);
    }
});

test('run rejects untrusted workspaces and failed saves before starting a task',async()=>{
    const untrusted=fixture();untrusted.vscode.workspace.isTrusted=false;
    await assert.rejects(runProject(untrusted.vscode,untrusted.dependencies),/trusted workspace/);
    assert.equal(untrusted.calls.saved,0);
    assert.deepEqual(untrusted.calls.executed,[]);
    const unsaved=fixture({saved:false});
    await assert.rejects(runProject(unsaved.vscode,unsaved.dependencies),/Save your changes/);
    assert.deepEqual(unsaved.calls.executed,[]);
});

test('missing files and toolchains report an error without executing anything',async()=>{
    const empty=fixture({workspace:null,file:null});
    await assert.rejects(runProject(empty.vscode,empty.dependencies),/save a .thor file/);
    const missing=fixture();
    await assert.rejects(runProject(missing.vscode,{toolchain:()=>undefined,exists:()=>false}),/compiler not found/);
    assert.deepEqual(missing.calls.executed,[]);
});

test('extension registers the run command and both Thor-scoped keyboard shortcuts',()=>{
    const callbacks={};const context={subscriptions:[]};
    const vscode={commands:{registerCommand:(name,callback)=>{callbacks[name]=callback; return 'subscription';}}};
    activateRunning(context,vscode);
    assert.equal(typeof callbacks['thor.run'],'function');
    assert.deepEqual(context.subscriptions,['subscription']);
    const manifest=require('../package.json');
    for(const [command,mac] of [['thor.run','cmd+r'],['thor.debug','cmd+shift+r']]){
        const key=manifest.contributes.keybindings.find(key=>key.command===command);
        assert.equal(key.mac,mac);
        assert.equal(key.when,'editorLangId == thor && !inDebugMode');
    }
});
