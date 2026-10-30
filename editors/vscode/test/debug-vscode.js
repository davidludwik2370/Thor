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
        const source = path.join(root,'main.thor');
        const document = await vscode.workspace.openTextDocument(source);
        await vscode.window.showTextDocument(document);
        const extension = vscode.extensions.getExtension('thor-local.thor-language');
        await extension.activate();
        const setup=new vscode.WorkspaceEdit();
        const original=document.getText();
        setup.insert(document.uri,document.positionAt(original.indexOf('main :: proc() {')+'main :: proc() {'.length),
            '\n    state := struct { marker: int }{1}\n    using state\n    assert(marker == 1)\n');
        assert.ok(await vscode.workspace.applyEdit(setup));
        await document.save();
        assert.ok(!document.getText().includes('#+feature using-stmt'));
        assert.equal(extension.packageJSON.contributes.keybindings.find(k=>k.command==='thor.debug').mac,'cmd+shift+r');
        assert.ok(extension.packageJSON.contributes.breakpoints.some(b=>b.language==='thor'));
        const stops = [];
        subscriptions.push(vscode.debug.registerDebugAdapterTrackerFactory('thor-lldb',{
            createDebugAdapterTracker() { return {onWillReceiveMessage(message) {
                if (['initialize','launch','restart','disconnect'].includes(message.command)) result.protocol.push(message.command);
            },onDidSendMessage(message) {
                if (message.type==='event' && message.event==='stopped') stops.push(message.body);
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
        assert.ok(fs.readFileSync(path.join(root,'.vscode/bin/generated/main.odin'),'utf8').startsWith('#+feature using-stmt\n'));
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
        assert.ok(await vscode.workspace.applyEdit(edit));
        await document.save();
        await vscode.commands.executeCommand('workbench.action.debug.restart');
        current=await frame();
        assert.equal(current.frame.line,position.line+1);
        assert.equal(result.protocol.filter(command=>command==='launch').length,2,'Restart must launch a fresh adapter/build');
        vscode.debug.removeBreakpoints([breakpoint]);
        await session.customRequest('continue',{threadId:current.thread});
        await until(()=>vscode.debug.activeDebugSession,s=>!s);
        result.success=true;
        result.checks=['mkthor project','shortcut command','pre-launch build task','automatic using header','CodeLLDB launch',
            'Thor breakpoint','Step Over','Step Into fn through fstring','locals/watch','Step Out','restart/rebuild','clean exit'];
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
