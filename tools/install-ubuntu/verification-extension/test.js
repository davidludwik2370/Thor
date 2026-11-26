'use strict';
// Runs inside real VS Code, loading the installed Thor VSIX, not its source tree.
const vscode=require('vscode');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const path=require('node:path');
const delay=ms=>new Promise(resolve=>setTimeout(resolve,ms));
async function until(label,callback,ms=30000) {
    const deadline=Date.now()+ms;let last;
    while(Date.now()<deadline){try{const value=await callback();if(value)return value;}catch(e){last=e;}await delay(200);}
    throw new Error(`${label} timed out${last ? ': '+last.message : ''}`);
}
async function run() {
    const checks=[];
    const result=process.env.THOR_INSTALL_GUI_RESULT;
    try {
        const extension=vscode.extensions.getExtension('thor-local.thor-language');
        assert.ok(extension,'Installed Thor extension is missing');
        assert.ok(!extension.extensionPath.startsWith('/source'),'Loaded checkout rather than installed VSIX');
        await extension.activate();checks.push('installed-extension-activation');
        const folder=vscode.workspace.workspaceFolders[0];
        const odin=vscode.extensions.getExtension('DanielGavin.ols');
        assert.ok(odin,'Standard Odin Language Server extension is missing');
        const odinFile=path.join(folder.uri.fsPath,'odin_highlight.odin');
        const odinSource='package main\nInstaller_Odin_Record :: struct {number: int}\ninstaller_odin_identity::proc(value:$T)->T{return value}\ninstaller_odin_probe::proc(){record:=installer_odin_identity(Installer_Odin_Record{1});_=record.number}\n';
        fs.writeFileSync(odinFile,odinSource);
        const odinDocument=await vscode.workspace.openTextDocument(odinFile);
        assert.equal(odinDocument.languageId,'odin');
        assert.ok(odin.packageJSON.contributes.grammars.some(grammar=>grammar.language==='odin'));
        assert.ok(!extension.packageJSON.contributes.languages.some(language=>language.id==='odin'));
        const odinIcon=odin.packageJSON.contributes.languages.find(language=>language.id==='odin').icon;
        for(const theme of ['light','dark'])assert.ok(fs.existsSync(path.join(odin.extensionPath,odinIcon[theme])));
        checks.push('standard-ols-owns-odin-language-grammar-and-icons');
        {
            const server=vscode.workspace.getConfiguration('ols').get('server.path');
            assert.ok(server&&fs.existsSync(server),'Installer must select a native OLS server');
            await odin.activate();
            await until('automatic native OLS hover',async()=>{
                const values=await vscode.commands.executeCommand('vscode.executeHoverProvider',odinDocument.uri,
                    odinDocument.positionAt(odinSource.indexOf('record:=')+1));
                return values?.some(v=>v.contents.some(c=>String(c.value||c).includes('Installer_Odin_Record')));
            });
            const tokens=await until('automatic native OLS semantic tokens',()=>vscode.commands.executeCommand('vscode.provideDocumentSemanticTokens',odinDocument.uri));
            assert.ok(tokens.data.length);
            await until('automatic native OLS completion',async()=>{
                const response=await vscode.commands.executeCommand('vscode.executeCompletionItemProvider',odinDocument.uri,
                    odinDocument.positionAt(odinSource.indexOf('record.number')+'record.'.length));
                return response?.items.some(item=>(typeof item.label==='string'?item.label:item.label.label)==='number');
            });
            checks.push('automatic-native-ols-build-and-language-services');
        }

        // Code itself starts without the installed bin on PATH. Exercise a new
        // terminal, rather than the absolute compiler path used by Thor: Run.
        const terminalOutput=path.join(folder.uri.fsPath,'terminal-version.txt');
        const terminalStatus=path.join(folder.uri.fsPath,'terminal-status.txt');
        fs.rmSync(terminalOutput,{force:true});fs.rmSync(terminalStatus,{force:true});
        const quote=value=>"'"+value.replace(/'/g,"'\\''")+"'";
        const terminal=vscode.window.createTerminal({name:'Installer PATH check',shellPath:'/bin/sh',
            shellArgs:['-c',`if thor version > ${quote(terminalOutput)} 2>&1; then printf ok > ${quote(terminalStatus)}; else printf fail > ${quote(terminalStatus)}; fi`]});
        try {
            await until('terminal PATH',()=>fs.existsSync(terminalStatus));
            assert.equal(fs.readFileSync(terminalStatus,'utf8'),'ok',fs.readFileSync(terminalOutput,'utf8'));
            assert.match(fs.readFileSync(terminalOutput,'utf8'),/Thor/);
        } finally {terminal.dispose();}
        checks.push('terminal-path-without-shell-profile');
        const source=path.join(folder.uri.fsPath,'editor.thor');
        const document=await vscode.workspace.openTextDocument(source);
        const editor=await vscode.window.showTextDocument(document);
        const text=document.getText();
        function position(needle,delta=0){return document.positionAt(text.indexOf(needle)+delta);}
        const hover=await until('TLS hover',async()=>{
            const values=await vscode.commands.executeCommand('vscode.executeHoverProvider',document.uri,position('person.name'));
            return values?.some(v=>v.contents.some(c=>String(c.value||c).includes('Person'))) && values;
        });
        assert.ok(hover.length);checks.push('tls-hover-private-sdk');
        const completions=await until('TLS fields',async()=>{
            const response=await vscode.commands.executeCommand('vscode.executeCompletionItemProvider',document.uri,position('person.name','person.'.length));
            const names=response?.items.map(i=>typeof i.label==='string'?i.label:i.label.label)||[];
            return names.includes('name')&&names.includes('age')&&names;
        });
        assert.ok(completions.includes('age'));checks.push('tls-field-completion');
        const palette=await until('optional colors',()=>vscode.workspace.getConfiguration('editor').get('semanticTokenColorCustomizations')?.rules);
        assert.equal(palette['variable.thorCompileTime:thor'],'#8582DE');
        assert.equal(vscode.workspace.getConfiguration('workbench').get('colorTheme'),'Dark Modern');
        checks.push('profile-theme-and-thor-only-colors');
        await vscode.commands.executeCommand('thor.restoreRecommendedColors');
        assert.equal(vscode.workspace.getConfiguration('workbench').get('colorTheme'),'Dark Modern');
        // The installer opens a real profile window before the test host. Its
        // startup activation can already add colors; extension-test hosts use
        // separate mementos and cannot claim that other host's ownership. Clear
        // only that preset in this disposable fixture profile, then exercise
        // enable/restore within one host. Preserve unrelated settings/rules.
        const colorConfig=vscode.workspace.getConfiguration('editor');
        const raw=colorConfig.inspect('semanticTokenColorCustomizations')?.globalValue;
        if (raw?.rules) {
            const rules={...raw.rules};
            for (const key of ['variable.thorCompileTime:thor','label:thor','parameter.readonly:thor','property.readonly:thor']) {
                if (rules[key]===palette[key]) delete rules[key];
            }
            await colorConfig.update('semanticTokenColorCustomizations',{...raw,rules},vscode.ConfigurationTarget.Global);
        }
        await vscode.commands.executeCommand('thor.applyRecommendedColors');
        await until('color preset enable',()=>vscode.workspace.getConfiguration('editor').get('semanticTokenColorCustomizations')?.rules?.['variable.thorCompileTime:thor']==='#8582DE');
        await vscode.commands.executeCommand('thor.restoreRecommendedColors');
        await until('color preset restore',()=>!vscode.workspace.getConfiguration('editor').get('semanticTokenColorCustomizations')?.rules?.['variable.thorCompileTime:thor']);
        await vscode.commands.executeCommand('thor.applyRecommendedColors');
        checks.push('optional-color-enable-and-restore');
        const runMarker=path.join(folder.uri.fsPath,'ran.txt');
        // A prior CLI smoke run must not satisfy the editor Run check.
        fs.rmSync(runMarker,{force:true});
        await vscode.commands.executeCommand('thor.run');
        await until('Cmd+R program',()=>fs.existsSync(runMarker));
        assert.equal(fs.readFileSync(runMarker,'utf8'),'ok');
        checks.push('run-without-shell-path');
        // Use the regular Thor command and a source breakpoint. The test launch
        // opts out of ASLR disabling because Docker restricts personality(2).
        let stopped,terminated=false;
        const tracker=vscode.debug.registerDebugAdapterTrackerFactory('thor-lldb',{createDebugAdapterTracker:()=>({onDidSendMessage:m=>{if(m.event==='stopped')stopped=m.body;}})});
        const end=vscode.debug.onDidTerminateDebugSession(()=>{terminated=true;});
        const breakpoint=new vscode.SourceBreakpoint(new vscode.Location(document.uri,position('person :>')));
        vscode.debug.addBreakpoints([breakpoint]);
        const started=await vscode.commands.executeCommand('thor.debug');
        assert.equal(started,true);
        await until('mapped source breakpoint',()=>stopped,45000);
        checks.push('codelldb-source-breakpoint');
        const session=vscode.debug.activeDebugSession;
        const frames=await session.customRequest('stackTrace',{threadId:stopped.threadId,startFrame:0,levels:5});
        assert.ok(frames.stackFrames.some(f=>f.source?.path===source),'Breakpoint must resolve to original Thor source');
        await session.customRequest('continue',{threadId:stopped.threadId});
        await until('debug program exit',()=>terminated);
        tracker.dispose();end.dispose();vscode.debug.removeBreakpoints([breakpoint]);
        checks.push('debug-clean-exit');
        fs.writeFileSync(result,JSON.stringify({success:true,checks},null,2));
    } catch(error) {
        fs.writeFileSync(result,JSON.stringify({success:false,checks,error:error.stack||String(error)},null,2));
        throw error;
    }
}
module.exports={run};
