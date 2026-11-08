// Run in an isolated VS Code Extension Development Host; see tools/test_vscode.py.
'use strict';
const vscode = require('vscode');
const assert = require('node:assert/strict');
const path = require('node:path');
const fs = require('node:fs');
const os = require('node:os');

async function eventually(action, predicate) {
    const deadline = Date.now() + 60000;
    let result;
    do {
        result = await action();
        if (predicate(result)) return result;
        await new Promise(resolve => setTimeout(resolve, 250));
    } while (Date.now() < deadline);
    assert.fail(`Language provider did not become ready: ${JSON.stringify(result)}`);
}

function hoverText(values) {
    return values.flatMap(value=>value.contents.map(content=>typeof content==='string'?content:content.value||'')).join('\n');
}

async function run() {
    const root = path.resolve(__dirname, '../../..');
    const result = { success: false };
    const temporary = fs.mkdtempSync(path.join(os.tmpdir(), 'thor-editor-diagnostics-'));
    try {
        await vscode.commands.executeCommand('workbench.action.closeAllEditors');
        assert.equal(vscode.window.activeTextEditor, undefined);
        const extension = vscode.extensions.getExtension('thor-local.thor-language');
        assert.ok(extension);
        await extension.activate();
        const document = await vscode.workspace.openTextDocument(vscode.Uri.file(path.join(root, 'demo/main.thor')));
        await vscode.window.showTextDocument(document);
        assert.equal(document.languageId, 'thor');
        const codeActionRange = new vscode.Range(0, 0, 0, 0);
        const earlyActions = await vscode.commands.executeCommand('vscode.executeCodeActionProvider', document.uri, codeActionRange);
        assert.ok(Array.isArray(earlyActions));
        assert.deepEqual(extension.packageJSON.contributes.semanticTokenScopes,
            [{language:'thor',scopes:{namespace:['variable.other.namespace.thor'],'parameter.readonly':['variable.other.constant.thor'],label:['entity.name.label.thor']}}]);
        const colorConfig=vscode.workspace.getConfiguration('editor');
        const colorBaseline=colorConfig.inspect('semanticTokenColorCustomizations')?.globalValue;
        try {
            await vscode.commands.executeCommand('thor.previewConstantColor','#8582DE');
            assert.equal(colorConfig.inspect('semanticTokenColorCustomizations').globalValue.rules['variable.thorCompileTime:thor'],'#8582DE');
            await vscode.commands.executeCommand('thor.previewConstantColor','#8877DD');
            assert.equal(colorConfig.inspect('semanticTokenColorCustomizations').globalValue.rules['variable.thorCompileTime:thor'],'#8877DD');
        } finally {
            await vscode.commands.executeCommand('thor.restoreConstantColor');
        }
        assert.deepEqual(colorConfig.inspect('semanticTokenColorCustomizations')?.globalValue,colorBaseline,
            'Restoring constant colors must restore the original user preference exactly');
        const source = document.getText();
        const offset = source.indexOf('numbers :=') + 2;
        const hover = await eventually(() => vscode.commands.executeCommand('vscode.executeHoverProvider', document.uri, document.positionAt(offset)),
            values => values?.some(value => value.contents.some(content => (content.value || '').includes('[3]int'))));
        assert.ok(hover.length);
        const packageHover = await eventually(() => vscode.commands.executeCommand('vscode.executeHoverProvider', document.uri,
            document.positionAt(source.indexOf('package main')+'package '.length+1)),
            values => hoverText(values || []).includes('main: package'));
        assert.ok(hoverText(packageHover).includes('main: package'));
        const noFixActions = await vscode.commands.executeCommand('vscode.executeCodeActionProvider', document.uri, codeActionRange);
        assert.ok(Array.isArray(noFixActions));
        const symbolsAfterActions = await vscode.commands.executeCommand('vscode.executeDocumentSymbolProvider', document.uri);
        assert.ok(symbolsAfterActions.some(symbol => symbol.name === 'main'));
        const commentHover = await vscode.commands.executeCommand('vscode.executeHoverProvider', document.uri, new vscode.Position(0,0));
        assert.equal(commentHover.length,0,'A hidden set import must not create a package hover on the first comment');
        for(const [fragment,expected] of [['names: sets.Set(string)','names: set[string]'],['other_names := sets.clone','other_names: set[string]']]){
            const setHover=await eventually(()=>vscode.commands.executeCommand('vscode.executeHoverProvider',document.uri,
                document.positionAt(source.indexOf(fragment)+2)),
                values=>values?.some(value=>value.contents.some(content=>(content.value||'').includes(expected))));
            assert.ok(!hoverText(setHover).includes('sets.Set('));
            assert.ok(hoverText(setHover).includes('```thor'),hoverText(setHover));
        }
        const insertPosition=document.positionAt(source.indexOf('sets.insert(&names')+'sets.'.length+2);
        const insertHover=await eventually(()=>vscode.commands.executeCommand('vscode.executeHoverProvider',document.uri,insertPosition),
            values=>values?.some(value=>value.contents.some(content=>(content.value||'').includes('set: ^set[$T]'))));
        assert.ok(!hoverText(insertHover).includes('Set('));
        const legend=await eventually(()=>vscode.commands.executeCommand('vscode.provideDocumentSemanticTokensLegend',document.uri),value=>value?.tokenTypes?.length>0);
        const semantic=await eventually(()=>vscode.commands.executeCommand('vscode.provideDocumentSemanticTokens',document.uri),value=>value?.data?.length>0);
        let line=0,column=0,previousEnd=0;
        const semanticByOffset=new Map();
        for(let i=0;i<semantic.data.length;i+=5){
            const [delta,char,length,kind]=semantic.data.slice(i,i+4);
            line+=delta;column=delta?char:column+char;
            if(delta)previousEnd=0;
            assert.ok(column>=previousEnd,'Overlapping semantic token');
            assert.ok(column+length<=document.lineAt(line).text.length,'Semantic token extends past source line');
            const start=document.offsetAt(new vscode.Position(line,column));
            const token=source.slice(start,start+length);
            assert.match(token,/^[\p{L}_][\p{L}\p{N}_]*$/u,'Semantic token splits an identifier or covers punctuation');
            semanticByOffset.set(start,{type:legend.tokenTypes[kind],token});
            previousEnd=column+length;
        }
        for(const [fragment,name,type] of [['either := fruit ~ more_fruit','fruit','variable'],['case 3..<5: [.Red','.Red','enumMember'],['PURE_REPORT ::','PURE_REPORT','variable'],['report := PURE_REPORT','PURE_REPORT','variable'],['Owned_Report ::','Owned_Report','struct']]){
            const start=source.indexOf(fragment)+fragment.indexOf(name)+(name.startsWith('.')?1:0);
            assert.deepEqual(semanticByOffset.get(start),{type,token:name.replace(/^\./,'')});
        }
        const printOffset = source.indexOf('println("T01') + 2;
        const definition = await eventually(() => vscode.commands.executeCommand('vscode.executeDefinitionProvider', document.uri, document.positionAt(printOffset)),
            values => values?.length > 0);
        assert.ok(definition.some(value => (value.targetUri || value.uri).path.includes('/core/fmt/')),
            `Selected import did not resolve to its original package: ${JSON.stringify(definition)}`);
        const odin = await vscode.workspace.openTextDocument(vscode.Uri.file(path.join(root, 'demo/support.odin')));
        assert.notEqual(odin.languageId, 'thor');
        const testPath = path.join(temporary, 'main.thor');
        const initial = 'package main\nmain :: proc(){}\n';
        fs.writeFileSync(testPath, initial);
        const testDocument = await vscode.workspace.openTextDocument(vscode.Uri.file(testPath));
        await vscode.window.showTextDocument(testDocument);
        const changed = 'package main\nmain :: proc(){ _ = "😀"; a := [1,"wrong"]; _ = a }\n';
        const replace = async text => {
            const edit = new vscode.WorkspaceEdit();
            edit.replace(testDocument.uri, new vscode.Range(testDocument.positionAt(0), testDocument.positionAt(testDocument.getText().length)), text);
            const editApplied=await vscode.workspace.applyEdit(edit);
            assert.ok(editApplied);
        };
        const sliceAccess='package main\ncopy_values::proc(source,destination:[]int){copy(destination,source)}\nexplicit::proc(a,#mut b:[]int){_=a;_=b}\nmain::proc(){values :> []int{1};output:=[]int{0};copy_values(values,output);explicit(values,output)}\n';
        await replace(sliceAccess);
        await eventually(()=>Promise.resolve(vscode.languages.getDiagnostics(testDocument.uri)),values=>values.length===0);
        const sliceHints=async()=>vscode.commands.executeCommand('vscode.executeInlayHintProvider',testDocument.uri,
            new vscode.Range(testDocument.positionAt(0),testDocument.positionAt(testDocument.getText().length)));
        const mutHints=values=>(values||[]).filter(hint=>(typeof hint.label==='string'?hint.label:hint.label.map(part=>part.value).join('')).includes('#mut'));
        const inferredHints=await eventually(sliceHints,values=>mutHints(values).length===1);
        assert.ok(mutHints(inferredHints)[0].position.isEqual(testDocument.positionAt(sliceAccess.indexOf('destination:'))));
        await eventually(()=>vscode.commands.executeCommand('vscode.executeHoverProvider',testDocument.uri,
            testDocument.positionAt(sliceAccess.indexOf('copy_values(values')+1)),values=>hoverText(values||[]).includes('#mut destination'));
        const sliceError=sliceAccess.replace('copy_values(values,output)','copy_values(output,values)');
        await replace(sliceError);
        const accessErrors=await eventually(()=>Promise.resolve(vscode.languages.getDiagnostics(testDocument.uri)),values=>values.some(value=>value.message.includes('read-only slice')));
        const accessError=accessErrors.find(value=>value.message.includes('read-only slice'));
        assert.ok(accessError.range.start.isEqual(testDocument.positionAt(sliceError.indexOf('values)',sliceError.indexOf('copy_values(output')))));
        assert.ok(accessError.relatedInformation?.length>0);
        await replace(sliceAccess.replace('copy(destination,source)','_=source;_=destination'));
        await eventually(sliceHints,values=>mutHints(values).length===0);
        if(process.env.THOR_VSCODE_TEST_SCOPE==='slice-access'){
            await vscode.commands.executeCommand('workbench.action.revertAndCloseActiveEditor');
            await vscode.commands.executeCommand('workbench.action.closeAllEditors');
            result.success=true;
            result.checks=['demo language/hover/semantic tokens and source definitions',
                'inferred per-name #mut inlay and explicit annotation deduplication',
                'hover write contract', 'read-only argument diagnostic and related locations',
                'unsaved reader edit removes inferred #mut', 'refresh replies preserve the server process'];
            return;
        }
        fs.mkdirSync(path.join(temporary,'completion_library'));
        fs.writeFileSync(path.join(temporary,'completion_library/main.thor'),'package completion_library\n// Alpha accepts a number.\nalpha::proc(value:int)->int{return value}\nalpine::proc(){}\n@(private) hidden::proc(){}\n');
        const importExample='package main\nimport "completion_library" / \nmain::proc(){}\n';
        await replace(importExample);
        const importPosition=testDocument.positionAt(importExample.indexOf(' / ')+3);
        const importLabel=item=>typeof item.label==='string'?item.label:item.label.label;
        const importItems=await eventually(()=>vscode.commands.executeCommand('vscode.executeCompletionItemProvider',testDocument.uri,importPosition,'/'),
            value=>value?.items.some(item=>importLabel(item)==='alpha'));
        const importedAlpha=importItems.items.find(item=>importLabel(item)==='alpha');
        assert.equal(importedAlpha.insertText,'alpha');
        assert.ok(!importItems.items.some(item=>importLabel(item)==='hidden'));
        assert.ok((importedAlpha.documentation?.value||importedAlpha.documentation||'').includes('Alpha accepts a number'));
        const acceptImportSuggestion=async(initial,characters,expected)=>{
            await vscode.commands.executeCommand('hideSuggestWidget');
            await replace(initial);
            const editor=await vscode.window.showTextDocument(testDocument,{preserveFocus:false});
            const at=testDocument.positionAt(initial.indexOf('\nmain::'));
            editor.selection=new vscode.Selection(at,at);
            if(process.env.THOR_VSCODE_INTERACTIVE_TYPING==='1'){
                // Optional foreground test of the real suggestion widget.
                for(const text of characters)await vscode.commands.executeCommand('default:type',{text});
                await eventually(async()=>{
                    await vscode.commands.executeCommand('acceptSelectedSuggestion');
                    return testDocument.getText();
                },text=>text.includes(expected));
            }else{
                // Background extension hosts do not reliably receive keyboard
                // commands. Exercise the real client completion/edit conversion
                // without depending on OS focus or a visible suggestion widget.
                const inserted=characters.join('');
                const edited=await editor.edit(edit=>edit.insert(at,inserted));
                assert.ok(edited);
                const cursor=testDocument.positionAt(testDocument.offsetAt(at)+inserted.length);
                const prefix=testDocument.lineAt(cursor.line).text.slice(0,cursor.character).match(/[\p{L}_][\p{L}\p{N}_]*$/u)?.[0]||'';
                const list=await eventually(()=>vscode.commands.executeCommand('vscode.executeCompletionItemProvider',testDocument.uri,cursor),
                    value=>value?.items.some(item=>importLabel(item).startsWith(prefix)));
                const items=list.items.filter(item=>importLabel(item).startsWith(prefix))
                    .sort((a,b)=>(a.sortText||importLabel(a)).localeCompare(b.sortText||importLabel(b)));
                const item=items[0];
                const range=item.range?.replacing||item.range||testDocument.getWordRangeAtPosition(cursor)||new vscode.Range(cursor,cursor);
                const edit=new vscode.WorkspaceEdit();
                edit.replace(testDocument.uri,range,item.insertText||importLabel(item));
                const applied=await vscode.workspace.applyEdit(edit);
                assert.ok(applied);
                assert.ok(testDocument.getText().includes(expected),testDocument.getText());
            }
        };
        await acceptImportSuggestion(importExample.replace(' / ',' '),['/',' '],' / alpha\n');
        await acceptImportSuggestion(importExample.replace(' / ',' / alpha'),[',',' '],' / alpha, alpine\n');
        await acceptImportSuggestion(importExample.replace(' / ',' '),['/',' ','a','l','p','i'],' / alpine\n');
        const precisionExample='package main\nmain::proc(){ratio:=Rational(1,3);text:=f"😀 {ratio:.6g}";delete(text);delete(ratio)}\n';
        await replace(precisionExample);
        await eventually(()=>vscode.commands.executeCommand('vscode.executeHoverProvider',testDocument.uri,
            testDocument.positionAt(precisionExample.indexOf('ratio:.6g')+1)),values=>hoverText(values||[]).includes('Rational'));
        const invalidPrecision=precisionExample.replace('ratio:.6g','ratio:g');
        await replace(invalidPrecision);
        const precisionDiagnostic=await eventually(()=>Promise.resolve(vscode.languages.getDiagnostics(testDocument.uri)),
            values=>values.some(v=>v.message.includes('requires explicit precision')));
        const rationalError=precisionDiagnostic.find(v=>v.message.includes('requires explicit precision'));
        assert.ok(rationalError.range.start.isEqual(testDocument.positionAt(invalidPrecision.indexOf('ratio:g'))));
        assert.ok(!rationalError.message.includes('runtime/'));
        await replace(precisionExample);
        await eventually(()=>Promise.resolve(vscode.languages.getDiagnostics(testDocument.uri)),values=>values.length===0);
        const widthExample='package main\nmain::proc(){width:>6;text:=f"{12:{width}}";delete(text)}\n';
        await replace(widthExample);
        const widthPosition=testDocument.positionAt(widthExample.indexOf('width}}')+1);
        await eventually(()=>vscode.commands.executeCommand('vscode.executeHoverProvider',testDocument.uri,widthPosition),values=>hoverText(values||[]).includes('width: int'));
        const widthRename=await vscode.commands.executeCommand('vscode.executeDocumentRenameProvider',testDocument.uri,widthPosition,'padding');
        assert.equal(widthRename.get(testDocument.uri).length,2);
        const bareWidth=widthExample.replace(':{width}',':width');
        await replace(bareWidth);
        await eventually(()=>Promise.resolve(vscode.languages.getDiagnostics(testDocument.uri)),values=>values.some(v=>v.message.includes('variable width needs nested braces')));
        await eventually(()=>vscode.commands.executeCommand('vscode.executeHoverProvider',testDocument.uri,
            testDocument.positionAt(bareWidth.indexOf('width:>')+1)),values=>hoverText(values||[]).includes('width: int'));
        const bareWidthStart=bareWidth.indexOf(':width}')+1;
        await eventually(()=>vscode.commands.executeCommand('vscode.provideDocumentSemanticTokens',testDocument.uri),value=>{
            if(!value?.data?.length)return false;
            let line=0,column=0;
            for(let i=0;i<value.data.length;i+=5){
                const [delta,char,length]=value.data.slice(i,i+3);
                line+=delta;column=delta?char:column+char;
                const at=testDocument.offsetAt(new vscode.Position(line,column));
                if(at<bareWidthStart+'width'.length && at+length>bareWidthStart)return false;
            }
            return true;
        });
        const badWidth=widthExample.replace('width:>6','width:>"bad"');
        await replace(badWidth);
        const widthError=await eventually(()=>Promise.resolve(vscode.languages.getDiagnostics(testDocument.uri)),values=>values.some(v=>v.message.includes('width must have an integer type')));
        assert.ok(widthError.find(v=>v.message.includes('width must have an integer type')).range.start.isEqual(testDocument.positionAt(badWidth.indexOf('width}}'))));
        await replace(widthExample);
        await eventually(()=>Promise.resolve(vscode.languages.getDiagnostics(testDocument.uri)),values=>values.length===0);
        const lookupExample='package main\nGames::enum{roulette,blackjack}\nTABLE::[Games]f64{.roulette=0.526,.blackjack=0.503}\nGLOBAL::TABLE[.roulette]\nmain::proc(){GAME::Games.roulette;HOUSE_ODDS::TABLE[GAME];ALIAS::(TABLE[GAME]);Actual_Type::type_of(TABLE[GAME]);value:Actual_Type=HOUSE_ODDS;_=GLOBAL+ALIAS+value}\n';
        await replace(lookupExample);
        const lookupLegend=await vscode.commands.executeCommand('vscode.provideDocumentSemanticTokensLegend',testDocument.uri);
        const constantFlag=1<<lookupLegend.tokenModifiers.indexOf('thorCompileTime');
        await eventually(()=>vscode.commands.executeCommand('vscode.provideDocumentSemanticTokens',testDocument.uri),value=>{
            if(!value?.data?.length)return false;
            let line=0,column=0;
            const constants=[],types=[];
            for(let i=0;i<value.data.length;i+=5){
                const [delta,char,length,kind,flags]=value.data.slice(i,i+5);
                line+=delta;column=delta?char:column+char;
                const at=testDocument.offsetAt(new vscode.Position(line,column)),name=lookupExample.slice(at,at+length);
                if(['HOUSE_ODDS','GLOBAL','ALIAS'].includes(name))constants.push(lookupLegend.tokenTypes[kind]==='variable' && !!(flags&constantFlag));
                if(name==='Actual_Type')types.push(lookupLegend.tokenTypes[kind]==='type');
            }
            return constants.length===6 && constants.every(Boolean) && types.length===2 && types.every(Boolean);
        });
        const rootExample='// 😀\npackage main\nimport * "thor:basic"\nmain::proc(){prob_loss:f64;p_win_pad:>len(f"{(1-p_loss) * 100:.2f}");_=p_win_pad}\n';
        await replace(rootExample);
        const rootDiagnostics=await eventually(()=>Promise.resolve(vscode.languages.getDiagnostics(testDocument.uri).filter(d=>d.severity===vscode.DiagnosticSeverity.Error)),
            values=>values.length===1 && values[0].message.includes('Undeclared name: p_loss'));
        const rootStart=rootExample.indexOf('p_loss');
        assert.ok(rootDiagnostics[0].range.isEqual(new vscode.Range(testDocument.positionAt(rootStart),testDocument.positionAt(rootStart+'p_loss'.length))));
        assert.ok(!rootDiagnostics[0].range.contains(testDocument.positionAt(rootExample.indexOf('100'))));
        assert.ok(!rootDiagnostics[0].message.includes('polymorphic'));
        await replace(rootExample.replace('1-p_loss','1-prob_loss'));
        await eventually(()=>Promise.resolve(vscode.languages.getDiagnostics(testDocument.uri).filter(d=>d.severity===vscode.DiagnosticSeverity.Error)),values=>values.length===0);
        const bigExample='package main\nmain::proc(){\n huge:Integer=12345678901234567890123456789012345678901234567890\n ratio:=Rational(22,13)\n component:=ratio.numerator\n delete(huge);delete(ratio);delete(component)\n}\n';
        await replace(bigExample);
        for(const [fragment,expected] of [['huge:','huge: Integer'],['ratio:=','ratio: Rational'],['component:=','component: Integer'],['numerator','numerator: Integer']]){
            const values=await eventually(()=>vscode.commands.executeCommand('vscode.executeHoverProvider',testDocument.uri,
                testDocument.positionAt(bigExample.indexOf(fragment)+1)),values=>hoverText(values||[]).includes(expected));
            assert.ok(!hoverText(values).includes('_value'));
        }
        const bigCompletion=await eventually(()=>vscode.commands.executeCommand('vscode.executeCompletionItemProvider',testDocument.uri,
            testDocument.positionAt(bigExample.indexOf('ratio.numerator')+'ratio.'.length)),result=>result?.items.some(item=>(typeof item.label==='string'?item.label:item.label.label)==='denominator'));
        assert.ok(!bigCompletion.items.some(item=>(typeof item.label==='string'?item.label:item.label.label).startsWith('_')));
        const bracketExample='package main\nmain::proc(){\n    values:> [2]int{1,2}\n}\n';
        await replace(bracketExample);
        const commentEditor=vscode.window.activeTextEditor;
        const jumpAt=async position=>{
            commentEditor.selection=new vscode.Selection(position,position);
            await vscode.commands.executeCommand('editor.action.jumpToBracket');
            return commentEditor.selection.active;
        };
        const assertArrayPair=async()=>{
            const text=testDocument.getText();
            const open=testDocument.positionAt(text.indexOf('['));
            const close=testDocument.positionAt(text.indexOf(']'));
            await eventually(()=>jumpAt(open),position=>position.isEqual(close));
        };
        await assertArrayPair();
        commentEditor.selection=new vscode.Selection(2,0,2,testDocument.lineAt(2).text.length);
        await vscode.commands.executeCommand('editor.action.commentLine');
        assert.ok(testDocument.lineAt(2).text.trimStart().startsWith('//'));
        const commentPosition=new vscode.Position(2,testDocument.lineAt(2).text.indexOf('['));
        const functionClose=testDocument.positionAt(testDocument.getText().lastIndexOf('}'));
        await eventually(()=>jumpAt(commentPosition),position=>position.isEqual(functionClose));
        await eventually(()=>vscode.commands.executeCommand('vscode.provideDocumentSemanticTokens',testDocument.uri),value=>{
            if(!value?.data?.length)return false;
            let line=0;
            for(let i=0;i<value.data.length;i+=5){line+=value.data[i];if(line===2)return false;}
            return true;
        });
        commentEditor.selection=new vscode.Selection(2,0,2,testDocument.lineAt(2).text.length);
        await vscode.commands.executeCommand('editor.action.commentLine');
        assert.equal(testDocument.getText(),bracketExample);
        await assertArrayPair();
        // Exercise batched Cmd+/ edits, undo/redo, and explicit recovery. The
        // refresh must preserve the source buffer, TLS and language rules.
        const loopExample='package main\nmain::proc(){\n    a:=0\n    for _ in 0..<20 {\n        _ = [2]int{a,(a+1)}\n    }\n    _=a\n}\n';
        await replace(loopExample);
        const loopSource=testDocument.getText();
        const editHistory=async command=>{
            // Undo/redo route to the focused control, unlike provider commands.
            // Bracket jumps and provider waits need not leave editor focus there.
            await vscode.window.showTextDocument(testDocument,{preserveFocus:false});
            await vscode.commands.executeCommand(command);
        };
        for(let cycle=0;cycle<3;cycle++){
            commentEditor.selection=new vscode.Selection(3,0,5,testDocument.lineAt(5).text.length);
            await vscode.commands.executeCommand('editor.action.commentLine');
            const commentedSource=testDocument.getText();
            assert.ok(testDocument.lineAt(3).text.trimStart().startsWith('//'));
            await vscode.commands.executeCommand('thor.refreshBracketHighlighting');
            assert.equal(testDocument.getText(),commentedSource);
            const open=testDocument.positionAt(commentedSource.indexOf('[2]'));
            const close=testDocument.positionAt(commentedSource.lastIndexOf('}'));
            await eventually(()=>jumpAt(open),position=>position.isEqual(close));
            await editHistory('undo');
            await eventually(()=>testDocument.getText(),text=>text===loopSource);
            await assertArrayPair();
            await editHistory('redo');
            await eventually(()=>testDocument.getText(),text=>text===commentedSource);
            await eventually(()=>jumpAt(testDocument.positionAt(commentedSource.indexOf('[2]'))),position=>position.isEqual(close));
            await editHistory('undo');
            await eventually(()=>testDocument.getText(),text=>text===loopSource);
        }
        await eventually(()=>vscode.commands.executeCommand('vscode.executeHoverProvider',testDocument.uri,
            testDocument.positionAt(loopSource.indexOf('a:=')+1)),values=>hoverText(values||[]).includes('a: int'));
        for(const literal of [
            'f"fib ({i}): {a:,}"', 'f!"fib ({i}): {a:,}"',
            'f`fib ({i}): {a:,}`', 'f!`fib ({i}): {a:,}`',
            'f"outer ({f"inner ({i})"}) {a:,}"',
            '"fib (text) [text] {text}"', '`fib (text) [text] {text}`',
        ]){
            const example='package main\nimport "core:fmt"\nmain::proc(){\n    i:=1; a:=2; _=i; _=a\n    fmt.println('+literal+')\n}\n';
            await replace(example);
            const call=example.indexOf('println(')+'println'.length;
            const callEnd=example.lastIndexOf(')');
            await eventually(()=>jumpAt(testDocument.positionAt(call)),position=>position.isEqual(testDocument.positionAt(callEnd)));
            const literalOpen=example.indexOf('(',call+1),literalClose=example.indexOf(')',literalOpen);
            await eventually(()=>jumpAt(testDocument.positionAt(literalOpen)),position=>!position.isEqual(testDocument.positionAt(literalClose)));
            if(literal.includes('{i}')){
                const field=example.indexOf('{i}');
                await eventually(()=>jumpAt(testDocument.positionAt(field)),position=>position.isEqual(testDocument.positionAt(field+2)));
            }
        }
        const bindings='package main\nread::proc(input:[]int,pointer:^int){\nfor value,index in input{_=value;_=index}\nfor &cell,offset in input{cell+=1;_=cell;_=offset}\n_=pointer\n}\n';
        await replace(bindings);
        const bindingTokens=await eventually(()=>vscode.commands.executeCommand('vscode.provideDocumentSemanticTokens',testDocument.uri),
            value=>value?.data?.length>0);
        const readonly=1<<legend.tokenModifiers.indexOf('readonly');
        const constant=1<<legend.tokenModifiers.indexOf('thorCompileTime');
        const occurrences=new Map();
        let bindingLine=0,bindingColumn=0;
        for(let i=0;i<bindingTokens.data.length;i+=5){
            const [delta,char,length,,modifiers]=bindingTokens.data.slice(i,i+5);
            bindingLine+=delta;bindingColumn=delta?char:bindingColumn+char;
            const name=testDocument.lineAt(bindingLine).text.slice(bindingColumn,bindingColumn+length);
            if(!['input','pointer','value','index','cell','offset'].includes(name))continue;
            assert.equal(Boolean(modifiers&readonly),name!=='cell',name);
            assert.equal(modifiers&constant,0,name);
            occurrences.set(name,(occurrences.get(name)||0)+1);
        }
        for(const name of ['input','pointer','value','index','cell','offset'])assert.ok(occurrences.get(name)>=2,name);
        const records='package main\nRecord::struct{field:int}\nread::proc(value:Record,pointer:^Record){frozen:>value;mutable:=frozen;_=value.field;_=pointer.field;_=frozen.field;_=mutable.field}\n';
        await replace(records);
        const recordTokens=await eventually(()=>vscode.commands.executeCommand('vscode.provideDocumentSemanticTokens',testDocument.uri),
            value=>value?.data?.length>0);
        const recordFields=new Map();
        let recordLine=0,recordColumn=0;
        for(let i=0;i<recordTokens.data.length;i+=5){
            const [delta,char,length,kind,modifiers]=recordTokens.data.slice(i,i+5);
            recordLine+=delta;recordColumn=delta?char:recordColumn+char;
            if(legend.tokenTypes[kind]==='property')recordFields.set(testDocument.offsetAt(new vscode.Position(recordLine,recordColumn)),modifiers);
        }
        for(const [fragment,isReadonly] of [['value.field',true],['pointer.field',false],['frozen.field',true],['mutable.field',false]]){
            const at=records.indexOf(fragment)+fragment.indexOf('.')+1;
            assert.ok(recordFields.has(at),`Missing property token for ${fragment}`);
            assert.equal(Boolean(recordFields.get(at)&readonly),isReadonly,fragment);
        }
        const typeSwitches='package main\nRecord::struct{field:int}\nChoice::union{int,Record}\nread::proc(){source:Choice;switch selected in source{case int:_=selected;case Record:_=selected.field;case:_=selected};switch &reference in source{case int:reference=1;case Record:reference.field=2;case:_=reference}}\n';
        await replace(typeSwitches);
        const switchTokens=await eventually(()=>vscode.commands.executeCommand('vscode.provideDocumentSemanticTokens',testDocument.uri),
            value=>value?.data?.length>0);
        let switchLine=0,switchColumn=0;
        const switchOccurrences=new Map(),switchFields=new Map();
        for(let i=0;i<switchTokens.data.length;i+=5){
            const [delta,char,length,kind,modifiers]=switchTokens.data.slice(i,i+5);
            switchLine+=delta;switchColumn=delta?char:switchColumn+char;
            const name=testDocument.lineAt(switchLine).text.slice(switchColumn,switchColumn+length);
            if(name==='selected'||name==='reference'){
                assert.equal(legend.tokenTypes[kind],'variable',name);
                assert.equal(Boolean(modifiers&readonly),name==='selected',name);
                switchOccurrences.set(name,(switchOccurrences.get(name)||0)+1);
            }
            if(legend.tokenTypes[kind]==='property')switchFields.set(testDocument.offsetAt(new vscode.Position(switchLine,switchColumn)),modifiers);
        }
        for(const name of ['selected','reference'])assert.equal(switchOccurrences.get(name),4,name);
        for(const [fragment,isReadonly] of [['selected.field',true],['reference.field',false]]){
            const at=typeSwitches.indexOf(fragment)+fragment.indexOf('.')+1;
            assert.ok(switchFields.has(at),fragment);
            assert.equal(Boolean(switchFields.get(at)&readonly),isReadonly,fragment);
        }
        const labels='package main\nmain::proc(){row_check:=1;row_check:\nfor i in 0..<2{_=row_check;if i==0 do continue row_check;break row_check}}\n';
        await replace(labels);
        const labelTokens=await eventually(()=>vscode.commands.executeCommand('vscode.provideDocumentSemanticTokens',testDocument.uri),
            value=>value?.data?.length>0);
        let labelLine=0,labelColumn=0;
        const labelKinds=[];
        for(let i=0;i<labelTokens.data.length;i+=5){
            const [delta,char,length,kind,modifiers]=labelTokens.data.slice(i,i+5);
            labelLine+=delta;labelColumn=delta?char:labelColumn+char;
            if(testDocument.lineAt(labelLine).text.slice(labelColumn,labelColumn+length)!=='row_check')continue;
            const type=legend.tokenTypes[kind];labelKinds.push(type);
            assert.equal(Boolean(modifiers&readonly),type==='label');
            assert.equal(modifiers&constant,0);
        }
        assert.deepEqual(labelKinds,['variable','label','variable','label','label']);
        const immutableRename='package main\nKind::enum{Empty,Wall}\nColor::struct{r:u8}\ncolors:>[Kind]Color{.Empty={0},.Wall={255}}\nmain::proc(){_=colors[.Wall]}\n';
        await replace(immutableRename);
        const immutableRenameEdit=await eventually(()=>vscode.commands.executeCommand('vscode.executeDocumentRenameProvider',
            testDocument.uri,testDocument.positionAt(immutableRename.indexOf('colors:>')),'palette'),value=>value?.size>0);
        assert.equal(immutableRenameEdit.get(testDocument.uri).length,2);
        const immutableRenameEditApplied=await vscode.workspace.applyEdit(immutableRenameEdit);
        assert.ok(immutableRenameEditApplied);
        assert.equal(testDocument.getText(),immutableRename.replaceAll('colors','palette'));
        const repetition='package main\nX::"a"*3\nY::4*X+"z"\ntake::proc(value:cstring){}\nmain::proc(){value:=3*"ab";_=value;_=Y;take(X+" title")}\n';
        await replace(repetition);
        const repetitionHover=await eventually(()=>vscode.commands.executeCommand('vscode.executeHoverProvider',
            testDocument.uri,testDocument.positionAt(repetition.indexOf('value:=')+2)),
            values=>hoverText(values||[]).includes('value: string'));
        assert.ok(repetitionHover.length);
        const invalidRepetition=repetition.replace('3*"ab"','"ab"*(-1)');
        await replace(invalidRepetition);
        const repetitionDiagnostics=await eventually(()=>vscode.languages.getDiagnostics(testDocument.uri),
            values=>values.some(value=>value.message.includes('String repetition requires a nonnegative count')));
        const repetitionError=repetitionDiagnostics.find(value=>value.message.includes('String repetition requires a nonnegative count'));
        assert.equal(testDocument.getText(repetitionError.range),'"ab"*(-1)');
        await replace(repetition);
        await eventually(()=>vscode.languages.getDiagnostics(testDocument.uri),values=>values.length===0);
        const renameSource = 'package main\nmain::proc(){_="😀";value:=2;message:=f"value {value}";delete(message);_=value}\n';
        await replace(renameSource);
        const renameEdit = await eventually(() => vscode.commands.executeCommand('vscode.executeDocumentRenameProvider',
            testDocument.uri, testDocument.positionAt(renameSource.indexOf('value:=')+1), 'count'),
            value => value?.size > 0);
        assert.equal(renameEdit.get(testDocument.uri).length, 3);
        const renameEditApplied=await vscode.workspace.applyEdit(renameEdit);
        assert.ok(renameEditApplied);
        assert.equal(testDocument.getText(), renameSource.replace('value:=', 'count:=').replace('{value}', '{count}').replace('_=value', '_=count'));
        await eventually(() => vscode.languages.getDiagnostics(testDocument.uri), values => values.length === 0);
        const unusedImportSource = 'package main\nimport "core:fmt"\nimport "core:math/big"\nmain::proc(){fmt.println("big.Int is only text")}\n';
        await replace(unusedImportSource);
        const unused = await eventually(() => vscode.languages.getDiagnostics(testDocument.uri),
            values => values.some(value => value.code === 'Unused'));
        const unusedImport = unused.find(value => value.code === 'Unused');
        assert.equal(testDocument.getText(unusedImport.range), 'import "core:math/big"');
        assert.equal(unusedImport.severity, vscode.DiagnosticSeverity.Hint);
        assert.ok(unusedImport.tags.includes(vscode.DiagnosticTag.Unnecessary));
        await replace(unusedImportSource.replace('fmt.println(', 'value:big.Int;_=value;fmt.println('));
        await eventually(() => vscode.languages.getDiagnostics(testDocument.uri), values => values.length === 0);
        const selectedImportSource='package main\nimport "core:fmt" / print,\n    println\nmain::proc(){println("hello")}\n';
        await replace(selectedImportSource);
        const selectedUnused=await eventually(()=>vscode.languages.getDiagnostics(testDocument.uri),
            values=>values.some(value=>value.code==='Unused'&&testDocument.getText(value.range)==='print'));
        assert.ok(selectedUnused.find(value=>value.code==='Unused').tags.includes(vscode.DiagnosticTag.Unnecessary));
        const selectedColors=async()=>{
            const tokens=await vscode.commands.executeCommand('vscode.provideDocumentSemanticTokens',testDocument.uri);
            if(!tokens?.data?.length)return false;
            let line=0,column=0;
            const found=[];
            for(let i=0;i<tokens.data.length;i+=5){
                const [delta,char,length,kind]=tokens.data.slice(i,i+4);
                line+=delta;column=delta?char:column+char;
                if(line!==1&&line!==2)continue;
                found.push([testDocument.lineAt(line).text.slice(column,column+length),legend.tokenTypes[kind]]);
            }
            return JSON.stringify(found)===JSON.stringify([['print','function'],['println','function']]);
        };
        await eventually(selectedColors,Boolean);
        const selectedPrintPosition=testDocument.positionAt(selectedImportSource.indexOf('/ print')+3);
        const importedPrintHover=await eventually(()=>vscode.commands.executeCommand('vscode.executeHoverProvider',
            testDocument.uri,selectedPrintPosition),values=>hoverText(values||[]).includes('fmt.print :: proc('));
        assert.ok(hoverText(importedPrintHover).includes('Formats'));
        assert.equal(testDocument.getText(importedPrintHover[0].range),'print');
        await replace(selectedImportSource.replace('println("hello")','print("used");println("hello")'));
        await eventually(()=>vscode.languages.getDiagnostics(testDocument.uri),values=>values.length===0);
        const calledPrintHover=await eventually(()=>vscode.commands.executeCommand('vscode.executeHoverProvider',
            testDocument.uri,testDocument.positionAt(testDocument.getText().indexOf('print("used")')+1)),
            values=>hoverText(values||[]).includes('fmt.print :: proc('));
        assert.equal(hoverText(importedPrintHover),hoverText(calledPrintHover));
        const allSelectedUnused='package main\nimport "core:fmt" / print\nmain::proc(){}\n';
        await replace(allSelectedUnused);
        await eventually(()=>vscode.languages.getDiagnostics(testDocument.uri),values=>values.some(value=>
            value.code==='Unused'&&testDocument.getText(value.range)==='import "core:fmt" / print'));
        const unusedPrintHover=await eventually(()=>vscode.commands.executeCommand('vscode.executeHoverProvider',
            testDocument.uri,testDocument.positionAt(allSelectedUnused.indexOf('/ print')+3)),
            values=>hoverText(values||[]).includes('fmt.print :: proc('));
        assert.equal(hoverText(unusedPrintHover),hoverText(importedPrintHover));
        const genericLoop = 'package main\nasc::proc(first:$T,rest:..T)->bool{previous:=first;for v in rest{if previous>v do return false;previous=v};return true}\ndesc::proc(first:$T,rest:..T)->bool{previous:=first;for v in rest{if previous<v do return false;previous=v};return true}\nmain::proc(){assert(asc(1,2,3));assert(desc(3,2,1))}\n';
        await replace(genericLoop);
        await eventually(() => vscode.commands.executeCommand('vscode.provideDocumentSemanticTokens',testDocument.uri), value => {
            if (!value?.data?.length) return false;
            let line=0,column=0;
            const kinds=[];
            for(let i=0;i<value.data.length;i+=5){
                const [delta,char,length,kind]=value.data.slice(i,i+4);
                line+=delta;column=delta?char:column+char;
                const start=testDocument.offsetAt(new vscode.Position(line,column));
                if(genericLoop.slice(start,start+length)==='v') kinds.push(legend.tokenTypes[kind]);
            }
            return kinds.length===6 && kinds.every(kind=>kind==='variable');
        });
        const genericHover = await eventually(() => vscode.commands.executeCommand('vscode.executeHoverProvider',testDocument.uri,
            testDocument.positionAt(genericLoop.indexOf('>v')+1)), values=>hoverText(values||[]).includes('v: T'));
        assert.ok(!hoverText(genericHover).includes('$v'));
        await eventually(() => vscode.languages.getDiagnostics(testDocument.uri), values => values.length === 0);
        const genericDefault = 'package main\n/* 😀 */ sorted::proc(a:$T/[]$E,less:=proc(i,j:E)->bool{return missing})->[]E{_=less(a[0],a[1]);return a}\nmain::proc(){_=sorted([]int{1,2})}\n';
        await replace(genericDefault);
        const defaultErrors = await eventually(() => vscode.languages.getDiagnostics(testDocument.uri),
            values=>values.some(value=>value.message.includes('Undeclared name: missing')));
        const defaultError=defaultErrors.find(value=>value.message.includes('Undeclared name: missing'));
        assert.equal(testDocument.getText(defaultError.range),'missing');
        assert.ok(!defaultError.message.includes('__thor_'));
        assert.ok(!defaultError.message.includes('did not provide a source location'));
        assert.ok(!vscode.languages.getDiagnostics(document.uri).some(value=>value.message.includes('Undeclared name: missing')),
            'An error in the temporary example must not appear on demo/main.thor');
        await replace(genericDefault.replace('return missing','return i<j'));
        await eventually(() => vscode.languages.getDiagnostics(testDocument.uri),values=>values.length===0);
        const comparatorSource = 'package main\nimport "core:slice"\nsorted::proc(\n a:$T/[]$E,\n less:=proc(i,j:E)->bool{return i<j},\n allocator:=context.allocator,\n)->[]E{ret:=make([]E,len(a),allocator);copy(ret,a);slice.sort_by(ret,less);return ret}\nmain::proc(){result:=sorted([]int{2,1});delete(result)}\n';
        await replace(comparatorSource);
        await eventually(() => vscode.languages.getDiagnostics(testDocument.uri),values=>values.length===0);
        const forwardedOffset=comparatorSource.indexOf('sort_by(ret,less)')+'sort_by(ret,'.length;
        await eventually(() => vscode.commands.executeCommand('vscode.provideDocumentSemanticTokens',testDocument.uri),value=>{
            if(!value?.data?.length)return false;
            let line=0,column=0;
            for(let i=0;i<value.data.length;i+=5){
                const [delta,char,,kind]=value.data.slice(i,i+4);
                line+=delta;column=delta?char:column+char;
                if(testDocument.offsetAt(new vscode.Position(line,column))===forwardedOffset)return legend.tokenTypes[kind]==='parameter';
            }
            return false;
        });
        const parameterEnd=testDocument.positionAt(comparatorSource.indexOf('a:$T')+1);
        const parameterEdit=await eventually(()=>vscode.commands.executeCommand('vscode.executeDocumentRenameProvider',
            testDocument.uri,parameterEnd,'data'),value=>value?.size>0);
        assert.equal(parameterEdit.get(testDocument.uri).length,3);
        const parameterEditApplied=await vscode.workspace.applyEdit(parameterEdit);
        assert.ok(parameterEditApplied);
        assert.equal(testDocument.getText(),comparatorSource.replace('a:$T','data:$T').replace('len(a)','len(data)').replace('copy(ret,a)','copy(ret,data)'));
        await eventually(() => vscode.languages.getDiagnostics(testDocument.uri),values=>values.length===0);
        const extremaSource='package main\nmain::proc(){values:=[]i16{3,1};low,present:=min(..values);high:=max(**[2]f32{1,3});_=low;_=present;_=high}\n';
        await replace(extremaSource);
        await eventually(()=>vscode.languages.getDiagnostics(testDocument.uri),values=>values.length===0);
        for(const [word,type] of [['low,present','i16'],['present:=','bool'],['high:=','f32']]){
            await eventually(()=>vscode.commands.executeCommand('vscode.executeHoverProvider',testDocument.uri,
                testDocument.positionAt(extremaSource.indexOf(word)+1)),values=>hoverText(values||[]).includes(': '+type));
        }
        const invalidExtrema=extremaSource.replace('[2]f32{1,3}','[0]f32{}');
        await replace(invalidExtrema);
        const extremaErrors=await eventually(()=>vscode.languages.getDiagnostics(testDocument.uri),
            values=>values.some(value=>value.message.includes('empty fixed array')));
        assert.equal(testDocument.getText(extremaErrors.find(value=>value.message.includes('empty fixed array')).range),'[0]f32{}');
        await replace(extremaSource);
        await eventually(()=>vscode.languages.getDiagnostics(testDocument.uri),values=>values.length===0);
        const wildcardSource='package main\nimport * "thor:basic"\nmain::proc(){result:=sorted([]int{2,1});defer delete(result);print(result)}\n';
        await replace(wildcardSource);
        await eventually(()=>vscode.languages.getDiagnostics(testDocument.uri),values=>values.length===0);
        await eventually(()=>vscode.commands.executeCommand('vscode.executeHoverProvider',testDocument.uri,
            testDocument.positionAt(wildcardSource.indexOf('sorted(')+2)),values=>hoverText(values||[]).includes('less'));
        const suppressedSource=wildcardSource.replace('import * "thor:basic"','import _ "thor:basic" / sorted, print');
        await replace(suppressedSource);
        await eventually(()=>vscode.languages.getDiagnostics(testDocument.uri),values=>values.length===0);
        await eventually(()=>vscode.commands.executeCommand('vscode.executeHoverProvider',testDocument.uri,
            testDocument.positionAt(suppressedSource.indexOf('sorted(')+2)),values=>hoverText(values||[]).includes('less')&&!hoverText(values||[]).includes('__thor_'));
        const nativeBad='package main\nimport "core:math" / round\nCOUNT::int(#run round("bad"))\nmain::proc(){values:[COUNT]int;_=values}\n';
        await replace(nativeBad);
        const nativeErrors=await eventually(()=>vscode.languages.getDiagnostics(testDocument.uri),
            values=>values.some(value=>value.message.includes('No procedures or ambiguous call')));
        const nativeError=nativeErrors.find(value=>value.message.includes('No procedures or ambiguous call'));
        assert.equal(testDocument.getText(nativeError.range),'round');
        assert.ok(!nativeError.message.includes('thor_eval_')&&!nativeError.message.includes('__thor_'));
        const nativeGood=nativeBad.replace('round("bad")','round(1000 ** .33333)');
        await replace(nativeGood);
        await eventually(()=>vscode.languages.getDiagnostics(testDocument.uri),values=>values.length===0);
        await replace(changed);
        const diagnostics = await eventually(() => vscode.languages.getDiagnostics(testDocument.uri),
            values => values.some(value => value.code === 'thor' && value.message.includes('Cannot convert')));
        const error = diagnostics.find(value => value.code === 'thor' && value.message.includes('Cannot convert'));
        assert.deepEqual(error.range.start, testDocument.positionAt(changed.indexOf('"wrong"')));
        assert.equal(testDocument.getText(error.range), '"wrong"');
        assert.ok(!error.message.includes('__thor_'));
        assert.equal(fs.readFileSync(testPath, 'utf8'), initial, 'Checking must use unsaved edits without saving them');
        await replace(initial);
        await eventually(() => vscode.languages.getDiagnostics(testDocument.uri), values => values.length === 0);
        const immutable = 'package main\nimport f "thor:format"\nmain::proc(){_="😀"; value:>7; value=9; text:=f.group_integer(value); delete(text)}\n';
        await replace(immutable);
        const immutableHover = await eventually(() => vscode.commands.executeCommand('vscode.executeHoverProvider', testDocument.uri,
            testDocument.positionAt(immutable.indexOf('value:>') + 2)),
            values => values?.some(value => value.contents.some(content => (content.value || '').includes('immutable storage'))));
        assert.ok(immutableHover.length);
        const immutableErrors = await eventually(() => vscode.languages.getDiagnostics(testDocument.uri),
            values => values.some(value => value.message.includes("immutable storage 'value'")));
        const immutableError = immutableErrors.find(value => value.message.includes("immutable storage 'value'"));
        assert.equal(testDocument.getText(immutableError.range), 'value');
        const formatDefinition = await eventually(() => vscode.commands.executeCommand('vscode.executeDefinitionProvider', testDocument.uri,
            testDocument.positionAt(immutable.indexOf('group_integer') + 2)), values => values?.length > 0);
        assert.ok(formatDefinition.some(value => (value.targetUri || value.uri).path.includes('/runtime/format/')));
        await replace(immutable.replace('value:>7', 'value:=7'));
        await eventually(() => vscode.languages.getDiagnostics(testDocument.uri), values => values.length === 0);
        const expressions = 'package main\nmain::proc(){name:="world";message:=f"hello {name}";choice:=switch 1{case 1:2;case:3};names:=set[int]{1,2};data:=[1,2];_=data[-1];_=message;_=choice;_=names;delete(message);delete(names)}\n';
        await replace(expressions);
        for (const [name, type] of [['message', 'string'], ['choice', 'int'], ['names', 'set[int]']]) {
            await eventually(() => vscode.commands.executeCommand('vscode.executeHoverProvider', testDocument.uri,
                testDocument.positionAt(expressions.indexOf(name + ':=') + 2)),
                values => values?.some(value => value.contents.some(content => (content.value || '').includes(type))));
        }
        const fieldDefinition = await eventually(() => vscode.commands.executeCommand('vscode.executeDefinitionProvider', testDocument.uri,
            testDocument.positionAt(expressions.indexOf('{name}') + 2)), values => values?.length > 0);
        assert.deepEqual((fieldDefinition[0].targetSelectionRange || fieldDefinition[0].range).start,
            testDocument.positionAt(expressions.indexOf('name:=')));
        await eventually(() => vscode.languages.getDiagnostics(testDocument.uri), values => values.length === 0);
        await replace(expressions.replace('{name}', '{missing_field}'));
        const fieldErrors = await eventually(() => vscode.languages.getDiagnostics(testDocument.uri),
            values => values.some(value => value.message.includes('missing_field')));
        const fieldError = fieldErrors.find(value => value.message.includes('missing_field'));
        assert.equal(testDocument.getText(fieldError.range), 'missing_field');
        assert.ok(!fieldError.message.includes('__thor_'));
        await replace(expressions);
        await eventually(() => vscode.languages.getDiagnostics(testDocument.uri), values => values.length === 0);
        const implicitImports='package main\nimport "thor:format"\nimport "thor:sets"\nimport "thor:pure"\nmain::proc(){text:=format.group_integer(1234);delete(text);names:sets.Set(string);sets.insert(&names,"ok");sets.destroy(names);values:=pure.clone([]int{1,2});pure.destroy(values)}\n';
        await replace(implicitImports);
        const implicitDefinition=await eventually(()=>vscode.commands.executeCommand('vscode.executeDefinitionProvider',testDocument.uri,
            testDocument.positionAt(implicitImports.indexOf('group_integer')+2)),values=>values?.length>0);
        assert.ok(implicitDefinition.some(value=>(value.targetUri||value.uri).path.includes('/runtime/format/')));
        await eventually(()=>vscode.languages.getDiagnostics(testDocument.uri),values=>values.length===0);
        const runeFormat='package main\nmain::proc(){text:=f`raw \\n {"é😀":\'界\'^5}`;delete(text)}\n';
        await replace(runeFormat);
        const bareFormat=runeFormat.replace("'界'",'界');
        await replace(bareFormat);
        const formatErrors=await eventually(()=>vscode.languages.getDiagnostics(testDocument.uri),values=>values.some(value=>value.message.includes('single-quoted rune')));
        const formatError=formatErrors.find(value=>value.message.includes('single-quoted rune'));
        assert.equal(testDocument.getText(formatError.range),'界^5');
        await replace(runeFormat);
        await eventually(()=>vscode.languages.getDiagnostics(testDocument.uri),values=>values.length===0);
        const declaration='allowed_size::proc($N:int)->int where N in [2,4,8]{return N}\n';
        const powerSource='package main\nmain::proc(){_="😀";base:f64=2;exponent:f64 = -3;result:=base**exponent;_=result}\n';
        await replace(powerSource);
        await eventually(()=>vscode.commands.executeCommand('vscode.executeHoverProvider',testDocument.uri,
            testDocument.positionAt(powerSource.indexOf('result:=')+2)),
            values=>values?.some(value=>value.contents.some(content=>(content.value||'').includes('result: f64'))));
        await eventually(()=>vscode.languages.getDiagnostics(testDocument.uri),values=>values.length===0);
        await replace(powerSource.replace('exponent:f64 = -3','exponent:int = 3'));
        const powerErrors=await eventually(()=>vscode.languages.getDiagnostics(testDocument.uri),values=>values.some(value=>value.message.includes('matching operand types')));
        const powerError=powerErrors.find(value=>value.message.includes('matching operand types'));
        assert.equal(testDocument.getText(powerError.range),'base**exponent');
        assert.ok(!powerError.message.includes('Thor expression'));
        await replace(powerSource);
        await eventually(()=>vscode.languages.getDiagnostics(testDocument.uri),values=>values.length===0);
        const typedLoops='package main\nmain::proc(){\n values:=[10,20]\n for value,(index:i8) in values{_=value;_=index}\n for number:f64 in 1..=3{_=number}\n}\n';
        await replace(typedLoops);
        for(const [fragment,expected] of [['index:','index: i8'],['number:','number: f64'],['value,','value: int']]){
            await eventually(()=>vscode.commands.executeCommand('vscode.executeHoverProvider',testDocument.uri,
                testDocument.positionAt(typedLoops.indexOf(fragment)+1)),values=>hoverText(values||[]).includes(expected));
        }
        const typedRename=await vscode.commands.executeCommand('vscode.executeDocumentRenameProvider',testDocument.uri,
            testDocument.positionAt(typedLoops.indexOf('index:')+1),'ordinal');
        assert.equal(typedRename.get(testDocument.uri).length,2);
        await replace(typedLoops.replace('index:i8','index:f64'));
        const typedErrors=await eventually(()=>vscode.languages.getDiagnostics(testDocument.uri),values=>values.some(value=>value.message.includes('indices require an integer type')));
        const typedError=typedErrors.find(value=>value.message.includes('indices require an integer type'));
        assert.equal(testDocument.getText(typedError.range),'f64');
        assert.ok(!typedError.message.includes('Thor expression'));
        await replace(typedLoops);
        await eventually(()=>vscode.languages.getDiagnostics(testDocument.uri),values=>values.length===0);
        const arrayTypeMisuse='// 😀 original header\npackage main\nmain::proc(){\n Rows::[3]struct{x:int}\n i:=1\n Rows[i]={1}\n}\n';
        await replace(arrayTypeMisuse);
        const arrayErrors=await eventually(()=>vscode.languages.getDiagnostics(testDocument.uri),values=>values.some(value=>value.message.includes('it names a type')));
        assert.equal(arrayErrors.length,1);
        assert.equal(testDocument.getText(arrayErrors[0].range),'Rows');
        const declarationNote=arrayErrors[0].relatedInformation[0];
        assert.equal(declarationNote.location.uri.toString(),testDocument.uri.toString());
        assert.equal(declarationNote.location.range.start.line,3);
        assert.equal(testDocument.getText(declarationNote.location.range),'Rows');
        assert.ok(declarationNote.message.includes('declared as a type'));
        await replace(arrayTypeMisuse.replace('Rows::','Rows:'));
        await eventually(()=>vscode.languages.getDiagnostics(testDocument.uri),values=>values.length===0);
        const shadowQueries='// 😀 header\npackage main\nimport native "base:builtin"\ntype_of::proc()->[2]int{return {1,2}}\nmain::proc(){values:=[1,2];i:=-1;result:=type_of()[i];native.assert(result==2)}\n';
        await replace(shadowQueries);
        await eventually(()=>vscode.languages.getDiagnostics(testDocument.uri),values=>values.length===0);
        await eventually(()=>vscode.commands.executeCommand('vscode.executeHoverProvider',testDocument.uri,
            testDocument.positionAt(shadowQueries.indexOf('result:=')+1)),values=>hoverText(values||[]).includes('result: int'));
        await replace(shadowQueries.replace('type_of()[i]','type_of()[missing]'));
        const missingIndexErrors=await eventually(()=>vscode.languages.getDiagnostics(testDocument.uri),values=>values.some(value=>value.message.includes('Undeclared name: missing')));
        assert.equal(missingIndexErrors.length,1);
        assert.equal(testDocument.getText(missingIndexErrors[0].range),'missing');
        await replace(shadowQueries.replace('i:=-1','i:f64=1'));
        const indexTypeErrors=await eventually(()=>vscode.languages.getDiagnostics(testDocument.uri),values=>values.some(value=>value.message.includes('Sequence indices must have an integer type')));
        assert.equal(indexTypeErrors.length,1);
        assert.equal(testDocument.getText(indexTypeErrors[0].range),'i');
        await replace(shadowQueries.replace('result:=','result:string=').replace('native.assert(result==2)','native.assert(true)'));
        const indexResultErrors=await eventually(()=>vscode.languages.getDiagnostics(testDocument.uri),values=>values.some(value=>value.message.includes("value 'type_of()[i]'")));
        assert.equal(indexResultErrors.length,1);
        assert.ok(!indexResultErrors[0].message.includes('Thor expression'));
        const memberContext='package main\nRecord::struct{x:int}\nmain::proc(){/* 😀 */ record:=Record{1};_=record in [1,2]}\n';
        await replace(memberContext);
        const memberErrors=await eventually(()=>vscode.languages.getDiagnostics(testDocument.uri),values=>values.filter(value=>value.message.includes('In this membership test')).length===2);
        for(const error of memberErrors){
            assert.ok(error.message.includes("comparable with 'record'"));
            const note=error.relatedInformation[0];
            assert.equal(note.location.uri.toString(),testDocument.uri.toString());
            assert.equal(testDocument.getText(note.location.range),'record');
        }
        await replace(memberContext.replace('[1,2]','[Record{1},Record{2}]'));
        await eventually(()=>vscode.languages.getDiagnostics(testDocument.uri),values=>values.length===0);
        const usingSource='// 😀 original header\npackage main\nKind::enum{A,B}\nRecord::struct{x:int,kind:Kind}\nread::proc(using record:Record)->int{return x}\nwrite::proc(using record:^Record){kind=switch{case x==4:.A;case:true?.B:.A}}\nmain::proc(){record:=Record{x=4};write(&record);using record;value:=read(record);_=value}\n';
        await replace(usingSource);
        await eventually(()=>vscode.commands.executeCommand('vscode.executeHoverProvider',testDocument.uri,
            testDocument.positionAt(usingSource.indexOf('value:=')+2)),values=>hoverText(values||[]).includes('value: int'));
        await eventually(()=>vscode.commands.executeCommand('vscode.executeHoverProvider',testDocument.uri,
            testDocument.positionAt(usingSource.indexOf('x==4:.A')+'x==4:.'.length)),values=>hoverText(values||[]).includes('Kind: .A'));
        await replace(usingSource.replace('_=value','_=missing'));
        const usingErrors=await eventually(()=>vscode.languages.getDiagnostics(testDocument.uri),values=>values.some(value=>value.message.includes('Undeclared name: missing')));
        assert.equal(testDocument.getText(usingErrors.find(value=>value.message.includes('Undeclared name: missing')).range),'missing');
        assert.ok(!usingErrors.some(value=>value.message.includes('has been disallowed')));
        await replace(usingSource);
        await eventually(()=>vscode.languages.getDiagnostics(testDocument.uri),values=>values.length===0);
        const multiSource='package main\nKind::enum{A,B}\nmain::proc(){number:=0;text:="";number,text=switch{case true:1,"ok";case:2,"other"};kind:Kind;number,kind=switch{case number==1:2,.B;case:break};_=text;_=kind}\n';
        await replace(multiSource);
        await eventually(()=>vscode.commands.executeCommand('vscode.executeHoverProvider',testDocument.uri,
            testDocument.positionAt(multiSource.indexOf('_=text')+3)),values=>hoverText(values||[]).includes('text: string'));
        await eventually(()=>vscode.commands.executeCommand('vscode.executeHoverProvider',testDocument.uri,
            testDocument.positionAt(multiSource.indexOf('2,.B')+3)),values=>hoverText(values||[]).includes('Kind: .B'));
        await replace(multiSource.replace('1,"ok"','1,99'));
        const multiErrors=await eventually(()=>vscode.languages.getDiagnostics(testDocument.uri),values=>values.some(value=>value.message.includes('Cannot')));
        assert.equal(testDocument.getText(multiErrors.find(value=>value.message.includes('Cannot')).range),'99');
        assert.ok(multiErrors.every(value=>!value.message.includes('__thor_')));
        await replace(multiSource);
        await eventually(()=>vscode.languages.getDiagnostics(testDocument.uri),values=>values.length===0);
        const membershipSource='package main\nRecord::struct{value:i8}\nmain::proc(){small:i8=4;found:=small in [4,8];record:=Record{4};if record in [{value=4},{value=8}]{};_=found}\n';
        await replace(membershipSource);
        await eventually(()=>vscode.commands.executeCommand('vscode.executeHoverProvider',testDocument.uri,
            testDocument.positionAt(membershipSource.indexOf('found:=')+2)),values=>hoverText(values||[]).includes('found: bool'));
        await eventually(()=>vscode.commands.executeCommand('vscode.executeHoverProvider',testDocument.uri,
            testDocument.positionAt(membershipSource.indexOf('[{value=4')+3)),values=>hoverText(values||[]).includes('value: i8'));
        await replace(membershipSource.replace('[4,8]','[4,128]'));
        const membershipErrors=await eventually(()=>vscode.languages.getDiagnostics(testDocument.uri),values=>values.some(value=>value.message.includes('128')));
        assert.equal(testDocument.getText(membershipErrors.find(value=>value.message.includes('128')).range),'128');
        await replace(membershipSource);
        await eventually(()=>vscode.languages.getDiagnostics(testDocument.uri),values=>values.length===0);
        const intervalSource='package main\nallowed_range::proc($N:int)->int where N in 0..<10{return N}\nwhen -2 in -3..=0 {SELECTED::42} else {SELECTED::false}\nmain::proc(){value:=3;found:=value in 0..<10;result:=allowed_range(9);_=found;_=result;_=SELECTED}\n';
        await replace(intervalSource);
        await eventually(()=>vscode.commands.executeCommand('vscode.executeHoverProvider',testDocument.uri,
            testDocument.positionAt(intervalSource.indexOf('found:=')+2)),values=>values?.some(value=>value.contents.some(content=>(content.value||'').includes('found: bool'))));
        await replace(intervalSource.replace('allowed_range(9)','allowed_range(10)'));
        const intervalErrors=await eventually(()=>vscode.languages.getDiagnostics(testDocument.uri),values=>values.some(value=>value.message.includes('where')));
        assert.ok(intervalErrors.every(value=>!value.message.includes('__thor_')));
        await replace(intervalSource);
        await eventually(()=>vscode.languages.getDiagnostics(testDocument.uri),values=>values.length===0);
        const redeclared='package main\n'+declaration+declaration+'main::proc(){}\n';
        await replace(redeclared);
        const redeclarations=await eventually(()=>vscode.languages.getDiagnostics(testDocument.uri),values=>values.some(value=>value.code==='Declaration'));
        const redeclaration=redeclarations.find(value=>value.code==='Declaration');
        assert.ok(redeclaration.message.includes("Redeclaration of 'allowed_size'"));
        assert.deepEqual(redeclaration.range.start,testDocument.positionAt(redeclared.lastIndexOf('allowed_size')));
        await replace(runeFormat);
        await eventually(()=>vscode.languages.getDiagnostics(testDocument.uri),values=>values.length===0);
        const hardened = 'package main\nlast::proc "c"(a:[]int,i:int)->int{return a[i]}\nmain::proc(){value:=1;p:=&value;p^=2;a:=set[int]{1};b:=set[int]{2};a~=b;_=len(a~b);_=f"{(true ? f"nested {7}" : "borrowed")}";for item in set[int]{1,2}{_=item};delete(a);delete(b)}\n';
        await replace(hardened);
        await eventually(() => vscode.languages.getDiagnostics(testDocument.uri), values => values.length === 0);
        const manualFstring='package main\ncheck::proc(text:string){}\nmain::proc(){message:=f!"manual {7}";check(f"automatic {message}");delete(message)}\n';
        await replace(manualFstring);
        await eventually(()=>vscode.commands.executeCommand('vscode.executeHoverProvider',testDocument.uri,
            testDocument.positionAt(manualFstring.indexOf('message:=')+2)),values=>hoverText(values||[]).includes('message: string'));
        await eventually(()=>vscode.languages.getDiagnostics(testDocument.uri),values=>values.length===0);
        await replace(manualFstring.replace('manual {7}','manual {missing}'));
        const manualFstringErrors=await eventually(()=>vscode.languages.getDiagnostics(testDocument.uri),values=>values.some(value=>value.message.includes('Undeclared')));
        assert.equal(testDocument.getText(manualFstringErrors.find(value=>value.message.includes('missing')).range),'missing');
        await replace(manualFstring);
        await eventually(()=>vscode.languages.getDiagnostics(testDocument.uri),values=>values.length===0);
        const pure = 'package main\nsquare::fn(value:int)->int{return value*value}\nSIZE::#run square(3)\nmain::proc(){result:=#run square(4);_=result;data:[SIZE]int;_=data}\n';
        await replace(pure);
        await eventually(() => vscode.commands.executeCommand('vscode.executeHoverProvider', testDocument.uri,
            testDocument.positionAt(pure.indexOf('square') + 2)),
            values => values?.some(value => value.contents.some(content => (content.value || '').includes('pure function'))));
        await eventually(() => vscode.commands.executeCommand('vscode.executeHoverProvider', testDocument.uri,
            testDocument.positionAt(pure.indexOf('result:=') + 2)),
            values => values?.some(value => value.contents.some(content => (content.value || '').includes('result: int'))));
        await eventually(() => vscode.languages.getDiagnostics(testDocument.uri), values => values.length === 0);
        const impure = pure.replace('return value*value', '_=context;return value*value');
        await replace(impure);
        const pureErrors = await eventually(() => vscode.languages.getDiagnostics(testDocument.uri),
            values => values.some(value => value.message.includes('ambient context')));
        const pureError = pureErrors.find(value => value.message.includes('ambient context'));
        assert.equal(testDocument.getText(pureError.range), 'context');
        assert.ok(!pureError.message.includes('__thor_') && !pureError.message.includes('thor-run-'));
        await replace(pure);
        await eventually(() => vscode.languages.getDiagnostics(testDocument.uri), values => values.length === 0);
        const allocated = 'package main\nimport pure "thor:pure"\nnames::fn()->[]string{result:=make([]string,1);result[0]="hello";return result}\nmain::proc(){result:=#run names();pure.destroy(result)}\n';
        await replace(allocated);
        await eventually(() => vscode.commands.executeCommand('vscode.executeHoverProvider', testDocument.uri,
            testDocument.positionAt(allocated.indexOf('result:=#run') + 2)),
            values => values?.some(value => value.contents.some(content => (content.value || '').includes('result: []string'))));
        await eventually(() => vscode.languages.getDiagnostics(testDocument.uri), values => values.length === 0);
        await replace(allocated.replace('result:=#run names();pure.destroy(result)', 'result::#run names();_=result'));
        const allocatedErrors = await eventually(() => vscode.languages.getDiagnostics(testDocument.uri),
            values => values.some(value => value.message.includes('slice-containing #run result')));
        const allocatedError = allocatedErrors.find(value => value.message.includes('slice-containing #run result'));
        assert.equal(testDocument.getText(allocatedError.range), '#run names()');
        assert.ok(!allocatedError.message.includes('__thor_') && !allocatedError.message.includes('thor-run-'));
        await replace(allocated);
        await eventually(() => vscode.languages.getDiagnostics(testDocument.uri), values => values.length === 0);
        const automatic = 'package main\nsquare::fn(n:int)->int{return n*n}\nSIZE::square(3)\nwork::proc($N:int)->int{VALUE::#run square(N);return VALUE}\nmain::proc(){result:=square(SIZE);data:[SIZE]int;assert(work(3)==9);_=result;_=data}\n';
        await replace(automatic);
        await eventually(() => vscode.commands.executeCommand('vscode.executeHoverProvider', testDocument.uri,
            testDocument.positionAt(automatic.indexOf('square') + 2)),
            values => values?.some(value => value.contents.some(content => (content.value || '').includes('Known-argument calls'))));
        await eventually(() => vscode.languages.getDiagnostics(testDocument.uri), values => values.length === 0);
        const genericErrorText = automatic.replace('VALUE::#run square(N)', 'value:=N;VALUE::#run square(value)');
        await replace(genericErrorText);
        const genericErrors = await eventually(() => vscode.languages.getDiagnostics(testDocument.uri),
            values => values.some(value => value.message.includes('runtime storage is not a constant')));
        const genericError = genericErrors.find(value => value.message.includes('runtime storage is not a constant'));
        assert.equal(testDocument.getText(genericError.range), 'value');
        assert.ok(!genericError.message.includes('__thor_') && !genericError.message.includes('thor-run-'));
        await replace(automatic);
        await eventually(() => vscode.languages.getDiagnostics(testDocument.uri), values => values.length === 0);
        const borrowedView = hardened.replace('value:=1', '_="😀";view:=f"abc {1}"[1:];_=view;value:=1');
        await replace(borrowedView);
        const lifetimeErrors = await eventually(() => vscode.languages.getDiagnostics(testDocument.uri),
            values => values.some(value => value.message.includes('named owner')));
        const lifetimeError = lifetimeErrors.find(value => value.message.includes('named owner'));
        assert.equal(testDocument.getText(lifetimeError.range), 'f"abc {1}"[1:]');
        assert.ok(!lifetimeError.message.includes('__thor_'));
        await replace(hardened);
        await eventually(() => vscode.languages.getDiagnostics(testDocument.uri), values => values.length === 0);
        const contextual = 'package main\nColor::enum{Red,Blue}\nOther::enum{Red,Green}\nmain::proc(){other:Other=.Red;choice:[2]Color=switch 1{case 1:[.Red,.Blue];case:[.Blue,.Red]};_=choice;_=other}\n';
        await replace(contextual);
        const memberPosition=testDocument.positionAt(contextual.indexOf('1:[.Red')+5);
        await eventually(()=>vscode.commands.executeCommand('vscode.executeHoverProvider',testDocument.uri,memberPosition),
            values=>hoverText(values||[]).includes('Color: .Red'));
        const memberDefinition=await vscode.commands.executeCommand('vscode.executeDefinitionProvider',testDocument.uri,memberPosition);
        assert.ok(memberDefinition.some(value=>(value.range||value.targetSelectionRange).start.isEqual(testDocument.positionAt(contextual.indexOf('Red,Blue')))));
        const memberCompletion=await vscode.commands.executeCommand('vscode.executeCompletionItemProvider',testDocument.uri,memberPosition,'.');
        const memberLabels=memberCompletion.items.map(value=>typeof value.label==='string'?value.label:value.label.label);
        assert.ok(memberLabels.includes('Red')&&memberLabels.includes('Blue')&&!memberLabels.includes('Green'),JSON.stringify(memberLabels));
        const memberRename=await vscode.commands.executeCommand('vscode.executeDocumentRenameProvider',testDocument.uri,memberPosition,'Crimson');
        const memberRenameApplied=await vscode.workspace.applyEdit(memberRename);
        assert.ok(memberRenameApplied);
        assert.equal(testDocument.getText(),contextual.replace('Color::enum{Red','Color::enum{Crimson').replaceAll('.Red','.Crimson').replace('other:Other=.Crimson','other:Other=.Red'));
        await eventually(() => vscode.languages.getDiagnostics(testDocument.uri), values => values.length === 0);
        await vscode.commands.executeCommand('workbench.action.revertAndCloseActiveEditor');
        await vscode.commands.executeCommand('workbench.action.closeAllEditors');
        assert.equal(vscode.window.activeTextEditor, undefined);
        await vscode.commands.executeCommand('tls.restart');
        await vscode.window.showTextDocument(document);
        await eventually(() => vscode.commands.executeCommand('vscode.executeHoverProvider', document.uri, document.positionAt(offset)), values => values?.length > 0);
        result.success = true;
        result.checks = ['Thor language registration', 'TLS hover', 'selected-import definition', 'Odin association preserved',
            'comment toggling removes bracket matches and semantic tokens, uncommenting restores code brackets',
            'batched comment toggles, undo/redo and bracket refresh preserve source, live brackets and TLS',
            'literal parentheses are excluded from matching in ordinary, raw, f/f! and nested strings; interpolation brackets stay active',
            'Rename Symbol provider applies original-source edits to unsaved Unicode text',
            'live compiler diagnostics on unsaved text', 'Unicode source ranges', 'diagnostics clear after fix',
            'unused imports carry VS Code dimming tags and update without saving',
            'selected import names keep function colors, fade individually, and update without saving',
            'selected import hovers show the same function signature and docs as calls, including unused imports',
            'activation and restart with no active editor', 'package declaration hover',
            'generic loop variables remain values in semantic tokens and hover',
            'generic inline defaults compile and body errors highlight original source', 'hidden Set imports do not hover on comments',
            'callback values use parameter color and a one-letter parameter renames at its end boundary',
            'contextual switch-array enum hover, definition, repeated-value completion and precise rename',
            'spread min/max element and status hover, original argument error ranges and unsaved correction',
            'wildcard and namespace-free imports compile and provide source hovers',
            'standard-library #run checks original overload ranges and clears after unsaved correction',
            'immutable hover and compiler errors', 'runtime collection definition', 'TLS restart',
            'reversible compile-time constant color preview and custom color',
            'readonly parameter and value-loop tokens, mutable reference-loop tokens and immutable theme fallback',
            'readonly value-struct members, mutable pointer members and mutable copies of immutable structs',
            'readonly type-switch aliases and narrowed struct fields, mutable reference aliases and assignment targets',
            'labels have one semantic category at declarations and branches, distinct from same-named values',
            'global immutable enum-indexed array rename from its declaration',
            'constant string repetition hover and original-source count diagnostics with unsaved recovery',
            'interpolation/switch/Set inference', 'definition inside interpolation', 'mapped interpolation diagnostics',
            'native pointer assignment and Set xor', 'mixed nested interpolation', 'mapped temporary-view diagnostics',
            'f! string hovers, anonymous f arguments, original field diagnostics and unsaved recovery',
            'pure function hover', '#run result inference', 'mapped unsaved purity diagnostics',
            'allocated #run result inference', 'mapped slice-constant diagnostic',
            'automatic constants', 'generic-dependent evaluation', 'mapped unsaved generic diagnostic',
            'full demo semantic-token ranges and classifications',
            'implicit runtime import names and namespace theme mapping',
            'rune fill syntax and mapped format diagnostics',
            'immediate same-file redeclarations and clearing',
            'contextual membership literals, record-field hovers and mapped unsaved overflow diagnostics',
            'automatic using feature with original-source hovers and unsaved diagnostics',
            'promoted using fields supply switch result types and contextual enum hovers',
            'multiple switch results, assignment-only break, corresponding enum hovers and unsaved diagnostics',
            'range membership inference and unsaved constraint diagnostics', 'power inference and unsaved type diagnostics', 'canonical set types in variable and procedure hovers', 'built-in big-number types, long literals, read-only property hovers and completion'];
        result.checks.push('dynamic width hover, rename and precise unsaved type diagnostics');
        result.checks.push('invalid bare format names retain diagnostics and surrounding semantic highlighting');
        result.checks.push('indexed compile-time values use constant tokens while actual type aliases stay types');
        result.checks.push('selective-import member completion, docs and client edits after slash/comma/space (interactive widget opt-in)');
        result.checks.push('undeclared interpolation names keep one precise root diagnostic and no false literal underline');
        result.checks.push('Odin-style precision formats, Rational g/G contract and mapped unsaved recovery');
        result.checks.push('typed range and index hovers, header rename, precise annotation errors and unsaved recovery');
        result.checks.push('type-as-array errors have original use ranges, clickable declaration notes and unsaved recovery');
        result.checks.push('index root/type/result diagnostics, membership context notes and user builtin-spelling hovers recover without saving');
        result.checks.push('early code actions without vet findings preserve hover, symbols and semantic tokens');
        console.log('Thor VS Code integration passed:', result.checks.join(', '));
    } catch (error) {
        result.error = error.stack || String(error);
        result.diagnostics = vscode.languages.getDiagnostics().map(([uri, values]) => ({ uri: uri.toString(), diagnostics: values }));
        throw error;
    } finally {
        fs.rmSync(temporary, { recursive: true, force: true });
        if (process.env.THOR_VSCODE_TEST_RESULT) fs.writeFileSync(process.env.THOR_VSCODE_TEST_RESULT, JSON.stringify(result, null, 2));
    }
}

module.exports = { run };
