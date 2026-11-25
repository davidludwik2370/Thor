'use strict';
const vscode = require('vscode');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');

async function eventually(action, predicate) {
    const deadline = Date.now() + 45000;
    let value;
    do {
        value = await action();
        if (predicate(value)) return value;
        await new Promise(resolve => setTimeout(resolve, 150));
    } while (Date.now() < deadline);
    assert.fail(`Hover did not become ready: ${JSON.stringify(value)}`);
}

async function run() {
    const result = { success: false };
    const workspace = vscode.workspace.workspaceFolders[0].uri;
    const fixture = vscode.Uri.joinPath(workspace, 'hover-size.thor');
    const config = vscode.workspace.getConfiguration('tls', workspace);
    const text = 'package main\nPerson::struct{age:i32}\nmain::proc(){\n count:i32\n my_var2:=10\n person:^Person\n values:[]i32\n _=count;_=my_var2;_=person;_=values\n}\n';
    try {
        await vscode.workspace.fs.writeFile(fixture, Buffer.from(text));
        const extension = vscode.extensions.getExtension('thor-local.thor-language');
        await extension.activate();
        const document = await vscode.workspace.openTextDocument(fixture);
        const editor = await vscode.window.showTextDocument(document);
        for (const [needle, detail] of [['count:i32', '4 bytes'], ['my_var2:=', '8 bytes · target dependent'],
            ['person:^', '8 bytes · pointer · target dependent'], ['values:[]', '16 bytes · slice header · target dependent']]) {
            const position = document.positionAt(text.indexOf(needle) + 1);
            const hovers = await eventually(() => vscode.commands.executeCommand('vscode.executeHoverProvider', fixture, position),
                values => values?.some(hover => hover.contents.some(content => content.value?.includes('<small>') && content.value.includes(detail))));
            const hover = hovers.find(hover => hover.contents.some(content => content.value?.includes('<small>')));
            assert.equal(hover.contents.length, 1);
            assert.equal(hover.contents[0].supportHtml, true);
            assert.equal(hover.contents[0].isTrusted, false);
            assert.ok(hover.contents[0].value.includes('var(--vscode-descriptionForeground)'));
        }
        const position = document.positionAt(text.indexOf('my_var2:=') + 2);
        editor.selection = new vscode.Selection(position, position);
        await vscode.commands.executeCommand('editor.action.showHover');
        if (process.env.THOR_VSCODE_HOVER_CAPTURE) {
            const capture = process.env.THOR_VSCODE_HOVER_CAPTURE;
            fs.writeFileSync(capture + '.ready', 'ready');
            const deadline = Date.now() + 30000;
            while (!fs.existsSync(capture + '.done') && Date.now() < deadline) {
                await new Promise(resolve => setTimeout(resolve, 100));
            }
        }
        await config.update('hoverSize', false, vscode.ConfigurationTarget.Workspace);
        const hidden = await eventually(() => vscode.commands.executeCommand('vscode.executeHoverProvider', fixture, position),
            values => values?.length && !values.some(hover => hover.contents.some(content => /bytes|thor-hover-size/.test(content.value || ''))));
        assert.ok(hidden.length);
        await config.update('hoverSize', true, vscode.ConfigurationTarget.Workspace);
        const restored = await eventually(() => vscode.commands.executeCommand('vscode.executeHoverProvider', fixture, position),
            values => values?.some(hover => hover.contents.some(content => content.value?.includes('<small>'))));
        assert.ok(restored.length);
        result.success = true;
        result.details = 'Muted second line, theme color, pointer/header qualifiers, and setting changes passed in VS Code.';
    } catch (error) {
        result.error = error.stack || String(error);
        throw error;
    } finally {
        if (process.env.THOR_VSCODE_TEST_RESULT) fs.writeFileSync(process.env.THOR_VSCODE_TEST_RESULT, JSON.stringify(result));
    }
}

module.exports = { run };
