'use strict';
// Runs inside real VS Code, loading the installed Thor VSIX, not its source tree.
const vscode=require('vscode');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const path=require('node:path');
const delay=ms=>new Promise(resolve=>setTimeout(resolve,ms));
const timings={};
async function until(label,callback,ms=90000) {
    const started=Date.now(),deadline=started+ms;let last;
    while(Date.now()<deadline){try{const value=await callback();if(value){timings[label]=Date.now()-started;return value;}}catch(e){last=e;}await delay(200);}
    throw new Error(`${label} timed out${last ? ': '+last.message : ''}`);
}
async function run() {
    const checks=[];
    const result=process.env.THOR_INSTALL_GUI_RESULT;
    // Fresh profiles use the template. Lifecycle callers can instead assert
    // the known theme retained by an existing profile after reinstall.
    const expectedTheme=process.env.THOR_INSTALL_GUI_EXPECTED_THEME||'Dark Modern';
    const check=label=>{checks.push(label);fs.writeFileSync(result,JSON.stringify({success:false,in_progress:true,checks,timings},null,2));};
    try {
        const extension=vscode.extensions.getExtension('thor-local.thor-language');
        assert.ok(extension,'Installed Thor extension is missing');
        assert.ok(!extension.extensionPath.startsWith('/source'),'Loaded checkout rather than installed VSIX');
        await extension.activate();check('installed-extension-activation');
        const folder=vscode.workspace.workspaceFolders[0];
        const odin=vscode.extensions.getExtension('DanielGavin.ols');
        assert.ok(odin,'Standard Odin Language Server extension is missing');
        const odinFile=path.join(folder.uri.fsPath,'odin_highlight.odin');
        const odinSource=fs.readFileSync(odinFile,'utf8');
        check('native-odin-fixture-present');
        const odinDocument=await vscode.workspace.openTextDocument(odinFile);
        check('native-odin-document-opened');
        assert.equal(odinDocument.languageId,'odin');
        assert.ok(odin.packageJSON.contributes.grammars.some(grammar=>grammar.language==='odin'));
        assert.ok(!extension.packageJSON.contributes.languages.some(language=>language.id==='odin'));
        const odinIcon=odin.packageJSON.contributes.languages.find(language=>language.id==='odin').icon;
        for(const theme of ['light','dark'])assert.ok(fs.existsSync(path.join(odin.extensionPath,odinIcon[theme])));
        check('standard-ols-owns-odin-language-grammar-and-icons');
        {
            const server=vscode.workspace.getConfiguration('ols').get('server.path');
            assert.ok(server&&fs.existsSync(server),'Installer must select a native OLS server');
            await odin.activate();
            await until('automatic native OLS hover',async()=>{
                const values=await vscode.commands.executeCommand('vscode.executeHoverProvider',odinDocument.uri,
                    odinDocument.positionAt(odinSource.indexOf('record :=')+1));
                return values?.some(v=>v.contents.some(c=>String(c.value||c).includes('Installer_Odin_Record')));
            });
            const tokens=await until('automatic native OLS semantic tokens',()=>vscode.commands.executeCommand('vscode.provideDocumentSemanticTokens',odinDocument.uri));
            assert.ok(tokens.data.length);
            await until('automatic native OLS completion',async()=>{
                const response=await vscode.commands.executeCommand('vscode.executeCompletionItemProvider',odinDocument.uri,
                    odinDocument.positionAt(odinSource.indexOf('record.number')+'record.'.length));
                return response?.items.some(item=>(typeof item.label==='string'?item.label:item.label.label)==='number');
            });
            check('automatic-native-ols-build-and-language-services');
        }

        // Code itself starts without the installed bin on PATH. Exercise a new
        // terminal, rather than the absolute compiler path used by Thor: Run.
        const terminalOutput=path.join(folder.uri.fsPath,'terminal-version.txt');
        const terminalStatus=path.join(folder.uri.fsPath,'terminal-status.txt');
        fs.rmSync(terminalOutput,{force:true});fs.rmSync(terminalStatus,{force:true});
        const quote=value=>"'"+value.replace(/'/g,"'\\''")+"'";
        let terminal;
        if(process.platform==='win32') {
            const script=path.join(folder.uri.fsPath,'terminal-probe.cmd');
            fs.writeFileSync(script,`@echo off\r\ncall thor version >"${terminalOutput}" 2>&1\r\nif errorlevel 1 (echo fail>"${terminalStatus}") else (echo ok>"${terminalStatus}")\r\n`);
            terminal=vscode.window.createTerminal({name:'Installer PATH check',shellPath:process.env.ComSpec||'C:\\Windows\\System32\\cmd.exe',shellArgs:['/d','/c',script]});
        } else {
            terminal=vscode.window.createTerminal({name:'Installer PATH check',shellPath:'/bin/sh',
                shellArgs:['-c',`if thor version > ${quote(terminalOutput)} 2>&1; then printf ok > ${quote(terminalStatus)}; else printf fail > ${quote(terminalStatus)}; fi`]});
        }
        try {
            await until('terminal PATH',()=>fs.existsSync(terminalStatus));
            assert.equal(fs.readFileSync(terminalStatus,'utf8').trim(),'ok',fs.readFileSync(terminalOutput,'utf8'));
            assert.match(fs.readFileSync(terminalOutput,'utf8'),/Thor/);
        } finally {
            terminal.dispose();
            // This was a one-shot PATH probe, not an interactive shell for
            // Thor: Run. Wait for its asynchronous close notification before
            // selecting the next terminal in the integration test.
            await until('terminal PATH probe close',()=>!vscode.window.terminals.includes(terminal));
        }
        check('terminal-path-without-shell-profile');
        const source=path.join(folder.uri.fsPath,'editor.thor');
        const document=await vscode.workspace.openTextDocument(source);
        const editor=await vscode.window.showTextDocument(document);
        const text=document.getText();
        function position(needle,delta=0){return document.positionAt(text.indexOf(needle)+delta);}
        const hover=await until('TLS hover',async()=>{
            const values=await vscode.commands.executeCommand('vscode.executeHoverProvider',document.uri,position('person.name'));
            return values?.some(v=>v.contents.some(c=>String(c.value||c).includes('Person'))) && values;
        });
        assert.ok(hover.length);check('tls-hover-private-sdk');
        const completions=await until('TLS fields',async()=>{
            const response=await vscode.commands.executeCommand('vscode.executeCompletionItemProvider',document.uri,position('person.name','person.'.length));
            const names=response?.items.map(i=>typeof i.label==='string'?i.label:i.label.label)||[];
            return names.includes('name')&&names.includes('age')&&names;
        });
        assert.ok(completions.includes('age'));check('tls-field-completion');
        const palette=await until('optional colors',()=>vscode.workspace.getConfiguration('editor').get('semanticTokenColorCustomizations')?.rules);
        assert.equal(palette['variable.thorCompileTime:thor'],'#8582DE');
        assert.equal(vscode.workspace.getConfiguration('workbench').get('colorTheme'),expectedTheme);
        check('profile-theme-and-thor-only-colors');
        await vscode.commands.executeCommand('thor.restoreRecommendedColors');
        assert.equal(vscode.workspace.getConfiguration('workbench').get('colorTheme'),expectedTheme);
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
        check('optional-color-enable-and-restore');
        const runMarker=path.join(folder.uri.fsPath,'ran.txt');
        // A prior CLI smoke run must not satisfy the editor Run check.
        fs.rmSync(runMarker,{force:true});
        const runDiagnostics={shell:vscode.env.shell,commands:[],output:''};
        const runOutput=vscode.window.onDidStartTerminalShellExecution(event=>{
            runDiagnostics.commands.push(event.execution.commandLine.value);
            void (async()=>{for await(const chunk of event.execution.read())runDiagnostics.output+=chunk;})()
                .catch(error=>{runDiagnostics.readError=String(error);});
        });
        try {
            const runTerminal=await vscode.commands.executeCommand('thor.run');
            runDiagnostics.options=runTerminal?.creationOptions;
            await until('Cmd+R program',()=>fs.existsSync(runMarker));
        } catch(error) {
            fs.writeFileSync(path.join(path.dirname(result),'run-diagnostics.json'),JSON.stringify(runDiagnostics,null,2));
            throw error;
        } finally {runOutput.dispose();}
        assert.equal(fs.readFileSync(runMarker,'utf8'),'ok');
        check('run-without-shell-path');
        // Use the regular Thor command and a source breakpoint. The test launch
        // opts out of ASLR disabling because Docker restricts personality(2).
        let stopped,terminated=false;
        const tracker=vscode.debug.registerDebugAdapterTrackerFactory('thor-lldb',{createDebugAdapterTracker:()=>({onDidSendMessage:m=>{if(m.event==='stopped')stopped=m.body;}})});
        const end=vscode.debug.onDidTerminateDebugSession(()=>{terminated=true;});
        const breakpoint=new vscode.SourceBreakpoint(new vscode.Location(document.uri,position('person :>')));
        vscode.debug.addBreakpoints([breakpoint]);
        const started=await vscode.commands.executeCommand('thor.debug');
        assert.equal(started,true);
        await until('mapped source breakpoint',()=>stopped,90000);
        check('codelldb-source-breakpoint');
        const session=vscode.debug.activeDebugSession;
        const frames=await session.customRequest('stackTrace',{threadId:stopped.threadId,startFrame:0,levels:5});
        assert.ok(frames.stackFrames.some(f=>f.source?.path===source),'Breakpoint must resolve to original Thor source');
        await session.customRequest('continue',{threadId:stopped.threadId});
        await until('debug program exit',()=>terminated);
        tracker.dispose();end.dispose();vscode.debug.removeBreakpoints([breakpoint]);
        check('debug-clean-exit');
        fs.writeFileSync(result,JSON.stringify({success:true,checks,timings,expectedTheme},null,2));
    } catch(error) {
        fs.writeFileSync(result,JSON.stringify({success:false,checks,timings,error:error.stack||String(error)},null,2));
        throw error;
    }
}
module.exports={run};
