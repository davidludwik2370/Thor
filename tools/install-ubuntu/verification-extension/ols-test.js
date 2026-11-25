'use strict';
// Runs with the official OLS extension and an unmodified native upstream server.
const vscode=require('vscode');
const fs=require('node:fs');
const path=require('node:path');
const assert=require('node:assert/strict');
const textOf=values=>(values||[]).flatMap(v=>v.contents.map(c=>c.value||c)).join('\n');
async function until(action,predicate) {
    const deadline=Date.now()+30000;let value;
    do {
        value=await action();if(predicate(value))return value;
        await new Promise(resolve=>setTimeout(resolve,150));
    }while(Date.now()<deadline);
    throw Error('OLS request timed out: '+JSON.stringify(value));
}
async function run() {
    const result={success:false,checks:[]};
    try {
        const folder=vscode.workspace.workspaceFolders[0].uri;
        const uri=vscode.Uri.joinPath(folder,'main.odin');
        const text='package main\nRecord::struct{number:int}\nidentity::proc(value:$T)->T{return value}\nmain::proc(){record:=identity(Record{3});_=record.number}\n';
        await vscode.workspace.fs.writeFile(uri,Buffer.from(text));
        const doc=await vscode.workspace.openTextDocument(uri);
        await vscode.window.showTextDocument(doc);
        assert.equal(doc.languageId,'odin');
        const ols=vscode.extensions.getExtension('DanielGavin.ols');
        assert.ok(ols);await ols.activate();
        const thor=vscode.extensions.getExtension('thor-local.thor-language');
        assert.ok(thor);await thor.activate();
        assert.ok(!thor.packageJSON.contributes.languages.some(language=>language.id==='odin'));
        assert.ok(!thor.packageJSON.contributes.grammars.some(grammar=>grammar.language==='odin'));
        const language=ols.packageJSON.contributes.languages.find(language=>language.id==='odin');
        assert.ok(ols.packageJSON.contributes.grammars.some(grammar=>grammar.language==='odin'));
        for(const theme of ['dark','light'])assert.ok(fs.existsSync(path.join(ols.extensionPath,language.icon[theme])));
        result.checks.push('official OLS owns Odin grammar and icons; Thor contributes none');
        const hover=await until(()=>vscode.commands.executeCommand('vscode.executeHoverProvider',uri,
            doc.positionAt(text.indexOf('record:=')+1)),values=>textOf(values).includes('Record'));
        assert.ok(textOf(hover).includes('record:'));
        result.checks.push('upstream OLS generic result hover');
        const completion=await until(()=>vscode.commands.executeCommand('vscode.executeCompletionItemProvider',uri,
            doc.positionAt(text.indexOf('record.number')+'record.'.length)),value=>value?.items.some(item=>(item.label.label||item.label)==='number'));
        assert.ok(completion.items.length);
        result.checks.push('upstream OLS field completion');
        const tokens=await until(()=>vscode.commands.executeCommand('vscode.provideDocumentSemanticTokens',uri),value=>value?.data?.length);
        assert.ok(tokens.data.length);
        result.checks.push('upstream OLS semantic highlighting');
        const thorUri=vscode.Uri.joinPath(folder,'other.thor');
        const thorText='package main\nanswer::fn()->int{return 42}\n';
        await vscode.workspace.fs.writeFile(thorUri,Buffer.from(thorText));
        const thorDoc=await vscode.workspace.openTextDocument(thorUri);
        await vscode.window.showTextDocument(thorDoc);
        await until(()=>vscode.commands.executeCommand('vscode.executeHoverProvider',thorUri,new vscode.Position(1,2)),values=>textOf(values).includes('fn'));
        result.checks.push('TLS still serves Thor alongside OLS');
        result.success=true;
    }catch(error){result.error=error.stack||String(error);throw error;}
    finally {fs.writeFileSync(process.env.THOR_INSTALL_GUI_RESULT,JSON.stringify(result,null,2));}
}
module.exports={run};
