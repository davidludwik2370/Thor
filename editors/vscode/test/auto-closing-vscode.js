'use strict';
const vscode = require('vscode');
const assert = require('node:assert/strict');
const fs = require('node:fs');

async function run() {
    const result = {success: false, checks: []};
    try {
        const document = await vscode.workspace.openTextDocument({language: 'thor', content: ''});
        const editor = await vscode.window.showTextDocument(document);
        const config = vscode.workspace.getConfiguration('editor', document.uri);
        await config.update('autoClosingBrackets', 'languageDefined', vscode.ConfigurationTarget.Global);
        await config.update('autoClosingQuotes', 'languageDefined', vscode.ConfigurationTarget.Global);
        const reset = async (text = '') => {
            const applied = await editor.edit(edit => edit.replace(
                new vscode.Range(document.positionAt(0), document.positionAt(document.getText().length)), text));
            assert.ok(applied);
            const end = document.positionAt(text.length);
            editor.selection = new vscode.Selection(end, end);
            await vscode.commands.executeCommand('workbench.action.focusActiveEditorGroup');
        };
        const type = async text => {
            for (const character of text) {
                await vscode.commands.executeCommand('default:type', {text: character});
            }
        };
        const check = (text, offset) => {
            assert.equal(document.getText(), text);
            assert.equal(document.offsetAt(editor.selection.active), offset);
        };
        for (const prefix of ['f', 'f!']) {
            for (const quote of ['"', '`']) {
                await reset();
                await type(prefix + quote);
                check(prefix + quote + quote, prefix.length + 1);
                await type('{');
                check(prefix + quote + '{}' + quote, prefix.length + 2);
                await type('value}' + quote);
                const expected = prefix + quote + '{value}' + quote;
                check(expected, expected.length);
                result.checks.push(`${prefix}${quote} closes the quote and field, then overtypes both closers`);

                await reset();
                await type(prefix + quote + 'hello {');
                const withText = prefix + quote + 'hello {}' + quote;
                check(withText, withText.length - 2);
            }
        }
        await reset();
        await type('f"outer {f"{value}"}"');
        check('f"outer {f"{value}"}"', 'f"outer {f"{value}"}"'.length);
        result.checks.push('nested interpolations retain native quote and brace overtyping');

        for (const prefix of ['// f', '/* f']) {
            await reset(prefix);
            await type('"{');
            check(prefix + '"{', prefix.length + 2);
        }
        result.checks.push('comments suppress automatic closing');

        await config.update('autoClosingBrackets', 'never', vscode.ConfigurationTarget.Global);
        await reset();
        await type('f"{');
        check('f"{"', 3);
        await config.update('autoClosingBrackets', 'languageDefined', vscode.ConfigurationTarget.Global);
        await config.update('autoClosingQuotes', 'never', vscode.ConfigurationTarget.Global);
        await reset();
        await type('f"');
        check('f"', 2);
        result.checks.push('native auto-closing preferences remain effective');
        const discarded = await editor.edit(edit => edit.delete(
            new vscode.Range(document.positionAt(0), document.positionAt(document.getText().length))));
        assert.ok(discarded);
        await vscode.commands.executeCommand('workbench.action.revertAndCloseActiveEditor');
        result.success = true;
    } catch (error) {
        result.error = error.stack || String(error);
        throw error;
    } finally {
        if (process.env.THOR_VSCODE_TEST_RESULT) {
            fs.writeFileSync(process.env.THOR_VSCODE_TEST_RESULT, JSON.stringify(result, null, 2));
        }
    }
}

module.exports = {run};
