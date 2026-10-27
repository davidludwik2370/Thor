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
        assert.deepEqual(extension.packageJSON.contributes.semanticTokenScopes,
            [{language:'thor',scopes:{namespace:['variable.other.namespace.thor']}}]);
        const source = document.getText();
        const offset = source.indexOf('numbers :=') + 2;
        const hover = await eventually(() => vscode.commands.executeCommand('vscode.executeHoverProvider', document.uri, document.positionAt(offset)),
            values => values?.some(value => value.contents.some(content => (content.value || '').includes('[3]int'))));
        assert.ok(hover.length);
        const packageHover = await eventually(() => vscode.commands.executeCommand('vscode.executeHoverProvider', document.uri,
            document.positionAt(source.indexOf('package main')+'package '.length+1)),
            values => hoverText(values || []).includes('main: package'));
        assert.ok(hoverText(packageHover).includes('main: package'));
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
            assert.ok(await vscode.workspace.applyEdit(edit));
        };
        const renameSource = 'package main\nmain::proc(){_="😀";value:=2;message:=f"value {value}";delete(message);_=value}\n';
        await replace(renameSource);
        const renameEdit = await eventually(() => vscode.commands.executeCommand('vscode.executeDocumentRenameProvider',
            testDocument.uri, testDocument.positionAt(renameSource.indexOf('value:=')+1), 'count'),
            value => value?.size > 0);
        assert.equal(renameEdit.get(testDocument.uri).length, 3);
        assert.ok(await vscode.workspace.applyEdit(renameEdit));
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
        assert.ok(await vscode.workspace.applyEdit(parameterEdit));
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
        const intervalSource='package main\nallowed_range::proc($N:int)->int where N in 0..<10{return N}\n#if -2 in -3..=0 {SELECTED::42} #else {SELECTED::false}\nmain::proc(){value:=3;found:=value in 0..<10;result:=allowed_range(9);_=found;_=result;_=SELECTED}\n';
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
        await vscode.commands.executeCommand('workbench.action.revertAndCloseActiveEditor');
        await vscode.commands.executeCommand('workbench.action.closeAllEditors');
        assert.equal(vscode.window.activeTextEditor, undefined);
        await vscode.commands.executeCommand('tls.restart');
        await vscode.window.showTextDocument(document);
        await eventually(() => vscode.commands.executeCommand('vscode.executeHoverProvider', document.uri, document.positionAt(offset)), values => values?.length > 0);
        result.success = true;
        result.checks = ['Thor language registration', 'TLS hover', 'selected-import definition', 'Odin association preserved',
            'Rename Symbol provider applies original-source edits to unsaved Unicode text',
            'live compiler diagnostics on unsaved text', 'Unicode source ranges', 'diagnostics clear after fix',
            'unused imports carry VS Code dimming tags and update without saving',
            'activation and restart with no active editor', 'package declaration hover',
            'generic loop variables remain values in semantic tokens and hover',
            'generic inline defaults compile and body errors highlight original source', 'hidden Set imports do not hover on comments',
            'callback values use parameter color and a one-letter parameter renames at its end boundary',
            'spread min/max element and status hover, original argument error ranges and unsaved correction',
            'immutable hover and compiler errors', 'runtime collection definition', 'TLS restart',
            'interpolation/switch/Set inference', 'definition inside interpolation', 'mapped interpolation diagnostics',
            'native pointer assignment and Set xor', 'mixed nested interpolation', 'mapped temporary-view diagnostics',
            'pure function hover', '#run result inference', 'mapped unsaved purity diagnostics',
            'allocated #run result inference', 'mapped slice-constant diagnostic',
            'automatic constants', 'generic-dependent evaluation', 'mapped unsaved generic diagnostic',
            'full demo semantic-token ranges and classifications',
            'implicit runtime import names and namespace theme mapping',
            'rune fill syntax and mapped format diagnostics',
            'immediate same-file redeclarations and clearing',
            'range membership inference and unsaved constraint diagnostics', 'power inference and unsaved type diagnostics', 'canonical set types in variable and procedure hovers'];
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
