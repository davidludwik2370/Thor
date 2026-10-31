'use strict';
const vscode=require('vscode');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const path=require('node:path');

async function run(){
    const result={success:false};
    try{
        assert.ok(!vscode.workspace.workspaceFolders?.length,'This test requires no workspace folder');
        const document=await vscode.workspace.openTextDocument(process.env.THOR_VSCODE_TEST_SOURCE);
        await vscode.window.showTextDocument(document);
        await vscode.extensions.getExtension('thor-local.thor-language').activate();
        const edit=new vscode.WorkspaceEdit();
        edit.insert(document.uri,document.positionAt(document.getText().length),'// unsaved standalone edit\n');
        assert.ok(await vscode.workspace.applyEdit(edit));
        await vscode.commands.executeCommand('thor.run');
        const output=path.join(path.dirname(document.uri.fsPath),'standalone-run.txt');
        const deadline=Date.now()+30000;
        while(!fs.existsSync(output)&&Date.now()<deadline)await new Promise(resolve=>setTimeout(resolve,100));
        assert.equal(document.isDirty,false);
        assert.equal(fs.readFileSync(output,'utf8'),'ok');
        assert.equal(vscode.debug.activeDebugSession,undefined);
        result.success=true;
        result.checks=['standalone .thor file with spaces and no workspace folder','unsaved edits saved','integrated process execution without debugging'];
    }catch(error){result.error=error.stack||String(error);throw error;}
    finally{
        for(const terminal of vscode.window.terminals)if(terminal.name.startsWith('Thor: '))terminal.dispose();
        fs.writeFileSync(process.env.THOR_VSCODE_TEST_RESULT,JSON.stringify(result,null,2));
    }
}
module.exports={run};
