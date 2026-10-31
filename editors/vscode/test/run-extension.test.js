'use strict';
const assert = require('node:assert/strict');
const test = require('node:test');
const {activateRunning, runProject} = require('../run-extension');

function fixture({workspace = '/projects/my project', file = '/projects/my project/my-project.thor', saved = true} = {}) {
    const uri = name => ({fsPath:name, scheme:'file', toString:()=>`file://${name}`});
    const folder = workspace ? {uri:uri(workspace), name:'my project'} : undefined;
    const calls = {saved:0, created:[]};
    const terminal = options => ({options,commands:[],shown:0,
        show(){this.shown++;},sendText(text,newline){this.commands.push({text,newline});}});
    const vscode = {
        window:{activeTextEditor:file ? {document:{uri:uri(file), languageId:'thor'}} : undefined,
            createTerminal:options=>{const value=terminal(options);calls.created.push(value);vscode.window.activeTerminal=value;return value;}},
        workspace:{isTrusted:true, workspaceFolders:folder ? [folder] : [], getWorkspaceFolder:()=>folder,
            getConfiguration:()=>{throw new Error('Run must not query toolchain settings');},
            saveAll:async()=>{calls.saved++; return saved;}},
        tasks:{fetchTasks:()=>{throw new Error('Run must not enumerate tasks');},
            executeTask:()=>{throw new Error('Run must not start a task');}}
    };
    return {vscode,calls,terminal};
}

test('run saves changes and sends only thor run . to the active terminal',async()=>{
    const {vscode,calls,terminal}=fixture();
    const active=terminal({cwd:'/some/current/directory'});
    vscode.window.activeTerminal=active;
    assert.equal(await runProject(vscode),active);
    assert.equal(calls.saved,1);
    assert.deepEqual(calls.created,[]);
    assert.deepEqual(active.commands,[{text:'thor run .',newline:true}]);
    assert.equal(active.shown,1);
});

test('a project terminal is created once and reused on later runs',async()=>{
    const {vscode,calls}=fixture({file:'/projects/my project/lib/helper.thor'});
    const first=await runProject(vscode);
    assert.deepEqual(first.options,{name:'Thor',cwd:'/projects/my project'});
    assert.equal(await runProject(vscode),first);
    assert.equal(calls.created.length,1);
    assert.equal(first.commands.length,2);
});

test('standalone files open a normal terminal in their directory without shell commands for paths',async()=>{
    const {vscode}=fixture({workspace:null,file:"/loose files/O'Brien $(echo bad)/file.thor"});
    const terminal=await runProject(vscode);
    assert.deepEqual(terminal.options,{name:'Thor',cwd:"/loose files/O'Brien $(echo bad)"});
    assert.deepEqual(terminal.commands,[{text:'thor run .',newline:true}]);
});

test('exited terminals are replaced rather than receiving input',async()=>{
    const {vscode,calls,terminal}=fixture();
    const exited=terminal({});exited.exitStatus={code:0};
    vscode.window.activeTerminal=exited;
    await runProject(vscode);
    assert.equal(calls.created.length,1);
    assert.deepEqual(exited.commands,[]);
});

test('an existing terminal can run from its cwd without an open project',async()=>{
    const {vscode,terminal}=fixture({workspace:null,file:null});
    vscode.window.activeTerminal=terminal({});
    await runProject(vscode);
    assert.equal(vscode.window.activeTerminal.commands[0].text,'thor run .');
});

test('untrusted workspaces and failed saves do not execute or create a terminal',async()=>{
    const untrusted=fixture();untrusted.vscode.workspace.isTrusted=false;
    await assert.rejects(runProject(untrusted.vscode),/trusted workspace/);
    assert.equal(untrusted.calls.saved,0);
    const unsaved=fixture({saved:false});
    await assert.rejects(runProject(unsaved.vscode),/Save your changes/);
    assert.deepEqual(unsaved.calls.created,[]);
});

test('a window without a folder, saved file or terminal reports the missing cwd',async()=>{
    const {vscode}=fixture({workspace:null,file:null});
    await assert.rejects(runProject(vscode),/Open a project folder/);
});

test('extension registers the run command and both Thor-scoped keyboard shortcuts',()=>{
    const callbacks={};const context={subscriptions:[]};
    const vscode={commands:{registerCommand:(name,callback)=>{callbacks[name]=callback;return 'subscription';}}};
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
