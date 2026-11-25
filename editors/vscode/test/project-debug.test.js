'use strict';
require('./isolated-installation');
const assert = require('node:assert/strict');
const test = require('node:test');
const path = require('node:path');
const fs = require('node:fs');
const os = require('node:os');
const {activateProjectDebugging, taskType} = require('../project-debug');

function fixture({workspace='/projects', file='/projects/game/game.thor', saved=true, configurations=[]}={}) {
    const uri = fsPath => ({scheme:'file', fsPath});
    const folder = workspace ? {uri:uri(workspace), name:'projects'} : undefined;
    const commands={}, providers={}, launches=[], context={subscriptions:[]};
    const vscode = {
        window:{activeTextEditor:file ? {document:{uri:uri(file)}} : undefined},
        workspace:{isTrusted:true, workspaceFolders:folder ? [folder] : [], getWorkspaceFolder:()=>folder,
            saveAll:async()=>saved,
            getConfiguration:section=>({get:(key,fallback)=>section==='launch' && key==='configurations' ? configurations : fallback})},
        commands:{registerCommand:(name,callback)=>{commands[name]=callback;return {dispose(){}};}},
        tasks:{registerTaskProvider:(name,provider)=>{providers[name]=provider;return {dispose(){}};}},
        debug:{startDebugging:async(folder,config)=>{launches.push({folder,config});return true;}},
        ProcessExecution:class {constructor(command,args,options){Object.assign(this,{command,args,options});}},
        Task:class {constructor(definition,scope,name,source,execution,problemMatchers){Object.assign(this,{definition,scope,name,source,execution,problemMatchers});}},
        TaskGroup:{Build:'build'}
    };
    activateProjectDebugging(context,vscode);
    return {vscode,commands,providers,launches,folder};
}

test('nested project debugging builds and runs the active source directory with matching output paths',async()=>{
    const {commands,providers,launches,folder}=fixture();
    await commands['thor.debug']();
    const config=launches[0].config;
    assert.equal(launches[0].folder,folder);
    assert.equal(config.cwd,'/projects/game');
    assert.equal(config.program,path.join('/projects/game/.vscode/bin',process.platform==='win32'?'debug.exe':'debug'));
    assert.equal(config.manifest,path.join('/projects/game','.vscode','bin','generated','thor-manifest.json'));
    const tasks=providers[taskType].provideTasks();
    assert.equal(tasks.length,1);
    assert.equal(tasks[0].execution.options.cwd,config.cwd);
    assert.deepEqual(tasks[0].definition,config.preLaunchTask);
    assert.deepEqual(tasks[0].execution.args,['build','.','-debug','-o:none','--output-dir',
        path.join('/projects/game','.vscode','bin','generated'),'-out:'+config.program]);
    assert.equal(tasks[0].scope,folder);
});

test('root projects keep their custom launch configuration and prelaunch build unchanged',async()=>{
    const configured={name:'My debug config',type:'thor-lldb',cwd:'${workspaceFolder}',preLaunchTask:'custom-build'};
    const {commands,providers,launches}=fixture({file:'/projects/main.thor',configurations:[configured]});
    await commands['thor.debug']();
    assert.equal(launches[0].config,'My debug config');
    assert.equal(providers[taskType].provideTasks().length,0);
    assert.equal(configured.preLaunchTask,'custom-build');
});

test('nested launches preserve configured program arguments and environment while replacing the root build',async()=>{
    const configured={name:'Root debug',type:'thor-lldb',args:['argument'],env:{EXAMPLE:'yes'},preLaunchTask:'root-build'};
    const {vscode,commands,providers,launches}=fixture({configurations:[configured]});
    await commands['thor.debug']();
    vscode.window.activeTextEditor.document.uri.fsPath='/projects/other folder/source.thor';
    await commands['thor.debug']();
    assert.equal(launches[1].config.cwd,'/projects/other folder');
    assert.deepEqual(launches[1].config.args,['argument']);
    assert.deepEqual(launches[1].config.env,{EXAMPLE:'yes'});
    assert.equal(launches[1].config.preLaunchTask.directory,'/projects/other folder');
    assert.equal(providers[taskType].provideTasks().length,2);
});

test('an open root folder without generated VS Code files can still debug its Thor package',async()=>{
    const {commands,launches}=fixture({file:'/projects/main.thor'});
    await commands['thor.debug']();
    assert.equal(launches[0].config.cwd,'/projects');
    assert.equal(launches[0].config.type,'thor-lldb');
});

test('resolved build tasks keep the original task definition identity',()=>{
    const {providers,folder}=fixture();
    const definition={type:taskType,directory:'/projects/game'};
    const resolved=providers[taskType].resolveTask({definition,scope:folder});
    assert.equal(resolved.definition,definition);
    assert.equal(providers[taskType].resolveTask({definition:{type:taskType}}),undefined);
});

test('debug builds select the native compiler in a checkout containing the shell launcher',()=>{
    const root=fs.mkdtempSync(path.join(os.tmpdir(),'thor-debug-toolchain-'));
    const previous=process.env.THOR_ROOT;
    try {
        fs.mkdirSync(path.join(root,'tls'));
        fs.mkdirSync(path.join(root,'bin'));
        fs.writeFileSync(path.join(root,'thor'),'shell launcher');
        fs.writeFileSync(path.join(root,'bin','thor.exe'),'native compiler fixture');
        process.env.THOR_ROOT=root;
        const {providers,folder}=fixture();
        const task=providers[taskType].resolveTask({definition:{type:taskType,directory:'/projects/game'},scope:folder});
        assert.equal(task.execution.command,path.join(root,process.platform==='win32'?'bin/thor.exe':'thor'));
    } finally {
        if(previous===undefined)delete process.env.THOR_ROOT;else process.env.THOR_ROOT=previous;
        fs.rmSync(root,{recursive:true,force:true});
    }
});

test('untrusted workspaces, failed saves, and missing workspace stop before building or debugging',async()=>{
    const untrusted=fixture();untrusted.vscode.workspace.isTrusted=false;
    await assert.rejects(untrusted.commands['thor.debug'](),/trusted/);
    const unsaved=fixture({saved:false});
    await assert.rejects(unsaved.commands['thor.debug'](),/Save your changes/);
    const loose=fixture({workspace:null,file:'/loose/source.thor'});
    await assert.rejects(loose.commands['thor.debug'](),/Open a Thor project folder/);
    for (const item of [untrusted,unsaved,loose]) {
        assert.deepEqual(item.launches,[]);
        assert.deepEqual(item.providers[taskType].provideTasks(),[]);
    }
});
