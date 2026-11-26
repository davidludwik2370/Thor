'use strict';
require('./isolated-installation');
const assert = require('node:assert/strict');
const test = require('node:test');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const cp = require('node:child_process');
const {activateRunning, runProject, runCommand,invocation} = require('../run-extension');

function fixture({workspace = '/projects/my project', file = '/projects/my project/my-project.thor', saved = true} = {}) {
    const uri = name => ({fsPath:name, scheme:'file', toString:()=>`file://${name}`});
    const folder = workspace ? {uri:uri(workspace), name:'my project'} : undefined;
    const calls = {saved:0, created:[]};
    const terminal = options => ({options,commands:[],shown:0,
        show(){this.shown++;},sendText(text,newline){this.commands.push({text,newline});}});
    const vscode = {
        env:{shell:'/bin/zsh'},
        window:{activeTextEditor:file ? {document:{uri:uri(file), languageId:'thor'}} : undefined,
            get terminals(){return [...new Set([this.activeTerminal,...calls.created].filter(Boolean))];},
            createTerminal:options=>{const value=terminal(options);calls.created.push(value);vscode.window.activeTerminal=value;return value;}},
        workspace:{isTrusted:true, workspaceFolders:folder ? [folder] : [], getWorkspaceFolder:()=>folder,
            getConfiguration:()=>({get:(_key,fallback)=>fallback}),
            saveAll:async()=>{calls.saved++; return saved;}},
        tasks:{fetchTasks:()=>{throw new Error('Run must not enumerate tasks');},
            executeTask:()=>{throw new Error('Run must not start a task');}}
    };
    return {vscode,calls,terminal};
}

test('configured compiler paths work without a terminal PATH entry',async()=>{
    const {vscode}=fixture();
    const compiler=process.platform==='win32'?"C:\\tools\\Thor O'Brien\\thor.exe":"/tools/Thor O'Brien/thor";
    vscode.workspace.getConfiguration=()=>({get:()=>compiler});
    const terminal=await runProject(vscode);
    const expected=process.platform==='win32'?"'C:\\tools\\Thor O'\\''Brien\\thor.exe' run .":"'/tools/Thor O'\\''Brien/thor' run .";
    assert.equal(terminal.commands[0].text,expected);
});
test('native installer paths use correct PowerShell and cmd quoting',()=>{
    assert.equal(invocation({env:{shell:'powershell.exe'}},null,"C:/Thor O'Brien/thor.exe"),"& 'C:/Thor O''Brien/thor.exe' run .");
    assert.equal(invocation({env:{shell:'cmd.exe'}},null,'C:/Thor tools/thor.exe'),'"C:/Thor tools/thor.exe" run .');
    assert.throws(()=>invocation({env:{shell:'cmd.exe'}},null,'C:/%bad%/thor.exe'));
});

test('run saves changes and sends only thor run . when the terminal is already in the source directory',async()=>{
    const {vscode,calls,terminal}=fixture();
    const active=terminal({cwd:'/some/initial/directory'});
    active.shellIntegration={cwd:{scheme:'file',fsPath:'/projects/my project'}};
    vscode.window.activeTerminal=active;
    const result=await runProject(vscode);
    assert.equal(result,active);
    assert.equal(calls.saved,1);
    assert.deepEqual(calls.created,[]);
    assert.deepEqual(active.commands,[{text:'thor run .',newline:true}]);
    assert.equal(active.shown,1);
});

test('a terminal opens in the active file directory even inside a larger workspace',async()=>{
    const {vscode,calls}=fixture({file:'/projects/my project/lib/helper.thor'});
    const first=await runProject(vscode);
    assert.deepEqual(first.options,{name:'Thor',cwd:'/projects/my project/lib'});
    first.shellIntegration={cwd:{scheme:'file',fsPath:'/projects/my project/lib'}};
    const second=await runProject(vscode);
    assert.equal(second,first);
    assert.equal(calls.created.length,1);
    assert.equal(first.commands.length,2);
    assert.deepEqual(first.commands.map(c=>c.text),['thor run .','thor run .']);
});

test('an existing terminal changes directory for the active file and follows later manual directory changes',async()=>{
    const {vscode,calls,terminal}=fixture();
    const active=terminal({cwd:'/projects/my project'});
    active.shellIntegration={cwd:{scheme:'file',fsPath:'/elsewhere'}};
    vscode.window.activeTerminal=active;
    await runProject(vscode);
    assert.match(active.commands[0].text,/cd -- '\/projects\/my project'/);
    vscode.window.activeTextEditor.document.uri.fsPath='/second project/file.thor';
    await runProject(vscode);
    assert.match(active.commands[1].text,/cd -- '\/second project'/);
    active.shellIntegration.cwd.fsPath='/second project';
    await runProject(vscode);
    assert.equal(active.commands[2].text,'thor run .');
    assert.deepEqual(calls.created,[]);
});

