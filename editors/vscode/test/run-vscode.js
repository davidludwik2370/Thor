'use strict';
const vscode=require('vscode');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const path=require('node:path');

async function run(){
    const result={success:false};
    let executionSubscription;
    let startSubscription;
    try{
        assert.ok(!vscode.workspace.workspaceFolders?.length,'This test requires no workspace folder');
        const document=await vscode.workspace.openTextDocument(process.env.THOR_VSCODE_TEST_SOURCE);
        await vscode.window.showTextDocument(document);
        await vscode.extensions.getExtension('thor-local.thor-language').activate();
        const edit=new vscode.WorkspaceEdit();
        edit.insert(document.uri,document.positionAt(document.getText().length),'// unsaved standalone edit\n');
        const applied=await vscode.workspace.applyEdit(edit);
        assert.ok(applied);
        // Start in the wrong directory, rather than relying on terminal defaults.
        const terminal=vscode.window.createTerminal({name:'Thor',cwd:path.dirname(path.dirname(document.uri.fsPath))});
        terminal.show();
        const terminalDeadline=Date.now()+10000;
        while(vscode.window.activeTerminal!==terminal&&Date.now()<terminalDeadline)await new Promise(resolve=>setTimeout(resolve,25));
        assert.equal(vscode.window.activeTerminal,terminal);
        const executions=[];
        result.executions=[];
        result.output='';
        startSubscription=vscode.window.onDidStartTerminalShellExecution(event=>{
            if(event.terminal===terminal)void (async()=>{
                for await (const chunk of event.execution.read())result.output+=chunk;
            })();
        });
        executionSubscription=vscode.window.onDidEndTerminalShellExecution(event=>{
            if(event.terminal===terminal){
                executions.push(event);
                result.executions.push({command:event.execution.commandLine.value,exitCode:event.exitCode});
            }
        });
        await vscode.commands.executeCommand('thor.run');
        const output=path.join(path.dirname(document.uri.fsPath),'standalone-run.txt');
        const deadline=Date.now()+30000;
        while((!fs.existsSync(output)||executions.length<1)&&Date.now()<deadline)await new Promise(resolve=>setTimeout(resolve,100));
        assert.equal(document.isDirty,false);
        assert.equal(fs.readFileSync(output,'utf8'),'ok');
        assert.equal(vscode.debug.activeDebugSession,undefined);
        assert.equal(vscode.window.activeTerminal,terminal);
        // Quoted-path shell syntax is covered by the Node regressions. Odin's
        // -out validation rejects apostrophes even when shell quoting is correct.
        const nextFolder=path.join(path.dirname(document.uri.fsPath),'another project');
        fs.mkdirSync(nextFolder);
        const nextSource=path.join(nextFolder,'next.thor');
        fs.writeFileSync(nextSource,document.getText());
        const nextDocument=await vscode.workspace.openTextDocument(nextSource);
        await vscode.window.showTextDocument(nextDocument);
        await vscode.commands.executeCommand('thor.run');
        const nextOutput=path.join(nextFolder,'standalone-run.txt');
        const nextDeadline=Date.now()+30000;
        while((!fs.existsSync(nextOutput)||executions.length<2)&&Date.now()<nextDeadline)await new Promise(resolve=>setTimeout(resolve,100));
        assert.equal(fs.readFileSync(nextOutput,'utf8'),'ok');
        assert.equal(vscode.window.activeTerminal,terminal);
        // dispose() immediately invalidates the terminal API object; its close
        // event and activeTerminal update may still be queued when Run starts.
        fs.unlinkSync(nextOutput);
        terminal.dispose();
        await vscode.commands.executeCommand('thor.run');
        const replacementDeadline=Date.now()+30000;
        while(!fs.existsSync(nextOutput)&&Date.now()<replacementDeadline)await new Promise(resolve=>setTimeout(resolve,100));
        assert.equal(fs.readFileSync(nextOutput,'utf8'),'ok');
        assert.notEqual(vscode.window.activeTerminal,terminal);
        result.success=true;
        result.checks=['standalone .thor file with spaces and no workspace folder','unsaved edits saved',
            'existing terminal changes from the wrong directory','switching source folders reuses the terminal',
            'directory names with spaces','integrated process execution without debugging',
            'Run immediately after disposing the active terminal'];
    }catch(error){result.error=error.stack||String(error);throw error;}
    finally{
        executionSubscription?.dispose();
        startSubscription?.dispose();
        for(const terminal of vscode.window.terminals)if(terminal.name==='Thor')terminal.dispose();
        fs.writeFileSync(process.env.THOR_VSCODE_TEST_RESULT,JSON.stringify(result,null,2));
    }
}
module.exports={run};
