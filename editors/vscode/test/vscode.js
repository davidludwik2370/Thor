// Run in an isolated VS Code Extension Development Host; see tools/test_vscode.py.
'use strict';
const vscode = require('vscode');
const assert = require('node:assert/strict');
const path = require('node:path');
const fs = require('node:fs');
const os = require('node:os');

async function eventually(action, predicate) {
    const deadline = Date.now() + 20000;
    let result;
    do {
        result = await action();
        if (predicate(result)) return result;
        await new Promise(resolve => setTimeout(resolve, 250));
    } while (Date.now() < deadline);
    assert.fail(`Language provider did not become ready: ${JSON.stringify(result)}`);
}

async function run() {
    const root = path.resolve(__dirname, '../../..');
    const result = { success: false };
    const temporary = fs.mkdtempSync(path.join(os.tmpdir(), 'thor-editor-diagnostics-'));
    try {
        const document = await vscode.workspace.openTextDocument(vscode.Uri.file(path.join(root, 'demo/main.thor')));
        await vscode.window.showTextDocument(document);
        assert.equal(document.languageId, 'thor');
        const extension = vscode.extensions.getExtension('thor-local.thor-language');
        assert.ok(extension);
        await extension.activate();
        const source = document.getText();
        const offset = source.indexOf('numbers :=') + 2;
        const hover = await eventually(() => vscode.commands.executeCommand('vscode.executeHoverProvider', document.uri, document.positionAt(offset)),
            values => values?.some(value => value.contents.some(content => (content.value || '').includes('[3]int'))));
        assert.ok(hover.length);
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
        await vscode.commands.executeCommand('workbench.action.revertAndCloseActiveEditor');
        await vscode.window.showTextDocument(document);
        await vscode.commands.executeCommand('tls.restart');
        await eventually(() => vscode.commands.executeCommand('vscode.executeHoverProvider', document.uri, document.positionAt(offset)), values => values?.length > 0);
        result.success = true;
        result.checks = ['Thor language registration', 'TLS hover', 'selected-import definition', 'Odin association preserved',
            'live compiler diagnostics on unsaved text', 'Unicode source ranges', 'diagnostics clear after fix',
            'immutable hover and compiler errors', 'runtime collection definition', 'TLS restart',
            'interpolation/switch/Set inference', 'definition inside interpolation', 'mapped interpolation diagnostics'];
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
