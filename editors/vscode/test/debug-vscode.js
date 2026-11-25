'use strict';
const vscode = require('vscode');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');

async function until(action, predicate) {
    const deadline = Date.now()+60000;
    while (Date.now()<deadline) {
        const value = await action();
        if (predicate(value)) return value;
        await new Promise(resolve=>setTimeout(resolve,100));
    }
    throw new Error('Timed out waiting for debugger state');
}

async function run() {
    const result = {success:false,stops:[],protocol:[]};
    const subscriptions = [];
    try {
        const root = vscode.workspace.workspaceFolders[0].uri.fsPath;
        const source = path.join(root,'debug_project.thor');
        const document = await vscode.workspace.openTextDocument(source);
        await vscode.window.showTextDocument(document);
        const extension = vscode.extensions.getExtension('thor-local.thor-language');
        await extension.activate();
        const setup=new vscode.WorkspaceEdit();
        const original=document.getText();
        setup.insert(document.uri,document.positionAt(original.indexOf('main :: proc() {')+'main :: proc() {'.length),
            '\n    confirm_run()\n    state := struct { marker: int }{1}\n    using state\n    assert(marker == 1)\n');
        const setupApplied=await vscode.workspace.applyEdit(setup);
        assert.ok(setupApplied);
        const startedTasks=[];
        subscriptions.push(vscode.tasks.onDidStartTask(event=>startedTasks.push(event.execution.task.name)));
        assert.ok(document.isDirty);
        assert.equal(extension.packageJSON.contributes.keybindings.find(k=>k.command==='thor.run').mac,'cmd+r');
        const start=Date.now();
        await vscode.commands.executeCommand('thor.run');
        result.runDispatchMs=Date.now()-start;
        const terminal=await until(()=>vscode.window.activeTerminal,Boolean);
        await until(()=>fs.existsSync(path.join(root,'normal-run.txt')),Boolean);
        assert.equal(document.isDirty,false,'Run must save the edited source');
        assert.equal(fs.readFileSync(path.join(root,'normal-run.txt'),'utf8'),'ok','Run must build the whole package without -debug');
        assert.equal(vscode.debug.activeDebugSession,undefined);
        assert.equal(fs.existsSync(path.join(root,'.vscode/bin',process.platform==='win32'?'debug.exe':'debug')),false);
        await new Promise(resolve=>setTimeout(resolve,100)); // Let the first program return to the shell prompt.
        fs.unlinkSync(path.join(root,'normal-run.txt'));
        const warmStart=Date.now();
        await vscode.commands.executeCommand('thor.run');
        result.warmRunDispatchMs=Date.now()-warmStart;
        assert.equal(vscode.window.activeTerminal,terminal,'Repeated runs must reuse the active shell');
        await until(()=>fs.existsSync(path.join(root,'normal-run.txt')),Boolean);
        assert.deepEqual(startedTasks,[],'Cmd+R must not start a VS Code task');
        assert.equal(fs.readFileSync(path.join(root,'normal-run.txt'),'utf8'),'ok');
        assert.ok(!document.getText().includes('#+feature using-stmt'));
        assert.equal(extension.packageJSON.contributes.keybindings.find(k=>k.command==='thor.debug').mac,'cmd+shift+r');
        assert.ok(extension.packageJSON.contributes.breakpoints.some(b=>b.language==='thor'));
        const stops = [];
        subscriptions.push(vscode.debug.registerDebugAdapterTrackerFactory('thor-lldb',{
            createDebugAdapterTracker() { return {onWillReceiveMessage(message) {
                if (['initialize','launch','restart','disconnect'].includes(message.command)) result.protocol.push(message.command);
                if (['next','stepIn','stepOut','continue'].includes(message.command)) (result.steps ||= []).push(message.command);
            },onDidSendMessage(message) {
                if (message.type==='event' && message.event==='stopped') stops.push(message.body);
                if(message.type==='event' && ['terminated','exited'].includes(message.event)) (result.exits ||= []).push(message);
                if(message.type==='response' && message.command==='stackTrace') (result.frames ||= []).push(message.body?.stackFrames?.[0]);
                if (message.type==='event' && message.event==='output' && message.body.category==='stderr') {
                    (result.errors ||= []).push(message.body.output);
                }
            }}; }
        }));
        const text = document.getText();
        const position = document.positionAt(text.indexOf('numbers :='));
        const breakpoint = new vscode.SourceBreakpoint(new vscode.Location(document.uri,new vscode.Range(position,position)));
        vscode.debug.addBreakpoints([breakpoint]);
        // This is the command contributed for Cmd+Shift+R, exercising the task,
        // generated launch config, extension factory, CodeLLDB and source maps.
        await vscode.commands.executeCommand('thor.debug');
        let session = await until(()=>vscode.debug.activeDebugSession,s=>s?.type==='thor-lldb');
        async function frame() {
            const stop = await until(()=>stops.shift(),s=>!!s);
            session=await until(()=>vscode.debug.activeDebugSession,s=>s?.type==='thor-lldb');
            const frames = (await session.customRequest('stackTrace',{threadId:stop.threadId})).stackFrames;
            assert.equal(frames[0].source.path,source);
            assert.ok(!JSON.stringify(frames).includes('__thor_'));
            result.stops.push(frames[0].line);
            return {frame:frames[0],thread:stop.threadId};
        }
        let current = await frame();
        assert.equal(current.frame.line,position.line+1);
        const manifestPath=session.configuration.manifest;
        const manifest=JSON.parse(fs.readFileSync(manifestPath,'utf8'));
        const generatedSource=manifest.generated.find(file=>path.basename(file)==='debug_project.odin');
        assert.ok(generatedSource,'The debug manifest must include the active Thor source');
        assert.ok(fs.readFileSync(path.resolve(path.dirname(manifestPath),generatedSource),'utf8').startsWith('#+feature using-stmt\n'));
        await session.customRequest('next',{threadId:current.thread});
        current=await frame();
        assert.equal(current.frame.line,document.positionAt(text.indexOf('last :=')).line+1);
        await session.customRequest('next',{threadId:current.thread});
        current=await frame();
        assert.equal(current.frame.line,document.positionAt(text.indexOf('message :=')).line+1);
        const bodyLine=document.positionAt(text.indexOf('result :=')).line+1;
        for (let i=0;i<20 && current.frame.line!==bodyLine;i++) {
            await session.customRequest('stepIn',{threadId:current.thread});
            current=await frame();
        }
        assert.equal(current.frame.line,bodyLine);
        // Inspect the completed assignment too: some native Windows PDBs point
        // the entry line before the parameter's prologue spill. The dedicated
        // DAP regressions retain that first-statement failure separately.
        if(process.platform==='win32') {
            const entry=await session.customRequest('evaluate',{frameId:current.frame.id,expression:'value',context:'watch'});
            result.entryArgument=entry.result;
            await session.customRequest('next',{threadId:current.thread});
            current=await frame();
            assert.equal(current.frame.line,bodyLine+1);
        }
        const value = await session.customRequest('evaluate',{frameId:current.frame.id,expression:'value',context:'watch'});
        assert.equal(value.result,'9');
        const scopes = (await session.customRequest('scopes',{frameId:current.frame.id})).scopes;
        for (const scope of scopes.filter(s=>!s.expensive)) {
            const variables=(await session.customRequest('variables',{variablesReference:scope.variablesReference})).variables;
            assert.ok(!variables.some(v=>/__thor_[a-f0-9]{12}_/.test(v.name)));
        }
        await session.customRequest('stepOut',{threadId:current.thread});
        current=await frame();
        const edit=new vscode.WorkspaceEdit();
        edit.insert(document.uri,document.positionAt(document.getText().length),'\n// Rebuild maps on restart.\n');
        const restartEditApplied=await vscode.workspace.applyEdit(edit);
        assert.ok(restartEditApplied);
        await document.save();
        await vscode.commands.executeCommand('workbench.action.debug.restart');
        current=await frame();
        assert.equal(current.frame.line,position.line+1);
        assert.equal(result.protocol.filter(command=>command==='launch').length,2,'Restart must launch a fresh adapter/build');
        vscode.debug.removeBreakpoints([breakpoint]);
        await session.customRequest('continue',{threadId:current.thread});
        await until(()=>vscode.debug.activeDebugSession,s=>!s);
        // A nested package must build and launch locally even though only its
        // parent has a VS Code launch configuration.
        const nested=path.join(root,'nested project');
        fs.mkdirSync(nested);
        const nestedSource=path.join(nested,'nested.thor');
        fs.writeFileSync(nestedSource,`package main
import "core:os"
main::proc(){
 value:=42
 err:=os.write_entire_file("debug-cwd.txt", []u8{'o','k'})
 if err!=nil {panic("write marker")}
 _=value
}
`);
        const nestedDocument=await vscode.workspace.openTextDocument(nestedSource);
        await vscode.window.showTextDocument(nestedDocument);
        const nestedPosition=nestedDocument.positionAt(nestedDocument.getText().indexOf('value:=42'));
        const nestedBreakpoint=new vscode.SourceBreakpoint(new vscode.Location(nestedDocument.uri,new vscode.Range(nestedPosition,nestedPosition)));
        vscode.debug.addBreakpoints([nestedBreakpoint]);
        await vscode.commands.executeCommand('thor.debug');
        session=await until(()=>vscode.debug.activeDebugSession,s=>s?.type==='thor-lldb');
        assert.equal(session.configuration.cwd,nested);
        let nestedStop=await until(()=>stops.shift(),Boolean);
        let nestedFrames=(await session.customRequest('stackTrace',{threadId:nestedStop.threadId})).stackFrames;
        assert.equal(nestedFrames[0].source.path,nestedSource);
        assert.equal(nestedFrames[0].line,nestedPosition.line+1);
        await vscode.commands.executeCommand('workbench.action.debug.restart');
        nestedStop=await until(()=>stops.shift(),Boolean);
        session=await until(()=>vscode.debug.activeDebugSession,s=>s?.type==='thor-lldb');
        nestedFrames=(await session.customRequest('stackTrace',{threadId:nestedStop.threadId})).stackFrames;
        assert.equal(nestedFrames[0].source.path,nestedSource);
        vscode.debug.removeBreakpoints([nestedBreakpoint]);
        await session.customRequest('continue',{threadId:nestedStop.threadId});
        await until(()=>vscode.debug.activeDebugSession,s=>!s);
        assert.equal(fs.readFileSync(path.join(nested,'debug-cwd.txt'),'utf8'),'ok');
        assert.ok(!fs.existsSync(path.join(root,'debug-cwd.txt')));
        result.success=true;
        result.checks=['minimal mkthor project with project-named source','Cmd+R sends thor run . to a normal terminal and saves edits',
            'repeated runs reuse the active terminal without starting VS Code tasks',
            'Cmd+Shift+R debug command','pre-launch build task','automatic using header','CodeLLDB launch',
            'Thor breakpoint','Step Over','Step Into fn through fstring','locals/watch','Step Out','restart/rebuild',
            'nested source-folder build and debug cwd','nested project breakpoint and restart','clean exit'];
    } catch (error) {
        result.error=error.stack || String(error);
        throw error;
    } finally {
        for (const subscription of subscriptions) subscription.dispose();
        await vscode.debug.stopDebugging();
        if (process.env.THOR_VSCODE_TEST_RESULT) fs.writeFileSync(process.env.THOR_VSCODE_TEST_RESULT,JSON.stringify(result,null,2));
    }
}
module.exports={run};