test('unknown shell cwd checks PWD instead of trusting stale terminal creation options',async()=>{
    const {vscode,terminal}=fixture();
    const active=terminal({cwd:'/projects/my project'});
    vscode.window.activeTerminal=active;
    await runProject(vscode);
    assert.equal(active.commands[0].text,`if [ "$PWD" != '/projects/my project' ]; then cd -- '/projects/my project'; fi && thor run .`);
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

test('Run selects the live terminal after saving, rather than an earlier active terminal',async()=>{
    const {vscode,calls,terminal}=fixture();
    const earlier=terminal({}),current=terminal({});
    vscode.window.activeTerminal=earlier;
    vscode.workspace.saveAll=async()=>{vscode.window.activeTerminal=current;return true;};
    const result=await runProject(vscode);
    assert.equal(result,current);
    assert.deepEqual(earlier.commands,[]);
    assert.equal(current.commands.length,1);
    assert.equal(calls.created.length,0);
});

test('a terminal that exits while saving is replaced',async()=>{
    const {vscode,calls,terminal}=fixture();
    const closed=terminal({});vscode.window.activeTerminal=closed;
    vscode.workspace.saveAll=async()=>{closed.exitStatus={code:0};return true;};
    const result=await runProject(vscode);
    assert.equal(result,calls.created[0]);
    assert.equal(calls.created.length,1);
    assert.deepEqual(closed.commands,[]);
});

test('stale active terminals absent from the live collection are replaced',async()=>{
    const {vscode,calls,terminal}=fixture();
    const closed=terminal({});vscode.window.activeTerminal=closed;
    Object.defineProperty(vscode.window,'terminals',{value:[]});
    const result=await runProject(vscode);
    assert.equal(result,calls.created[0]);
    assert.deepEqual(closed.commands,[]);
});

for (const operation of ['show','sendText']) test(`Run replaces a disposed terminal before ${operation}, while close events are still queued`,async()=>{
    const {vscode,calls,terminal}=fixture();
    const closed=terminal({});vscode.window.activeTerminal=closed;
    closed[operation]=()=>{throw new Error('Terminal has already been disposed');};
    const result=await runProject(vscode);
    assert.equal(calls.created.length,1);
    assert.equal(result,calls.created[0]);
    assert.deepEqual(closed.commands,[]);
    assert.deepEqual(result.commands,[{text:'thor run .',newline:true}]);
});

test('unrelated terminal errors propagate without dispatching a second command',async()=>{
    const {vscode,calls,terminal}=fixture();
    const active=terminal({});vscode.window.activeTerminal=active;
    active.sendText=()=>{throw new Error('unrelated terminal failure');};
    await assert.rejects(runProject(vscode),/unrelated terminal failure/);
    assert.equal(calls.created.length,0);
});

test('a newly created terminal failing to open is not retried',async()=>{
    const {vscode,calls,terminal}=fixture();
    vscode.window.createTerminal=options=>{
        const value=terminal(options);calls.created.push(value);
        value.show=()=>{throw new Error('Terminal has already been disposed');};
        return value;
    };
    await assert.rejects(runProject(vscode),/Terminal has already been disposed/);
    assert.equal(calls.created.length,1);
});

test('an existing terminal can run from its cwd without an open project',async()=>{
    const {vscode,terminal}=fixture({workspace:null,file:null});
    vscode.window.activeTerminal=terminal({});
    await runProject(vscode);
    assert.equal(vscode.window.activeTerminal.commands[0].text,'thor run .');
});

test('missing editor falls back to the workspace directory',async()=>{
    const {vscode}=fixture({file:null});
    const terminal=await runProject(vscode);
    assert.equal(terminal.options.cwd,'/projects/my project');
});

test('POSIX fallback preserves quoted paths, skips redundant cd, and refuses to run after failed cd',
    {skip:process.platform==='win32'},()=>{
    const temporary=fs.mkdtempSync(path.join(os.tmpdir(),'thor-terminal-'));
    const root=fs.realpathSync(temporary);
    try {
        const target=path.join(root,"O'Brien $(echo surprise) with spaces");
        fs.mkdirSync(target);
        const {vscode,terminal}=fixture();
        const command=runCommand(vscode,terminal({}),target);
        const stub=`thor() { printf '%s\\n' "$PWD" "$*"; }; `;
        const moved=cp.spawnSync('/bin/sh',['-c',stub+command],{cwd:root,encoding:'utf8'});
        assert.equal(moved.status,0,moved.stderr);
        assert.equal(moved.stdout,target+'\nrun .\n');
        const same=cp.spawnSync('/bin/sh',['-c',stub+'cd() { return 77; }; '+command],{cwd:target,encoding:'utf8'});
        assert.equal(same.status,0,same.stderr);
        assert.equal(same.stdout,target+'\nrun .\n');
        const missing=cp.spawnSync('/bin/sh',['-c',stub+runCommand(vscode,terminal({}),path.join(root,'missing'))],
            {cwd:root,encoding:'utf8'});
        assert.notEqual(missing.status,0);
        assert.equal(missing.stdout,'');
    } finally {fs.rmSync(root,{recursive:true,force:true});}
});

test('PowerShell and fish get their own conditional-directory syntax',()=>{
    const {vscode,terminal}=fixture();
    const powershell=terminal({});powershell.creationOptions={shellPath:'pwsh'};
    const command=runCommand(vscode,powershell,"/O'Brien [files]");
    assert.match(command,/-LiteralPath '\/O''Brien \[files\]' -ErrorAction Stop/);
    const fish=terminal({});fish.creationOptions={shellPath:'/bin/fish'};
    assert.equal(runCommand(vscode,fish,'/project'),`if test "$PWD" != '/project'; cd -- '/project'; end; and thor run .`);
});

test('cmd checks its current drive/directory and safely opens a new terminal for literal percent paths',async()=>{
    const {vscode,terminal,calls}=fixture({file:'/projects/50%done/file.thor'});
    vscode.env.shell='cmd.exe';
    const active=terminal({});
    assert.equal(runCommand(vscode,active,'/project'),`if /I "%CD%"=="/project" (thor run .) else (cd /d "/project" && thor run .)`);
    vscode.window.activeTerminal=active;
    await runProject(vscode);
    assert.deepEqual(active.commands,[]);
    assert.equal(calls.created.length,1);
    assert.equal(calls.created[0].options.cwd,'/projects/50%done');
    assert.equal(calls.created[0].commands[0].text,'thor run .');
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
